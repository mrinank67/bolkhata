const CACHE_VERSION = "bolkhata-v10";
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/icons/favicon.svg",
  "/app.js",
  "/js/theme.js",
  "/js/config.js",
  "/js/auth.js",
  "/js/idle-timer.js",
  "/js/recording.js",
  "/js/ui.js",
  "/js/dashboard.js",
  "/js/history.js",
  "/js/suppliers.js",
  "/js/ledger.js",
  "/js/orders.js",
  "/styles.css",
  "/manifest.json",
  "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap"
];

// Listen for skip-waiting message from the client
self.addEventListener("message", e => {
  if (e.data && e.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

// Install — cache static shell, then immediately activate
self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE_VERSION).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate — purge ALL old caches, then claim clients
self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch — NETWORK-FIRST for everything
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // Skip non-GET requests and API endpoints entirely (let browser handle them)
  if (e.request.method !== "GET") return;
  if (
    url.pathname.startsWith("/process_voice") ||
    url.pathname.startsWith("/voice") ||
    url.pathname.startsWith("/config") ||
    url.pathname.startsWith("/history") ||
    url.pathname.startsWith("/inventory") ||
    url.pathname.startsWith("/confirm_clear_inventory") ||
    url.pathname.startsWith("/suppliers") ||
    url.pathname.startsWith("/ledger") ||
    url.pathname.startsWith("/orders") ||
    url.pathname.startsWith("/pay") ||
    url.pathname.startsWith("/settings")
  ) {
    return;
  }

  // Network-first: try network, update cache, fall back to cache if offline
  e.respondWith(
    fetch(e.request)
      .then(response => {
        // Cache the fresh response for offline use
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_VERSION).then(cache => cache.put(e.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Offline — serve from cache
        return caches.match(e.request).then(cached => {
          if (cached) return cached;
          // Fallback: return cached index.html for navigation requests
          if (e.request.mode === "navigate") {
            return caches.match("/index.html");
          }
        });
      })
  );
});
