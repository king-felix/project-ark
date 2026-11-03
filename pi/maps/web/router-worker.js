/* PROJECT ARK — the router, off the page's main thread.
 *
 * A route over a regional graph takes tens of milliseconds, which is already
 * fast enough to feel instant; the worker exists so that a slow phone stays
 * responsive while it happens, and so a route calculated in the background can
 * be cancelled by simply replacing the page.  The algorithm itself lives in
 * router.js and is also used directly when a browser refuses module workers.
 */

import { decodeGraph, Router } from "./router.js";

let router = null;

self.addEventListener("message", (event) => {
  const message = event.data || {};
  try {
    if (message.type === "load") {
      const graph = decodeGraph(message.buffer, message.meta || {});
      router = new Router(graph, message.meta || {});
      self.postMessage({
        type: "ready",
        nodes: graph.nodes,
        edges: graph.edges,
        names: graph.names.length,
      });
      return;
    }
    if (message.type === "route") {
      if (!router) throw new Error("the map graph is not loaded yet");
      const result = router.route(message.from, message.to, message.profile);
      self.postMessage({ type: "route", id: message.id, result });
      return;
    }
    if (message.type === "ping") {
      self.postMessage({ type: "pong", id: message.id });
    }
  } catch (error) {
    self.postMessage({
      type: message.type === "load" ? "load-error" : "route-error",
      id: message.id,
      message: (error && error.message) || String(error),
    });
  }
});
