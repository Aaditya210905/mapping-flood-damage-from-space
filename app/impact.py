"""Exposure and before/after access on a pre-event OSM road graph."""
import copy

import networkx as nx
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree


DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service", "road"}


def feature_id(feature, index):
    return str(feature.get("id") or feature.get("properties", {}).get("@osmId") or f"feature-{index}")


def analyze_impacts(osm, extent, grid, snap_distance=1000):
    hazard = unary_union([grid.project(shape(f["geometry"])) for f in extent["features"]])
    buildings, roads, settlements, destinations, bridges = [], [], [], [], []
    for index, source in enumerate(osm["features"]):
        if not source.get("geometry"):
            continue
        feature = copy.deepcopy(source)
        props = feature.setdefault("properties", {})
        props["id"] = feature_id(feature, index)
        props["name"] = props.get("name") or props.get("name:en") or props["id"]
        geom = grid.project(shape(feature["geometry"]))
        if geom.is_empty:
            continue
        intersection = geom.intersection(hazard)
        props["exposed"] = not intersection.is_empty
        if props.get("building") not in (None, "no"):
            props["overlap_fraction"] = round(intersection.area / geom.area, 3) if geom.area else 0
            buildings.append(feature)
        if props.get("highway") in DRIVABLE and props.get("access") not in ("no", "private") and geom.geom_type in ("LineString", "MultiLineString"):
            props["length_m"] = round(geom.length, 2)
            props["exposed_length_m"] = round(intersection.length, 2)
            roads.append(feature)
        if props.get("bridge") not in (None, "no"):
            bridges.append(feature)
        if props.get("place") in ("village", "hamlet", "isolated_dwelling"):
            settlements.append(feature)
        if props.get("place") in ("town", "city") or props.get("amenity") == "hospital":
            destinations.append(feature)

    # Use shared geometry vertices, never planar crossing intersections. This preserves
    # overpasses with no common OSM vertex. Ohsome geometry lacks OSM node identifiers.
    segments = []
    for road in roads:
        geom = grid.project(shape(road["geometry"]))
        lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        for line in lines:
            for start, end in zip(list(line.coords), list(line.coords)[1:]):
                if start[:2] != end[:2]:
                    segments.append((LineString([start[:2], end[:2]]), road["properties"]["id"]))
    tree = STRtree([s[0] for s in segments]) if segments else None
    anchors = {}
    for item in settlements + destinations:
        point = grid.project(shape(item["geometry"]).centroid)
        props = item["properties"]
        props["snap_distance_m"] = None
        if tree is None:
            continue
        nearest = int(tree.nearest(point))
        segment = segments[nearest][0]
        distance = point.distance(segment)
        props["snap_distance_m"] = round(distance, 1)
        if distance > snap_distance:
            continue
        projected = segment.interpolate(segment.project(point))
        node = (round(projected.x, 3), round(projected.y, 3))
        props["_node"] = node
        anchors.setdefault(nearest, []).append(segment.project(projected))

    baseline = nx.MultiGraph()
    for index, (segment, road_id) in enumerate(segments):
        distances = sorted(set([0, segment.length] + anchors.get(index, [])))
        for left, right in zip(distances, distances[1:]):
            if right - left < .001:
                continue
            a, b = segment.interpolate(left), segment.interpolate(right)
            u, v = (round(a.x, 3), round(a.y, 3)), (round(b.x, 3), round(b.y, 3))
            part = LineString([a, b])
            baseline.add_edge(u, v, weight=part.length, blocked=part.intersects(hazard), road_id=road_id)
    after = baseline.copy()
    after.remove_edges_from([(a, b, key) for a, b, key, props in after.edges(keys=True, data=True) if props["blocked"]])
    target_nodes = {d["properties"]["_node"]: d["properties"]["name"] for d in destinations if "_node" in d["properties"] and d["properties"]["_node"] in baseline}
    # A multi-source distance map identifies nearest destination by pre-event road length.
    pre_dist, pre_paths = nx.multi_source_dijkstra(baseline, list(target_nodes), weight="weight") if target_nodes else ({}, {})
    post_dist = nx.multi_source_dijkstra_path_length(after, list(target_nodes), weight="weight") if target_nodes else {}
    for item in settlements:
        props = item["properties"]
        node = props.get("_node")
        props["status"] = "unassessed"
        props["nearest_destination"] = None
        props["before_distance_km"] = None
        props["after_distance_km"] = None
        if node in pre_dist:
            destination = pre_paths[node][0]
            props["nearest_destination"] = target_nodes[destination]
            props["before_distance_km"] = round(pre_dist[node] / 1000, 2)
            props["status"] = "connected" if node in post_dist else "cut_off"
            if node in post_dist:
                props["after_distance_km"] = round(post_dist[node] / 1000, 2)
            props["nearest_destination_access_lost"] = not nx.has_path(after, node, destination)
        elif node is not None and target_nodes:
            props["status"] = "baseline_disconnected"
        props.pop("_node", None)
    for item in destinations:
        item["properties"].pop("_node", None)

    stats = {
        "buildings_total": len(buildings), "buildings_exposed": sum(f["properties"]["exposed"] for f in buildings),
        "roads_total": len(roads), "roads_exposed": sum(f["properties"]["exposed"] for f in roads),
        "road_exposed_km": round(sum(f["properties"]["exposed_length_m"] for f in roads) / 1000, 3),
        "bridges_total": len(bridges), "bridges_exposed": sum(f["properties"]["exposed"] for f in bridges),
        "settlements_total": len(settlements), "settlements_cut_off": sum(f["properties"]["status"] == "cut_off" for f in settlements),
        "settlements_unassessed": sum(f["properties"]["status"] == "unassessed" for f in settlements),
        "settlements_baseline_disconnected": sum(f["properties"]["status"] == "baseline_disconnected" for f in settlements),
        "destinations_total": len(destinations),
    }
    def fc(features):
        return {"type": "FeatureCollection", "features": features}
    return {"buildings": fc(buildings), "roads": fc(roads), "bridges": fc(bridges), "settlements": fc(settlements), "destinations": fc(destinations)}, stats

