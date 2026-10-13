from datetime import date
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import parse_qs

import httpx
import pytest
import pyarrow as arrow
import pyarrow.parquet as parquet
from shapely import to_wkb
from shapely.geometry import LineString

from app.models import AnalysisRequest
from app import providers


@pytest.mark.parametrize("event,expected", [(date(2026,8,26),"2026-07-27T09:00:00Z"),(date(2026,7,1),"2026-06-30T23:59:59Z")])
def test_real_ohsome_v1_schema_and_strict_pre_event_snapshot(monkeypatch,event,expected):
    original = httpx.Client
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200,json={"apiVersion":"1.10.4","extractRegion":{"temporalExtent":{"fromTimestamp":"2007-10-08T00:00:00Z","toTimestamp":"2026-07-27T09:00Z"}}})
        return httpx.Response(200,json={"type":"FeatureCollection","features":[]})
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    _, timestamp = providers.osm_snapshot(AnalysisRequest(flood_date=event))
    assert timestamp == expected
    payload = parse_qs(requests[-1].content.decode())
    assert payload["time"] == [expected]
    assert payload["properties"] == ["tags,metadata"]
    assert "bridge=*" in payload["filter"][0]


def test_unknown_metadata_shape_fails_without_current_osm_fallback(monkeypatch):
    original=httpx.Client
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"apiVersion":"future"})),**kw))
    with pytest.raises(ValueError,match="unverified snapshot"):
        providers.osm_snapshot(AnalysisRequest())


def test_post_event_osm_metadata_is_rejected(monkeypatch):
    original=httpx.Client
    def respond(request):
        if request.url.path.endswith("metadata"):
            return httpx.Response(200,json={"extractRegion":{"temporalExtent":{"toTimestamp":"2026-07-27T09:00Z"}}})
        return httpx.Response(200,json={"type":"FeatureCollection","features":[{"properties":{"@lastEdit":"2026-09-01T00:00:00Z"}}]})
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    with pytest.raises(ValueError,match="edited after"):
        providers.osm_snapshot(AnalysisRequest())


def test_historical_v2_fallback_preserves_snapshot_and_checks_key(monkeypatch):
    table=arrow.table({"osm_type":["way"],"osm_id":[123],"version":[2],
                       "tags":arrow.array([[('highway','secondary'),('name','Test road')]],type=arrow.map_(arrow.string(),arrow.string())),
                       "geom":[to_wkb(LineString([(85.3,28.1),(85.31,28.11)]))],
                       "edit_timestamp":[datetime(2026,7,1,tzinfo=timezone.utc)]})
    stream=BytesIO();parquet.write_table(table,stream)
    requests=[]
    def respond(request):
        requests.append(request)
        if str(request.url)==providers.OSM_URL+'/metadata':
            return httpx.Response(200,json={"extractRegion":{"temporalExtent":{"toTimestamp":"2026-07-27T09:00Z"}}})
        if request.url.path.endswith(('/elements/geometry', '/elementsFullHistory/geometry')):
            return httpx.Response(403)
        assert request.headers['Authorization']=='synthetic-test-key'
        if request.url.path.endswith('/metadata'):
            return httpx.Response(200,json={"apiVersion":"2.0.0","temporalExtent":{"start":"2007-10-08T00:00:00Z","end":"2026-10-09T07:25:28Z"}})
        return httpx.Response(200,content=stream.getvalue())
    original=httpx.Client
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.setenv('OHSOME_API_KEY','synthetic-test-key')
    data,timestamp=providers.osm_snapshot(AnalysisRequest())
    assert timestamp=='2026-07-27T09:00:00Z'
    assert str(requests[-1].url.params['time'])==timestamp
    assert data['features'][0]['properties']['@osmId']=='way/123'
    assert data['features'][0]['properties']['name']=='Test road'
    assert data['metadata']['sourceApi']==providers.OSM_V2_URL


def test_v2_requires_key_when_v1_is_unavailable(monkeypatch):
    def respond(request):
        if request.url.path.endswith('metadata'):
            return httpx.Response(200,json={"extractRegion":{"temporalExtent":{"toTimestamp":"2026-07-27T09:00Z"}}})
        return httpx.Response(403)
    original=httpx.Client
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.delenv('OHSOME_API_KEY',raising=False)
    with pytest.raises(ValueError,match='OHSOME_API_KEY'):
        providers.osm_snapshot(AnalysisRequest())


@pytest.mark.parametrize("last_edit,rejected", [("2026-07-01T00:00:00Z", False), ("2026-08-01T00:00:00Z", True)])
def test_nepal_snapshot_cap_remains_enforced_when_provider_history_advances(monkeypatch, last_edit, rejected):
    original = httpx.Client
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith("metadata"):
            return httpx.Response(200, json={"temporalExtent": {"end": "2026-10-09T00:00:00Z"}})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [{"properties": {"@lastEdit": last_edit}}]})
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    if rejected:
        with pytest.raises(ValueError, match="requested historical snapshot"):
            providers.osm_snapshot(AnalysisRequest())
    else:
        _, stamp = providers.osm_snapshot(AnalysisRequest())
        assert stamp == "2026-07-27T23:59:59Z"
    assert parse_qs(requests[-1].content.decode())["time"] == ["2026-07-27T23:59:59Z"]
