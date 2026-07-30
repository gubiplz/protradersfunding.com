/* Service worker: web push + click-through to the portal.
   Served from the domain root (/sw.js) so its scope covers the whole site. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.title || 'Pro Traders Funding', {
    body: d.body || '',
    icon: '/static/img/apple-touch-icon.png',
    badge: '/static/img/favicon.png',
    tag: d.tag || 'ptf',
    data: { url: d.url || '/portal' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/portal';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) {
      if (c.url.includes('/portal') && 'focus' in c) { c.navigate(url); return c.focus(); }
    }
    return clients.openWindow(url);
  }));
});
