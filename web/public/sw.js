/* Minimal offline shell: cache the app shell, let /v1/* pass through, audio via Cache Storage by the app itself. */
const SHELL = 'tmusic-shell-v1';
const SHELL_URLS = ['/', '/index.html', '/manifest.webmanifest'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)).catch(() => undefined));
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k.startsWith('tmusic-')).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/stream')) return;
  if (e.request.mode === 'navigate') {
    e.respondWith(caches.match('/index.html').then((r) => r || fetch(e.request).catch(() => caches.match('/index.html'))));
    return;
  }
  if (SHELL_URLS.some((p) => url.pathname === p) || url.pathname.startsWith('/assets/')) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => { caches.open(SHELL).then((c) => c.put(e.request, res.clone())).catch(() => {}); return res; })));
  }
});
