"""Deterministic synthetic sensor inputs. These are NOT real observations."""
from datetime import datetime, timedelta, timezone

import numpy as np
from shapely.geometry import LineString, Point, box, mapping

from app.models import Scene


def make_demo(grid, params):
    rng = np.random.default_rng(927)
    h, w = grid.height, grid.width
    y, x = np.mgrid[:h, :w]
    river = w * (.48 + .07 * np.sin(y / h * 12) + .02 * np.cos(y / h * 25))
    river_distance = np.abs(x - river)
    permanent = river_distance < max(2, w * .008)
    new_water = (river_distance < w * (.025 + .032 * np.exp(-((y / h - .48) / .17) ** 2))) & ~permanent
    new_water &= (y / h > .13) & (y / h < .85)
    debris = (river_distance < w * .06) & ~permanent & ~new_water & (y / h > .20) & (y / h < .46)
    before_db = -9 + rng.normal(0, 1.2, (h, w))
    before_db[permanent] = -23
    after_db = before_db + rng.normal(0, .6, (h, w))
    after_db[new_water] = -23 + rng.normal(0, .6, new_water.sum())
    after_db[debris] += 5
    before = np.stack([10 ** (before_db / 10), np.ones((h, w)), np.zeros((h, w))]).astype("float32")
    after = np.stack([10 ** (after_db / 10), np.ones((h, w)), np.zeros((h, w))]).astype("float32")
    dem = (800 + (h - y) * grid.resolution * .035 + ((x - river) * grid.resolution) ** 2 * .000045).astype("float32")
    optical_before = np.stack([np.full((h,w), .15), np.full((h,w), .1), np.full((h,w), .5), np.full((h,w), .25), np.full((h,w), 4), np.ones((h,w))]).astype("float32")
    optical_after = optical_before.copy()
    optical_after[2, debris] = .12
    optical_after[1, debris] = .16
    for a in (optical_before, optical_after):
        a[0, permanent] = .25
        a[3, permanent] = .04
    optical_after[0, new_water] = .25
    optical_after[3, new_water] = .04
    # Northern patch demonstrates exclusion of cloudy optical pixels.
    optical_before[4, :int(h * .1)] = 9
    optical_after[4, :int(h * .1)] = 9

    def coord(fx, fy):
        return grid.center(min(h - 1, round(h * fy)), min(w - 1, round(w * fx)))
    features = []
    def add(geometry, props):
        features.append({"type": "Feature", "geometry": mapping(geometry), "properties": {"@osmId": f"synthetic/{len(features)}", **props}})
    trunk_x = .29
    north_south = [coord(trunk_x, .96 - i * .04) for i in range(23)]
    add(LineString(north_south), {"highway": "primary", "name": "Demo valley highway"})
    names = ["Demo ridge village", "Demo east village", "Demo upper village", "Demo riverside village", "Demo west village"]
    branches = [(.24, .08), (.84, .40), (.75, .24), (.69, .60), (.16, .80)]
    for name, (sx, sy) in zip(names, branches):
        # Branches meet an existing trunk vertex; river crossings can be cut.
        points = [coord(trunk_x + (sx - trunk_x) * i / 24, sy) for i in range(25)]
        if sx > .5:
            crossing = min(range(1, 24), key=lambda i: abs(trunk_x + (sx - trunk_x) * i / 24 - .5))
            left, right = max(1, crossing - 2), min(23, crossing + 2)
            add(LineString(points[:left + 1]), {"highway": "secondary", "name": f"{name} west access"})
            add(LineString(points[left:right + 1]), {"highway": "secondary", "bridge": "yes", "name": f"{name} bridge"})
            add(LineString(points[right:]), {"highway": "secondary", "name": f"{name} east access"})
        else:
            add(LineString(points), {"highway": "secondary", "name": f"{name} access"})
        add(coord(sx, sy), {"place": "village", "name": name})
    add(coord(trunk_x, .96), {"place": "town", "name": "Demo town"})
    add(coord(trunk_x, .88), {"amenity": "hospital", "name": "Demo hospital"})
    for _ in range(180):
        sy = rng.uniform(.18, .85)
        rx = .48 + .07 * np.sin(sy * 12) + .02 * np.cos(sy * 25)
        p = grid.project(coord(float(rx + rng.uniform(-.085, .085)), float(sy)))
        add(grid.geographic(box(p.x - 10, p.y - 8, p.x + 10, p.y + 8)), {"building": "yes", "name": "Synthetic building"})
    day = datetime.combine(params.flood_date, datetime.min.time(), tzinfo=timezone.utc)
    scenes = [Scene(id="SYNTHETIC-S1-BEFORE", acquired=day - timedelta(days=6), relative_orbit=19, direction="ascending"), Scene(id="SYNTHETIC-S1-AFTER", acquired=day + timedelta(days=6), relative_orbit=19, direction="ascending")]
    return {"before": before, "after": after, "dem": dem, "optical_before": optical_before, "optical_after": optical_after, "osm": {"type": "FeatureCollection", "features": features}, "scenes": scenes, "snapshot": str(params.flood_date - timedelta(days=30)), "optical_scenes": ["SYNTHETIC-S2-BEFORE", "SYNTHETIC-S2-AFTER"]}
