"""Read-only, typed views of a single analysis; no geospatial recalculation."""
from datetime import date, datetime
import hashlib
import json
import math
from urllib.parse import urlsplit
from uuid import UUID
from app.availability import normalize_analysis_availability

from app.copilot_types import (EvidenceFact, ToolResult, EmptyArgs, InfrastructureArgs,
                               ConnectivityArgs, FeatureArgs)


# Labels and units belong to Python, never to model-generated prose.
METRICS = {
    "water_km2": ("Potential new inundation", "सम्भावित नयाँ डुबान", "km²", "measurement"),
    "debris_km2": ("Possible surface/debris change", "सम्भावित माटो/ढुंगा वा भूआवरण परिवर्तन", "km²", "measurement"),
    "permanent_water_km2": ("Pre-existing water", "पहिलेदेखिको पानी", "km²", "measurement"),
    "valid_coverage_pct": ("Eligible observation coverage", "विश्लेषणका लागि योग्य अवलोकन क्षेत्र", "%", "measurement"),
    "optical_clear_pct": ("Paired clear optical coverage", "दुवै मितिको बादलरहित अप्टिकल क्षेत्र", "%", "measurement"),
    "buildings_exposed": ("Potentially exposed buildings", "सम्भावित प्रभावित भवन", "count", "potential_exposure"),
    "roads_exposed": ("Potentially exposed road features", "सम्भावित प्रभावित सडक अभिलेख", "count", "potential_exposure"),
    "bridges_exposed": ("Potentially exposed bridges", "सम्भावित प्रभावित पुल", "count", "potential_exposure"),
    "road_exposed_km": ("Road length intersecting mapped changes", "नक्साको परिवर्तन क्षेत्रमा पर्ने सडक लम्बाइ", "km", "potential_exposure"),
    "settlements_cut_off": ("Settlements with modeled loss of access", "मोडेल अनुसार सडक सम्पर्क गुमाएका बस्ती", "count", "modeled_connectivity"),
    "settlements_unassessed": ("Unassessed settlements", "मूल्याङ्कन हुन नसकेका बस्ती", "count", "modeled_connectivity"),
    "settlements_access_uncertain": ("Settlements with uncertain access", "सडक सम्पर्क अनिश्चित भएका बस्ती", "count", "modeled_connectivity"),
    "settlements_baseline_disconnected": ("Already disconnected settlements", "पहिलेदेखि सम्पर्क नभएका बस्ती", "count", "modeled_connectivity"),
    "water_eligible_coverage_pct": ("Water-class eligible coverage", "डुबान वर्गका लागि योग्य अवलोकन क्षेत्र", "%", "measurement"),
    "debris_eligible_coverage_pct": ("Surface-change-class eligible coverage", "भूआवरण परिवर्तन वर्गका लागि योग्य अवलोकन क्षेत्र", "%", "measurement"),
}
FLOOD_FIELDS = tuple(METRICS)[:5] + ("water_eligible_coverage_pct", "debris_eligible_coverage_pct")
INFRA_FIELDS = ("buildings_exposed", "roads_exposed", "bridges_exposed", "road_exposed_km")
ACCESS_FIELDS = tuple(METRICS)[9:13]
CONNECTIVITY_QUERY_FIELDS = ("filter_road_id", "matching_settlements_count", "route_membership_unavailable_count", "destination_type", "destination_assessment_unavailable_count")
FEATURE_FIELDS = {
    "name": ("Name", "नाम", None, "recorded_metadata"),
    "exposed": ("Mapped intersection", "नक्साको परिवर्तनसँग जुधेको", None, "potential_exposure"),
    "assessment_status": ("Assessment status", "मूल्याङ्कनको अवस्था", None, "potential_exposure"),
    "assessment_fraction": ("Observed sampling fraction", "अवलोकित नमुनाको अनुपात", "fraction", "measurement"),
    "length_m": ("Road length", "सडक लम्बाइ", "m", "measurement"),
    "exposed_length_m": ("Potentially exposed road length", "सम्भावित प्रभावित सडक लम्बाइ", "m", "potential_exposure"),
    "overlap_fraction": ("Footprint overlap fraction", "भवन क्षेत्र जुधेको अनुपात", "fraction", "potential_exposure"),
    "status": ("Modeled connectivity status", "मोडेल अनुसार सडक सम्पर्कको अवस्था", None, "modeled_connectivity"),
    "nearest_destination": ("Pre-event destination", "घटनाअघिको गन्तव्य", None, "modeled_connectivity"),
    "before_distance_km": ("Baseline route length", "घटनाअघिको मार्ग लम्बाइ", "km", "modeled_connectivity"),
    "after_distance_km": ("Disrupted-network route length", "अवरोध मानेपछिको मार्ग लम्बाइ", "km", "modeled_connectivity"),
    "before_destination_id": ("Baseline destination ID", "घटनाअघिको गन्तव्य पहिचान", None, "modeled_connectivity"),
    "after_destination_id": ("Disrupted-network destination ID", "अवरोध मानेपछिको गन्तव्य पहिचान", None, "modeled_connectivity"),
    "same_destination_extra_distance_km": ("Extra route distance to the same baseline destination", "उही घटनाअघिको गन्तव्यसम्म पुग्न थप मार्ग लम्बाइ", "km", "modeled_connectivity"),
    "baseline_road_ids": ("Baseline route road IDs", "घटनाअघिको मार्गका सडक पहिचान", None, "modeled_connectivity"),
    "after_road_ids": ("Disrupted-network route road IDs", "अवरोध मानेपछिको मार्गका सडक पहिचान", None, "modeled_connectivity"),
}


class EvidenceError(ValueError):
    pass


class EvidenceContext:
    def __init__(self, run, expected_run_id=None):
        run = normalize_analysis_availability(run)
        try:
            self.run_id = str(UUID(str(run["id"])))
            if expected_run_id is not None and self.run_id != str(UUID(str(expected_run_id))):
                raise EvidenceError("Analysis identifier does not match the requested run")
        except (KeyError, TypeError, ValueError):
            raise EvidenceError("Invalid or mismatched analysis identifier") from None
        if run.get("mode") not in ("demo", "live"):
            raise EvidenceError("Analysis data mode is missing or invalid")
        self.run = run
        self.stats = run.get("stats") if isinstance(run.get("stats"), dict) else {}
        self.provenance = run.get("provenance") if isinstance(run.get("provenance"), dict) else {}
        self.facts = {}
        self.global_fields = {}
        self.index = {}
        self.feature_positions = {}
        self.returned_fact_ids = set()
        self.layer_missing = set()
        layers = run.get("layers") if isinstance(run.get("layers"), dict) else {}
        for layer in ("buildings", "roads", "bridges", "settlements", "destinations"):
            features = layers.get(layer, {}).get("features") if isinstance(layers.get(layer), dict) else None
            if not isinstance(features, list):
                self.layer_missing.add(layer)
                continue
            seen = set()
            for position, item in enumerate(features):
                if not isinstance(item, dict) or not isinstance(item.get("properties"), dict):
                    continue
                identifier = item["properties"].get("id")
                if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                    continue
                if identifier in seen:
                    raise EvidenceError("Duplicate feature identifiers in a saved layer")
                seen.add(identifier)
                self.index[(layer, identifier)] = item
                self.feature_positions[(layer, identifier)] = position
        self._global_facts()

    def _add(self, path, field, value, label, *, unit=None, interpretation="recorded_metadata",
             subject_type="analysis", subject_id=None, subject_name=None, method="Saved analysis metadata",
             source_ids=None, validator=None, missing_reason=None, uncertainty=None):
        status = "available"
        if value is None:
            status, missing_reason = "missing", missing_reason or "The selected analysis does not contain this measurement or metadata."
        elif validator and not validator(value):
            status, missing_reason, value = "invalid", "The saved field failed type, range or format validation.", None
        fact_id = self.run_id + ":" + hashlib.sha256(path.encode()).hexdigest()[:20]
        fact = EvidenceFact(fact_id=fact_id, run_id=self.run_id, field_name=field, path=path,
                            subject_type=subject_type, subject_id=subject_id or self.run_id,
                            subject_name=subject_name if isinstance(subject_name, str) else self.run.get("name") if subject_type == "analysis" and isinstance(self.run.get("name"), str) else None,
                            value=value, unit=unit, status=status, label_en=label[0], label_ne=label[1],
                            source_ids=source_ids or ["result.json"], method=method,
                            timestamp=self.run.get("created_at") if isinstance(self.run.get("created_at"), str) else None,
                            interpretation=interpretation if status == "available" else "unavailable",
                            uncertainty=uncertainty or ("Satellite change/exposure and simulated connectivity require independent field verification." if interpretation in ("measurement", "potential_exposure", "modeled_connectivity") else None),
                            missing_reason=missing_reason)
        self.facts[fact_id] = fact
        return fact

    @staticmethod
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    @staticmethod
    def text(value):
        return isinstance(value, str) and 0 < len(value) <= 1000 and not any(ord(c) < 32 and c not in '\n\t' for c in value)

    @staticmethod
    def timestamp(value):
        try:
            return isinstance(value, str) and bool(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return False

    def _global_facts(self):
        for field, (en, ne, unit, semantics) in METRICS.items():
            check = (lambda v: type(v) is int and v >= 0) if unit == "count" else self.numeric
            if unit == "%":
                check = lambda v: self.numeric(v) and v <= 100
            method = "Saved raster class area and observation masks" if field in FLOOD_FIELDS else "Saved metric OSM/hazard intersections" if field in INFRA_FIELDS else "Saved pre-event and assumed-disrupted directed road graph"
            availability = self.run.get("class_availability", {}).get(field.removesuffix("_km2"), {}) if field in ("water_km2", "debris_km2") else {}
            fact = self._add("stats." + field, field, self.stats.get(field), (en, ne), unit=unit,
                             interpretation=semantics, method=method, validator=check,
                             missing_reason=availability.get("missing_reason"),
                             uncertainty="The value covers only class-eligible observations; the rest of the analysis area remains unknown." if availability.get("status") == "partial" else None)
            self.global_fields[field] = fact
        mapping = self.provenance.get("mapping", self.provenance.get("water_model", {}))
        mapping = mapping if isinstance(mapping, dict) else {}
        specs = [
            ("bbox", "area_bounds", self.run.get("bbox"), ("Analysis bounds (west, south, east, north)", "विश्लेषण क्षेत्रको सीमा (पश्चिम, दक्षिण, पूर्व, उत्तर)"), "degrees", lambda v: isinstance(v,list) and len(v)==4 and all(type(x) in (int,float) and math.isfinite(x) for x in v)),
            ("provenance.crs", "analysis_crs", self.provenance.get("crs"), ("Analysis CRS", "विश्लेषणको निर्देशाङ्क प्रणाली"), None, self.text),
            ("provenance.osm_source_api", "osm_source", self.provenance.get("osm_source_api"), ("Historical OSM service", "ऐतिहासिक OSM सेवा"), None, self.text),
            ("processing_status", "processing_status", self.run.get("processing_status"), ("Processing status", "प्रशोधनको अवस्था"), None, self.text),
            ("analysis_status", "analysis_status", self.run.get("analysis_status"), ("Observation completeness", "अवलोकनको पूर्णता"), None, self.text),
            ("name", "analysis_name", self.run.get("name"), ("Analysis area", "विश्लेषण क्षेत्र"), None, self.text),
            ("flood_date", "event_date", self.run.get("flood_date"), ("Requested event date", "घटनाको मिति"), "date", self.timestamp),
            ("created_at", "processed_at", self.run.get("created_at"), ("Analysis creation time", "विश्लेषण तयार भएको समय"), "date", self.timestamp),
            ("mode", "data_mode", self.run["mode"], ("Data mode", "तथ्याङ्कको प्रकार"), None, lambda v: v in ("demo", "live")),
            ("provenance.osm_snapshot", "osm_snapshot", self.provenance.get("osm_snapshot"), ("Historical OSM snapshot", "ऐतिहासिक OSM को मिति"), "date", self.timestamp),
            ("provenance.mapping.algorithm" if "mapping" in self.provenance else "provenance.water_model.algorithm", "mapping_method", mapping.get("algorithm"), ("Detection method", "परिवर्तन पहिचान विधि"), None, self.text),
            ("provenance.radar_processing", "radar_processing", self.provenance.get("radar_processing"), ("Radar processing", "राडार प्रशोधन"), None, self.text),
            ("provenance.optical_scenes", "optical_scene_ids", self.provenance.get("optical_scenes"), ("Processed optical product IDs", "प्रशोधित अप्टिकल तस्बिर पहिचान"), None, lambda v: isinstance(v, list) and all(self.text(x) for x in v)),
            ("limitations", "limitations", self.run.get("limitations"), ("Recorded limitations", "अभिलेखित सीमाहरू"), None, lambda v: isinstance(v, list) and all(self.text(x) for x in v)),
            ("warnings", "warnings", self.run.get("warnings"), ("Recorded processing warnings", "अभिलेखित प्रशोधन चेतावनी"), None, lambda v: isinstance(v, list) and all(self.text(x) for x in v)),
        ]
        for path, field, value, label, unit, check in specs:
            self.global_fields[field] = self._add(path, field, value, label, unit=unit, validator=check,
                                                   interpretation="limitation" if field in ("limitations","warnings") else "recorded_metadata")
        observations = self.provenance.get("optical_observations")
        dates = [o.get('acquired') for o in observations if isinstance(o,dict) and o.get('id') in (self.provenance.get('optical_scenes') or [])] if isinstance(observations,list) and observations else None
        self.global_fields['optical_acquisition_dates'] = self._add('provenance.optical_observations', 'optical_acquisition_dates', dates,
            ('Processed optical acquisition dates', 'प्रशोधित अप्टिकल अवलोकन मिति'), unit='date', validator=lambda v: isinstance(v,list) and all(self.timestamp(x) for x in v))
        acquisition = self.provenance.get('acquisition')
        dem = acquisition.get('dem') if isinstance(acquisition,dict) else None
        tiles = dem.get('tiles') if isinstance(dem,dict) else None
        # Send public product identifiers, never signed asset URLs or credentials.
        dem_ids = [urlsplit(t).path.rsplit('/',1)[-1] for t in tiles] if isinstance(tiles,list) and all(self.text(t) for t in tiles) else None
        self.global_fields['dem_product_ids'] = self._add('provenance.acquisition.dem.tiles', 'dem_product_ids', dem_ids,
            ('Recorded DEM tile IDs', 'अभिलेखित DEM तस्बिर पहिचान'), validator=lambda v: isinstance(v,list) and bool(v) and all(self.text(x) for x in v))
        scenes = self.provenance.get("radar_scenes", [])
        for index, epoch in enumerate(("before", "after")):
            scene = scenes[index] if isinstance(scenes, list) and len(scenes) > index and isinstance(scenes[index], dict) else {}
            for field, label, check in (("acquired", ("Radar acquisition", "राडार अवलोकन मिति"), self.timestamp),
                                         ("id", ("Radar product ID", "राडार तस्बिर पहिचान"), self.text),
                                         ("relative_orbit", ("Relative orbit", "सापेक्ष कक्षा"), lambda v: type(v) is int and v > 0),
                                         ("direction", ("Orbit direction", "कक्षाको दिशा"), lambda v: v in ("ascending", "descending"))):
                key = f"radar_{epoch}_{field}"
                self.global_fields[key] = self._add(f"provenance.radar_scenes.{index}.{field}", key, scene.get(field),
                    (f"{epoch.title()} {label[0].lower()}", ("घटनाअघि " if epoch == "before" else "घटनापछि ") + label[1]),
                    unit="date" if field == "acquired" else None, subject_type="observation", subject_id=f"radar:{epoch}",
                    subject_name=scene.get("id"), source_ids=[scene["id"]] if self.text(scene.get("id")) else None, validator=check)
        parameters = self.run.get("parameters") if isinstance(self.run.get("parameters"), dict) else {}
        for field, label in (("water_threshold_db", ("Water backscatter threshold", "पानी पहिचानको राडार सीमा")),
                             ("change_threshold_db", ("Radar-change threshold", "राडार परिवर्तनको सीमा")),
                             ("max_slope_deg", ("Water-target slope limit", "पानी पहिचानको भिरालोपन सीमा")),
                             ("resolution_m", ("Grid resolution", "ग्रिडको रिजोल्युसन"))):
            self.global_fields[field] = self._add("parameters." + field, field, parameters.get(field), label,
                unit="dB" if field.endswith("db") else "degrees" if field == "max_slope_deg" else "m",
                validator=lambda v: type(v) in (int, float) and math.isfinite(v))

    @staticmethod
    def connectivity_properties(properties, destination_type="all"):
        if destination_type == "all":
            return properties
        typed = properties.get("connectivity_by_destination_type")
        selected = typed.get(destination_type) if isinstance(typed, dict) else None
        return selected if isinstance(selected, dict) else {}

    def feature_facts(self, layer, identifier, destination_type="all"):
        feature = self.index.get((layer, identifier))
        if feature is None:
            return []
        original_props = feature["properties"]
        typed = layer == "settlements" and destination_type != "all"
        props = self.connectivity_properties(original_props, destination_type) if typed else original_props
        prefix = f"layers.{layer}.features.{self.feature_positions[(layer, identifier)]}.properties"
        if typed:
            prefix += ".connectivity_by_destination_type." + destination_type
        position = self.feature_positions[(layer, identifier)]
        fields = ["name", "exposed", "assessment_status", "assessment_fraction"]
        if layer == "roads":
            fields += ["length_m", "exposed_length_m"]
        elif layer == "buildings":
            fields += ["overlap_fraction"]
        elif layer == "settlements":
            fields = ["name", "status", "nearest_destination", "before_distance_km", "after_distance_km",
                      "before_destination_id", "after_destination_id", "same_destination_extra_distance_km",
                      "baseline_road_ids", "after_road_ids"]
        facts = []
        for field in fields:
            en, ne, unit, semantics = FEATURE_FIELDS[field]
            path = prefix + "." + field
            value = props.get(field)
            if field == "name":
                value = original_props.get(field)
                path = f"layers.{layer}.features.{position}.properties.name"
            if field == "exposed" and value is False and props.get("assessment_status") == "unassessed_or_partial":
                value = None
            if field in ("baseline_road_ids", "after_road_ids"):
                route = "baseline_route" if field == "baseline_road_ids" else "after_route"
                value = props.get(route, {}).get("properties", {}).get("road_ids") if isinstance(props.get(route), dict) else None
                path = prefix + f".{route}.properties.road_ids"
            if typed and field != "name":
                en = ("Hospital access: " if destination_type == "hospital" else "Town access: ") + en
                ne = ("अस्पतालसम्मको पहुँच: " if destination_type == "hospital" else "नगरसम्मको पहुँच: ") + ne
            check = self.numeric if unit in ("m", "km") else (lambda v: self.numeric(v) and v <= 1) if unit == "fraction" else self.text
            if field == "exposed":
                check = lambda v: type(v) is bool
            elif field == "status":
                check = lambda v: v in ("cut_off", "connected", "uncertain_access", "unassessed", "baseline_disconnected")
            elif field == "assessment_status":
                check = lambda v: v in ("potential_exposure", "no_mapped_change_at_observed_samples", "unassessed_or_partial")
            elif field.endswith("road_ids"):
                check = lambda v: isinstance(v, list) and all(isinstance(x, str) and ("roads", x) in self.index for x in v)
            facts.append(self._add(path, field, value, (en, ne), unit=unit, interpretation=semantics,
                subject_type=layer, subject_id=identifier, subject_name=original_props.get("name"),
                source_ids=[f"{layer}.geojson", identifier], method=props.get("assessment_method") if self.text(props.get("assessment_method")) else "Saved infrastructure intersections or road-graph result", validator=check,
                missing_reason="The selected run has no saved destination-specific graph evidence; combined town/hospital access cannot answer this question." if typed and not props else None))
        return facts

    def _result(self, tool, facts, *, feature_ids=None, next_offset=None, missing=None):
        self.returned_fact_ids.update(f.fact_id for f in facts)
        return ToolResult(run_id=self.run_id, tool=tool,
                          status="not_found" if missing and not facts else "partial" if missing or any(f.status != "available" for f in facts) else "ok",
                          facts=facts, feature_ids=feature_ids or [], next_offset=next_offset, missing=missing or [],
                          limitations=["Potential exposure is not confirmed damage. Connectivity assumes intersecting road segments are impassable; field closures are unverified."])

    def event_summary(self, args):
        return self._result("event_summary", list(self.global_fields.values()))

    def flood_surface_change(self, args):
        keys = FLOOD_FIELDS + ("mapping_method", "water_threshold_db", "change_threshold_db", "max_slope_deg", "resolution_m")
        return self._result("flood_surface_change", [self.global_fields[k] for k in keys])

    def infrastructure_impacts(self, args):
        rows = [(identifier, item) for (layer, identifier), item in self.index.items() if layer == args.asset_type
                and (not args.only_exposed or item["properties"].get("exposed") is True)
                and (not args.query or args.query.casefold() in (identifier+' '+str(item['properties'].get('name',''))).casefold())]
        selection = rows[args.offset:args.offset + args.limit]
        fields = [args.asset_type + "_exposed"] + (["road_exposed_km"] if args.asset_type == "roads" else [])
        facts = [self.global_fields[k] for k in fields]
        for identifier, _ in selection:
            facts += self.feature_facts(args.asset_type, identifier)
        return self._result("infrastructure_impacts", facts, feature_ids=[x[0] for x in selection],
            next_offset=args.offset + args.limit if args.offset + args.limit < len(rows) else None,
            missing=[f"Saved {args.asset_type} layer unavailable"] if args.asset_type in self.layer_missing else [])

    def route_membership(self, properties, road_id):
        """None means unavailable; False requires at least one valid saved route."""
        available = False
        for key in ("baseline_route", "after_route"):
            route = properties.get(key)
            route_props = route.get("properties") if isinstance(route, dict) else None
            ids = route_props.get("road_ids") if isinstance(route_props, dict) else None
            if not isinstance(ids, list) or not all(isinstance(i, str) and ("roads", i) in self.index for i in ids):
                continue
            available = True
            if road_id in ids:
                return True
        return False if available else None

    @staticmethod
    def connectivity_query_scope(road_id, status, query, destination_type="all"):
        scope = {"road_id": road_id, "status": status, "query": query}
        if destination_type != "all":
            scope["destination_type"] = destination_type
        return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]

    def settlement_connectivity(self, args):
        if args.road_id and ("roads", args.road_id) not in self.index:
            return self._result("settlement_connectivity", [], missing=["Road identifier is not present in this analysis"])
        candidates = [(identifier, item) for (layer, identifier), item in self.index.items() if layer == "settlements"
                      and (not args.query or args.query.casefold() in (identifier+' '+str(item['properties'].get('name',''))).casefold())]
        destination_missing = sum(not self.connectivity_properties(item["properties"], args.destination_type) for _, item in candidates)
        rows = [(identifier, item) for identifier, item in candidates
                if (args.status == "all" or self.connectivity_properties(item["properties"], args.destination_type).get("status") == args.status)
                and (args.destination_type == "all" or self.connectivity_properties(item["properties"], args.destination_type))]
        if args.road_id:
            unknown = sum(self.route_membership(self.connectivity_properties(item["properties"], args.destination_type), args.road_id) is None for _, item in rows)
            rows = [(identifier, item) for identifier, item in rows if self.route_membership(self.connectivity_properties(item["properties"], args.destination_type), args.road_id) is True]
        if args.road_id or args.destination_type != "all":
            scope = self.connectivity_query_scope(args.road_id, args.status, args.query, args.destination_type)
            prefix = "queries.settlement_connectivity." + scope
            missing_layer = "settlements" in self.layer_missing
            typed_evidence_absent = bool(candidates) and args.destination_type != "all" and destination_missing == len(candidates)
            facts = [
                self._add(prefix + ".matching_count", "matching_settlements_count", None if missing_layer else len(rows),
                          ("Recorded settlement route matches for the requested filters", "अनुरोध गरिएका सर्तसँग मिल्ने अभिलेखित बस्ती मार्ग"),
                          unit="count", interpretation="modeled_connectivity", method="Saved baseline/disrupted route road-ID membership; before pagination",
                          source_ids=["settlements.geojson"],
                          missing_reason="This run has no destination-specific graph results; combined town/hospital access cannot determine hospital-only or town-only access." if typed_evidence_absent else None),
            ]
            if typed_evidence_absent:
                facts[0] = self._add(prefix + ".matching_count", "matching_settlements_count", None,
                    ("Recorded settlement route matches for the requested filters", "अनुरोध गरिएका सर्तसँग मिल्ने अभिलेखित बस्ती मार्ग"),
                    unit="count", interpretation="modeled_connectivity", source_ids=["settlements.geojson"],
                    missing_reason="This run has no destination-specific graph results; combined town/hospital access cannot determine hospital-only or town-only access.")
            if args.road_id:
                facts += [self._add(prefix + ".road_id", "filter_road_id", args.road_id,
                    ("Road used to filter saved settlement routes", "बस्तीका अभिलेखित मार्ग छान्न प्रयोग गरिएको सडक"), source_ids=["roads.geojson", args.road_id]),
                    self._add(prefix + ".unavailable_count", "route_membership_unavailable_count", None if missing_layer else unknown,
                    ("Settlements without valid saved route evidence for the requested filters", "अनुरोध गरिएका सर्तका लागि मान्य अभिलेखित मार्ग प्रमाण नभएका बस्ती"),
                    unit="count", interpretation="modeled_connectivity", source_ids=["settlements.geojson"])]
            if args.destination_type != "all":
                facts += [self._add(prefix + ".destination_type", "destination_type", args.destination_type,
                    ("Requested destination type", "अनुरोध गरिएको गन्तव्यको प्रकार"), source_ids=["settlements.geojson"]),
                    self._add(prefix + ".destination_unavailable_count", "destination_assessment_unavailable_count", None if missing_layer else destination_missing,
                    ("Settlements without saved destination-specific analysis", "गन्तव्य अनुसारको अभिलेखित विश्लेषण नभएका बस्ती"),
                    unit="count", interpretation="modeled_connectivity", source_ids=["settlements.geojson"])]
        else:
            facts = [self.global_fields[k] for k in ACCESS_FIELDS]
        selection = rows[args.offset:args.offset + args.limit]
        for identifier, _ in selection:
            facts += self.feature_facts("settlements", identifier, args.destination_type)
        return self._result("settlement_connectivity", facts, feature_ids=[x[0] for x in selection],
            next_offset=args.offset + args.limit if args.offset + args.limit < len(rows) else None,
            missing=["Saved settlements layer unavailable"] if "settlements" in self.layer_missing else [])

    def sources_limitations(self, args):
        return self._result("sources_limitations", [f for key, f in self.global_fields.items() if key not in METRICS])

    def feature_evidence(self, args):
        facts = self.feature_facts(args.layer, args.feature_id, args.destination_type)
        return self._result("feature_evidence", facts, feature_ids=[args.feature_id] if facts else [],
                            missing=[] if facts else ["Feature identifier is not present in the selected analysis/layer"])

    def call(self, name, arguments):
        if name not in TOOL_REGISTRY:
            raise EvidenceError("Tool is not approved")
        model, _ = TOOL_REGISTRY[name]
        args = model.model_validate(arguments)
        # Only this explicit registry is callable; arguments cannot choose a path, run or method.
        return getattr(self, name)(args)


TOOL_REGISTRY = {
    "event_summary": (EmptyArgs, "Retrieve the selected event, computed aggregate metrics, acquisition dates and limitations."),
    "flood_surface_change": (EmptyArgs, "Retrieve measured potential water/surface change, units, method and thresholds."),
    "infrastructure_impacts": (InfrastructureArgs, "Retrieve saved potentially exposed buildings, roads or bridges, with feature IDs and measurements. Paginated."),
    "settlement_connectivity": (ConnectivityArgs, "Retrieve modeled connectivity, destinations and saved route evidence. Optionally filter by an exact road_id from this run; matches use recorded baseline/disrupted routes, not physical proximity. Filtered counts precede pagination; missing routes remain unknown. No field closures are confirmed. Paginated."),
    "sources_limitations": (EmptyArgs, "Retrieve actual recorded products, acquisition dates, historical OSM, processing and limitations."),
    "feature_evidence": (FeatureArgs, "Retrieve saved evidence for one exact feature ID and layer in this run; never accept filesystem paths."),
}


def tool_definitions():
    return [{"type": "function", "name": name, "description": description,
             "parameters": model.model_json_schema(), "strict": True} for name, (model, description) in TOOL_REGISTRY.items()]
