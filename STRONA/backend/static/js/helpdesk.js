/* Dymek pomocy w prawym dolnym rogu portalu (zastępuje zakładkę Support).
 *
 * Jak komunikator: lista rozmów (tytuł, podgląd, godzina, status, nieprzeczytane)
 * i widok jednej rozmowy ze strzałką wstecz. W rozmowie najpierw odpowiada
 * asystent AI (FAQ, platforma, zasady); gdy nie pomoże — albo temat dotyczy
 * pieniędzy, wyniku konta czy reklamacji — rozmowa idzie do zespołu i dalej
 * odpisuje człowiek w tym samym okienku. Logika i granice są na serwerze
 * (app/helpdesk.py, /api/me/chat*); tu tylko widok.
 *
 * Korzysta z globali portal-app.js (ME, H) i chat-kit.js (chatHtml, chatCompose).
 * Własny fetch zamiast api(): odpowiedź modelu bywa dłuższa niż 15 s limitu api().
 */
(function () {
  'use strict';
  var WIDOK = 'list', LISTA = [], CHAT = null, ASSIST = true, OTWARTY = false, CZEKA = false, TIMER = null;
  var AGENT = { name: 'Support', team: 'Support team', online: false };
  var KLUCZ_WIDZIANE = 'pf_hd_seen2';
  var NAZWA_AI = 'PTF AI Assistant';   // „AI" w nazwie: trader wie, że to nie człowiek
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

  var IKONA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  var ISKRA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'
    + '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>';
  var ZESPOL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M17 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-3A3.5 3.5 0 0 0 7 18.5V20"/>'
    + '<circle cx="12" cy="8.5" r="3.5"/></svg>';
  var ZAMKNIJ = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
    + '<path d="M6 6l12 12M18 6L6 18"/></svg>';
  var WSTECZ = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"'
    + ' stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';
  var PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">'
    + '<path d="M12 5v14M5 12h14"/></svg>';

  function zbuduj() {
    if (document.getElementById('hd-fab')) return;
    var fab = document.createElement('button');
    fab.id = 'hd-fab'; fab.type = 'button'; fab.className = 'hd-fab hidden';
    fab.setAttribute('aria-label', 'Help & support chat');
    fab.innerHTML = IKONA + '<span class="hd-dot hidden"></span>';
    fab.onclick = function () { OTWARTY ? zamknij() : otworz(); };
    var p = document.createElement('section');
    p.id = 'hd-panel'; p.className = 'hd-panel'; p.setAttribute('role', 'dialog');
    p.setAttribute('aria-label', 'Support chat'); p.hidden = true;
    p.innerHTML = '<header class="hd-head">'
      + '<button type="button" class="hd-back" id="hd-back" aria-label="All conversations" onclick="hdList()">'
      + WSTECZ + '</button><div class="hd-av" id="hd-av"></div>'
      + '<div class="hd-ttl"><b id="hd-name"></b><span id="hd-sub"></span></div>'
      + '<button type="button" class="hd-x" aria-label="Close" onclick="hdClose()">' + ZAMKNIJ + '</button></header>'
      + '<div class="hd-body" id="hd-body"></div><div class="hd-foot" id="hd-foot"></div>';
    document.body.appendChild(fab);
    document.body.appendChild(p);
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
  function kropka() {
    var d = document.querySelector('#hd-fab .hd-dot');
    if (d) d.classList.toggle('hidden', OTWARTY || !LISTA.some(nieprzeczytana));
  }

  /* ---------- nagłówek: z kim trader rozmawia ----------
     Lista: „Messages". Rozmowa: asystent AI, kolejka zespołu (przekazane, nikt
     jeszcze nie odpisał) albo konsultant, który przejął rozmowę. Kropka
     „online" = ktoś z zespołu jest teraz w panelu (serwer: aktywność < 5 min). */
  function awatar(el, rodzaj, maly) {
    // Obecność pokazujemy tylko przy konsultancie, który przejął rozmowę.
    var on = AGENT.online && rodzaj === 'person' ? '<span class="hd-online" title="Online"></span>' : '';
    el.className = 'hd-av ' + rodzaj + (maly ? ' sm' : '');
    el.innerHTML = rodzaj === 'ai' ? ISKRA
      : rodzaj === 'person' ? esc((AGENT.name || 'S').charAt(0).toUpperCase()) + on
        : rodzaj === 'team' ? ZESPOL + on : IKONA;
  }
  function rodzajRozmowy(c) {
    if (!c || (c.mode === 'assistant' && ASSIST)) return 'ai';
    return c.taken_over ? 'person' : 'team';
  }
  function naglowek() {
    var av = document.getElementById('hd-av'), name = document.getElementById('hd-name'),
      sub = document.getElementById('hd-sub');
    document.getElementById('hd-back').hidden = WIDOK === 'list' || (!LISTA.length && !CHAT);
    document.getElementById('hd-panel').classList.toggle('list', WIDOK === 'list');
    av.hidden = WIDOK === 'list';
    if (WIDOK === 'list') {
      // Lista jak w komunikatorze: sam tytuł, bez awatara i bez statusu zespołu.
      name.textContent = 'Messages';
      sub.textContent = NAZWA_AI + ' and ' + AGENT.team;
      return;
    }
    var rodzaj = rodzajRozmowy(CHAT);
    awatar(av, rodzaj);
    if (rodzaj === 'ai') {
      name.textContent = NAZWA_AI;
      sub.textContent = 'Replies instantly';
    } else if (rodzaj === 'person') {
      name.textContent = AGENT.name;
      sub.innerHTML = esc(AGENT.team) + (AGENT.online ? ' · <span class="hd-on">Online</span>' : ' · replies here');
    } else {
      name.textContent = AGENT.team;
      sub.textContent = 'A person will reply here and by email';
    }
  }

  /* ---------- lista rozmów ---------- */
  function kiedy(iso) {
    var d = utc(iso), teraz = new Date();
    if (d.toDateString() === teraz.toDateString())
      return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === new Date(teraz - 864e5).toDateString()) return 'Yesterday';
    if (teraz - d < 6 * 864e5) return d.toLocaleDateString('en-US', { weekday: 'short' });
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  // Pigułka tylko tam, gdzie coś mówi: kto rozmawia, pokazuje już awatar.
  var STATUS = { open: ['Waiting', 'st-wait'], closed: ['Closed', 'st-closed'] };
  function wpis(c) {
    var kto = !c.last ? '' : c.last.author === 'trader' ? 'You: '
      : c.last.author === 'admin' ? AGENT.name + ': ' : 'AI: ';
    var st = STATUS[c.status], nowe = nieprzeczytana(c);
    var av = document.createElement('div');
    awatar(av, rodzajRozmowy(c), true);
    return '<button type="button" class="hd-item' + (nowe ? ' unread' : '') + '" onclick="hdOpen(' + c.id + ')">'
      + av.outerHTML
      + '<div class="hd-it"><div class="hd-it-top"><b>' + esc(c.title || 'Conversation') + '</b>'
      + '<time>' + kiedy(c.last_ts) + '</time></div>'
      + '<div class="hd-it-bot"><span class="hd-prev">' + esc(kto + (c.last ? c.last.body : '')) + '</span>'
      + (nowe ? '<span class="hd-new"></span>' : st ? '<span class="hd-pill ' + st[1] + '">' + st[0] + '</span>' : '')
      + '</div></div></button>';
  }
  function rysujListe() {
    var body = document.getElementById('hd-body'), foot = document.getElementById('hd-foot');
    body.innerHTML = LISTA.length
      ? '<div class="hd-list">' + LISTA.map(wpis).join('') + '</div>'
      : '<div class="hd-empty">No conversations yet.<br>Ask the AI assistant anything about getting started, MT5 or the rules.</div>';
    body.scrollTop = 0;
    body.dataset.chat = '';
    foot.innerHTML = '<button type="button" class="hd-new-btn" onclick="hdNewChat()">' + PLUS + 'New conversation</button>';
  }

  /* ---------- jedna rozmowa ---------- */
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
    var body = document.getElementById('hd-body'), foot = document.getElementById('hd-foot');
    var thread = (CHAT && CHAT.thread) || [];
    // Na dół: przy wejściu w rozmowę albo gdy trader już był przy końcu. Ktoś, kto
    // czyta starsze wiadomości, nie zostaje ściągnięty w dół przez odświeżenie.
    var klucz = String((CHAT && CHAT.id) || 'new');
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
    var inp = document.getElementById('hd-inp');
    if (inp) inp.disabled = CZEKA;
    // Dopiero teraz: pole pisania pod spodem zmienia wysokość okna rozmowy.
    // Pusta rozmowa zostaje u góry — powitanie i pytania czyta się od początku.
    if (!thread.length && !CZEKA) body.scrollTop = 0;
    else if (naDol) body.scrollTop = body.scrollHeight;
  }

  function rysuj() {
    if (!document.getElementById('hd-body')) return;
    naglowek();
    if (WIDOK === 'list') rysujListe(); else rysujRozmowe();
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
      if (WIDOK === 'chat' && CHAT && CHAT.id) {
        var r = await zapytaj('/api/me/chat/' + CHAT.id);
        CHAT = r.chat; meta(r);
        if (OTWARTY) oznaczWidziane(CHAT);
      }
      if (OTWARTY) rysuj();
      kropka();
    } catch (e) { /* cicho: następny cykl spróbuje znowu */ }
  }
  function planuj() {
    clearTimeout(TIMER);
    // Rozmowa czekająca na zespół w otwartym oknie — częściej; w tle rzadko.
    // 5 s, żeby „is typing…" konsultanta i jego odpowiedź pojawiały się na bieżąco.
    var ms = OTWARTY && WIDOK === 'chat' && CHAT && CHAT.mode === 'team' && CHAT.status !== 'closed' ? 5000 : 45000;
    TIMER = setTimeout(async function () {
      if (document.visibilityState === 'visible') await odswiez();
      planuj();
    }, ms);
  }

  async function otworz() {
    zbuduj();
    OTWARTY = true;
    var p = document.getElementById('hd-panel');
    p.hidden = false;
    requestAnimationFrame(function () { p.classList.add('open'); });
    document.getElementById('hd-fab').classList.add('on');
    try { await pobierzListe(); } catch (e) { /* pokaże pustą listę */ }
    // Bez rozmów od razu nowa; z rozmowami — lista, jak w komunikatorze.
    if (!LISTA.length) { WIDOK = 'chat'; CHAT = null; } else if (!CHAT) { WIDOK = 'list'; }
    rysuj(); kropka(); planuj();
    fokus();
  }
  function fokus() {
    var inp = document.getElementById('hd-inp');
    if (WIDOK === 'chat' && inp && matchMedia('(hover: hover) and (pointer: fine)').matches) inp.focus();
  }
  function zamknij() {
    OTWARTY = false;
    var p = document.getElementById('hd-panel');
    if (!p) return;
    p.classList.remove('open');
    setTimeout(function () { if (!OTWARTY) p.hidden = true; }, 220);
    document.getElementById('hd-fab').classList.remove('on');
    kropka(); planuj();
  }
  async function otworzRozmowe(id) {
    try {
      var r = await zapytaj('/api/me/chat/' + id);
      CHAT = r.chat; meta(r); WIDOK = 'chat';
      oznaczWidziane(CHAT);
      document.getElementById('hd-foot').innerHTML = '';
      rysuj(); kropka(); planuj(); fokus();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }
  async function doListy() {
    WIDOK = 'list'; CHAT = null;
    rysuj();
    await odswiez();
    planuj();
  }
  function nowaRozmowa() {
    WIDOK = 'chat'; CHAT = null;
    document.getElementById('hd-foot').innerHTML = '';
    rysuj(); fokus();
  }

  async function wyslij() {
    var inp = document.getElementById('hd-inp');
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

  window.openHelpdesk = otworz;
  window.hdClose = zamknij;
  window.hdSend = wyslij;
  window.hdHuman = czlowiek;
  window.hdOpen = otworzRozmowe;
  window.hdList = doListy;
  window.hdNewChat = nowaRozmowa;
  window.hdAsk = function (i) {
    var inp = document.getElementById('hd-inp');
    if (!inp || CZEKA || !PYTANIA[i]) return;
    inp.value = PYTANIA[i];
    wyslij();
  };

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && OTWARTY) zamknij(); });

  // Dymek pokazuje się dopiero po zalogowaniu (ME ustawia boot() portalu) i znika po wylogowaniu.
  var bylZalogowany = false;
  setInterval(function () {
    var teraz = zalogowany();
    if (teraz === bylZalogowany) return;
    bylZalogowany = teraz;
    zbuduj();
    document.getElementById('hd-fab').classList.toggle('hidden', !teraz);
    if (teraz) { odswiez(); planuj(); } else { zamknij(); CHAT = null; LISTA = []; clearTimeout(TIMER); }
  }, 1000);
})();
