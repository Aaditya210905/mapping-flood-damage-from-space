"""Metric raster grid, conservative change detection, vectorization and DEM screening."""
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import math

import numpy as np
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling
from scipy import ndimage
from shapely.geometry import LineString, Point, mapping, shape
from shapely import transform as transform_geometry


@lru_cache(maxsize=128)
def transformer(source, destination):
    return Transformer.from_crs(source, destination, always_xy=True)


@dataclass
class Grid:
    crs: str
    transform: object
    width: int
    height: int
    resolution: float

    @classmethod
    def for_bbox(cls, bbox, resolution):
        west, south, east, north = bbox
        lon, lat = (west + east) / 2, (south + north) / 2
        zone = min(60, max(1, int((lon + 180) // 6) + 1))
        crs = f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"
        project = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        x0, y0, x1, y1 = project.transform_bounds(west, south, east, north, densify_pts=21)
        width, height = math.ceil((x1 - x0) / resolution), math.ceil((y1 - y0) / resolution)
        if width < 3 or height < 3:
            raise ValueError("Choose an area at least three pixels wide and high at this resolution.")
        if width > 2500 or height > 2500:
            raise ValueError("Raster exceeds 2500 pixels per side. Increase resolution or reduce the area.")
        return cls(crs, from_origin(x0, y1, resolution, resolution), width, height, resolution)

    @property
    def bounds(self):
        t = self.transform
        return [t.c, t.f - self.height * self.resolution, t.c + self.width * self.resolution, t.f]

    def project(self, geometry):
        return transform_geometry(geometry, transformer("EPSG:4326", self.crs).transform, interleaved=False)

    def geographic(self, geometry):
        return transform_geometry(geometry, transformer(self.crs, "EPSG:4326").transform, interleaved=False)

    def cell_for(self, lon, lat):
        p = self.project(Point(lon, lat))
        return math.floor((self.transform.f - p.y) / self.resolution), math.floor((p.x - self.transform.c) / self.resolution)

    def center(self, row, col):
        return self.geographic(Point(self.transform.c + (col + .5) * self.resolution, self.transform.f - (row + .5) * self.resolution))


def clean_mask(mask, minimum):
    labels, count = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= minimum
    keep[0] = False
    return keep[labels] if count else np.zeros_like(mask, dtype=bool)


def slope_degrees(dem, resolution):
    dy, dx = np.gradient(dem.astype(float), resolution)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def detect_changes(before, after, grid, params, dem=None, optical_before=None, optical_after=None):
    """S1 arrays are [VV linear gamma0, dataMask, shadowMask]. S2: B03,B04,B08,B11,SCL,dataMask."""
    valid = (before[1] > 0) & (after[1] > 0) & (before[0] > 0) & (after[0] > 0)
    valid &= np.isfinite(before[0]) & np.isfinite(after[0])
    if before.shape[0] > 2 and after.shape[0] > 2:
        valid &= (before[2] == 0) & (after[2] == 0)
    radar_valid = valid.copy()
    low_slope = np.ones(valid.shape,dtype=bool) if dem is None else np.isfinite(dem) & (slope_degrees(dem, grid.resolution) < params.max_slope_deg)
    valid &= low_slope
    # Median in log space suppresses isolated speckle; invalid neighbors are never treated as water.
    raw_before_db = np.where(radar_valid, 10 * np.log10(np.maximum(before[0], 1e-10)), np.nan)
    raw_after_db = np.where(radar_valid, 10 * np.log10(np.maximum(after[0], 1e-10)), np.nan)
    before_db = ndimage.median_filter(np.nan_to_num(raw_before_db, nan=-10), size=3)
    after_db = ndimage.median_filter(np.nan_to_num(raw_after_db, nan=-10), size=3)
    drop = before_db - after_db
    permanent = valid & (before_db < params.water_threshold_db)
    water = valid & ~permanent & (after_db < params.water_threshold_db) & (drop > params.change_threshold_db)
    debris = np.zeros_like(water)
    optical_valid = np.zeros_like(water)
    optical_water = np.zeros_like(water)
    if optical_before is not None and optical_after is not None:
        excluded = [0, 1, 3, 7, 8, 9, 10, 11]
        optical_valid = (optical_before[5] > 0) & (optical_after[5] > 0)
        optical_valid &= ~np.isin(optical_before[4], excluded) & ~np.isin(optical_after[4], excluded)
        optical_valid &= np.all(np.isfinite(optical_before[:4]), axis=0) & np.all(np.isfinite(optical_after[:4]), axis=0)
        optical_valid &= np.all(optical_before[:4] >= 0,axis=0) & np.all(optical_after[:4] >= 0,axis=0)
        def index(a, b):
            return np.divide(a-b,a+b,out=np.full(a.shape,np.nan,dtype='float32'),where=(a+b)>1e-6)
        pre_ndvi = index(optical_before[2], optical_before[1])
        post_ndvi = index(optical_after[2], optical_after[1])
        pre_water = index(optical_before[0], optical_before[3])
        post_water = index(optical_after[0], optical_after[3])
        optical_water = optical_valid & low_slope & ~permanent & (post_water > .2) & (pre_water <= .2)
        # Vegetation loss + radar change is only a debris candidate; wet soil/harvest can look similar.
        if params.mode == "demo":
            debris = optical_valid & valid & ~permanent & ~water & ~optical_water
            debris &= (pre_ndvi > .3) & ((pre_ndvi - post_ndvi) > .25) & (np.abs(drop) > params.change_threshold_db)
        else:
            # Optical evidence remains observed on steep slopes and in radar
            # shadow. Require both vegetation loss and increased bare surface;
            # never use corrupted radar as confirmation there.
            pre_bare = index(optical_before[3]+optical_before[1], optical_before[2]+optical_before[0])
            post_bare = index(optical_after[3]+optical_after[1], optical_after[2]+optical_after[0])
            debris = optical_valid & (pre_water <= .2) & (post_water <= .2) & ~water & ~optical_water
            debris &= (pre_ndvi > .3) & ((pre_ndvi-post_ndvi) > .25) & ((post_bare-pre_bare) > .1)
            # Require radar change where radar is interpretable. Optical-only
            # targets use stricter spectral changes and remain low confidence.
            corroborated = radar_valid & (np.abs(drop) > params.change_threshold_db)
            optical_only = ~radar_valid & ((pre_ndvi-post_ndvi) > .4) & ((post_bare-pre_bare) > .2)
            debris &= corroborated | optical_only
        permanent |= optical_valid & low_slope & (pre_water > .2)
        water |= optical_water
    water_eligible = valid | (optical_valid & low_slope)
    debris_eligible = optical_valid & (valid if params.mode == "demo" else np.ones(valid.shape,dtype=bool))
    valid = water_eligible | debris_eligible
    water = clean_mask(water, params.min_component_pixels)
    debris = clean_mask(debris, params.min_component_pixels)
    classes = np.zeros(water.shape, dtype=np.uint8)
    classes[permanent] = 1
    classes[water] = 2
    classes[debris] = 3
    classes[~valid] = 255
    return {
        "classes": classes, "water": water, "debris": debris, "valid": valid,
        "permanent": permanent, "before_db": before_db, "after_db": after_db,
        "optical_valid": optical_valid, "optical_water": optical_water,
        "radar_valid": radar_valid, "water_eligible": water_eligible, "debris_eligible": debris_eligible,
    }


def mask_geometries(mask, grid):
    return [shape(g) for g, value in shapes(mask.astype("uint8"), mask=mask, transform=grid.transform) if value == 1]


def extent_features(result, grid):
    features = []
    for kind, confidence in (("water", "moderate"), ("debris", "low")):
        for i, geom in enumerate(mask_geometries(result[kind], grid)):
            features.append({"type": "Feature", "geometry": mapping(grid.geographic(geom)), "properties": {
                "id": f"{kind}-{i}", "kind": kind, "confidence": confidence,
                "area_m2": round(geom.area, 2), "interpretation": "potential flood water" if kind == "water" else "possible debris or land-cover change",
            }})
    return {"type": "FeatureCollection", "features": features}


def image_bytes(result, dem=None, epoch="after", grid=None):
    values = result[f"{epoch}_db"]
    gray = np.clip(np.nan_to_num((values + 28) / 25, nan=.4, posinf=1, neginf=0), 0, 1)
    rgb = np.stack([gray * .52 + .14, gray * .61 + .18, gray * .53 + .16], axis=-1)
    if dem is not None:
        dy, dx = np.gradient(dem.astype(float))
        shade = np.clip(.65 + (-dx + dy) * .025, .25, 1)
        rgb *= shade[..., None]
    rgb[result["permanent"]] = [.22, .49, .55]
    rgb[~result["valid"]] = rgb[~result["valid"]] * .65 + np.array([.38, .40, .34]) * .35
    pixels = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    display_bbox = None
    if grid is not None:
        transform, width, height = calculate_default_transform(grid.crs, "EPSG:3857", grid.width, grid.height, *grid.bounds)
        projected = np.zeros((height, width, 4), dtype=np.uint8)
        for band in range(3):
            reproject(pixels[..., band], projected[..., band], src_transform=grid.transform, src_crs=grid.crs,
                      dst_transform=transform, dst_crs="EPSG:3857", resampling=Resampling.bilinear)
        reproject(np.full(pixels.shape[:2], 255, dtype=np.uint8), projected[..., 3], src_transform=grid.transform, src_crs=grid.crs,
                  dst_transform=transform, dst_crs="EPSG:3857", resampling=Resampling.nearest)
        to_geo = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        west, south = to_geo.transform(transform.c, transform.f - height * abs(transform.e))
        east, north = to_geo.transform(transform.c + width * transform.a, transform.f)
        display_bbox = [west, south, east, north]
        pixels = projected
    image = Image.fromarray(pixels)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue(), display_bbox


def trace_downhill(dem, grid, lon, lat, settlements):
    row, col = grid.cell_for(lon, lat)
    if not (0 <= row < grid.height and 0 <= col < grid.width):
        raise ValueError("Choose an upstream point inside the analysis area.")
    path, visited, reason = [], set(), "boundary"
    for _ in range(grid.width * grid.height):
        if (row, col) in visited:
            reason = "cycle"; break
        if not np.isfinite(dem[row, col]):
            reason = "missing elevation"; break
        visited.add((row, col))
        path.append(grid.center(row, col))
        candidates = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = row + dr, col + dc
                if (dr or dc) and 0 <= nr < grid.height and 0 <= nc < grid.width and np.isfinite(dem[nr, nc]):
                    gradient = (dem[row, col] - dem[nr, nc]) / math.hypot(dr, dc)
                    candidates.append((gradient, nr, nc))
        if not candidates or max(candidates)[0] <= 0:
            reason = "local depression or flat terrain"; break
        _, row, col = max(candidates)
        if row in (0, grid.height - 1) or col in (0, grid.width - 1):
            path.append(grid.center(row, col)); break
    if len(path) < 2:
        raise ValueError("No downhill path at this point; choose another location.")
    line = LineString([(p.x, p.y) for p in path])
    metric = grid.project(line)
    nearby = []
    for item in settlements:
        point = grid.project(shape(item["geometry"]).centroid)
        if metric.distance(point) <= 1000:
            nearby.append({"name": item["properties"]["name"], "distance_along_km": round(metric.project(point) / 1000, 2)})
    nearby.sort(key=lambda p: p["distance_along_km"])
    return {"type": "Feature", "geometry": mapping(line), "properties": {
        "length_km": round(metric.length / 1000, 2), "stopped_reason": reason,
        "settlements": nearby, "method": "D8 steepest descent; no depression filling or hydraulic modeling",
    }}
