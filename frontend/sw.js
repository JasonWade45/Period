/* CycleCare Service Worker — Offline Shell & Locale Caching */
const CACHE_NAME = "cyclecare-pwa-v2";
const ASSETS = [
  "/",
  "/index.html",
  "/style.css?v=20261021",
  "/rtl.css?v=20261021",
  "/i18n.js?v=20261021",
  "/app.js?v=20261021",
  "/manifest.json",
  "/locales/ar.json",
  "/locales/en.json"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).catch(() => null)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/v1/")) return;
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((c) => c.put(event.request, clone)).catch(() => null);
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
