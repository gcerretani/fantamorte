/* Fantamorte service worker */
const CACHE = 'fantamorte-v{{ cache_version }}';
const PRECACHE = [
  '/',
  '/offline/',
  '/static/css/fantamorte.css',
  '/static/js/fantamorte.js',
  '/static/pwa/icon.svg',
  '/static/pwa/icon-192.png',
  '/static/pwa/icon-512.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(PRECACHE).catch(function () { /* ignore single misses */ });
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // Solo GET stessa-origine + CDN bootstrap
  const sameOrigin = url.origin === self.location.origin;
  const isBootstrap = url.host === 'cdn.jsdelivr.net';
  if (!sameOrigin && !isBootstrap) return;
  // Network-first per HTML, cache-first per asset statici
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(req).then(function (resp) {
        const copy = resp.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
        return resp;
      }).catch(function () {
        return caches.match(req).then(function (m) { return m || caches.match('/offline/'); });
      })
    );
  } else {
    event.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (resp) {
          const copy = resp.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
          return resp;
        });
      })
    );
  }
});

// -------- Push --------
self.addEventListener('push', function (event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: 'Fantamorte', body: event.data && event.data.text() }; }
  const title = data.title || '☠ Fantamorte';
  const options = {
    body: data.body || '',
    icon: '/static/pwa/icon-192.png',
    badge: '/static/pwa/icon-192.png',
    tag: data.tag || 'fantamorte',
    data: { url: data.url || '/' },
    requireInteraction: !!data.urgent,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (const c of list) {
        if (c.url.indexOf(url) !== -1 && 'focus' in c) return c.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
