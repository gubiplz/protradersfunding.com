/* Dymek supportu w panelu admina (prawy dolny róg) — odpowiednik okienka tradera.
 *
 * Wszystkie powiadomienia supportu siedzą TUTAJ, nie w dzwonku: plakietka
 * z liczbą rozmów czekających na odpowiedź, pierścień i podgląd nowej
 * wiadomości tradera przy dymku. Push na telefon (kategoria „Support chat"
 * w ustawieniach powiadomień) prowadzi tu przez /admin?chat=<id>.
 *
 * Dwie zakładki na dole:
 *  - Inbox: kolejka jak w skrzynce helpdesku — filtry Needs reply / Answered /
 *    Closed / All z licznikami, wyszukiwarka (trader, mail, treść), czekające
 *    od najdłużej czekającego z czasem oczekiwania; rozmowa z polem odpowiedzi,
 *    wstawianiem artykułu i zamknięciem;
 *  - Articles: te same artykuły co u tradera (strona /faq) — do przeczytania,
 *    skopiowania albo wstawienia do odpowiedzi.
 *
 * Dane: GET /api/admin/support/chats (lista + `waiting` + `last_id`),
 * GET /api/admin/tickets/{id} (wątek), POST …/reply i …/typing — te same,
 * których używa widok Tickets; artykuły z GET /api/help/articles. Korzysta
 * z globali admin-panel.js (api, ME, toast, VIEW, go) i chat-kit.js (chatHtml,
 * chatCompose). Style: helpdesk.css.
 */
(function () {
  'use strict';
  var LISTA = [], CZEKA = 0, OSTATNI = null, CHAT = null, WIDOK = 'list', OTWARTY = false,
    TIMER = null, WYSYLA = false, PODGLAD_T = null;
  var ZAKLADKA = 'inbox', FILTR = null, SZUKAJ = '', ARTS = null, ART = null, SZUKAJ_ART = '', WYBOR = false;
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var utc = function (iso) { return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'); };
  var zalogowany = function () { return typeof ME !== 'undefined' && !!ME; };
  var el = function (id) { return document.getElementById(id); };

  var SVG = function (d, w) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 2)
      + '" stroke-linecap="round" stroke-linejoin="round">' + d + '</svg>';
  };
  var ZAMKNIJ = SVG('<path d="M6 6l12 12M18 6L6 18"/>', 2.2);
  var WSTECZ = SVG('<path d="M15 5l-7 7 7 7"/>', 2.2);
  var LUPA = SVG('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>', 2.2);
  var DALEJ = SVG('<path d="M9 5l7 7-7 7"/>', 2.2);
  var GOTOWE = SVG('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 2.4);
  var PELNY = SVG('<path d="M14 4h6v6M20 4l-7 7"/><path d="M10 20H4v-6M4 20l7-7"/>');
  var DOK = SVG('<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>');
  var KOPIUJ = SVG('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>');
  var T_INBOX = SVG('<path d="M4 13l2.5-7.5A1 1 0 0 1 7.4 5h9.2a1 1 0 0 1 .9.5L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M4 13h4.5l1.5 2.5h4l1.5-2.5H20"/>');
  var T_ARTS = SVG('<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z"/>');

  /* Ikona dymka jak w komunikatorach pomocy (Intercom, Crisp, Chatwoot): biały
     dymek z trzema kropkami; po otwarciu płynnie zamienia się w strzałkę w dół. */
  var FAB_IKONY = '<span class="hd-fab-ic hd-fab-chat" aria-hidden="true"><svg viewBox="0 0 28 28">'
    + '<path class="hd-fab-bub" d="M14 4.2c-6 0-10.8 4.1-10.8 9.3 0 2.7 1.3 5.1 3.4 6.8l-.8 3.9 4.4-2.3c1.2.4 2.5.6 3.8.6'
    + ' 6 0 10.8-4.1 10.8-9.2S20 4.2 14 4.2z"/><circle class="hd-fab-dot" cx="9.4" cy="13.6" r="1.55"/>'
    + '<circle class="hd-fab-dot" cx="14" cy="13.6" r="1.55"/><circle class="hd-fab-dot" cx="18.6" cy="13.6" r="1.55"/></svg></span>'
    + '<span class="hd-fab-ic hd-fab-down" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5l6 6 6-6"/></svg></span>';

  function zbuduj() {
    if (el('as-fab')) return;
    var fab = document.createElement('button');
    fab.id = 'as-fab'; fab.type = 'button'; fab.className = 'hd-fab hidden';
    fab.setAttribute('aria-label', 'Support inbox');
    fab.innerHTML = FAB_IKONY + '<span class="hd-cnt hidden" aria-hidden="true"></span>';
    fab.onclick = function () { OTWARTY ? zamknij() : otworz(); };
    var pk = document.createElement('div');
    pk.id = 'as-peek'; pk.className = 'hd-peek'; pk.hidden = true;
    pk.setAttribute('role', 'status'); pk.setAttribute('aria-live', 'polite');
    var p = document.createElement('section');
    p.id = 'as-panel'; p.className = 'hd-panel as-panel'; p.setAttribute('role', 'dialog');
    p.setAttribute('aria-label', 'Support inbox'); p.hidden = true;
    p.innerHTML = '<header class="hd-head" id="as-head"></header>'
      + '<div class="hd-body" id="as-body"></div><div class="hd-foot" id="as-foot"></div>'
      + '<nav class="hd-tabs as-tabs" id="as-tabs" aria-label="Support sections">'
      + '<button type="button" class="hd-tab" data-tab="inbox" onclick="asTab(\'inbox\')">' + T_INBOX
      + '<span>Inbox</span><i class="hd-tab-dot" hidden></i></button>'
      + '<button type="button" class="hd-tab" data-tab="articles" onclick="asTab(\'articles\')">' + T_ARTS
      + '<span>Articles</span></button></nav>';
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
  // „12m", „3h", „2d" — ile trader czeka na odpowiedź.
  function ile(iso) {
    var m = Math.max(0, Math.round((Date.now() - utc(iso)) / 60000));
    return m < 1 ? 'now' : m < 60 ? m + 'm' : m < 1440 ? Math.round(m / 60) + 'h' : Math.round(m / 1440) + 'd';
  }
  function czekaOd(c) { return (c.trader_last && c.trader_last.ts) || c.last_ts; }
  // Temat nadany automatycznie („Chat: <pierwsza wiadomość>") tylko dubluje treść.
  function temat(c) { return c && c.subject && !/^chat:/i.test(c.subject) ? c.subject : ''; }
  var X_BTN = '<button type="button" class="hd-x" aria-label="Close" onclick="asClose()">' + ZAMKNIJ + '</button>';

  /* ---------- plakietka + podgląd ---------- */
  function plakietka() {
    var d = document.querySelector('#as-fab .hd-cnt'), fab = el('as-fab');
    if (!d) return;
    var bylo = +(d.dataset.n || 0);
    d.dataset.n = CZEKA;
    d.textContent = CZEKA > 9 ? '9+' : String(CZEKA);
    d.classList.toggle('hidden', !CZEKA);
    fab.setAttribute('aria-label', 'Support inbox' + (CZEKA ? ' — ' + CZEKA + ' waiting for a reply' : ''));
    if (CZEKA > bylo) { fab.classList.remove('ping'); void fab.offsetWidth; fab.classList.add('ping'); }
    var td = document.querySelector('#as-tabs .hd-tab-dot');
    if (td) td.hidden = !CZEKA;
    if (!CZEKA) schowajPodglad();
  }
  function podglad(c) {
    // Rozmowa, którą admin ma właśnie otwartą, nie potrzebuje podglądu.
    if (!c || !c.trader_last || (OTWARTY && WIDOK === 'chat' && CHAT && CHAT.id === c.id)) return;
    var pk = el('as-peek');
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
    var pk = el('as-peek');
    if (!pk || pk.hidden) return;
    pk.classList.remove('show');
    clearTimeout(PODGLAD_T);
    setTimeout(function () { if (!pk.classList.contains('show')) pk.hidden = true; }, 220);
  }

  /* ================= INBOX ================= */
  // Filtry kolejki; domyślnie „Needs reply", gdy coś czeka — inaczej wszystko.
  var FILTRY = [['waiting', 'Needs reply'], ['answered', 'Answered'], ['closed', 'Closed'], ['all', 'All']];
  function pasuje(c, f) {
    return f === 'all' || (f === 'waiting' ? c.waiting : f === 'answered' ? (!c.waiting && c.status !== 'closed')
      : c.status === 'closed');
  }
  var STATUS = { answered: ['Answered', 'st-ok'], closed: ['Closed', 'st-closed'] };
  function wpis(c) {
    // Czekająca rozmowa: słowa tradera (ostatnia w wątku bywa notką asystenta o przekazaniu).
    var od = c.waiting || !c.last ? '' : c.last.author === 'admin' ? 'You: '
      : c.last.author === 'ai' ? 'AI: ' : '';
    var tekst = c.waiting && c.trader_last ? c.trader_last.body : c.last ? c.last.body : c.subject;
    var st = STATUS[c.status], pill;
    if (c.waiting) {
      var minut = (Date.now() - utc(czekaOd(c))) / 60000;
      pill = '<span class="hd-pill ' + (minut >= 60 ? 'st-late' : 'st-wait') + '" title="Waiting for a reply">'
        + ile(czekaOd(c)) + '</span>';
    } else pill = st ? '<span class="hd-pill ' + st[1] + '">' + st[0] + '</span>' : '';
    return '<button type="button" class="hd-item' + (c.waiting ? ' unread' : '') + '" onclick="asOpenChat(' + c.id + ')">'
      + '<div class="hd-av person sm">' + inicjal(c) + '</div>'
      + '<div class="hd-it"><div class="hd-it-top"><b>' + esc(kto(c)) + '</b>'
      + '<time>' + kiedy(c.last_ts) + '</time></div>'
      + (temat(c) ? '<div class="as-subj">' + esc(temat(c)) + '</div>' : '')
      + '<div class="hd-it-bot"><span class="hd-prev">' + esc(od + tekst) + '</span>' + pill
      + '</div></div></button>';
  }
  function rysujListe() {
    var head = el('as-head');
    if (!FILTR) FILTR = CZEKA ? 'waiting' : 'all';
    var najdluzej = LISTA.filter(function (c) { return c.waiting; })[0];
    var pod = CZEKA ? CZEKA + ' waiting · longest ' + ile(czekaOd(najdluzej)) : 'All caught up';
    // Pole wyszukiwania tylko raz — przerysowanie z pollingu kasowałoby fokus.
    if (!head.querySelector('#as-q')) {
      head.innerHTML = '<div class="hd-ttl"><b>Inbox</b><span id="as-sub"></span></div>'
        + '<button type="button" class="hd-x" aria-label="Open the Tickets view" title="Open the Tickets view" onclick="asTickets()">'
        + PELNY + '</button>' + X_BTN
        + '<label class="hd-search"><input id="as-q" type="search" placeholder="Search name, email or message"'
        + ' autocomplete="off" aria-label="Search conversations">' + LUPA + '</label>'
        + '<div class="as-filters" id="as-filters" role="tablist"></div>';
      var q = el('as-q');
      q.value = SZUKAJ;
      q.addEventListener('input', function () { SZUKAJ = q.value; listaRozmow(); });
    }
    el('as-sub').textContent = pod;
    el('as-filters').innerHTML = FILTRY.map(function (f) {
      var n = LISTA.filter(function (c) { return pasuje(c, f[0]); }).length;
      return '<button type="button" role="tab" class="as-f' + (FILTR === f[0] ? ' on' : '') + '" aria-selected="'
        + (FILTR === f[0]) + '" onclick="asFilter(\'' + f[0] + '\')">' + f[1]
        + (f[0] !== 'all' && n ? '<span>' + n + '</span>' : '') + '</button>';
    }).join('');
    listaRozmow();
    el('as-foot').innerHTML = '';
  }
  function listaRozmow() {
    var body = el('as-body'), q = SZUKAJ.trim().toLowerCase();
    var lista = LISTA.filter(function (c) {
      if (!pasuje(c, FILTR)) return false;
      if (!q) return true;
      return [c.trader_name, c.trader_email, c.subject, c.ref, c.last && c.last.body, c.trader_last && c.trader_last.body]
        .join(' ').toLowerCase().indexOf(q) >= 0;
    });
    body.dataset.chat = '';
    body.innerHTML = lista.length
      ? '<div class="hd-list">' + lista.map(wpis).join('') + '</div>'
      : '<div class="hd-guest">' + (q ? LUPA : GOTOWE) + '<h3>'
        + (q ? 'No conversations match' : FILTR === 'waiting' ? 'All caught up' : 'Nothing here') + '</h3><p>'
        + (q ? 'Try a different name, email or phrase.'
          : FILTR === 'waiting' ? 'No trader is waiting for a reply. Chats the AI assistant hands over land here.'
            : 'Conversations from the last 7 days show up here.') + '</p></div>';
  }

  /* ---------- rozmowa ---------- */
  function rysujRozmowe() {
    var head = el('as-head'), body = el('as-body'), foot = el('as-foot');
    var zamknieta = CHAT.status === 'closed';
    head.innerHTML = '<button type="button" class="hd-back" aria-label="Inbox" onclick="asList()">' + WSTECZ + '</button>'
      + '<div class="hd-av person">' + inicjal(CHAT) + '</div>'
      + '<div class="hd-ttl"><b>' + esc(kto(CHAT)) + '</b><span>'
      + esc([CHAT.trader_email !== kto(CHAT) ? CHAT.trader_email : '', CHAT.ref ? '#' + CHAT.ref : ''].filter(Boolean).join(' · '))
      + '</span></div>' + X_BTN;
    var klucz = String(CHAT.id);
    var naDol = body.dataset.chat !== klucz || body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    body.dataset.chat = klucz;
    body.innerHTML = (temat(CHAT) ? '<div class="as-topic"><b>' + esc(temat(CHAT)) + '</b>'
      + (CHAT.category ? '<span>' + esc(String(CHAT.category).replace(/_/g, ' ')) + '</span>' : '') + '</div>' : '')
      + chatHtml(CHAT.thread || [], { me: 'admin', them: 'Trader', tz: 'Europe/Warsaw',
        names: { ai: 'AI assistant', ai_note: 'AI handover note (team only)' } });
    // Pole odpowiedzi tylko raz — inaczej odświeżanie kasowałoby wpisywany tekst.
    if (!foot.querySelector('.chat-compose') && !zamknieta) {
      foot.innerHTML = '<div class="hd-acts as-acts">'
        + '<button type="button" class="hd-link" onclick="asPick()">' + DOK + 'Insert article</button>'
        + '<button type="button" class="hd-link" onclick="asCloseChat()">' + GOTOWE + 'Close conversation</button></div>'
        + '<div class="as-pick" id="as-pick" hidden></div>'
        + chatCompose({ id: 'as-inp', send: 'asSend()', placeholder: 'Reply to ' + kto(CHAT).split(' ')[0] + '…' });
      var pole = el('as-inp'), ost = 0, id = CHAT.id;
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

  /* ---------- wybór artykułu do odpowiedzi ---------- */
  function tekstArt(a) {
    var d = document.createElement('div'); d.innerHTML = a.body;
    return (d.textContent || '').replace(/\s+/g, ' ').trim();
  }
  function wstawArt(id) {
    var a = (ARTS || []).filter(function (x) { return x.id === id; })[0], inp = el('as-inp');
    if (!a || !inp) return;
    var tekst = tekstArt(a) + '\n\nMore in our FAQ: ' + location.origin + '/faq';
    inp.value = inp.value.trim() ? inp.value.replace(/\s+$/, '') + '\n\n' + tekst : tekst;
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    WYBOR = false; rysujWybor();
    inp.focus();
  }
  function rysujWybor() {
    var box = el('as-pick');
    if (!box) return;
    box.hidden = !WYBOR;
    if (!WYBOR) { box.innerHTML = ''; return; }
    if (!ARTS) { box.innerHTML = '<div class="hd-skel"><i></i><i></i></div>'; artykuly().then(rysujWybor); return; }
    if (!box.querySelector('input')) {
      box.innerHTML = '<label class="hd-search"><input id="as-pick-q" type="search" placeholder="Find an article"'
        + ' autocomplete="off" aria-label="Find an article">' + LUPA + '</label><div class="as-pick-list" id="as-pick-list"></div>';
      el('as-pick-q').addEventListener('input', rysujWyborListe);
      el('as-pick-q').focus();
    }
    rysujWyborListe();
  }
  function rysujWyborListe() {
    var q = ((el('as-pick-q') || {}).value || '').trim().toLowerCase();
    var lista = ARTS.filter(function (a) { return !q || (a.title + ' ' + tekstArt(a)).toLowerCase().indexOf(q) >= 0; });
    el('as-pick-list').innerHTML = lista.length ? lista.slice(0, 30).map(function (a) {
      return '<button type="button" class="hd-art" onclick="asInsertArticle(\'' + esc(a.id) + '\')"><span><b>'
        + esc(a.title) + '</b><small>' + esc(a.category) + '</small></span>' + DALEJ + '</button>';
    }).join('') : '<p class="hd-note" style="padding:10px">No article matches.</p>';
  }

  /* ================= ARTICLES ================= */
  function artykuly() {
    if (ARTS) return Promise.resolve(ARTS);
    return fetch('/api/help/articles').then(function (r) { return r.ok ? r.json() : { articles: [] }; })
      .then(function (d) { ARTS = d.articles || []; return ARTS; }).catch(function () { return []; });
  }
  function rysujArtykuly() {
    var head = el('as-head'), body = el('as-body');
    el('as-foot').innerHTML = '';
    if (ART) {
      var a = (ARTS || []).filter(function (x) { return x.id === ART; })[0];
      head.innerHTML = '<button type="button" class="hd-back" aria-label="All articles" onclick="asArticle(null)">'
        + WSTECZ + '</button><div class="hd-ttl"><b>Article</b></div>' + X_BTN;
      body.innerHTML = a ? '<article class="hd-article"><small>' + esc(a.category) + '</small><h2>' + esc(a.title)
        + '</h2><div class="hd-article-body">' + a.body + '</div></article>'
        + '<div class="as-art-acts"><button type="button" class="hd-new-btn" onclick="asCopyArticle(\'' + esc(a.id) + '\')">'
        + KOPIUJ + 'Copy answer</button></div>'
        + '<p class="hd-note" style="margin-top:10px">Same text the trader sees in Help. To change it, edit the FAQ page.</p>'
        : '<div class="hd-empty">This article is no longer available.</div>';
      body.scrollTop = 0;
      return;
    }
    if (!head.querySelector('#as-aq')) {
      head.innerHTML = '<div class="hd-ttl"><b>Articles</b><span>What traders see in Help</span></div>' + X_BTN
        + '<label class="hd-search"><input id="as-aq" type="search" placeholder="Search articles" autocomplete="off"'
        + ' aria-label="Search articles">' + LUPA + '</label>';
      var q = el('as-aq');
      q.value = SZUKAJ_ART;
      q.addEventListener('input', function () { SZUKAJ_ART = q.value; listaArtykulow(); });
    }
    listaArtykulow();
    if (!ARTS) artykuly().then(function () { if (OTWARTY && ZAKLADKA === 'articles') listaArtykulow(); });
  }
  function listaArtykulow() {
    var body = el('as-body');
    if (!ARTS) { body.innerHTML = '<div class="hd-skel"><i></i><i></i><i></i></div>'; return; }
    var q = SZUKAJ_ART.trim().toLowerCase(), grupy = [], po = {};
    ARTS.forEach(function (a) {
      if (q && (a.title + ' ' + tekstArt(a)).toLowerCase().indexOf(q) < 0) return;
      if (!po[a.category]) { po[a.category] = []; grupy.push(a.category); }
      po[a.category].push(a);
    });
    body.innerHTML = grupy.length ? grupy.map(function (g) {
      return '<div class="hd-group"><h3>' + esc(g) + '<span>' + po[g].length + '</span></h3>'
        + po[g].map(function (a) {
          return '<button type="button" class="hd-art" onclick="asArticle(\'' + esc(a.id) + '\')"><span><b>'
            + esc(a.title) + '</b></span>' + DALEJ + '</button>';
        }).join('') + '</div>';
    }).join('') : '<div class="hd-empty">No articles match.</div>';
  }

  function rysuj() {
    var p = el('as-panel');
    if (!p || !el('as-body')) return;
    var wRozmowie = ZAKLADKA === 'inbox' && WIDOK === 'chat' && CHAT;
    p.dataset.tab = ZAKLADKA;
    p.classList.toggle('in-chat', !!wRozmowie);
    document.querySelectorAll('#as-tabs .hd-tab').forEach(function (b) {
      b.classList.toggle('on', b.dataset.tab === ZAKLADKA);
    });
    var head = el('as-head');
    // Nagłówek z polem wyszukiwania innej zakładki — do wymiany.
    if ((ZAKLADKA !== 'inbox' || wRozmowie) && head.querySelector('#as-q')) head.innerHTML = '';
    if ((ZAKLADKA !== 'articles' || ART) && head.querySelector('#as-aq')) head.innerHTML = '';
    if (!wRozmowie && el('as-foot').querySelector('.chat-compose')) el('as-foot').innerHTML = '';
    if (ZAKLADKA === 'articles') rysujArtykuly();
    else if (wRozmowie) rysujRozmowe();
    else rysujListe();
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
      if (OTWARTY && ZAKLADKA === 'inbox' && WIDOK === 'chat' && CHAT) {
        var t = await api('/api/admin/tickets/' + CHAT.id);
        CHAT = Object.assign({}, CHAT, t);
      }
      plakietka();
      // Artykuły nie zależą od rozmów — bez przerysowania (czytany tekst i wyszukiwarka zostają).
      if (OTWARTY && ZAKLADKA === 'inbox') rysuj();
    } catch (e) { /* cicho: następny cykl spróbuje znowu */ }
  }
  function planuj() {
    clearTimeout(TIMER);
    // Otwarta rozmowa co 5 s (odpowiedzi tradera na bieżąco), w tle co 15 s.
    var ms = OTWARTY && ZAKLADKA === 'inbox' && WIDOK === 'chat' ? 5000 : 15000;
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
    var p = el('as-panel');
    p.hidden = false;
    requestAnimationFrame(function () { p.classList.add('open'); });
    el('as-fab').classList.add('on');
  }
  async function otworz() {
    pokazPanel();
    ZAKLADKA = 'inbox'; WIDOK = 'list'; CHAT = null;
    // Po każdym otwarciu filtr wraca do „Needs reply", jeśli coś czeka.
    FILTR = CZEKA ? 'waiting' : 'all';
    rysuj();
    await odswiez();
    planuj();
  }
  function zamknij() {
    OTWARTY = false;
    var p = el('as-panel');
    if (!p) return;
    p.classList.remove('open');
    setTimeout(function () { if (!OTWARTY) p.hidden = true; }, 220);
    el('as-fab').classList.remove('on');
    planuj();
  }
  async function otworzRozmowe(id) {
    if (!OTWARTY) pokazPanel();
    schowajPodglad();
    try {
      var t = await api('/api/admin/tickets/' + id);
      var c = LISTA.filter(function (x) { return x.id === id; })[0] || {};
      CHAT = Object.assign({ trader_name: c.trader_name }, t);
      ZAKLADKA = 'inbox'; WIDOK = 'chat'; WYBOR = false;
      el('as-foot').innerHTML = '';
      rysuj(); planuj();
      var inp = el('as-inp');
      if (inp && matchMedia('(hover: hover) and (pointer: fine)').matches) inp.focus();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }
  async function doListy() {
    ZAKLADKA = 'inbox'; WIDOK = 'list'; CHAT = null;
    rysuj();
    await odswiez();
    planuj();
  }
  async function odpowiedz(zamykaj) {
    if (!CHAT || WYSYLA) return;
    var inp = el('as-inp');
    var tekst = inp ? inp.value.trim() : '';
    if (!tekst && !zamykaj) return;
    WYSYLA = true;
    try {
      var t = await api('/api/admin/tickets/' + CHAT.id + '/reply', { method: 'POST',
        body: JSON.stringify({ message: tekst, close: !!zamykaj }) });
      CHAT = Object.assign({}, CHAT, t);
      if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true })); }
      if (zamykaj) el('as-foot').innerHTML = '';
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
      // Zamknięta rozmowa = gotowe: wróć do kolejki po następną.
      if (zamykaj) doListy();
    }
  }

  window.asOpenChat = function (id) { return otworzRozmowe(+id); };
  window.asClose = zamknij;
  window.asList = doListy;
  window.asSend = function () { return odpowiedz(false); };
  window.asCloseChat = function () { return odpowiedz(true); };
  window.asPeekClose = schowajPodglad;
  window.asTickets = function () { zamknij(); if (typeof go === 'function') go('tickets'); };
  window.asFilter = function (f) { FILTR = f; rysuj(); };
  window.asTab = function (k) {
    ZAKLADKA = k; ART = null;
    if (k === 'inbox') { WIDOK = 'list'; CHAT = null; }
    rysuj(); planuj();
    if (k === 'inbox') odswiez();
  };
  window.asArticle = function (id) { ZAKLADKA = 'articles'; ART = id || null; rysuj(); };
  window.asPick = function () { WYBOR = !WYBOR; rysujWybor(); };
  window.asInsertArticle = wstawArt;
  window.asCopyArticle = function (id) {
    var a = (ARTS || []).filter(function (x) { return x.id === id; })[0];
    if (!a) return;
    var ok = function () { if (typeof toast === 'function') toast('Answer copied.', 'ok'); };
    try { navigator.clipboard.writeText(tekstArt(a)).then(ok, function () {}); } catch (e) { /* bez schowka */ }
  };

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
    el('as-fab').classList.toggle('hidden', !teraz);
    if (teraz) { odswiez(); planuj(); } else { zamknij(); clearTimeout(TIMER); LISTA = []; CZEKA = 0; plakietka(); }
  }, 1000);
})();
