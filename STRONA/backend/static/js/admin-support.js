/* Dymek supportu w panelu admina (prawy dolny róg) — odpowiednik dymka tradera.
 *
 * Wszystkie powiadomienia supportu siedzą TUTAJ, nie w dzwonku: plakietka
 * z liczbą rozmów czekających na odpowiedź, pierścień i podgląd nowej
 * wiadomości tradera przy dymku, a w okienku lista rozmów jak w komunikatorze
 * i sama rozmowa z polem odpowiedzi. Push na telefon (kategoria „Support chat"
 * w ustawieniach powiadomień) prowadzi tu przez /admin?chat=<id>.
 *
 * Dane: GET /api/admin/support/chats (lista + `waiting` + `last_id`),
 * GET /api/admin/tickets/{id} (wątek), POST …/reply i …/typing — te same,
 * których używa widok Tickets. Korzysta z globali admin-panel.js (api, ME,
 * toast, VIEW, go) i chat-kit.js (chatHtml, chatCompose). Style: helpdesk.css.
 */
(function () {
  'use strict';
  var LISTA = [], CZEKA = 0, OSTATNI = null, CHAT = null, WIDOK = 'list', OTWARTY = false,
    TIMER = null, WYSYLA = false, PODGLAD_T = null;
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var utc = function (iso) { return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'); };
  var zalogowany = function () { return typeof ME !== 'undefined' && !!ME; };

  var IKONA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.8 7L4 20.5l1.5-4.6A8 8 0 1 1 21 12Z"/>'
    + '<path d="M8.5 11h7M8.5 14.5h4"/></svg>';
  var ZAMKNIJ = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
    + '<path d="M6 6l12 12M18 6L6 18"/></svg>';
  var WSTECZ = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';

  function zbuduj() {
    if (document.getElementById('as-fab')) return;
    var fab = document.createElement('button');
    fab.id = 'as-fab'; fab.type = 'button'; fab.className = 'hd-fab hidden';
    fab.setAttribute('aria-label', 'Support chats');
    fab.innerHTML = IKONA + '<span class="hd-cnt hidden" aria-hidden="true"></span>';
    fab.onclick = function () { OTWARTY ? zamknij() : otworz(); };
    var pk = document.createElement('div');
    pk.id = 'as-peek'; pk.className = 'hd-peek'; pk.hidden = true;
    pk.setAttribute('role', 'status'); pk.setAttribute('aria-live', 'polite');
    var p = document.createElement('section');
    p.id = 'as-panel'; p.className = 'hd-panel'; p.setAttribute('role', 'dialog');
    p.setAttribute('aria-label', 'Support chats'); p.hidden = true;
    p.innerHTML = '<header class="hd-head">'
      + '<button type="button" class="hd-back" id="as-back" aria-label="All conversations" onclick="asList()">'
      + WSTECZ + '</button><div class="hd-av" id="as-av"></div>'
      + '<div class="hd-ttl"><b id="as-name"></b><span id="as-sub"></span></div>'
      + '<button type="button" class="hd-x" aria-label="Close" onclick="asClose()">' + ZAMKNIJ + '</button></header>'
      + '<div class="hd-body" id="as-body"></div><div class="hd-foot" id="as-foot"></div>';
    document.body.appendChild(fab);
    document.body.appendChild(pk);
    document.body.appendChild(p);
  }

  function kto(c) { return (c && (c.trader_name || c.trader_email)) || 'Trader'; }
  function inicjal(c) { return esc(kto(c).trim().charAt(0).toUpperCase() || 'T'); }
  function kiedy(iso) {
    var d = utc(iso), teraz = new Date();
    if (d.toDateString() === teraz.toDateString())
      return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === new Date(teraz - 864e5).toDateString()) return 'Yesterday';
    if (teraz - d < 6 * 864e5) return d.toLocaleDateString('en-US', { weekday: 'short' });
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  /* ---------- plakietka + podgląd ---------- */
  function plakietka() {
    var d = document.querySelector('#as-fab .hd-cnt'), fab = document.getElementById('as-fab');
    if (!d) return;
    var bylo = +(d.dataset.n || 0);
    d.dataset.n = CZEKA;
    d.textContent = CZEKA > 9 ? '9+' : String(CZEKA);
    d.classList.toggle('hidden', !CZEKA);
    fab.setAttribute('aria-label', 'Support chats' + (CZEKA ? ' — ' + CZEKA + ' waiting for a reply' : ''));
    if (CZEKA > bylo) { fab.classList.remove('ping'); void fab.offsetWidth; fab.classList.add('ping'); }
    if (!CZEKA) schowajPodglad();
  }
  function podglad(c) {
    // Rozmowa, którą admin ma właśnie otwartą, nie potrzebuje podglądu.
    if (!c || !c.trader_last || (OTWARTY && WIDOK === 'chat' && CHAT && CHAT.id === c.id)) return;
    var pk = document.getElementById('as-peek');
    pk.innerHTML = '<button type="button" class="hd-peek-in" onclick="asOpenChat(' + c.id + ')">'
      + '<div class="hd-av person sm">' + inicjal(c) + '</div>'
      + '<span class="hd-peek-t"><b>' + esc(kto(c)) + ' · new message</b>'
      + '<span>' + esc(c.trader_last.body) + '</span></span></button>'
      + '<button type="button" class="hd-peek-x" aria-label="Dismiss" onclick="asPeekClose()">' + ZAMKNIJ + '</button>';
    pk.hidden = false;
    requestAnimationFrame(function () { pk.classList.add('show'); });
    clearTimeout(PODGLAD_T);
    PODGLAD_T = setTimeout(schowajPodglad, 10000);
  }
  function schowajPodglad() {
    var pk = document.getElementById('as-peek');
    if (!pk || pk.hidden) return;
    pk.classList.remove('show');
    clearTimeout(PODGLAD_T);
    setTimeout(function () { if (!pk.classList.contains('show')) pk.hidden = true; }, 220);
  }

  /* ---------- widoki ---------- */
  function naglowek() {
    var av = document.getElementById('as-av'), name = document.getElementById('as-name'),
      sub = document.getElementById('as-sub');
    document.getElementById('as-back').hidden = WIDOK === 'list';
    document.getElementById('as-panel').classList.toggle('list', WIDOK === 'list');
    av.hidden = WIDOK === 'list';
    if (WIDOK === 'list') {
      name.textContent = 'Support';
      sub.textContent = CZEKA ? CZEKA + ' waiting for a reply' : 'All caught up';
      return;
    }
    av.className = 'hd-av person'; av.innerHTML = inicjal(CHAT);
    name.textContent = kto(CHAT);
    sub.textContent = (CHAT.ref ? '#' + CHAT.ref + ' · ' : '') + (CHAT.subject || '');
  }
  var STATUS = { open: ['Waiting', 'st-wait'], answered: ['Answered', 'st-ok'], closed: ['Closed', 'st-closed'] };
  function wpis(c) {
    // Czekająca rozmowa: słowa tradera (ostatnia w wątku bywa notką asystenta o przekazaniu).
    var od = c.waiting || !c.last ? '' : c.last.author === 'admin' ? 'You: '
      : c.last.author === 'ai' ? 'AI: ' : '';
    var tekst = c.waiting && c.trader_last ? c.trader_last.body : c.last ? c.last.body : c.subject;
    var st = STATUS[c.status];
    return '<button type="button" class="hd-item' + (c.waiting ? ' unread' : '') + '" onclick="asOpenChat(' + c.id + ')">'
      + '<div class="hd-av person sm">' + inicjal(c) + '</div>'
      + '<div class="hd-it"><div class="hd-it-top"><b>' + esc(kto(c)) + '</b>'
      + '<time>' + kiedy(c.last_ts) + '</time></div>'
      + '<div class="hd-it-bot"><span class="hd-prev">' + esc(od + tekst) + '</span>'
      + (st ? '<span class="hd-pill ' + st[1] + '">' + st[0] + '</span>' : '')
      + '</div></div></button>';
  }
  function rysujListe() {
    var body = document.getElementById('as-body'), foot = document.getElementById('as-foot');
    body.innerHTML = LISTA.length
      ? '<div class="hd-list">' + LISTA.map(wpis).join('') + '</div>'
      : '<div class="hd-empty">No conversations with the team.<br>Chats the AI assistant hands over land here.</div>';
    body.dataset.chat = '';
    foot.innerHTML = '<button type="button" class="hd-link" style="width:100%" onclick="asTickets()">'
      + 'Open the full ticket history</button>';
  }
  function rysujRozmowe() {
    var body = document.getElementById('as-body'), foot = document.getElementById('as-foot');
    var klucz = String(CHAT.id);
    var naDol = body.dataset.chat !== klucz || body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    body.dataset.chat = klucz;
    body.innerHTML = chatHtml(CHAT.thread || [], { me: 'admin', them: 'Trader', tz: 'Europe/Warsaw',
      names: { ai: 'AI assistant', ai_note: 'AI handover note (team only)' } });
    var zamknieta = CHAT.status === 'closed';
    // Pole odpowiedzi tylko raz — inaczej odświeżanie kasowałoby wpisywany tekst.
    if (!foot.querySelector('.chat-compose') && !zamknieta) {
      foot.innerHTML = '<div class="hd-acts"><button type="button" class="hd-link" onclick="asCloseChat()">'
        + 'Close conversation</button></div>'
        + chatCompose({ id: 'as-inp', send: 'asSend()', placeholder: 'Reply to the trader…' });
      var pole = document.getElementById('as-inp'), ost = 0, id = CHAT.id;
      // Pisanie = przejęcie: trader widzi konsultanta i „is typing…" (najwyżej co 3 s).
      if (pole) pole.addEventListener('input', function () {
        var teraz = Date.now();
        if (!pole.value.trim() || teraz - ost < 3000) return;
        ost = teraz;
        api('/api/admin/tickets/' + id + '/typing', { method: 'POST' }).catch(function () {});
      });
    } else if (zamknieta) {
      foot.innerHTML = '<p class="hd-note" style="margin:6px 0">This conversation is closed.</p>';
    }
    if (naDol) body.scrollTop = body.scrollHeight;
  }
  function rysuj() {
    if (!document.getElementById('as-body')) return;
    naglowek();
    if (WIDOK === 'list') rysujListe(); else rysujRozmowe();
  }

  /* ---------- dane ---------- */
  async function odswiez() {
    if (!zalogowany() || WYSYLA) return;
    try {
      var r = await api('/api/admin/support/chats');
      var nowa = OSTATNI !== null && r.last_id > OSTATNI;
      LISTA = r.chats || []; CZEKA = r.waiting || 0;
      // Pierwszy odczyt tylko ustawia punkt odniesienia — stare rozmowy nie udają nowych.
      if (nowa) podglad(LISTA.filter(function (c) { return c.trader_last && c.trader_last.id === r.last_id; })[0]);
      OSTATNI = Math.max(OSTATNI || 0, r.last_id || 0);
      if (OTWARTY && WIDOK === 'chat' && CHAT) {
        var t = await api('/api/admin/tickets/' + CHAT.id);
        CHAT = Object.assign({}, CHAT, t);
      }
      plakietka();
      if (OTWARTY) rysuj();
    } catch (e) { /* cicho: następny cykl spróbuje znowu */ }
  }
  function planuj() {
    clearTimeout(TIMER);
    // Otwarta rozmowa co 5 s (odpowiedzi tradera na bieżąco), w tle co 15 s.
    var ms = OTWARTY && WIDOK === 'chat' ? 5000 : 15000;
    TIMER = setTimeout(async function () {
      if (document.visibilityState === 'visible') await odswiez();
      planuj();
    }, ms);
  }

  /* ---------- akcje ---------- */
  function pokazPanel() {
    zbuduj();
    schowajPodglad();
    OTWARTY = true;
    var p = document.getElementById('as-panel');
    p.hidden = false;
    requestAnimationFrame(function () { p.classList.add('open'); });
    document.getElementById('as-fab').classList.add('on');
  }
  async function otworz() {
    pokazPanel();
    WIDOK = 'list'; CHAT = null;
    rysuj();
    await odswiez();
    planuj();
  }
  function zamknij() {
    OTWARTY = false;
    var p = document.getElementById('as-panel');
    if (!p) return;
    p.classList.remove('open');
    setTimeout(function () { if (!OTWARTY) p.hidden = true; }, 220);
    document.getElementById('as-fab').classList.remove('on');
    planuj();
  }
  async function otworzRozmowe(id) {
    if (!OTWARTY) pokazPanel();
    schowajPodglad();
    try {
      var t = await api('/api/admin/tickets/' + id);
      var c = LISTA.filter(function (x) { return x.id === id; })[0] || {};
      CHAT = Object.assign({ trader_name: c.trader_name }, t);
      WIDOK = 'chat';
      document.getElementById('as-foot').innerHTML = '';
      rysuj(); planuj();
      var inp = document.getElementById('as-inp');
      if (inp && matchMedia('(hover: hover) and (pointer: fine)').matches) inp.focus();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }
  async function doListy() {
    WIDOK = 'list'; CHAT = null;
    rysuj();
    await odswiez();
    planuj();
  }
  async function odpowiedz(zamykaj) {
    if (!CHAT || WYSYLA) return;
    var inp = document.getElementById('as-inp');
    var tekst = inp ? inp.value.trim() : '';
    if (!tekst && !zamykaj) return;
    WYSYLA = true;
    try {
      var t = await api('/api/admin/tickets/' + CHAT.id + '/reply', { method: 'POST',
        body: JSON.stringify({ message: tekst, close: !!zamykaj }) });
      CHAT = Object.assign({}, CHAT, t);
      if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true })); }
      if (zamykaj) document.getElementById('as-foot').innerHTML = '';
      if (typeof toast === 'function')
        toast(zamykaj ? 'Conversation closed.' : 'Reply sent. The trader sees it in the chat.', 'ok');
      // Widok Tickets pod spodem pokazuje te same rozmowy — niech nie kłamie.
      if (typeof VIEW !== 'undefined' && VIEW === 'tickets' && typeof go === 'function') go('tickets');
    } catch (e) {
      if (typeof toast === 'function') toast('Error: ' + e.message, 'err');
    } finally {
      WYSYLA = false;
      await odswiez();
      rysuj();
    }
  }

  window.asOpenChat = function (id) { return otworzRozmowe(+id); };
  window.asClose = zamknij;
  window.asList = doListy;
  window.asSend = function () { return odpowiedz(false); };
  window.asCloseChat = function () { return odpowiedz(true); };
  window.asPeekClose = schowajPodglad;
  window.asTickets = function () { zamknij(); if (typeof go === 'function') go('tickets'); };

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && OTWARTY) zamknij(); });
  // Klik w stronę poza oknem czatu zamyka je (jak Escape). pointerdown, nie click:
  // klik w środku, który przerysowuje okno, odpinałby cel i liczył się jako „poza".
  document.addEventListener('pointerdown', function (e) {
    if (!OTWARTY) return;
    var t = e.target;
    if (t.closest && t.closest('#as-panel, #as-fab, #as-peek')) return;
    zamknij();
  });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && zalogowany()) odswiez();
  });
  // Push na tym urządzeniu (sw.js wysyła „push" do otwartych kart) = odśwież od razu.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (e.data && e.data.type === 'push' && zalogowany()) odswiez();
    });
  }

  // Dymek dopiero po potwierdzeniu sesji admina (ME ustawia start admin-panel.js).
  var bylZalogowany = false;
  setInterval(function () {
    var teraz = zalogowany();
    if (teraz === bylZalogowany) return;
    bylZalogowany = teraz;
    zbuduj();
    document.getElementById('as-fab').classList.toggle('hidden', !teraz);
    if (teraz) { odswiez(); planuj(); } else { zamknij(); clearTimeout(TIMER); LISTA = []; CZEKA = 0; plakietka(); }
  }, 1000);
})();
