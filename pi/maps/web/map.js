/* PROJECT ARK — drawing a region on a phone, from bytes on the phone.
 *
 * No tiles are fetched.  The pack carries six GeoJSON layers and a routing graph;
 * this file turns degrees into screen pixels, keeps a coarse grid so a viewport
 * only looks at the features near it, and draws with the 2D canvas every phone
 * already has.  It is deliberately plain: no animation library, no WebGL, no
 * glyph atlas, because the interesting failure here is a person in the rain with
 * a cheap phone, not a demo on a laptop.
 *
 * Everything is north-up.  That is a choice, not a missing feature: a north-up
 * map with a scale and a reset control is predictable, and a rotating map on a
 * stressed reader is one more thing to understand.
 */

export const LAYER_ORDER = ["forest", "water", "boundary", "buildings", "base-minor", "base-major"];
const ROAD_WIDTH = {
  motorway: 3.2, trunk: 2.8, primary: 2.3, secondary: 1.8,
  tertiary: 1.4, residential: 1.0, track: 0.7, path: 0.6,
};
const ROAD_COLOUR = {
  motorway: "#b9b9b9", trunk: "#a8a8a8", primary: "#969696",
  secondary: "#808080", tertiary: "#6c6c6c", residential: "#565656",
  track: "#454545", path: "#3d3d3d",
};
const PLACE_ZOOM = {
  city: 6, town: 8, village: 10, hamlet: 12, suburb: 12, quarter: 12,
  neighbourhood: 13, locality: 13,
};

const TILE = 0.01;            // ~1.1 km cells; a viewport asks a handful
const MAX_LAT = 85.05112878;

export function projectWorld(lat, lon) {
  const clamped = Math.max(-MAX_LAT, Math.min(MAX_LAT, Number(lat)));
  const x = (Number(lon) + 180) / 360;
  const s = Math.sin((clamped * Math.PI) / 180);
  const y = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
  return { x, y };
}

export function unprojectWorld(x, y) {
  const lon = x * 360 - 180;
  const n = Math.PI - 2 * Math.PI * y;
  const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  return { lat, lon };
}

function featurePoints(feature) {
  const geometry = feature && feature.geometry;
  if (!geometry) return [];
  if (geometry.type === "LineString") return [geometry.coordinates];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiLineString" || geometry.type === "MultiPolygon") {
    return geometry.coordinates.flat(1);
  }
  return [];
}

function featureBBox(feature) {
  let south = Infinity, west = Infinity, north = -Infinity, east = -Infinity;
  for (const line of featurePoints(feature)) {
    for (const pair of line) {
      const lon = Number(pair[0]);
      const lat = Number(pair[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      south = Math.min(south, lat); north = Math.max(north, lat);
      west = Math.min(west, lon); east = Math.max(east, lon);
    }
  }
  if (!Number.isFinite(south)) return null;
  return [south, west, north, east];
}

class FeatureIndex {
  constructor(features) {
    this.features = [];
    this.buckets = new Map();
    for (const feature of features || []) {
      const bbox = featureBBox(feature);
      if (!bbox) continue;
      const id = this.features.length;
      const entry = { feature, bbox, id };
      this.features.push(entry);
      const south = Math.floor(bbox[0] / TILE);
      const north = Math.floor(bbox[2] / TILE);
      const west = Math.floor(bbox[1] / TILE);
      const east = Math.floor(bbox[3] / TILE);
      // A shape that spans an absurd number of cells (a coastline ring) is put
      // in the coarse cells only: drawing it is cheap, indexing every cell it
      // touches is not.
      if ((north - south) > 200 || (east - west) > 200) {
        this._push(`${south}:${west}`, id);
        continue;
      }
      for (let latCell = south; latCell <= north; latCell++) {
        for (let lonCell = west; lonCell <= east; lonCell++) {
          this._push(`${latCell}:${lonCell}`, id);
        }
      }
    }
  }

  _push(key, id) {
    let list = this.buckets.get(key);
    if (!list) { list = []; this.buckets.set(key, list); }
    list.push(id);
  }

  query(bbox) {
    const south = Math.floor(bbox[0] / TILE);
    const north = Math.floor(bbox[2] / TILE);
    const west = Math.floor(bbox[1] / TILE);
    const east = Math.floor(bbox[3] / TILE);
    const seen = new Set();
    const out = [];
    const latSpan = north - south;
    const lonSpan = east - west;
    if (latSpan * lonSpan > 40000) {
      // The viewport is the whole region at a wide zoom: scanning the layer is
      // cheaper than visiting every cell, and the answer is the same.
      return this.features;
    }
    for (let latCell = south; latCell <= north; latCell++) {
      for (let lonCell = west; lonCell <= east; lonCell++) {
        const list = this.buckets.get(`${latCell}:${lonCell}`);
        if (!list) continue;
        for (const id of list) {
          if (seen.has(id)) continue;
          seen.add(id);
          out.push(this.features[id]);
        }
      }
    }
    return out;
  }
}

function niceDistance(metres) {
  const steps = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
    20000, 50000, 100000, 200000, 500000];
  for (const step of steps) if (step >= metres) return step;
  return 1000000;
}

export class MapView {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.onPick = options.onPick || (() => {});
    this.onView = options.onView || (() => {});
    this.onMissing = options.onMissing || (() => {});
    this.bounds = null;
    this.minZoom = 5;
    this.maxZoom = 18;
    this.zoom = 8;
    this.center = projectWorld(47.14, 9.52);
    this.layers = {};
    this.indexes = {};
    this.places = [];
    this.route = null;
    this.markers = {};
    this.width = 1;
    this.height = 1;
    this.dpr = 1;
    this._raf = null;
    this._pointers = new Map();
    this._drag = null;
    this._pinch = null;
    this._bind();
    this.resize();
  }

  setUnits(units) {
    this.units = units === "imperial" ? "imperial" : "metric";
    this.schedule();
  }

  setPack({ bounds, layers = {}, places = [], minZoom = 5, maxZoom = 17 }) {
    this.bounds = bounds || null;
    this.minZoom = Math.max(2, Math.min(18, Number(minZoom) || 5));
    this.maxZoom = Math.max(this.minZoom, Math.min(18, Number(maxZoom) || 17));
    this.places = Array.isArray(places) ? places : [];
    this.layers = {};
    this.indexes = {};
    for (const [name, body] of Object.entries(layers)) this.addLayer(name, body);
    if (bounds && bounds.length === 4) this.fitBounds(bounds);
  }

  addLayer(name, body) {
    const features = body && Array.isArray(body.features) ? body.features : [];
    this.layers[name] = features;
    this.indexes[name] = new FeatureIndex(features);
    this.schedule();
  }

  hasLayer(name) {
    return Object.prototype.hasOwnProperty.call(this.layers, name);
  }

  clearRoute() {
    this.route = null;
    this.markers = {};
    this.schedule();
  }

  setRoute(route) {
    this.route = route && route.ok ? route : null;
    if (this.route) {
      this.markers = {
        from: this.route.start,
        to: this.route.end,
      };
      if (this.route.bounds) this.fitBounds(this.route.bounds, { padding: 52 });
    }
    this.schedule();
  }

  setMarkers(markers) {
    this.markers = { ...(markers || {}) };
    this.schedule();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    this.width = Math.max(1, rect.width);
    this.height = Math.max(1, rect.height);
    this.dpr = dpr;
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.schedule();
  }

  worldSize(zoom = this.zoom) {
    return 256 * Math.pow(2, zoom);
  }

  latLonToScreen(lat, lon) {
    const world = projectWorld(lat, lon);
    const size = this.worldSize();
    return {
      x: (world.x - this.center.x) * size + this.width / 2,
      y: (world.y - this.center.y) * size + this.height / 2,
    };
  }

  screenToLatLon(x, y) {
    const size = this.worldSize();
    const worldX = (x - this.width / 2) / size + this.center.x;
    const worldY = (y - this.height / 2) / size + this.center.y;
    return unprojectWorld(worldX, worldY);
  }

  visibleBounds() {
    const nw = this.screenToLatLon(0, 0);
    const se = this.screenToLatLon(this.width, this.height);
    return [Math.min(nw.lat, se.lat), Math.min(nw.lon, se.lon),
      Math.max(nw.lat, se.lat), Math.max(nw.lon, se.lon)];
  }

  fitBounds(bounds, { padding = 34, animate = false } = {}) {
    if (!bounds || bounds.length !== 4) return;
    const [south, west, north, east] = bounds;
    const sw = projectWorld(south, west);
    const ne = projectWorld(north, east);
    const dx = Math.max(1e-9, Math.abs(ne.x - sw.x));
    const dy = Math.max(1e-9, Math.abs(sw.y - ne.y));
    const usableW = Math.max(40, this.width - padding * 2);
    const usableH = Math.max(40, this.height - padding * 2);
    const zoom = Math.min(
      Math.log2(usableW / (256 * dx)),
      Math.log2(usableH / (256 * dy)),
    );
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, zoom));
    this.center = { x: (sw.x + ne.x) / 2, y: (sw.y + ne.y) / 2 };
    this.onView(this.describe());
    this.schedule();
  }

  goTo(lat, lon, zoom = this.zoom) {
    this.center = projectWorld(lat, lon);
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, zoom));
    this.onView(this.describe());
    this.schedule();
  }

  zoomBy(delta, anchor = null) {
    const before = anchor ? this.screenToLatLon(anchor.x, anchor.y) : null;
    this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.zoom + delta));
    if (before) {
      const after = this.latLonToScreen(before.lat, before.lon);
      const size = this.worldSize();
      this.center.x += (after.x - anchor.x) / size;
      this.center.y += (after.y - anchor.y) / size;
    }
    this.onView(this.describe());
    this.schedule();
  }

  describe() {
    const centre = unprojectWorld(this.center.x, this.center.y);
    return {
      lat: centre.lat, lon: centre.lon, zoom: this.zoom,
      inside: this.insideCoverage(centre.lat, centre.lon),
    };
  }

  insideCoverage(lat, lon) {
    if (!this.bounds) return true;
    const [s, w, n, e] = this.bounds;
    return lat >= s && lat <= n && lon >= w && lon <= e;
  }

  // -- interaction ----------------------------------------------------------

  _bind() {
    const canvas = this.canvas;
    canvas.addEventListener("pointerdown", (event) => {
      canvas.setPointerCapture?.(event.pointerId);
      this._pointers.set(event.pointerId, { x: event.offsetX, y: event.offsetY });
      if (this._pointers.size === 1) {
        this._drag = { x: event.offsetX, y: event.offsetY, moved: 0 };
        this._pinch = null;
      } else if (this._pointers.size === 2) {
        const [a, b] = [...this._pointers.values()];
        this._pinch = {
          distance: Math.hypot(a.x - b.x, a.y - b.y),
          zoom: this.zoom,
          mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
        };
        this._drag = null;
      }
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this._pointers.has(event.pointerId)) return;
      this._pointers.set(event.pointerId, { x: event.offsetX, y: event.offsetY });
      if (this._pinch && this._pointers.size === 2) {
        const [a, b] = [...this._pointers.values()];
        const distance = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
        const next = this._pinch.zoom + Math.log2(distance / this._pinch.distance);
        this.zoom = Math.max(this.minZoom, Math.min(this.maxZoom, next));
        this.onView(this.describe());
        this.schedule();
        return;
      }
      if (!this._drag) return;
      const dx = event.offsetX - this._drag.x;
      const dy = event.offsetY - this._drag.y;
      this._drag.moved += Math.abs(dx) + Math.abs(dy);
      this._drag.x = event.offsetX;
      this._drag.y = event.offsetY;
      const size = this.worldSize();
      this.center.x -= dx / size;
      this.center.y -= dy / size;
      this.onView(this.describe());
      this.schedule();
    });
    const end = (event) => {
      const wasDrag = this._drag;
      this._pointers.delete(event.pointerId);
      if (this._pointers.size < 2) this._pinch = null;
      if (this._pointers.size === 0) this._drag = null;
      if (wasDrag && wasDrag.moved < 6 && !this._pinch) {
        this.onPick(this.screenToLatLon(event.offsetX, event.offsetY));
      }
    };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const step = event.deltaY < 0 ? 1 : -1;
      this.zoomBy(step, { x: event.offsetX, y: event.offsetY });
    }, { passive: false });
    canvas.addEventListener("dblclick", (event) => {
      this.zoomBy(1, { x: event.offsetX, y: event.offsetY });
    });
    canvas.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 160 : 60;
      let handled = true;
      switch (event.key) {
        case "ArrowLeft": this.center.x -= step / this.worldSize(); break;
        case "ArrowRight": this.center.x += step / this.worldSize(); break;
        case "ArrowUp": this.center.y -= step / this.worldSize(); break;
        case "ArrowDown": this.center.y += step / this.worldSize(); break;
        case "+": case "=": this.zoomBy(1); break;
        case "-": this.zoomBy(-1); break;
        case "Home": if (this.bounds) this.fitBounds(this.bounds); break;
        default: handled = false;
      }
      if (handled) {
        event.preventDefault();
        this.onView(this.describe());
        this.schedule();
      }
    });
  }

  schedule() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = null;
      this.draw();
    });
  }

  // -- drawing --------------------------------------------------------------

  draw() {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, this.width, this.height);
    const bbox = this.visibleBounds();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    for (const name of LAYER_ORDER) {
      if (!this.hasLayer(name)) continue;
      if (name === "buildings" && this.zoom < 13) continue;
      this._drawLayer(name, bbox);
    }
    if (this.route) this._drawRoute();
    this._drawMarkers();
    this._drawLabels(bbox);
    this._drawCoverage();
    this._drawScale();
    this._drawNorth();
    this._drawAttribution();
  }

  _drawLayer(name, bbox) {
    const ctx = this.ctx;
    const entries = this.indexes[name].query(bbox);
    if (!entries.length) return;
    if (name === "buildings") {
      ctx.fillStyle = "#191919";
      ctx.strokeStyle = "#2a2a2a";
      ctx.lineWidth = 0.5;
      for (const { feature } of entries) {
        this._path(feature);
        ctx.fill();
        ctx.stroke();
      }
      return;
    }
    if (name === "water") {
      ctx.fillStyle = "#0a1e30";
      ctx.strokeStyle = "#123650";
      ctx.lineWidth = 1;
      for (const { feature } of entries) {
        this._path(feature);
        if (feature.geometry.type === "Polygon" || feature.geometry.type === "MultiPolygon") {
          ctx.fill();
        }
        ctx.stroke();
      }
      return;
    }
    if (name === "forest") {
      ctx.fillStyle = "#0b2115";
      ctx.strokeStyle = "#12351f";
      ctx.lineWidth = 1;
      for (const { feature } of entries) {
        this._path(feature);
        if (feature.geometry.type === "Polygon" || feature.geometry.type === "MultiPolygon") {
          ctx.fill();
        }
        ctx.stroke();
      }
      return;
    }
    if (name === "boundary") {
      ctx.strokeStyle = "#5a4a2c";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([6, 5]);
      for (const { feature } of entries) {
        this._path(feature);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      return;
    }
    // Roads: draw the minor network first, then the major one, so a primary
    // road is not hidden under a residential street that happens to be nearby.
    const base = name === "base-major" ? 1 : 0;
    for (const { feature } of entries) {
      const klass = feature.properties && feature.properties.class;
      const width = ROAD_WIDTH[klass] ?? (base ? 2 : 1);
      const scale = Math.max(0.55, Math.min(2.4, 0.8 + (this.zoom - 8) * 0.13));
      ctx.strokeStyle = ROAD_COLOUR[klass] || (base ? "#8a8a8a" : "#555555");
      ctx.lineWidth = width * scale;
      this._path(feature);
      ctx.stroke();
    }
  }

  _path(feature) {
    const ctx = this.ctx;
    const geometry = feature.geometry;
    if (!geometry) return;
    const lines = featurePoints(feature);
    ctx.beginPath();
    for (const line of lines) {
      let started = false;
      for (const pair of line) {
        const point = this.latLonToScreen(pair[1], pair[0]);
        if (!started) { ctx.moveTo(point.x, point.y); started = true; }
        else ctx.lineTo(point.x, point.y);
      }
      if (geometry.type === "Polygon" || geometry.type === "MultiPolygon") ctx.closePath();
    }
  }

  _drawRoute() {
    const ctx = this.ctx;
    const nodes = this.route.nodes;
    if (!nodes || nodes.length < 2) return;
    // `geometry` is aligned with `nodes` by position; `nodes` holds graph node
    // numbers, which are not indices into anything the page may use directly.
    const geometry = this.route.geometry || [];
    const points = nodes.map((_node, index) => {
      const point = geometry[index] || geometry[geometry.length - 1];
      return this.latLonToScreen(point.lat, point.lon);
    });
    const stroke = (width, colour) => {
      ctx.strokeStyle = colour;
      ctx.lineWidth = width;
      ctx.beginPath();
      points.forEach((point, index) => {
        if (index === 0) ctx.moveTo(point.x, point.y);
        else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();
    };
    stroke(9, "rgba(0,0,0,0.85)");
    stroke(5, "#ffb000");
  }

  _drawMarkers() {
    const ctx = this.ctx;
    for (const [kind, point] of Object.entries(this.markers)) {
      if (!point || !Number.isFinite(point.lat)) continue;
      const screen = this.latLonToScreen(point.lat, point.lon);
      ctx.beginPath();
      ctx.arc(screen.x, screen.y, kind === "from" ? 8 : 9, 0, Math.PI * 2);
      ctx.fillStyle = kind === "from" ? "#1f9d55" : "#e5484d";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
      if (kind === "to") {
        ctx.beginPath();
        ctx.moveTo(screen.x - 5, screen.y + 7);
        ctx.lineTo(screen.x, screen.y + 17);
        ctx.lineTo(screen.x + 5, screen.y + 7);
        ctx.closePath();
        ctx.fillStyle = "#e5484d";
        ctx.fill();
      }
    }
  }

  _drawLabels(bbox) {
    if (this.zoom < 7) return;
    const ctx = this.ctx;
    const placed = [];
    const fits = (x, y, w, h) => {
      for (const box of placed) {
        if (x < box[0] + box[2] && x + w > box[0] && y < box[1] + box[3] && y + h > box[1]) return false;
      }
      return true;
    };
    const draw = (text, x, y, size, colour, weight = "500") => {
      ctx.font = `${weight} ${size}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      const width = ctx.measureText(text).width;
      if (!fits(x - width / 2, y - size, width + 6, size + 4)) return false;
      placed.push([x - width / 2, y - size, width + 6, size + 4]);
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(0,0,0,0.9)";
      ctx.strokeText(text, x - width / 2, y);
      ctx.fillStyle = colour;
      ctx.fillText(text, x - width / 2, y);
      return true;
    };
    // Settlements first: a village name is worth more than a bench.
    const places = this.places
      .filter((place) => place && place.name && this.insideCoverage(place.lat, place.lon))
      .filter((place) => this.zoom >= (PLACE_ZOOM[place.kind] ?? 16))
      .sort((a, b) => (a.rank ?? 20) - (b.rank ?? 20));
    let drawn = 0;
    for (const place of places) {
      if (drawn >= 28) break;
      const screen = this.latLonToScreen(place.lat, place.lon);
      if (screen.x < -40 || screen.y < -20 || screen.x > this.width + 40 || screen.y > this.height + 20) continue;
      const settlement = place.kind in PLACE_ZOOM;
      const size = settlement ? (place.kind === "city" ? 15 : 13) : 11;
      if (draw(String(place.name), screen.x, screen.y - 8, size,
        settlement ? "#f2f2f2" : "#bdbdbd", settlement ? "600" : "400")) drawn++;
    }
    if (this.zoom >= 15) {
      let streets = 0;
      for (const name of ["base-major", "base-minor"]) {
        if (!this.hasLayer(name)) continue;
        for (const { feature } of this.indexes[name].query(bbox)) {
          if (streets >= 22) break;
          const props = feature.properties || {};
          if (!props.name) continue;
          const lines = featurePoints(feature);
          const line = lines[0];
          if (!line || line.length < 2) continue;
          const mid = line[Math.floor(line.length / 2)];
          const screen = this.latLonToScreen(mid[1], mid[0]);
          if (screen.x < 0 || screen.y < 0 || screen.x > this.width || screen.y > this.height) continue;
          if (draw(String(props.name), screen.x, screen.y, 11, "#9a9a9a", "400")) streets++;
        }
      }
    }
  }

  _drawCoverage() {
    if (!this.bounds) return;
    const [south, west, north, east] = this.bounds;
    const nw = this.latLonToScreen(north, west);
    const se = this.latLonToScreen(south, east);
    const ctx = this.ctx;
    ctx.strokeStyle = "rgba(255,176,0,0.35)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([8, 6]);
    ctx.strokeRect(nw.x, nw.y, se.x - nw.x, se.y - nw.y);
    ctx.setLineDash([]);
  }

  _drawScale() {
    const ctx = this.ctx;
    const centre = unprojectWorld(this.center.x, this.center.y);
    const metresPerPixel = (156543.03392 * Math.cos((centre.lat * Math.PI) / 180))
      / Math.pow(2, this.zoom);
    const targetPixels = Math.min(150, Math.max(70, this.width * 0.25));
    let pixels;
    let label;
    if (this.units === "imperial") {
      const feet = metresPerPixel * targetPixels * 3.28084;
      const steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5280, 10560,
        26400, 52800, 105600, 264000];
      const chosen = steps.find((step) => step >= feet) || 528000;
      pixels = (chosen / 3.28084) / metresPerPixel;
      label = chosen < 5280 ? `${chosen} ft`
        : `${(chosen / 5280).toFixed(chosen < 52800 ? 1 : 0)} mi`;
    } else {
      const metres = niceDistance(metresPerPixel * targetPixels);
      pixels = metres / metresPerPixel;
      label = metres >= 1000 ? `${metres / 1000} km` : `${metres} m`;
    }
    const x = 14;
    const y = this.height - 22;
    ctx.strokeStyle = "#d0d0d0";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y - 5);
    ctx.lineTo(x, y);
    ctx.lineTo(x + pixels, y);
    ctx.lineTo(x + pixels, y - 5);
    ctx.stroke();
    ctx.font = "11px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "#d0d0d0";
    ctx.fillText(label, x, y - 9);
  }

  _drawNorth() {
    const ctx = this.ctx;
    const x = this.width - 24;
    const y = 26;
    ctx.fillStyle = "#d0d0d0";
    ctx.font = "600 12px system-ui, -apple-system, sans-serif";
    ctx.fillText("N", x - 4, y + 16);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - 5, y + 10);
    ctx.lineTo(x + 5, y + 10);
    ctx.closePath();
    ctx.fill();
  }

  _drawAttribution() {
    const ctx = this.ctx;
    ctx.font = "10px system-ui, -apple-system, sans-serif";
    const text = "© OpenStreetMap contributors";
    const width = ctx.measureText(text).width;
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(this.width - width - 12, this.height - 20, width + 10, 16);
    ctx.fillStyle = "#bcbcbc";
    ctx.fillText(text, this.width - width - 7, this.height - 8);
  }

  // -- a still picture for a printed route sheet -----------------------------

  svgFor(bounds, { width = 900, height = 620, route = this.route, title = "" } = {}) {
    const [south, west, north, east] = bounds;
    const sw = projectWorld(south, west);
    const ne = projectWorld(north, east);
    const dx = Math.max(1e-9, ne.x - sw.x);
    const dy = Math.max(1e-9, sw.y - ne.y);
    const pad = 36;
    const scale = Math.min((width - pad * 2) / dx, (height - pad * 2) / dy);
    const ox = (width - dx * scale) / 2 - sw.x * scale;
    const oy = (height - dy * scale) / 2 - sw.y * scale;
    const point = (lat, lon) => {
      const world = projectWorld(lat, lon);
      return [world.x * scale + ox, world.y * scale + oy];
    };
    const parts = [];
    parts.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${escapeXml(title || "Route map")}">`);
    parts.push(`<rect width="${width}" height="${height}" fill="#000"/>`);
    const drawLayer = (name, stroke, fill, lineWidth, dash = "") => {
      if (!this.hasLayer(name)) return;
      const entries = this.indexes[name].query([south, west, north, east]);
      for (const { feature } of entries) {
        const lines = featurePoints(feature);
        for (const line of lines) {
          if (!line.length) continue;
          const d = line.map((pair, index) => {
            const [x, y] = point(pair[1], pair[0]);
            return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
          }).join(" ");
          const isArea = feature.geometry.type === "Polygon" || feature.geometry.type === "MultiPolygon";
          parts.push(`<path d="${d}${isArea ? " Z" : ""}" fill="${isArea ? fill : "none"}" stroke="${stroke}" stroke-width="${lineWidth}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`);
        }
      }
    };
    drawLayer("forest", "#12351f", "#0b2115", 0.8);
    drawLayer("water", "#123650", "#0a1e30", 0.8);
    drawLayer("boundary", "#5a4a2c", "none", 1, "6 5");
    drawLayer("base-minor", "#555555", "none", 1);
    drawLayer("base-major", "#9a9a9a", "none", 2);
    if (route && route.ok && route.nodes && route.geometry) {
      const d = route.geometry.map((position, index) => {
        const [x, y] = point(position.lat, position.lon);
        return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      }).join(" ");
      parts.push(`<path d="${d}" fill="none" stroke="#000" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>`);
      parts.push(`<path d="${d}" fill="none" stroke="#ffb000" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>`);
    }
    if (route && route.start) {
      const [x, y] = point(route.start.lat, route.start.lon);
      parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="8" fill="#1f9d55" stroke="#fff" stroke-width="2"/>`);
    }
    if (route && route.end) {
      const [x, y] = point(route.end.lat, route.end.lon);
      parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="9" fill="#e5484d" stroke="#fff" stroke-width="2"/>`);
    }
    parts.push(`<rect x="8" y="${height - 30}" width="250" height="22" fill="rgba(0,0,0,0.65)"/>`);
    parts.push(`<text x="14" y="${height - 14}" fill="#cfcfcf" font-family="system-ui, sans-serif" font-size="11">© OpenStreetMap contributors</text>`);
    parts.push("</svg>");
    return parts.join("");
  }
}

export function escapeXml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
