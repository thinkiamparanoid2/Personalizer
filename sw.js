const CACHE_NAME = 'jarvis-v1';

const APP_SHELL = [
  './',
  './index.html',
  './style.css',
  './script.js',
  './firebase.js',
  './vault-crypto.js',
  './pc.js',
  './farvis.js',
  './notes.js',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './icon-maskable-512.png',
  './favicon.png',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-solid-900.woff2',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-brands-400.woff2',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-regular-400.woff2'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return Promise.allSettled(APP_SHELL.map(url => cache.add(url)));
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// Stale-while-revalidate for the app's own files and CDN libraries: the cached copy answers instantly
// (no waiting on a slow network), the network refreshes the cache in the background, and open pages are
// told when one of our own files changed so they can offer a reload.
// Everything else (Firebase APIs, the PC agent, OMDB) goes straight to the network and is never cached.
const CACHEABLE_HOSTS = ['cdnjs.cloudflare.com', 'www.gstatic.com', 'fonts.googleapis.com', 'fonts.gstatic.com'];

const version = res => res && (res.headers.get('ETag') || res.headers.get('Last-Modified'));

async function notifyUpdated() {
  for (const client of await self.clients.matchAll({ type: 'window' })) client.postMessage({ type: 'app-updated' });
}

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  const own = url.origin === self.location.origin;
  if (req.method !== 'GET' || !(own || CACHEABLE_HOSTS.includes(url.hostname))) return;
  // Navigations like /?addMovie=... share one cached shell.
  const key = req.mode === 'navigate' ? new Request(url.origin + url.pathname) : req;

  event.respondWith(caches.open(CACHE_NAME).then(async cache => {
    const cached = await cache.match(key);
    const refresh = fetch(req).then(async res => {
      if (res.ok) {
        await cache.put(key, res.clone());
        if (own && cached && version(res) && version(res) !== version(cached)) await notifyUpdated();
      }
      return res;
    });
    if (!cached) return refresh;
    event.waitUntil(refresh.catch(() => {}));  // offline: the cached copy is all we need
    return cached;
  }));
});
