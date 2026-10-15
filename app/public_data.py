"""Public raw Sentinel downloads with local Copernicus-DEM terrain correction.

The public mirror serves the original ESA GRD measurement and annotation files.
Ready-made RTC from that mirror is deliberately excluded: it uses PlanetDEM.
"""
from datetime import datetime, timedelta, timezone
from functools import cached_property
import hashlib
import json
import math
import time
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling
from scipy.interpolate import interp1d

from app.storage import ROOT, save_json, sha256
from app.providers import osm_snapshot, select_pair, interval_snapshot

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
REMOTE_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_TIMEOUT": 90, "GDAL_CACHEMAX": 64 * 1024 * 1024}


def catalog(params, collection):
    event = params.flood_date
    payload = {"collections": [collection], "bbox": params.bbox, "limit": 100,
               "datetime": f"{event-timedelta(days=params.search_days)}T00:00:00Z/{event+timedelta(days=params.search_days)}T23:59:59Z"}
    items = []
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        url = STAC
        for _ in range(20):
            response = client.post(url, json=payload); response.raise_for_status()
            body = response.json(); items.extend(body.get("features", []))
            link = next((link for link in body.get("links", []) if link["rel"] == "next"), None)
            if not link:
                return items
            url = link["href"]
            payload = link.get("body", payload)
    raise ValueError("Public catalog query exceeded its page limit. Reduce area or date range.")


def signed_url(url):
    with httpx.Client(timeout=45) as client:
        for attempt in range(4):
            response = client.get(SIGN, params={"href": url})
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()["href"]


def read_grid(url, grid, resampling=Resampling.nearest):
    with rasterio.Env(**REMOTE_ENV), rasterio.open(url) as source, WarpedVRT(
        source, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
        resampling=resampling, dtype="float32", nodata=np.nan,
    ) as vrt:
        return vrt.read(1)


def write_grid(path, array, grid):
    with rasterio.open(path, "w", driver="GTiff", width=grid.width, height=grid.height,
                       count=1 if array.ndim == 2 else array.shape[0], dtype="float32", crs=grid.crs,
                       transform=grid.transform, nodata=np.nan, tiled=True, compress="deflate") as target:
        target.write(array[None] if array.ndim == 2 else array)


def optical_pair(items, params):
    """Select complete acquisition mosaics, including cloudy tiles for SCL masking."""
    from shapely.geometry import box, shape
    from shapely.ops import unary_union
    area = box(*params.bbox)
    groups = {}
    for item in items:
        props = item["properties"]
        key = (props["datetime"], props["platform"], props["sat:relative_orbit"])
        groups.setdefault(key, []).append(item)
    complete = []
    for key, tiles in groups.items():
        if unary_union([shape(i["geometry"]) for i in tiles]).intersection(area).area / area.area < .98:
            continue
        weights = [shape(i["geometry"]).intersection(area).area for i in tiles]
        cloud = np.average([i["properties"].get("eo:cloud_cover", 100) for i in tiles], weights=weights)
        day = datetime.fromisoformat(key[0].replace("Z", "+00:00")).date()
        complete.append((day, float(cloud) + 1.5 * abs((day-params.flood_date).days), tiles))
    pair = []
    for before in (True, False):
        candidates = [i for i in complete if (i[0] < params.flood_date if before else i[0] > params.flood_date)]
        pair.append(min(candidates, key=lambda i: i[1])[2] if candidates else None)
    return pair


def optical_mosaic(tiles, grid, cache):
    mosaic = np.full((6, grid.height, grid.width), np.nan, dtype="float32")
    mosaic[5] = 0
    for item in sorted(tiles, key=lambda i: i["properties"].get("eo:cloud_cover", 100)):
        path = cache / f'{item["id"]}-optical.tif'
        if path.exists():
            with rasterio.open(path) as source:
                array = source.read()
        else:
            # The public signature covers the storage container. Reuse it for
            # this tile's bands rather than repeatedly requesting identical SAS.
            token = signed_url(item["assets"]["B03"]["href"]).split("?", 1)[1]
            values = [read_grid(item["assets"][band]["href"] + "?" + token, grid) for band in ("B03", "B04", "B08", "B11", "SCL")]
            baseline = float(item["properties"]["s2:processing_baseline"])
            for band, name in enumerate(("B03", "B04", "B08", "B11")):
                metadata = item["assets"][name].get("raster:bands", [{}])[0]
                values[band] = values[band] * metadata.get("scale", .0001) + metadata.get("offset", -.1 if baseline >= 4 else 0)
            valid = np.all(np.isfinite(values), axis=0) & (values[4] > 0)
            array = np.stack(values + [valid.astype("float32")])
            write_grid(path, array, grid)
        # Preserve a clear observation over a cloudy overlap, without mixing
        # reflectance bands from different tiles at the same pixel.
        clear = (array[5] > 0) & np.isin(array[4], [2, 4, 5, 6])
        existing_clear = (mosaic[5] > 0) & np.isin(mosaic[4], [2, 4, 5, 6])
        use = (array[5] > 0) & ((mosaic[5] == 0) | (clear & ~existing_clear))
        mosaic[:, use] = array[:, use]
    return mosaic


def dem_tiles(grid):
    from pyproj import Transformer
    west, south, east, north = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform_bounds(*grid.bounds, densify_pts=21)
    output = np.full((grid.height, grid.width), np.nan, dtype="float32")
    urls = []
    for lat in range(math.floor(south), math.floor(north) + 1):
        for lon in range(math.floor(west), math.floor(east) + 1):
            tile = f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02}_00_{'E' if lon >= 0 else 'W'}{abs(lon):03}_00_DEM"
            url = f"https://copernicus-dem-30m.s3.amazonaws.com/{tile}/{tile}.tif"
            array = read_grid(url, grid, Resampling.bilinear)
            good = np.isfinite(array)
            output[good] = array[good]; urls.append(url)
    if not np.isfinite(output).all():
        raise ValueError("Copernicus DEM does not completely cover this terrain-correction grid.")
    return output, urls


class Calibration:
    """Interpolate per-scene calibration and thermal noise in original DN geometry."""
    def __init__(self, folder, polarization="vv"):
        self.folder = Path(folder)
        calibration = ElementTree.parse(self.folder / f"annotation/calibration/calibration-iw-{polarization}.xml")
        self.beta = self.vectors(calibration.findall(".//calibrationVector"), "betaNought")
        noise = ElementTree.parse(self.folder / f"annotation/calibration/noise-iw-{polarization}.xml")
        self.noise = self.vectors(noise.findall(".//noiseRangeVector"), "noiseRangeLut")
        self.azimuth = []
        for vector in noise.findall(".//noiseAzimuthVector"):
            self.azimuth.append((int(vector.findtext("firstAzimuthLine")), int(vector.findtext("lastAzimuthLine")),
                int(vector.findtext("firstRangeSample")), int(vector.findtext("lastRangeSample")),
                np.fromstring(vector.findtext("line"), sep=" "), np.fromstring(vector.findtext("noiseAzimuthLut"), sep=" ")))
        annotation = ElementTree.parse(self.folder / f"annotation/iw-{polarization}.xml")
        self.already_denoised = annotation.findtext(".//thermalNoiseCorrectionPerformed") == "true"

    @staticmethod
    def vectors(vectors, field):
        if not vectors:
            raise ValueError("The raw GRD product is missing its calibration/noise vectors.")
        return [(int(v.findtext("line")), np.fromstring(v.findtext("pixel"), sep=" "),
                 np.fromstring(v.findtext(field), sep=" ")) for v in vectors]

    @staticmethod
    def interpolate(vectors, lines, pixels):
        along_range = np.stack([np.interp(pixels, xp, values) for _, xp, values in vectors])
        return interp1d([v[0] for v in vectors], along_range, axis=0, bounds_error=False,
                        fill_value=(along_range[0], along_range[-1]))(lines).astype("float32")

    def correct(self, block):
        lines, pixels = block.line.values, block.pixel.values
        beta = self.interpolate(self.beta, lines, pixels)
        power = block.values.astype("float32") ** 2
        if not self.already_denoised:
            azimuth = np.ones_like(power)
            for first_line, last_line, first_pixel, last_pixel, axis, values in self.azimuth:
                rows = np.flatnonzero((lines >= first_line) & (lines <= last_line))
                cols = np.flatnonzero((pixels >= first_pixel) & (pixels <= last_pixel))
                azimuth[np.ix_(rows, cols)] = np.interp(lines[rows], axis, values)[:, None]
            power -= self.interpolate(self.noise, lines, pixels) * azimuth
        corrected = np.where((power > 0) & (block.values > 0) & (beta > 0), power / beta ** 2, np.nan)
        return block.copy(data=corrected.astype("float32"))


def raw_rtc(item, dem_path, padded, grid, cache, polarization="vv"):
    import dask
    import xarray as xr
    from sarsen import Sentinel1SarProduct, apps
    folder = cache / item["id"]
    folder.mkdir(exist_ok=True)
    output = folder / ("rtc-final.tif" if polarization == "vv" else "rtc-vh.tif")
    if output.exists():
        return read_grid(str(output), grid)
    measurement = item["assets"][polarization]["href"]
    base = measurement.rsplit("/measurement/", 1)[0]
    signed = signed_url(measurement)
    token = signed.split("?", 1)[1]
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        for name in ("manifest.safe", f"annotation/iw-{polarization}.xml", f"annotation/calibration/calibration-iw-{polarization}.xml", f"annotation/calibration/noise-iw-{polarization}.xml"):
            path = folder / name
            if not path.exists():
                response = client.get(f"{base}/{name}?{token}"); response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(response.content)
    # Explicit /vsicurl/ prevents GDAL's VRT driver downloading the full GRD file.
    with rasterio.Env(**REMOTE_ENV), rasterio.open(signed) as source:
        width, height = source.width, source.height
    pointer = folder / f"measurement/iw-{polarization}.tiff"
    pointer.parent.mkdir(exist_ok=True)
    pointer.write_text(f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}"><VRTRasterBand dataType="UInt16" band="1"><NoDataValue>0</NoDataValue><SimpleSource><SourceFilename relativeToVRT="0">/vsicurl/{escape(signed)}</SourceFilename><SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="{width}" ySize="{height}"/><DstRect xOff="0" yOff="0" xSize="{width}" ySize="{height}"/></SimpleSource></VRTRasterBand></VRTDataset>')
    calibration = Calibration(folder, polarization)
    class CorrectedProduct(Sentinel1SarProduct):
        @cached_property
        def measurement(self):
            return super().measurement
        def beta_nought(self, persist=False):
            measurement = self.measurement["measurement"]
            corrected = xr.map_blocks(calibration.correct, measurement, template=measurement.astype("float32"))
            return corrected.drop_vars(["pixel", "line"])
    product = CorrectedProduct(str(folder / "manifest.safe"), measurement_group=f"IW/{polarization.upper()}", measurement_chunks=512)
    temporary = output.with_name("rtc-processing.tif")
    try:
        with rasterio.Env(**REMOTE_ENV), dask.config.set(scheduler="synchronous", num_workers=2):
            geocoded = apps.terrain_correction(product, str(dem_path), output_urlpath=str(temporary),
                correct_radiometry="gamma_bilinear", chunks=256, radiometry_chunks=512, radiometry_bound=64)
            geocoded.close()
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        if "measurement" in product.__dict__:
            product.measurement.close()
    return read_grid(str(output), grid)


def acquire_public(params, grid, folder, progress):
    from app.geo import Grid
    cache = ROOT / "data/cache/public" / hashlib.sha256(json.dumps([params.bbox, params.resolution_m, str(params.flood_date), "denoised-rtc-v1"]).encode()).hexdigest()[:20]
    cache.mkdir(parents=True, exist_ok=True)
    progress("Fetching actual pre-event OSM geometry", 8)
    osm_path = cache / "osm.geojson"
    if osm_path.exists():
        osm = json.loads(osm_path.read_text()); timestamp = osm["metadata"]["snapshot"]
    else:
        osm, timestamp = osm_snapshot(params)
        osm.setdefault("metadata", {})["snapshot"] = timestamp
        save_json(osm_path, osm)
    progress("Selecting actual same-track Sentinel-1 GRD acquisitions", 15)
    catalog_path = cache / "radar-catalog.json"
    items = json.loads(catalog_path.read_text()) if catalog_path.exists() else catalog(params, "sentinel-1-grd")
    save_json(catalog_path, items)
    # A STAC polarization declaration does not guarantee both downloadable
    # measurements are present. Reject incomplete products before selection.
    items = [item for item in items if {"vv", "vh"}.issubset(item.get("assets", {}))]
    scenes = select_pair(items, params)
    selected = [next(item for item in items if item["id"] == scene.id) for scene in scenes]
    pad = 64
    padded = Grid(grid.crs, from_origin(grid.transform.c-pad*grid.resolution, grid.transform.f+pad*grid.resolution, grid.resolution, grid.resolution),
                  grid.width+2*pad, grid.height+2*pad, grid.resolution)
    dem_path = cache / "dem-ellipsoid.tif"
    dem_original = cache / "dem-orthometric.tif"
    progress("Downloading actual Copernicus DEM; converting its vertical datum", 22)
    if not dem_path.exists():
        elevation, dem_urls = dem_tiles(padded)
        # EGM2008 geoid is a coordinate-datum conversion, not a prediction layer.
        geoid_url = "https://cdn.proj.org/us_nga_egm08_25.tif"
        geoid = read_grid(geoid_url, padded, Resampling.bilinear)
        if not np.isfinite(geoid).all():
            raise ValueError("EGM2008 vertical-datum conversion is unavailable.")
        write_grid(dem_original, elevation, padded)
        write_grid(dem_path, elevation + geoid, padded)
        save_json(cache / "dem-sources.json", {"tiles": dem_urls, "vertical_conversion": geoid_url})
    dem = read_grid(str(dem_original), grid)
    arrays, cross_arrays, quality_arrays = [], [], []
    for index, (scene, item) in enumerate(zip(scenes, selected)):
        progress(f"Locally calibrating and terrain-correcting {'pre' if index == 0 else 'post'}-event Sentinel-1", 28+index*18)
        values = raw_rtc(item, dem_path, padded, grid, cache)
        cross = raw_rtc(item, dem_path, padded, grid, cache, polarization="vh")
        from app.terrain import scene_quality
        quality_path = cache / f"{scene.id}-terrain-quality-v3.tif"
        if not quality_path.exists():
            with rasterio.open(dem_path) as source:
                elevation = source.read(1)
            quality = scene_quality(cache / scene.id / "annotation/iw-vv.xml", elevation, padded)
            write_grid(quality_path, quality, padded)
        with rasterio.open(quality_path) as source, WarpedVRT(source, crs=grid.crs, transform=grid.transform,
            width=grid.width,height=grid.height,resampling=Resampling.nearest) as warped:
            quality = warped.read()
        valid = np.isfinite(values) & (values > 0) & np.isfinite(cross) & (cross > 0)
        arrays.append(np.stack([values, valid.astype("float32"), (quality[1] > 0).astype('float32')]))
        cross_arrays.append(cross); quality_arrays.append(quality)
        write_grid(Path(folder) / f"s1-{'before' if index == 0 else 'after'}.tif", arrays[-1], grid)
        write_grid(Path(folder) / f"s1-vh-{'before' if index == 0 else 'after'}.tif", cross, grid)
        write_grid(Path(folder) / f"terrain-{'before' if index == 0 else 'after'}.tif", quality, grid)
    optical_before, optical_after, optical_scenes = None, None, []
    optical_observations = []
    warnings = [f"Annotation-driven local layover/self-shadow and DEM ray screening are applied over a {pad * grid.resolution / 1000:g} km horizon. Visibility is approximate; distant occlusion and subpixel terrain remain uncertain."]
    progress("Downloading actual Sentinel-2 clear-sky support", 63)
    try:
        optical_catalog = cache / "optical-catalog.json"
        optical = json.loads(optical_catalog.read_text()) if optical_catalog.exists() else catalog(params, "sentinel-2-l2a")
        save_json(optical_catalog, optical)
        pair = optical_pair(optical, params)
        if all(pair):
            optical_arrays = []
            for index, tiles in enumerate(pair):
                array = optical_mosaic(tiles, grid, cache); optical_arrays.append(array)
                write_grid(Path(folder) / f"s2-{'before' if index == 0 else 'after'}.tif", array, grid)
            optical_before, optical_after = optical_arrays; optical_scenes = [i["id"] for tiles in pair for i in tiles]
            optical_observations = [{"id": item["id"], "acquired": item["properties"]["datetime"]} for tiles in pair for item in tiles]
            if any(abs((datetime.fromisoformat(tiles[0]["properties"]["datetime"].replace("Z", "+00:00")).date()-params.flood_date).days) > 12 for tiles in pair):
                warnings.append("A Sentinel-2 observation is more than 12 days from the event. Optical change can include other intervening changes; cloud masks apply pixel by pixel.")
        else:
            warnings.append("No suitable clear-sky Sentinel-2 pair covers the full area; debris estimation is unavailable.")
    except (httpx.HTTPError, ValueError, KeyError, rasterio.errors.RasterioError) as error:
        warnings.append(f"Sentinel-2 retrieval failed ({type(error).__name__}); no fabricated optical inputs were substituted. Debris estimation is unavailable.")
    return {"before": arrays[0], "after": arrays[1], "dem": dem, "osm": osm, "snapshot": timestamp, "scenes": scenes,
            "vh_before": cross_arrays[0], "vh_after": cross_arrays[1], "terrain_before": quality_arrays[0], "terrain_after": quality_arrays[1],
            "optical_before": optical_before, "optical_after": optical_after, "optical_scenes": optical_scenes, "optical_observations": optical_observations, "warnings": warnings,
            "acquisition": {"provider": "Original ESA Sentinel GRD via the Microsoft public mirror; locally processed",
                "radar_assets": [i["assets"]["vv"]["href"] for i in selected], "catalog": STAC,
                "radar_assets_vh": [i["assets"]["vh"]["href"] for i in selected],
                "terrain_screening": {"method":"Orbit/annotation local incidence, self-shadow and layover; Copernicus-DEM ray occlusion", "horizon_m":1920,"approximate":True},
                "dem": json.loads((cache / "dem-sources.json").read_text()),
                "processing": "DN squared, annotation range/azimuth thermal denoising, beta0 calibration, sarsen gamma_bilinear RTC with Copernicus GLO-30 and EGM2008-to-ellipsoid height conversion",
                "osm_sha256": sha256(osm_path)}}
