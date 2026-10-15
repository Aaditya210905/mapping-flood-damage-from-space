"use strict";

const $ = (id) => document.getElementById(id);
const state = { run: null, sources: null, epoch: "after", layers: {}, rasters: {}, tracing: false, busy: false, asking: false, trace: null, history: [], needsMapFit: false, selection: null };
const map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true, minZoom: 3, maxZoom: 19, zoomSnap: 0.25 }).setView([0, 0], 3);
map.createPane("radar");
map.getPane("radar").style.zIndex = 250;
map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>');
map.attributionControl.addAttribution("© OpenStreetMap contributors · Contains modified Copernicus Sentinel data 2026");
L.control.scale({ position: "bottomright", imperial: false }).addTo(map);

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

async function api(url, options) {
  let response;
  try { response = await fetch(url, options); }
  catch { throw new Error("Unable to reach FloodLens. Check that the local server is running, then retry."); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("FloodLens could not complete this request. Please retry or check the server status."); }
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(Array.isArray(detail) ? detail.map((e) => e.msg).join("; ") : detail || `Request failed (${response.status})`);
  }
  return data;
}

const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const dateLabel = (value) => new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
const analysisBase = () => `/api/analyses/${state.run.id}`;
const numberLabel = (value, digits = 0) => Number.isFinite(value) ? value.toLocaleString("en", {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "Unavailable";
const accessNames = { cut_off: "Modeled cutoff · assumed closures", connected: "Modeled observed route", uncertain_access: "Uncertain access", unassessed: "Unassessed", baseline_disconnected: "Pre-event disconnected" };

function appendCitedAnswer(container, response) {
  const facts = new Map(response.facts.map((fact) => [fact.fact_id, fact]));
  const pattern = /\[([0-9a-f-]{36}:[0-9a-f]{20})\]/g;
  let offset = 0;
  for (const match of response.answer.matchAll(pattern)) {
    container.append(document.createTextNode(response.answer.slice(offset, match.index)));
    const fact = facts.get(match[1]);
    if (fact) {
      const button = document.createElement("button"); button.type = "button"; button.className = "fact-link";
      button.textContent = `[${response.facts.indexOf(fact) + 1}]`; button.title = fact.fact_id;
      button.setAttribute("aria-label", `Inspect ${fact.label_en}: ${fact.fact_id}`);
      button.addEventListener("click", () => inspectFact(fact)); container.append(button);
    } else container.append(document.createTextNode(match[0]));
    offset = match.index + match[0].length;
  }
  container.append(document.createTextNode(response.answer.slice(offset)));
}

function renderSettlementTable() {
  if (!state.run) return;
  const status = $("access-filter").value, query = $("settlement-search").value.toLocaleLowerCase();
  const features = state.run.layers.settlements.features.filter((f) => (status === "all" || f.properties.status === status) &&
    [f.properties.name, f.properties["name:en"], f.properties["name:ne"]].some((name) => String(name || "").toLocaleLowerCase().includes(query)));
  $("settlement-table").innerHTML = features.map((f) => {
    const p = f.properties;
    const distances = `${p.before_distance_km == null ? "—" : p.before_distance_km + " km"} / ${p.after_distance_km == null ? "—" : p.after_distance_km + " km"}`;
    return `<tr><td><button type="button" class="feature-link" data-settlement-id="${escapeHTML(p.id)}">${escapeHTML(p.name)}</button></td><td><span class="access-status ${escapeHTML(p.status)}">${escapeHTML(accessNames[p.status] || "Unknown")}</span></td><td>${escapeHTML(p.nearest_destination || "Not mapped")}</td><td>${escapeHTML(distances)}</td></tr>`;
  }).join("") || '<tr><td colspan="4">No settlements match this filter. Missing OSM records cannot establish absence.</td></tr>';
  document.querySelectorAll("[data-settlement-id]").forEach((button) => button.addEventListener("click", () => inspectFeature("settlements", button.dataset.settlementId)));
}

async function inspectFact(fact) {
  if (fact.run_id !== state.run?.id) return;
  if (["buildings", "roads", "bridges", "settlements", "destinations"].includes(fact.subject_type)) {
    await inspectFeature(fact.subject_type, fact.subject_id);
  } else {
    $("feature-detail").hidden = false;
    $("feature-detail-title").textContent = fact.label_en;
    $("feature-detail-content").textContent = `${fact.fact_id} · ${fact.status === "available" ? JSON.stringify(fact.value) : "Unavailable"} ${fact.unit || ""}\n${fact.method}\n${fact.missing_reason || fact.uncertainty || ""}\nArea-wide fact; no individual feature is implied.`;
    setView("map"); fitSelectedMap();
  }
  $("feature-detail").scrollIntoView({behavior: "smooth", block: "nearest"});
}

async function inspectFeature(layer, identifier) {
  if (!state.run) return;
  const runId = state.run.id;
  $("feature-detail").hidden = false;
  $("feature-detail-title").textContent = "Loading feature evidence…";
  $("feature-detail-content").textContent = "Reading the selected analysis only.";
  try {
    const data = await api(`/api/analyses/${runId}/features/${layer}?feature_id=${encodeURIComponent(identifier)}`);
    if (state.run?.id !== runId) return;
    const feature = data.feature, p = feature.properties;
    $("feature-detail-title").textContent = [p["name:en"] || p.name || identifier, p["name:ne"]].filter((name, index, names) => name && names.indexOf(name) === index).join(" / ");
    const pairs = [["OSM ID", p.id], ["Assessment", p.assessment_status || "Unknown"], ["Observed fraction", p.assessment_fraction == null ? "Unavailable" : `${numberLabel(p.assessment_fraction * 100, 1)}%`], ["Exposure", p.exposed ? "Potential exposure; physical damage not confirmed" : p.assessment_fraction < 1 ? "Unassessed or partial; absence of exposure is unknown" : "No mapped overlap in assessed hazard classes"], ["Counting unit", p.counting_unit || "Historical OSM element; inventory completeness unknown"]];
    if (layer === "settlements") {
      pairs.push(["Access", accessNames[p.status] || "Unknown"], ["Baseline destination", p.nearest_destination || "Not mapped"], ["After destination", p.after_destination_name || p.after_destination || "Unavailable"], ["Snap connector", `${numberLabel(p.snap_distance_m, 1)} m · off-road access unverified`], ["Same-target extra distance", p.same_destination_extra_distance_km == null ? "Unavailable" : `${p.same_destination_extra_distance_km} km`], ["Boundary", p.boundary_limited ? "Routes beyond the area may change this result" : "Routes outside the area are not assessed"]);
      const hospital = p.connectivity_by_destination_type?.hospital;
      pairs.push(["Hospital access", hospital ? `${accessNames[hospital.status] || hospital.status} · ${hospital.nearest_destination || hospital.before_destination_name || "No mapped reachable hospital"}` : "Hospital-specific routing was not computed for this saved run"]);
    }
    if (layer === "buildings") pairs.push(["Footprint overlap", `${numberLabel(p.overlap_fraction * 100, 1)}%`]);
    if (layer === "roads" || layer === "bridges") pairs.push(["Mapped overlap", `${numberLabel(p.exposed_length_m, 1)} m`], ["Closure", p.closure_assessment || "Candidate closure scenario; not a verified closure"]);
    if (layer === "bridges") pairs.push(["Bridge interpretation", "Water under a bridge does not establish deck damage or a failed crossing"]);
    $("feature-detail-content").innerHTML = pairs.map(([key, value]) => `<div class="provenance-row"><span>${escapeHTML(key)}</span><strong>${escapeHTML(value)}</strong></div>`).join("") + `<p class="field-note">Routes: dashed gray = baseline; blue = modeled after-event route. Unobserved links and assumed closures require field checks. ${escapeHTML(data.evidence.facts.map((fact) => fact.fact_id).join(" · "))}</p>`;
    if (state.selection) map.removeLayer(state.selection);
    const selected = L.geoJSON(feature, {style: {color: "#872DB1", weight: 5, fillOpacity: .25}, pointToLayer: (_, latlng) => L.circleMarker(latlng, {radius: 10, color: "#872DB1", weight: 3, fillOpacity: .2})});
    const routes = [selected];
    for (const [key, color, dashArray] of [["baseline_route", "#545454", "6 5"], ["after_route", "#0072B2", null]]) {
      if (p[key]) routes.push(L.geoJSON(p[key], {style: {color, dashArray, weight: 4}}));
    }
    state.selection = L.featureGroup(routes).addTo(map);
    setView("map");
    setTimeout(() => { map.invalidateSize(); const bounds = state.selection?.getBounds(); if (bounds?.isValid()) map.fitBounds(bounds.pad(.15), {maxZoom: 16}); }, 0);
  } catch (error) { $("feature-detail-title").textContent = "Feature evidence unavailable"; $("feature-detail-content").textContent = error.message; }
}

function boundsFor(run) {
  const [west, south, east, north] = run.display_bbox || run.bbox;
  return [[south, west], [north, east]];
}

function popup(title, pairs) {
  return `<strong>${escapeHTML(title)}</strong><br>${pairs.map(([key, value]) => `${escapeHTML(key)}: ${escapeHTML(value)}`).join("<br>")}`;
}

function sourceUsage(source) {
  if (source.category === "evaluation_only") return [state.run?.mode === "live" && state.run?.evaluation?.metrics ? "Comparison attached" : "Evaluation only", "reference"];
  if (!state.run) return ["On demand", ""];
  if (state.run.mode === "demo") return ["Synthetic fixture", "demo"];
  if (source.id === "sentinel2" && !state.run.provenance.optical_scenes?.length) return ["Unavailable in run", ""];
  return ["Used in this run", "active"];
}

function renderSources() {
  if (!state.sources) return;
  const data = state.sources;
  const inputs = data.sources.filter((source) => source.category === "input");
  const actualRuns = data.workspace?.completed_actual_runs || 0;
  $("workspace-data-status").textContent = state.run ? (state.run.mode === "demo" ? "Synthetic demonstration loaded" : "Actual observations loaded") : (actualRuns ? `${actualRuns} saved actual-data ${actualRuns === 1 ? "run" : "runs"}` : "No actual observations acquired");
  $("ai-component-status").textContent = "English / Nepali situation-report copilot";
  $("dataset-status").innerHTML = inputs.map((source) => {
    const [label] = sourceUsage(source);
    return `<div class="source-summary-row"><strong>${escapeHTML(source.name)}</strong><span>${escapeHTML(label)}</span></div>`;
  }).join("") + '<p>Satellite change detection supplies the map. The copilot uses its saved facts to answer questions and generate reports.</p>';
  const groups = [
    ["input", "Live analysis inputs", "Fetched when needed"],
    ["evaluation_only", "Independent reference maps", "Never prediction inputs"],
  ];
  $("source-inventory").innerHTML = groups.map(([category, title, subtitle]) => {
    const cards = data.sources.filter((source) => source.category === category).map((source) => {
      const [label, kind] = sourceUsage(source);
      return `<article class="source-card"><div class="source-card-header"><h4><a href="${escapeHTML(source.url)}" target="_blank" rel="noreferrer">${escapeHTML(source.name)} ↗</a></h4><span class="tag ${kind}">${escapeHTML(label)}</span></div><p>${escapeHTML(source.description || source.role)}</p><small>${escapeHTML(source.license)}</small></article>`;
    }).join("");
    return `<section class="source-group"><div class="source-group-title"><h4>${title}</h4><span>${subtitle}</span></div><div class="source-list">${cards}</div></section>`;
  }).join("") + '<article class="source-card"><div class="source-card-header"><h4>Situation-report copilot</h4><span class="tag active">Selected AI component</span></div><p>Answers rescuers’ questions and writes English and Nepali briefings using areas, counts and settlement names from the saved maps. Unsupported questions receive no estimate.</p><small>Mapping uses change-detection rules. No flood-segmentation weights or training datasets are required. <a href="/api/copilot" target="_blank" rel="noreferrer">Inspect copilot method ↗</a></small></article>';
  syncSetup();
}

function syncSetup() {
  const demo = $("mode").value === "demo";
  document.querySelectorAll("#analysis-form input, #analysis-form select").forEach((control) => { control.disabled = state.busy; });
  $("satellite-provider").disabled = demo || state.busy;
  $("credential-note").hidden = demo;
  $("mode-note").textContent = demo ? "Generated test fixtures. No satellite data is downloaded." : "Observations are fetched when you run an analysis.";
  $("mapping-method-note").textContent = demo ? "Generated inputs supply the maps and facts for this synthetic copilot demonstration." : "Satellite change detection builds the map. The situation-report copilot answers questions and writes briefings from its results.";
  $("credential-note").textContent = $("satellite-provider").value === "copernicus" ? "This provider requires your Copernicus account credentials. Historical OSM is verified separately." : "Public access requires no satellite account. Historical OSM availability is checked separately.";
}

function syncResultControls() {
  const available = Boolean(state.run) && !state.busy;
  document.querySelectorAll("[data-layer], [data-epoch], #fit-map, #trace-toggle").forEach((control) => { control.disabled = !available; });
  for (const kind of ["water", "debris"]) {
    const control = document.querySelector(`[data-layer="${kind}"]`);
    if (state.run?.class_availability?.[kind]?.status === "unavailable") { control.disabled = true; control.checked = false; }
  }
  document.querySelectorAll("[data-question], #question, #ask-button").forEach((control) => { control.disabled = !available || state.asking; });
  $("run-button").disabled = state.busy;
  $("preview-demo").disabled = state.busy;
  $("map-legend").hidden = !state.run;
  $("generate-report").disabled = !available || state.asking;
  $("language").disabled = state.asking;
  const engine = state.sources?.copilot?.llm?.configured ? "Groq with validated map facts" : "Deterministic fallback with saved map facts";
  $("copilot-status").textContent = state.run ? engine + ". English and Nepali supported." : "Available after analysis. English and Nepali answers use saved map facts.";
  syncSetup();
}

function renderMap(run) {
  Object.values(state.layers).forEach((layer) => map.removeLayer(layer));
  Object.values(state.rasters).forEach((layer) => map.removeLayer(layer));
  if (state.trace) { map.removeLayer(state.trace); state.trace = null; }
  if (state.selection) { map.removeLayer(state.selection); state.selection = null; }
  $("feature-detail").hidden = true;
  state.tracing = false;
  $("trace-toggle").classList.remove("active");
  $("trace-result").hidden = true;
  state.layers = {};
  const bounds = boundsFor(run);
  const source = (epoch) => `${analysisBase()}/files/${epoch}.png`;
  state.rasters = { before: L.imageOverlay(source("before"), bounds, { opacity: 1, pane: "radar" }), after: L.imageOverlay(source("after"), bounds, { opacity: 1, pane: "radar" }) };
  state.layers.water = L.geoJSON(run.layers.extent.features.filter((f) => f.properties.kind === "water"), {
    style: { color: "#39a6bc", weight: .7, fillColor: "#76d1dc", fillOpacity: .55 },
    onEachFeature: (f, layer) => layer.bindPopup(popup("Potential inundation", [["Area", `${(f.properties.area_m2 / 1e6).toFixed(3)} km²`], ["Evidence", "New radar / optical water change"], ["Confidence", f.properties.confidence]])),
  });
  state.layers.debris = L.geoJSON(run.layers.extent.features.filter((f) => f.properties.kind === "debris"), {
    style: { color: "#c4945d", weight: .7, fillColor: "#e3bb83", fillOpacity: .65 },
    onEachFeature: (f, layer) => layer.bindPopup(popup("Possible debris / land-cover change", [["Area", `${(f.properties.area_m2 / 1e6).toFixed(3)} km²`], ["Confidence", "Low; requires independent verification"]])),
  });
  state.layers.roads = L.geoJSON(run.layers.roads, {
    style: (f) => ({ color: f.properties.exposed ? "#D55E00" : f.properties.assessment_fraction < .99 ? "#747474" : "#009E73", weight: f.properties.highway === "primary" ? 2.5 : 1.8, opacity: .95, dashArray: f.properties.exposed ? "6 4" : f.properties.assessment_fraction < .99 ? "2 5" : null }),
    onEachFeature: (f, layer) => { layer.bindTooltip(f.properties.name || f.properties.id); layer.on("click", () => inspectFeature("roads", f.properties.id)); },
  });
  state.layers.buildings = L.geoJSON(run.layers.buildings.features.filter((f) => f.properties.exposed), {
    style: { color: "#ecaa77", fillColor: "#e8a065", fillOpacity: 1, weight: 1.5 },
    onEachFeature: (f, layer) => layer.on("click", () => inspectFeature("buildings", f.properties.id)),
  });
  state.layers.bridges = L.geoJSON(run.layers.bridges || {type: "FeatureCollection", features: []}, {
    style: (f) => ({color: f.properties.exposed ? "#D55E00" : "#747474", weight: 5, dashArray: "5 3"}),
    pointToLayer: (f, latlng) => L.circleMarker(latlng, {radius: 7, color: "#fff", weight: 2, fillColor: f.properties.exposed ? "#D55E00" : "#747474", fillOpacity: 1}),
    onEachFeature: (f, layer) => { layer.bindTooltip(`Bridge · ${f.properties.name || f.properties.id}`); layer.on("click", () => inspectFeature("bridges", f.properties.id)); },
  });
  const settlements = L.geoJSON(run.layers.settlements, {
    pointToLayer: (f, latlng) => L.circleMarker(latlng, { radius: 5, color: "#fff9ec", weight: 2, fillColor: ({cut_off: "#be7749", connected: "#416c3c", uncertain_access: "#b59a4d", unassessed: "#92998c", baseline_disconnected: "#92998c"})[f.properties.status] || "#92998c", fillOpacity: 1 }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      layer.bindTooltip(p.name, { permanent: run.mode === "demo" || p.status === "cut_off", direction: "right", offset: [6, 0], className: `settlement-label ${p.status === "cut_off" ? "cutoff-label" : ""}` });
      layer.on("click", () => inspectFeature("settlements", p.id));
    },
  });
  const destinations = L.geoJSON(run.layers.destinations, {
    pointToLayer: (f, latlng) => L.circleMarker(latlng, { radius: 6, color: "#f0f5e7", weight: 2, fillColor: "#274e35", fillOpacity: 1 }),
    onEachFeature: (f, layer) => {
      layer.bindTooltip(f.properties.name, { permanent: run.mode === "demo", direction: "left", offset: [-5, 0], className: "settlement-label" });
      layer.bindPopup(popup(f.properties.name, [["Destination", f.properties.amenity || f.properties.place], ["Availability", "Mapped facility; operating status unknown"]]));
    },
  });
  state.layers.settlements = L.layerGroup([settlements, destinations]);
  state.epoch = "after";
  updateLayers();
  fitSelectedMap();
  $("map-empty").hidden = true;
  $("before-button").classList.remove("active");
  $("after-button").classList.add("active");
}

function fitSelectedMap() {
  if (!state.run) return;
  const container = map.getContainer();
  if (!container.clientWidth || !container.clientHeight) { state.needsMapFit = true; return; }
  map.invalidateSize();
  map.fitBounds(boundsFor(state.run), { padding: [23, 28], animate: false });
  state.needsMapFit = false;
}

function updateLayers() {
  Object.entries(state.rasters).forEach(([epoch, layer]) => { if (epoch === state.epoch) layer.addTo(map); else map.removeLayer(layer); });
  document.querySelectorAll("[data-layer]").forEach((input) => {
    const layer = state.layers[input.dataset.layer];
    if (!layer) return;
    const postOnly = ["water", "debris", "buildings"].includes(input.dataset.layer);
    if (input.checked && (!postOnly || state.epoch === "after")) layer.addTo(map); else map.removeLayer(layer);
  });
  if (state.layers.roads) state.layers.roads.bringToFront();
  if (state.layers.buildings) state.layers.buildings.bringToFront();
  if (state.layers.bridges) state.layers.bridges.bringToFront();
  if (state.layers.settlements) state.layers.settlements.eachLayer((layer) => layer.bringToFront());
}

function renderRun(run) {
  state.run = run;
  state.history = [];
  try { localStorage.setItem("flood-lens-run", run.id); } catch { /* Results work without persistent browser storage. */ }
  const s = run.stats;
  $("stat-water").textContent = numberLabel(s.water_km2, 2);
  $("stat-buildings").textContent = numberLabel(s.buildings_exposed);
  $("stat-roads").textContent = numberLabel(s.road_exposed_km, 2);
  $("stat-cutoff").textContent = numberLabel(s.settlements_cut_off);
  $("map-badge").textContent = run.mode === "demo" ? "DEMO DATA" : run.analysis_status === "partial" ? "PARTIAL OBSERVATIONS" : "SATELLITE ESTIMATE";
  $("map-badge").classList.toggle("demo", run.mode === "demo");
  $("run-title").textContent = run.name;
  const mappingMethod = run.provenance.mapping?.algorithm || run.provenance.water_model?.algorithm || "Rule-based change detection";
  $("run-meta").textContent = `${run.acquisition_mode === "cached_reprocessing" ? "Cached real inputs · " : ""}${dateLabel(run.flood_date)} · ${mappingMethod}`;
  $("evidence-mode").textContent = run.mode === "demo" ? "Synthetic inputs" : "Actual observations";
  $("evidence-mode").classList.toggle("demo", run.mode === "demo");
  $("notice").className = `notice ${run.mode}`;
  const notice = run.mode === "demo" ? "<strong>Synthetic demonstration</strong> · All map features and results are generated test data. They do not describe the real Nepal flood." : "<strong>Satellite estimates</strong> · Values describe mapped observations only. Missing coverage and unknown access cannot establish absence of flooding or damage.";
  $("notice").innerHTML = `<span class="notice-icon" aria-hidden="true">i</span><span>${notice}</span>`;
  const availability = run.class_availability || {};
  $("observability").hidden = false;
  $("observability").textContent = `Water assessment: ${numberLabel(availability.water?.coverage_pct ?? s.water_eligible_coverage_pct, 1)}% of AOI. Debris assessment: ${availability.debris?.status === "unavailable" ? "unavailable — no eligible paired optical observations" : numberLabel(availability.debris?.coverage_pct ?? s.debris_eligible_coverage_pct, 1) + "% of AOI"}. ${numberLabel(s.settlements_access_uncertain)} settlements have uncertain access; ${numberLabel(s.settlements_unassessed)} are unassessed. A zero is limited to the assessed area.`;
  $("layer-availability").textContent = availability.debris?.status === "unavailable" ? "Debris is unavailable for this run. Water-only evidence cannot establish debris absence." : "Click mapped features for saved evidence. Road colors describe modeled exposure and observation coverage.";
  $("map-caption").textContent = `${run.mode === "demo" ? "Synthetic" : "Estimated"} · ${run.name}`;
  $("coverage-label").textContent = `${numberLabel(s.valid_coverage_pct, 1)}% eligible observations · ${numberLabel(s.water_eligible_coverage_pct ?? s.valid_coverage_pct, 1)}% water-eligible · ${run.provenance.resolution_m} m grid`;
  const scenes = run.provenance.radar_scenes;
  $("before-date").textContent = dateLabel(scenes[0].acquired);
  $("after-date").textContent = dateLabel(scenes[1].acquired);
  $("pair-label").textContent = `Relative orbit ${scenes[0].relative_orbit} · ${scenes[0].direction} · ${run.provenance.resolution_m} m`;
  const links = { "report-link": "report?language=en", "nepali-report": "report?language=ne", "export-geojson": "files/extent.geojson", "export-raster": "files/change-mask.tif", "export-audit": "files/result.json", "export-access": "exports/settlements.csv" };
  Object.entries(links).forEach(([id, suffix]) => { $(id).href = `${analysisBase()}/${suffix}`; $(id).setAttribute("aria-disabled", "false"); $(id).removeAttribute("tabindex"); });
  $("report-link").target = $("nepali-report").target = "_blank";
  $("access-summary").innerHTML = [[s.settlements_cut_off, "modeled cutoffs"], [s.settlements_access_uncertain, "uncertain access"], [s.settlements_unassessed, "unassessed"], [s.settlements_baseline_disconnected, "already disconnected"]].map(([count, label]) => `<span><strong>${count == null ? "—" : escapeHTML(count)}</strong>${label}</span>`).join("");
  renderSettlementTable();
  const rows = [
    ["Data mode", run.mode === "demo" ? "Synthetic test inputs; not real observations" : "Copernicus + pre-event OSM"],
    ["Before radar scene", scenes[0].id], ["After radar scene", scenes[1].id],
    ["Orbit compatibility", `${scenes[0].platform} · ${scenes[0].relative_orbit} · ${scenes[0].direction} · ${scenes[0].polarization}`],
    ["Historical OSM", `${run.provenance.osm_snapshot} · ${run.provenance.osm_source_api || "source recorded in snapshot"}`], ["Optical clear support", `${s.optical_clear_pct}% of grid; cloud/snow/shadow excluded`],
    ["Possible debris", Number.isFinite(s.debris_km2) ? `${s.debris_km2.toFixed(3)} km² within eligible coverage · low-confidence surface change` : "Unavailable · no eligible paired optical observations"],
    ["Scientific completeness", run.analysis_status || "Coverage must be reviewed"],
    ["Processing", run.provenance.radar_processing], ["Analysis projection", `${run.provenance.crs} · ${run.provenance.resolution_m} m`],
    ["Mapping method", mappingMethod],
    ["Radar / water / debris eligibility", `${numberLabel(s.radar_valid_coverage_pct ?? s.valid_coverage_pct,1)}% / ${numberLabel(s.water_eligible_coverage_pct ?? s.valid_coverage_pct,1)}% / ${numberLabel(s.debris_eligible_coverage_pct ?? s.optical_clear_pct,1)}%`],
    ["Uncertain road access", `${s.settlements_access_uncertain ?? "Not assessed"} settlements; unobserved segments are not confirmed intact`],
    ["AI component", "Situation-report copilot; optional Groq tool calling, validated claims and deterministic fallback"],
  ];
  $("provenance-content").innerHTML = rows.map(([k, v]) => `<div class="provenance-row"><span>${escapeHTML(k)}</span><strong>${escapeHTML(v)}</strong></div>`).join("") + run.warnings.map((w) => `<p class="field-note">${escapeHTML(w)}</p>`).join("");
  $("limitations-list").innerHTML = run.limitations.map((item) => `<li>${escapeHTML(item)}</li>`).join("");
  const score = (value) => Number.isFinite(value) ? value.toFixed(3) : "N/A";
  $("evaluation-status").textContent = run.evaluation.metrics ? `${run.mode === "demo" ? "Synthetic sanity check" : "Official EMSR927 comparison"}: IoU ${score(run.evaluation.metrics.iou)}, F1 ${score(run.evaluation.metrics.f1)}. ${run.evaluation.reason || ""} ${run.evaluation.primary_target || ""}. ${run.evaluation.limitations}` : run.evaluation.reason;
  $("conversation").replaceChildren();
  renderMap(run);
  renderSources();
  syncResultControls();
}

function setView(view) {
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active);
    button.tabIndex = active ? 0 : -1;
    $(button.dataset.view + "-view").hidden = !active;
  });
  $("workspace-link").classList.toggle("active", view !== "method");
  $("method-link").classList.toggle("active", view === "method");
  $("workspace-link").setAttribute("aria-current", view === "method" ? "false" : "page");
  $("method-link").setAttribute("aria-current", view === "method" ? "page" : "false");
  if (view === "map") setTimeout(() => { map.invalidateSize(); if (state.needsMapFit) fitSelectedMap(); }, 0);
}

async function runAnalysis() {
  if (state.busy) return;
  if (!$("analysis-form").reportValidity()) return;
  state.busy = true;
  syncResultControls();
  $("job-progress").hidden = false;
  $("form-error").hidden = true;
  $("progress-fill").style.width = "0%";
  $("progress-track").setAttribute("aria-valuenow", "0");
  const payload = { name: $("area-name").value, bbox: ["west", "south", "east", "north"].map((id) => Number($(id).value)), case_id: $("region").value, flood_date: $("event-date").value, mode: $("mode").value, satellite_provider: $("satellite-provider").value, resolution_m: Number($("resolution").value), change_threshold_db: Number($("change").value) };
  try {
    const job = await post("/api/analyses", payload);
    while (true) {
      const status = await api(`/api/analyses/${job.id}/status`);
      $("progress-label").textContent = status.message;
      $("progress-fill").style.width = `${status.progress}%`;
      $("progress-track").setAttribute("aria-valuenow", String(status.progress));
      const stages = [...document.querySelectorAll("[data-stage]")];
      const activeIndex = stages.findIndex((element) => element.dataset.stage === status.stage);
      stages.forEach((element, index) => { element.classList.toggle("done", status.status === "complete" || (activeIndex >= 0 && index < activeIndex)); element.classList.toggle("current", index === activeIndex); });
      if (["failed", "interrupted"].includes(status.status)) throw new Error(status.message);
      if (status.status === "complete") break;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    renderRun(await api(`/api/analyses/${job.id}?view=dashboard`));
    try { state.sources = await api("/api/data-sources"); renderSources(); } catch { /* Saved-run evidence remains available. */ }
    setView("map");
    $("map-tab").focus({preventScroll: true});
  } catch (error) {
    $("form-error").textContent = error.message;
    $("form-error").hidden = false;
    $("progress-label").textContent = "Analysis could not complete. Previous results remain labeled with their original sources.";
    if (!state.run) { $("map-empty").querySelector("h3").textContent = "Ready when you are"; $("map-empty").querySelector("p").textContent = "Choose an area and run an actual satellite analysis."; }
  } finally {
    state.busy = false;
    syncResultControls();
  }
}

async function ask(question) {
  if (!state.run || state.busy || state.asking) return;
  state.asking = true;
  syncResultControls();
  const queriedRun = state.run.id;
  const language = $("language").value;
  const container = $("conversation");
  const q = document.createElement("div"); q.className = "chat-question"; q.textContent = question; container.append(q);
  $("question").value = "";
  const loading = document.createElement("p"); loading.className = "chat-loading"; loading.setAttribute("role", "status"); loading.textContent = language === "ne" ? "प्रमाण जाँचिँदै…" : "Checking analysis evidence…"; container.append(loading);
  try {
    const response = await post(`/api/analyses/${queriedRun}/copilot`, { question, language, history: state.history });
    if (state.run.id !== queriedRun) return;
    if (response.run_id !== queriedRun || response.language !== language) throw new Error("The response does not match the selected analysis or language. Please retry.");
    if (response.facts.some((fact) => fact.run_id !== queriedRun) || response.evidence.some((item) => item.run_id !== queriedRun)) throw new Error("Evidence belongs to a different analysis. Please retry.");
    const text = document.createElement("div"); text.className = "chat-answer";
    text.classList.toggle("refusal", response.intent === "unsupported");
    appendCitedAnswer(text, response); container.append(text);
    appendMatchingLists(container, response, queriedRun, language);
    const status = document.createElement("p"); status.className = "generation-status";
    status.textContent = response.generation.mode === "llm" ? (language === "ne" ? "Groq · प्रमाणसँग जाँचिएका तथ्य" : "Groq · evidence-validated claims") : (language === "ne" ? "निश्चित वैकल्पिक उत्तर · सुरक्षित तथ्य" : "Deterministic fallback · evidence-backed answer"); container.append(status);
    if (response.facts.some((fact) => fact.status === "available")) {
      const verified = document.createElement("span"); verified.className = "verification-badge";
      verified.textContent = language === "ne" ? "✓ मानहरू सुरक्षित तथ्यसँग मिल्छन् · वास्तविक अवस्था पुष्टि भएको होइन" : "✓ Values match this saved run · physical accuracy unverified"; container.append(verified);
    }
    if (response.generation.reason && response.generation.mode !== "llm") status.textContent += ` · ${response.generation.reason.replaceAll("_", " ")}`;
    if (response.generation.retry_after_seconds != null) status.textContent += ` · ${language === "ne" ? "पुनः प्रयास समय" : "provider retry delay"}: ${response.generation.retry_after_seconds}s`;
    const evidence = document.createElement("details"); evidence.className = "evidence";
    const summary = document.createElement("summary"); summary.textContent = `${language === "ne" ? "प्रमाण" : "Supporting evidence"} · ${response.facts.length} · Run ${queriedRun.slice(0, 8)}`; evidence.append(summary);
    for (const fact of response.facts) {
      const item = document.createElement("dl");
      const heading = document.createElement("dt"); heading.textContent = `${language === "ne" ? fact.label_ne : fact.label_en} · ${fact.subject_name || fact.subject_id}`; item.append(heading);
      const value = document.createElement("dd"); value.textContent = fact.status === "available" ? `${JSON.stringify(fact.value)} ${fact.unit || ""}` : (language === "ne" ? "उपलब्ध छैन" : "Unavailable"); item.append(value);
      const source = document.createElement("dd"); source.textContent = `${fact.fact_id} · ${fact.path} · ${fact.source_ids.join(" · ")} · ${fact.method}`; item.append(source); evidence.append(item);
      const inspect = document.createElement("button"); inspect.type = "button"; inspect.className = "fact-link"; inspect.textContent = language === "ne" ? "नक्सामा प्रमाण हेर्नुहोस्" : "Inspect on map";
      inspect.addEventListener("click", () => inspectFact(fact)); item.append(inspect);
    }
    const credits = document.createElement("p"); credits.textContent = (response.attributions || []).join(" "); evidence.append(credits);
    container.append(evidence);
    appendReportLinks(container, response.report_links, queriedRun, language);
    if (response.report_error) { const note = document.createElement("p"); note.className = "error"; note.textContent = response.report_error; container.append(note); }
    state.history = [...state.history, {role: "user", content: question, run_id: queriedRun}, {role: "assistant", content: response.answer.slice(0, 2000), run_id: queriedRun}].slice(-6);
  } catch (error) {
    if (state.run.id !== queriedRun) return;
    const p = document.createElement("p"); p.className = "error"; p.textContent = error.message; container.append(p);
  } finally {
    loading.remove();
    state.asking = false;
    syncResultControls();
    container.scrollTop = container.scrollHeight;
  }
}

function appendMatchingLists(container, response, runId, language) {
  const facts = new Map(response.facts.map((fact) => [fact.fact_id, fact]));
  for (const list of response.lists || []) {
    const details = document.createElement("details"); details.className = "matching-list";
    const summary = document.createElement("summary");
    summary.textContent = `${language === "ne" ? "मिल्ने सूची" : "Matching list"} · ${list.returned_count}/${list.total_count ?? "—"} · ${list.layer}`;
    details.append(summary);
    const table = document.createElement("table"); table.className = "matching-list-table";
    const body = document.createElement("tbody");
    for (const row of list.rows) {
      const tr = document.createElement("tr");
      const name = document.createElement("td"), status = document.createElement("td");
      const button = document.createElement("button"); button.type = "button"; button.className = "fact-link"; button.textContent = row.name;
      button.addEventListener("click", () => inspectFeature(list.layer, row.feature_id)); name.append(button);
      status.textContent = row.status;
      for (const identifier of row.fact_ids) {
        const fact = facts.get(identifier);
        if (!fact || fact.run_id !== runId) throw new Error("List evidence does not match the selected analysis.");
      }
      tr.append(name, status); body.append(tr);
    }
    table.append(body); details.append(table);
    if (list.export_url.startsWith(`/api/analyses/${runId}/`)) {
      const link = document.createElement("a"); link.href = list.export_url;
      link.textContent = language === "ne" ? "पूर्ण तह निर्यात ↓" : "Full layer export ↓"; details.append(link);
    }
    container.append(details);
  }
}

function appendReportLinks(container, links, runId, language) {
  if (!links) return;
  const row = document.createElement("div"); row.className = "report-downloads";
  for (const format of ["html", "pdf", "json"]) {
    if (!links[format] || !links[format].startsWith(`/api/analyses/${runId}/`)) continue;
    const link = document.createElement("a"); link.href = links[format]; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = `${language === "ne" ? "प्रतिवेदन" : "Report"} ${format.toUpperCase()} ↗`; row.append(link);
  }
  container.append(row);
}

$("generate-report").addEventListener("click", async () => {
  if (!state.run || state.busy || state.asking) return;
  const runId = state.run.id, language = $("language").value;
  state.asking = true; syncResultControls();
  const note = document.createElement("p"); note.className = "chat-loading"; note.setAttribute("role", "status"); note.textContent = language === "ne" ? "प्रतिवेदन तयार हुँदै…" : "Preparing situation report…"; $("conversation").append(note);
  try {
    const response = await post(`/api/analyses/${runId}/report`, {language});
    if (state.run.id !== runId) return;
    if (response.run_id !== runId || response.language !== language) throw new Error("Report does not match the selected analysis.");
    const label = language === "ne" ? (response.status === "partial" ? "अपूर्ण प्रतिवेदन · नभएका तथ्य उपलब्ध छैनन्" : "प्रतिवेदन तयार भयो") : (response.status === "partial" ? "Incomplete report · missing facts marked unavailable" : "Report ready");
    note.textContent = `${label} · ${response.generation.mode === "llm" ? "Groq" : language === "ne" ? "निश्चित वैकल्पिक उत्तर" : "Deterministic fallback"}`; note.className = "generation-status";
    appendReportLinks($("conversation"), response.links, runId, language);
  } catch (error) { note.className = "error"; note.textContent = error.message; }
  finally { state.asking = false; syncResultControls(); }
});

$("analysis-form").addEventListener("submit", (event) => { event.preventDefault(); runAnalysis(); });
$("access-filter").addEventListener("change", renderSettlementTable);
$("settlement-search").addEventListener("input", renderSettlementTable);
$("close-feature-detail").addEventListener("click", () => { $("feature-detail").hidden = true; if (state.selection) { map.removeLayer(state.selection); state.selection = null; } });
$("copilot-form").addEventListener("submit", (event) => { event.preventDefault(); ask($("question").value); });
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => ask(button.dataset.question)));
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-layer]").forEach((input) => input.addEventListener("change", updateLayers));
document.querySelectorAll("[data-epoch]").forEach((button) => button.addEventListener("click", () => {
  state.epoch = button.dataset.epoch;
  document.querySelectorAll("[data-epoch]").forEach((b) => b.classList.toggle("active", b === button));
  updateLayers();
}));
$("fit-map").addEventListener("click", fitSelectedMap);
$("method-link").addEventListener("click", () => setView("method"));
$("workspace-link").addEventListener("click", () => setView("map"));
$("inspect-sources").addEventListener("click", () => { setView("method"); $("method-panel").scrollIntoView({block: "start"}); });
document.querySelectorAll("[data-view]").forEach((button, index, buttons) => {
  button.addEventListener("keydown", (event) => {
    let target;
    if (event.key === "ArrowRight") target = (index + 1) % buttons.length;
    if (event.key === "ArrowLeft") target = (index + buttons.length - 1) % buttons.length;
    if (event.key === "Home") target = 0;
    if (event.key === "End") target = buttons.length - 1;
    if (target == null) return;
    event.preventDefault(); setView(buttons[target].dataset.view); buttons[target].focus();
  });
});
$("preview-demo").addEventListener("click", () => {
  $("mode").value = "demo"; $("resolution").value = "100";
  syncSetup(); runAnalysis();
});
$("change").addEventListener("input", () => { $("change-value").textContent = `${$("change").value} dB`; });
$("mode").addEventListener("change", syncSetup);
$("satellite-provider").addEventListener("change", syncSetup);
$("region").addEventListener("change", () => {
  const preset = state.sources?.presets?.[$("region").value];
  if (preset) {
    ["west", "south", "east", "north"].forEach((id, i) => { $(id).value = preset.bbox[i]; });
    $("area-name").value = preset.name; $("event-date").value = preset.flood_date;
  } else { $("advanced").open = true; $("area-name").value = "Custom analysis area"; $("west").focus(); }
});
$("trace-toggle").addEventListener("click", () => {
  if (!state.run) return;
  state.tracing = !state.tracing;
  $("trace-toggle").classList.toggle("active", state.tracing);
  $("trace-result").hidden = !state.tracing;
  $("trace-result").textContent = "Click an upstream point on the map. The DEM trace follows downhill terrain; it is not a flood simulation.";
});
map.on("click", async (event) => {
  if (!state.tracing || !state.run) return;
  state.tracing = false;
  $("trace-toggle").classList.remove("active");
  $("trace-result").textContent = "Tracing downhill terrain…";
  try {
    const path = await post(`${analysisBase()}/trace`, { longitude: event.latlng.lng, latitude: event.latlng.lat });
    if (state.trace) map.removeLayer(state.trace);
    state.trace = L.geoJSON(path, { style: { color: "#e5f3a8", weight: 3, dashArray: "6 4" } }).addTo(map);
    const villages = path.properties.settlements.map((s) => s.name).join(", ") || "none within the corridor buffer";
    $("trace-result").textContent = `DEM path: ${path.properties.length_km} km. Nearby settlements: ${villages}. Stops at ${path.properties.stopped_reason}. Terrain screening only.`;
  } catch (error) { $("trace-result").textContent = error.message; }
});

async function initialize() {
  syncResultControls();
  try {
    state.sources = await api("/api/data-sources");
    const preset = state.sources.presets?.[$("region").value];
    const configuration = state.sources.mapping_configuration;
    if (preset) {
      ["west", "south", "east", "north"].forEach((id, i) => { $(id).value = preset.bbox[i]; });
      $("area-name").value = preset.name; $("event-date").value = preset.flood_date;
      map.fitBounds([[preset.bbox[1], preset.bbox[0]], [preset.bbox[3], preset.bbox[2]]]);
    }
    if (configuration) {
      const changeBounds = state.sources.parameter_schema?.properties?.change_threshold_db;
      if (changeBounds) { $("change").min = changeBounds.minimum; $("change").max = changeBounds.maximum; }
      $("resolution").value = configuration.resolution_m;
      $("change").value = configuration.change_threshold_db;
      $("change-value").textContent = `${configuration.change_threshold_db} dB`;
    }
    if (state.sources.workspace?.preferred_satellite_provider === "copernicus") $("satellite-provider").value = "copernicus";
    renderSources();
  } catch (error) {
    $("dataset-status").textContent = `Source status unavailable: ${error.message}`;
    $("source-inventory").textContent = "Source details could not be loaded. Check the local server and retry.";
    $("workspace-data-status").textContent = "Source status unavailable";
  }
  let saved;
  const requested = new URLSearchParams(location.search).get("run");
  if (/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(requested || "")) saved = requested;
  else { try { saved = localStorage.getItem("flood-lens-run"); } catch { saved = null; } }
  try {
    let run;
    if (saved) {
      try { run = await api(`/api/analyses/${saved}?view=dashboard`); }
      catch { try { localStorage.removeItem("flood-lens-run"); } catch {} }
    }
    if ((!run || run.mode === "demo") && (state.sources?.workspace?.completed_actual_runs || !state.sources)) {
      try { run = await api("/api/analyses/latest-real?view=dashboard"); }
      catch (error) { if (!run) throw error; }
    }
    if (!run) return;
    $("event-date").value = run.flood_date; $("mode").value = run.mode; $("area-name").value = run.name;
    const legacyPreset = Object.values(state.sources?.presets || {}).find((preset) => preset.flood_date === run.flood_date && preset.bbox.every((value, index) => value === run.bbox[index]));
    $("region").value = run.parameters.case_id || legacyPreset?.id || "custom";
    $("satellite-provider").value = run.parameters.satellite_provider || "public";
    ["west", "south", "east", "north"].forEach((id, i) => { $(id).value = run.bbox[i]; });
    $("resolution").value = run.parameters.resolution_m;
    $("change").value = run.parameters.change_threshold_db;
    $("change-value").textContent = `${run.parameters.change_threshold_db} dB`;
    renderRun(run);
  } catch {
    $("map-empty").querySelector("p").textContent = "Set an area and flood date, or preview the synthetic demonstration.";
  } finally {
    syncResultControls();
  }
}
initialize();
