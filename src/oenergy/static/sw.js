/* Service Worker für die progressive Web-App.
 *
 * - Statische Dateien (CSS, JS, Chart.js, Icons) werden zwischengespeichert.
 * - Die API-Daten (/api/...) werden nie gecacht, damit die Anzeige aktuell bleibt.
 * - Für Seiten gilt "network first": online wird immer frisch geladen, offline
 *   kommt die zuletzt gecachte Seite.
 */
const CACHE = "oenergy-v2";
const ASSETS = [
  "/static/css/app.css",
  "/static/js/app.js",
  "/static/vendor/htmx.min.js",
  "/static/vendor/chart.umd.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/offline.html",
  "/login",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS).catch(() => undefined))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  // API-Daten niemals cachen
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => undefined);
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match("/login"))),
    );
    return;
  }

  // Die eigenen Dateien (app.js, css) werden zuerst vom Netz geholt, damit
  // Updates sofort ankommen; nur bei Offline-Betrieb kommt der Cache.
  // Die Vendor-Dateien (htmx, Chart.js) sind stabil und kommen aus dem Cache.
  const isVendor = url.pathname.startsWith("/static/vendor/");

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached && isVendor) {
        return cached;
      }
      return fetch(request, isVendor ? {} : { cache: "no-cache" })
        .then((response) => {
          if (response.ok && url.pathname.startsWith("/static/")) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => undefined);
          }
          return response;
        })
        .catch(() => cached);
    }),
  );
});

/* ---------- Push-Benachrichtigungen ---------- */
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { body: event.data ? event.data.text() : "" };
  }
  const title = payload.title || "o-energy";
  const options = {
    body: payload.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: payload.tag || "oenergy",
    data: { url: payload.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes(url) && "focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
