"use strict";

const $ = (id) => document.getElementById(id);
const state = { run: null, sources: null, epoch: "after", layers: {}, rasters: {}, tracing: false, busy: false, asking: false, trace: null };
const map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true, minZoom: 3, maxZoom: 19, zoomSnap: 0.25 }).setView([28.10, 85.30], 10);
map.createPane("radar");
map.getPane("radar").style.zIndex = 250;
map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>');
map.attributionControl.addAttribution("© OpenStreetMap contributors · Contains modified Copernicus Sentinel data 2026");
L.control.scale({ position: "bottomright", imperial: false }).addTo(map);

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(Array.isArray(detail) ? detail.map((e) => e.msg).join("; ") : detail || `Request failed (${response.status})`);
  }
  return data;
}

const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const dateLabel = (value) => new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
const analysisBase = () => `/api/analyses/${state.run.id}`;

function boundsFor(run) {
  const [west, south, east, north] = run.display_bbox || run.bbox;
  return [[south, west], [north, east]];
}

function popup(title, pairs) {
  return `<strong>${escapeHTML(title)}</strong><br>${pairs.map(([key, value]) => `${escapeHTML(key)}: ${escapeHTML(value)}`).join("<br>")}`;
}

function sourceUsage(source) {
  if (source.category === "training_reference") return ["Not loaded", ""];
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
  const ready = data.water_model.status === "ready";
  $("workspace-data-status").textContent = state.run ? (state.run.mode === "demo" ? "Synthetic demonstration loaded" : "Actual observations loaded") : (actualRuns ? `${actualRuns} saved actual-data ${actualRuns === 1 ? "run" : "runs"}` : "No actual observations acquired");
  $("model-availability").textContent = ready ? "External model available" : "External model not connected";
  const externalOption = $("water-model").querySelector('[value="external"]');
  externalOption.disabled = !ready;
  externalOption.textContent = ready ? "External model" : "External model · not connected";
  $("dataset-status").innerHTML = inputs.map((source) => {
    const [label] = sourceUsage(source);
    return `<div class="source-summary-row"><strong>${escapeHTML(source.name)}</strong><span>${escapeHTML(label)}</span></div>`;
  }).join("") + '<p>No training datasets are loaded by this application. Your model is supplied separately.</p>';
  const groups = [
    ["input", "Live analysis inputs", "Fetched when needed"],
    ["training_reference", "External training references", "Outside this application"],
    ["evaluation_only", "Independent reference maps", "Never prediction inputs"],
  ];
  $("source-inventory").innerHTML = groups.map(([category, title, subtitle]) => {
    const cards = data.sources.filter((source) => source.category === category).map((source) => {
      const [label, kind] = sourceUsage(source);
      return `<article class="source-card"><div class="source-card-header"><h4><a href="${escapeHTML(source.url)}" target="_blank" rel="noreferrer">${escapeHTML(source.name)} ↗</a></h4><span class="tag ${kind}">${escapeHTML(label)}</span></div><p>${escapeHTML(source.description || source.role)}</p><small>${escapeHTML(source.license)}</small></article>`;
    }).join("");
    const note = category === "training_reference" ? '<p class="source-group-note">Kuro Siwo and Sen1Floods11 are links for external training. Their images and labels are not read here. A connected model can declare its actual training sources in its metadata.</p>' : "";
    return `<section class="source-group"><div class="source-group-title"><h4>${title}</h4><span>${subtitle}</span></div><div class="source-list">${cards}</div>${note}</section>`;
  }).join("") + `<article class="source-card"><div class="source-card-header"><h4>External water model</h4><span class="tag ${ready ? "active" : ""}">${ready ? "Available" : "Not connected"}</span></div><p>${ready ? escapeHTML(data.water_model.algorithm) : "The current app uses a threshold baseline. Add your model later to enable external inference."}</p>${data.water_model.message ? `<p>${escapeHTML(data.water_model.message)}</p>` : ""}<small><a href="/api/water-model" target="_blank" rel="noreferrer">Inspect model metadata and setup ↗</a></small></article>`;
  syncSetup();
}

function syncSetup() {
  const demo = $("mode").value === "demo";
  const ready = state.sources?.water_model?.status === "ready";
  document.querySelectorAll("#analysis-form input, #analysis-form select").forEach((control) => { control.disabled = state.busy; });
  $("satellite-provider").disabled = demo || state.busy;
  $("water-model").disabled = demo || state.busy;
  if (demo) $("water-model").value = "threshold";
  $("credential-note").hidden = demo;
  $("mode-note").textContent = demo ? "Generated test fixtures. No satellite data is downloaded." : "Observations are fetched when you run an analysis.";
  $("model-note").textContent = demo ? "Synthetic mode uses generated inputs and baseline detection. The external model is not called." : ($("water-model").value === "external" && ready ? "Your connected model supplies water probabilities. Review its input requirements and validation." : "The baseline works without model weights. Your external model can be connected later.");
  $("credential-note").textContent = $("satellite-provider").value === "copernicus" ? "This provider requires your Copernicus account credentials. Historical OSM is verified separately." : "Public access requires no satellite account. Historical OSM availability is checked separately.";
}

function syncResultControls() {
  const available = Boolean(state.run) && !state.busy;
  document.querySelectorAll("[data-layer], [data-epoch], #fit-map, #trace-toggle").forEach((control) => { control.disabled = !available; });
  document.querySelectorAll("[data-question], #question, #ask-button").forEach((control) => { control.disabled = !available || state.asking; });
  $("run-button").disabled = state.busy;
  $("preview-demo").disabled = state.busy;
  $("map-legend").hidden = !state.run;
  $("copilot-status").textContent = state.run ? "English and Nepali answers use the facts in this saved analysis." : "Available after analysis. English and Nepali answers use saved map facts.";
  syncSetup();
}

function renderMap(run) {
  Object.values(state.layers).forEach((layer) => map.removeLayer(layer));
  Object.values(state.rasters).forEach((layer) => map.removeLayer(layer));
  if (state.trace) { map.removeLayer(state.trace); state.trace = null; }
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
    style: (f) => ({ color: f.properties.exposed ? "#e9ad6c" : "#dae0c7", weight: f.properties.highway === "primary" ? 2.5 : 1.8, opacity: .95, dashArray: f.properties.exposed ? "4 3" : null }),
    onEachFeature: (f, layer) => layer.bindPopup(popup(f.properties.name, [["Exposure", f.properties.exposed ? "Intersects mapped change" : "No mapped intersection"], ["Exposed length", `${(f.properties.exposed_length_m / 1000).toFixed(3)} km`], ["Closure", "Assumption for access analysis; unverified"]])),
  });
  state.layers.buildings = L.geoJSON(run.layers.buildings.features.filter((f) => f.properties.exposed), {
    style: { color: "#ecaa77", fillColor: "#e8a065", fillOpacity: 1, weight: 1.5 },
    onEachFeature: (f, layer) => layer.bindPopup(popup(f.properties.name, [["Status", "Potentially exposed building"], ["Footprint overlap", `${(f.properties.overlap_fraction * 100).toFixed(1)}%`], ["Damage", "Not confirmed"]])),
  });
  const settlements = L.geoJSON(run.layers.settlements, {
    pointToLayer: (f, latlng) => L.circleMarker(latlng, { radius: 5, color: "#fff9ec", weight: 2, fillColor: ({cut_off: "#be7749", connected: "#416c3c", uncertain_access: "#b59a4d", unassessed: "#92998c", baseline_disconnected: "#92998c"})[f.properties.status] || "#92998c", fillOpacity: 1 }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      layer.bindTooltip(p.name, { permanent: run.mode === "demo" || p.status === "cut_off", direction: "right", offset: [6, 0], className: `settlement-label ${p.status === "cut_off" ? "cutoff-label" : ""}` });
      layer.bindPopup(popup(p.name, [["Road access", p.status.replaceAll("_", " ")], ["Pre-event destination", p.nearest_destination || "Not mapped"], ["Before", p.before_distance_km == null ? "Unknown" : `${p.before_distance_km} km`], ["After", p.after_distance_km == null ? "No assessed connection" : `${p.after_distance_km} km`]]));
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
  map.fitBounds(bounds, { padding: [23, 28], animate: false });
  $("map-empty").hidden = true;
  $("before-button").classList.remove("active");
  $("after-button").classList.add("active");
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
  if (state.layers.settlements) state.layers.settlements.eachLayer((layer) => layer.bringToFront());
}

function renderRun(run) {
  state.run = run;
  try { localStorage.setItem("flood-lens-run", run.id); } catch { /* Results work without persistent browser storage. */ }
  const s = run.stats;
  $("stat-water").textContent = s.water_km2.toFixed(2);
  $("stat-buildings").textContent = s.buildings_exposed.toLocaleString();
  $("stat-roads").textContent = s.road_exposed_km.toFixed(2);
  $("stat-cutoff").textContent = s.settlements_cut_off;
  $("map-badge").textContent = run.mode === "demo" ? "DEMO DATA" : "SATELLITE ESTIMATE";
  $("map-badge").classList.toggle("demo", run.mode === "demo");
  $("run-title").textContent = run.name;
  $("run-meta").textContent = `${dateLabel(run.flood_date)} · ${run.provenance.water_model?.algorithm || "Threshold baseline"}`;
  $("evidence-mode").textContent = run.mode === "demo" ? "Synthetic inputs" : "Actual observations";
  $("evidence-mode").classList.toggle("demo", run.mode === "demo");
  $("notice").className = `notice ${run.mode}`;
  const notice = run.mode === "demo" ? "<strong>Synthetic demonstration</strong> · All map features and results are generated test data. They do not describe the real Nepal flood." : "<strong>Satellite estimates</strong> · Infrastructure exposure and road interruptions require field verification.";
  $("notice").innerHTML = `<span class="notice-icon" aria-hidden="true">i</span><span>${notice}</span>`;
  $("map-caption").textContent = `${run.mode === "demo" ? "Synthetic" : "Estimated"} · ${run.name}`;
  $("coverage-label").textContent = `${s.valid_coverage_pct}% eligible observations · ${s.water_eligible_coverage_pct ?? s.valid_coverage_pct}% water-eligible · ${run.provenance.resolution_m} m grid`;
  const scenes = run.provenance.radar_scenes;
  $("before-date").textContent = dateLabel(scenes[0].acquired);
  $("after-date").textContent = dateLabel(scenes[1].acquired);
  $("pair-label").textContent = `Relative orbit ${scenes[0].relative_orbit} · ${scenes[0].direction} · ${run.provenance.resolution_m} m`;
  const links = { "report-link": "report?language=en", "nepali-report": "report?language=ne", "export-geojson": "files/extent.geojson", "export-raster": "files/change-mask.tif", "export-audit": "files/result.json" };
  Object.entries(links).forEach(([id, suffix]) => { $(id).href = `${analysisBase()}/${suffix}`; $(id).setAttribute("aria-disabled", "false"); $(id).removeAttribute("tabindex"); });
  $("report-link").target = $("nepali-report").target = "_blank";
  const names = { cut_off: "Modeled cutoff", connected: "Observed route available", uncertain_access: "Uncertain · unobserved route", unassessed: "Unassessed", baseline_disconnected: "Pre-event disconnected" };
  $("access-summary").innerHTML = [[s.settlements_cut_off, "modeled cutoffs"], [s.settlements_access_uncertain, "uncertain access"], [s.settlements_unassessed, "unassessed"], [s.settlements_baseline_disconnected, "already disconnected"]].map(([count, label]) => `<span><strong>${count == null ? "—" : escapeHTML(count)}</strong>${label}</span>`).join("");
  $("settlement-table").innerHTML = run.layers.settlements.features.map((f) => {
    const p = f.properties;
    const distances = `${p.before_distance_km == null ? "—" : p.before_distance_km + " km"} / ${p.after_distance_km == null ? "—" : p.after_distance_km + " km"}`;
    return `<tr><td>${escapeHTML(p.name)}</td><td><span class="access-status ${p.status}">${names[p.status]}</span></td><td>${escapeHTML(p.nearest_destination || "Not mapped")}</td><td>${escapeHTML(distances)}</td></tr>`;
  }).join("") || '<tr><td colspan="4">No settlements in this historical snapshot.</td></tr>';
  const rows = [
    ["Data mode", run.mode === "demo" ? "Synthetic test inputs; not real observations" : "Copernicus + pre-event OSM"],
    ["Before radar scene", scenes[0].id], ["After radar scene", scenes[1].id],
    ["Orbit compatibility", `${scenes[0].platform} · ${scenes[0].relative_orbit} · ${scenes[0].direction} · ${scenes[0].polarization}`],
    ["Historical OSM", `${run.provenance.osm_snapshot} · ${run.provenance.osm_source_api || "source recorded in snapshot"}`], ["Optical clear support", `${s.optical_clear_pct}% of grid; cloud/snow/shadow excluded`],
    ["Possible debris", `${s.debris_km2.toFixed(3)} km² · low-confidence change candidate`],
    ["Processing", run.provenance.radar_processing], ["Analysis projection", `${run.provenance.crs} · ${run.provenance.resolution_m} m`],
    ["Water model", run.provenance.water_model?.algorithm || "Threshold baseline"],
    ["Training datasets", (run.provenance.water_model?.training_datasets || []).join(" + ") || "None for this run"],
    ["Radar / water / debris eligibility", `${s.radar_valid_coverage_pct ?? s.valid_coverage_pct}% / ${s.water_eligible_coverage_pct ?? s.valid_coverage_pct}% / ${s.debris_eligible_coverage_pct ?? s.optical_clear_pct}%`],
    ["Uncertain road access", `${s.settlements_access_uncertain ?? "Not assessed"} settlements; unobserved segments are not confirmed intact`],
    ["Copilot", "Local bilingual intent classifier; all facts rendered from saved analysis"],
  ];
  $("provenance-content").innerHTML = rows.map(([k, v]) => `<div class="provenance-row"><span>${escapeHTML(k)}</span><strong>${escapeHTML(v)}</strong></div>`).join("") + run.warnings.map((w) => `<p class="field-note">${escapeHTML(w)}</p>`).join("");
  $("limitations-list").innerHTML = run.limitations.map((item) => `<li>${escapeHTML(item)}</li>`).join("");
  const score = (value) => Number.isFinite(value) ? value.toFixed(3) : "undefined";
  $("evaluation-status").textContent = run.evaluation.metrics ? `${run.mode === "demo" ? "Synthetic sanity check" : "Official EMSR927 comparison"}: IoU ${score(run.evaluation.metrics.iou)}, F1 ${score(run.evaluation.metrics.f1)}. ${run.evaluation.limitations}` : run.evaluation.reason;
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
  if (view === "map") setTimeout(() => map.invalidateSize(), 0);
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
  const payload = { name: $("area-name").value, bbox: ["west", "south", "east", "north"].map((id) => Number($(id).value)), flood_date: $("event-date").value, mode: $("mode").value, satellite_provider: $("satellite-provider").value, water_model: $("water-model").value, resolution_m: Number($("resolution").value), change_threshold_db: Number($("change").value) };
  try {
    const job = await post("/api/analyses", payload);
    while (true) {
      const status = await api(`/api/analyses/${job.id}/status`);
      $("progress-label").textContent = status.message;
      $("progress-fill").style.width = `${status.progress}%`;
      $("progress-track").setAttribute("aria-valuenow", String(status.progress));
      if (status.status === "failed") throw new Error(status.message);
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
  const container = $("conversation");
  const q = document.createElement("div"); q.className = "chat-question"; q.textContent = question; container.append(q);
  $("question").value = "";
  try {
    const response = await post(`${analysisBase()}/copilot`, { question, language: $("language").value });
    if (state.run.id !== queriedRun) return;
    const text = document.createElement("div"); text.className = "chat-answer"; text.textContent = response.answer; container.append(text);
    const evidence = document.createElement("div"); evidence.className = "evidence";
    evidence.textContent = response.evidence.length ? `Evidence: ${response.evidence.map((e) => e.path).join(" · ")} · Run ${queriedRun.slice(0, 8)}` : "No estimate supplied: this question is outside the mapped evidence.";
    container.append(evidence);
  } catch (error) {
    if (state.run.id !== queriedRun) return;
    const p = document.createElement("p"); p.className = "error"; p.textContent = error.message; container.append(p);
  } finally {
    state.asking = false;
    syncResultControls();
    container.scrollTop = container.scrollHeight;
  }
}

$("analysis-form").addEventListener("submit", (event) => { event.preventDefault(); runAnalysis(); });
$("copilot-form").addEventListener("submit", (event) => { event.preventDefault(); ask($("question").value); });
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => ask(button.dataset.question)));
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-layer]").forEach((input) => input.addEventListener("change", updateLayers));
document.querySelectorAll("[data-epoch]").forEach((button) => button.addEventListener("click", () => {
  state.epoch = button.dataset.epoch;
  document.querySelectorAll("[data-epoch]").forEach((b) => b.classList.toggle("active", b === button));
  updateLayers();
}));
$("fit-map").addEventListener("click", () => { if (state.run) map.fitBounds(boundsFor(state.run), { padding: [23, 28], animate: false }); });
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
$("water-model").addEventListener("change", syncSetup);
$("region").addEventListener("change", () => {
  if ($("region").value === "trishuli") {
    ["west", "south", "east", "north"].forEach((id, i) => { $(id).value = [85.19, 27.90, 85.41, 28.30][i]; });
    $("area-name").value = "Trishuli river corridor";
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
    renderSources();
  } catch (error) {
    $("dataset-status").textContent = `Source status unavailable: ${error.message}`;
    $("source-inventory").textContent = "Source details could not be loaded. Check the local server and retry.";
    $("workspace-data-status").textContent = "Source status unavailable";
    $("model-availability").textContent = "External model status unknown";
  }
  let saved;
  try { saved = localStorage.getItem("flood-lens-run"); } catch { saved = null; }
  try {
    let run;
    if (saved) {
      try { run = await api(`/api/analyses/${saved}?view=dashboard`); }
      catch { try { localStorage.removeItem("flood-lens-run"); } catch {} }
    }
    if (!run && (state.sources?.workspace?.completed_actual_runs || !state.sources)) {
      run = await api("/api/analyses/latest-real?view=dashboard");
    }
    if (!run) return;
    $("event-date").value = run.flood_date; $("mode").value = run.mode; $("area-name").value = run.name;
    $("satellite-provider").value = run.parameters.satellite_provider || "public";
    const selectedModel = run.parameters.water_model === "trained" ? "external" : run.parameters.water_model || "threshold";
    const option = $("water-model").querySelector(`[value="${selectedModel === "external" ? "external" : "threshold"}"]`);
    $("water-model").value = option.disabled ? "threshold" : option.value;
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
