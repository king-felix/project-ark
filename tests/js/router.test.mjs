import test from "node:test";
import assert from "node:assert/strict";
import { decodeGraph, Router, formatDistance } from "../../pi/maps/web/router.js";

const META = {
  names: ["Main Street", "Back Lane"],
  classes: ["path", "track", "residential", "tertiary", "secondary", "primary", "trunk", "motorway"],
  modes: { car: 1, bike: 2, foot: 4 },
  speeds_kmh: { motorway: 110, trunk: 90, primary: 70, secondary: 60, tertiary: 50, residential: 30, track: 16, path: 5 },
  foot_kmh: 4.8,
  profiles: {
    car: { label: "By car", factor: { residential: 1, track: 0.6 }, oneway: true },
    bike: { label: "By bicycle", factor: { residential: 0.55, track: 0.55, path: 0.5 }, oneway: true },
    foot: { label: "On foot", factor: {}, oneway: false },
  },
  turn_penalty_s: 4,
  turn_penalty_uturn_s: 20,
  turns: [],
};

function buildGraph(edges, turns = []) {
  // A(0,0), B(0, 0.001), C(0.001, 0.001) and D — a second way into A that is
  // nowhere near C.  Without D, a destination with no incoming car edge would
  // be snapped to a reachable node, which is a different behaviour than the
  // one-way rule this test is about.
  const nodes = [[47.0, 8.0], [47.0, 8.001], [47.001, 8.001], [47.002, 8.0]];
  const e = edges.length;
  const size = 24 + nodes.length * 8 + (nodes.length + 1) * 4 + e * 18;
  const buffer = new ArrayBuffer(size);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  bytes.set([65, 82, 75, 71], 0);
  view.setUint16(4, 2, true);
  view.setUint16(6, 0, true);
  view.setUint32(8, nodes.length, true);
  view.setUint32(12, e, true);
  view.setUint32(16, META.names.length, true);
  view.setUint32(20, 0, true);
  let at = 24;
  for (const [lat, lon] of nodes) {
    view.setInt32(at, Math.round(lat * 1e6), true);
    view.setInt32(at + 4, Math.round(lon * 1e6), true);
    at += 8;
  }
  const out = Array.from({ length: nodes.length }, () => []);
  edges.forEach((edge, index) => out[edge.from].push(index));
  let running = 0;
  for (let i = 0; i <= nodes.length; i++) {
    view.setUint32(at, running, true);
    at += 4;
    if (i < nodes.length) running += out[i].length;
  }
  // The file is CSR: every point's outgoing edges are contiguous.  Writing the
  // array in its original order would pair the offsets with somebody else's
  // edges — a fixture that builds a graph the real builder never writes.
  for (const index of out.flat()) {
    const edge = edges[index];
    view.setUint32(at, edge.to, true);
    view.setFloat32(at + 4, edge.metres, true);
    view.setUint16(at + 8, edge.cls, true);
    view.setUint16(at + 10, edge.name, true);
    view.setUint32(at + 12, edge.way, true);
    view.setUint16(at + 16, edge.modes, true);
    at += 18;
  }
  return { buffer, meta: { ...META, turns } };
}

function routerWith(edges, turns = []) {
  const { buffer, meta } = buildGraph(edges, turns);
  const graph = decodeGraph(buffer, meta);
  return new Router(graph, meta);
}

// Way 10: A -> B, one-way for vehicles, still walkable back.  111 m.
// Way 11: B <-> C, two-way.  Way 10 -> 11 at B has a no_left_turn for cars.
const edges = [
  { from: 0, to: 1, metres: 111, cls: 2, name: 0, way: 10, modes: 7 },
  { from: 1, to: 0, metres: 111, cls: 2, name: 0, way: 10, modes: 4 },
  { from: 1, to: 2, metres: 90, cls: 2, name: 1, way: 11, modes: 7 },
  { from: 2, to: 1, metres: 90, cls: 2, name: 1, way: 11, modes: 7 },
  { from: 3, to: 0, metres: 222, cls: 2, name: 1, way: 12, modes: 7 },
  { from: 0, to: 3, metres: 222, cls: 2, name: 1, way: 12, modes: 7 },
];
const A = { lat: 47.0, lon: 8.0 };
const C = { lat: 47.001, lon: 8.001 };

test("a car may drive the one-way way, and may not drive back up it", () => {
  const router = routerWith(edges);
  const forward = router.route(A, C, "car");
  assert.equal(forward.ok, true);
  assert.ok(forward.distance > 190 && forward.distance < 220);
  const backward = router.route(C, A, "car");
  assert.equal(backward.ok, false);
  assert.equal(backward.reason, "no-route");
});

test("a walker ignores the car's one-way rule and can walk back", () => {
  const router = routerWith(edges);
  const backward = router.route(C, A, "foot");
  assert.equal(backward.ok, true);
  assert.ok(backward.distance > 190);
});

test("a no_left_turn at the junction is obeyed for a car and not for a walker", () => {
  const turns = [[10, 1, 11, 0, 1]]; // TURN_NO, car only
  const router = routerWith(edges, turns);
  const car = router.route(A, C, "car");
  assert.equal(car.ok, false, "the only car route turns left at B");
  const foot = router.route(A, C, "foot");
  assert.equal(foot.ok, true, "the restriction is for motor vehicles");
});

test("an only_ restriction leaves exactly one way out", () => {
  const turns = [[10, 1, 11, 1, 1]]; // TURN_ONLY
  const router = routerWith(edges, turns);
  const car = router.route(A, C, "car");
  assert.equal(car.ok, true, "way 11 is the one permitted turn");
});

test("a town point that snaps to a one-way island is routed to a reachable road", () => {
  // Nodes 2 and 3 are a two-node island: each has an incoming car edge, so a
  // destination can snap there, but no path from the start reaches them.  A
  // town centroid must not answer "no route" when a road 100 m away is reachable.
  const island = [
    { from: 0, to: 1, metres: 76, cls: 2, name: 0, way: 10, modes: 7 },
    { from: 1, to: 0, metres: 76, cls: 2, name: 0, way: 10, modes: 7 },
    { from: 2, to: 3, metres: 200, cls: 2, name: 1, way: 11, modes: 7 },
    { from: 3, to: 2, metres: 200, cls: 2, name: 1, way: 11, modes: 7 },
  ];
  const router = routerWith(island);
  const town = { lat: 47.0012, lon: 8.0012 };   // nearer the island than the road
  const result = router.route(A, town, "car");
  assert.equal(result.ok, true);
  assert.ok(result.snap.to > 50, "the answer says how far the chosen road is");
});

test("a graph that is cut short is refused, not half-read", () => {
  const { buffer, meta } = buildGraph(edges);
  assert.throws(() => decodeGraph(buffer.slice(0, buffer.byteLength - 5), meta), /bytes/);
});

test("distance is written the way a person reads it", () => {
  assert.equal(formatDistance(0), "0 m");
  assert.equal(formatDistance(950), "950 m");
  assert.equal(formatDistance(1240), "1.2 km");
  assert.match(formatDistance(1609, "imperial"), /mi/);
});
