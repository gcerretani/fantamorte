{% load static %}/* Fantamorte service worker */
const CACHE = 'fantamorte-v{{ cache_version }}';
// Asset propri risolti dal tag static di Django: in produzione
// (ManifestStaticFilesStorage) sono i nomi con hash, gli stessi che le
// pagine referenziano — i path non hashati non verrebbero mai riusati e
// nginx li serve con cache lunga (30 giorni), rischiando stale.
const PRECACHE = [
  '/',
  '/offline/',
  '{% static "css/fantamorte.css" %}',
  '{% static "js/fantamorte.js" %}',
  '{% static "pwa/icon.svg" %}',
  '{% static "pwa/icon-192.png" %}',
  '{% static "pwa/icon-512.png" %}',
  '{% static "pwa/badge-96.png" %}',
  '{% static "pwa/icon-maskable-192.png" %}',
  '{% static "pwa/icon-maskable-512.png" %}',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js',
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
  // Cache-first SOLO per asset immutabili: static (nomi con hash), media,
  // CDN. Mai per le API: una risposta JSON cachata qui verrebbe riservita
  // per sempre e il modal persona mostrerebbe bonus/dati vecchi anche a
  // server aggiornato.
  const isAsset = isBootstrap
    || url.pathname.startsWith('/static/')
    || url.pathname.startsWith('/media/');
  if (isAsset) {
    event.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (resp) {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
          }
          return resp;
        });
      })
    );
    return;
  }
  // Network-first con fallback offline per le pagine HTML.
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(req).then(function (resp) {
        // Cache solo risposte 200 stessa-origine: mai redirect (302 login),
        // pagine di errore o risposte opache.
        if (resp.ok && resp.type === 'basic') {
          const copy = resp.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return resp;
      }).catch(function () {
        return caches.match(req).then(function (m) { return m || caches.match('/offline/'); });
      })
    );
  }
  // Tutto il resto (API JSON, manifest, ...): non intercettare, va in rete.
});

// -------- Push --------
self.addEventListener('push', function (event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: 'Fantamorte', body: event.data && event.data.text() }; }
  const title = data.title || '☠ Fantamorte';
  const options = {
    body: data.body || '',
    // icon: immagine grande a colori. badge: silhouette nella status bar
    // Android, che ne usa SOLO il canale alpha — deve essere il PNG
    // monocromatico trasparente, mai l'icona quadrata opaca (diventerebbe
    // un quadrato bianco). Il payload può fare override di entrambi.
    icon: data.icon || '{% static "pwa/icon-192.png" %}',
    badge: data.badge || '{% static "pwa/badge-96.png" %}',
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
