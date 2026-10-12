"""Allowed-input adapters. Published damage maps are deliberately absent."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path

import httpx
import numpy as np
from rasterio.io import MemoryFile
from rasterio.warp import reproject, Resampling
from shapely import from_wkb
from shapely.geometry import box, shape, mapping

from app.models import Scene, validate_pair


STAC_URL = "https://stac.dataspace.copernicus.eu/v1/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
OSM_URL = "https://api.ohsome.org/v1"
OSM_V2_URL = "https://api.heigit.org/ohsome-api/v2-rc"
OSM_FILTER = "building=* or highway=* or bridge=* or place in (village,hamlet,isolated_dwelling,town,city) or amenity=hospital"


def search_catalog(bbox, start, end, collection="sentinel-1-grd"):
    payload = {"collections": [collection], "bbox": bbox, "datetime": f"{start}/{end}", "limit": 100}
    features = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for _ in range(10):
            response = client.post(STAC_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            features.extend(data.get("features", []))
            next_link = next((link for link in data.get("links", []) if link["rel"] == "next"), None)
            if not next_link:
                break
            # CDSE STAC continuation body; keep a bounded catalog query.
            if next_link.get("body"):
                payload = {**payload, **next_link["body"]}
            else:
                raise ValueError("Catalog search needs more pages. Reduce the area or search window.")
        else:
            raise ValueError("Catalog page limit reached. Reduce the area or search window.")
    return features


def scene_from_item(item):
    props = item["properties"]
    if "VV" not in props.get("sar:polarizations", []):
        raise ValueError("VV polarization unavailable")
    return Scene(id=item["id"], acquired=datetime.fromisoformat(props["datetime"].replace("Z", "+00:00")),
                 relative_orbit=props["sat:relative_orbit"], direction=props["sat:orbit_state"],
                 platform=props["platform"], instrument_mode=props.get("sar:instrument_mode", "UNKNOWN"))


def select_pair(items, params):
    area = box(*params.bbox)
    eligible = []
    for item in items:
        try:
            scene = scene_from_item(item)
            if scene.instrument_mode == "IW" and shape(item["geometry"]).intersection(area).area / area.area >= .98:
                eligible.append(scene)
        except (ValueError, KeyError):
            continue
    pairs = []
    for before in eligible:
        for after in eligible:
            try:
                validate_pair(before, after, params.flood_date)
            except ValueError:
                continue
            interval = (after.acquired - before.acquired).total_seconds() / 86400
            delay = (after.acquired.date() - params.flood_date).days
            pre_gap = (params.flood_date - before.acquired.date()).days
            score = delay + .3 * pre_gap + abs(interval - 12) * .4
            pairs.append((score, before, after))
    if not pairs:
        raise ValueError("No same-platform, same-track IW/VV pair covering this entire area. Reduce the area or widen the search window. The flood day itself is excluded.")
    _, before, after = min(pairs, key=lambda p: p[0])
    return [before, after]


def metadata_times(body):
    temporal = body.get("extractRegion", {}).get("temporalExtent") or body.get("temporalExtent") or {}
    end = temporal.get("toTimestamp") or temporal.get("end")
    start = temporal.get("fromTimestamp") or temporal.get("start")
    if not end:
        raise ValueError("Historical OSM metadata did not provide its available time range. Refusing an unverified snapshot.")
    return (datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None,
            datetime.fromisoformat(end.replace("Z", "+00:00")))


def choose_snapshot(body, event, hint=None):
    earliest, latest = metadata_times(body)
    snapshot = min(latest, event - timedelta(seconds=1), hint) if hint else min(latest, event - timedelta(seconds=1))
    if earliest and snapshot < earliest:
        raise ValueError("The event predates the historical OSM service coverage.")
    return snapshot


def historical_v2(client, params, event, snapshot_hint=None):
    """Documented historical successor; never fetches present-day OSM as fallback."""
    import pyarrow.parquet as parquet
    api_key = os.getenv("OHSOME_API_KEY")
    if not api_key:
        raise ValueError("The historical OSM v1 service is unavailable. Its v2 successor requires OHSOME_API_KEY from the free HeiGIT API account. Set the key and restart; no current-OSM fallback is allowed.")
    headers = {"Authorization": api_key}
    metadata = client.get(f"{OSM_V2_URL}/metadata", headers=headers)
    metadata.raise_for_status()
    body = metadata.json()
    snapshot = choose_snapshot(body, event, snapshot_hint)
    timestamp = snapshot.isoformat().replace("+00:00", "Z")
    response = client.get(f"{OSM_V2_URL}/extraction/features.parquet", params={
        "aoi": ",".join(map(str, params.bbox)), "time": timestamp, "filter": OSM_FILTER, "clip": "true",
    }, headers=headers)
    response.raise_for_status()
    if not response.content.startswith(b"PAR1"):
        raise ValueError("Historical OSM v2 did not return its expected Parquet extract.")
    features = []
    for row in parquet.read_table(BytesIO(response.content)).to_pylist():
        tags = dict(row.get("tags") or {})
        edited = row.get("edit_timestamp")
        tags.update({"@osmId": f"{row['osm_type']}/{row['osm_id']}", "@version": row.get("version")})
        if edited:
            tags["@lastEdit"] = edited.isoformat().replace("+00:00", "Z") if isinstance(edited, datetime) else str(edited)
        geom = from_wkb(row["geom"])
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": tags})
    return {"type": "FeatureCollection", "features": features, "apiVersion": body.get("apiVersion"),
            "attribution": body.get("attribution"), "metadata": {"sourceApi": OSM_V2_URL, "snapshot": timestamp}}, timestamp


def osm_snapshot(params):
    event = datetime.combine(params.flood_date, datetime.min.time(), tzinfo=timezone.utc)
    snapshot = None
    with httpx.Client(timeout=240, follow_redirects=True) as client:
        try:
            metadata = client.get(f"{OSM_URL}/metadata")
            metadata.raise_for_status()
            snapshot = choose_snapshot(metadata.json(), event)
            timestamp = snapshot.isoformat().replace("+00:00", "Z")
            response = client.post(f"{OSM_URL}/elements/geometry", data={
                "bboxes": ",".join(map(str, params.bbox)), "time": timestamp,
                "filter": OSM_FILTER, "properties": "tags,metadata", "clipGeometry": "true",
            })
            response.raise_for_status()
            data = response.json()
            data.setdefault("metadata", {})["sourceApi"] = OSM_URL
        except httpx.HTTPError:
            # v1's snapshot geometry endpoint can fail while its documented
            # full-history endpoint remains available. Select one instant from
            # a pre-event interval; never substitute bounding boxes for geometry.
            try:
                if snapshot is None:
                    raise httpx.RequestError("v1 metadata unavailable")
                data, timestamp = historical_v1_interval(client, params, snapshot)
            except httpx.HTTPError as error:
                try:
                    data, timestamp = historical_v2(client, params, event, snapshot)
                except httpx.HTTPError as error:
                    status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else "network error"
                    raise ValueError(f"Historical OSM extraction is unavailable ({status}). No current OSM or demo fallback was used.") from error
    if data.get("type") != "FeatureCollection":
        raise ValueError("Historical OSM provider did not return a FeatureCollection.")
    # Defensive check on metadata; never silently substitute present-day OSM.
    for feature in data["features"]:
        edited = feature.get("properties", {}).get("@lastEdit")
        if edited and datetime.fromisoformat(edited.replace("Z", "+00:00")) >= event:
            raise ValueError("OSM feature was edited after the event; refusing this input.")
    return data, timestamp


def interval_snapshot(data, instant):
    """Keep only geometries valid at this instant, including child-node history."""
    if data.get("type") != "FeatureCollection":
        raise ValueError("Historical OSM extraction was not a FeatureCollection.")
    selected = {}
    for feature in data["features"]:
        props = feature.get("properties", {})
        if not all(props.get(k) for k in ("@osmId", "@validFrom", "@validTo")):
            raise ValueError("Historical OSM geometry is missing its validity interval.")
        start = datetime.fromisoformat(props["@validFrom"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(props["@validTo"].replace("Z", "+00:00"))
        if start <= instant < end:
            key = props["@osmId"]
            if key in selected:
                raise ValueError("Multiple historical OSM versions are valid at the same instant.")
            selected[key] = feature
    return {**data, "features": list(selected.values())}


def historical_v1_interval(client, params, available_until):
    instant = available_until - timedelta(seconds=2)
    timestamp = instant.isoformat().replace("+00:00", "Z")
    end = (instant + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    response = client.post(f"{OSM_URL}/elementsFullHistory/geometry", data={
        "bboxes": ",".join(map(str, params.bbox)), "time": f"{timestamp},{end}",
        "filter": OSM_FILTER, "properties": "tags,metadata", "clipGeometry": "true",
    })
    response.raise_for_status()
    data = interval_snapshot(response.json(), instant)
    data["metadata"] = {"sourceApi": OSM_URL, "endpoint": "elementsFullHistory/geometry",
                        "snapshot": timestamp, "requestedInterval": [timestamp, end],
                        "selection": "validFrom <= snapshot < validTo; one geometry per OSM ID"}
    return data, timestamp


class CopernicusProvider:
    def __init__(self, grid, folder):
        self.grid, self.folder = grid, Path(folder)
        client_id, secret = os.getenv("CDSE_CLIENT_ID"), os.getenv("CDSE_CLIENT_SECRET")
        if not client_id or not secret:
            raise ValueError("Live mode needs CDSE_CLIENT_ID and CDSE_CLIENT_SECRET from a Copernicus Sentinel Hub OAuth client. The synthetic demo works without credentials.")
        with httpx.Client(timeout=30) as client:
            response = client.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
            if response.status_code != 200:
                raise ValueError("Copernicus authentication failed. Check the Sentinel Hub OAuth client in your CDSE account.")
            self.token = response.json()["access_token"]

    def process(self, kind, output_name, scene=None):
        bands = {"sentinel-1-grd": ["VV", "dataMask", "shadowMask"], "sentinel-2-l2a": ["B03", "B04", "B08", "B11", "SCL", "dataMask"], "dem": ["DEM"]}[kind]
        evaluation = f"//VERSION=3\nfunction setup(){{return {{input:{bands!r},output:{{bands:{len(bands)},sampleType:'FLOAT32'}}}};}}\nfunction evaluatePixel(s){{return [{','.join('s.' + band for band in bands)}];}}"
        data_filter = {}
        processing = {}
        if scene:
            acquired = scene.acquired if isinstance(scene, Scene) else datetime.fromisoformat(scene["properties"]["datetime"].replace("Z", "+00:00"))
            # A narrow acquisition window selects this pass; neighboring swath slices can
            # contribute, all sharing its track. No before/after date-range mosaic.
            data_filter["timeRange"] = {"from": (acquired - timedelta(seconds=30)).isoformat(), "to": (acquired + timedelta(seconds=60)).isoformat()}
        if kind == "sentinel-1-grd":
            data_filter.update({"acquisitionMode": "IW", "polarization": "DV", "orbitDirection": scene.direction.upper()})
            processing = {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30", "speckleFilter": {"type": "NONE"}}
        elif kind == "dem":
            data_filter["demInstance"] = "COPERNICUS_30"
        payload = {
            "input": {"bounds": {"bbox": self.grid.bounds, "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{self.grid.crs.split(':')[1]}"}},
                      "data": [{"type": kind, "dataFilter": data_filter, "processing": processing}]},
            "output": {"width": self.grid.width, "height": self.grid.height, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": evaluation,
        }
        with httpx.Client(timeout=240) as client:
            response = client.post(PROCESS_URL, headers={"Authorization": f"Bearer {self.token}"}, json=payload)
        if response.status_code != 200:
            # Do not dump requests/headers or authentication responses into the UI/logs.
            raise ValueError(f"Copernicus processing returned HTTP {response.status_code} for {kind}. Check account quota, scene availability and service status.")
        (self.folder / f"{output_name}.tif").write_bytes(response.content)
        with MemoryFile(response.content) as memory, memory.open() as source:
            if source.crs is None:
                raise ValueError("Satellite TIFF is missing its coordinate reference system.")
            array = np.full((len(bands), self.grid.height, self.grid.width), np.nan, dtype="float32")
            for index in range(len(bands)):
                reproject(source.read(index + 1), array[index], src_transform=source.transform, src_crs=source.crs,
                          dst_transform=self.grid.transform, dst_crs=self.grid.crs, resampling=Resampling.nearest, dst_nodata=np.nan)
        if kind != "dem" and not np.any(array[bands.index("dataMask")] > 0):
            raise ValueError(f"No valid {kind} pixels returned for this acquisition.")
        return array


def acquire_live(params, grid, folder, progress):
    provider = CopernicusProvider(grid, folder)
    progress("Fetching strictly pre-event historical OpenStreetMap", 8)
    # Verify OSM access before consuming image-processing quota.
    osm, timestamp = osm_snapshot(params)
    start = (params.flood_date - timedelta(days=params.search_days)).isoformat() + "T00:00:00Z"
    end = (params.flood_date + timedelta(days=params.search_days)).isoformat() + "T23:59:59Z"
    progress("Searching matching Sentinel-1 orbit tracks", 12)
    items = search_catalog(params.bbox, start, end)
    scenes = select_pair(items, params)
    progress("Processing pre-event Sentinel-1 GRD with terrain correction", 23)
    before = provider.process("sentinel-1-grd", "s1-before", scenes[0])
    progress("Processing post-event Sentinel-1 GRD with terrain correction", 38)
    after = provider.process("sentinel-1-grd", "s1-after", scenes[1])
    progress("Fetching Copernicus DEM", 50)
    dem = provider.process("dem", "dem")[0]
    optical_before, optical_after, optical_scenes = None, None, []
    warnings = []
    progress("Checking clear-sky Sentinel-2 support", 60)
    try:
        optical = search_catalog(params.bbox, start, end, "sentinel-2-l2a")
        area = box(*params.bbox)
        usable = [item for item in optical if item["properties"].get("eo:cloud_cover", 100) <= 60 and shape(item["geometry"]).intersection(area).area / area.area >= .98]
        pre = [i for i in usable if i["properties"]["datetime"][:10] < str(params.flood_date)]
        post = [i for i in usable if i["properties"]["datetime"][:10] > str(params.flood_date)]
        def optical_score(item):
            days = abs((datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00")).date() - params.flood_date).days)
            return item["properties"].get("eo:cloud_cover", 100) + days
        if pre and post:
            pre_item, post_item = min(pre, key=optical_score), min(post, key=optical_score)
            optical_before = provider.process("sentinel-2-l2a", "s2-before", pre_item)
            optical_after = provider.process("sentinel-2-l2a", "s2-after", post_item)
            optical_scenes = [pre_item["id"], post_item["id"]]
        else:
            warnings.append("No suitable pre/post optical pair. Debris candidates are unavailable; radar water analysis continues.")
    except (httpx.HTTPError, ValueError, KeyError):
        warnings.append("Optical provider unavailable. Radar water analysis continues without debris candidates.")
        optical_before, optical_after = None, None
    return {"before": before, "after": after, "dem": dem, "osm": osm, "scenes": scenes, "snapshot": timestamp,
            "optical_before": optical_before, "optical_after": optical_after, "optical_scenes": optical_scenes, "warnings": warnings}
