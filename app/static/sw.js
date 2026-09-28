// Installable-app service worker. Pages are always fetched from the network
// (spending data must be fresh); only when that fails is a small offline
// notice shown instead of the browser's error page.
const OFFLINE_HTML = `<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Нет сети</title>
<body style="font:16px/1.5 -apple-system,system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0 16px;text-align:center;background:#eef0ec;color:#17201b">
<div><div style="font-size:40px">📡</div><b>Нет связи с сервером</b><br>
<span style="color:#6b746e">Проверь интернет и обнови страницу.</span></div></body></html>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(
    fetch(event.request).catch(
      () => new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
    )
  );
});
