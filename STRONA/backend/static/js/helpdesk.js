/* Dymek pomocy w prawym dolnym rogu portalu (zastępuje zakładkę Support).
 *
 * Najpierw odpowiada asystent AI (FAQ, platforma, zasady), a gdy nie pomoże —
 * albo temat dotyczy pieniędzy, wyniku konta czy reklamacji — rozmowa idzie do
 * zespołu i dalej odpisuje człowiek, w tym samym okienku. Logika i granice są
 * na serwerze (app/helpdesk.py, /api/me/chat*); tu tylko widok.
 *
 * Korzysta z globali portal-app.js (ME, H) i chat-kit.js (chatHtml, chatCompose).
 * Własny fetch zamiast api(): odpowiedź modelu bywa dłuższa niż 15 s limitu api().
 */
(function () {
  'use strict';
  var CHAT = null, ASSIST = true, OTWARTY = false, CZEKA = false, TIMER = null;
  var AGENT = { name: 'Support', team: 'Support team', online: false };
  var KLUCZ_WIDZIANE = 'pf_hd_seen';
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
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
    p.innerHTML = '<header class="hd-head"><div class="hd-av" id="hd-av"></div>'
      + '<div class="hd-ttl"><b id="hd-name"></b><span id="hd-sub"></span></div>'
      + '<button type="button" class="hd-x" aria-label="Close" onclick="hdClose()">' + ZAMKNIJ + '</button></header>'
      + '<div class="hd-body" id="hd-body"></div><div class="hd-foot" id="hd-foot"></div>';
    document.body.appendChild(fab);
    document.body.appendChild(p);
  }

  function powitanie() {
    var imie = (ME && (ME.first_name || (ME.full_name || '').split(' ')[0])) || '';
    var tekst = ASSIST
      ? 'Hi' + (imie ? ' ' + imie : '') + ", I'm the AI assistant. I can help with getting started, MT5, the "
        + 'trading rules and the portal. Questions about payouts, refunds or your account results go '
        + 'straight to our team.'
      : 'Hi' + (imie ? ' ' + imie : '') + '! Send us your question and our support team will reply here.';
    return '<div class="chat"><div class="chat-row them first last by-ai"><div class="chat-who">'
      + (ASSIST ? 'AI assistant' : 'Support team') + '</div><div class="bub">' + esc(tekst) + '</div></div></div>';
  }

  /* Nagłówek mówi, z kim trader rozmawia: z asystentem AI, z kolejką zespołu
     (sprawa przekazana, nikt jeszcze nie odpisał) albo z konsultantem, który
     przejął rozmowę. Kropka „online" = ktoś z zespołu jest teraz w panelu
     (serwer: ostatnia aktywność panelu < 5 min), nie ozdobnik. */
  function naglowek() {
    var tryb = CHAT ? CHAT.mode : 'assistant', przejal = !!(CHAT && CHAT.taken_over);
    var av = document.getElementById('hd-av'), name = document.getElementById('hd-name'),
      sub = document.getElementById('hd-sub');
    var kropka = AGENT.online ? '<span class="hd-online" title="Online"></span>' : '';
    if (tryb === 'assistant' && ASSIST) {
      av.className = 'hd-av ai'; av.innerHTML = ISKRA;
      name.innerHTML = 'AI assistant <span class="hd-badge">AI</span>';
      sub.textContent = 'Automated help · answers right away';
    } else if (przejal) {
      av.className = 'hd-av person'; av.innerHTML = esc((AGENT.name || 'S').charAt(0).toUpperCase()) + kropka;
      name.textContent = AGENT.name;
      sub.innerHTML = esc(AGENT.team) + (AGENT.online ? ' · <span class="hd-on">Online</span>' : ' · replies here');
    } else {
      av.className = 'hd-av team'; av.innerHTML = ZESPOL + kropka;
      name.textContent = AGENT.team;
      sub.textContent = AGENT.online ? 'Online · a person will reply here'
        : 'A person will reply here and by email';
    }
  }

  function rysuj() {
    var body = document.getElementById('hd-body'), foot = document.getElementById('hd-foot');
    if (!body) return;
    naglowek();
    var thread = (CHAT && CHAT.thread) || [];
    body.innerHTML = powitanie() + (thread.length
      ? chatHtml(thread, { me: 'trader', names: { ai: 'AI assistant', admin: AGENT.name + ' · ' + AGENT.team } })
      : '')
      + (CZEKA ? '<div class="hd-typing" aria-label="Assistant is typing"><i></i><i></i><i></i></div>' : '');
    body.scrollTop = body.scrollHeight;

    var akcje = '';
    if (CHAT && CHAT.mode === 'assistant' && thread.length && !CZEKA)
      akcje = '<button type="button" class="hd-link" onclick="hdHuman()">Didn\'t help? Talk to a person</button>';
    else if (CHAT && CHAT.status === 'open')
      akcje = '<span class="hd-note">Passed to our support team — they\'ll reply here and by email.</span>';
    else if (CHAT && CHAT.status === 'answered')
      akcje = '<button type="button" class="hd-link" onclick="hdNew()">Start a new conversation</button>';
    // Pole pisania przerysowane tylko gdy go nie ma — inaczej poll kasowałby wpisywany tekst.
    var ak = foot.querySelector('.hd-acts');
    if (!ak) {
      foot.innerHTML = '<div class="hd-acts"></div>'
        + chatCompose({ id: 'hd-inp', send: 'hdSend()', placeholder: 'Ask a question…' });
      ak = foot.querySelector('.hd-acts');
    }
    ak.innerHTML = akcje;
    var inp = document.getElementById('hd-inp');
    if (inp) inp.disabled = CZEKA;
  }

  function nieprzeczytane() {
    if (!CHAT || !CHAT.thread || !CHAT.thread.length) return false;
    var ost = CHAT.thread[CHAT.thread.length - 1];
    var widziane = '';
    try { widziane = localStorage.getItem(KLUCZ_WIDZIANE) || ''; } catch (e) { /* tryb prywatny */ }
    return ost.author === 'admin' && ost.ts > widziane;
  }
  function oznaczWidziane() {
    if (!CHAT || !CHAT.thread || !CHAT.thread.length) return;
    try { localStorage.setItem(KLUCZ_WIDZIANE, CHAT.thread[CHAT.thread.length - 1].ts); } catch (e) { /* j.w. */ }
  }
  function kropka() {
    var d = document.querySelector('#hd-fab .hd-dot');
    if (d) d.classList.toggle('hidden', OTWARTY || !nieprzeczytane());
  }

  async function odswiez() {
    if (!zalogowany() || CZEKA) return;
    try {
      var r = await zapytaj('/api/me/chat');
      CHAT = r.chat; ASSIST = r.assistant; AGENT = r.agent || AGENT;
      if (OTWARTY) { rysuj(); oznaczWidziane(); }
      kropka();
    } catch (e) { /* cicho: następny cykl spróbuje znowu */ }
  }

  function planuj() {
    clearTimeout(TIMER);
    // Gdy rozmowa czeka na zespół i okno jest otwarte — częściej; w tle rzadko.
    var ms = OTWARTY && CHAT && CHAT.mode === 'team' ? 15000 : 45000;
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
    rysuj();
    await odswiez();
    oznaczWidziane(); kropka(); planuj();
    var inp = document.getElementById('hd-inp');
    if (inp && matchMedia('(hover: hover) and (pointer: fine)').matches) inp.focus();
  }
  function zamknij() {
    OTWARTY = false;
    var p = document.getElementById('hd-panel');
    if (!p) return;
    p.classList.remove('open');
    setTimeout(function () { if (!OTWARTY) p.hidden = true; }, 220);
    document.getElementById('hd-fab').classList.remove('on');
    planuj();
  }

  async function wyslij() {
    var inp = document.getElementById('hd-inp');
    var tekst = inp && inp.value.trim();
    if (!tekst || CZEKA) return;
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    // Wiadomość widać od razu; odpowiedź asystenta dochodzi po chwili.
    CHAT = CHAT || { mode: 'assistant', status: 'bot', thread: [] };
    CHAT.thread = (CHAT.thread || []).concat([{ author: 'trader', body: tekst, ts: new Date().toISOString() }]);
    CZEKA = CHAT.mode === 'assistant';
    rysuj();
    try {
      var r = await zapytaj('/api/me/chat', { method: 'POST', body: JSON.stringify({ message: tekst }) });
      CHAT = r.chat; ASSIST = r.assistant; AGENT = r.agent || AGENT;
    } catch (e) {
      if (typeof toast === 'function') toast('Message not sent: ' + e.message, 'err');
      CHAT.thread.pop();
      if (inp) inp.value = tekst;
    } finally {
      CZEKA = false; rysuj(); oznaczWidziane(); planuj();
    }
  }

  async function czlowiek() {
    try {
      var r = await zapytaj('/api/me/chat/human', { method: 'POST' });
      CHAT = r.chat; AGENT = r.agent || AGENT; rysuj(); planuj();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }
  async function nowa() {
    try {
      var r = await zapytaj('/api/me/chat/new', { method: 'POST' });
      CHAT = r.chat; ASSIST = r.assistant; AGENT = r.agent || AGENT; rysuj();
    } catch (e) { if (typeof toast === 'function') toast('Error: ' + e.message, 'err'); }
  }

  window.openHelpdesk = otworz;
  window.hdClose = zamknij;
  window.hdSend = wyslij;
  window.hdHuman = czlowiek;
  window.hdNew = nowa;

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && OTWARTY) zamknij(); });

  // Dymek pokazuje się dopiero po zalogowaniu (ME ustawia boot() portalu) i znika po wylogowaniu.
  var bylZalogowany = false;
  setInterval(function () {
    var teraz = zalogowany();
    if (teraz === bylZalogowany) return;
    bylZalogowany = teraz;
    zbuduj();
    document.getElementById('hd-fab').classList.toggle('hidden', !teraz);
    if (teraz) { odswiez(); planuj(); } else { zamknij(); CHAT = null; clearTimeout(TIMER); }
  }, 1000);
})();
