const CACHE_NAME = 'lottery-stats-v17';
const ASSETS = [
  './',
  './index.html',
  './draws.json',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // Cross-origin requests (e.g. the GoatCounter script and its hit beacon)
  // go straight to the network: a cached beacon would never be counted.
  if (url.origin !== self.location.origin) return;

  // draws.json changes daily -- go network-first so a launch with a live
  // connection always shows today's data instead of waiting for the *next*
  // launch (iOS has no background fetch to refresh the cache in between).
  // Falls back to the cached copy when the request fails or returns an error.
  if (url.pathname.endsWith('/draws.json')) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Everything else: stale-while-revalidate -- serve cached copy instantly,
  // refresh cache in the background for next launch.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
