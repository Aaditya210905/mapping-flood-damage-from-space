"""Mapped exposure and explicitly assumed road-access scenarios on historical OSM."""
import copy
import math

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from app.settings import MAPPING
from app.geo import observation_fraction


def feature_id(feature, index):
    return str(feature.get("id") or feature.get("properties", {}).get("@osmId") or f"feature-{index}")


def analyze_impacts(osm, extent, grid, snap_distance=None, assessment_mask=None,
                    assessment_masks=None, closure_min_length_m=None):
    """Assess mapped elements, not actual damage or operational road availability.

    Closure is a sensitivity scenario: an unbridged road needs at least half a
    raster cell of candidate overlap by default. Water under a mapped bridge
    makes its availability unknown, rather than proving deck failure. No
    off-road connector, exterior route, or missing OSM junction is invented.
    """
    snap_distance = MAPPING.road_snap_distance_m if snap_distance is None else snap_distance
    minimum_overlap = grid.resolution * MAPPING.road_overlap_cell_fraction if closure_min_length_m is None else float(closure_min_length_m)
    drivable=frozenset(MAPPING.drivable_highways)
    if not math.isfinite(snap_distance) or not math.isfinite(minimum_overlap) or snap_distance < 0 or minimum_overlap <= 0:
        raise ValueError("Snap distance must be nonnegative and closure overlap must be positive.")
    masks = {name: np.asarray(mask, dtype=bool) for name, mask in (assessment_masks or {}).items()}
    for mask in [*masks.values(), *([] if assessment_mask is None else [assessment_mask])]:
        if np.shape(mask) != (grid.height, grid.width):
            raise ValueError("Assessment mask must match the analysis grid.")
    available_masks = {name: mask for name, mask in masks.items() if mask.any()}
    unavailable_hazards = sorted(set(masks) - set(available_masks))
    if available_masks:
        overall_mask = np.logical_and.reduce(list(available_masks.values()))
    elif masks:
        overall_mask = np.zeros((grid.height, grid.width), dtype=bool)
    else:
        overall_mask = assessment_mask
    grid_extent = box(*grid.bounds)
    def observed_fraction(geom, mask=overall_mask):
        return observation_fraction(geom,mask,grid)

    hazard_parts = {}
    for feature in extent["features"]:
        kind = feature.get("properties", {}).get("kind", "unknown")
        hazard_parts.setdefault(kind, []).append(grid.project(shape(feature["geometry"])))
    hazards = {kind: unary_union(parts) for kind, parts in hazard_parts.items()}
    hazard = unary_union(list(hazards.values()))
    debris_hazard = hazards.get("debris", shapely.GeometryCollection())
    buildings, roads, settlements, destinations, bridges = [], [], [], [], []
    exposed_road_length_m = 0.
    seen = set()
    for index, source in enumerate(osm["features"]):
        if not source.get("geometry"):
            continue
        identifier = feature_id(source, index)
        if identifier in seen:
            continue
        seen.add(identifier)
        feature = copy.deepcopy(source)
        props = feature.setdefault("properties", {})
        props["id"] = identifier
        props["name"] = props.get("name") or props.get("name:en") or identifier
        props["counting_unit"] = "unique historical OSM element; not a verified physical-object inventory"
        geom = grid.project(shape(feature["geometry"]))
        if geom.is_empty:
            continue
        intersection = geom.intersection(hazard)
        props["exposed"] = intersection.area > .001 if geom.geom_type in ("Polygon", "MultiPolygon") else intersection.length > .001 if geom.geom_type in ("LineString", "MultiLineString") else not intersection.is_empty
        fraction = observed_fraction(geom)
        props["assessment_fraction"] = round(fraction, 3)
        props["assessment_complete"] = fraction >= 1-1e-9
        props["assessment_fraction_by_hazard"] = {kind: round(observed_fraction(geom, mask), 3) for kind, mask in masks.items()}
        props["assessment_scope"] = sorted(available_masks) if masks else ["supplied assessment mask"]
        props["unavailable_hazards"] = unavailable_hazards
        props["assessment_method"] = "polygon footprint area weighted by eligible raster cells; lines length-weighted midpoint samples at half-grid resolution"
        props["assessment_status"] = "potential_exposure" if props["exposed"] else ("no_mapped_change_in_assessed_hazard_scope" if fraction >= 1-1e-9 else "unassessed_or_partial")
        if props.get("building") not in (None, "no"):
            props["overlap_fraction"] = round(intersection.area/geom.area, 3) if geom.area else 0
            buildings.append(feature)
        if props.get("highway") in drivable and all(props.get(k) not in ("no", "private") for k in ("access", "vehicle", "motor_vehicle")) and geom.geom_type in ("LineString", "MultiLineString"):
            props["length_m"] = round(geom.length, 2)
            props["exposed_length_m"] = round(intersection.length, 2)
            props["overlap_fraction"] = round(intersection.length/geom.length, 3) if geom.length else 0
            exposed_road_length_m += intersection.length
            roads.append(feature)
        if props.get("bridge") not in (None, "no") or props.get("man_made") == "bridge":
            props["bridge_assessment"] = "potential exposure only; water beneath a bridge does not establish deck failure or closure"
            bridges.append(feature)
        if props.get("place") in MAPPING.settlement_places:
            settlements.append(feature)
        kinds = MAPPING.destination_types(props)
        if kinds:
            props['destination_types'] = kinds
            props["destination_type"] = kinds[0]
            props["operational_status"] = "unknown; mapped pre-event facility only"
            destinations.append(feature)

    # Ohsome geometry has no node IDs. Do not join planar crossing interiors.
    # Retain the topology limitation in every access result and route.
    segments = []
    for road in roads:
        geom = grid.project(shape(road["geometry"]))
        for line in (list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]):
            for start, end in zip(list(line.coords), list(line.coords)[1:]):
                if start[:2] != end[:2]:
                    segments.append((LineString([start[:2], end[:2]]), road["properties"]["id"]))
    road_props = {road["properties"]["id"]: road["properties"] for road in roads}
    closure_spans = {}
    for road in roads:
        props = road["properties"]
        geom = grid.project(shape(road["geometry"]))
        is_bridge = props.get("bridge") not in (None, "no")
        is_tunnel = props.get("tunnel") not in (None, "no")
        candidate_hazard = shapely.GeometryCollection() if is_tunnel else debris_hazard if is_bridge else hazard
        intersection = geom.intersection(candidate_hazard)
        # Apply the threshold to continuous overlap on the mapped way, before
        # graph subdivision. Adding a settlement anchor must not change closure.
        linear = shapely.line_merge(intersection)
        parts = list(linear.geoms) if hasattr(linear, "geoms") else [linear]
        qualified = [part for part in parts if part.geom_type == "LineString" and part.length >= minimum_overlap-1e-6]
        closure_spans[props["id"]] = unary_union(qualified)
        props["closure_candidate"] = bool(qualified)
        props["closure_min_length_m"] = minimum_overlap
        props["closure_assessment"] = "assumed impassable candidate overlap; not field-confirmed" if qualified else ("availability uncertain where candidate overlap is below threshold or water lies beneath bridge" if props["exposed"] else "no mapped closure candidate; not verified open")

    def level(road_id):
        props = road_props[road_id]
        try:
            return int(props.get("layer", 1 if props.get("bridge") not in (None, "no") else -1 if props.get("tunnel") not in (None, "no") else 0))
        except (TypeError, ValueError):
            return 0

    vertices = {}
    for road in roads:
        geom = grid.project(shape(road["geometry"]))
        for line in (list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]):
            coordinates = list(line.coords)
            for index, coordinate in enumerate(coordinates):
                key = (round(coordinate[0], 3), round(coordinate[1], 3))
                info = vertices.setdefault(key, {"levels": set(), "endpoint": False})
                info["levels"].add(level(road["properties"]["id"]))
                info["endpoint"] |= index in (0, len(coordinates)-1)

    def node_for(point, road_id):
        key = (round(point.x, 3), round(point.y, 3))
        info = vertices.get(key)
        return key+(level(road_id),) if info and len(info["levels"]) > 1 and not info["endpoint"] else key

    tree = STRtree([segment[0] for segment in segments]) if segments else None
    anchors = {}
    for item in settlements+destinations:
        point = grid.project(shape(item["geometry"]).centroid)
        props = item["properties"]
        props.update(snap_distance_m=None, snap_limit_m=snap_distance, snap_status="no_mapped_motorable_road", snap_connector=None)
        if tree is None:
            continue
        nearest = int(tree.nearest(point))
        segment, road_id = segments[nearest]
        distance = point.distance(segment)
        props["snap_distance_m"] = round(distance, 1)
        props["snap_road_id"] = road_id
        if distance > snap_distance:
            props["snap_status"] = "outside_snap_limit"
            continue
        projected = segment.interpolate(segment.project(point))
        props["snap_status"] = "on_mapped_road" if distance <= MAPPING.road_on_network_tolerance_m else "unverified_off_road_connector"
        if distance > 1:
            props["snap_connector"] = {"type": "Feature", "geometry": mapping(grid.geographic(LineString([point, projected]))), "properties": {"assumption": "unverified connector; may cross water, terrain or barriers"}}
        props["_node"] = node_for(projected, road_id)
        anchors.setdefault(nearest, []).append(segment.project(projected))

    baseline = nx.MultiDiGraph()
    for index, (segment, road_id) in enumerate(segments):
        distances = sorted(set([0, segment.length]+anchors.get(index, [])))
        for left, right in zip(distances, distances[1:]):
            if right-left < .001:
                continue
            a, b = segment.interpolate(left), segment.interpolate(right)
            u, v = node_for(a, road_id), node_for(b, road_id)
            part = LineString([a, b])
            overlap = part.intersection(hazard).length
            blocked = part.intersection(closure_spans[road_id]).length > .001
            uncertain_overlap = overlap > .001 and not blocked
            properties = {"weight": part.length, "blocked": blocked, "closure_uncertain": uncertain_overlap, "road_id": road_id, "overlap_length_m": overlap,
                          "observed": observed_fraction(part) >= 1-1e-9 and not uncertain_overlap}
            direction = str(road_props[road_id].get("oneway", "yes" if road_props[road_id].get("junction") == "roundabout" else "no")).lower()
            if direction != "-1":
                baseline.add_edge(u, v, **properties)
            if direction not in ("yes", "true", "1"):
                baseline.add_edge(v, u, **properties)
    after = baseline.copy()
    after.remove_edges_from([(a, b, key) for a, b, key, props in after.edges(keys=True, data=True) if props["blocked"]])
    observed_after = after.copy()
    observed_after.remove_edges_from([(a, b, key) for a, b, key, props in observed_after.edges(keys=True, data=True) if not props["observed"]])
    boundary_nodes = {node for node in baseline if Point(node[:2]).distance(grid_extent.boundary) <= grid.resolution or not grid_extent.covers(Point(node[:2]))}
    boundary_component_nodes = set()
    for component in nx.weakly_connected_components(baseline):
        if component & boundary_nodes:
            boundary_component_nodes.update(component)

    def route_evidence(path, graph):
        nodes = list(reversed(path))
        if len(nodes) < 2:
            return None
        road_ids = []
        for u, v in zip(nodes, nodes[1:]):
            edge = min(graph.get_edge_data(u, v).values(), key=lambda props: props["weight"])
            road_ids.append(edge["road_id"])
        return {"type": "Feature", "geometry": mapping(grid.geographic(LineString([node[:2] for node in nodes]))), "properties": {"road_ids": list(dict.fromkeys(road_ids)), "direction": "settlement to destination", "closure_evidence": "assumed candidate overlap scenario; not field-confirmed", "topology_method": "shared coordinate vertices; OSM node IDs unavailable"}}

    def destination_maps(selected):
        target_features = {}
        for destination in selected:
            props = destination["properties"]
            node = props.get("_node")
            if node in baseline:
                target_features.setdefault(node, []).append(props)
        for features in target_features.values():
            features.sort(key=lambda props: props["id"])
        sources = list(target_features)
        pre = nx.multi_source_dijkstra(baseline.reverse(copy=False), sources, weight="weight") if sources else ({}, {})
        post = nx.multi_source_dijkstra(after.reverse(copy=False), sources, weight="weight") if sources else ({}, {})
        observed_sources = [node for node, features in target_features.items() if any(props["snap_status"] == "on_mapped_road" for props in features)]
        observed = nx.multi_source_dijkstra(observed_after.reverse(copy=False), observed_sources, weight="weight") if observed_sources else ({}, {})
        return target_features, pre, post, observed

    groups = {"all": destination_maps(destinations)}
    groups.update({kind: destination_maps([item for item in destinations if kind in item["properties"]["destination_types"]]) for kind in MAPPING.destination_profiles})
    same_target_distances = {}

    def connectivity(props, group):
        targets, (pre_dist, pre_paths), (post_dist, post_paths), (observed_dist, observed_paths) = groups[group]
        node = props.get("_node")
        result = {"status": "unassessed", "nearest_destination": None, "before_destination_id": None, "before_destination_type": None,
                  "after_destination": None, "after_destination_id": None, "after_destination_type": None, "before_distance_km": None, "after_distance_km": None,
                  "same_destination_after_distance_km": None, "same_destination_extra_distance_km": None, "nearest_destination_access_lost": None,
                  "assessment_reason": "no_mapped_destination" if not targets else "settlement_not_snapped_to_mapped_motorable_road"}
        if not targets and any(group == "all" or group in destination["properties"]["destination_types"] for destination in destinations):
            result["assessment_reason"] = "no_destination_within_mapped_road_snap_limit"
        if node in pre_dist:
            before_target = pre_paths[node][0]
            before_feature = targets[before_target][0]
            result.update(nearest_destination=before_feature["name"], before_destination_id=before_feature["id"], before_destination_type=group if group != 'all' else before_feature["destination_type"],
                          before_distance_km=round(pre_dist[node]/1000, 2), baseline_route=route_evidence(pre_paths[node], baseline),
                          status="connected" if node in post_dist else "cut_off", assessment_reason="assumed_road_overlap_closure_scenario")
            if node in post_dist:
                # If an observed route exists, display that route rather than a
                # shorter optimistic route containing unknown segments.
                use_observed = node in observed_dist and props["snap_status"] == "on_mapped_road"
                distances, paths, graph = (observed_dist, observed_paths, observed_after) if use_observed else (post_dist, post_paths, after)
                after_feature = min(targets[paths[node][0]], key=lambda item: (item["snap_status"] != "on_mapped_road", item["id"]))
                result.update(after_destination=after_feature["name"], after_destination_id=after_feature["id"], after_destination_type=group if group != 'all' else after_feature["destination_type"],
                              after_distance_km=round(distances[node]/1000, 2), after_route=route_evidence(paths[node], graph))
                if node not in observed_dist or props["snap_status"] != "on_mapped_road":
                    result.update(status="uncertain_access", assessment_reason="unobserved_or_unverified_route_or_connector")
            if before_target not in same_target_distances:
                same_target_distances[before_target] = nx.single_source_dijkstra_path_length(after.reverse(copy=False), before_target, weight="weight")
            same_distance = same_target_distances[before_target].get(node)
            result["nearest_destination_access_lost"] = same_distance is None
            if same_distance is not None:
                result["same_destination_after_distance_km"] = round(same_distance/1000, 2)
                result["same_destination_extra_distance_km"] = round(max(0., same_distance-pre_dist[node])/1000, 2)
            if result["status"] == "cut_off" and (node in boundary_component_nodes or props["snap_status"] != "on_mapped_road" or before_feature["snap_status"] != "on_mapped_road"):
                result.update(status="uncertain_access", assessment_reason="AOI_boundary_or_unverified_connector_may_change_isolation")
        elif node is not None and targets:
            result.update(status="baseline_disconnected", assessment_reason="no_pre_event_path_in_mapped_network; not_new_flood_isolation")
        return result

    for item in settlements:
        props = item["properties"]
        props["boundary_limited"] = props.get("_node") in boundary_component_nodes
        props["connectivity_by_destination_type"] = {kind: connectivity(props, kind) for kind in MAPPING.destination_profiles}
        props.update(connectivity(props, "all"))
        props["closure_min_length_m"] = minimum_overlap
        props["access_mode"] = "mapped motorable roads; no footpath or off-road travel assumed"
        props["operational_verification"] = "no current road restrictions, border opening, facility operations or structural damage verified"
        props["topology_method"] = "shared coordinate vertices with grade-separation heuristic; OSM node IDs and turn restrictions unavailable"
        props.pop("_node", None)
    for item in destinations:
        item["properties"].pop("_node", None)

    stats = {
        "buildings_total": len(buildings), "buildings_exposed": sum(item["properties"]["exposed"] for item in buildings),
        "roads_total": len(roads), "roads_exposed": sum(item["properties"]["exposed"] for item in roads),
        "road_exposed_km": round(exposed_road_length_m/1000, 3),
        "bridges_total": len(bridges), "bridges_exposed": sum(item["properties"]["exposed"] for item in bridges),
        "settlements_total": len(settlements), "settlements_cut_off": sum(item["properties"]["status"] == "cut_off" for item in settlements),
        "settlements_unassessed": sum(item["properties"]["status"] == "unassessed" for item in settlements),
        "settlements_baseline_disconnected": sum(item["properties"]["status"] == "baseline_disconnected" for item in settlements),
        "settlements_access_uncertain": sum(item["properties"]["status"] == "uncertain_access" for item in settlements),
        "roads_unassessed_or_partial": sum(not item["properties"]["assessment_complete"] for item in roads),
        "buildings_unassessed_or_partial": sum(not item["properties"]["assessment_complete"] for item in buildings),
        "bridges_unassessed_or_partial": sum(not item["properties"]["assessment_complete"] for item in bridges),
        "destinations_total": len(destinations),
    }
    def fc(features):
        return {"type": "FeatureCollection", "features": features}
    return {"buildings": fc(buildings), "roads": fc(roads), "bridges": fc(bridges), "settlements": fc(settlements), "destinations": fc(destinations)}, stats
