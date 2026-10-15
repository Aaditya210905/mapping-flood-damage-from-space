"""Annotation-driven local incidence, layover and finite-range DEM shadow screening."""
from pathlib import Path
from xml.etree import ElementTree
import numpy as np
from pyproj import Transformer
from scipy.interpolate import LinearNDInterpolator, interp1d
from app.settings import ACQUISITION


def terrain_screen(dem, resolution, incidence, look_east, look_north, ray_steps=ACQUISITION.terrain_ray_steps):
    """Look components point from ground toward radar; output bit flags.

    Flags: 1 self-shadow, 2 local layover, 4 terrain occlusion, 8 missing.
    This is a screening approximation, not a complete SAR visibility solution.
    """
    dy, dx = np.gradient(dem.astype('float64'), resolution)
    dz_north = -dy
    theta = np.deg2rad(incidence)
    norm = np.sqrt(1+dx**2+dz_north**2)
    cos_local = (np.cos(theta)-np.sin(theta)*(dx*look_east+dz_north*look_north))/norm
    local = np.degrees(np.arccos(np.clip(cos_local, -1, 1)))
    toward = dx*look_east+dz_north*look_north
    flags = np.zeros(dem.shape, dtype='uint8')
    flags[cos_local <= 0] |= 1
    # A foreslope rises AWAY from the sensor. Once its rise exceeds the
    # incidence tangent, slant range reverses along the ground: local layover.
    flags[toward <= -np.tan(theta)] |= 2
    missing = ~np.isfinite(dem) | ~np.isfinite(incidence) | ~np.isfinite(look_east) | ~np.isfinite(look_north)
    flags[missing] |= 8
    # Scan toward the radar using the scene-average horizontal direction. The
    # per-pixel incidence controls ray elevation; the finite horizon is audited.
    east, north = float(np.nanmedian(look_east)), float(np.nanmedian(look_north))
    rise = 1/np.maximum(np.tan(theta), .01)
    offsets = set()
    height, width = dem.shape
    for step in range(1, ray_steps+1):
        dr, dc = int(round(-north*step)), int(round(east*step))
        if (dr, dc) in offsets or (dr == dc == 0):
            continue
        offsets.add((dr, dc))
        if abs(dr) >= height or abs(dc) >= width:
            continue
        rows = slice(max(0,-dr),min(height,height-dr)); cols = slice(max(0,-dc),min(width,width-dc))
        other_rows = slice(max(0,dr),min(height,height+dr)); other_cols = slice(max(0,dc),min(width,width+dc))
        distance = np.hypot(dr,dc)*resolution
        blocked = dem[other_rows,other_cols] > dem[rows,cols] + distance*rise[rows,cols] + resolution*.5
        flags[rows,cols] |= blocked.astype('uint8')*4
    return local.astype('float32'), flags


def annotation_geometry(annotation, grid, elevation=None):
    """Interpolate geolocation times; derive ENU sight vectors from orbit states."""
    root = ElementTree.parse(Path(annotation))
    points = root.findall('.//geolocationGridPoint')
    project = Transformer.from_crs(4326, grid.crs, always_xy=True)
    xy = np.array([project.transform(float(p.findtext('longitude')),float(p.findtext('latitude'))) for p in points])
    times = np.array([np.datetime64(p.findtext('azimuthTime').removesuffix('Z'),'ns').astype('int64') for p in points])
    origin = int(times.min())
    rows,cols = np.indices((grid.height,grid.width))
    x=grid.transform.c+(cols+.5)*grid.transform.a
    y=grid.transform.f+(rows+.5)*grid.transform.e
    seconds = LinearNDInterpolator(xy,(times-origin)/1e9)(x,y)
    orbits = root.findall('.//orbitList/orbit')
    axis = [(np.datetime64(o.findtext('time').removesuffix('Z'),'ns').astype('int64')-origin)/1e9 for o in orbits]
    positions = np.array([[float(o.findtext('position/'+c)) for c in ('x','y','z')] for o in orbits])
    satellite = interp1d(axis,positions,axis=0,kind='cubic',bounds_error=False,fill_value=np.nan)(seconds)
    lon,lat = Transformer.from_crs(grid.crs,4326,always_xy=True).transform(x,y)
    ground = np.stack(Transformer.from_crs(4979,4978,always_xy=True).transform(lon,lat,np.zeros_like(lon) if elevation is None else elevation),axis=-1)
    sight = satellite-ground
    # DEM gradients are in the projected grid axes. Rotate true ENU horizontal
    # directions into those axes rather than assuming grid north is true north.
    xe,ye=project.transform(lon+.0001,lat)
    xn,yn=project.transform(lon,lat+.0001)
    east_norm=np.hypot(xe-x,ye-y);north_norm=np.hypot(xn-x,yn-y)
    lon,lat = np.deg2rad(lon),np.deg2rad(lat)
    sx,sy,sz = np.moveaxis(sight,-1,0)
    east=-np.sin(lon)*sx+np.cos(lon)*sy
    north=-np.sin(lat)*np.cos(lon)*sx-np.sin(lat)*np.sin(lon)*sy+np.cos(lat)*sz
    up=np.cos(lat)*np.cos(lon)*sx+np.cos(lat)*np.sin(lon)*sy+np.sin(lat)*sz
    horizontal=np.hypot(east,north)
    incidence=np.degrees(np.arctan2(horizontal,up))
    look_x=(east*(xe-x)/east_norm+north*(xn-x)/north_norm)/horizontal
    look_y=(east*(ye-y)/east_norm+north*(yn-y)/north_norm)/horizontal
    return incidence.astype('float32'),look_x.astype('float32'),look_y.astype('float32')


def scene_quality(annotation, dem, grid):
    incidence,east,north=annotation_geometry(annotation,grid,dem)
    local,flags=terrain_screen(dem,grid.resolution,incidence,east,north)
    return np.stack([local,flags.astype('float32'),incidence])
