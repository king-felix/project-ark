/* PROJECT ARK — the page that turns a downloaded pack into a working map.
 *
 * The shape of the thing:
 *
 *   1. while the Ark is reachable, the catalogue lists regions and a download
 *      writes every component into IndexedDB, one MiB at a time, checking each
 *      file's fingerprint before it counts;
 *   2. once a pack is installed the page loads `graph.bin`, `places.json` and the
 *      drawing layers *from this device* — never from the Ark;
 *   3. the router runs in a worker over the graph, the search runs over the
 *      pack's own place list, and the canvas draws the layers.  No request is
 *      needed to pan to a place never seen, search a new name, or calculate a
 *      new route;
 *   4. the service worker keeps the application shell so the page itself opens
 *      with the Ark and the internet both gone.
 *
 * "Ready offline" is only shown when a pack is installed *and* the page is being
 * served by the service worker.  Before that, the page says what is still
 * missing rather than promising something it cannot keep.
 */

import * as store from "./store.js";
import { Sha256 } from "./sha256.js";
import { SearchIndex, parseCoordinates, describePlace, fold } from "./search.js";
import { decodeGraph, Router, formatDistance, formatDuration } from "./router.js";
import { MapView, escapeXml } from "./map.js";

const APP_VERSION = 1;
const CHUNK = store.CHUNK_BYTES;
const BASE_LAYERS = ["forest", "water", "boundary", "base-minor", "base-major"];
const ALL_FILES = ["manifest.json", "graph.json", "graph.bin", "places.json",
  "attribution.txt", "forest.geojson", "water.geojson", "boundary.geojson",
  "buildings.geojson", "base-minor.geojson", "base-major.geojson"];

const $ = (id) => document.getElementById(id);

const state = {
  online: null,
  catalog: null,
  installed: [],
  activeKey: "",
  packKey: "",
  pack: null,
  manifest: null,
  worker: null,
  router: null,
  search: null,
  map: null,
  places: [],
  graphMeta: null,
  units: "metric",
  endpoints: { from: null, to: null },
  pickTarget: "",
  saved: [],
  route: null,
  download: null,
  layers: new Set(),
  loadedGraphics: new Set(),
  viewTimer: null,
};

// ---------------------------------------------------------------------------
//  small helpers
// ---------------------------------------------------------------------------

function text(tag, value, className = "") {
  const node = document.createElement(tag);
  if (value !== undefined && value !== null) node.textContent = String(value);
  if (className) node.className = className;
  return node;
}

function button(label, onClick, className = "") {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  if (className) node.className = className;
  node.addEventListener("click", onClick);
  return node;
}

function setStatus(node, message, kind = "") {
  if (!node) return;
  node.textContent = message || "";
  node.className = `status ${kind}`.trim();
}

function humanBytes(n) {
  return store.humanBytes(n);
}

function isoDay(value) {
  const textValue = String(value || "");
  return textValue.length >= 10 ? textValue.slice(0, 10) : textValue;
}

function packLabel(manifest) {
  return manifest && manifest.region ? manifest.region.name : "this region";
}

function profileLabel(profile) {
  return { foot: "On foot", bike: "Bicycle", car: "Car" }[profile] || profile;
}

function currentProfile() {
  const chosen = document.querySelector('input[name="profile"]:checked');
  return chosen ? chosen.value : "car";
}

function apiPath(path) {
  return `api/${path}`;
}

async function fetchJSON(url, { timeout = 8000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`the Ark answered ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

function bytesText(bytes) {
  return new TextDecoder().decode(bytes);
}

function parseJSONBytes(bytes) {
  return JSON.parse(bytesText(bytes));
}

// ---------------------------------------------------------------------------
//  boot
// ---------------------------------------------------------------------------

async function boot() {
  bindEvents();
  try {
    await store.openDB();
  } catch (error) {
    fatal(error.message || "This browser would not open its storage.");
    return;
  }
  state.units = (await store.localGet("units", "metric")) || "metric";
  $("units").value = state.units;
  const saved = await store.localGet("saved", []);
  state.saved = Array.isArray(saved) ? saved : [];
  state.activeKey = (await store.activePackKey()) || "";
  await refreshInstalled();
  await refreshCatalog();
  renderAll();
  registerServiceWorker();

  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  const wanted = hash.get("pack");
  if (wanted && state.installed.some((row) => row.key === wanted && row.state === "installed")) {
    openPack(wanted);
  } else if (state.activeKey
    && state.installed.some((row) => row.key === state.activeKey && row.state === "installed")) {
    // A reader who opens Maps came back to the map, not to a shop window.  The
    // catalogue is one tap away under "Packs", and the map works whether or not
    // the Ark happens to be reachable at this moment.
    openPack(state.activeKey);
  }
}

async function refreshInstalled() {
  const rows = await store.listPacks();
  state.installed = rows;
  state.activeKey = (await store.activePackKey()) || state.activeKey;
}

async function refreshCatalog() {
  try {
    const data = await fetchJSON(apiPath("catalog"), { timeout: 6000 });
    state.catalog = data;
    state.online = true;
    await store.localPut("catalog", { at: new Date().toISOString(), data });
  } catch (_) {
    state.online = false;
    const cached = await store.localGet("catalog", null);
    if (cached && cached.data) state.catalog = cached.data;
  }
}

function fatal(message) {
  const main = $("main");
  if (!main) return;
  main.replaceChildren();
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.append(text("h2", "Maps cannot open on this page"));
  panel.append(text("p", message));
  panel.append(text("p", "If this is a private or restricted browser window, its storage may be blocked. Open the page in an ordinary tab and try again."));
  main.append(panel);
}

// ---------------------------------------------------------------------------
//  rendering the catalogue
// ---------------------------------------------------------------------------

function renderAll() {
  renderNetNote();
  renderStorage();
  renderCatalog();
  renderInstalled();
  renderSaved();
}

function renderNetNote() {
  const note = $("net-note");
  if (!note) return;
  if (state.online === true) {
    note.textContent = "The Ark is reachable. A pack you download now will keep working after you leave.";
    note.className = "note";
  } else if (state.online === false) {
    note.textContent = state.installed.some((row) => row.state === "installed")
      ? "The Ark is not reachable. The packs on this device still work: open one below."
      : "The Ark is not reachable, and no pack is installed on this device yet. Reconnect to the Ark to download a region.";
    note.className = "note warn";
  } else {
    note.textContent = "Looking for the Ark…";
    note.className = "note";
  }
}

async function renderStorage() {
  const line = $("storage-line");
  if (!line) return;
  const estimate = await store.storageEstimate();
  const keep = await store.persisted();
  const parts = [];
  if (estimate && estimate.quota) {
    parts.push(`${humanBytes(Math.max(0, estimate.quota - estimate.usage))} free for this page`);
    parts.push(`${humanBytes(estimate.usage)} already used`);
  } else {
    parts.push("This browser does not report its storage");
  }
  if (keep === true) parts.push("the browser has been asked not to remove it");
  else if (keep === false) parts.push("the browser may remove this copy when space is low");
  line.textContent = parts.join(" · ");
}

function findInstalled(id) {
  return state.installed
    .filter((row) => row.id === id && row.state === "installed")
    .sort((a, b) => String(b.version).localeCompare(String(a.version)))[0] || null;
}

function renderCatalog() {
  const list = $("catalog-list");
  if (!list) return;
  list.replaceChildren();
  const packs = state.catalog && Array.isArray(state.catalog.packs) ? state.catalog.packs : [];
  if (!packs.length) {
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.append(text("p", state.online === false
      ? "The catalogue cannot be reached, and no pack has been installed on this device yet."
      : "No map regions are ready on the Ark yet. The owner prepares a region on a computer and adds it from the Ark's owner pages."));
    list.append(panel);
    return;
  }
  for (const entry of packs) {
    list.append(catalogCard(entry));
  }
}

function catalogCard(entry) {
  const card = document.createElement("article");
  card.className = "card";
  const heading = document.createElement("h3");
  heading.append(text("span", entry.name || entry.id));
  heading.append(text("span", `version ${entry.version}`, "where"));
  card.append(heading);
  const where = document.createElement("p");
  where.className = "where";
  const bounds = Array.isArray(entry.bounds) ? entry.bounds : [];
  where.textContent = [
    entry.code ? `Region ${entry.code}` : "Regional pack",
    bounds.length === 4 ? `covers ${bounds[0].toFixed(2)}…${bounds[2].toFixed(2)} N, ${bounds[1].toFixed(2)}…${bounds[3].toFixed(2)} E` : "",
    entry.data_date ? `map data from ${isoDay(entry.data_date)}` : "",
  ].filter(Boolean).join(" · ");
  card.append(where);

  const facts = document.createElement("div");
  facts.className = "facts-line";
  const modes = entry.capabilities && Array.isArray(entry.capabilities.routing)
    ? entry.capabilities.routing.map(profileLabel) : [];
  facts.append(text("span", `${humanBytes(entry.bytes)} download`));
  facts.append(text("span", `${Number((entry.search || {}).count || 0)} searchable places`));
  if (modes.length) facts.append(text("span", modes.join(" · ")));
  if (entry.capabilities && entry.capabilities.turn_restrictions) {
    facts.append(text("span", "turn restrictions carried"));
  }
  if ((entry.search || {}).addresses === false) facts.append(text("span", "no street addresses"));
  card.append(facts);

  if (Array.isArray(entry.problems) && entry.problems.length) {
    const bad = document.createElement("p");
    bad.className = "status bad";
    bad.textContent = `This pack is not offered: ${entry.problems[0]}`;
    card.append(bad);
    return card;
  }

  const installed = findInstalled(entry.id);
  const same = installed && installed.version === entry.version;
  const actions = document.createElement("div");
  actions.className = "actions";
  if (same) {
    actions.append(text("span", "Installed on this device", "installed"));
    actions.append(button("Open the map", () => openPack(installed.key), "primary"));
  } else {
    if (installed) {
      actions.append(button("Open the installed version", () => openPack(installed.key), "ghost"));
      actions.append(button(`Update to ${entry.version}`, () => startDownload(entry, { update: true }), "primary"));
    } else {
      actions.append(button("Download for offline use", () => startDownload(entry), "primary"));
    }
  }
  card.append(actions);

  const more = document.createElement("details");
  more.className = "panel";
  more.append(text("summary", "What is in this pack"));
  const facts2 = document.createElement("div");
  facts2.className = "facts";
  const layerCounts = (entry.map || {}).counts || {};
  const useful = Object.entries(layerCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([name, count]) => `${name.replace("base-", "")} ${count}`);
  const rows = [
    ["Map detail", useful.length ? useful.join(", ") : "not described"],
    ["Zoom range", entry.map ? `${entry.map.min_zoom} to ${entry.map.max_zoom}` : "not described"],
    ["Search", `${Number((entry.search || {}).count || 0)} named places and points of interest`],
    ["Licence", entry.licence || "not stated"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(text("b", `${label}: `));
    row.append(text("span", value));
    facts2.append(row);
  }
  if (entry.limitations) {
    const row = document.createElement("div");
    row.append(text("b", "Limits: "));
    row.append(text("span", entry.limitations));
    facts2.append(row);
  }
  more.append(facts2);
  card.append(more);
  return card;
}

function renderInstalled() {
  const list = $("installed-list");
  if (!list) return;
  list.replaceChildren();
  const rows = state.installed
    .slice()
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
  if (!rows.length) {
    list.append(text("p", "Nothing is installed on this device yet.", "fine"));
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "card";
    const title = row.manifest ? packLabel(row.manifest) : row.id;
    const heading = document.createElement("h3");
    heading.append(text("span", title));
    heading.append(text("span", row.version, "where"));
    card.append(heading);
    const stateWords = {
      installed: "Ready on this device",
      downloading: "Download was interrupted",
      paused: "Download paused",
      failed: "This copy did not finish",
      verifying: "Being checked",
    }[row.state] || row.state;
    const size = row.manifest ? humanBytes(row.manifest.total_bytes) : "";
    card.append(text("p", `${stateWords}${size ? ` · ${size}` : ""}`, "fine"));
    const actions = document.createElement("div");
    actions.className = "actions";
    if (row.state === "installed") {
      actions.append(button("Open", () => openPack(row.key), "primary"));
      actions.append(button("Remove from this device", () => removePack(row.key), "danger"));
    } else {
      actions.append(button("Continue download", () => resumeDownload(row), "primary"));
      actions.append(button("Remove the partial copy", () => removePack(row.key), "danger"));
    }
    card.append(actions);
    list.append(card);
  }
}

// ---------------------------------------------------------------------------
//  downloading
// ---------------------------------------------------------------------------

async function startDownload(entry, { update = false } = {}) {
  if (state.download) {
    setStatus($("install-status"), "A download is already running. Let it finish, or pause it first.", "bad");
    return;
  }
  setStatus($("install-status"), "Reading the pack's description…");
  let manifest;
  try {
    manifest = await fetchJSON(apiPath(`packs/${encodeURIComponent(entry.id)}/${encodeURIComponent(entry.version)}/manifest.json`));
  } catch (error) {
    setStatus($("install-status"), `The Ark could not describe this pack: ${error.message}`, "bad");
    return;
  }
  const problems = manifestProblems(manifest);
  if (problems.length) {
    setStatus($("install-status"), `This pack cannot be installed: ${problems[0]}`, "bad");
    return;
  }
  const estimate = await store.storageEstimate();
  const needed = Number(manifest.total_bytes) || 0;
  if (estimate && estimate.quota && (estimate.quota - estimate.usage) < needed + 8 * 1024 * 1024) {
    setStatus($("install-status"),
      `This device reports ${humanBytes(Math.max(0, estimate.quota - estimate.usage))} free for this page, and the pack needs ${humanBytes(needed)}. Remove something from this device or use a device with more room.`,
      "bad");
    return;
  }
  const keep = await store.requestPersistence();
  if (keep === false) {
    setStatus($("install-status"),
      "The browser would not promise to keep this copy. It will work, but the browser may remove it if space runs low; the Ark can give you another copy.",
      "warn");
  }
  const key = store.packKey(manifest.id, manifest.version);
  const record = {
    key,
    id: manifest.id,
    version: manifest.version,
    manifest,
    state: "downloading",
    createdAt: new Date().toISOString(),
    bytes: needed,
  };
  await store.putPack(record);
  await refreshInstalled();
  const controller = new AbortController();
  state.download = { key, controller, paused: false, update, manifest };
  showDownload(manifest, key);
  try {
    await downloadPackFiles(manifest, key, controller.signal);
    const finished = await store.getPack(key);
    finished.state = "installed";
    finished.installedAt = new Date().toISOString();
    await store.putPack(finished);
    await store.setActivePackKey(key);
    state.activeKey = key;
    await refreshInstalled();
    state.download = null;
    renderAll();
    $("download-view").hidden = true;
    $("packs-view").hidden = false;
    setStatus($("install-status"), `${packLabel(manifest)} is ready offline on this device.`, "good");
    if (!state.packKey || update) {
      await openPack(key);
    } else {
      renderInstalled();
      renderCatalog();
    }
  } catch (error) {
    const failed = await store.getPack(key);
    if (failed) {
      failed.state = error && error.name === "AbortError" ? "paused" : "failed";
      failed.error = String((error && error.message) || error);
      await store.putPack(failed);
    }
    await refreshInstalled();
    state.download = null;
    if (error && error.name === "AbortError") {
      setStatus($("install-status"), "Download paused. The pieces already received are on this device; continue when you are ready.", "warn");
    } else {
      setStatus($("install-status"), `The download did not finish: ${(error && error.message) || error}`, "bad");
    }
    $("download-view").hidden = true;
    $("packs-view").hidden = false;
    renderAll();
  }
}

function manifestProblems(manifest) {
  const problems = [];
  if (!manifest || typeof manifest !== "object") return ["the description is not an object"];
  if (manifest.format !== "ark-map-pack") problems.push("this is not an Ark map pack");
  if (manifest.schema !== 2) problems.push(`the pack format is ${manifest.schema}, and this page reads 2`);
  if (!manifest.id || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(manifest.id)) problems.push("the pack has no safe identifier");
  if (!manifest.version || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(manifest.version)) problems.push("the pack has no safe version");
  if (!manifest.app || !Number.isInteger(manifest.app.min) || !Number.isInteger(manifest.app.max)) {
    problems.push("the pack does not say which page versions it works with");
  } else if (APP_VERSION < manifest.app.min || APP_VERSION > manifest.app.max) {
    problems.push(`this pack needs page version ${manifest.app.min} to ${manifest.app.max}, and this page is version ${APP_VERSION}`);
  }
  if (!manifest.engine || manifest.engine.graph !== 2) problems.push("this pack was built for a different routing engine");
  if (!Array.isArray(manifest.files) || !manifest.files.length) problems.push("the pack lists no files");
  else {
    const names = new Set();
    for (const row of manifest.files) {
      if (!Array.isArray(row) || row.length !== 3) { problems.push("a file entry is unreadable"); continue; }
      const name = String(row[0]);
      if (!ALL_FILES.includes(name)) problems.push(`${name} is not a file this page knows`);
      if (names.has(name)) problems.push(`${name} is listed twice`);
      names.add(name);
      if (!Number.isInteger(row[1]) || row[1] < 0) problems.push(`${name} has no valid size`);
      if (!/^[0-9a-f]{64}$/.test(String(row[2]))) problems.push(`${name} has no valid fingerprint`);
    }
    for (const required of ["graph.bin", "graph.json", "places.json", "attribution.txt",
      "base-major.geojson", "base-minor.geojson"]) {
      if (!names.has(required)) problems.push(`the pack is missing ${required}`);
    }
  }
  if (!Number.isInteger(manifest.total_bytes) || manifest.total_bytes <= 0) problems.push("the pack does not say how large it is");
  if (!Array.isArray((manifest.coverage || {}).bounds) || manifest.coverage.bounds.length !== 4) {
    problems.push("the pack does not say what it covers");
  }
  return problems;
}

function showDownload(manifest, key) {
  $("packs-view").hidden = true;
  $("map-view").hidden = true;
  $("download-view").hidden = false;
  $("download-name").textContent = `${packLabel(manifest)} · ${humanBytes(manifest.total_bytes)}`;
  $("download-bar").value = 0;
  setStatus($("download-detail"), "Starting…");
  $("pause-download").disabled = false;
  $("cancel-download").disabled = false;
  window.scrollTo({ top: 0, behavior: "auto" });
}

async function downloadPackFiles(manifest, key, signal) {
  const files = manifest.files.slice().sort((a, b) => a[0].localeCompare(b[0]));
  const total = Number(manifest.total_bytes) || files.reduce((sum, row) => sum + Number(row[1] || 0), 0);
  let completed = 0;
  for (const row of files) {
    const [name, size, sha] = row;
    await downloadOne(key, manifest, name, Number(size), String(sha), signal, (done) => {
      const fraction = total ? Math.min(1, (completed + done) / total) : 0;
      const bar = $("download-bar");
      bar.value = Math.round(fraction * 100);
      setStatus($("download-detail"),
        `${name} · ${humanBytes(completed + done)} of ${humanBytes(total)} (${Math.round(fraction * 100)}%)`);
    });
    completed += Number(size);
  }
  const bar = $("download-bar");
  bar.value = 100;
  setStatus($("download-detail"), "Every part arrived. Checking the fingerprints…");
}

async function downloadOne(key, manifest, name, size, sha, signal, onProgress) {
  const url = apiPath(`packs/${encodeURIComponent(manifest.id)}/${encodeURIComponent(manifest.version)}/${encodeURIComponent(name)}`);
  const chunks = Math.ceil(size / CHUNK);
  const hash = new Sha256();
  let have = 0;
  // Rebuild the hash from what a previous attempt already stored, and stop at
  // the first hole: chunks are written in order, so a hole means the rest is
  // missing too.
  for (let index = 0; index < chunks; index++) {
    const bytes = await store.getChunk(key, name, index);
    if (!bytes) break;
    const view = new Uint8Array(bytes);
    const expected = index === chunks - 1 ? size - index * CHUNK : CHUNK;
    if (view.byteLength !== expected) break;
    hash.update(view);
    have = index + 1;
    onProgress(have * CHUNK);
  }
  try {
    for (let index = have; index < chunks; index++) {
      const start = index * CHUNK;
      const end = Math.min(size, start + CHUNK) - 1;
      const slice = await fetchRange(url, start, end, signal);
      if (slice.byteLength !== end - start + 1) {
        throw new Error(`${name} arrived with ${slice.byteLength} bytes where ${end - start + 1} were expected`);
      }
      await store.putChunk(key, name, index, slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength));
      hash.update(slice);
      onProgress(Math.min(size, (index + 1) * CHUNK));
    }
  } catch (error) {
    if (!error || error.code !== "no-range") throw error;
    // This path does not honour a range request.  Start this file again as one
    // stream: keeping the pieces already stored would mean hashing the same
    // bytes twice, which is a wrong fingerprint for a file that is fine.
    await store.deleteChunks(key, name);
    await store.deleteFileRecord(key, name);
    const fresh = new Sha256();
    await streamWholeFile(url, key, name, size, signal, fresh, onProgress);
    if (fresh.hex() !== sha) {
      await store.deleteChunks(key, name);
      await store.deleteFileRecord(key, name);
      throw new Error(`${name} did not match its fingerprint; the partial file was removed, and a fresh download should fix it`);
    }
    await store.putFileRecord({
      key: store.fileKey(key, name), packKey: key, file: name,
      size, chunks, sha256: sha, checkedAt: new Date().toISOString(),
    });
    return;
  }
  if (hash.hex() !== sha) {
    await store.deleteChunks(key, name);
    await store.deleteFileRecord(key, name);
    throw new Error(`${name} did not match its fingerprint; the partial file was removed, and a fresh download should fix it`);
  }
  await store.putFileRecord({
    key: store.fileKey(key, name), packKey: key, file: name,
    size, chunks, sha256: sha, checkedAt: new Date().toISOString(),
  });
}

async function fetchRange(url, start, end, signal) {
  const response = await fetch(url, {
    signal,
    cache: "no-store",
    headers: { Range: `bytes=${start}-${end}` },
  });
  if (response.status === 206) {
    return new Uint8Array(await response.arrayBuffer());
  }
  if (response.status === 200) {
    // The Ark ignored the range.  Do not read the whole body here: signal the
    // caller, which restarts this file as one stream and hashes it once.
    try { if (response.body) await response.body.cancel(); } catch (_) { /* already gone */ }
    const error = new Error("this path does not support range requests");
    error.code = "no-range";
    throw error;
  }
  if (response.status === 416) {
    throw new Error(`the Ark refused the range ${start}-${end}`);
  }
  throw new Error(`the Ark answered ${response.status}`);
}

async function streamWholeFile(url, key, name, size, signal, hash, onProgress) {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`the Ark answered ${response.status}`);
  let index = 0;
  let received = 0;
  if (!response.body) {
    const whole = new Uint8Array(await response.arrayBuffer());
    if (whole.byteLength !== size) throw new Error(`${name} is ${whole.byteLength} bytes and should be ${size}`);
    for (let at = 0; at < whole.byteLength; at += CHUNK) {
      const piece = whole.slice(at, Math.min(whole.byteLength, at + CHUNK));
      await store.putChunk(key, name, index, piece.buffer.slice(piece.byteOffset, piece.byteOffset + piece.byteLength));
      hash.update(piece);
      index += 1;
      onProgress(Math.min(size, index * CHUNK));
    }
    return;
  }
  const reader = response.body.getReader();
  let pending = new Uint8Array(0);
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const merged = new Uint8Array(pending.length + value.length);
    merged.set(pending, 0);
    merged.set(value, pending.length);
    pending = merged;
    while (pending.length >= CHUNK) {
      const piece = pending.slice(0, CHUNK);
      pending = pending.slice(CHUNK);
      await store.putChunk(key, name, index, piece.buffer.slice(piece.byteOffset, piece.byteOffset + piece.byteLength));
      hash.update(piece);
      index += 1;
      received += piece.length;
      onProgress(received);
    }
  }
  if (pending.length) {
    await store.putChunk(key, name, index, pending.buffer.slice(pending.byteOffset, pending.byteOffset + pending.byteLength));
    hash.update(pending);
    index += 1;
    received += pending.length;
  }
  if (received !== size) throw new Error(`${name} is ${received} bytes and should be ${size}`);
  onProgress(size);
}

async function resumeDownload(row) {
  if (!row.manifest) {
    setStatus($("install-status"), "This copy has no description to resume from. Remove it and download the pack again.", "bad");
    return;
  }
  const entry = {
    id: row.id,
    version: row.version,
  };
  await startDownload(entry);
}

// ---------------------------------------------------------------------------
//  opening an installed pack
// ---------------------------------------------------------------------------

async function openPack(key) {
  const record = await store.getPack(key);
  if (!record || record.state !== "installed" || !record.manifest) {
    setStatus($("install-status"), "That pack is not installed on this device. Download it again from the catalogue.", "bad");
    return;
  }
  const manifest = record.manifest;
  const problems = manifestProblems(manifest);
  if (problems.length) {
    setStatus($("install-status"), `This copy cannot be opened: ${problems[0]}`, "bad");
    return;
  }
  try {
    setBusy("Opening the map…");
    const graphBytes = await store.readFile(key, "graph.bin");
    const graphMeta = parseJSONBytes(await store.readFile(key, "graph.json"));
    const placesBody = parseJSONBytes(await store.readFile(key, "places.json"));
    const places = Array.isArray(placesBody.places) ? placesBody.places : [];
    const layers = {};
    for (const name of BASE_LAYERS) {
      const body = parseJSONBytes(await store.readFile(key, `${name}.geojson`));
      layers[name] = body;
    }
    state.packKey = key;
    state.pack = record;
    state.manifest = manifest;
    state.places = places;
    state.graphMeta = graphMeta;
    state.search = new SearchIndex(places, { kinds: (manifest.search || {}).kinds || [] });
    state.layers = new Set(BASE_LAYERS);
    state.loadedGraphics = new Set(BASE_LAYERS);
    await loadRouter(graphBytes, graphMeta);
    showMap();
    const bounds = (manifest.coverage || {}).bounds
      || (manifest.region || {}).bounds || [0, 0, 0, 0];
    state.map.setPack({
      bounds, layers, places,
      minZoom: (manifest.map || {}).min_zoom ?? 6,
      maxZoom: (manifest.map || {}).max_zoom ?? 17,
    });
    state.map.setUnits(state.units);
    const savedView = await store.localGet(`view:${key}`, null);
    if (savedView && Number.isFinite(savedView.lat)) {
      state.map.goTo(savedView.lat, savedView.lon, savedView.zoom);
    }
    renderPackFacts();
    renderSaved();
    $("pack-caption").textContent = `${packLabel(manifest)} · ${humanBytes(manifest.total_bytes)} on this device`;
    $("map-heading").textContent = packLabel(manifest);
    $("offline-badge").hidden = !offlineReady();
    if (!offlineReady()) {
      setStatus($("route-status"), "This map is installed. Reopen the page once so the page itself is kept offline; then it will open with no Ark and no internet.", "warn");
    } else {
      setStatus($("route-status"), "");
    }
    history.replaceState(null, "", `#pack=${encodeURIComponent(key)}`);
    clearRoute();
    setBusy("");
  } catch (error) {
    setBusy("");
    setStatus($("install-status"),
      `This copy could not be opened: ${(error && error.message) || error}. Remove it and download it again.`, "bad");
  }
}

function offlineReady() {
  return Boolean(navigator.serviceWorker && navigator.serviceWorker.controller);
}

async function loadRouter(graphBytes, graphMeta) {
  if (state.worker) {
    state.worker.terminate();
    state.worker = null;
  }
  state.router = null;
  if (window.Worker) {
    try {
      const worker = new Worker("router-worker.js", { type: "module" });
      const ready = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("the router took too long to start")), 12000);
        worker.addEventListener("message", (event) => {
          const message = event.data || {};
          if (message.type === "ready") {
            clearTimeout(timer);
            resolve(true);
          } else if (message.type === "load-error") {
            clearTimeout(timer);
            reject(new Error(message.message || "the router refused the graph"));
          } else if (message.type === "route") {
            const pending = workerRoutes.get(message.id);
            if (pending) {
              workerRoutes.delete(message.id);
              pending.resolve(message.result);
            }
          } else if (message.type === "route-error") {
            const pending = workerRoutes.get(message.id);
            if (pending) {
              workerRoutes.delete(message.id);
              pending.reject(new Error(message.message || "the route could not be calculated"));
            }
          }
        });
        worker.addEventListener("error", (event) => {
          clearTimeout(timer);
          reject(new Error(event.message || "the router could not start"));
        });
      });
      worker.postMessage({ type: "load", buffer: graphBytes, meta: graphMeta });
      await ready;
      state.worker = worker;
      return;
    } catch (_) {
      if (state.worker) { state.worker.terminate(); state.worker = null; }
    }
  }
  // The worker is the polite way to keep the page responsive.  It is not the
  // only way: a browser that refuses module workers still gets a working router
  // on the main thread, a little slower, rather than a page that does nothing.
  const graph = decodeGraph(graphBytes.buffer.slice(graphBytes.byteOffset, graphBytes.byteOffset + graphBytes.byteLength), graphMeta);
  state.router = new Router(graph, graphMeta);
}

const workerRoutes = new Map();
let routeSeq = 0;

function routeInWorker(from, to, profile) {
  if (!state.worker) return null;
  const id = ++routeSeq;
  return new Promise((resolve, reject) => {
    workerRoutes.set(id, { resolve, reject });
    state.worker.postMessage({ type: "route", id, from, to, profile });
  });
}

function showMap() {
  $("packs-view").hidden = true;
  $("download-view").hidden = true;
  $("map-view").hidden = false;
  if (!state.map) {
    state.map = new MapView($("map"), {
      onPick: onMapPick,
      onView: onMapView,
    });
    const observer = new ResizeObserver(() => state.map.resize());
    observer.observe($("map").parentElement);
  }
  requestAnimationFrame(() => state.map.resize());
  window.scrollTo({ top: 0, behavior: "auto" });
}

function setBusy(message) {
  const node = $("install-status");
  if (node) node.textContent = message || "";
}

function renderPackFacts() {
  const box = $("pack-facts");
  if (!box) return;
  box.replaceChildren();
  const manifest = state.manifest;
  const rows = [
    ["Region", packLabel(manifest)],
    ["Version", manifest.version],
    ["Map data", isoDay((manifest.source || {}).data_date)],
    ["Built", isoDay(manifest.built)],
    ["Size on this device", humanBytes((manifest.total_bytes || 0))],
    ["Search", `${Number((manifest.search || {}).count || 0)} places`],
    ["Travel", (manifest.capabilities.routing || []).map(profileLabel).join(", ")],
    ["Turn restrictions", manifest.capabilities.turn_restrictions ? "carried" : "none in this region"],
    ["Licence", manifest.licence || "not stated"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(text("b", `${label}: `));
    row.append(text("span", value));
    box.append(row);
  }
  if ((manifest.coverage || {}).limitations) {
    const row = document.createElement("div");
    row.append(text("b", "Limits: "));
    row.append(text("span", manifest.coverage.limitations));
    box.append(row);
  }
  const source = manifest.source || {};
  if (source.url) {
    const row = document.createElement("div");
    row.append(text("b", "Source: "));
    row.append(text("span", source.url));
    box.append(row);
  }
}

async function loadGraphicsLayer(name) {
  if (state.loadedGraphics.has(name)) return;
  state.loadedGraphics.add(name);
  try {
    const body = parseJSONBytes(await store.readFile(state.packKey, `${name}.geojson`));
    state.map.addLayer(name, body);
    state.layers.add(name);
  } catch (_) {
    // A pack without building footprints still draws.  The manifest already
    // says whether the layer is empty; nothing here needs to raise.
  }
}

// ---------------------------------------------------------------------------
//  map events
// ---------------------------------------------------------------------------

function onMapView(view) {
  if (!state.map) return;
  $("map-live").textContent = `Map centred on ${view.lat.toFixed(4)}, ${view.lon.toFixed(4)} at zoom ${view.zoom}.`;
  const note = $("coverage-note");
  if (!view.inside) {
    note.hidden = false;
    note.textContent = "This point is outside the downloaded region. Move back inside the dashed line to see the map.";
  } else {
    note.hidden = true;
  }
  if (view.zoom >= 13) loadGraphicsLayer("buildings");
  if (state.viewTimer) clearTimeout(state.viewTimer);
  state.viewTimer = setTimeout(() => {
    if (state.packKey) store.localPut(`view:${state.packKey}`, { lat: view.lat, lon: view.lon, zoom: view.zoom });
  }, 600);
}

function onMapPick(point) {
  if (!state.pickTarget) {
    setStatus($("route-status"), "Tap “Pick on map” beside the start or the destination first.", "");
    return;
  }
  setEndpoint(state.pickTarget, { lat: point.lat, lon: point.lon, name: "Point on map" });
  setStatus($("route-status"), "", "");
}

function setEndpoint(target, place) {
  state.endpoints[target] = place ? { lat: place.lat, lon: place.lon, name: place.name || "" } : null;
  const input = $(target === "from" ? "from-input" : "to-input");
  input.value = place ? (place.name || `${place.lat.toFixed(5)}, ${place.lon.toFixed(5)}`) : "";
  state.pickTarget = "";
  $("pick-hint").hidden = true;
  $(target === "from" ? "from-suggestions" : "to-suggestions").replaceChildren();
}

// ---------------------------------------------------------------------------
//  search
// ---------------------------------------------------------------------------

function searchFor(target, query) {
  const list = $(target === "from" ? "from-suggestions" : "to-suggestions");
  list.replaceChildren();
  if (!query || fold(query).length < 1 || !state.search) return;
  const coords = parseCoordinates(query);
  if (coords) {
    const result = { name: `${coords.lat.toFixed(5)}, ${coords.lon.toFixed(5)}`, kind: "coordinates", lat: coords.lat, lon: coords.lon, label: "Coordinates" };
    list.append(suggestionButton(result, target));
    return;
  }
  const found = state.search.search(query, { limit: 10, near: state.map ? state.map.describe() : null });
  if (found.reason === "empty-index") {
    list.append(text("p", "This pack carries no searchable names. You can still choose points on the map.", "fine"));
    return;
  }
  if (found.reason === "no-match") {
    list.append(text("p", "No match in this region. Check the spelling, or choose the point on the map.", "fine"));
    return;
  }
  for (const place of found.results) list.append(suggestionButton(place, target));
}

function suggestionButton(place, target) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "suggestion";
  node.append(text("span", place.name));
  node.append(text("span", ` — ${place.label || place.kind || "Place"}`, "kind"));
  node.addEventListener("click", () => {
    setEndpoint(target, place);
    if (state.map) state.map.goTo(place.lat, place.lon, Math.max(13, state.map.zoom));
    if (target === "to") $("route-button").focus();
  });
  return node;
}

// ---------------------------------------------------------------------------
//  routing
// ---------------------------------------------------------------------------

async function calculateRoute() {
  setStatus($("route-status"), "");
  const from = state.endpoints.from;
  const to = state.endpoints.to;
  if (!from || !to) {
    setStatus($("route-status"), "Choose a start and a destination first — search for a name, or tap “Pick on map”.", "bad");
    return;
  }
  const profile = currentProfile();
  setStatus($("route-status"), "Calculating on this device…");
  try {
    let result = await routeInWorker(from, to, profile);
    if (!result) {
      if (!state.router) {
        const graphBytes = await store.readFile(state.packKey, "graph.bin");
        const graph = decodeGraph(graphBytes.buffer.slice(graphBytes.byteOffset, graphBytes.byteOffset + graphBytes.byteLength), state.graphMeta);
        state.router = new Router(graph, state.graphMeta);
      }
      result = state.router.route(from, to, profile);
    }
    if (!result || !result.ok) {
      setStatus($("route-status"), (result && result.message) || "No route could be calculated.", "bad");
      return;
    }
    state.route = result;
    renderRoute(result);
    state.map.setRoute(result);
  } catch (error) {
    setStatus($("route-status"), `The route could not be calculated: ${(error && error.message) || error}`, "bad");
  }
}

function renderRoute(result) {
  const panel = $("route-panel");
  panel.hidden = false;
  $("route-summary").textContent = `${formatDistance(result.distance, state.units)} · about ${formatDuration(result.seconds)}`;
  const notes = ["Calculated on this device from the downloaded pack. Times use road-class speeds, not live traffic, closures or weather."];
  if (result.snap && (result.snap.from > 35 || result.snap.to > 35)) {
    notes.push(`A point was moved to the nearest road: start ${formatDistance(result.snap.from, state.units)}, destination ${formatDistance(result.snap.to, state.units)}.`);
  }
  if (result.profile === "foot") notes.push("On foot the route ignores one-way rules for cars, but it can only use roads and paths in this pack.");
  $("route-note").textContent = notes.join(" ");
  setStatus($("route-status"), `Route ready · ${profileLabel(result.profile)}`, "good");
  const list = $("directions");
  list.replaceChildren();
  for (const step of result.steps) {
    const item = document.createElement("li");
    if (step.kind === "arrive") item.className = "arrive";
    item.append(text("span", step.instruction));
    if (step.distance > 0) item.append(text("span", formatDistance(step.distance, state.units), "dist"));
    list.append(item);
  }
}

function clearRoute() {
  state.route = null;
  $("route-panel").hidden = true;
  $("directions").replaceChildren();
  $("route-summary").textContent = "";
  $("route-note").textContent = "";
  if (state.map) state.map.clearRoute();
  setStatus($("route-status"), "");
}

// ---------------------------------------------------------------------------
//  exports
// ---------------------------------------------------------------------------

function sheetHTML(result) {
  const manifest = state.manifest || {};
  const fromName = (state.endpoints.from && state.endpoints.from.name) || "Start";
  const toName = (state.endpoints.to && state.endpoints.to.name) || "Destination";
  const svg = state.map.svgFor(result.bounds || (manifest.coverage || {}).bounds,
    { route: result, title: `${fromName} to ${toName}` });
  const steps = result.steps.map((step) => {
    const distance = step.distance > 0 ? ` — ${formatDistance(step.distance, state.units)}` : "";
    return `<li>${escapeXml(step.instruction)}${escapeXml(distance)}</li>`;
  }).join("");
  const limitations = (manifest.coverage || {}).limitations || "";
  return `<h1>Route sheet — ${escapeXml(fromName)} to ${escapeXml(toName)}</h1>
<p class="sheet-meta">${escapeXml(packLabel(manifest))} · map data ${escapeXml(isoDay((manifest.source || {}).data_date))} · ${escapeXml(profileLabel(result.profile))} · ${escapeXml(formatDistance(result.distance, state.units))} · about ${escapeXml(formatDuration(result.seconds))}</p>
<div class="sheet-map">${svg}</div>
<h2>Directions</h2>
<ol>${steps}</ol>
<h2>What this route is based on</h2>
<p>Calculated on this device from the downloaded map pack, with no network. Times use road-class speeds, not live traffic, closures or weather. ${escapeXml(limitations)}</p>
<p class="sheet-foot">Map data © OpenStreetMap contributors, available under the Open Database Licence (ODbL 1.0). ${escapeXml(manifest.licence || "")}</p>
<p class="sheet-foot">Made by Project Ark. Keep this sheet with you; it does not need a battery.</p>`;
}

function printableSheet() {
  if (!state.route) return;
  const sheet = $("print-sheet");
  sheet.innerHTML = sheetHTML(state.route);
  sheet.setAttribute("aria-hidden", "false");
  const cleanup = () => sheet.setAttribute("aria-hidden", "true");
  window.addEventListener("afterprint", cleanup, { once: true });
  window.print();
  setTimeout(cleanup, 2000);
}

function downloadSheet() {
  if (!state.route) return;
  const html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Route sheet — Project Ark</title>
<style>body{font:12pt/1.4 Georgia,serif;max-width:820px;margin:20px auto;padding:0 16px;color:#111}
h1{font-size:20pt;margin:0 0 4px}h2{font-size:14pt;margin:18px 0 6px}
.sheet-meta{color:#444;font-size:9pt}.sheet-map svg{width:100%;height:auto;border:1px solid #999}
ol{padding-left:20px}li{margin:5px 0}.sheet-foot{font-size:8pt;color:#555;border-top:1px solid #bbb;padding-top:6px}
@media print{body{margin:0}}</style></head><body>${sheetHTML(state.route)}</body></html>`;
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${(state.manifest || {}).id || "ark"}-route-${new Date().toISOString().slice(0, 10)}.html`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 5000);
}

function downloadGPX() {
  if (!state.route) return;
  const name = `${(state.endpoints.from || {}).name || "Start"} to ${(state.endpoints.to || {}).name || "Destination"}`;
  const points = state.route.geometry.map((point) => `<trkpt lat="${point.lat.toFixed(7)}" lon="${point.lon.toFixed(7)}"></trkpt>`).join("\n");
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Project Ark" xmlns="http://www.topografix.com/GPX/1/1">
<metadata><name>${escapeXml(name)}</name><desc>${escapeXml(packLabel(state.manifest || {}))}; calculated on the device without a network.</desc><time>${new Date().toISOString()}</time></metadata>
<trk><name>${escapeXml(name)}</name><trkseg>
${points}
</trkseg></trk></gpx>`;
  const blob = new Blob([gpx], { type: "application/gpx+xml" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${(state.manifest || {}).id || "ark"}-route-${new Date().toISOString().slice(0, 10)}.gpx`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 5000);
}

// ---------------------------------------------------------------------------
//  saved places, removal, checking
// ---------------------------------------------------------------------------

function renderSaved() {
  const list = $("saved-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.saved.length) {
    list.append(text("p", "No saved places yet.", "fine"));
    return;
  }
  state.saved.forEach((place, index) => {
    const row = document.createElement("div");
    row.className = "saved-row";
    row.append(text("span", place.name, "name"));
    row.append(button("Start", () => { setEndpoint("from", place); state.map.goTo(place.lat, place.lon, 14); }, "ghost"));
    row.append(button("Destination", () => { setEndpoint("to", place); state.map.goTo(place.lat, place.lon, 14); }, "ghost"));
    row.append(button("Remove", async () => {
      state.saved.splice(index, 1);
      await store.localPut("saved", state.saved);
      renderSaved();
    }, "ghost"));
    list.append(row);
  });
}

async function clearLocal() {
  const answer = window.confirm(
    "Remove the saved places and the last map view from this device? "
    + "Downloaded regions are not removed.");
  if (!answer) return;
  state.saved = [];
  await store.localPut("saved", []);
  if (state.packKey) await store.localDelete(`view:${state.packKey}`);
  renderSaved();
  setStatus($("route-status"),
    "Saved places and the last view were removed from this device. Downloaded regions were not removed.",
    "good");
}

async function saveDestination() {
  const place = state.endpoints.to;
  if (!place) {
    setStatus($("route-status"), "Choose a destination first, then save it.", "bad");
    return;
  }
  const name = place.name || `${place.lat.toFixed(4)}, ${place.lon.toFixed(4)}`;
  if (state.saved.some((row) => Math.abs(row.lat - place.lat) < 1e-6 && Math.abs(row.lon - place.lon) < 1e-6)) {
    setStatus($("route-status"), "That place is already saved on this device.", "");
    return;
  }
  state.saved.push({ name, lat: place.lat, lon: place.lon });
  await store.localPut("saved", state.saved);
  renderSaved();
  setStatus($("route-status"), `Saved “${name}” on this device only.`, "good");
}

async function removePack(key) {
  const record = await store.getPack(key);
  const label = record && record.manifest ? packLabel(record.manifest) : "this pack";
  if (!window.confirm(`Remove ${label} from this device? The Ark's copy and any copy on another phone are not affected.`)) return;
  await store.deletePack(key);
  if (state.activeKey === key) {
    await store.setActivePackKey("");
    state.activeKey = "";
  }
  if (state.packKey === key) {
    state.packKey = "";
    state.pack = null;
    state.manifest = null;
    $("map-view").hidden = true;
    $("packs-view").hidden = false;
    if (state.worker) { state.worker.terminate(); state.worker = null; }
    state.router = null;
    history.replaceState(null, "", location.pathname);
  }
  await refreshInstalled();
  renderAll();
}

async function checkCopy() {
  if (!state.manifest) return;
  const status = $("check-status");
  setStatus(status, "Checking every part against its fingerprint…");
  let bad = 0;
  for (const [name, size, sha] of state.manifest.files) {
    try {
      const record = await store.getFileRecord(state.packKey, name);
      if (!record || record.size !== size || record.chunks !== Math.ceil(size / CHUNK)) {
        bad += 1;
        setStatus(status, `${name} is incomplete.`, "bad");
        continue;
      }
      const hash = new Sha256();
      for (let index = 0; index < record.chunks; index++) {
        const bytes = await store.getChunk(state.packKey, name, index);
        if (!bytes) throw new Error("missing piece");
        hash.update(new Uint8Array(bytes));
      }
      if (hash.hex() !== sha) {
        bad += 1;
        setStatus(status, `${name} does not match its fingerprint.`, "bad");
      }
    } catch (_) {
      bad += 1;
      setStatus(status, `${name} could not be read.`, "bad");
    }
  }
  if (!bad) setStatus(status, "Every part is present and matches its fingerprint. This copy is complete.", "good");
  else setStatus(status, `${bad} part(s) are damaged or missing. Remove this copy and download the pack again.`, "bad");
}

// ---------------------------------------------------------------------------
//  service worker and updates
// ---------------------------------------------------------------------------

function offerUpdate() {
  // The prompt is built here rather than written into the page.  Two reasons: a
  // reader who never sees an update pays nothing for it, and there is no second
  // copy of this wording to drift out of step with the code that honours it.
  //
  // It is shown only when a worker is *already controlling this page*.  On a
  // first visit the newly installed worker is not an update — it is the page
  // arriving.
  if (!navigator.serviceWorker.controller) return;
  let note = document.getElementById("update-note");
  if (!note) {
    note = document.createElement("div");
    note.id = "update-note";
    note.className = "note update";
    note.setAttribute("role", "status");
    note.setAttribute("aria-live", "polite");
    const label = document.createElement("span");
    label.textContent = "A newer version of this page is ready.";
    const button = document.createElement("button");
    button.type = "button";
    button.id = "update-now";
    button.textContent = "Use the new version";
    button.addEventListener("click", takeUpdate);
    note.append(label, button);
    const main = document.getElementById("main");
    if (main && main.parentNode) main.parentNode.insertBefore(note, main);
    else document.body.appendChild(note);
  }
  note.hidden = false;
}

function takeUpdate() {
  // The reload is arranged **first**, before anything that could fail.  A button
  // that appears to work and changes nothing is the one outcome worth ruling
  // out, so the reader's tap always ends in the new page: either at once when
  // the waiting worker takes over (it asks the page to reload itself), or on
  // this timer.
  //
  // Asking the waiting worker to skip its wait is still tried, because it makes
  // the reload instant.  Where a browser declines, letting go of the old worker
  // is the reliable path — and nothing a reader owns lives in it: the downloaded
  // packs and the saved places are in this device's own storage, and only the
  // page's own files (which are being replaced anyway) are cached there.
  let going = false;
  const leave = () => {
    if (going) return;
    going = true;
    location.reload();
  };
  setTimeout(leave, 1500);
  try {
    navigator.serviceWorker.getRegistration().then((fresh) => {
      const waiting = fresh && (fresh.waiting || fresh.installing);
      if (waiting) waiting.postMessage({ type: "SKIP_WAITING" });
    }).catch(() => {
      // No registration to talk to: reloading still picks up the new files.
    });
  } catch (_) {
    // The same.
  }
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js", { scope: "./" }).then((registration) => {
    const wasControlled = Boolean(navigator.serviceWorker.controller);
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      $("offline-badge").hidden = !offlineReady();
      // The new worker took over.  If this page has been controlled all along,
      // that is the moment the reader's click earns its reload — and the map,
      // the packs and the saved places all survive it, because they live in
      // storage, not in the page.
      if (wasControlled) location.reload();
    });
    if (registration.waiting && wasControlled) offerUpdate();
    registration.addEventListener("updatefound", () => {
      const installing = registration.installing;
      if (!installing) return;
      installing.addEventListener("statechange", () => {
        if (installing.state === "installed" && wasControlled) offerUpdate();
      });
    });
    // Do not ask for an update in the same breath as registering.  On a first
    // install that races the install and can leave a second worker waiting,
    // which the page then reports as an update that is not one.  The browser
    // checks on navigation; the online event covers coming back to the Ark.
    window.addEventListener("online", () => registration.update().catch(() => {}));
  }).catch((error) => {
    // The page still works while the Ark is reachable; it just cannot promise to
    // reopen with the Ark gone.  Say that rather than hiding it.
    const note = $("net-note");
    if (note) {
      note.textContent = `This browser would not keep the page offline (${error.message}). The map works while the Ark is reachable; try a normal tab, or add the page to your home screen.`;
      note.className = "note warn";
    }
  });
}

// ---------------------------------------------------------------------------
//  events
// ---------------------------------------------------------------------------

function bindEvents() {
  $("back-to-packs").addEventListener("click", () => {
    $("map-view").hidden = true;
    $("packs-view").hidden = false;
    refreshCatalog().then(renderAll);
  });
  $("zoom-in").addEventListener("click", () => state.map && state.map.zoomBy(1));
  $("zoom-out").addEventListener("click", () => state.map && state.map.zoomBy(-1));
  $("zoom-home").addEventListener("click", () => state.map && state.map.bounds && state.map.fitBounds(state.map.bounds));
  $("locate").addEventListener("click", useLocation);
  $("route-button").addEventListener("click", calculateRoute);
  $("clear-route").addEventListener("click", clearRoute);
  $("print-sheet").addEventListener("click", printableSheet);
  $("download-sheet").addEventListener("click", downloadSheet);
  $("download-gpx").addEventListener("click", downloadGPX);
  $("save-current").addEventListener("click", saveDestination);
  $("clear-local").addEventListener("click", clearLocal);
  $("check-copy").addEventListener("click", checkCopy);
  $("pause-download").addEventListener("click", () => {
    if (!state.download) return;
    state.download.paused = true;
    state.download.controller.abort();
  });
  $("cancel-download").addEventListener("click", async () => {
    if (!state.download) return;
    const key = state.download.key;
    state.download.controller.abort();
    await store.deletePack(key);
    state.download = null;
    await refreshInstalled();
    $("download-view").hidden = true;
    $("packs-view").hidden = false;
    renderAll();
  });
  for (const target of ["from", "to"]) {
    const input = $(target === "from" ? "from-input" : "to-input");
    let timer = null;
    input.addEventListener("input", () => {
      state.endpoints[target] = null;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => searchFor(target, input.value), 130);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const first = $(target === "from" ? "from-suggestions" : "to-suggestions").querySelector("button");
        if (first) first.click();
        else if (state.endpoints.from && state.endpoints.to) calculateRoute();
      }
    });
  }
  document.querySelectorAll("[data-pick]").forEach((node) => {
    node.addEventListener("click", () => {
      state.pickTarget = node.dataset.pick;
      $("pick-hint").hidden = false;
      $("pick-hint").innerHTML = `Tap the map to choose the <b>${state.pickTarget === "from" ? "start" : "destination"}</b>.`;
      $("map").focus();
    });
  });
  document.querySelectorAll('input[name="profile"]').forEach((node) => {
    node.addEventListener("change", () => {
      if (state.route) calculateRoute();
    });
  });
  $("units").addEventListener("change", async () => {
    state.units = $("units").value;
    await store.localPut("units", state.units);
    if (state.map) state.map.setUnits(state.units);
    if (state.route) renderRoute(state.route);
  });
  window.addEventListener("online", () => refreshCatalog().then(renderAll));
  window.addEventListener("offline", () => { state.online = false; renderAll(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && navigator.onLine) refreshCatalog().then(renderAll);
  });
}

function useLocation() {
  if (!navigator.geolocation) {
    setStatus($("route-status"), "This browser does not offer a location. Choose the start on the map instead.", "bad");
    return;
  }
  setStatus($("route-status"), "Asking this device for a location…");
  navigator.geolocation.getCurrentPosition((position) => {
    const place = {
      lat: position.coords.latitude,
      lon: position.coords.longitude,
      name: "My location",
      accuracy: position.coords.accuracy,
    };
    setEndpoint("from", place);
    if (state.map) state.map.goTo(place.lat, place.lon, 15);
    setStatus($("route-status"), `Start set to this device's location${position.coords.accuracy ? ` (within about ${Math.round(position.coords.accuracy)} m)` : ""}.`, "good");
  }, (error) => {
    const why = error && error.code === 1
      ? "Location permission was not given. You can still choose the start on the map."
      : "This device could not find its location. Choose the start on the map instead.";
    setStatus($("route-status"), why, "bad");
  }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
}

boot();
