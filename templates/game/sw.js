{% load static %}/* Fantamorte service worker */
const CACHE = 'fantamorte-v{{ cache_version }}';
const PRECACHE = [
  '/offline/',
  '{% static "css/fantamorte.css" %}',
  '{% static "js/fantamorte.js" %}',
  '{% static "pwa/icon.svg" %}',
  '{% static "pwa/icon-192.png" %}',
  '{% static "pwa/icon-512.png" %}',
  '{% static "pwa/badge-96.png" %}',
  '{% static "pwa/icon-maskable-192.png" %}',
  '{% static "pwa/icon-maskable-512.png" %}',
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
  if (url.origin !== self.location.origin) return;

  // Cache only immutable/public assets. Authenticated HTML is deliberately
  // never persisted: a shared device or a later account must not receive a
  // page cached under a previous session.
  const isAsset = url.pathname.startsWith('/static/')
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

  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(req).catch(function () {
        return caches.match('/offline/');
      })
    );
  }
  // API JSON, manifest and every other request go directly to the network.
});

self.addEventListener('push', function (event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: 'Fantamorte', body: event.data && event.data.text() }; }
  const title = data.title || '☠ Fantamorte';
  const options = {
    body: data.body || '',
    icon: data.icon || '{% static "pwa/icon-192.png" %}',
    badge: data.badge || '{% static "pwa/badge-96.png" %}',
    tag: data.tag || 'fantamorte',
    data: { url: data.url || '/' },
    requireInteraction: !!data.urgent,
  };
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, options),
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (cs) {
      cs.forEach(function (c) { c.postMessage({ type: 'fm-notification' }); });
    }),
  ]));
});

const VAPID_PUBLIC_KEY = '{{ vapid_public_key }}';

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = self.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

self.addEventListener('pushsubscriptionchange', function (event) {
  const oldEndpoint = event.oldSubscription && event.oldSubscription.endpoint;
  if (!oldEndpoint) return;
  event.waitUntil((async function () {
    let sub = event.newSubscription || null;
    if (!sub) {
      if (!VAPID_PUBLIC_KEY) return;
      try {
        sub = await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
        });
      } catch (e) {
        return;
      }
    }
    try {
      await fetch('/api/push/rotate/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_endpoint: oldEndpoint, subscription: sub.toJSON() }),
      });
    } catch (e) { /* best-effort */ }
  })());
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
