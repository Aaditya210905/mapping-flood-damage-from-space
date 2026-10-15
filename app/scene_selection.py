"""Spatially complete optical passes; metadata cloud cover ranks rather than hides tiles."""
from datetime import datetime, timezone
import math
from shapely.geometry import box,shape
from shapely.ops import unary_union

from app.settings import ACQUISITION


def acquired(item):
    value=datetime.fromisoformat(item['properties']['datetime'].replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Catalog acquisition times require a timezone')
    return value.astimezone(timezone.utc)


def cloud_cover(item):
    try:
        value=float(item['properties'].get('eo:cloud_cover',100))
        return value if math.isfinite(value) and 0<=value<=100 else 100.
    except (ValueError,TypeError):return 100.


def optical_pair(items,params):
    area=box(*params.bbox)
    groups={}
    for item in items:
        try:
            instant=acquired(item)
            props=item['properties']
            geometry=shape(item['geometry']).intersection(area)
            if geometry.is_empty or not geometry.is_valid or not geometry.area:continue
            key=(instant.date(),props['platform'],props.get('sat:absolute_orbit') or props.get('sat:relative_orbit'))
            groups.setdefault(key,[]).append((instant,item,geometry))
        except (ValueError,KeyError,TypeError):continue
    complete=[]
    for key,records in groups.items():
        passes=[]
        for record in sorted(records,key=lambda value:value[0]):
            if not passes or (record[0]-passes[-1][0][0]).total_seconds()>ACQUISITION.optical_pass_tolerance_seconds:
                passes.append([])
            passes[-1].append(record)
        for records in passes:
            if unary_union([r[2] for r in records]).area/area.area<ACQUISITION.minimum_scene_coverage:continue
            total=sum(r[2].area for r in records)
            cloud=sum(cloud_cover(r[1])*r[2].area for r in records)/total
            day=key[0]
            score=cloud+ACQUISITION.optical_day_weight*abs((day-params.flood_date).days)
            complete.append((day,score,[r[1] for r in records]))
    return [min((r for r in complete if (r[0]<params.flood_date if before else r[0]>params.flood_date)),
                key=lambda r:r[1],default=(None,None,None))[2] for before in (True,False)]
