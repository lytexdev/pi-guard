// Service worker: keeps the app shell available offline and shows push alerts.
// API responses are never cached: what the dashboard shows must be current.
const CACHE = "pi-guard-shell-v1";
const SHELL = ["/", "/static/css/app.css", "/static/js/app.js", "/static/js/api.js",
  "/static/icons/sprite.svg", "/static/img/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request)
    .then((response) => {
      if (response.ok && (url.pathname === "/" || url.pathname.startsWith("/static/"))) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
      }
      return response;
    })
    .catch(() => caches.match(event.request)));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data?.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "pi-guard", {
    body: data.body || "",
    icon: "/static/img/icon-192.png",
    badge: "/static/img/icon-192.png",
    tag: data.tag || "pi-guard",
    renotify: true,
    requireInteraction: data.priority === "high" || data.priority === "critical",
    // Admins get two buttons. The token in this notification allows one tap, for an hour.
    actions: data.token ? [{ action: "ack", title: data.ack_label || "OK" }, { action: "alarm", title: data.alarm_label || "Alarm" }] : [],
    data: { url: data.url || "/#/events", token: data.token || null, labels: { ack: data.ack_label, alarm: data.alarm_label } },
  }));
});

async function act(action, data) {
  const response = await fetch("/api/push/action", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, token: data.token }), credentials: "omit" });
  const done = data.labels?.[action] || action;
  await self.registration.showNotification("pi-guard", {
    body: response.ok ? `✓ ${done}` : `✗ ${done}`, icon: "/static/img/icon-192.png", tag: "pi-guard-action",
  });
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  if (event.action && event.notification.data?.token) {
    event.waitUntil(act(event.action, event.notification.data));
    return;
  }
  const target = event.notification.data?.url || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    for (const client of clients) {
      if ("focus" in client) { client.navigate(target); return client.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
