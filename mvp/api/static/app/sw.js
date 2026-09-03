/* GauTrack service worker.
 *
 * Caches only the app shell (HTML/CSS/JS/icons) so the field app opens with no
 * network. API calls are deliberately NETWORK-ONLY: a stale cached answer about
 * who owns a cow is worse than no answer, and the offline write path is the
 * IndexedDB queue in app.js, not the cache.
 */
const CACHE = "gautrack-shell-v6";
const SHELL = [
  "/app/",
  "/app/index.html",
  "/app/app.js",
  "/app/styles.css",
  "/app/manifest.json",
  "/app/icon-192.png",
  "/app/icon-512.png",
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                             .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;                 // never cache a write
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;           // network-only

  e.respondWith(
    caches.match(e.request).then(function (hit) {
      if (hit) {
        // refresh in the background so a deploy is picked up next time
        fetch(e.request).then(function (res) {
          if (res && res.ok) caches.open(CACHE).then(function (c) { c.put(e.request, res.clone()); });
        }).catch(function () {});
        return hit;
      }
      return fetch(e.request).then(function (res) {
        if (res && res.ok && (url.pathname.startsWith("/app/") || url.pathname.startsWith("/static/"))) {
          const copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
        }
        return res;
      }).catch(function () {
        return caches.match("/app/index.html");
      });
    })
  );
});
