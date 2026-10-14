// K-Ray service worker: opens instantly, and keeps the last data you saw for when the network is bad.
const VERSION = 'kray-v1';
const SHELL = 'kray-shell-' + VERSION;
const API = 'kray-api-' + VERSION;
const ICONS = ['/icon-192.png', '/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ICONS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== SHELL && k !== API).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', e => {
  if (e.data === 'clear-data') e.waitUntil(caches.delete(API));
});

// Only these GETs are remembered for offline use. Backups, team, login and health are never stored.
function cacheableApi(url) {
  const p = url.pathname;
  if (!p.startsWith('/api/')) return false;
  if (/^\/api\/(backup|auth|team|summary|health)/.test(p) && p !== '/api/auth/me') return false;
  return true;
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then(r => { clearTimeout(t); resolve(r); }, err => { clearTimeout(t); reject(err); });
  });
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // The app page: always try the network first (so updates arrive), fall back to the saved copy.
  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      const cache = await caches.open(SHELL);
      try {
        const res = await withTimeout(fetch(req), 4000);
        if (res && res.ok) cache.put('/', res.clone());
        return res;
      } catch (err) {
        const hit = await cache.match('/');
        if (hit) return hit;
        return fetch(req);
      }
    })());
    return;
  }

  // Data: network first, last good answer when offline.
  if (cacheableApi(url)) {
    e.respondWith((async () => {
      const cache = await caches.open(API);
      try {
        const res = await fetch(req);
        if (res.ok) cache.put(url.pathname + url.search, res.clone());
        return res;
      } catch (err) {
        const hit = await cache.match(url.pathname + url.search);
        if (hit) return new Response(await hit.text(), { status: 200, headers: { 'Content-Type': 'application/json', 'X-From-Cache': '1' } });
        throw err;
      }
    })());
    return;
  }

  // Icons / manifest: saved copy first.
  if (ICONS.includes(url.pathname)) {
    e.respondWith(caches.match(req).then(h => h || fetch(req)));
  }
});
