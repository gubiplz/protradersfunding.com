/* Terminal PTF (zakładka Trade): wykres KLineChart, księga, transakcje, bilet
   zlecenia i tabele pozycji. Dane rynkowe z terminal-feed.js (prosto z giełd),
   konto i zlecenia z /api/terminal/* — to samo konto co login MT5. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const $$ = (sel, el) => [...(el || document).querySelectorAll(sel)];
  const root = $('terminal');
  const F = window.PTFeed;
  const MKTS = JSON.parse($('tMarkets').textContent);
  const BY = Object.fromEntries(MKTS.map((m) => [m.sym, m]));
  const BY_COIN = Object.fromEntries(MKTS.map((m) => [m.hl, m]));
  const CLS = { crypto: 'Crypto', metal: 'Metals', energy: 'Energy', index: 'Indices', fx: 'Forex' };

  /* ---------- pamięć przeglądarki (może być zablokowana) ---------- */
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
    json(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } },
  };

  /* ---------- formatowanie ---------- */
  const NF = {};
  const nf = (d) => NF[d] || (NF[d] = new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const ok = (v) => v != null && isFinite(v);
  const fx = (v, d) => (ok(v) ? nf(d).format(v) : '—');
  const usd = (v, d = 2) => (ok(v) ? (v < 0 ? '-$' : '$') + nf(d).format(Math.abs(v)) : '—');
  const sUsd = (v) => (ok(v) ? (v > 0 ? '+' : v < 0 ? '-' : '') + '$' + nf(2).format(Math.abs(v)) : '—');
  const big = (v) => (!ok(v) ? '—' : v >= 1e9 ? '$' + (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? '$' + (v / 1e3).toFixed(1) + 'K' : '$' + v.toFixed(0));
  const pct = (v) => (ok(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—');
  const px = (m, v) => fx(v, m.px_dec);
  const qtyF = (m, v) => fx(v, Math.min(Math.max(m.qty_dec, 0), 6));
  // Loty jak w MT5 (1 lot = m.contract jednostek); ułamki poniżej 0,01 lota z 4 miejscami.
  const lotsF = (v) => fx(v, v > 0 && v < 0.01 ? 4 : 2);
  const lotsOf = (m, q) => lotsF(q / m.contract) + ' lots';
  const sizeCell = (m, q) => `${lotsOf(m, q)} <span class="sub">${qtyF(m, q)} ${esc(m.base)}</span>`;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'dn' : '');
  const tm = (iso) => { if (!iso) return '—'; const d = new Date(iso); return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) + ' ' + d.toLocaleTimeString('en-GB', { hour12: false }); };
  const notional = (m, q, p) => (m.quote_inverse ? q : q * p);
  const qtyFromUsd = (m, u, p) => (m.quote_inverse ? u : p ? u / p : 0);
  const pnlOf = (m, side, q, entry, p) => { const r = side === 'buy' ? p - entry : entry - p; return m.quote_inverse ? (p ? (q * r) / p : 0) : q * r; };

  /* ---------- ikony rynków ---------- */
  const FLAG = (c) => `<img src="/static/img/markets/flag-${c}.svg" alt="">`;
  const HUE = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  function mIcon(m, sm) {
    const k = m.icon || '';
    const c = 'mi' + (sm ? ' sm' : '');
    if (['btc', 'eth', 'ltc', 'sol', 'xrp'].includes(k)) return `<span class="${c}"><img src="/static/img/markets/coin-${k}.svg" alt=""></span>`;
    if (k.includes('-')) { const [a, b] = k.split('-'); return `<span class="${c} pair">${FLAG(a)}${FLAG(b)}</span>`; }
    if (['us', 'jp', 'de', 'gb', 'eu', 'au'].includes(k)) return `<span class="${c}">${FLAG(k)}</span>`;
    const metal = { xau: ['#f3d27a', '#b9862b', 'Au'], xag: ['#eef1f6', '#8d97a6', 'Ag'], xpt: ['#e4e9ef', '#6f7b8d', 'Pt'] }[k];
    if (metal) return `<span class="${c}" style="background:linear-gradient(135deg,${metal[0]},${metal[1]});color:#1b1406">${metal[2]}</span>`;
    if (k === 'oil') return `<span class="${c}" style="background:#1e2a3d"><svg viewBox="0 0 24 24" style="width:60%;height:60%;stroke:none;fill:#e6b450"><path d="M12 3c3 4 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-7 6-11z"/></svg></span>`;
    if (k === 'gas') return `<span class="${c}" style="background:#1e2a3d"><svg viewBox="0 0 24 24" style="width:62%;height:62%;stroke:none;fill:#5aa9ff"><path d="M12 2c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-5 1-8.5z"/></svg></span>`;
    return `<span class="${c}" style="background:hsl(${HUE(m.sym)} 45% 38%)">${esc(m.base.slice(0, sm ? 1 : 2))}</span>`;
  }
  const V_ICON = {
    hyperliquid: '<svg viewBox="0 0 24 24"><path fill="#36B9A9" d="M20.6 11.2c0 4-2.5 5.3-3.9 3.6-1.3-1.5-1.8-4.4-4.1-4.6-2.9-.3-3 3-4.9 3.2-2 .3-2.3-2.8-2.3-4.4C5.4 7.5 6.5 5 8.6 5c2.6 0 2.7 3.6 5.5 3.7 2.9.1 3-3 5-3 1.4.1 1.5 3.6 1.5 5.5z"/></svg>',
    binance: '<svg viewBox="0 0 24 24"><path fill="#F0B90B" d="M12 3.5 14.6 6 9.4 11.2 6.8 8.6zM17.2 8.6l2.6 2.6L12 19l-7.8-7.8 2.6-2.6L12 13.8zM3.5 12 6 9.4 8.6 12 6 14.6zM18 9.4l2.5 2.6L18 14.6 15.4 12z"/></svg>',
    bybit: '<svg viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="3" fill="#17181e"/><path fill="#F7A600" d="M14.6 9h1.1v6h-1.1z"/><path fill="#fff" d="M5 9.2h2.2c1.2 0 1.7.6 1.7 1.3 0 .5-.3.9-.7 1 .6.1 1 .6 1 1.2 0 .9-.6 1.5-1.8 1.5H5zm1.1 2.3h.9c.5 0 .8-.2.8-.6s-.3-.6-.8-.6h-.9zm0 2.4h1c.5 0 .9-.2.9-.7 0-.4-.3-.7-.9-.7h-1zM10.3 9.2h1.2l1.1 2.2 1.1-2.2h1.1l-1.7 3.3v2.4h-1v-2.4z"/></svg>',
  };

  /* ---------- stan ---------- */
  const startSym = BY[root.dataset.symbol] ? root.dataset.symbol : 'BTCUSD';
  const S = {
    sym: startSym, m: BY[startSym], venue: ls.get('pf_term_venue', 'hyperliquid'),
    interval: ls.get('pf_term_int', '15m'), ctype: ls.get('pf_term_ctype', 'candle_solid'),
    book: { bids: [], asks: [] }, trades: [], tk: {}, mids: {}, ctx: {},
    // Jednostka pola Size: loty (domyślnie, jak w MT5), USD albo jednostka bazowa (BTC, oz…).
    side: 'buy', kind: 'market', unit: ['lots', 'usd', 'base'].includes(ls.get('pf_term_unit2', 'lots')) ? ls.get('pf_term_unit2', 'lots') : 'lots', slip: +ls.get('pf_term_slip', '1'),
    quick: ls.json('pf_term_quick', [50, 100, 250]), watch: ls.json('pf_term_watch', ['BTCUSD', 'ETHUSD', 'XAUUSD']),
    token: ls.get('pf_token', null),   // tylko sesja konta PTF (wspólna z portalem)
    accounts: [], accId: +ls.get('pf_term_acc', '0') || null, st: null, ptab: 'positions',
    cols: ls.json('pf_term_cols2', { value: true, slippage: true, lev: true, margin: true, tpsl: true }),
    weekend: false, fee: 0.03, group: null, bookUnit: 'base', tickMode: ls.get('pf_term_tick', 'trending'),
  };
  if (!F.venuesFor(S.m).includes(S.venue)) S.venue = 'hyperliquid';
  // Dźwignia nie jest do wyboru: z góry ustalona dla rynku (serwer i tak liczy po niej).
  const lev = () => S.m.max_lev;

  /* ---------- toasty i okna ---------- */
  function toast(title, msg, bad) {
    const el = document.createElement('div');
    el.className = 't-toast' + (bad ? ' bad' : '');
    el.innerHTML = `<b>${esc(title)}</b>${msg ? `<span>${esc(msg)}</span>` : ''}`;
    $('tToasts').appendChild(el);
    setTimeout(() => el.remove(), bad ? 6500 : 4200);
  }
  let modalOpen = null;
  function openModal(id) { closeModal(); modalOpen = $(id); modalOpen.hidden = false; const f = modalOpen.querySelector('input:not([type=hidden]),button.t-btn,a.t-btn'); if (f && window.innerWidth > 980) setTimeout(() => f.focus(), 30); }
  function closeModal() { if (modalOpen) { modalOpen.hidden = true; modalOpen = null; } }
  $$('.t-modal').forEach((md) => md.addEventListener('click', (e) => { if (e.target === md || e.target.closest('[data-close]')) closeModal(); }));
  function confirmBox(title, html, okLabel, cls) {
    return new Promise((res) => {
      $('tConfirmH').textContent = title;
      $('tConfirmBody').innerHTML = html;
      const b = $('tConfirmOk');
      b.textContent = okLabel || 'Confirm';
      b.className = 't-btn' + (cls ? ' ' + cls : '');
      openModal('tConfirm');
      const done = (v) => { b.onclick = null; obs.disconnect(); res(v); };
      b.onclick = () => { closeModal(); done(true); };
      const obs = new MutationObserver(() => { if ($('tConfirm').hidden) done(false); });
      obs.observe($('tConfirm'), { attributes: true, attributeFilter: ['hidden'] });
    });
  }

  /* ---------- menu rozwijane ---------- */
  /* Menu stoi w pozycji `fixed` względem ekranu: paski z przewijaniem w bok
     (narzędzia wykresu, pasek rynku, zakładki tabel) inaczej by je przycinały
     albo same robiły się przewijalne. Przewinięcie strony zamyka menu. */
  function placeMenu(btn, menu) {
    const r = btn.getBoundingClientRect();
    const up = !!menu.closest('.t-dd.up');
    Object.assign(menu.style, { position: 'fixed', right: 'auto', left: '0px', top: up ? 'auto' : r.bottom + 4 + 'px', bottom: up ? innerHeight - r.top + 6 + 'px' : 'auto' });
    const w = menu.offsetWidth;
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    if (up) return;
    // Za mało miejsca pod przyciskiem (np. zakładki tabel przy dolnej krawędzi) — menu w górę.
    const pod = innerHeight - r.bottom - 12, nad = r.top - 12;
    menu.style.maxHeight = '';
    const h = menu.offsetHeight;
    if (h > pod && nad > pod) {
      menu.style.maxHeight = nad + 'px';
      menu.style.top = Math.max(8, r.top - Math.min(h, nad) - 4) + 'px';
    } else menu.style.maxHeight = Math.max(120, pod) + 'px';
  }
  const closeMenus = () => $$('.t-menu').forEach((x) => (x.hidden = true));
  function dropdown(btn, menu, fill) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const was = !menu.hidden;
      closeMenus();
      if (!was) { if (fill) fill(menu); menu.hidden = false; placeMenu(btn, menu); }
    });
  }
  document.addEventListener('click', (e) => { if (!e.target.closest('.t-menu')) closeMenus(); });
  window.addEventListener('scroll', closeMenus, { passive: true });
  window.addEventListener('resize', closeMenus);

  /* ---------- API ---------- */
  async function api(path, opts) {
    const h = { 'Content-Type': 'application/json' };
    if (S.token) h.Authorization = 'Bearer ' + S.token;
    let r;
    try { r = await fetch(path, { ...(opts || {}), headers: h }); } catch (e) { throw new Error('Network error. Check your connection.'); }
    let d = null;
    try { d = await r.json(); } catch (e) {}
    if (r.status === 401 && S.token) { signOut(true); throw new Error((d && d.detail) || 'Session expired. Sign in again.'); }
    if (!r.ok) {
      const det = d && d.detail;
      throw new Error(Array.isArray(det) ? det.map((x) => x.msg).join('; ') : det || 'Error ' + r.status);
    }
    return d;
  }

  /* =====================================================================
     WYKRES
     ===================================================================== */
  const INT_LBL = { '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '4h': '240', '1d': '1D', '1w': '1W' };
  const PERIOD = { '1m': ['minute', 1], '3m': ['minute', 3], '5m': ['minute', 5], '15m': ['minute', 15], '30m': ['minute', 30], '1h': ['hour', 1], '4h': ['hour', 4], '1d': ['day', 1], '1w': ['week', 1] };
  let chart = null, barCb = null, histLimit = 500, lastBar = null;
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  function chartStyles() {
    const up = css('--up'), dn = css('--dn'), line = css('--line'), muted = css('--muted'), txt = css('--txt'), panel = css('--panel3'), acc = css('--acc');
    return {
      grid: { horizontal: { color: line }, vertical: { color: line } },
      candle: {
        type: S.ctype,
        bar: { upColor: up, downColor: dn, noChangeColor: muted, upBorderColor: up, downBorderColor: dn, noChangeBorderColor: muted, upWickColor: up, downWickColor: dn, noChangeWickColor: muted },
        area: { lineColor: acc, backgroundColor: [{ offset: 0, color: acc + '33' }, { offset: 1, color: acc + '00' }] },
        priceMark: {
          high: { color: muted }, low: { color: muted },
          last: { upColor: up, downColor: dn, noChangeColor: muted, text: { color: '#fff' } },
        },
        tooltip: {
          title: { show: true, color: txt, size: 14, template: `${S.sym} · ${INT_LBL[S.interval]} · ${F.VENUES[S.venue].label}` },
          legend: { color: muted, size: 12 },
        },
      },
      indicator: { tooltip: { title: { color: muted }, legend: { color: muted } }, bars: [{ upColor: up + '80', downColor: dn + '80', noChangeColor: muted }] },
      xAxis: { axisLine: { color: line }, tickLine: { color: line }, tickText: { color: muted } },
      yAxis: { axisLine: { color: line }, tickLine: { color: line }, tickText: { color: muted } },
      separator: { color: line, activeBackgroundColor: acc + '33' },
      crosshair: {
        horizontal: { line: { color: muted }, text: { backgroundColor: panel, borderColor: panel, color: txt } },
        vertical: { line: { color: muted }, text: { backgroundColor: panel, borderColor: panel, color: txt } },
      },
      overlay: { line: { color: acc }, point: { color: acc, borderColor: acc + '55', activeColor: acc, activeBorderColor: acc + '55' }, rect: { color: acc + '22', borderColor: acc }, polygon: { color: acc + '22', borderColor: acc }, text: { color: txt } },
    };
  }

  function registerOverlays() {
    // Linia ceny z etykietą: wejście, TP, SL, likwidacja, zlecenia oczekujące.
    klinecharts.registerOverlay({
      name: 'ptfLine', totalStep: 2, lock: true, needDefaultPointFigure: false, needDefaultXAxisFigure: false, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates, bounding, overlay }) => {
        const y = coordinates[0].y, e = overlay.extendData || {};
        return [
          { type: 'line', attrs: { coordinates: [{ x: 0, y }, { x: bounding.width, y }] }, styles: { style: 'dashed', dashedValue: [5, 4], color: e.color, size: 1 }, ignoreEvent: true },
          { type: 'text', attrs: { x: 10, y, text: e.text, baseline: 'middle' }, styles: { color: '#fff', backgroundColor: e.color, size: 11, weight: 600, paddingLeft: 6, paddingRight: 6, paddingTop: 3, paddingBottom: 3, borderRadius: 4, borderSize: 0 }, ignoreEvent: true },
        ];
      },
    });
  }

  function initChart() {
    if (!window.klinecharts) return;
    registerOverlays();
    chart = klinecharts.init('tChart', { styles: chartStyles(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, locale: 'en-US' });
    chart.setDataLoader({
      getBars: async ({ type, timestamp, callback }) => {
        // Każdy reset wykresu startuje własne ładowanie; odpowiedź, która
        // przyszła po kolejnym resecie (inny rynek/interwał), idzie do kosza.
        const nr = ++loadSeq;
        try {
          if (type === 'init') {
            const d = await F.history(S.venue, S.m, S.interval, Date.now(), histLimit);
            if (nr !== loadSeq) return;
            lastBar = d[d.length - 1] || null;
            callback(d, { forward: d.length > 10, backward: false });
            afterInit();
          } else if (type === 'forward') {
            const d = await F.history(S.venue, S.m, S.interval, timestamp - 1, 500);
            if (nr !== loadSeq) return;
            callback(d.filter((k) => k.timestamp < timestamp), { forward: d.length > 10, backward: false });
          } else callback([], { forward: false, backward: false });
        } catch (e) {
          if (nr !== loadSeq) return;
          callback([], { forward: false, backward: false });
          toast('Chart data unavailable', `${F.VENUES[S.venue].label}: ${e.message}`, true);
        }
      },
      subscribeBar: ({ callback }) => { barCb = callback; },
      unsubscribeBar: () => { barCb = null; },
    });
    loadIndicators();
    new ResizeObserver(() => chart && chart.resize()).observe($('tChart'));
  }
  let loadSeq = 0;
  /* Przeładowanie wykresu jednym wywołaniem: setSymbol/setPeriod same robią reset. */
  function reloadChart() {
    if (!chart) return;
    const want = { type: PERIOD[S.interval][0], span: PERIOD[S.interval][1] };
    const s = chart.getSymbol(), p = chart.getPeriod();
    let zrobione = false;
    if (!s || s.ticker !== S.sym) {
      chart.setSymbol({ ticker: S.sym, pricePrecision: S.m.px_dec, volumePrecision: Math.min(S.m.qty_dec, 4) });
      zrobione = !!p;
    }
    if (!p || p.type !== want.type || p.span !== want.span) { chart.setPeriod(want); zrobione = true; }
    if (!zrobione) chart.resetData();
  }
  let pendingRange = null;
  function afterInit() {
    if (pendingRange) { const r = pendingRange; pendingRange = null; setTimeout(() => fitRange(r), 30); }
    drawLines();
  }
  const RANGE = { '5y': ['1w', 5 * 365], '1y': ['1d', 365], '6m': ['1d', 182], '3m': ['4h', 91], '1mo': ['1h', 30], '5d': ['15m', 5], '1d': ['1m', 1] };
  function fitRange(r) {
    const [, days] = RANGE[r];
    const bars = (days * 864e5) / F.MS[S.interval];
    const w = $('tChart').clientWidth - 70;
    chart.setBarSpace(Math.max(1, Math.min(40, w / bars)));
    chart.scrollToRealTime();
  }

  // Rysowanie: kolejka do cofania/ponawiania
  const DRAW = [
    ['cross', 'Crosshair', '<path d="M12 3v18M3 12h18"/>'],
    ['segment', 'Trend line', '<path d="M4 20 20 4"/><circle cx="4" cy="20" r="1.6"/><circle cx="20" cy="4" r="1.6"/>'],
    ['rayLine', 'Ray', '<path d="M4 18 21 5"/><circle cx="4" cy="18" r="1.6"/>'],
    ['horizontalStraightLine', 'Horizontal line', '<path d="M3 12h18"/><circle cx="12" cy="12" r="1.6"/>'],
    ['horizontalRayLine', 'Horizontal ray', '<path d="M6 12h15"/><circle cx="6" cy="12" r="1.6"/>'],
    ['verticalStraightLine', 'Vertical line', '<path d="M12 3v18"/><circle cx="12" cy="12" r="1.6"/>'],
    ['parallelStraightLine', 'Parallel channel', '<path d="M3 16 17 4M7 20 21 8"/>'],
    ['priceChannelLine', 'Price channel', '<path d="M3 6h18M3 18h18M3 12h18" stroke-dasharray="2 3"/>'],
    ['fibonacciLine', 'Fibonacci retracement', '<path d="M3 5h18M3 10h18M3 14h18M3 19h18"/><path d="M5 19 19 5" stroke-dasharray="2 2"/>'],
    ['rect', 'Rectangle', '<rect x="4" y="6" width="16" height="12" rx="1.5"/>'],
    ['circle', 'Circle', '<circle cx="12" cy="12" r="8"/>'],
    ['polygon', 'Polygon', '<path d="m12 3 8 6-3 10H7L4 9z"/>'],
    ['brush', 'Brush', '<path d="M4 18c2 0 3-1 3-3s1-3 3-3 2 2 4 2 3-4 6-9"/>'],
    ['simpleAnnotation', 'Note', '<path d="M5 5h14v10H9l-4 4z"/>'],
    ['priceLine', 'Price label', '<path d="M3 12h11"/><path d="M14 8h7v8h-7z"/>'],
  ];
  const drawn = [], redo = [];
  let magnet = ls.get('pf_term_magnet', '0') === '1';
  function buildDrawRail() {
    const el = $('tDraw');
    el.innerHTML = DRAW.map(([n, t, p], i) => (i === 1 || i === 9 || i === 13 ? '<hr>' : '') + `<button type="button" data-d="${n}" title="${t}" aria-label="${t}"${n === 'cross' ? ' class="on"' : ''}><svg viewBox="0 0 24 24">${p}</svg></button>`).join('')
      + '<hr>'
      + `<button type="button" id="tMagnet" title="Magnet mode" aria-label="Magnet mode" class="${magnet ? 'on' : ''}"><svg viewBox="0 0 24 24"><path d="M6 4v8a6 6 0 0 0 12 0V4h-4v8a2 2 0 0 1-4 0V4z"/><path d="M6 8h4M14 8h4"/></svg></button>`
      + '<button type="button" id="tHideDraw" title="Hide drawings" aria-label="Hide drawings"><svg viewBox="0 0 24 24"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg></button>'
      + '<button type="button" id="tClearDraw" title="Remove all drawings" aria-label="Remove all drawings"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>';
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || !chart) return;
      if (b.id === 'tMagnet') { magnet = !magnet; b.classList.toggle('on', magnet); ls.set('pf_term_magnet', magnet ? '1' : '0'); return; }
      if (b.id === 'tHideDraw') {
        const hide = !b.classList.contains('on');
        b.classList.toggle('on', hide);
        drawn.forEach((o) => chart.overrideOverlay({ id: o.id, visible: !hide }));
        return;
      }
      if (b.id === 'tClearDraw') { drawn.splice(0).forEach((o) => chart.removeOverlay({ id: o.id })); redo.length = 0; return; }
      const n = b.dataset.d;
      $$('[data-d]', el).forEach((x) => x.classList.toggle('on', x === b));
      if (n === 'cross') return;
      chart.createOverlay({
        name: n, groupId: 'draw', mode: magnet ? 'weak_magnet' : 'normal',
        onDrawEnd: ({ overlay }) => {
          drawn.push({ id: overlay.id, name: overlay.name, points: overlay.points.map((p) => ({ timestamp: p.timestamp, value: p.value })) });
          redo.length = 0;
          $$('[data-d]', el).forEach((x) => x.classList.toggle('on', x.dataset.d === 'cross'));
          return false;
        },
        onRightClick: ({ overlay }) => { removeDrawn(overlay.id); return true; },
      });
    });
  }
  function removeDrawn(id) {
    const i = drawn.findIndex((o) => o.id === id);
    if (i >= 0) redo.push(drawn.splice(i, 1)[0]);
    chart.removeOverlay({ id });
  }
  $('tUndo').onclick = () => { const o = drawn[drawn.length - 1]; if (o) removeDrawn(o.id); };
  $('tRedo').onclick = () => {
    const o = redo.pop();
    if (!o || !chart) return;
    const id = chart.createOverlay({ name: o.name, groupId: 'draw', points: o.points });
    drawn.push({ ...o, id });
  };
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !e.target.closest('input')) { e.preventDefault(); (e.shiftKey ? $('tRedo') : $('tUndo')).click(); }
  });

  // Wskaźniki
  const IND_MAIN = [['MA', 'Moving Average'], ['EMA', 'Exponential MA'], ['SMA', 'Smoothed MA'], ['BOLL', 'Bollinger Bands'], ['SAR', 'Parabolic SAR'], ['BBI', 'Bull and Bear Index']];
  const IND_SUB = [['VOL', 'Volume'], ['MACD', 'MACD'], ['RSI', 'Relative Strength Index'], ['KDJ', 'Stochastic (KDJ)'], ['CCI', 'Commodity Channel Index'], ['WR', 'Williams %R'], ['OBV', 'On Balance Volume'], ['DMI', 'Directional Movement'], ['AO', 'Awesome Oscillator'], ['MTM', 'Momentum'], ['TRIX', 'TRIX'], ['ROC', 'Rate of Change']];
  let inds = ls.json('pf_term_inds', ['VOL']);
  function addInd(n) {
    if (IND_MAIN.some(([k]) => k === n)) chart.createIndicator({ name: n, paneId: 'candle_pane' }, true);
    else { chart.createIndicator({ name: n, paneId: 'pane_' + n }); chart.setPaneOptions({ id: 'pane_' + n, height: n === 'VOL' ? 72 : 96 }); }
  }
  function loadIndicators() { inds.forEach(addInd); }
  function toggleInd(n) {
    if (inds.includes(n)) {
      inds = inds.filter((x) => x !== n);
      chart.removeIndicator({ name: n });
    } else { inds.push(n); addInd(n); }
    ls.set('pf_term_inds', JSON.stringify(inds));
  }
  dropdown($('tIndBtn'), $('tIndMenu'), (menu) => {
    const row = ([k, t]) => `<button type="button" data-ind="${k}"><b>${k}</b><span style="color:var(--muted)">${t}</span>${inds.includes(k) ? '<span class="chk">✓</span>' : ''}</button>`;
    menu.innerHTML = '<h6>On the price chart</h6>' + IND_MAIN.map(row).join('') + '<h6>In a separate pane</h6>' + IND_SUB.map(row).join('');
  });
  $('tIndMenu').addEventListener('click', (e) => { const b = e.target.closest('[data-ind]'); if (b && chart) { toggleInd(b.dataset.ind); $('tIndMenu').hidden = true; } });

  dropdown($('tCType'), $('tCTypeMenu'), (menu) => $$('[data-ct]', menu).forEach((b) => b.classList.toggle('on', b.dataset.ct === S.ctype)));
  $('tCTypeMenu').addEventListener('click', (e) => {
    const b = e.target.closest('[data-ct]');
    if (!b) return;
    S.ctype = b.dataset.ct;
    ls.set('pf_term_ctype', S.ctype);
    chart && chart.setStyles({ candle: { type: S.ctype } });
    $('tCTypeMenu').hidden = true;
  });
  $('tInts').addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) setInterval_(b.dataset.i); });
  function setInterval_(i, keepLimit) {
    S.interval = i;
    ls.set('pf_term_int', i);
    $$('[data-i]', $('tInts')).forEach((b) => b.classList.toggle('on', b.dataset.i === i));
    if (!keepLimit) histLimit = 500;
    restream();
  }
  $('tRanges').addEventListener('click', (e) => {
    const b = e.target.closest('[data-r]');
    if (!b) return;
    const [int, days] = RANGE[b.dataset.r];
    histLimit = Math.min(4800, Math.ceil((days * 864e5) / F.MS[int]) + 60);
    pendingRange = b.dataset.r;
    setInterval_(int, true);
  });
  $('tAxis').addEventListener('click', (e) => {
    const b = e.target.closest('[data-ax]');
    if (!b || !chart) return;
    $$('[data-ax]', $('tAxis')).forEach((x) => x.classList.toggle('on', x === b));
    chart.overrideYAxis({ paneId: 'candle_pane', name: b.dataset.ax });
  });
  $('tShot').onclick = () => {
    if (!chart) return;
    const a = document.createElement('a');
    a.href = chart.getConvertPictureUrl(true, 'png', css('--bg'));
    a.download = `${S.sym}-${S.interval}.png`;
    a.click();
  };
  $('tFull').onclick = () => { root.classList.toggle('full-chart'); setTimeout(() => chart && chart.resize(), 30); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if (root.classList.contains('full-chart')) { root.classList.remove('full-chart'); chart && chart.resize(); } closeModal(); $$('.t-menu').forEach((x) => (x.hidden = true)); } });

  // zegar
  function clock() {
    const d = new Date(), off = -d.getTimezoneOffset() / 60;
    $('tClock').textContent = d.toLocaleTimeString('en-GB', { hour12: false }) + ' UTC' + (off >= 0 ? '+' : '') + off;
  }
  setInterval(clock, 1000); clock();

  // zakładki wykresu
  $$('[data-ctab]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-ctab]').forEach((x) => x.classList.toggle('on', x === b));
    $$('[data-cpane]').forEach((p) => p.classList.toggle('on', p.dataset.cpane === b.dataset.ctab));
    if (b.dataset.ctab === 'chart') setTimeout(() => chart && chart.resize(), 20);
    else renderInfo();
  }));
  function renderInfo() {
    const m = S.m, t = S.tk, c = S.ctx[m.hl] || {};
    const kv = (k, v, p) => `<div class="t-kv"><small>${k}</small><b class="num">${v}</b>${p ? `<p>${p}</p>` : ''}</div>`;
    $('tData').innerHTML = [
      kv('Mark price', px(m, t.mark || c.mark)), kv('24h change', `<span class="${cls(t.chg)}">${pct(t.chg)}</span>`),
      kv('24h volume', big(t.vol || c.vol)), kv('Open interest', big(t.oiUsd || (c.oi && c.mark ? c.oi * c.mark : null))),
      kv('Funding (1h)', ok(t.funding) ? (t.funding * 100).toFixed(4) + '%' : '—', 'Exchange funding rate, for reference. PTF positions do not pay funding.'),
      kv('Spread', $('tSpread').textContent), kv('Bid depth', $('tBidD').textContent), kv('Ask depth', $('tAskD').textContent),
    ].join('');
    $('tInfo').innerHTML = [
      kv('Market', `${m.sym} · ${esc(m.name)}`), kv('Category', CLS[m.cls]),
      kv('Max leverage', m.max_lev + '×'), kv('Contract size', `1 lot = ${fx(m.contract, 0)} ${m.base}`, 'Lots count toward the exposure cap of your account.'),
      kv('Trading fee', S.fee.toFixed(3) + '% per side', 'Charged on the order value when a position opens and when it closes.'),
      kv('Trading hours', m.cls === 'crypto' ? '24/7' : 'Mon–Fri', m.cls === 'crypto' ? 'On weekends with the Weekend Trading add-on.' : 'Closed Saturday and Sunday (server time). Open positions are valued at the Friday close.'),
      kv('Prices', F.VENUES[S.venue].label, 'Orders, TP/SL and liquidations settle at Hyperliquid prices.'),
    ].join('');
  }

  /* =====================================================================
     STRUMIEŃ RYNKU
     ===================================================================== */
  let strm = null, bookRaf = 0;
  function restream() {
    if (strm) strm.close();
    S.book = { bids: [], asks: [] }; S.trades = []; S.tk = {};
    renderBook(); renderTrades(); renderTicker();
    if (chart) {
      lastBar = null;
      chart.setStyles({ candle: { tooltip: { title: { template: `${S.sym} · ${INT_LBL[S.interval]} · ${F.VENUES[S.venue].label}` } } } });
      reloadChart();
    }
    strm = F.stream(S.venue, S.m, S.interval, {
      book: (b, a) => { S.book = { bids: b, asks: a }; if (!bookRaf) bookRaf = requestAnimationFrame(() => { bookRaf = 0; renderBook(); updateTicket(); }); },
      trades: (list) => { S.trades = list.concat(S.trades).sort((x, y) => y.t - x.t).slice(0, 80); renderTrades(); },
      candle: (k) => { if (barCb && lastBar && k.timestamp >= lastBar.timestamp) { lastBar = k; barCb(k); } else if (barCb && !lastBar) { lastBar = k; barCb(k); } },
      ticker: (t) => { S.tk = { ...S.tk, ...t }; renderTicker(); },
      status: (s) => { venueState = s; renderConn(); },
    });
  }
  let venueState = 'connecting', hlState = 'connecting', latency = null;
  F.onHlState((s) => { hlState = s; renderConn(); });
  F.onLatency((l) => { latency = l; renderConn(); });
  function renderConn() {
    const st = S.venue === 'hyperliquid' ? hlState : venueState;
    const el = $('tConn');
    el.className = 't-conn ' + (st === 'open' ? 'ok' : st === 'closed' ? 'bad' : '');
    el.querySelector('b').textContent = st === 'open' ? 'Connected' : st === 'closed' ? 'Reconnecting' : 'Connecting';
    $('tLat').textContent = st === 'open' && latency != null ? latency + 'ms' : '';
  }

  /* ---------- pasek rynku ---------- */
  function markPx() {
    const b = S.book.bids[0], a = S.book.asks[0];
    return S.tk.mark || (b && a ? (b[0] + a[0]) / 2 : S.mids[S.m.hl]) || null;
  }
  function renderTicker() {
    const m = S.m, c = S.ctx[m.hl] || {};
    const mark = markPx();
    $('tMark').textContent = px(m, mark);
    const chg = ok(S.tk.chg) ? S.tk.chg : c.prev && mark ? (mark / c.prev - 1) * 100 : null;
    $('tChg').textContent = pct(chg);
    $('tChg').className = 'num ' + cls(chg);
    $('tOi').textContent = big(S.tk.oiUsd || (c.oi && c.mark ? c.oi * c.mark : null));
    $('tVol').textContent = big(S.tk.vol || c.vol);
    if (mark) document.title = `${px(m, mark)} | ${m.sym} · Trade`;
  }
  function renderMarketHead() {
    const m = S.m;
    $('tSymIc').innerHTML = mIcon(m);
    $('tSymName').textContent = m.sym;
    $('tMaxLev').textContent = m.max_lev + '×';
    $('tVenueIc').innerHTML = V_ICON[S.venue];
    $('tVenueLbl').textContent = F.VENUES[S.venue].label;
    const note = S.venue === 'hyperliquid'
      ? 'Prices, the order book and fills come from Hyperliquid.'
      : `Prices, the order book and trades come from ${F.VENUES[S.venue].label}. Orders fill at Hyperliquid prices.`;
    $('tSrc').textContent = note;
    $('tFillNote').textContent = note + ` Leverage on ${m.sym} is 1:${m.max_lev}, the same on every account. Margin is shared across the account; if equity falls to half of the margin in use, open positions are closed (stop-out).`;
    $('tEstLev').textContent = '1:' + m.max_lev;
    $('tSizeUnitLbl').textContent = unitLbl();
    $('tGo').textContent = (S.side === 'buy' ? 'Buy / Long ' : 'Sell / Short ') + baseName(m);
    buildBookCtl();
    renderInfo();
  }
  const baseName = (m) => (m.cls === 'crypto' ? m.base : m.sym);

  // giełda (venue)
  const venueMenu = document.createElement('div');
  venueMenu.className = 't-menu';
  venueMenu.hidden = true;
  $('tVenueBtn').parentElement.style.position = 'relative';
  $('tVenueBtn').parentElement.appendChild(venueMenu);
  dropdown($('tVenueBtn'), venueMenu, (menu) => {
    const dost = F.venuesFor(S.m);
    menu.innerHTML = Object.values(F.VENUES).map((v) => `<button type="button" data-v="${v.key}" ${dost.includes(v.key) ? '' : 'disabled style="opacity:.4"'}><span class="v-ic">${V_ICON[v.key]}</span>${v.label}${v.key === 'hyperliquid' ? '<span class="tag">Default</span>' : ''}${v.key === S.venue ? '<span class="chk">✓</span>' : ''}</button>`).join('');
  });
  venueMenu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-v]');
    if (!b || b.disabled) return;
    S.venue = b.dataset.v;
    ls.set('pf_term_venue', S.venue);
    venueMenu.hidden = true;
    renderMarketHead();
    restream();
    renderConn();
  });

  /* ---------- przełączenie rynku ---------- */
  function setMarket(sym, push) {
    if (!BY[sym] || sym === S.sym) return;
    S.sym = sym; S.m = BY[sym];
    if (!F.venuesFor(S.m).includes(S.venue)) S.venue = 'hyperliquid';
    S.group = null;
    if (push !== false) history.replaceState(null, '', '/trade/' + sym);
    renderMarketHead();
    restream();
    renderStrip();
    resetTicket();
    renderTables();
  }

  /* =====================================================================
     KSIĘGA I TRANSAKCJE
     ===================================================================== */
  function buildBookCtl() {
    const m = S.m;
    const t = Math.pow(10, -Math.min(m.px_dec, 6));
    const opts = [1, 10, 100, 1000].map((k) => +(t * k).toFixed(Math.max(0, m.px_dec)));
    if (!S.group || !opts.includes(S.group)) S.group = opts[0];
    $('tGroup').innerHTML = opts.map((g) => `<option value="${g}"${g === S.group ? ' selected' : ''}>${g}</option>`).join('');
    $('tUnit').innerHTML = `<option value="base"${S.bookUnit === 'base' ? ' selected' : ''}>${esc(m.base)}</option><option value="usd"${S.bookUnit === 'usd' ? ' selected' : ''}>USD</option>`;
    const u = S.bookUnit === 'usd' ? 'USD' : m.base;
    $('tBhSize').textContent = `Size (${u})`;
    $('tBhTot').textContent = `Total (${u})`;
    $('tThSize').textContent = `Size (${u})`;
  }
  $('tGroup').onchange = (e) => { S.group = +e.target.value; renderBook(); };
  $('tUnit').onchange = (e) => { S.bookUnit = e.target.value; buildBookCtl(); renderBook(); renderTrades(); };
  function grouped(levels, side) {
    const g = S.group || 0, out = new Map();
    levels.forEach(([p, q]) => {
      const k = g ? (side === 'bid' ? Math.floor(p / g + 1e-9) * g : Math.ceil(p / g - 1e-9) * g) : p;
      const key = k.toFixed(8);
      out.set(key, (out.get(key) || 0) + q);
    });
    return [...out].map(([k, q]) => [+k, q]).sort((a, b) => (side === 'bid' ? b[0] - a[0] : a[0] - b[0]));
  }
  function renderBook() {
    const m = S.m, el = $('tAsks');
    const rows = Math.max(5, Math.floor((el.clientHeight || 230) / 21));
    const asks = grouped(S.book.asks, 'ask').slice(0, rows), bids = grouped(S.book.bids, 'bid').slice(0, rows);
    const val = (p, q) => (S.bookUnit === 'usd' ? notional(m, q, p) : q);
    const sd = S.bookUnit === 'usd' ? 0 : Math.min(m.qty_dec, 4);
    let ca = 0, cb = 0;
    const aRows = asks.map(([p, q]) => { ca += val(p, q); return [p, val(p, q), ca]; });
    const bRows = bids.map(([p, q]) => { cb += val(p, q); return [p, val(p, q), cb]; });
    const max = Math.max(ca, cb) || 1;
    const row = ([p, q, t]) => `<div class="t-row" data-px="${p}"><i style="width:${((t / max) * 100).toFixed(1)}%"></i><span>${px(m, p)}</span><span>${fx(q, sd)}</span><span>${fx(t, sd)}</span></div>`;
    el.innerHTML = aRows.slice().reverse().map(row).join('');
    $('tBids').innerHTML = bRows.map(row).join('');
    const a = S.book.asks[0], b = S.book.bids[0];
    if (a && b) {
      $('tSpread').textContent = px(m, a[0] - b[0]);
      $('tSpreadPct').textContent = (((a[0] - b[0]) / ((a[0] + b[0]) / 2)) * 100).toFixed(5) + '%';
    }
    const sum = (ca + cb) || 1;
    $('tBidD').textContent = ((cb / sum) * 100).toFixed(1) + '%';
    $('tAskD').textContent = ((ca / sum) * 100).toFixed(1) + '%';
    $('tBidBar').style.flexGrow = cb / sum;
    $('tAskBar').style.flexGrow = ca / sum;
    if (!S.tk.mark) renderTicker();
  }
  [$('tAsks'), $('tBids')].forEach((el) => el.addEventListener('click', (e) => {
    const r = e.target.closest('[data-px]');
    if (!r) return;
    if (S.kind === 'market') setKind('limit');
    $('tPx').value = px(S.m, +r.dataset.px).replace(/,/g, '');
    updateTicket();
  }));
  function renderTrades() {
    const m = S.m, sd = S.bookUnit === 'usd' ? 0 : Math.min(m.qty_dec, 4);
    $('tTrades').innerHTML = S.trades.slice(0, 60).map((t) => `<div class="t-row"><span class="${t.side === 'buy' ? 'up' : 'dn'}">${px(m, t.px)}</span><span>${fx(S.bookUnit === 'usd' ? notional(m, t.sz, t.px) : t.sz, sd)}</span><span>${new Date(t.t).toLocaleTimeString('en-GB', { hour12: false })}</span></div>`).join('');
  }
  $$('[data-btab]').forEach((b) => b.addEventListener('click', () => {
    $$('[data-btab]').forEach((x) => x.classList.toggle('on', x === b));
    $$('[data-bpane]').forEach((p) => p.classList.toggle('on', p.dataset.bpane === b.dataset.btab));
  }));

  /* =====================================================================
     PASEK TICKERÓW, WYSZUKIWARKA, STOPKA
     ===================================================================== */
  const mid = (m) => { const v = S.mids[m.hl]; return v != null ? +v : (S.ctx[m.hl] || {}).mid || null; };
  const chg24 = (m) => { const c = S.ctx[m.hl] || {}, p = mid(m); return c.prev && p ? (p / c.prev - 1) * 100 : null; };
  const vol24 = (m) => (S.ctx[m.hl] || {}).vol || 0;
  function sorted(mode) {
    let list = MKTS.slice();
    if (mode === 'trending') list.sort((a, b) => Math.abs(chg24(b) || 0) - Math.abs(chg24(a) || 0));
    else if (mode === 'volume') list.sort((a, b) => vol24(b) - vol24(a));
    else if (mode === 'watch') list = list.filter((m) => S.watch.includes(m.sym));
    return list;
  }
  const MODES = { trending: 'Trending', volume: 'Top Volume', watch: 'Watchlist', all: 'All markets' };
  let stripT = 0;
  function renderStrip() {
    if (stripT) return;
    stripT = setTimeout(() => {
      stripT = 0;
      $('tTickModeLbl').textContent = MODES[S.tickMode];
      $('tTicker').innerHTML = sorted(S.tickMode).map((m) => {
        const c = chg24(m);
        return `<button class="t-ti${m.sym === S.sym ? ' on' : ''}" type="button" data-sym="${m.sym}">${mIcon(m, true)}<b>${m.sym}</b><span class="num">${px(m, mid(m))}</span><span class="num ${cls(c)}">${pct(c)}</span></button>`;
      }).join('') || '<span class="t-ti" style="color:var(--muted)">Star markets in search to build a watchlist.</span>';
    }, 250);
  }
  $('tTicker').addEventListener('click', (e) => { const b = e.target.closest('[data-sym]'); if (b) setMarket(b.dataset.sym); });
  $('tTickMode').onclick = () => {
    const keys = Object.keys(MODES);
    S.tickMode = keys[(keys.indexOf(S.tickMode) + 1) % keys.length];
    ls.set('pf_term_tick', S.tickMode);
    renderStrip();
  };
  $('tTickPrev').onclick = () => { $('tTicker').scrollLeft -= 360; };
  $('tTickNext').onclick = () => { $('tTicker').scrollLeft += 360; };

  F.allMids((mids) => {
    for (const k in mids) if (BY_COIN[k]) S.mids[k] = +mids[k];
    renderStrip();
    if (S.st) renderLivePnl();
    if (!$('tSearch').hidden) renderSearch();
  });
  async function loadCtx() { try { S.ctx = await F.contexts(); renderStrip(); renderTicker(); } catch (e) {} }
  loadCtx();
  setInterval(loadCtx, 30000);

  // wyszukiwarka rynków
  let sCat = 'all', sIdx = 0, sList = [];
  function renderSearch() {
    const q = $('tSearchIn').value.trim().toLowerCase();
    $('tSearchCats').innerHTML = [['all', 'All'], ['watch', 'Watchlist'], ...Object.entries(CLS)].map(([k, l]) => `<button type="button" data-cat="${k}" class="${k === sCat ? 'on' : ''}">${l}</button>`).join('');
    sList = MKTS.filter((m) => (sCat === 'all' || (sCat === 'watch' ? S.watch.includes(m.sym) : m.cls === sCat))
      && (!q || m.sym.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)));
    sIdx = Math.min(sIdx, Math.max(0, sList.length - 1));
    $('tSearchList').innerHTML = sList.map((m, i) => {
      const c = chg24(m), st = S.watch.includes(m.sym);
      return `<div class="t-sr${i === sIdx ? ' on' : ''}" data-sym="${m.sym}"><div class="nm">${mIcon(m)}<b>${m.sym}</b><small>${esc(m.name)}</small></div><span class="num">${px(m, mid(m))}</span><span class="num ${cls(c)}">${pct(c)}</span><span class="num vol">${big(vol24(m))}</span><button type="button" class="star${st ? ' on' : ''}" data-star="${m.sym}" aria-label="Watchlist"><svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg></button></div>`;
    }).join('') || '<p class="t-empty">No markets match.</p>';
  }
  function toggleWatch(sym) {
    S.watch = S.watch.includes(sym) ? S.watch.filter((x) => x !== sym) : S.watch.concat(sym);
    ls.set('pf_term_watch', JSON.stringify(S.watch));
  }
  function openSearch() { $('tSearchIn').value = ''; sCat = 'all'; sIdx = 0; renderSearch(); openModal('tSearch'); setTimeout(() => $('tSearchIn').focus(), 30); }
  $('tSym').onclick = openSearch;
  $('tSearchBtn').onclick = openSearch;
  $('tSearchIn').oninput = () => { sIdx = 0; renderSearch(); };
  $('tSearchCats').addEventListener('click', (e) => { const b = e.target.closest('[data-cat]'); if (b) { sCat = b.dataset.cat; sIdx = 0; renderSearch(); } });
  $('tSearchList').addEventListener('click', (e) => {
    const st = e.target.closest('[data-star]');
    if (st) { e.stopPropagation(); toggleWatch(st.dataset.star); renderSearch(); renderStrip(); return; }
    const r = e.target.closest('[data-sym]');
    if (r) { closeModal(); setMarket(r.dataset.sym); }
  });
  $('tSearchIn').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sIdx = Math.min(sList.length - 1, sIdx + 1); renderSearch(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { sIdx = Math.max(0, sIdx - 1); renderSearch(); e.preventDefault(); }
    else if (e.key === 'Enter' && sList[sIdx]) { closeModal(); setMarket(sList[sIdx].sym); }
  });
  document.addEventListener('keydown', (e) => { if (e.key === '/' && !e.target.closest('input,textarea') && !modalOpen) { e.preventDefault(); openSearch(); } });

  // stopka: listy rynków i kalkulator
  const flist = (list, star) => list.map((m) => { const c = chg24(m); return `<button type="button" class="fl" data-sym="${m.sym}">${mIcon(m, true)}<b>${m.sym}</b><span class="num">${px(m, mid(m))}</span><span class="num ${cls(c)}">${pct(c)}</span></button>`; }).join('') || `<p class="t-hint" style="padding:10px">${star ? 'Star markets in search to build a watchlist.' : 'Loading…'}</p>`;
  dropdown($('tFTickers'), $('tFTickersMenu'), (mn) => { mn.innerHTML = flist(MKTS); });
  dropdown($('tFTop'), $('tFTopMenu'), (mn) => { mn.innerHTML = flist(sorted('volume').slice(0, 12)); });
  dropdown($('tFWatch'), $('tFWatchMenu'), (mn) => { mn.innerHTML = flist(sorted('watch'), true); });
  ['tFTickersMenu', 'tFTopMenu', 'tFWatchMenu'].forEach((id) => $(id).addEventListener('click', (e) => { const b = e.target.closest('[data-sym]'); if (b) { $(id).hidden = true; setMarket(b.dataset.sym); } }));
  $('tFTickIc').innerHTML = '';
  dropdown($('tFCalc'), $('tCalcMenu'), (mn) => {
    const eq = S.st ? S.st.account.equity : 100000;
    mn.innerHTML = `<h6 style="padding:0">Position size calculator · ${S.m.sym}</h6>
      <label class="t-fld"><span>Account equity</span><div class="t-in"><input id="cEq" inputmode="decimal" value="${eq.toFixed(0)}"><em>USD</em></div></label>
      <label class="t-fld"><span>Risk per trade</span><div class="t-in"><input id="cRisk" inputmode="decimal" value="1"><em>%</em></div></label>
      <label class="t-fld"><span>Entry</span><div class="t-in"><input id="cEn" inputmode="decimal" value="${(markPx() || 0).toFixed(S.m.px_dec)}"><em>USD</em></div></label>
      <label class="t-fld"><span>Stop loss</span><div class="t-in"><input id="cSl" inputmode="decimal" placeholder="price"><em>USD</em></div></label>
      <div class="row"><span>Size</span><b class="num" id="cOut">—</b></div><div class="row"><span>Lots</span><b class="num" id="cLots">—</b></div><div class="row"><span>Value</span><b class="num" id="cVal">—</b></div>`;
    const calc = () => {
      const eqv = +$('cEq').value, r = +$('cRisk').value / 100, en = +$('cEn').value, sl = +$('cSl').value;
      if (!(eqv > 0 && r > 0 && en > 0 && sl > 0 && sl !== en)) { $('cOut').textContent = '—'; return; }
      const risk = eqv * r;
      const q = S.m.quote_inverse ? (risk * sl) / Math.abs(en - sl) : risk / Math.abs(en - sl);
      $('cOut').textContent = qtyF(S.m, q) + ' ' + S.m.base;
      $('cLots').textContent = fx(q / S.m.contract, 2);
      $('cVal').textContent = usd(notional(S.m, q, en));
    };
    mn.addEventListener('input', calc);
    mn.addEventListener('click', (e) => e.stopPropagation());
  });

  /* =====================================================================
     BILET ZLECENIA
     ===================================================================== */
  function setKind(k) {
    S.kind = k;
    $$('[data-ot]').forEach((b) => b.classList.toggle('on', b.dataset.ot === (k === 'stop' ? 'adv' : k)));
    $('tAdvLbl').textContent = k === 'stop' ? 'Stop Market' : 'Advanced';
    $('tPxRow').hidden = k === 'market';
    $('tPxLbl').textContent = k === 'stop' ? 'Trigger price' : 'Limit price';
    if (k !== 'market' && !$('tPx').value) { const p = markPx(); if (p) $('tPx').value = p.toFixed(S.m.px_dec); }
    updateTicket();
  }
  $$('.t-otabs > [data-ot]').forEach((b) => b.addEventListener('click', () => setKind(b.dataset.ot)));
  dropdown($('tAdvBtn'), $('tAdvMenu'));
  $('tAdvMenu').addEventListener('click', (e) => { const b = e.target.closest('[data-adv]'); if (b) { $('tAdvMenu').hidden = true; setKind(b.dataset.adv); } });
  function setSide(s) {
    S.side = s;
    $$('[data-side]').forEach((b) => b.classList.toggle('on', b.dataset.side === s));
    $('tGo').className = 't-go ' + s;
    $('tGo').textContent = (s === 'buy' ? 'Buy / Long ' : 'Sell / Short ') + baseName(S.m);
    $('tQuickSide').textContent = s === 'buy' ? 'Long' : 'Short';
    $('tQuick').classList.toggle('sell', s === 'sell');
    updateTicket();
  }
  $$('[data-side]').forEach((b) => b.addEventListener('click', () => setSide(b.dataset.side)));
  $('tPxMid').onclick = () => { const p = markPx(); if (p) { $('tPx').value = p.toFixed(S.m.px_dec); updateTicket(); } };
  /* Pole Size w wybranej jednostce: Lots → USD → jednostka bazowa → Lots. */
  const UNITS = ['lots', 'usd', 'base'];
  const unitLbl = () => (S.unit === 'usd' ? 'USD' : S.unit === 'lots' ? 'Lots' : S.m.base);
  function toUnit(q) {
    if (!(q > 0)) return '';
    if (S.unit === 'usd') return notional(S.m, q, refPx()).toFixed(2);
    if (S.unit === 'lots') return (Math.floor((q / S.m.contract) * 100 + 1e-9) / 100).toFixed(2);
    return q.toFixed(S.m.qty_dec);
  }
  $('tSizeUnit').onclick = () => {
    const q = orderQty();
    S.unit = UNITS[(UNITS.indexOf(S.unit) + 1) % UNITS.length];
    ls.set('pf_term_unit2', S.unit);
    $('tSizeUnitLbl').textContent = unitLbl();
    if (q > 0) $('tSize').value = toUnit(q);
    updateTicket();
  };
  function refPx() {
    if (S.kind !== 'market') { const p = +$('tPx').value; if (p > 0) return p; }
    const a = S.book.asks[0], b = S.book.bids[0];
    return (S.side === 'buy' ? a && a[0] : b && b[0]) || markPx() || 0;
  }
  function orderQty() {
    const v = +String($('tSize').value).replace(/,/g, '');
    if (!(v > 0)) return 0;
    return S.unit === 'usd' ? qtyFromUsd(S.m, v, refPx()) : S.unit === 'lots' ? v * S.m.contract : v;
  }
  function maxQty() {
    if (!S.st) return 0;
    const avail = S.st.account.available, p = refPx(), L = lev();
    if (!p || !(avail > 0)) return 0;
    const usdMax = (avail * L) / (1 + (S.fee / 100) * L);
    return qtyFromUsd(S.m, usdMax, p);
  }
  function setPct(p) {
    p = Math.max(0, Math.min(100, Math.round(p)));
    $('tPct').value = p; $('tPctIn').value = p;
    $('tSize').value = toUnit((maxQty() * p) / 100);
    updateTicket();
  }
  $('tPct').oninput = (e) => setPct(+e.target.value);
  $('tPctIn').onchange = (e) => setPct(+e.target.value || 0);
  $('tSize').oninput = () => { const mq = maxQty(), q = orderQty(); const p = mq ? Math.min(100, (q / mq) * 100) : 0; $('tPct').value = p; $('tPctIn').value = Math.round(p); updateTicket(); };
  $('tPx').oninput = updateTicket;
  $('tReduce').onchange = updateTicket;
  $('tTpsl').onchange = () => { $('tTpslBox').hidden = !$('tTpsl').checked; updateTicket(); };
  [$('tTp'), $('tSl')].forEach((el) => (el.oninput = updateTicket));
  $('tSlipBtn').onclick = () => {
    const opts = [0.25, 0.5, 1, 2, 3, 5];
    S.slip = opts[(opts.indexOf(S.slip) + 1) % opts.length] || 1;
    ls.set('pf_term_slip', String(S.slip));
    updateTicket();
  };
  function resetTicket() {
    $('tSize').value = ''; $('tPx').value = ''; $('tTp').value = ''; $('tSl').value = '';
    $('tPct').value = 0; $('tPctIn').value = 0;
    if (S.kind !== 'market') setKind(S.kind);
    $('tSizeUnitLbl').textContent = unitLbl();
    renderQuick(); updateTicket();
  }
  function updateTicket() {
    const m = S.m, q = orderQty(), p = refPx(), L = lev();
    const val = q > 0 ? notional(m, q, p) : 0;
    // Przelicznik pod polem Size: loty ⇄ jednostki bazowe (⇄ USD).
    $('tSizeHint').textContent = q > 0 ? `${lotsF(q / m.contract)} lots = ${qtyF(m, q)} ${m.base}${S.unit !== 'usd' && val ? ' ≈ ' + usd(val, 0) : ''}` : `1 lot = ${fx(m.contract, 0)} ${m.base}`;
    $('tEstPx').textContent = p ? px(m, p) : '—';
    $('tEstVal').textContent = val ? usd(val) : '—';
    $('tEstMargin').textContent = val ? usd(val / L) : '—';
    $('tSlip').textContent = S.kind === 'market' ? S.slip + '%' : '—';
    $('tFee').textContent = S.fee.toFixed(3) + '%';
    const tp = +$('tTp').value, sl = +$('tSl').value, ref = p || markPx();
    $('tTpPct').textContent = tp > 0 && ref ? pct(((S.side === 'buy' ? tp - ref : ref - tp) / ref) * 100 * L) : 'ROE';
    $('tSlPct').textContent = sl > 0 && ref ? pct(((S.side === 'buy' ? sl - ref : ref - sl) / ref) * 100 * L) : 'ROE';
    if (S.st) {
      const a = S.st.account;
      $('tCap').textContent = `${fx(a.open_lots, 2)} / ${fx(a.max_lots, 2)} lots`;
    } else $('tCap').textContent = S.token ? '—' : 'Sign In';
  }
  function renderQuick() {
    $('tQuick').innerHTML = S.quick.map((v, i) => `<button type="button" data-q="${i}">$${fx(v, 0)}</button>`).join('');
  }
  $('tQuick').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-q]');
    if (!b) return;
    const v = S.quick[+b.dataset.q];
    if (!S.token) return openModal('tReady');
    setKind('market');
    // Szybki rozmiar jest w USD niezależnie od jednostki pola — ilość idzie wprost.
    submitOrder(true, qtyFromUsd(S.m, v, refPx()));
  });
  $('tQuickEdit').onclick = () => {
    if ($('tQuick').querySelector('input')) {
      S.quick = $$('input', $('tQuick')).map((i) => Math.max(1, +i.value || 0));
      ls.set('pf_term_quick', JSON.stringify(S.quick));
      renderQuick();
      return;
    }
    $('tQuick').innerHTML = S.quick.map((v) => `<input inputmode="decimal" value="${v}" aria-label="Quick size in USD">`).join('');
    $('tQuick').querySelector('input').focus();
  };
  $('tQuick').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.matches('input')) $('tQuickEdit').click(); });


  let sending = false;
  async function submitOrder(quick, qtyOverride) {
    if (!S.token) return openModal('tReady');
    if (!S.st || !S.st.account.tradable) {
      return toast('This account cannot trade', (S.st && S.st.account.reason) || 'Choose an active challenge account.', true);
    }
    const m = S.m, q = qtyOverride || orderQty();
    if (!(q > 0)) return toast('Enter a size', 'Type an amount or use the slider.', true);
    const body = {
      account_id: S.accId, symbol: m.sym, side: S.side, kind: S.kind, qty: +q.toFixed(Math.max(m.qty_dec, 2) + 2),
      leverage: lev(), reduce_only: $('tReduce').checked, max_slippage_pct: S.slip,
    };
    if (S.kind !== 'market') body.price = +$('tPx').value;
    if ($('tTpsl').checked) { if (+$('tTp').value) body.tp = +$('tTp').value; if (+$('tSl').value) body.sl = +$('tSl').value; }
    if (quick && ls.get('pf_term_quick_ok', '0') !== '1') {
      const okk = await confirmBox('Confirm quick order', `<p>${S.side === 'buy' ? 'Buy / Long' : 'Sell / Short'} <b>${usd(notional(m, q, refPx()))}</b> of ${m.sym} at market, ${lev()}× leverage.</p>
        <label class="ck" style="margin-top:14px"><input type="checkbox" id="tQuickNoAsk"><span>Don't ask again for quick orders</span></label>`, S.side === 'buy' ? 'Buy / Long' : 'Sell / Short');
      if (!okk) return;
    }
    if (document.getElementById('tQuickNoAsk') && document.getElementById('tQuickNoAsk').checked) ls.set('pf_term_quick_ok', '1');
    if (sending) return;
    sending = true;
    $('tGo').disabled = true;
    try {
      const st = await api('/api/terminal/orders', { method: 'POST', body: JSON.stringify(body) });
      setState(st);
      const what = S.kind === 'market' ? 'Order filled' : 'Order placed';
      const z = st.order_history && st.order_history[0];
      toast(what, `${S.side === 'buy' ? 'Long' : 'Short'} ${lotsOf(m, body.qty)} (${qtyF(m, body.qty)} ${m.base}) ${m.sym}${z && z.fill_price && S.kind === 'market' ? ' at ' + px(m, z.fill_price) : body.price ? ' at ' + px(m, body.price) : ''}`);
      $('tSize').value = ''; $('tPct').value = 0; $('tPctIn').value = 0;
      updateTicket();
    } catch (e) {
      toast('Order rejected', e.message, true);
    } finally {
      sending = false;
      $('tGo').disabled = false;
    }
  }
  $('tGo').onclick = () => submitOrder(false);

  /* =====================================================================
     KONTO, LOGOWANIE, STAN
     ===================================================================== */
  /* Wylogowanie = wylogowanie z PTF (ta sama sesja co portal). `silent` — sesja
     wygasła sama (401), wtedy tylko czyścimy stan. */
  function signOut(silent) {
    S.token = null; S.st = null; S.accounts = [];
    ls.del('pf_token');
    if (!silent) toast('Signed out');
    renderUser(); renderTables(); renderAccount(); drawLines(); updateTicket();
  }
  function renderUser() {
    $('tGuest').hidden = !!S.token;
    $('tUser').hidden = !S.token;
    const a = S.accounts.find((x) => x.id === S.accId);
    $('tAccLbl').textContent = a ? `#${a.login} · ${big(a.size).replace('.0K', 'K')}` : 'No account';
    $('tAccBtn').querySelector('.dot').classList.toggle('off', !(a && a.tradable));
  }
  const accMenu = document.createElement('div');
  accMenu.className = 't-menu';
  accMenu.hidden = true;
  accMenu.style.cssText = 'left:auto;right:0;min-width:260px';
  $('tAccBtn').parentElement.classList.add('t-dd');
  $('tAccBtn').parentElement.appendChild(accMenu);
  dropdown($('tAccBtn'), accMenu, (mn) => {
    mn.innerHTML = '<h6>Accounts</h6>' + (S.accounts.map((a) => `<button type="button" data-acc="${a.id}"><span class="mi sm" style="background:${a.tradable ? 'var(--up)' : 'var(--dim)'}"></span>#${esc(a.login)} · ${big(a.size)}<span class="tag">${phaseLbl(a)}</span>${a.id === S.accId ? '<span class="chk">✓</span>' : ''}</button>`).join('') || '<p class="t-hint" style="padding:8px 10px">No active challenge accounts.</p>')
      + '<a href="/portal">Open dashboard</a><a href="/#pricing">Get a new challenge</a>'
      + '<button type="button" data-out="1">Sign out</button>';
  });
  accMenu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-acc]');
    if (b) { accMenu.hidden = true; selectAccount(+b.dataset.acc); }
    if (e.target.closest('[data-out]')) { accMenu.hidden = true; signOut(); }
  });
  const phaseLbl = (a) => (a.status === 'funded' || a.phase === 'funded' ? 'Funded' : a.phase === 'eval_2' ? 'Phase 2' : a.status === 'passed' ? 'Passed' : 'Phase 1');

  async function loadAccounts() {
    if (!S.token) { renderUser(); renderTables(); renderAccount(); return; }
    try {
      const d = await api('/api/terminal/accounts');
      S.accounts = d.accounts;
      if (!S.accounts.some((a) => a.id === S.accId)) {
        const t = S.accounts.find((a) => a.tradable) || S.accounts[0];
        S.accId = t ? t.id : null;
      }
      renderUser();
      if (S.accId) await refresh(); else { renderTables(); renderAccount(); }
    } catch (e) { renderUser(); }
  }
  function selectAccount(id) {
    S.accId = id;
    ls.set('pf_term_acc', String(id));
    renderUser();
    refresh();
  }
  let refreshing = false;
  async function refresh() {
    if (!S.token || !S.accId || refreshing) return;
    refreshing = true;
    try { setState(await api('/api/terminal/state?account_id=' + S.accId)); } catch (e) {} finally { refreshing = false; }
  }
  function setState(st) {
    const before = S.st;
    S.st = st;
    const a = S.accounts.find((x) => x.id === st.account.id);
    if (a) Object.assign(a, { balance: st.account.balance, status: st.account.status, tradable: st.account.tradable });
    if (before) notifyFills(before, st);
    S.fee = st.fee_pct;
    renderUser(); renderAccount(); renderTables(); drawLines(); updateTicket();
  }
  function notifyFills(a, b) {
    const was = new Set(a.positions.map((p) => p.id));
    const now = new Set(b.positions.map((p) => p.id));
    b.history.filter((h) => was.has(h.id) && !now.has(h.id) && ['tp', 'sl', 'liquidation', 'stop_out', 'breach'].includes(h.reason)).forEach((h) => {
      const L = { tp: 'Take profit hit', sl: 'Stop loss hit', liquidation: 'Position liquidated', stop_out: 'Stop-out: margin level too low', breach: 'Closed: account rule breached' }[h.reason];
      toast(L, `${h.symbol} ${sUsd(h.pnl)}`, h.pnl < 0);
    });
    const oldOpen = new Set(a.orders.map((o) => o.id));
    b.order_history.filter((o) => oldOpen.has(o.id) && o.status === 'filled').forEach((o) => toast('Order filled', `${o.symbol} ${o.kind} ${o.side} at ${px(BY[o.symbol] || S.m, o.fill_price)}`));
    if (a.account.status !== b.account.status && b.account.status === 'failed') toast('Account closed', 'An account rule was breached. Check your dashboard for details.', true);
  }
  setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 15000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });

  // live P&L po stronie przeglądarki (midy Hyperliquid, jak na serwerze)
  function livePos() {
    if (!S.st) return [];
    return S.st.positions.map((p) => {
      const m = BY[p.symbol];
      const closed = S.st && S.weekend && m.cls !== 'crypto';
      const mk = closed ? p.mark : (S.mids[m.hl] != null ? S.mids[m.hl] : p.mark);
      const pnl = mk != null ? pnlOf(m, p.side, p.qty, p.entry, mk) : p.pnl;
      return { ...p, mark: mk, pnl, roe: p.margin ? (pnl / p.margin) * 100 : null, value: mk ? notional(m, p.qty, mk) : p.value };
    });
  }
  let lastLive = 0;
  function renderLivePnl() {
    const t = performance.now();
    if (t - lastLive < 400) return;
    lastLive = t;
    renderAccount();
    if (S.ptab === 'positions') renderTables();
    checkTriggers();
  }
  // Cena przecięła TP/SL/limit — od razu prosimy serwer o przegląd (on rozstrzyga).
  let trigT = 0;
  function checkTriggers() {
    if (!S.st || Date.now() - trigT < 4000) return;
    const hit = livePos().some((p) => p.mark && ((p.side === 'buy' && ((p.tp && p.mark >= p.tp) || (p.sl && p.mark <= p.sl) || (p.liq && p.mark <= p.liq)))
      || (p.side === 'sell' && ((p.tp && p.mark <= p.tp) || (p.sl && p.mark >= p.sl) || (p.liq && p.mark >= p.liq)))))
      || S.st.orders.some((o) => { const m = BY[o.symbol], p = S.mids[m.hl]; if (!p) return false; return o.kind === 'limit' ? (o.side === 'buy' ? p <= o.price : p >= o.price) : (o.side === 'buy' ? p >= o.price : p <= o.price); });
    if (hit) { trigT = Date.now(); refresh(); }
  }

  function renderAccount() {
    if (!S.st) {
      ['tMUsed', 'tMBal', 'tMAvail', 'tDayLeft', 'tDdLeft'].forEach((id) => ($(id).textContent = '—'));
      $('tMBar').style.width = '0';
      updateTicket();
      return;
    }
    const a = S.st.account, pos = livePos();
    const floating = pos.reduce((s, p) => s + (p.pnl || 0), 0);
    const eq = a.balance + floating + (a.other_floating || 0);
    const used = a.margin_used;
    $('tMUsed').textContent = usd(used);
    $('tMBar').style.width = Math.min(100, eq > 0 ? (used / eq) * 100 : 0) + '%';
    $('tMBal').textContent = usd(eq);
    $('tMAvail').textContent = usd(Math.max(0, eq - used));
    $('tDayLeft').textContent = usd(eq - a.metrics.daily_floor);
    $('tDdLeft').textContent = usd(eq - a.metrics.overall_floor);
    $('tDayLeft').className = 'num ' + (eq - a.metrics.daily_floor < a.size * 0.01 ? 'dn' : '');
    S.st.account.available = Math.max(0, eq - used);
  }

  /* ---------- linie na wykresie ---------- */
  function drawLines() {
    if (!chart) return;
    chart.removeOverlay({ groupId: 'ptf' });
    if (!S.st) return;
    const ts = lastBar ? lastBar.timestamp : Date.now();
    const up = css('--up'), dn = css('--dn'), acc = css('--acc'), warn = css('--warn');
    const line = (v, text, color) => v && chart.createOverlay({ name: 'ptfLine', groupId: 'ptf', lock: true, points: [{ timestamp: ts, value: v }], extendData: { text, color } });
    S.st.positions.filter((p) => p.symbol === S.sym).forEach((p) => {
      line(p.entry, `${p.side === 'buy' ? 'Long' : 'Short'} ${lotsOf(S.m, p.qty)} @ ${px(S.m, p.entry)}`, p.side === 'buy' ? up : dn);
      line(p.tp, 'TP ' + px(S.m, p.tp), acc);
      line(p.sl, 'SL ' + px(S.m, p.sl), warn);
      if (p.liq && p.liq > 0 && isFinite(p.liq)) line(p.liq, 'Liq. ' + px(S.m, p.liq), dn);
    });
    S.st.orders.filter((o) => o.symbol === S.sym).forEach((o) => line(o.price, `${o.kind === 'limit' ? 'Limit' : 'Stop'} ${o.side} ${lotsOf(S.m, o.qty)}`, o.side === 'buy' ? up : dn));
  }

  /* =====================================================================
     TABELE NA DOLE
     ===================================================================== */
  $('tColsMenu').addEventListener('click', (e) => e.stopPropagation());
  dropdown($('tColsBtn'), $('tColsMenu'), (mn) => {
    const L = { value: 'Value', slippage: 'Slippage', lev: 'Leverage', margin: 'Margin', tpsl: 'TP / SL' };
    mn.innerHTML = '<h6>Positions columns</h6>' + Object.entries(L).map(([k, l]) => `<label class="ck"><input type="checkbox" data-col="${k}"${S.cols[k] ? ' checked' : ''}><span>${l}</span></label>`).join('');
  });
  $('tColsMenu').addEventListener('change', (e) => {
    const c = e.target.dataset.col;
    if (!c) return;
    S.cols[c] = e.target.checked;
    ls.set('pf_term_cols2', JSON.stringify(S.cols));
    renderTables();
  });
  $$('.t-ptabs > [data-pt]').forEach((b) => b.addEventListener('click', () => setPTab(b.dataset.pt)));
  dropdown($('tMoreBtn'), $('tMoreMenu'));
  $('tMoreMenu').addEventListener('click', (e) => { const b = e.target.closest('[data-pt]'); if (b) { $('tMoreMenu').hidden = true; setPTab(b.dataset.pt); } });
  function setPTab(t) {
    S.ptab = t;
    $$('.t-ptabs [data-pt]').forEach((b) => b.classList.toggle('on', b.dataset.pt === t || (b.id === 'tMoreBtn' && ['rules', 'fills'].includes(t))));
    renderTables();
  }
  function renderTables() {
    const el = $('tTable');
    const st = S.st;
    $('tPosN').textContent = st && st.positions.length ? ` (${st.positions.length})` : '';
    $('tOrdN').textContent = st && st.orders.length ? ` (${st.orders.length})` : '';
    $('tCloseAll').disabled = !(st && st.positions.length);
    if (!S.token) {
      el.innerHTML = `<div class="t-empty"><div>Sign in to see your trading activity<br><button class="t-btn" type="button" data-signin>Sign In</button></div></div>`;
      return;
    }
    if (S.ptab === 'accounts') {
      el.innerHTML = S.accounts.length ? `<div class="t-accs">${S.accounts.map((a) => `<button type="button" class="t-accc${a.id === S.accId ? ' on' : ''}" data-acc="${a.id}"><small>#${esc(a.login)} · ${esc(a.server || '')}</small><b>${usd(a.balance)}</b><small>${big(a.size)} challenge · ${phaseLbl(a)} · <span class="t-pill ${a.tradable ? 'ok' : ''}">${a.tradable ? 'Active' : 'Not tradable'}</span></small></button>`).join('')}</div>`
        : `<div class="t-empty"><div>No active challenge account yet.<br><a class="t-btn" href="/#pricing">Choose a Challenge</a></div></div>`;
      return;
    }
    if (!st) { el.innerHTML = `<div class="t-empty">${S.accounts.length ? 'Loading…' : 'No active challenge account yet.'}</div>`; return; }
    const sym = (s) => { const m = BY[s]; return m ? `<span class="sym">${mIcon(m, true)}${s}</span>` : esc(s); };
    const side = (s) => `<span class="side ${s === 'buy' ? 'up' : 'dn'}">${s === 'buy' ? 'Long' : 'Short'}</span>`;
    const table = (head, rows, empty) => (rows.length ? `<table><thead><tr>${head.map(([h, n]) => `<th class="${n ? 'num' : ''}">${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>` : `<div class="t-empty">${empty}</div>`);
    if (S.ptab === 'positions') {
      const c = S.cols;
      const head = [['Market'], ['Side'], ['Size', 1]].concat(c.value ? [['Value (USD)', 1]] : [], [['Entry Price', 1]], c.slippage ? [['Slippage', 1]] : [], [['Mark Price', 1]], c.liq ? [['Liq. Price', 1]] : [], [['PnL (ROE)', 1]], c.lev ? [['Leverage', 1]] : [], c.margin ? [['Margin', 1]] : [], c.tpsl ? [['TP / SL', 1]] : [], [['', 1]]);
      const rows = livePos().map((p) => {
        const m = BY[p.symbol];
        return `<tr><td>${sym(p.symbol)}</td><td>${side(p.side)}</td><td class="num">${sizeCell(m, p.qty)}</td>${c.value ? `<td class="num">${usd(p.value)}</td>` : ''}<td class="num">${px(m, p.entry)}</td>${c.slippage ? `<td class="num">${ok(p.slippage) ? p.slippage.toFixed(3) + '%' : '—'}</td>` : ''}<td class="num">${px(m, p.mark)}</td>${c.liq ? `<td class="num">${p.liq > 0 && isFinite(p.liq) ? px(m, p.liq) : '—'}</td>` : ''}<td class="num ${cls(p.pnl)}">${sUsd(p.pnl)} (${pct(p.roe)})</td>${c.lev ? `<td class="num">1:${p.leverage}</td>` : ''}${c.margin ? `<td class="num">${usd(p.margin)}</td>` : ''}${c.tpsl ? `<td class="num"><button type="button" class="tpsl" data-tpsl="${p.id}">${p.tp ? px(m, p.tp) : '—'} / ${p.sl ? px(m, p.sl) : '—'}</button></td>` : ''}<td><div class="act"><button type="button" data-half="${p.id}">Close 50%</button><button type="button" class="dn" data-close="${p.id}">Close</button></div></td></tr>`;
      });
      el.innerHTML = table(head, rows, 'No open positions');
    } else if (S.ptab === 'orders') {
      el.innerHTML = table([['Time'], ['Market'], ['Type'], ['Side'], ['Size', 1], ['Price', 1], ['Reduce Only'], ['TP / SL', 1], ['', 1]], st.orders.map((o) => {
        const m = BY[o.symbol];
        return `<tr><td>${tm(o.created_at)}</td><td>${sym(o.symbol)}</td><td>${o.kind === 'limit' ? 'Limit' : 'Stop Market'}</td><td>${side(o.side)}</td><td class="num">${sizeCell(m, o.qty)}</td><td class="num">${px(m, o.price)}</td><td>${o.reduce_only ? 'Yes' : 'No'}</td><td class="num">${o.tp ? px(m, o.tp) : '—'} / ${o.sl ? px(m, o.sl) : '—'}</td><td><div class="act"><button type="button" class="dn" data-cancel="${o.id}">Cancel</button></div></td></tr>`;
      }), 'No open orders');
      if (st.orders.length) el.insertAdjacentHTML('afterbegin', '<div style="padding:8px 12px;text-align:right"><button class="t-danger" type="button" data-cancelall>Cancel all orders</button></div>');
    } else if (S.ptab === 'phist') {
      const R = { close: 'Closed', reduce: 'Reduced', tp: 'Take profit', sl: 'Stop loss', liquidation: 'Liquidation', stop_out: 'Stop-out', breach: 'Rule breach' };
      el.innerHTML = table([['Closed'], ['Market'], ['Side'], ['Size', 1], ['Entry', 1], ['Exit', 1], ['PnL', 1], ['Fees', 1], ['Reason']], st.history.map((h) => {
        const m = BY[h.symbol] || S.m;
        return `<tr><td>${tm(h.closed_at)}</td><td>${sym(h.symbol)}</td><td>${side(h.side)}</td><td class="num">${sizeCell(m, h.qty)}</td><td class="num">${px(m, h.entry)}</td><td class="num">${px(m, h.close)}</td><td class="num ${cls(h.pnl)}">${sUsd(h.pnl)}</td><td class="num">${usd(h.fee)}</td><td><span class="t-pill ${h.reason === 'tp' ? 'ok' : ['sl', 'liquidation', 'stop_out', 'breach'].includes(h.reason) ? 'bad' : ''}">${R[h.reason] || esc(h.reason || '')}</span></td></tr>`;
      }), 'No closed positions yet');
    } else if (S.ptab === 'ohist' || S.ptab === 'fills') {
      const list = S.ptab === 'fills' ? st.order_history.filter((o) => o.status === 'filled') : st.order_history;
      const K = { market: 'Market', limit: 'Limit', stop: 'Stop Market' };
      el.innerHTML = table([['Time'], ['Market'], ['Type'], ['Side'], ['Size', 1], ['Price', 1], ['Filled at', 1], ['Status'], ['Note']], list.map((o) => {
        const m = BY[o.symbol] || S.m;
        return `<tr><td>${tm(o.done_at || o.created_at)}</td><td>${sym(o.symbol)}</td><td>${K[o.kind] || o.kind}${o.reduce_only ? ' · RO' : ''}</td><td>${side(o.side)}</td><td class="num">${sizeCell(m, o.qty)}</td><td class="num">${o.price ? px(m, o.price) : 'Market'}</td><td class="num">${o.fill_price ? px(m, o.fill_price) : '—'}</td><td><span class="t-pill ${o.status === 'filled' ? 'ok' : o.status === 'rejected' ? 'bad' : ''}">${o.status}</span></td><td style="color:var(--muted);white-space:normal;min-width:180px">${esc(o.note || '')}</td></tr>`;
      }), S.ptab === 'fills' ? 'No trades yet' : 'No orders yet');
    } else if (S.ptab === 'rules') {
      const a = st.account, mt = a.metrics;
      const eq = a.balance + livePos().reduce((s, p) => s + (p.pnl || 0), 0);
      const meter = (used, max) => { const f = max > 0 ? Math.max(0, Math.min(100, (used / max) * 100)) : 0; return `<div class="t-meter"><i class="${f > 80 ? 'bad' : f > 50 ? 'warn' : ''}" style="width:${f}%"></i></div>`; };
      const dayLim = (mt.max_daily_loss_pct / 100) * a.size, ddLim = (mt.max_overall_loss_pct / 100) * a.size;
      el.innerHTML = `<div class="t-rules">
        <div class="t-kv"><small>Daily loss left</small><b class="num">${usd(eq - mt.daily_floor)}</b>${meter(dayLim - (eq - mt.daily_floor), dayLim)}<p>Limit ${mt.max_daily_loss_pct}% · floor ${usd(mt.daily_floor)}</p></div>
        <div class="t-kv"><small>Max drawdown left</small><b class="num">${usd(eq - mt.overall_floor)}</b>${meter(ddLim - (eq - mt.overall_floor), ddLim)}<p>Limit ${mt.max_overall_loss_pct}% · floor ${usd(mt.overall_floor)}</p></div>
        <div class="t-kv"><small>Profit target</small><b class="num">${mt.target_equity ? usd(mt.target_equity) : 'None'}</b>${mt.target_equity ? meter(a.balance - a.size, mt.target_equity - a.size) : ''}<p>Profit so far ${pct(mt.profit_pct)}</p></div>
        <div class="t-kv"><small>Trading days</small><b class="num">${mt.trading_days} / ${mt.min_trading_days}</b><p>A day counts when you hold a position.</p></div>
        <div class="t-kv"><small>Exposure cap</small><b class="num">${fx(a.open_lots, 2)} / ${fx(a.max_lots, 2)} lots</b>${meter(a.open_lots, a.max_lots)}<p>All open positions together.</p></div>
      </div>`;
    }
  }
  $('tTable').addEventListener('click', async (e) => {
    const t = e.target;
    if (t.closest('[data-signin]')) return openLogin();
    const acc = t.closest('[data-acc]');
    if (acc) return selectAccount(+acc.dataset.acc);
    const cl = t.closest('[data-close]'), hf = t.closest('[data-half]');
    if (cl || hf) {
      const id = +(cl || hf).dataset.close || +(hf && hf.dataset.half);
      const p = S.st.positions.find((x) => x.id === id);
      if (!p) return;
      try {
        const body = { account_id: S.accId, max_slippage_pct: Math.max(S.slip, 2) };
        if (hf) body.qty = +(p.qty / 2).toFixed(8);
        setState(await api(`/api/terminal/positions/${id}/close`, { method: 'POST', body: JSON.stringify(body) }));
        const h = S.st.history[0];
        toast(hf ? 'Closed 50%' : 'Position closed', h ? `${h.symbol} ${sUsd(h.pnl)}` : '');
      } catch (er) { toast('Close failed', er.message, true); }
      return;
    }
    const cn = t.closest('[data-cancel]');
    if (cn) { try { setState(await api(`/api/terminal/orders/${cn.dataset.cancel}?account_id=${S.accId}`, { method: 'DELETE' })); toast('Order cancelled'); } catch (er) { toast('Cancel failed', er.message, true); } return; }
    if (t.closest('[data-cancelall]')) { try { setState(await api('/api/terminal/cancel-all', { method: 'POST', body: JSON.stringify({ account_id: S.accId }) })); toast('All orders cancelled'); } catch (er) { toast('Cancel failed', er.message, true); } return; }
    const tps = t.closest('[data-tpsl]');
    if (tps) editTpsl(+tps.dataset.tpsl);
  });
  async function editTpsl(id) {
    const p = S.st.positions.find((x) => x.id === id);
    if (!p) return;
    const m = BY[p.symbol];
    const okk = await confirmBox(`TP / SL · ${p.symbol} ${p.side === 'buy' ? 'Long' : 'Short'}`, `<p>Entry ${px(m, p.entry)} · mark ${px(m, S.mids[m.hl] || p.mark)}</p>
      <div class="t-tpsl" style="margin-top:14px"><label class="t-fld"><span>Take profit</span><div class="t-in"><input id="eTp" inputmode="decimal" value="${p.tp || ''}" placeholder="—"></div></label>
      <label class="t-fld"><span>Stop loss</span><div class="t-in"><input id="eSl" inputmode="decimal" value="${p.sl || ''}" placeholder="—"></div></label></div>
      <p class="t-hint" style="margin-top:10px">Leave a field empty to remove it.</p>`, 'Save');
    if (!okk) return;
    try {
      setState(await api(`/api/terminal/positions/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: S.accId, tp: +$('eTp').value || null, sl: +$('eSl').value || null }) }));
      toast('TP / SL updated');
    } catch (er) { toast('Could not update', er.message, true); }
  }
  $('tCloseAll').onclick = async () => {
    if (!S.st || !S.st.positions.length) return;
    const okk = await confirmBox('Close all positions', `<p>Close <b>${S.st.positions.length}</b> open position(s) at market?</p>`, 'Close All', 'sell');
    if (!okk) return;
    try { setState(await api('/api/terminal/close-all', { method: 'POST', body: JSON.stringify({ account_id: S.accId }) })); toast('All positions closed'); } catch (er) { toast('Close failed', er.message, true); }
  };

  /* ---------- logowanie ---------- */
  function openLogin() { openModal('tLogin'); }
  $('tSignIn').onclick = openLogin;
  $('tReadySignIn').onclick = openLogin;
  ls.del('pf_term_token');   // sprzątanie po wycofanym logowaniu danymi MT5
  async function doLogin(form, path, body, store) {
    const err = form.querySelector('.t-err'), btn = form.querySelector('button[type=submit]');
    err.hidden = true; btn.disabled = true;
    try {
      const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Sign in failed');
      store(d);
      closeModal();
      toast('Signed in', 'Your accounts are loaded.');
      await loadAccounts();
    } catch (e) { err.textContent = e.message; err.hidden = false; } finally { btn.disabled = false; }
  }
  $('tLoginPtf').onsubmit = (e) => {
    e.preventDefault();
    const f = e.target;
    doLogin(f, '/api/auth/login', { email: f.email.value.trim(), password: f.password.value }, (d) => {
      S.token = d.token; ls.set('pf_token', d.token);
    });
  };

  /* ---------- telefon: zakładki paneli ---------- */
  function setMPane(p) {
    root.dataset.mpane = p;
    $$('#tMTabs [data-pane]').forEach((b) => b.classList.toggle('on', b.dataset.pane === p));
    $$('.t-grid > [data-pane]').forEach((s) => s.classList.toggle('on', s.dataset.pane.split(' ').includes(p)));
    if (p === 'book' || p === 'trades') $$('[data-btab]').find((b) => b.dataset.btab === (p === 'book' ? 'book' : 'trades')).click();
    if (p === 'chart') setTimeout(() => chart && chart.resize(), 30);
    renderBook();
  }
  $('tMTabs').addEventListener('click', (e) => { const b = e.target.closest('[data-pane]'); if (b) setMPane(b.dataset.pane); });
  $('tMBarBtns').addEventListener('click', (e) => {
    const b = e.target.closest('[data-quick]');
    if (!b) return;
    setSide(b.dataset.quick);
    setMPane('ticket');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  /* ---------- motyw, udostępnianie ---------- */
  $('tTheme').onclick = () => {
    const light = document.documentElement.dataset.theme !== 'light';
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    ls.set('pf_term_theme', light ? 'light' : 'dark');
    if (chart) { chart.setStyles(chartStyles()); drawLines(); }
  };
  $('tShare').onclick = () => {
    const u = location.origin + '/trade/' + S.sym;
    try { navigator.clipboard.writeText(u); toast('Link copied', u); } catch (e) { toast('Copy this link', u); }
  };

  /* ---------- monit „Free Trade Case" dla niezalogowanych ---------- */
  function casePrompt() {
    if (S.token || ls.get('pf_case_prompt', '0') === '1') return;
    const need = 10 + Math.floor(Math.random() * 30);   // 10–40 s patrzenia na stronę
    let seen = 0;
    const t = setInterval(() => {
      if (document.visibilityState !== 'visible' || modalOpen || S.token) return;
      if (++seen < need) return;
      clearInterval(t);
      ls.set('pf_case_prompt', '1');
      openModal('tCase');
    }, 1000);
  }

  /* ---------- start ---------- */
  function boot() {
    renderMarketHead();
    setSide('buy');
    renderQuick();
    $$('[data-i]', $('tInts')).forEach((b) => b.classList.toggle('on', b.dataset.i === S.interval));
    buildDrawRail();
    initChart();
    restream();
    renderStrip();
    setPTab('positions');
    setMPane('chart');
    loadAccounts();
    fetch('/api/terminal/markets').then((r) => r.json()).then((d) => { S.weekend = !!d.weekend; S.fee = d.fee_pct; updateTicket(); renderInfo(); }).catch(() => {});
    if (root.dataset.missing) toast(`${root.dataset.missing} trades in MT5`, 'The web terminal has crypto, metals, energy, indices and EUR/GBP/JPY for now.');
    casePrompt();
  }
  if (window.klinecharts) boot(); else window.addEventListener('load', boot);
})();
