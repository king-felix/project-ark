/* PROJECT ARK — finding a place in a pack, without a network.
 *
 * The index is the pack's own `places.json`: a name, a search key already folded
 * by the builder, a kind, and a point.  Searching is a scan of that list with a
 * folded comparison — a few thousand names is a phone-sized problem, and a scan
 * that answers in a millisecond is easier to trust than an index that might have
 * been built from a different generation of the data.
 *
 * The three answers are deliberately different, because they mean different
 * things to somebody standing in the rain:
 *   - `empty-index`  this pack carries no searchable names at all;
 *   - `no-match`     the index was asked and nothing matched;
 *   - `ok`           here are the matches.
 */

export function fold(text) {
  return String(text || "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    // Python's casefold turns ß into ss; JavaScript's toLowerCase does not, and
    // the pack's keys were written by Python.  This is the one fold difference
    // worth closing, because a German village name is not an exotic input here.
    .replace(/ß/g, "ss")
    .replace(/\s+/g, " ")
    .trim();
}

export function parseCoordinates(text) {
  const match = String(text || "").trim().match(
    /^(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)$/);
  if (!match) return null;
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lat < -85 || lat > 85 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

export function haversineM(a, b) {
  const R = 6371008.8;
  const p1 = (a.lat * Math.PI) / 180;
  const p2 = (b.lat * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((b.lon - a.lon) * Math.PI) / 180;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export class SearchIndex {
  constructor(places, options = {}) {
    this.places = Array.isArray(places) ? places : [];
    this.byKey = new Map();
    for (const place of this.places) {
      const key = String(place.key || fold(place.name));
      if (!key) continue;
      if (!this.byKey.has(key)) this.byKey.set(key, []);
      this.byKey.get(key).push(place);
    }
    this.kinds = options.kinds || [];
  }

  get count() {
    return this.places.length;
  }

  search(query, { limit = 12, near = null, kind = "" } = {}) {
    const text = fold(query);
    if (!text) return { reason: "empty-query", results: [] };
    if (!this.places.length) return { reason: "empty-index", results: [] };
    const tokens = text.split(" ").filter(Boolean);
    const scored = [];
    for (const place of this.places) {
      if (kind && String(place.kind || "") !== kind) continue;
      const key = String(place.key || fold(place.name));
      if (!key) continue;
      let all = true;
      for (const token of tokens) {
        if (!key.includes(token)) { all = false; break; }
      }
      if (!all) continue;
      let score;
      if (key === text) score = 0;
      else if (key.startsWith(text)) score = 10;
      else if (tokens.every((token) => key.split(" ").some((word) => word.startsWith(token)))) score = 20;
      else score = 30;
      score += Number(place.rank || 20) / 5;
      score += Math.min(8, key.length / 8);
      if (near && Number.isFinite(place.lat) && Number.isFinite(place.lon)) {
        // Distance breaks ties between two villages with the same name; it is
        // never allowed to outrank a better textual match (the scale is metres
        // divided by 20 km, so a farther exact match still wins).
        score += Math.min(20, haversineM(near, place) / 20000);
      }
      scored.push({ score, place });
    }
    scored.sort((a, b) => a.score - b.score || String(a.place.name).localeCompare(String(b.place.name)));
    if (!scored.length) return { reason: "no-match", results: [] };
    return { reason: "ok", results: scored.slice(0, limit).map((row) => row.place) };
  }

  nearest(lat, lon, limit = 6) {
    const near = { lat, lon };
    return this.places
      .filter((place) => Number.isFinite(place.lat) && Number.isFinite(place.lon))
      .map((place) => ({ place, metres: haversineM(near, place) }))
      .sort((a, b) => a.metres - b.metres)
      .slice(0, limit);
  }
}

export function kindWords(place) {
  return String(place.label || place.kind || "Place")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

export function describePlace(place) {
  const words = kindWords(place);
  const name = String(place.name || "").trim();
  return name ? `${name} — ${words}` : words;
}
