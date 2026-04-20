const CACHE_NAME = "bolkhata-v1";
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/app.js",
  "/styles.css",
  "/manifest.json",
  "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap"
];

// Install — cache static shell
self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — network-first for API calls, cache-first for static assets
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // Always hit network for API endpoints
  if (
    url.pathname.startsWith("/process_voice") ||
    url.pathname.startsWith("/config") ||
    url.pathname.startsWith("/history")
  ) {
    return;
  }

  // Cache-first for everything else (static assets)
  e.respondWith(
    caches.match(e.request).then(cached => {
      return cached || fetch(e.request).then(response => {
        // Cache successful responses for next time
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return response;
      });
    }).catch(() => {
      // Offline fallback — return cached index.html for navigation requests
      if (e.request.mode === "navigate") {
        return caches.match("/index.html");
      }
    })
  );
});
