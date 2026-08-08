/* Minimal service worker: required for Chrome/Samsung Internet to treat the
   page as an installable PWA and the base for web push (iOS 16.4+). It caches
   NOTHING on purpose — the portal shows live financial data and a stale
   balance from cache would be worse than no offline mode at all.

   ONE worker serves TWO apps on this origin: the trader portal (scope /portal)
   and the admin panel (scope /admin, its own manifest). The payload URL decides
   which app a click belongs to; on iOS each home-screen install runs its own
   isolated copy of this worker anyway. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});

/* Web push: payload JSON {title, body, url, tag} z backendu (app/push.py) */
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title || 'ProTraders', {
    body: d.body || '',
    icon: '/static/img/icon-192.png',
    badge: '/static/img/icon-192.png',
    tag: d.tag || undefined,
    data: { url: d.url || '/portal' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/portal';
  /* Which SPA does this click belong to? Admin URLs focus/open the panel;
     everything else keeps the old portal behaviour byte for byte. */
  const app = url.startsWith('/admin') ? '/admin' : '/portal';
  e.waitUntil((async () => {
    /* The click target also lands in Cache Storage (a single navigation entry,
       not an asset cache): iOS can drop the postMessage to a suspended page,
       so the page re-reads this entry on every return to the foreground.
       Separate keys per app — in a shared desktop profile both SPAs read this
       cache, and one key would let the portal swallow an admin deep link. */
    const key = app === '/admin' ? '/__pending-nav-admin' : '/__pending-nav';
    const save = caches.open('pf-nav')
      .then((c) => c.put(key, new Response(JSON.stringify({ url, ts: Date.now() }))))
      .catch(() => {});
    const ws = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    let act = null;
    for (const w of ws) {
      if (w.url.includes(app) && 'focus' in w) {
        /* postMessage instead of navigate(): the SPA switches the view without a
           reload (navigate() would drop the logged-in state mid-flow). */
        w.postMessage({ type: 'navigate', url });
        act = w.focus();
        break;
      }
    }
    if (!act) act = self.clients.openWindow(url);
    await Promise.all([save, Promise.resolve(act).catch(() => {})]);
  })());
});
