const CACHE_NAME = 'command-center-v10';

const APP_SHELL = [
  './',
  './index.html',
  './style.css',
  './script.js',
  './firebase.js',
  './vault-crypto.js',
  './pc.js',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
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

// Network-first for the app's own files and CDN libraries (fresh after every deploy, cached for offline).
// Everything else (Firebase APIs, the PC agent, OMDB) goes straight to the network and is never cached.
const CACHEABLE_HOSTS = ['cdnjs.cloudflare.com', 'www.gstatic.com'];

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const cacheable = event.request.method === 'GET' &&
    (url.origin === self.location.origin || CACHEABLE_HOSTS.includes(url.hostname));
  if (!cacheable) return;

  event.respondWith(
    fetch(event.request).then(response => {
      if (response.ok) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
      }
      return response;
    }).catch(() =>
      // Offline: navigations like /?addMovie=... still get the cached shell.
      caches.match(event.request, { ignoreSearch: event.request.mode === 'navigate' }))
  );
});
