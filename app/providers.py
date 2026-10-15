"""Allowed-input adapters. Published damage maps are deliberately absent."""
from datetime import datetime, timedelta, timezone
import hashlib
import time
from io import BytesIO
import os
from pathlib import Path
import json
import tarfile
from urllib.parse import urljoin,urlsplit,urlunsplit

import httpx
import numpy as np
from rasterio.io import MemoryFile
from rasterio.warp import reproject, Resampling
from shapely import from_wkb
from shapely.geometry import box, shape, mapping
from shapely.ops import unary_union

from app.models import Scene, validate_pair
from app.presets import osm_cutoff
from app.settings import ACQUISITION,MAPPING
from app.scene_selection import optical_pair,acquired
from app.cdse_runtime import access_token,request_with_retry
from app.storage import save_json


STAC_URL = ACQUISITION.cdse_stac_url
PROCESS_URL = ACQUISITION.cdse_process_url
TOKEN_URL = ACQUISITION.cdse_token_url
OSM_URL = ACQUISITION.osm_url
OSM_V2_URL = ACQUISITION.osm_v2_url
OSM_FILTER = "building=* or highway=* or bridge=* or man_made=bridge or place in (village,hamlet,isolated_dwelling,town,city) or amenity=hospital"


def historical_request(client, method, url, **kwargs):
    """Retry transient transport/service failures without changing data policy."""
    for attempt in range(3):
        try:
            response = client.request(method,url,**kwargs)
            if response.status_code in (429,500,502,503,504) and attempt < 2:
                time.sleep(attempt+1)
                continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException,httpx.NetworkError):
            if attempt == 2:raise
            time.sleep(attempt+1)


def search_catalog(bbox, start, end, collection="sentinel-1-grd"):
    payload = {"collections": [collection], "bbox": bbox, "datetime": f"{start}/{end}", "limit": ACQUISITION.catalog_page_size}
    features = []
    method,url='POST',STAC_URL
    seen=set()
    with httpx.Client(timeout=ACQUISITION.catalog_timeout_seconds, follow_redirects=True) as client:
        for _ in range(ACQUISITION.catalog_max_pages):
            response = request_with_retry(client,method,url,**({'json':payload} if method=='POST' else {}))
            response.raise_for_status()
            data = response.json()
            for item in data.get('features',[]):
                if item.get('id') not in seen:
                    features.append(item);seen.add(item.get('id'))
            next_link = next((link for link in data.get("links", []) if link["rel"] == "next"), None)
            if not next_link:
                break
            # CDSE STAC continuation body; keep a bounded catalog query.
            target=urljoin(STAC_URL,next_link.get('href') or STAC_URL)
            parsed=urlsplit(target)
            if (parsed.scheme,parsed.netloc)!=(urlsplit(STAC_URL).scheme,urlsplit(STAC_URL).netloc):
                raise ValueError('Catalog continuation changed service origin')
            method=next_link.get('method','POST' if next_link.get('body') else 'GET').upper()
            if method not in ('GET','POST') or not (next_link.get('href') or next_link.get('body')):
                raise ValueError("Catalog search needs more pages. Reduce the area or search window.")
            if method=='POST':payload={**payload,**next_link.get('body',{})}
            url=target
        else:
            raise ValueError("Catalog page limit reached. Reduce the area or search window.")
    return features


def scene_from_item(item):
    props = item["properties"]
    if not {"VV", "VH"}.issubset(props.get("sar:polarizations", [])):
        raise ValueError("This acquisition path requires both VV and VH polarizations")
    return Scene(id=item["id"], acquired=datetime.fromisoformat(props["datetime"].replace("Z", "+00:00")),
                 relative_orbit=props["sat:relative_orbit"], direction=props["sat:orbit_state"],
                 platform=props["platform"],polarization='+'.join(sorted(props['sar:polarizations'])), instrument_mode=props.get("sar:instrument_mode", "UNKNOWN"))


def select_pair(items, params, *, allow_mosaics=False):
    area = box(*params.bbox)
    eligible = []
    partial=[]
    for item in items:
        try:
            scene = scene_from_item(item)
            footprint=shape(item['geometry']).intersection(area)
            if scene.instrument_mode=='IW' and not footprint.is_empty and footprint.is_valid:
                partial.append((scene,footprint))
                if footprint.area/area.area>=ACQUISITION.minimum_scene_coverage:eligible.append(scene)
        except (ValueError, KeyError):
            continue
    if allow_mosaics:
        groups={}
        for scene,footprint in partial:
            key=(scene.acquired.date(),scene.platform,scene.relative_orbit,scene.direction,scene.polarization,scene.instrument_mode)
            groups.setdefault(key,[]).append((scene,footprint))
        for records in groups.values():
            passes=[]
            for record in sorted(records,key=lambda value:value[0].acquired):
                if not passes or (record[0].acquired-passes[-1][0][0].acquired).total_seconds()>ACQUISITION.radar_pass_tolerance_seconds:
                    passes.append([])
                passes[-1].append(record)
            for records in passes:
                if len(records)<2 or unary_union([g for _,g in records]).area/area.area<ACQUISITION.minimum_scene_coverage:continue
                identifiers=list(dict.fromkeys(scene.id for scene,_ in records))
                eligible.append(records[0][0].model_copy(update={'catalog_ids':identifiers,'acquisition_end':records[-1][0].acquired}))
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
            score = delay + ACQUISITION.radar_pre_gap_weight * pre_gap + abs(interval - ACQUISITION.radar_repeat_days) * ACQUISITION.radar_repeat_weight
            pairs.append((score, before, after))
    if not pairs:
        raise ValueError("No same-platform, same-track IW/VV+VH pair covering this entire area. Reduce the area or widen the search window. The flood day itself is excluded.")
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


def choose_snapshot(body, event, hint=None, cutoff=None):
    earliest, latest = metadata_times(body)
    limit = event - timedelta(seconds=1)
    if cutoff is not None:
        limit = min(limit, cutoff)
    snapshot = min(latest, limit, hint) if hint else min(latest, limit)
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
    snapshot = choose_snapshot(body, event, snapshot_hint, osm_cutoff(params))
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
    with httpx.Client(timeout=httpx.Timeout(240,connect=20), follow_redirects=True) as client:
        try:
            metadata = historical_request(client,"GET",f"{OSM_URL}/metadata")
            metadata.raise_for_status()
            snapshot = choose_snapshot(metadata.json(), event, cutoff=osm_cutoff(params))
            timestamp = snapshot.isoformat().replace("+00:00", "Z")
            response = historical_request(client,"POST",f"{OSM_URL}/elements/geometry", data={
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
        if edited:
            edited_at = datetime.fromisoformat(edited.replace("Z", "+00:00"))
            if edited_at >= event:
                raise ValueError("OSM feature was edited after the event; refusing this input.")
            if edited_at > datetime.fromisoformat(timestamp.replace("Z", "+00:00")):
                raise ValueError("OSM feature was edited after the requested historical snapshot; refusing this input.")
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
    response = historical_request(client,"POST",f"{OSM_URL}/elementsFullHistory/geometry", data={
        "bboxes": ",".join(map(str, params.bbox)), "time": f"{timestamp},{end}",
        "filter": OSM_FILTER, "properties": "tags,metadata", "clipGeometry": "true",
    })
    response.raise_for_status()
    data = interval_snapshot(response.json(), instant)
    data["metadata"] = {"sourceApi": OSM_URL, "endpoint": "elementsFullHistory/geometry",
                        "snapshot": timestamp, "requestedInterval": [timestamp, end],
                        "selection": "validFrom <= snapshot < validTo; one geometry per OSM ID"}
    return data, timestamp


class CopernicusProcessingError(ValueError):
    def __init__(self, status_code, kind):
        self.status_code = status_code
        self.kind = kind
        super().__init__(f"Copernicus processing returned HTTP {status_code} for {kind}. Check account quota, scene availability and service status.")


class CopernicusProvider:
    def __init__(self, grid, folder):
        self.grid, self.folder = grid, Path(folder)
        self.token = access_token(TOKEN_URL)
        self.observations={}

    def process(self, kind, output_name, scene=None):
        bands = {"sentinel-1-grd": ["VV", "dataMask", "shadowMask", "VH"], "sentinel-2-l2a": ["B03", "B04", "B08", "B11", "SCL", "dataMask"], "dem": ["DEM"]}[kind]
        satellite=kind!='dem'
        if satellite and not scene:raise ValueError('Satellite processing requires a selected catalog acquisition')
        returns=','.join('s.'+band for band in bands)
        if satellite:
            allowed=[code for code in range(12) if code not in MAPPING.optical_excluded_scl_classes]
            quality=f'{json.dumps(allowed)}.indexOf(s.SCL)>=0' if kind=='sentinel-2-l2a' else 's.shadowMask===0'
            evaluation=(f"//VERSION=3\nfunction setup(){{return {{input:{json.dumps(bands)},mosaicking:'TILE',output:{{bands:{len(bands)},sampleType:'FLOAT32'}}}};}}\n"
                f"function evaluatePixel(samples){{var first=null;for(var i=0;i<samples.length;i++){{var s=samples[i];if(s.dataMask>0){{if(first===null)first=s;if({quality})return [{returns}];}}}}if(first!==null){{var s=first;return [{returns}];}}return {json.dumps([0]*len(bands))};}}\n"
                "function updateOutputMetadata(scenes,inputMetadata,outputMetadata){outputMetadata.userData={tiles:scenes.tiles.map(function(t){return {date:t.date,shId:t.shId,dataPath:t.dataPath};})};}")
        else:
            evaluation=f"//VERSION=3\nfunction setup(){{return {{input:{json.dumps(bands)},output:{{bands:{len(bands)},sampleType:'FLOAT32'}}}};}}\nfunction evaluatePixel(s){{return [{returns}];}}"
        data_filter = {}
        processing = {}
        if scene:
            selected=scene if isinstance(scene,list) else [scene]
            times=[instant for item in selected for instant in ((item.acquired,item.acquisition_end or item.acquired) if isinstance(item,Scene) else (acquired(item),))]
            # A narrow radar window selects this pass, including adjacent slices.
            first,last=min(times),max(times)
            lower=max(first-timedelta(seconds=ACQUISITION.scene_window_before_seconds),first.replace(hour=0,minute=0,second=0,microsecond=0))
            upper=min(last+timedelta(seconds=ACQUISITION.scene_window_after_seconds),last.replace(hour=23,minute=59,second=59,microsecond=999999))
            if kind=='sentinel-2-l2a':
                # CDSE STAC timestamps describe the datatake start, whereas
                # Sentinel Hub tile times describe local sensing. A tiny S1
                # pass window can therefore return no S2 pixels. Query the
                # selected UTC day and retain actual processed tile dates/IDs.
                lower=first.replace(hour=0,minute=0,second=0,microsecond=0)
                upper=last.replace(hour=23,minute=59,second=59,microsecond=999999)
            data_filter["timeRange"] = {"from":lower.isoformat(),"to":upper.isoformat()}
        if kind == "sentinel-1-grd":
            data_filter.update({"acquisitionMode": "IW", "polarization": "DV", "orbitDirection": scene.direction.upper()})
            processing = {"orthorectify": True, "backCoeff": "GAMMA0_TERRAIN", "demInstance": "COPERNICUS_30", "speckleFilter": {"type": "NONE"}}
        elif kind == "dem":
            data_filter["demInstance"] = "COPERNICUS_30"
        elif kind=='sentinel-2-l2a':
            data_filter['mosaickingOrder']='leastCC'
            # Per-tile cloud percentages are ranking hints, not AOI masks.
            # SCL and dataMask decide which returned pixels are usable.
            data_filter['maxCloudCoverage']=100
        payload = {
            "input": {"bounds": {"bbox": self.grid.bounds, "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{self.grid.crs.split(':')[1]}"}},
                      "data": [{"type": kind, "dataFilter": data_filter, "processing": processing}]},
            "output": {"width": self.grid.width, "height": self.grid.height, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]+([{'identifier':'userdata','format':{'type':'application/json'}}] if satellite else [])},
            "evalscript": evaluation,
        }
        with httpx.Client(timeout=ACQUISITION.process_timeout_seconds) as client:
            self.token=access_token(TOKEN_URL)
            for attempt in range(2):
                response = request_with_retry(client,'POST',PROCESS_URL,milliseconds=True,
                    headers={"Authorization":f'Bearer {self.token}','Accept':'application/x-tar' if satellite else 'image/tiff'},json=payload)
                if response.status_code!=401 or attempt:break
                self.token=access_token(TOKEN_URL,invalidate=self.token)
        if response.status_code != 200:
            # Do not dump requests/headers or authentication responses into the UI/logs.
            raise CopernicusProcessingError(response.status_code, kind)
        content=response.content
        metadata=None
        if satellite:
            try:
                with tarfile.open(fileobj=BytesIO(content),mode='r:*') as archive:
                    members=archive.getmembers()
                    images=[member for member in members if member.name in ('default.tif','default.tiff')]
                    details=[member for member in members if member.name=='userdata.json']
                    if len(images)!=1 or len(details)!=1 or not images[0].isfile() or not details[0].isfile():raise ValueError('Missing unique imagery/metadata')
                    if images[0].size>self.grid.width*self.grid.height*len(bands)*8+1024*1024 or details[0].size>4*1024*1024:
                        raise ValueError('Oversized processing output')
                    content=archive.extractfile(images[0]).read()
                    metadata=json.load(archive.extractfile(details[0]))
                tiles=metadata['tiles']
                if not isinstance(tiles,list) or not tiles or len(tiles)>ACQUISITION.catalog_page_size*ACQUISITION.catalog_max_pages:
                    raise ValueError('Missing or excessive processed tiles')
                clean=[]
                for tile in tiles:
                    instant=datetime.fromisoformat(tile['date'].replace('Z','+00:00'))
                    if instant.tzinfo is None or not lower<=instant<=upper:raise ValueError('Processing returned an unexpected observation date')
                    path=urlsplit(str(tile.get('dataPath','')))
                    clean.append({'date':instant.isoformat(),'shId':tile.get('shId'),
                                  'dataPath':urlunsplit((path.scheme,path.netloc,path.path,'',''))})
                metadata={'selected_catalog_ids':[identifier for item in selected for identifier in ((item.catalog_ids or [item.id]) if isinstance(item,Scene) else [item['id']])],
                          'processed_tiles':clean,'time_range':data_filter['timeRange'],'mosaicking':'TILE; prefer observed non-shadow/clear samples'}
            except (tarfile.TarError,OSError,KeyError,TypeError,ValueError):
                raise ValueError('CDSE imagery or processed-scene metadata is invalid; refusing unverified observations') from None
        with MemoryFile(content) as memory, memory.open() as source:
            if source.crs is None:
                raise ValueError("Satellite TIFF is missing its coordinate reference system.")
            if source.count!=len(bands):raise ValueError('CDSE raster band count does not match the requested sensor contract')
            array = np.full((len(bands), self.grid.height, self.grid.width), np.nan, dtype="float32")
            for index in range(len(bands)):
                reproject(source.read(index + 1), array[index], src_transform=source.transform, src_crs=source.crs,
                          dst_transform=self.grid.transform, dst_crs=self.grid.crs, resampling=Resampling.nearest, dst_nodata=np.nan)
        if kind != "dem" and not np.any(array[bands.index("dataMask")] > 0):
            raise ValueError(f"No valid {kind} pixels returned for this acquisition.")
        (self.folder/f'{output_name}.tif').write_bytes(content)
        if metadata:
            self.observations[output_name]=metadata
            save_json(self.folder/f'{output_name}-sources.json',metadata)
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
    scenes = select_pair(items, params, allow_mosaics=True)
    progress("Processing pre-event Sentinel-1 GRD with terrain correction", 23)
    before = provider.process("sentinel-1-grd", "s1-before", scenes[0])
    progress("Processing post-event Sentinel-1 GRD with terrain correction", 38)
    after = provider.process("sentinel-1-grd", "s1-after", scenes[1])
    progress("Fetching Copernicus DEM", 50)
    warnings = []
    dem_source = {"provider": "Copernicus Data Space Sentinel Hub", "product": "COPERNICUS_30"}
    try:
        dem = provider.process("dem", "dem")[0]
    except CopernicusProcessingError as error:
        if error.status_code != 403:
            raise
        # The public mirror supplies the same allowed GLO-30 product; this is
        # a recorded real-data source change, never a synthetic replacement.
        from app.public_data import dem_tiles, write_grid
        progress("CDSE DEM access denied; fetching public Copernicus GLO-30", 52)
        dem, urls = dem_tiles(grid)
        write_grid(Path(folder) / "dem.tif", dem, grid)
        dem_source = {"provider": "Public Copernicus DEM GLO-30 mirror", "tiles": urls,
                      "fallback_reason": "CDSE DEM Processing API returned HTTP 403"}
        warnings.append("CDSE denied the DEM request (HTTP 403). Actual Copernicus GLO-30 elevation was retrieved from its public mirror; satellite imagery remains from CDSE.")
    optical_before, optical_after, optical_scenes = None, None, []
    optical_observations = []
    progress("Checking clear-sky Sentinel-2 support", 60)
    try:
        optical = search_catalog(params.bbox, start, end, "sentinel-2-l2a")
        pair=optical_pair(optical,params)
        if all(pair):
            optical_before=provider.process('sentinel-2-l2a','s2-before',pair[0])
            optical_after=provider.process('sentinel-2-l2a','s2-after',pair[1])
            processed=getattr(provider,'observations',{})
            if all('s2-'+epoch in processed for epoch in ('before','after')):
                for epoch in ('before','after'):
                    for tile in processed['s2-'+epoch]['processed_tiles']:
                        identifier=('CDSE-SH-'+str(tile['shId'])) if tile.get('shId') is not None else ('CDSE-SH-'+hashlib.sha256((tile['dataPath']+tile['date']).encode()).hexdigest()[:20])
                        if identifier not in optical_scenes:
                            optical_scenes.append(identifier)
                            optical_observations.append({'id':identifier,'acquired':tile['date'],'epoch':epoch,'source':'actual Sentinel Hub processed tile metadata'})
            else:
                optical_scenes=[item['id'] for tiles in pair for item in tiles]
                optical_observations=[{'id':item['id'],'acquired':item['properties']['datetime'],'epoch':epoch}
                                      for epoch,tiles in zip(('before','after'),pair) for item in tiles]
        else:
            warnings.append("No suitable pre/post optical pair. Debris candidates are unavailable; radar water analysis continues.")
    except (httpx.HTTPError, ValueError, KeyError):
        warnings.append("Optical provider unavailable. Radar water analysis continues without debris candidates.")
        optical_before, optical_after = None, None
        optical_scenes,optical_observations=[],[]
        for epoch in ('before','after'):
            path=Path(folder)/f's2-{epoch}.tif'
            if path.exists():path.replace(path.with_name('unpaired-'+path.name))
    return {"before": before, "after": after, "vh_before": before[3], "vh_after": after[3], "dem": dem, "osm": osm, "scenes": scenes, "snapshot": timestamp,
            "optical_before": optical_before, "optical_after": optical_after, "optical_scenes": optical_scenes, "optical_observations": optical_observations, "warnings": warnings,
            "acquisition": {"provider": "Copernicus Data Space Sentinel Hub Processing API", "catalog": STAC_URL,
                            "process_endpoint": PROCESS_URL, "radar_scene_ids": [scene.id for scene in scenes],
                            "radar_catalog_ids":[scene.catalog_ids or [scene.id] for scene in scenes],
                            "dem": dem_source,
                            "processed_observations":getattr(provider,'observations',{}),
                            "processing": "Sentinel Hub VV/VH gamma0 terrain backscatter, orthorectified using COPERNICUS_30; data and shadow masks retained"}}
