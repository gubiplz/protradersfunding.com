/* Minimalny service worker: wymagany, by Chrome/Samsung Internet traktowały
   stronę jako instalowalną PWA i fundament pod przyszłe web push (iOS 16.4+).
   Celowo NIE cache'uje nic — portal pokazuje dane finansowe na żywo, a stale
   saldo z cache byłoby gorsze niż brak offline'u. */
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
  e.waitUntil((async () => {
    /* Cel kliku ląduje też w Cache Storage (jeden wpis nawigacyjny, nie cache
       HTTP): iOS gubi postMessage do zawieszonej/przeładowanej strony — portal
       czyta wpis przy starcie i przy visibilitychange (TTL 30 s). Zapis NIE
       jest awaitowany przed focus/openWindow: Safari odrzuca je, gdy minie
       zbyt dużo czasu od gestu użytkownika. */
    const save = caches.open('pf-nav')
      .then((c) => c.put('/__pending-nav', new Response(JSON.stringify({ url, ts: Date.now() }))))
      .catch(() => {});
    const ws = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    let act = null;
    for (const w of ws) {
      if (w.url.includes('/portal') && 'focus' in w) {
        /* postMessage zamiast navigate(): SPA przełącza widok bez reloadu
           (navigate() gubi stan i bywa zawodny w iOS PWA) */
        w.postMessage({ type: 'navigate', url });
        act = w.focus();
        break;
      }
    }
    if (!act) act = self.clients.openWindow(url);
    await Promise.all([save, Promise.resolve(act).catch(() => {})]);
  })());
});
