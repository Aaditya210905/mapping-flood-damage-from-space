"""Metric raster grid, conservative change detection, vectorization and DEM screening."""
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import math

import numpy as np
import shapely
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling
from scipy import ndimage
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely import transform as transform_geometry
from app.settings import MAPPING, ACQUISITION


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


def observation_fraction(geom, mask, grid):
    """Observed footprint area / length-weighted line samples / point sample."""
    if mask is None:return 1.
    if np.shape(mask)!=(grid.height,grid.width):raise ValueError('Observation mask must match the grid')
    if geom.geom_type in ('Polygon','MultiPolygon'):
        if not geom.area:return 0.
        xmin,ymin,xmax,ymax=geom.bounds
        c0=max(0,math.floor((xmin-grid.transform.c)/grid.resolution))
        c1=min(grid.width,math.ceil((xmax-grid.transform.c)/grid.resolution))
        r0=max(0,math.floor((grid.transform.f-ymax)/grid.resolution))
        r1=min(grid.height,math.ceil((grid.transform.f-ymin)/grid.resolution))
        if r0>=r1 or c0>=c1:return 0.
        window=mask[r0:r1,c0:c1]
        if not window.any():return 0.
        if window.all():area=geom.intersection(box(*grid.bounds)).area
        else:
            rows,cols=np.nonzero(window)
            left=grid.transform.c+(cols+c0)*grid.resolution
            top=grid.transform.f-(rows+r0)*grid.resolution
            cells=shapely.box(left,top-grid.resolution,left+grid.resolution,top)
            area=float(np.sum(shapely.area(shapely.intersection(cells,geom))))
        return min(1.,max(0.,area/geom.area))
    if geom.geom_type in ('LineString','MultiLineString'):
        lines=list(geom.geoms) if geom.geom_type=='MultiLineString' else [geom]
        observed,length=0.,0.
        for line in lines:
            count=max(1,math.ceil(line.length/(grid.resolution/2)))
            for index in range(count):
                point=line.interpolate((index+.5)*line.length/count)
                row=math.floor((grid.transform.f-point.y)/grid.resolution)
                col=math.floor((point.x-grid.transform.c)/grid.resolution)
                if 0<=row<grid.height and 0<=col<grid.width and mask[row,col]:observed+=line.length/count
            length+=line.length
        return min(1.,observed/length) if length else 0.
    point=geom.representative_point()
    row=math.floor((grid.transform.f-point.y)/grid.resolution)
    col=math.floor((point.x-grid.transform.c)/grid.resolution)
    return float(0<=row<grid.height and 0<=col<grid.width and mask[row,col])


def clean_mask(mask, minimum):
    labels, count = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= minimum
    keep[0] = False
    return keep[labels] if count else np.zeros_like(mask, dtype=bool)


def slope_degrees(dem, resolution):
    dy, dx = np.gradient(dem.astype(float), resolution)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def masked_median(values, valid, size=3, minimum_support=3):
    """Median of observed neighbours, with no synthetic values at mask edges.

    Process a bounded strip at a time: a full 2500-square sliding window can
    otherwise allocate hundreds of MB during nanmedian. An observed centre and
    at least three observed samples are required; an isolated return is not
    enough evidence for a change estimate.
    """
    if size < 1 or size % 2 != 1 or not 1 <= minimum_support <= size * size:
        raise ValueError("Median size must be odd and support must fit the window.")
    values = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid, dtype=bool) & np.isfinite(values)
    radius = size // 2
    padded = np.pad(np.where(valid, values, np.nan), radius, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (size, size))
    result = np.full(values.shape, np.nan, dtype=np.float32)
    support = np.zeros(values.shape, dtype=np.uint8)
    for first in range(0, values.shape[0], 128):
        last = min(first + 128, values.shape[0])
        strip = windows[first:last]
        counts = np.count_nonzero(np.isfinite(strip), axis=(-2, -1))
        support[first:last] = counts
        usable = valid[first:last] & (counts >= minimum_support)
        # Reduce only usable windows so all-NaN neighbourhoods do not emit
        # warnings or become apparent observations.
        if usable.any():
            result[first:last][usable] = np.nanmedian(strip[usable], axis=(-2, -1))
    return result, support


def detect_changes(before, after, grid, params, dem=None, optical_before=None, optical_after=None):
    """S1 arrays are [VV linear gamma0, dataMask, shadowMask]. S2: B03,B04,B08,B11,SCL,dataMask."""
    valid = (before[1] > 0) & (after[1] > 0) & (before[0] > 0) & (after[0] > 0)
    valid &= np.isfinite(before[0]) & np.isfinite(after[0])
    if before.shape[0] > 2 and after.shape[0] > 2:
        valid &= (before[2] == 0) & (after[2] == 0)
    radar_valid = valid.copy()
    low_slope = np.ones(valid.shape,dtype=bool) if dem is None else np.isfinite(dem) & (slope_degrees(dem, grid.resolution) < params.max_slope_deg)
    valid &= low_slope
    # Invalid/shadow neighbours must not bias the median toward an invented
    # -10 dB return. Keep physical radar coverage separate from filter support.
    raw_before_db = np.where(radar_valid, 10 * np.log10(np.maximum(before[0], 1e-10)), np.nan)
    raw_after_db = np.where(radar_valid, 10 * np.log10(np.maximum(after[0], 1e-10)), np.nan)
    before_db, radar_filter_support = masked_median(raw_before_db, radar_valid)
    after_db, _ = masked_median(raw_after_db, radar_valid)
    valid &= np.isfinite(before_db) & np.isfinite(after_db)
    drop = before_db - after_db
    permanent = valid & (before_db < params.water_threshold_db)
    water = valid & ~permanent & (after_db < params.water_threshold_db) & (drop > params.change_threshold_db)
    debris = np.zeros_like(water)
    optical_valid = np.zeros_like(water)
    optical_water = np.zeros_like(water)
    if optical_before is not None and optical_after is not None:
        allowed=[code for code in range(12) if code not in MAPPING.optical_excluded_scl_classes]
        optical_valid = (optical_before[5] > 0) & (optical_after[5] > 0)
        optical_valid &= np.isin(optical_before[4], allowed) & np.isin(optical_after[4], allowed)
        optical_valid &= np.all(np.isfinite(optical_before[:4]), axis=0) & np.all(np.isfinite(optical_after[:4]), axis=0)
        optical_valid &= np.all(optical_before[:4] >= 0,axis=0) & np.all(optical_after[:4] >= 0,axis=0)
        def index(a, b):
            return np.divide(a-b,a+b,out=np.full(a.shape,np.nan,dtype='float32'),where=(a+b)>1e-6)
        pre_ndvi = index(optical_before[2], optical_before[1])
        post_ndvi = index(optical_after[2], optical_after[1])
        pre_water = index(optical_before[0], optical_before[3])
        post_water = index(optical_after[0], optical_after[3])
        # A cloud-free dataMask with zero reflectance is not a usable change
        # observation if its spectral indices are undefined.
        optical_valid &= np.isfinite(pre_ndvi) & np.isfinite(post_ndvi) & np.isfinite(pre_water) & np.isfinite(post_water)
        optical_permanent = optical_valid & low_slope & (pre_water > MAPPING.optical_water_threshold)
        permanent |= optical_permanent
        # A clear pre-event optical water observation contradicts "new water",
        # including a radar-only candidate produced before optical processing.
        water &= ~permanent
        optical_water = optical_valid & low_slope & ~permanent & (post_water > MAPPING.optical_water_threshold) & (pre_water <= MAPPING.optical_water_threshold)
        # Vegetation loss + radar change is only a debris candidate; wet soil/harvest can look similar.
        if params.mode == "demo":
            debris = optical_valid & valid & ~permanent & ~water & ~optical_water
            debris &= (pre_ndvi > MAPPING.vegetation_baseline_threshold) & ((pre_ndvi - post_ndvi) > MAPPING.vegetation_loss_threshold) & (np.abs(drop) > params.change_threshold_db)
        else:
            # Optical evidence remains observed on steep slopes and in radar
            # shadow. Require both vegetation loss and increased bare surface;
            # never use corrupted radar as confirmation there.
            pre_bare = index(optical_before[3]+optical_before[1], optical_before[2]+optical_before[0])
            post_bare = index(optical_after[3]+optical_after[1], optical_after[2]+optical_after[0])
            debris = optical_valid & (pre_water <= MAPPING.optical_water_threshold) & (post_water <= MAPPING.optical_water_threshold) & ~water & ~optical_water
            debris &= (pre_ndvi > MAPPING.vegetation_baseline_threshold) & ((pre_ndvi-post_ndvi) > MAPPING.vegetation_loss_threshold) & ((post_bare-pre_bare) > MAPPING.bare_surface_increase_threshold)
            # Require radar change where radar is interpretable. Optical-only
            # targets use stricter spectral changes and remain low confidence.
            radar_interpretable = radar_valid & np.isfinite(drop)
            corroborated = radar_interpretable & (np.abs(drop) > params.change_threshold_db)
            optical_only = ~radar_interpretable & ((pre_ndvi-post_ndvi) > MAPPING.optical_only_vegetation_loss_threshold) & ((post_bare-pre_bare) > MAPPING.optical_only_bare_increase_threshold)
            debris &= corroborated | optical_only
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
        "radar_filter_support": radar_filter_support,
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
        if metric.distance(point) <= ACQUISITION.trace_settlement_buffer_m:
            nearby.append({"name": item["properties"]["name"], "distance_along_km": round(metric.project(point) / 1000, 2)})
    nearby.sort(key=lambda p: p["distance_along_km"])
    return {"type": "Feature", "geometry": mapping(line), "properties": {
        "length_km": round(metric.length / 1000, 2), "stopped_reason": reason,
        "settlements": nearby, "method": "D8 steepest descent; no depression filling or hydraulic modeling",
    }}
