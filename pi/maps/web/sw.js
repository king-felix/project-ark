/* PROJECT ARK — keeping the Maps page itself, so it opens with nothing else.
 *
 * The pack is stored by the page in IndexedDB; this worker stores only the
 * application shell (HTML, CSS, JavaScript, the help page, the icon).  Three
 * rules matter:
 *
 *   * `/maps/api/` is never cached.  Pack descriptions and pack bytes are the
 *     page's business, and a stale catalogue served from a cache would be a lie
 *     about what the Ark has;
 *   * nothing outside `/maps/` is touched — not the portal, and certainly not
 *     the owner's pages on another origin;
 *   * a new version waits.  The page asks the reader before taking it, so an
 *     update cannot replace the code in the middle of a route.
 */

// The Ark replaces this token with a fingerprint of every application file when
// it serves this script, so any change to the page produces a byte-different
// worker — which is what makes an already-installed phone update.  Opening this
// file from disk (no server) leaves the literal, which is a safe fallback: the
// cache name is then simply "the version in the file".
const VERSION = "ark-maps-" + "__ARK_RELEASE__".replace(/^__.*__$/, "v3");
const ASSETS = [
  "./",
  "index.html",
  "help.html",
  "app.css",
  "app.js",
  "sha256.js",
  "store.js",
  "search.js",
  "router.js",
  "router-worker.js",
  "map.js",
  "app.webmanifest",
  "icon.svg",
  "icon-180.png",
  "icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    // Each entry is fetched with the cache bypassed: a "new" worker whose files
    // came out of the old HTTP cache would install the old page under a new name.
    await cache.addAll(ASSETS.map((url) => new Request(url, { cache: "reload" })));
    // First install: take over at once so "Ready offline" becomes true without
    // asking the reader to reload a page they just opened.  An *update* does
    // not do this; it waits for the page's message.
    if (!self.registration.active) await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    // Every other release's cache goes.  One failure here must not stop the
    // worker taking over: the leftovers are swept on the next activate.
    await Promise.all(keys.filter((key) => key.startsWith("ark-maps-") && key !== VERSION)
      .map((key) => caches.delete(key).catch(() => {})));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

function isMaps(url) {
  return url.origin === self.location.origin && url.pathname.startsWith("/maps/");
}

async function networkFirst(request, fallback) {
  try {
    const response = await withTimeout(fetch(request), 3000);
    if (response && response.ok) {
      const cache = await caches.open(VERSION);
      cache.put(request, response.clone()).catch(() => {});
      return response;
    }
  } catch (_) {
    // The Ark is not reachable: the cache below is the whole point.
  }
  const cached = await caches.match(request, { ignoreSearch: true });
  if (cached) return cached;
  return (await caches.match(fallback)) || new Response(
    "<!doctype html><meta charset=utf-8><title>Maps</title><p>This page has not been kept on this device yet. Reconnect to the Ark and open Maps once.</p>",
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function cacheFirst(request) {
  // Look in **this release's** cache and nowhere else.  `caches.match()` searches
  // every cache the origin has, oldest first, so an update that had already been
  // installed could still be handed the previous release's `app.js` — the reader
  // pressed "use the new version" and got the old page, which is worse than no
  // update at all.
  const cache = await caches.open(VERSION);
  const cached = await cache.match(request, { ignoreSearch: true });
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    cache.put(request, response.clone()).catch(() => {});
  }
  return response;
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("too slow")), ms);
    promise.then((value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); });
  });
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (!isMaps(url)) return;
  if (url.pathname.startsWith("/maps/api/")) return;   // the network, or nothing
  if (request.mode === "navigate") {
    const help = url.pathname.replace(/\/$/, "").endsWith("/help");
    event.respondWith(networkFirst(request, help ? "help.html" : "index.html"));
    return;
  }
  event.respondWith(cacheFirst(request).catch(() => new Response("", { status: 504 })));
});
