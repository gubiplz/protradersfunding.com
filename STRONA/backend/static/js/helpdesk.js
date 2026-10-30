/* Okienko pomocy w prawym dolnym rogu — portal i strona publiczna.
 *
 * Trzy zakładki na dole, jak w komunikatorach pomocy:
 *  - Home: powitanie, ostatnia rozmowa / „Send us a message", link do FAQ
 *    i wyszukiwarka z popularnymi artykułami;
 *  - Help: wszystkie artykuły ze strony /faq (GET /api/help/articles),
 *    pogrupowane kategoriami, z wyszukiwaniem i widokiem artykułu;
 *  - Messages: lista rozmów i rozmowa. Najpierw odpowiada asystent AI (FAQ,
 *    platforma, zasady); gdy nie pomoże — albo temat dotyczy pieniędzy, wyniku
 *    konta czy reklamacji — rozmowa idzie do zespołu i dalej odpisuje człowiek
 *    w tym samym okienku. Logika i granice są na serwerze (app/helpdesk.py,
 *    /api/me/chat*); tu tylko widok.
 *
 * Niezalogowany gość (strona publiczna, ekran logowania portalu) widzi Home
 * i Help; Messages prosi o zalogowanie. Wszystkie napisy po angielsku — cała
 * strona jest po angielsku.
 *
 * W portalu korzysta z globali portal-app.js (ME, H) i chat-kit.js (chatHtml,
 * chatCompose). Własny fetch zamiast api(): odpowiedź modelu bywa dłuższa niż
 * 15 s limitu api().
 */
(function () {
  'use strict';
  var ZAKLADKA = 'home', ART = null, SZUKAJ = '', ARTS = null, ARTS_P = null, MAIL = '';
  var WIDOK = 'list', LISTA = [], CHAT = null, ASSIST = true, OTWARTY = false, CZEKA = false, TIMER = null;
  var AGENT = { name: 'Support', team: 'Support team', online: false };
  var KLUCZ_WIDZIANE = 'pf_hd_seen2';
  var NAZWA_AI = 'PTF AI Assistant';   // „AI" w nazwie: trader wie, że to nie człowiek
  // Strona publiczna (base.html: data-hd-public) — bez portalu pod spodem, więc bez
  // jego dolnego paska zakładek. Nazwa marki z szablonu (data-site).
  var SKRYPT = document.currentScript;
  var PUBLICZNA = !!(SKRYPT && SKRYPT.hasAttribute('data-hd-public'));
  var MARKA = (SKRYPT && SKRYPT.dataset.site) || 'Pro Traders Funding';
  /* Popularne artykuły na Home — po początku tytułu z /faq. Brak = kolejne z listy. */
  var POPULARNE = ['how do i start', 'how is the daily loss', 'how do payouts work', 'can i use my own mt5'];
  /* Gotowe pytania w nowej rozmowie — tylko tematy, na które asystent odpowiada
     sam (FAQ, platforma, zasady). Klik wysyła pytanie od razu. */
  var PYTANIA = [
    'How do I log in to MT5?',
    'How is the daily loss limit calculated?',
    'Can I hold trades over the weekend or trade news?',
    'What counts as a trading day?',
    'How does KYC verification work?',
    'Can I run multiple challenges at once?',
  ];
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  // Baza zapisuje nagie UTC (bez strefy) — tak jak w chat-kit.js.
  var utc = function (iso) {
    var s = String(iso || '').replace(' ', 'T');
    return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
  };
  var zalogowany = function () { return typeof ME !== 'undefined' && !!ME; };
  var el = function (id) { return document.getElementById(id); };

  async function zapytaj(path, opts) {
    var ctl = new AbortController(), tm = setTimeout(function () { ctl.abort(); }, 120000);
    try {
      var r = await fetch(path, Object.assign({ headers: H(), signal: ctl.signal }, opts || {}));
      if (!r.ok) throw new Error((await r.json().catch(function () { return {}; })).detail || r.status);
      return await r.json();
    } finally { clearTimeout(tm); }
  }
  function meta(r) {
    if ('assistant' in r) ASSIST = r.assistant;
    if (r.agent) AGENT = r.agent;
  }

  var SVG = function (d, w) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (w || 2)
      + '" stroke-linecap="round" stroke-linejoin="round">' + d + '</svg>';
  };
  var IKONA = SVG('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>');
  var ISKRA = SVG('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'
    + '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>');
  var ZESPOL = SVG('<path d="M17 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-3A3.5 3.5 0 0 0 7 18.5V20"/><circle cx="12" cy="8.5" r="3.5"/>');
  var ZAMKNIJ = SVG('<path d="M6 6l12 12M18 6L6 18"/>', 2.2);
  var WSTECZ = SVG('<path d="M15 5l-7 7 7 7"/>', 2.2);
  var PLUS = SVG('<path d="M12 5v14M5 12h14"/>', 2.4);
  var LUPA = SVG('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>', 2.2);
  var DALEJ = SVG('<path d="M9 5l7 7-7 7"/>', 2.2);
  var ZEWN = SVG('<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>');
  var WYSLIJ = SVG('<path d="M5 12h14M13 6l6 6-6 6"/>', 2.2);
  var T_HOME = SVG('<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>');
  var T_HELP = SVG('<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.7"/><path d="M12 17h.01"/>');
  var T_MSG = SVG('<path d="M5 5h14a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-4 3v-3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/><path d="M8 9.5h8M8 12.5h5"/>');

  /* Ikona dymka: ciemna szklana tarcza z białym dymkiem cienką kreską (jak karty
     portalu); po otwarciu płynnie zamienia się w strzałkę w dół. */
  var FAB_IKONY = '<span class="hd-fab-ic hd-fab-chat" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"'
    + ' stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 4.5c-4.7 0-8.5 3.2-8.5 7.2 0 2.1 1 4 2.7 5.3L5.5 20.5l4-1.9c.8.2 1.6.3 2.5.3 4.7 0 8.5-3.2 8.5-7.2S16.7 4.5 12 4.5z"/>'
    + '<path d="M8.6 11.7h6.8M8.6 14.4h4"/></svg></span>'
    + '<span class="hd-fab-ic hd-fab-down" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5l6 6 6-6"/></svg></span>';

  function zbuduj() {
    if (el('hd-fab')) return;
    var fab = document.createElement('button');
    fab.id = 'hd-fab'; fab.type = 'button'; fab.className = 'hd-fab hidden' + (PUBLICZNA ? ' hd-public' : '');
    fab.setAttribute('aria-label', 'Help & support');
    fab.innerHTML = FAB_IKONY + '<span class="hd-cnt hidden" aria-hidden="true"></span>';
    fab.onclick = function () { OTWARTY ? zamknij() : otworz(); };
    var p = document.createElement('section');
    p.id = 'hd-panel'; p.className = 'hd-panel' + (PUBLICZNA ? ' hd-public' : ''); p.setAttribute('role', 'dialog');
    p.setAttribute('aria-label', 'Help & support'); p.hidden = true;
    p.innerHTML = '<header class="hd-head" id="hd-head"></header>'
      + '<div class="hd-body" id="hd-body"></div><div class="hd-foot" id="hd-foot"></div>'
      + '<nav class="hd-tabs" id="hd-tabs" aria-label="Help sections">'
      + tab('home', 'Home', T_HOME) + tab('help', 'Help', T_HELP) + tab('messages', 'Messages', T_MSG)
      + '</nav>';
    // Podgląd nowej odpowiedzi zespołu przy dymku (jak w komunikatorze): klik
    // otwiera rozmowę, krzyżyk chowa podgląd — plakietka z liczbą zostaje.
    var pk = document.createElement('div');
    pk.id = 'hd-peek'; pk.className = 'hd-peek' + (PUBLICZNA ? ' hd-public' : ''); pk.hidden = true;
    pk.setAttribute('role', 'status'); pk.setAttribute('aria-live', 'polite');
    document.body.appendChild(fab);
    document.body.appendChild(pk);
    document.body.appendChild(p);
  }
  function tab(k, label, ikona) {
    return '<button type="button" class="hd-tab" data-tab="' + k + '" onclick="hdTab(\'' + k + '\')">'
      + ikona + '<span>' + label + '</span>' + (k === 'messages' ? '<i class="hd-tab-dot" hidden></i>' : '') + '</button>';
  }

  /* ---------- artykuły (strona /faq) ---------- */
  function artykuly() {
    if (ARTS) return Promise.resolve(ARTS);
    if (!ARTS_P) {
      ARTS_P = fetch('/api/help/articles').then(function (r) { return r.ok ? r.json() : { articles: [] }; })
        .then(function (d) { ARTS = d.articles || []; MAIL = d.support_email || ''; return ARTS; })
        .catch(function () { ARTS_P = null; return []; });
    }
    return ARTS_P;
  }
  function tekstArt(a) {
    var d = document.createElement('div'); d.innerHTML = a.body;
    return (d.textContent || '').toLowerCase();
  }
  function popularne() {
    var lista = ARTS || [], out = [];
    POPULARNE.forEach(function (k) {
      var a = lista.filter(function (x) { return x.title.toLowerCase().indexOf(k) === 0; })[0];
      if (a && out.indexOf(a) < 0) out.push(a);
    });
    for (var i = 0; out.length < 4 && i < lista.length; i++) if (out.indexOf(lista[i]) < 0) out.push(lista[i]);
    return out;
  }
  function wierszArt(a, z) {
    return '<button type="button" class="hd-art" onclick="hdArticle(\'' + esc(a.id) + '\')">'
      + '<span><b>' + esc(a.title) + '</b>' + (z ? '<small>' + esc(a.category) + '</small>' : '') + '</span>'
      + DALEJ + '</button>';
  }

  /* ---------- nieprzeczytane: odpowiedź zespołu, której trader nie otworzył ---------- */
  function widziane() {
    try { return JSON.parse(localStorage.getItem(KLUCZ_WIDZIANE) || '{}'); } catch (e) { return {}; }
  }
  function nieprzeczytana(c) {
    return !!(c.last && c.last.author === 'admin' && c.last.ts > (widziane()[c.id] || ''));
  }
  function oznaczWidziane(c) {
    if (!c || !c.thread || !c.thread.length) return;
    var w = widziane();
    w[c.id] = c.thread[c.thread.length - 1].ts;
    try { localStorage.setItem(KLUCZ_WIDZIANE, JSON.stringify(w)); } catch (e) { /* tryb prywatny */ }
  }
  /* Plakietka na dymku i kropka na zakładce Messages: liczba rozmów z nieprzeczytaną
     odpowiedzią zespołu. Jedyne miejsce powiadomień supportu (dzwonek ich nie dostaje). */
  function kropka() {
    var d = document.querySelector('#hd-fab .hd-cnt'), fab = el('hd-fab');
    if (!d) return;
    var wszystkie = LISTA.filter(nieprzeczytana).length;
    var n = OTWARTY ? 0 : wszystkie;
    var bylo = +(d.dataset.n || 0);
    d.dataset.n = n;
    d.textContent = n > 9 ? '9+' : String(n);
    d.classList.toggle('hidden', !n);
    fab.setAttribute('aria-label', 'Help & support' + (n ? ' — ' + n + ' new repl' + (n > 1 ? 'ies' : 'y') : ''));
    if (n > bylo) { fab.classList.remove('ping'); void fab.offsetWidth; fab.classList.add('ping'); }
    var td = document.querySelector('#hd-tabs .hd-tab-dot');
    if (td) td.hidden = !wszystkie;
    if (!n) schowajPodglad();
  }

  /* ---------- podgląd nowej wiadomości przy dymku ----------
     Raz na wiadomość: pamiętamy (w tej karcie) znacznik czasu ostatniej
     pokazanej. Otwarte okno albo przeczytana rozmowa = bez podglądu. */
  var POKAZANE = {}, PODGLAD_T = null;
  try { POKAZANE = JSON.parse(sessionStorage.getItem('pf_hd_peeked') || '{}'); } catch (e) { POKAZANE = {}; }
  function podglad() {
    if (OTWARTY) return;
    var c = LISTA.filter(function (x) { return nieprzeczytana(x) && POKAZANE[x.id] !== x.last.ts; })[0];
    if (!c) return;
    POKAZANE[c.id] = c.last.ts;
    try { sessionStorage.setItem('pf_hd_peeked', JSON.stringify(POKAZANE)); } catch (e) { /* tryb prywatny */ }
    var pk = el('hd-peek');
    pk.innerHTML = '<button type="button" class="hd-peek-in" onclick="hdOpenPeek(' + c.id + ')">'
      + awatar('person', true)
      + '<span class="hd-peek-t"><b>' + esc(AGENT.name) + ' · ' + esc(AGENT.team) + '</b>'
      + '<span>' + esc(c.last.body) + '</span></span></button>'
      + '<button type="button" class="hd-peek-x" aria-label="Dismiss" onclick="hdPeekClose()">' + ZAMKNIJ + '</button>';
    pk.hidden = false;
    requestAnimationFrame(function () { pk.classList.add('show'); });
    clearTimeout(PODGLAD_T);
    PODGLAD_T = setTimeout(schowajPodglad, 9000);
  }
  function schowajPodglad() {
    var pk = el('hd-peek');
    if (!pk || pk.hidden) return;
    pk.classList.remove('show');
    clearTimeout(PODGLAD_T);
    setTimeout(function () { if (!pk.classList.contains('show')) pk.hidden = true; }, 220);
  }

  /* ---------- awatary: asystent AI, kolejka zespołu, konsultant ---------- */
  function awatar(rodzaj, maly) {
    // Obecność pokazujemy tylko przy konsultancie, który przejął rozmowę.
    var on = AGENT.online && (rodzaj === 'person' || rodzaj === 'team') ? '<span class="hd-online" title="Online"></span>' : '';
    var w = rodzaj === 'ai' ? ISKRA
      : rodzaj === 'person' ? esc((AGENT.name || 'S').charAt(0).toUpperCase()) + on
        : rodzaj === 'team' ? ZESPOL + on : IKONA;
    return '<div class="hd-av ' + rodzaj + (maly ? ' sm' : '') + '">' + w + '</div>';
  }
  function rodzajRozmowy(c) {
    if (!c || (c.mode === 'assistant' && ASSIST)) return 'ai';
    return c.taken_over ? 'person' : 'team';
  }
  var X_BTN = '<button type="button" class="hd-x" aria-label="Close" onclick="hdClose()">' + ZAMKNIJ + '</button>';
  var BACK = function (fn, label) {
    return '<button type="button" class="hd-back" aria-label="' + label + '" onclick="' + fn + '">' + WSTECZ + '</button>';
  };
  function kiedy(iso) {
    var d = utc(iso), teraz = new Date();
    if (d.toDateString() === teraz.toDateString())
      return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === new Date(teraz - 864e5).toDateString()) return 'Yesterday';
    if (teraz - d < 6 * 864e5) return d.toLocaleDateString('en-US', { weekday: 'short' });
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  /* ================= HOME ================= */
  function rysujHome() {
    var head = el('hd-head'), body = el('hd-body'), foot = el('hd-foot');
    head.innerHTML = '<img class="hd-logo" src="/static/img/logo.png" alt="" width="28" height="28">'
      + '<span class="hd-brand">' + esc(MARKA) + '</span>' + X_BTN;
    var imie = zalogowany() ? (ME.first_name || (ME.full_name || '').split(' ')[0] || '') : '';
    var ostatnia = zalogowany() ? LISTA[0] : null;
    var karta = ostatnia
      ? '<button type="button" class="hd-card hd-recent" onclick="hdOpen(' + ostatnia.id + ')">'
        + '<small>Recent message</small><span class="hd-recent-row">' + awatar(rodzajRozmowy(ostatnia), true)
        + '<span class="hd-it"><span class="hd-it-top"><b>' + esc(ostatnia.title || 'Conversation') + '</b>'
        + '<time>' + kiedy(ostatnia.last_ts) + '</time></span><span class="hd-prev">'
        + esc(ostatnia.last ? ostatnia.last.body : '') + '</span></span>'
        + (nieprzeczytana(ostatnia) ? '<span class="hd-new"></span>' : '') + '</span></button>'
      : '';
    var wiadomosc = '<button type="button" class="hd-card hd-row" onclick="hdNewChat()">'
      + '<span><b>Send us a message</b><small>' + (zalogowany()
        ? 'The AI assistant replies instantly, our team within hours'
        : 'Sign in to chat with our assistant and support team') + '</small></span>'
      + '<span class="hd-ic">' + WYSLIJ + '</span></button>';
    var pop = ARTS ? popularne() : [];
    body.innerHTML = '<div class="hd-hero"><p>Hi' + (imie ? ' ' + esc(imie) : '') + ' 👋</p><h2>How can we help?</h2></div>'
      + karta + wiadomosc
      + '<a class="hd-card hd-row" href="/faq"' + (PUBLICZNA ? '' : ' target="_blank" rel="noopener"') + '>'
      + '<span><b>Read our FAQ</b></span><span class="hd-ic">' + ZEWN + '</span></a>'
      + '<div class="hd-card hd-searchcard">'
      + '<button type="button" class="hd-search-btn" onclick="hdTab(\'help\',true)"><span>Search for help</span>' + LUPA + '</button>'
      + (pop.length ? pop.map(function (a) { return wierszArt(a, false); }).join('')
        : '<div class="hd-skel"><i></i><i></i><i></i></div>')
      + '</div>';
    body.scrollTop = 0;
    foot.innerHTML = '';
    if (!ARTS) artykuly().then(function () { if (OTWARTY && ZAKLADKA === 'home') rysuj(); });
  }

  /* ================= HELP ================= */
  function rysujHelp() {
    var head = el('hd-head'), body = el('hd-body'), foot = el('hd-foot');
    foot.innerHTML = '';
    if (ART) {
      var a = (ARTS || []).filter(function (x) { return x.id === ART; })[0];
      head.innerHTML = BACK('hdArticle(null)', 'All articles') + '<div class="hd-ttl"><b>Help</b></div>' + X_BTN;
      body.innerHTML = a
        ? '<article class="hd-article"><small>' + esc(a.category) + '</small><h2>' + esc(a.title) + '</h2>'
          + '<div class="hd-article-body">' + a.body + '</div></article>'
          + '<div class="hd-card hd-more"><b>Still need help?</b>'
          + '<button type="button" class="hd-new-btn" onclick="hdNewChat()">' + T_MSG + 'Send us a message</button></div>'
        : '<div class="hd-empty">This article is no longer available.</div>';
      body.scrollTop = 0;
      return;
    }
    // Pole wyszukiwania tylko raz — inaczej przerysowanie kasowałoby wpisywany tekst i fokus.
    if (!head.querySelector('#hd-q')) {
      head.innerHTML = '<div class="hd-ttl hd-center"><b>Help</b></div>' + X_BTN
        + '<label class="hd-search">' + '<input id="hd-q" type="search" placeholder="Search for help" autocomplete="off"'
        + ' enterkeyhint="search" aria-label="Search for help">' + LUPA + '</label>';
      var q = el('hd-q');
      q.value = SZUKAJ;
      q.addEventListener('input', function () { SZUKAJ = q.value; listaArt(); });
    }
    listaArt();
    if (!ARTS) artykuly().then(function () { if (OTWARTY && ZAKLADKA === 'help') listaArt(); });
  }
  function listaArt() {
    var body = el('hd-body');
    if (!ARTS) { body.innerHTML = '<div class="hd-skel"><i></i><i></i><i></i><i></i></div>'; return; }
    var q = SZUKAJ.trim().toLowerCase();
    if (q) {
      var slowa = q.split(/\s+/);
      var wyniki = ARTS.map(function (a) {
        var t = a.title.toLowerCase(), b = tekstArt(a), pkt = 0;
        slowa.forEach(function (s) { if (t.indexOf(s) >= 0) pkt += 3; else if (b.indexOf(s) >= 0) pkt += 1; else pkt -= 100; });
        return { a: a, pkt: pkt };
      }).filter(function (x) { return x.pkt > 0; }).sort(function (x, y) { return y.pkt - x.pkt; });
      body.innerHTML = wyniki.length
        ? '<div class="hd-group"><h3>' + wyniki.length + ' result' + (wyniki.length > 1 ? 's' : '') + '</h3>'
          + wyniki.map(function (x) { return wierszArt(x.a, true); }).join('') + '</div>'
        : '<div class="hd-empty">No articles match “' + esc(SZUKAJ.trim()) + '”.<br>'
          + '<button type="button" class="hd-link" onclick="hdNewChat()">Ask us instead</button></div>';
      return;
    }
    var grupy = [], po = {};
    ARTS.forEach(function (a) {
      if (!po[a.category]) { po[a.category] = []; grupy.push(a.category); }
      po[a.category].push(a);
    });
    body.innerHTML = '<p class="hd-count">' + ARTS.length + ' articles</p>' + grupy.map(function (g) {
      return '<div class="hd-group"><h3>' + esc(g) + '<span>' + po[g].length + '</span></h3>'
        + po[g].map(function (a) { return wierszArt(a, false); }).join('') + '</div>';
    }).join('');
  }

  /* ================= MESSAGES ================= */
  function naglowekRozmowy() {
    var head = el('hd-head'), rodzaj = rodzajRozmowy(CHAT), nazwa, pod;
    var odcisk = ['chat', CHAT && CHAT.id, rodzaj, AGENT.name, AGENT.team, AGENT.online].join('|');
    if (head.dataset.odcisk === odcisk) return;
    head.dataset.odcisk = odcisk;
    if (rodzaj === 'ai') { nazwa = NAZWA_AI; pod = 'Replies instantly'; }
    else if (rodzaj === 'person') {
      nazwa = esc(AGENT.name);
      pod = esc(AGENT.team) + (AGENT.online ? ' · <span class="hd-on">Online</span>' : ' · replies here');
    } else { nazwa = esc(AGENT.team); pod = 'A person will reply here and by email'; }
    head.innerHTML = BACK('hdList()', 'All conversations') + awatar(rodzaj)
      + '<div class="hd-ttl"><b>' + nazwa + '</b><span>' + pod + '</span></div>' + X_BTN;
  }
  // Pigułka tylko tam, gdzie coś mówi: kto rozmawia, pokazuje już awatar.
  var STATUS = { open: ['Waiting', 'st-wait'], closed: ['Closed', 'st-closed'] };
  function wpis(c) {
    var kto = !c.last ? '' : c.last.author === 'trader' ? 'You: '
      : c.last.author === 'admin' ? AGENT.name + ': ' : 'AI: ';
    var st = STATUS[c.status], nowe = nieprzeczytana(c);
    return '<button type="button" class="hd-item' + (nowe ? ' unread' : '') + '" onclick="hdOpen(' + c.id + ')">'
      + awatar(rodzajRozmowy(c), true)
      + '<div class="hd-it"><div class="hd-it-top"><b>' + esc(c.title || 'Conversation') + '</b>'
      + '<time>' + kiedy(c.last_ts) + '</time></div>'
      + '<div class="hd-it-bot"><span class="hd-prev">' + esc(kto + (c.last ? c.last.body : '')) + '</span>'
      + (nowe ? '<span class="hd-new"></span>' : st ? '<span class="hd-pill ' + st[1] + '">' + st[0] + '</span>' : '')
      + '</div></div></button>';
  }
  function rysujListe() {
    var head = el('hd-head'), body = el('hd-body'), foot = el('hd-foot');
    head.innerHTML = '<div class="hd-ttl hd-center"><b>Messages</b></div>' + X_BTN;
    body.dataset.chat = '';
    if (!zalogowany()) {
      // Gość: rozmowa wymaga konta — asystent i zespół widzą, kto pisze.
      body.innerHTML = '<div class="hd-guest">' + T_MSG + '<h3>Sign in to message us</h3>'
        + '<p>Chat with our AI assistant and support team from your trader account.</p>'
        + '<a class="hd-new-btn" href="/portal?view=support">Sign in</a>'
        + (MAIL ? '<p class="hd-guest-mail">No account yet? Email <a href="mailto:' + esc(MAIL) + '">' + esc(MAIL) + '</a></p>' : '')
        + '</div>';
      foot.innerHTML = '';
      if (!ARTS) artykuly().then(function () { if (OTWARTY && ZAKLADKA === 'messages' && !zalogowany()) rysujListe(); });
      return;
    }
    body.innerHTML = LISTA.length
      ? '<div class="hd-list">' + LISTA.map(wpis).join('') + '</div>'
      : '<div class="hd-guest">' + T_MSG + '<h3>No messages</h3>'
        + '<p>Ask the AI assistant anything about getting started, MT5 or the rules.</p></div>';
    body.scrollTop = 0;
    foot.innerHTML = '<button type="button" class="hd-new-btn" onclick="hdNewChat()">Send us a message' + WYSLIJ + '</button>';
  }

  function powitanie() {
    var imie = (ME && (ME.first_name || (ME.full_name || '').split(' ')[0])) || '';
    var tekst = ASSIST
      ? 'Hi' + (imie ? ' ' + imie : '') + ", I'm the PTF AI Assistant. I can help with getting started, MT5, the "
        + 'trading rules and the portal. Questions about payouts, refunds or your account results go '
        + 'straight to our team.'
      : 'Hi' + (imie ? ' ' + imie : '') + '! Send us your question and our support team will reply here.';
    return '<div class="chat"><div class="chat-row them first last by-ai"><div class="chat-who">'
      + (ASSIST ? NAZWA_AI : 'Support team') + '</div><div class="bub">' + esc(tekst) + '</div></div></div>';
  }
  function rysujRozmowe() {
    var body = el('hd-body'), foot = el('hd-foot');
    naglowekRozmowy();
    var thread = (CHAT && CHAT.thread) || [];
    // Na dół: przy wejściu w rozmowę albo gdy trader już był przy końcu. Ktoś, kto
    // czyta starsze wiadomości, nie zostaje ściągnięty w dół przez odświeżenie.
    var klucz = String((CHAT && CHAT.id) || 'new');
    var akcje = '', zamknieta = CHAT && CHAT.status === 'closed';
    // „Talk to a person" dopiero gdy asystent nie pomógł, po kilku wiadomościach
    // albo po chwili rozmowy (serwer: offer_human; czas liczymy też tu, bez czekania na poll).
    var poChwili = CHAT && CHAT.created_at && Date.now() - utc(CHAT.created_at) >= 120000;
    if (CHAT && CHAT.mode === 'assistant' && thread.length && !CZEKA && (CHAT.offer_human || poChwili))
      akcje = '<button type="button" class="hd-link" onclick="hdHuman()">Didn\'t help? Talk to a person</button>';
    else if (CHAT && CHAT.status === 'open')
      akcje = '<span class="hd-note">Passed to our support team — they\'ll reply here and by email.</span>';
    else if (zamknieta)
      akcje = '<span class="hd-note">This conversation is closed.</span> '
        + '<button type="button" class="hd-link" onclick="hdNewChat()">Start a new one</button>';
    // Odcisk tego, co widać: bez zmian = bez podmiany DOM. Poll co 5 s przebudowywał
    // całą rozmowę i kasował zaznaczony tekst (kopiowanie na telefonie), gubił
    // pozycję przewijania i restartował animację „pisze".
    var ost = thread.length ? thread[thread.length - 1] : null;
    var odcisk = [klucz, thread.length, ost && ost.ts, ost && ost.author, CZEKA, !!(CHAT && CHAT.agent_typing),
      CHAT && CHAT.status, CHAT && CHAT.mode, ASSIST, AGENT.name, AGENT.online, akcje].join('|');
    if (body.dataset.odcisk === odcisk) return;
    body.dataset.odcisk = odcisk;
    var naDol = body.dataset.chat !== klucz || body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    body.dataset.chat = klucz;
    body.innerHTML = powitanie() + (thread.length
      ? chatHtml(thread, { me: 'trader', names: { ai: NAZWA_AI, admin: AGENT.name + ' · ' + AGENT.team } })
      : '')
      + (CZEKA ? '<div class="hd-typing" aria-label="Assistant is typing"><i></i><i></i><i></i></div>' : '')
      + (!CZEKA && CHAT && CHAT.agent_typing
        ? '<div class="hd-typing-who">' + esc(AGENT.name) + ' is typing…</div>'
          + '<div class="hd-typing" aria-hidden="true"><i></i><i></i><i></i></div>' : '')
      // Pusta rozmowa z asystentem: gotowe pytania do kliknięcia.
      + (!thread.length && ASSIST && !CZEKA
        ? '<div class="hd-sugg"><span>Popular questions</span>' + PYTANIA.map(function (q, i) {
          return '<button type="button" class="hd-chip" onclick="hdAsk(' + i + ')">' + esc(q) + '</button>';
        }).join('') + '</div>' : '');

    // Pole pisania przerysowane tylko gdy go nie ma — inaczej poll kasowałby wpisywany tekst.
    var ak = foot.querySelector('.hd-acts');
    if (!ak) {
      foot.innerHTML = '<div class="hd-acts"></div>'
        + chatCompose({ id: 'hd-inp', send: 'hdSend()', placeholder: 'Ask a question…' });
      ak = foot.querySelector('.hd-acts');
    }
    ak.innerHTML = akcje;
    var komp = foot.querySelector('.chat-compose');
    if (komp) komp.hidden = !!zamknieta;
    var inp = el('hd-inp');
    if (inp) inp.disabled = CZEKA;
    // Dopiero teraz: pole pisania pod spodem zmienia wysokość okna rozmowy.
    // Pusta rozmowa zostaje u góry — powitanie i pytania czyta się od początku.
    if (!thread.length && !CZEKA) body.scrollTop = 0;
    else if (naDol) body.scrollTop = body.scrollHeight;
  }

  function rysuj() {
    var p = el('hd-panel');
    if (!p || !el('hd-body')) return;
    var wRozmowie = ZAKLADKA === 'messages' && WIDOK === 'chat' && zalogowany();
    if (!wRozmowie) { el('hd-body').dataset.odcisk = ''; el('hd-head').dataset.odcisk = ''; }
    p.dataset.tab = ZAKLADKA;
    // W rozmowie i w artykule pasek zakładek znika — liczy się treść i pole pisania.
    p.classList.toggle('in-chat', wRozmowie);
    p.classList.toggle('in-article', ZAKLADKA === 'help' && !!ART);
    document.querySelectorAll('#hd-tabs .hd-tab').forEach(function (b) {
      var on = b.dataset.tab === ZAKLADKA;
      b.classList.toggle('on', on);
      b.setAttribute('aria-current', on ? 'page' : 'false');
    });
    if (ZAKLADKA !== 'help') { var h = el('hd-head'); if (h.querySelector('#hd-q')) h.innerHTML = ''; }
    if (!wRozmowie && el('hd-foot').querySelector('.chat-compose')) el('hd-foot').innerHTML = '';
    if (ZAKLADKA === 'home') rysujHome();
    else if (ZAKLADKA === 'help') rysujHelp();
    else if (wRozmowie) rysujRozmowe();
    else rysujListe();
    kropka();
  }

  /* ---------- dane ---------- */
  async function pobierzListe() {
    var r = await zapytaj('/api/me/chats');
    LISTA = r.chats; meta(r);
  }
  async function odswiez() {
    if (!zalogowany() || CZEKA) return;
    try {
      await pobierzListe();
      if (ZAKLADKA === 'messages' && WIDOK === 'chat' && CHAT && CHAT.id) {
        var r = await zapytaj('/api/me/chat/' + CHAT.id);
        CHAT = r.chat; meta(r);
        if (OTWARTY) oznaczWidziane(CHAT);
      }
      // Help ma pole wyszukiwania — odświeżanie listy rozmów go nie dotyczy.
      if (OTWARTY && ZAKLADKA !== 'help') rysuj();
      kropka(); podglad();
    } catch (e) { /* cicho: następny cykl spróbuje znowu */ }
  }
  function planuj() {
    clearTimeout(TIMER);
    if (!zalogowany()) return;
    // Rozmowa czekająca na zespół w otwartym oknie — co 5 s, żeby „is typing…"
    // konsultanta i jego odpowiedź pojawiały się na bieżąco. W tle co 15 s, gdy
    // trader ma rozmowę u zespołu (plakietka na dymku), inaczej rzadko. Push na
    // tym urządzeniu i powrót do karty odświeżają od razu (nasłuch niżej).
    var uZespolu = LISTA.some(function (c) { return c.mode === 'team' && c.status !== 'closed'; });
    var ms = OTWARTY && ZAKLADKA === 'messages' && WIDOK === 'chat' && CHAT && CHAT.mode === 'team'
      && CHAT.status !== 'closed' ? 5000 : uZespolu ? 15000 : 45000;
    TIMER = setTimeout(async function () {
      if (document.visibilityState === 'visible') await odswiez();
      planuj();
    }, ms);
  }

  function pokazPanel() {
    zbuduj();
    schowajPodglad();
    OTWARTY = true;
    var p = el('hd-panel');
    p.hidden = false;
    requestAnimationFrame(function () { p.classList.add('open'); dopasujDoKlawiatury(); });
    el('hd-fab').classList.add('on');
  }
  /* Klawiatura na telefonie: okno na pełny ekran trzyma się układu strony, a Safari
     przesuwa go, gdy wyjeżdża klawiatura — okno „odkleja się" od góry. Wysokość
     i pozycja z visualViewport trzymają je w widocznej części ekranu. */
  function dopasujDoKlawiatury() {
    var p = el('hd-panel'), vv = window.visualViewport;
    if (!p || !vv) return;
    var telefon = matchMedia('(max-width:640px)').matches;
    var klawiatura = telefon && OTWARTY && vv.height < window.innerHeight - 120;
    if (klawiatura) {
      p.style.height = vv.height + 'px';
      p.style.top = vv.offsetTop + 'px';
      p.style.bottom = 'auto';
    } else if (p.style.height) {
      p.style.height = ''; p.style.top = ''; p.style.bottom = '';
    }
  }
  if (window.visualViewport) {
    visualViewport.addEventListener('resize', dopasujDoKlawiatury);
    visualViewport.addEventListener('scroll', dopasujDoKlawiatury);
  }

  async function otworz(chatId) {
    pokazPanel();
    artykuly();
    // Deep link z pusha / maila (?view=support&chat=ID): od razu ta rozmowa.
    if (chatId && zalogowany()) {
      try { await pobierzListe(); } catch (e) { /* strzałka wstecz pokaże pustą listę */ }
      return otworzRozmowe(chatId);
    }
    // Nowa odpowiedź zespołu czeka — od razu do wiadomości; inaczej Home.
    var nowe = zalogowany() && LISTA.some(nieprzeczytana);
    ZAKLADKA = nowe ? 'messages' : 'home'; ART = null;
    if (nowe) { WIDOK = 'list'; CHAT = null; }
    rysuj();
    if (zalogowany()) {
      try { await pobierzListe(); } catch (e) { /* zostaje to, co było */ }
      if (OTWARTY && ZAKLADKA !== 'help') rysuj();
      planuj();
    }
  }
  function fokus() {
    var inp = el('hd-inp');
    if (inp && matchMedia('(hover: hover) and (pointer: fine)').matches) inp.focus();
  }
  function zamknij() {
    OTWARTY = false;
    var p = el('hd-panel');
    if (!p) return;
    p.classList.remove('open');
    dopasujDoKlawiatury();
    setTimeout(function () { if (!OTWARTY) p.hidden = true; }, 220);
    el('hd-fab').classList.remove('on');
    kropka(); planuj();
  }
  function zakladka(k, szukaj) {
    ZAKLADKA = k; ART = null;
    if (k === 'messages') { WIDOK = 'list'; CHAT = null; }
    rysuj();
    if (k === 'messages' && zalogowany()) odswiez().then(planuj);
    if (k === 'help' && szukaj) { var q = el('hd-q'); if (q) q.focus(); }
  }
  async function otworzRozmowe(id) {
    if (!OTWARTY) pokazPanel();
    try {
      var r = await zapytaj('/api/me/chat/' + id);
      CHAT = r.chat; meta(r); ZAKLADKA = 'messages'; WIDOK = 'chat';
      oznaczWidziane(CHAT);
      el('hd-foot').innerHTML = '';
      rysuj(); planuj(); fokus();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }
  async function doListy() {
    ZAKLADKA = 'messages'; WIDOK = 'list'; CHAT = null;
    rysuj();
    await odswiez();
    planuj();
  }
  function nowaRozmowa() {
    ZAKLADKA = 'messages'; ART = null;
    if (!zalogowany()) { WIDOK = 'list'; rysuj(); return; }
    WIDOK = 'chat'; CHAT = null;
    el('hd-foot').innerHTML = '';
    rysuj(); fokus();
  }

  async function wyslij() {
    var inp = el('hd-inp');
    var tekst = inp && inp.value.trim();
    if (!tekst || CZEKA) return;
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    var id = CHAT && CHAT.id;
    // Wiadomość widać od razu; odpowiedź asystenta dochodzi po chwili.
    CHAT = CHAT || { mode: 'assistant', status: 'bot', thread: [] };
    CHAT.thread = (CHAT.thread || []).concat([{ author: 'trader', body: tekst, ts: new Date().toISOString() }]);
    CZEKA = CHAT.mode === 'assistant';
    rysuj();
    try {
      var r = await zapytaj('/api/me/chat', { method: 'POST', body: JSON.stringify({ message: tekst, chat_id: id || null }) });
      CHAT = r.chat; meta(r);
    } catch (e) {
      if (typeof toast === 'function') toast('Message not sent: ' + e.message, 'err');
      CHAT.thread.pop();
      if (inp) inp.value = tekst;
    } finally {
      CZEKA = false; rysuj(); oznaczWidziane(CHAT); planuj();
      pobierzListe().then(kropka).catch(function () {});
    }
  }
  async function czlowiek() {
    if (!CHAT || !CHAT.id) return;
    try {
      var r = await zapytaj('/api/me/chat/' + CHAT.id + '/human', { method: 'POST' });
      CHAT = r.chat; meta(r); rysuj(); planuj();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }

  window.openHelpdesk = function (chatId) { return otworz(+chatId || 0); };
  window.hdOpenPeek = function (id) { otworz(id); };
  window.hdPeekClose = schowajPodglad;
  window.hdClose = zamknij;
  window.hdSend = wyslij;
  window.hdHuman = czlowiek;
  window.hdOpen = otworzRozmowe;
  window.hdList = doListy;
  window.hdNewChat = nowaRozmowa;
  window.hdTab = zakladka;
  // Przyciski na stronach (FAQ, kontakt): otwórz okienko na zakładce; `nowa` = od razu
  // nowa rozmowa dla zalogowanego, gość widzi „Sign in to message us".
  window.hdOpenTab = function (k, nowa) {
    if (!OTWARTY) pokazPanel();
    artykuly();
    if (nowa && zalogowany()) { nowaRozmowa(); return; }
    zakladka(k);
  };
  window.hdArticle = function (id) {
    ZAKLADKA = 'help'; ART = id || null;
    if (!ART) el('hd-head').innerHTML = '';   // wróć do listy z polem wyszukiwania
    rysuj();
  };
  window.hdAsk = function (i) {
    var inp = el('hd-inp');
    if (!inp || CZEKA || !PYTANIA[i]) return;
    inp.value = PYTANIA[i];
    wyslij();
  };

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && OTWARTY) zamknij(); });
  // Klik w stronę poza oknem zamyka je (jak Escape). pointerdown, nie click:
  // klik w środku, który przerysowuje okno, odpinałby cel i liczył się jako „poza".
  document.addEventListener('pointerdown', function (e) {
    if (!OTWARTY) return;
    var t = e.target;
    if (t.closest && t.closest('#hd-panel, #hd-fab, #hd-peek')) return;
    zamknij();
  });
  // Powrót do karty / PWA z tła i push na tym urządzeniu (sw.js) = odśwież od razu.
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && zalogowany()) odswiez();
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (e.data && e.data.type === 'push' && zalogowany()) odswiez();
    });
  }

  // Dymek widać od razu (gość: Home i Help); po zalogowaniu dochodzą rozmowy,
  // po wylogowaniu znikają. ME ustawia boot() portalu.
  zbuduj();
  el('hd-fab').classList.remove('hidden');
  var bylZalogowany = false;
  setInterval(function () {
    var teraz = zalogowany();
    if (teraz === bylZalogowany) return;
    bylZalogowany = teraz;
    if (teraz) { odswiez(); planuj(); }
    else { CHAT = null; LISTA = []; clearTimeout(TIMER); kropka(); if (OTWARTY) rysuj(); }
  }, 1000);
})();
