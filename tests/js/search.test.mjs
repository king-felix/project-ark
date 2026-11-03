import test from "node:test";
import assert from "node:assert/strict";
import { SearchIndex, parseCoordinates, fold, describePlace } from "../../pi/maps/web/search.js";

const places = [
  { name: "Zürich", key: fold("Zürich"), kind: "city", label: "City", lat: 47.37, lon: 8.54, rank: 0 },
  { name: "Zurich Airport", key: fold("Zurich Airport"), kind: "aeroway", label: "Airport", lat: 47.46, lon: 8.55, rank: 20 },
  { name: "Central Pharmacy", key: fold("Central Pharmacy"), kind: "pharmacy", label: "Pharmacy", lat: 47.38, lon: 8.54, rank: 20 },
  { name: "Alpha", key: fold("Alpha"), kind: "village", label: "Village", lat: 47.0, lon: 8.0, rank: 2 },
];

test("an accent and a case difference are the same name", () => {
  const index = new SearchIndex(places);
  const found = index.search("zurich");
  assert.equal(found.reason, "ok");
  assert.equal(found.results[0].name, "Zürich");
});

test("a prefix finds the longer name too, ranked after the exact match", () => {
  const index = new SearchIndex(places);
  const found = index.search("zur");
  assert.equal(found.results[0].name, "Zürich");
  assert.ok(found.results.some((row) => row.name === "Zurich Airport"));
});

test("no match and an empty index are different answers", () => {
  assert.equal(new SearchIndex(places).search("nothing here").reason, "no-match");
  assert.equal(new SearchIndex([]).search("anything").reason, "empty-index");
});

test("coordinates are accepted as a destination", () => {
  assert.deepEqual(parseCoordinates("47.14, 9.52"), { lat: 47.14, lon: 9.52 });
  assert.deepEqual(parseCoordinates("47.14 9.52"), { lat: 47.14, lon: 9.52 });
  assert.equal(parseCoordinates("not a coordinate"), null);
  assert.equal(parseCoordinates("95, 9.52"), null);
});

test("a kind filter keeps the pharmacy out of a village search", () => {
  const index = new SearchIndex(places);
  assert.equal(index.search("a", { kind: "pharmacy" }).results.length, 1);
});

test("a result reads as a name and a kind", () => {
  assert.match(describePlace(places[2]), /Central Pharmacy/);
  assert.match(describePlace(places[2]), /Pharmacy/);
});
