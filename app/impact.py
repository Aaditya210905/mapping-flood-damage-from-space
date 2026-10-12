"""Exposure and before/after access on a pre-event OSM road graph."""
import copy
import math
import numpy as np

import networkx as nx
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree


DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service", "road"}


def feature_id(feature, index):
    return str(feature.get("id") or feature.get("properties", {}).get("@osmId") or f"feature-{index}")


def analyze_impacts(osm, extent, grid, snap_distance=1000, assessment_mask=None):
    def observed_fraction(geom):
        if assessment_mask is None:
            return 1.
        if geom.geom_type == "LineString":
            points = [geom.interpolate(d) for d in np.linspace(0,geom.length,max(2,math.ceil(geom.length/(grid.resolution/2))+1))]
        else:
            points = [geom.representative_point()]
        observed = 0
        for point in points:
            row = math.floor((grid.transform.f-point.y)/grid.resolution)
            col = math.floor((point.x-grid.transform.c)/grid.resolution)
            observed += bool(0 <= row < grid.height and 0 <= col < grid.width and assessment_mask[row,col])
        return observed/len(points)
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
        props["exposed"] = intersection.area > .001 if geom.geom_type in ("Polygon","MultiPolygon") else intersection.length > .001 if geom.geom_type in ("LineString","MultiLineString") else not intersection.is_empty
        props["assessment_fraction"] = round(observed_fraction(geom),3)
        props["assessment_method"] = "line sampling at half-grid resolution; other geometries at representative point"
        props["assessment_status"] = "potential_exposure" if props["exposed"] else ("no_mapped_change_at_observed_samples" if props["assessment_fraction"] == 1 else "unassessed_or_partial")
        if props.get("building") not in (None, "no"):
            props["overlap_fraction"] = round(intersection.area / geom.area, 3) if geom.area else 0
            buildings.append(feature)
        if props.get("highway") in DRIVABLE and all(props.get(k) not in ("no","private") for k in ("access","vehicle","motor_vehicle")) and geom.geom_type in ("LineString", "MultiLineString"):
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
    road_props = {r["properties"]["id"]:r["properties"] for r in roads}
    def level(road_id):
        p=road_props[road_id]
        try:
            return int(p.get("layer", 1 if p.get("bridge") not in (None,"no") else -1 if p.get("tunnel") not in (None,"no") else 0))
        except (TypeError,ValueError):
            return 0
    vertices = {}
    for road in roads:
        g=grid.project(shape(road["geometry"]))
        for line in (list(g.geoms) if g.geom_type == "MultiLineString" else [g]):
            coordinates=list(line.coords)
            for index,coordinate in enumerate(coordinates):
                key=(round(coordinate[0],3),round(coordinate[1],3))
                info=vertices.setdefault(key,{"levels":set(),"endpoint":False})
                info["levels"].add(level(road["properties"]["id"]))
                info["endpoint"] |= index in (0,len(coordinates)-1)
    def node_for(point,road_id):
        key=(round(point.x,3),round(point.y,3));info=vertices.get(key)
        return key+(level(road_id),) if info and len(info["levels"]) > 1 and not info["endpoint"] else key
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
        node = node_for(projected,segments[nearest][1])
        props["_node"] = node
        anchors.setdefault(nearest, []).append(segment.project(projected))

    baseline = nx.MultiDiGraph()
    for index, (segment, road_id) in enumerate(segments):
        distances = sorted(set([0, segment.length] + anchors.get(index, [])))
        for left, right in zip(distances, distances[1:]):
            if right - left < .001:
                continue
            a, b = segment.interpolate(left), segment.interpolate(right)
            u, v = node_for(a,road_id),node_for(b,road_id)
            part = LineString([a, b])
            properties={"weight":part.length,"blocked":part.intersection(hazard).length > .001,"road_id":road_id,"observed":observed_fraction(part) >= .999}
            direction=str(road_props[road_id].get("oneway","yes" if road_props[road_id].get("junction") == "roundabout" else "no")).lower()
            if direction != "-1":
                baseline.add_edge(u,v,**properties)
            if direction not in ("yes","true","1"):
                baseline.add_edge(v,u,**properties)
    after = baseline.copy()
    after.remove_edges_from([(a, b, key) for a, b, key, props in after.edges(keys=True, data=True) if props["blocked"]])
    observed_after=after.copy()
    observed_after.remove_edges_from([(a,b,key) for a,b,key,p in observed_after.edges(keys=True,data=True) if not p["observed"]])
    target_nodes = {d["properties"]["_node"]: d["properties"]["name"] for d in destinations if "_node" in d["properties"] and d["properties"]["_node"] in baseline}
    # A multi-source distance map identifies nearest destination by pre-event road length.
    pre_dist, pre_paths = nx.multi_source_dijkstra(baseline.reverse(copy=False), list(target_nodes), weight="weight") if target_nodes else ({}, {})
    post_dist,post_paths = nx.multi_source_dijkstra(after.reverse(copy=False), list(target_nodes), weight="weight") if target_nodes else ({},{})
    observed_dist=nx.multi_source_dijkstra_path_length(observed_after.reverse(copy=False),list(target_nodes),weight="weight") if target_nodes else {}
    def route_evidence(path,graph):
        nodes=list(reversed(path))
        if len(nodes)<2:return None
        road_ids=[]
        for u,v in zip(nodes,nodes[1:]):
            edge=min(graph.get_edge_data(u,v).values(),key=lambda p:p["weight"])
            road_ids.append(edge["road_id"])
        return {"type":"Feature","geometry":mapping(grid.geographic(LineString([n[:2] for n in nodes]))),
                "properties":{"road_ids":list(dict.fromkeys(road_ids)),"direction":"settlement to destination","closure_evidence":"assumed candidate intersection; not field-confirmed"}}
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
            props["baseline_route"] = route_evidence(pre_paths[node],baseline)
            props["status"] = "connected" if node in post_dist else "cut_off"
            if node in post_dist:
                props["after_distance_km"] = round(post_dist[node] / 1000, 2)
                props["after_route"] = route_evidence(post_paths[node],after)
                if node not in observed_dist:
                    props["status"] = "uncertain_access"
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
        "settlements_access_uncertain": sum(f["properties"]["status"] == "uncertain_access" for f in settlements),
        "roads_unassessed_or_partial": sum(f["properties"]["assessment_fraction"] < 1 for f in roads),
        "destinations_total": len(destinations),
    }
    def fc(features):
        return {"type": "FeatureCollection", "features": features}
    return {"buildings": fc(buildings), "roads": fc(roads), "bridges": fc(bridges), "settlements": fc(settlements), "destinations": fc(destinations)}, stats
