/* PROJECT ARK — routing on a phone, with no network and no server.
 *
 * This is the browser half of `pi/api/ark_maps.py`'s `graph.bin` contract.  The
 * graph is a compressed sparse row of *directed* edges, each carrying:
 *
 *   to     the point it arrives at
 *   metres its length
 *   class  which kind of road (path … motorway)
 *   name   an index into the street-name table
 *   way    the OpenStreetMap way id, so a turn restriction can name it
 *   modes  a bit mask: car=1, bike=2, foot=4
 *
 * Two facts make the router honest rather than plausible:
 *
 * * direction is stored, not inferred — a one-way street simply has no reverse
 *   edge for a car, while the walker's reverse edge is still there;
 * * turn restrictions from the survey are checked at every junction, so a
 *   `no_left_turn` is not something the page discovers after the fact.
 *
 * A* searches over *edges*, not points, because a turn penalty and a turn
 * restriction both depend on which street you arrived on.  Search over points
 * would have to throw that away, and the directions would then send somebody
 * the wrong way down a one-way street the graph already knew about.
 *
 * Everything here is pure arithmetic on typed arrays: no DOM, no fetch, no
 * storage.  That is what lets the same file run in a worker and be tested in
 * Node against a made-up graph.
 */

export const GRAPH_MAGIC = "ARKG";
export const GRAPH_VERSION = 2;
export const HEADER_BYTES = 24;
export const EDGE_BYTES = 18;
export const NODE_BYTES = 8;
export const OFFSET_BYTES = 4;
export const NO_NAME = 0xffff;
export const MODE_CAR = 1;
export const MODE_BIKE = 2;
export const MODE_FOOT = 4;
export const MODE_BITS = { car: MODE_CAR, bike: MODE_BIKE, foot: MODE_FOOT };

export const TURN_NO = 0;
export const TURN_ONLY = 1;

export const DEFAULT_SNAP_M = 500;
export const MAX_SPEED_KMH = 130;

function fail(message) {
  throw new Error(message);
}

export function decodeGraph(buffer, meta = {}) {
  const bytes = new Uint8Array(buffer);
  if (bytes.byteLength < HEADER_BYTES) {
    fail("this map graph is too short to read");
  }
  const view = new DataView(buffer);
  const magic = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
  if (magic !== GRAPH_MAGIC) fail("this is not a map graph");
  const version = view.getUint16(4, true);
  if (version !== GRAPH_VERSION) {
    fail(`this map graph is version ${version} and this page reads version ${GRAPH_VERSION}`);
  }
  const nodes = view.getUint32(8, true);
  const edges = view.getUint32(12, true);
  const nameCount = view.getUint32(16, true);
  if (!nodes || !edges) fail("this map graph has no roads in it");
  const expected = HEADER_BYTES + nodes * NODE_BYTES + (nodes + 1) * OFFSET_BYTES + edges * EDGE_BYTES;
  if (bytes.byteLength !== expected) {
    fail(`this map graph is ${bytes.byteLength} bytes and its header says ${expected}`);
  }

  const lat = new Float64Array(nodes);
  const lon = new Float64Array(nodes);
  let at = HEADER_BYTES;
  for (let i = 0; i < nodes; i++) {
    lat[i] = view.getInt32(at, true) / 1e6;
    lon[i] = view.getInt32(at + 4, true) / 1e6;
    at += NODE_BYTES;
  }
  const offsets = new Uint32Array(nodes + 1);
  for (let i = 0; i <= nodes; i++) {
    offsets[i] = view.getUint32(at, true);
    at += OFFSET_BYTES;
  }
  if (offsets[0] !== 0 || offsets[nodes] !== edges) {
    fail("this map graph's road index does not add up");
  }
  const to = new Uint32Array(edges);
  const metres = new Float32Array(edges);
  const cls = new Uint16Array(edges);
  const name = new Uint16Array(edges);
  const way = new Uint32Array(edges);
  const modes = new Uint16Array(edges);
  const source = new Uint32Array(edges);
  for (let i = 0; i < edges; i++) {
    to[i] = view.getUint32(at, true);
    metres[i] = view.getFloat32(at + 4, true);
    cls[i] = view.getUint16(at + 8, true);
    name[i] = view.getUint16(at + 10, true);
    way[i] = view.getUint32(at + 12, true);
    modes[i] = view.getUint16(at + 16, true);
    at += EDGE_BYTES;
    if (to[i] >= nodes) fail(`edge ${i} points at a point this graph does not have`);
    if (!(metres[i] > 0)) fail(`edge ${i} has no length`);
    if (!modes[i]) fail(`edge ${i} permits nobody`);
  }
  for (let i = 0; i < nodes; i++) {
    for (let e = offsets[i]; e < offsets[i + 1]; e++) source[e] = i;
  }
  const names = Array.isArray(meta.names) ? meta.names : [];
  if (nameCount !== names.length) {
    fail(`this graph holds ${nameCount} street names and its description lists ${names.length}`);
  }
  const classes = Array.isArray(meta.classes) ? meta.classes : [];
  const graph = {
    nodes, edges, lat, lon, offsets, to, metres, cls, name, way, modes, source,
    names, classes, meta,
    hasMode: {},
    hasModeIn: {},
    component: {},
    componentSize: {},
    largestComponent: {},
  };
  for (const profile of Object.keys(MODE_BITS)) {
    const bit = MODE_BITS[profile];
    const flags = new Uint8Array(nodes);
    const inbound = new Uint8Array(nodes);
    const parent = new Int32Array(nodes);
    for (let i = 0; i < nodes; i++) parent[i] = i;
    const find = (value) => {
      let root = value;
      while (parent[root] !== root) root = parent[root];
      let walk = value;
      while (parent[walk] !== walk) {
        const next = parent[walk];
        parent[walk] = root;
        walk = next;
      }
      return root;
    };
    for (let e = 0; e < edges; e++) {
      if (modes[e] & bit) {
        flags[source[e]] = 1;
        inbound[to[e]] = 1;
        const a = find(source[e]);
        const b = find(to[e]);
        if (a !== b) parent[a] = b;
      }
    }
    const sizes = new Uint32Array(nodes);
    for (let i = 0; i < nodes; i++) {
      const root = find(i);
      parent[i] = root;
      sizes[root] += 1;
    }
    let largest = 0;
    for (let i = 0; i < nodes; i++) largest = Math.max(largest, sizes[i]);
    graph.hasMode[profile] = flags;
    graph.hasModeIn[profile] = inbound;
    graph.component[profile] = parent;
    graph.componentSize[profile] = sizes;
    graph.largestComponent[profile] = largest;
  }
  return graph;
}

export function haversineM(aLat, aLon, bLat, bLon) {
  const R = 6371008.8;
  const p1 = (aLat * Math.PI) / 180;
  const p2 = (bLat * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((bLon - aLon) * Math.PI) / 180;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export function bearingDegrees(aLat, aLon, bLat, bLon) {
  const p1 = (aLat * Math.PI) / 180;
  const p2 = (bLat * Math.PI) / 180;
  const dl = ((bLon - aLon) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

export function compass(bearing) {
  const words = ["north", "north-east", "east", "south-east", "south", "south-west",
    "west", "north-west"];
  const index = Math.round((((bearing % 360) + 360) % 360) / 45) % 8;
  return words[index];
}

export function formatDistance(metres, units = "metric") {
  const value = Math.max(0, Number(metres) || 0);
  if (units === "imperial") {
    const feet = value * 3.28084;
    if (feet < 500) return `${Math.round(feet / 10) * 10} ft`;
    const miles = feet / 5280;
    return `${miles < 10 ? miles.toFixed(1) : Math.round(miles)} mi`;
  }
  if (value < 1000) return `${Math.round(value / 10) * 10} m`;
  const km = value / 1000;
  return `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

export function formatDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return "under a minute";
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export class Router {
  constructor(graph, options = {}) {
    this.graph = graph;
    this.meta = graph.meta || {};
    this.turnPenalty = Number(this.meta.turn_penalty_s ?? 4);
    this.uturnPenalty = Number(this.meta.turn_penalty_uturn_s ?? 20);
    this.snapLimit = Number(options.snapLimit ?? DEFAULT_SNAP_M);
    this.speed = {};
    const classes = graph.classes;
    const kmh = this.meta.speeds_kmh || {};
    const footKmh = Number(this.meta.foot_kmh ?? 4.8);
    for (const profile of Object.keys(MODE_BITS)) {
      const table = new Float64Array(classes.length || 8);
      for (let c = 0; c < table.length; c++) {
        if (profile === "foot") {
          table[c] = footKmh / 3.6;
        } else {
          const spec = (this.meta.profiles || {})[profile] || {};
          const klass = classes[c] || "residential";
          const factor = (spec.factor || {})[klass] ?? 1;
          table[c] = ((Number(kmh[klass] ?? 30) * Number(factor)) / 3.6);
        }
      }
      this.speed[profile] = table;
    }
    this.maxSpeed = {};
    for (const profile of Object.keys(MODE_BITS)) {
      let best = 0;
      for (let c = 0; c < this.speed[profile].length; c++) {
        best = Math.max(best, this.speed[profile][c]);
      }
      this.maxSpeed[profile] = best || (MAX_SPEED_KMH / 3.6);
    }
    this.turns = new Map();
    for (const row of this.meta.turns || []) {
      if (!Array.isArray(row) || row.length !== 5) continue;
      const [fromWay, via, toWay, kind, mask] = row;
      const key = `${fromWay}:${via}`;
      let entry = this.turns.get(key);
      if (!entry) {
        entry = { only: [], no: [] };
        this.turns.set(key, entry);
      }
      const item = { way: Number(toWay), mask: Number(mask) || 7 };
      if (Number(kind) === TURN_ONLY) entry.only.push(item);
      else entry.no.push(item);
    }
  }

  nearest(lat, lon, profile = "car", { destination = false } = {}) {
    const { lat: lats, lon: lons, nodes, hasMode, hasModeIn, component, componentSize } = this.graph;
    const outbound = hasMode[profile] || hasMode.car;
    const inbound = (hasModeIn && hasModeIn[profile]) || outbound;
    const flags = destination ? inbound : outbound;
    const roots = component && component[profile];
    const sizes = componentSize && componentSize[profile];
    // A node on a tiny fragment is a car park aisle, a private track that lost
    // its connection, or a survey artefact.  It is still the *nearest* node, and
    // snapping to it makes a route that should be obvious answer "no route".
    // Keep the nearest substantial node as an alternative; below 50 connected
    // nodes the fragment is not a network anybody can travel through.
    let best = null;
    let substantial = null;
    for (let i = 0; i < nodes; i++) {
      if (!flags[i]) continue;
      const dLat = lats[i] - lat;
      const dLon = (lons[i] - lon) * Math.cos((lat * Math.PI) / 180);
      const d = dLat * dLat + dLon * dLon;
      const metres = haversineM(lat, lon, lats[i], lons[i]);
      if (!best || metres < best.metres) best = { node: i, metres };
      if (roots && sizes && sizes[roots[i]] >= 50
          && (!substantial || metres < substantial.metres)) {
        substantial = { node: i, metres };
      }
    }
    if (!best) {
      return { ok: false, reason: "no-roads", message: "This pack has no road this way of travelling can use." };
    }
    let chosen = best;
    if (roots && sizes && sizes[roots[best.node]] < 50 && substantial
        && substantial.metres <= this.snapLimit) {
      chosen = substantial;
    }
    return {
      ok: true,
      node: chosen.node,
      lat: lats[chosen.node],
      lon: lons[chosen.node],
      metres: chosen.metres,
    };
  }

  _roadName(edge) {
    const index = this.graph.name[edge];
    if (index !== NO_NAME && this.graph.names[index]) return this.graph.names[index];
    const klass = this.graph.classes[this.graph.cls[edge]] || "road";
    if (klass === "path") return "the path";
    if (klass === "track") return "the track";
    return "an unnamed road";
  }

  _turnAllowed(edge, next) {
    const fromWay = this.graph.way[edge];
    if (!fromWay) return true;
    const via = this.graph.to[edge];
    const entry = this.turns.get(`${fromWay}:${via}`);
    if (!entry) return true;
    const bit = MODE_BITS[this._profile];
    let only = null;
    for (const rule of entry.only) {
      if (!(rule.mask & bit)) continue;
      if (!only) only = new Set();
      only.add(rule.way);
    }
    if (only && !only.has(this.graph.way[next])) return false;
    for (const rule of entry.no) {
      if ((rule.mask & bit) && rule.way === this.graph.way[next]) return false;
    }
    return true;
  }

  _penalty(edge, next) {
    const g = this.graph;
    if (g.to[next] === g.source[edge]) return this.uturnPenalty;
    if (g.name[edge] !== g.name[next]) return this.turnPenalty;
    // A sharp turn on the same street is still a turn; the class table has no
    // junction geometry, so the name is the best signal the survey gives us.
    const p0 = g.source[edge];
    const p1 = g.to[edge];
    const p2 = g.to[next];
    const b1 = bearingDegrees(g.lat[p0], g.lon[p0], g.lat[p1], g.lon[p1]);
    const b2 = bearingDegrees(g.lat[p1], g.lon[p1], g.lat[p2], g.lon[p2]);
    let delta = ((b2 - b1 + 540) % 360) - 180;
    if (Math.abs(delta) > 60) return this.turnPenalty;
    return 0;
  }

  route(from, to, profile = "car") {
    const result = this._routeOnce(from, to, profile);
    if (result.ok || result.reason !== "no-route") return result;
    // A destination can snap to a one-way node that no path can enter even
    // though the opposite carriageway 20 m away is reachable.  Answering "no
    // route" for a place plainly on the network is wrong, so before saying it,
    // try the next nearest destination points within the snap limit.
    for (const alt of this.alternatives(to.lat, to.lon, profile, result.snap.to)) {
      const retry = this._routeOnce(from, alt, profile);
      if (retry.ok) {
        // The route ends at the alternative, but the distance that matters to
        // the person is from the place they asked for.
        retry.snap = { ...retry.snap, to: haversineM(to.lat, to.lon, alt.lat, alt.lon) };
        retry.end = { lat: alt.lat, lon: alt.lon };
        return retry;
      }
    }
    return result;
  }

  alternatives(lat, lon, profile, firstMetres, limit = 4) {
    const { lat: lats, lon: lons, nodes, hasMode, hasModeIn } = this.graph;
    const inbound = (hasModeIn && hasModeIn[profile]) || hasMode[profile] || hasMode.car;
    // A request that sits exactly on a graph point means that point: moving it
    // to the far side of a one-way would answer a different question.  A place
    // that had to be snapped anyway (a town centroid, a tapped spot) may be
    // snapped to any point within the same limit that a route can actually reach.
    if (!firstMetres || firstMetres < 1) return [];
    const found = [];
    for (let i = 0; i < nodes; i++) {
      if (!inbound[i]) continue;
      const metres = haversineM(lat, lon, lats[i], lons[i]);
      if (metres > this.snapLimit) continue;
      if (firstMetres != null && Math.abs(metres - firstMetres) < 10) continue;
      found.push({ lat: lats[i], lon: lons[i], metres });
      if (found.length > 64) {
        found.sort((a, b) => a.metres - b.metres);
        found.length = 32;
      }
    }
    found.sort((a, b) => a.metres - b.metres);
    const picked = [];
    for (const item of found) {
      if (picked.some((other) => haversineM(other.lat, other.lon, item.lat, item.lon) < 15)) {
        continue;
      }
      picked.push(item);
      if (picked.length >= limit) break;
    }
    return picked;
  }

  _routeOnce(from, to, profile = "car") {
    const started = Date.now();
    if (!MODE_BITS[profile]) throw new Error(`unknown way of travelling: ${profile}`);
    this._profile = profile;
    const bit = MODE_BITS[profile];
    const g = this.graph;
    const start = this.nearest(from.lat, from.lon, profile);
    if (!start.ok) return { ok: false, reason: start.reason, message: start.message };
    const end = this.nearest(to.lat, to.lon, profile, { destination: true });
    if (!end.ok) return { ok: false, reason: end.reason, message: end.message };
    if (start.metres > this.snapLimit || end.metres > this.snapLimit) {
      return {
        ok: false,
        reason: "far-from-road",
        message: `The nearest road this way of travelling can use is ${formatDistance(
          Math.max(start.metres, end.metres))} away. Pick a point closer to a road.`,
        snap: { from: start.metres, to: end.metres },
      };
    }
    if (start.node === end.node) {
      return {
        ok: true, profile, edges: [], nodes: [start.node], distance: 0, seconds: 0,
        steps: [{
          kind: "arrive", instruction: "You are already here.", road: "",
          distance: 0, from: { lat: start.lat, lon: start.lon },
          to: { lat: end.lat, lon: end.lon },
        }],
        snap: { from: start.metres, to: end.metres },
        searched: 0, elapsedMs: Date.now() - started,
      };
    }
    const E = g.edges;
    const gScore = new Float64Array(E).fill(Infinity);
    const prev = new Int32Array(E).fill(-1);
    const closed = new Uint8Array(E);
    const speeds = this.speed[profile];
    const maxSpeed = this.maxSpeed[profile];
    const heapEdge = [];
    const heapCost = [];
    const heapKey = [];

    const heur = (node) => haversineM(g.lat[node], g.lon[node], end.lat, end.lon) / maxSpeed;

    const push = (edge, cost, priority) => {
      let i = heapEdge.length;
      heapEdge.push(edge);
      heapCost.push(cost);
      heapKey.push(priority);
      while (i > 0) {
        const parent = (i - 1) >> 1;
        if (heapKey[parent] <= priority) break;
        heapEdge[i] = heapEdge[parent];
        heapCost[i] = heapCost[parent];
        heapKey[i] = heapKey[parent];
        i = parent;
      }
      heapEdge[i] = edge;
      heapCost[i] = cost;
      heapKey[i] = priority;
    };
    const pop = () => {
      const edge = heapEdge[0];
      const cost = heapCost[0];
      const lastEdge = heapEdge.pop();
      const lastCost = heapCost.pop();
      const lastKey = heapKey.pop();
      const size = heapEdge.length;
      if (size) {
        // Sift the last element down.  The value that was at the end is the one
        // being placed, so every comparison is against `lastKey`, not against a
        // slot that has already been popped.
        let i = 0;
        while (true) {
          const left = i * 2 + 1;
          if (left >= size) break;
          const right = left + 1;
          const child = right < size && heapKey[right] < heapKey[left] ? right : left;
          if (heapKey[child] >= lastKey) break;
          heapEdge[i] = heapEdge[child];
          heapCost[i] = heapCost[child];
          heapKey[i] = heapKey[child];
          i = child;
        }
        heapEdge[i] = lastEdge;
        heapCost[i] = lastCost;
        heapKey[i] = lastKey;
      }
      return { edge, cost };
    };

    for (let e = g.offsets[start.node]; e < g.offsets[start.node + 1]; e++) {
      if (!(g.modes[e] & bit)) continue;
      const cost = g.metres[e] / speeds[g.cls[e]];
      if (cost < gScore[e]) {
        gScore[e] = cost;
        push(e, cost, cost + heur(g.to[e]));
      }
    }
    let goal = -1;
    let popped = 0;
    while (heapEdge.length) {
      const { edge, cost } = pop();
      if (closed[edge]) continue;
      if (cost > gScore[edge] + 1e-9) continue;
      closed[edge] = 1;
      popped++;
      if (g.to[edge] === end.node) { goal = edge; break; }
      const node = g.to[edge];
      for (let next = g.offsets[node]; next < g.offsets[node + 1]; next++) {
        if (!(g.modes[next] & bit)) continue;
        if (!this._turnAllowed(edge, next)) continue;
        const step = g.metres[next] / speeds[g.cls[next]] + this._penalty(edge, next);
        const nextCost = cost + step;
        if (nextCost + 1e-9 < gScore[next]) {
          gScore[next] = nextCost;
          prev[next] = edge;
          push(next, nextCost, nextCost + heur(g.to[next]));
        }
      }
    }
    if (goal < 0) {
      return {
        ok: false,
        reason: "no-route",
        message: "There is no route between those points with the roads and paths in this pack. "
          + "They may be on different networks, or the connection may be outside the region.",
        snap: { from: start.metres, to: end.metres },
        searched: popped,
        elapsedMs: Date.now() - started,
      };
    }
    const path = [];
    let walk = goal;
    while (walk !== -1) { path.push(walk); walk = prev[walk]; }
    path.reverse();
    let distance = 0;
    for (const edge of path) distance += g.metres[edge];
    const nodes = [g.source[path[0]]];
    for (const edge of path) nodes.push(g.to[edge]);
    const steps = this.directions(path);
    const geometry = nodes.map((node) => ({ lat: g.lat[node], lon: g.lon[node] }));
    let minLat = Infinity, minLon = Infinity, maxLat = -Infinity, maxLon = -Infinity;
    for (const node of nodes) {
      minLat = Math.min(minLat, g.lat[node]);
      minLon = Math.min(minLon, g.lon[node]);
      maxLat = Math.max(maxLat, g.lat[node]);
      maxLon = Math.max(maxLon, g.lon[node]);
    }
    return {
      ok: true,
      profile,
      edges: path,
      nodes,
      geometry,
      distance,
      seconds: gScore[goal],
      steps,
      snap: { from: start.metres, to: end.metres },
      bounds: [minLat, minLon, maxLat, maxLon],
      start: { lat: start.lat, lon: start.lon },
      end: { lat: end.lat, lon: end.lon },
      searched: popped,
      elapsedMs: Date.now() - started,
    };
  }

  directions(path) {
    if (!path.length) return [];
    const g = this.graph;
    const steps = [];
    const point = (node) => ({ lat: g.lat[node], lon: g.lon[node] });
    let runStart = 0;
    let runDistance = g.metres[path[0]];
    const firstBearing = bearingDegrees(
      g.lat[g.source[path[0]]], g.lon[g.source[path[0]]],
      g.lat[g.to[path[0]]], g.lon[g.to[path[0]]]);
    let current = {
      kind: "start",
      instruction: `Head ${compass(firstBearing)} on ${this._roadName(path[0])}`,
      road: this._roadName(path[0]),
      distance: 0,
      bearing: firstBearing,
      from: point(g.source[path[0]]),
      to: point(g.to[path[0]]),
    };
    for (let i = 1; i < path.length; i++) {
      const edge = path[i - 1];
      const next = path[i];
      const junction = g.to[edge];
      const b1 = bearingDegrees(g.lat[g.source[edge]], g.lon[g.source[edge]],
        g.lat[junction], g.lon[junction]);
      const b2 = bearingDegrees(g.lat[junction], g.lon[junction],
        g.lat[g.to[next]], g.lon[g.to[next]]);
      let delta = ((b2 - b1 + 540) % 360) - 180;
      const nameChanged = g.name[edge] !== g.name[next];
      const sharp = Math.abs(delta) > 25;
      if (!sharp && !nameChanged) {
        runDistance += g.metres[next];
        current.to = point(g.to[next]);
        continue;
      }
      current.distance = runDistance;
      current.to = point(g.to[edge]);
      steps.push(current);
      let kind = "straight";
      let words;
      if (Math.abs(delta) > 150) { kind = "uturn"; words = "Make a U-turn"; }
      else if (delta < -25) { kind = "left"; words = "Turn left"; }
      else if (delta > 25) { kind = "right"; words = "Turn right"; }
      else { words = "Continue"; }
      const road = this._roadName(next);
      current = {
        kind,
        instruction: `${words} onto ${road}`,
        road,
        distance: 0,
        bearing: b2,
        from: point(junction),
        to: point(g.to[next]),
      };
      runStart = i;
      runDistance = g.metres[next];
    }
    current.distance = runDistance;
    current.to = point(g.to[path[path.length - 1]]);
    steps.push(current);
    const last = steps[steps.length - 1];
    steps.push({
      kind: "arrive",
      instruction: last.road ? `Arrive at your destination, on ${last.road}` : "Arrive at your destination",
      road: last.road,
      distance: 0,
      bearing: last.bearing,
      from: last.to,
      to: last.to,
    });
    return steps;
  }
}

export function routeSummary(result, units = "metric") {
  if (!result || !result.ok) return "";
  return `${formatDistance(result.distance, units)} · about ${formatDuration(result.seconds)}`;
}
