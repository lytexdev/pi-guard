// Opens a linked household's dashboard under /fed/<id>/: this Pi's own page asks for /api/...
// as if it were at home, and this worker sends those requests to /fed/<id>/api/..., which this
// Pi passes through the link. Pages and scripts are never fetched from there.
const base = new URL(self.registration.scope).pathname;  // "/fed/<id>/"
const THROUGH = /^\/api\//;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith(base) || !THROUGH.test(url.pathname)) return;
  const target = `${base}${url.pathname.slice(1)}${url.search}`;
  event.respondWith((async () => {
    const request = event.request;
    if (request.mode === "navigate" || request.method === "GET" || request.method === "HEAD") {
      return fetch(target, { method: request.method, headers: request.headers, credentials: "same-origin", cache: request.cache === "only-if-cached" ? "default" : request.cache });
    }
    return fetch(target, { method: request.method, headers: request.headers, body: await request.blob(), credentials: "same-origin" });
  })());
});
