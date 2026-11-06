/* PTF terminal (Trade tab): KLineChart chart, book, trades, order ticket and
   position tables. Market data from terminal-feed.js (straight from exchanges),
   account and orders from /api/terminal/*. Sign-in uses the MT5 credentials of each account. */
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

  /* ---------- browser storage (may be blocked) ---------- */
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
    json(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } },
  };

  /* ---------- formatting ---------- */
  const NF = {};
  const nf = (d) => NF[d] || (NF[d] = new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const ok = (v) => v != null && isFinite(v);
  const fx = (v, d) => (ok(v) ? nf(d).format(v) : '—');
  const usd = (v, d = 2) => (ok(v) ? (v < 0 ? '-$' : '$') + nf(d).format(Math.abs(v)) : '—');
  const sUsd = (v) => (ok(v) ? (v > 0 ? '+' : v < 0 ? '-' : '') + '$' + nf(2).format(Math.abs(v)) : '—');
  const big = (v) => (!ok(v) ? '—' : v >= 1e12 ? '$' + (v / 1e12).toFixed(2) + 'T' : v >= 1e9 ? '$' + (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? '$' + (v / 1e3).toFixed(1) + 'K' : '$' + v.toFixed(0));
  const pct = (v) => (ok(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—');
  const px = (m, v) => fx(v, m.px_dec);
  const qtyF = (m, v) => fx(v, Math.min(Math.max(m.qty_dec, 0), 6));
  // Lots as in MT5 (1 lot = m.contract units); fractions below 0.01 lot with 4 decimals.
  const lotsF = (v) => fx(v, v > 0 && v < 0.01 ? 4 : 2);
  const lotsOf = (m, q) => lotsF(q / m.contract) + ' lots';
  const sizeCell = (m, q) => `${lotsOf(m, q)} <span class="sub">${qtyF(m, q)} ${esc(m.base)}</span>`;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'dn' : '');
  // Number from a field: thousands commas and spaces don't break it ("65,000" → 65000, not NaN).
  const numOf = (v) => { const n = +String(v == null ? '' : v).replace(/[,\s]/g, ''); return isFinite(n) && n > 0 ? n : 0; };
  // Tabs: .on class and aria-selected together (screen readers know which is selected).
  const tabOn = (list, pred) => list.forEach((b) => {
    const on = !!pred(b);
    b.classList.toggle('on', on);
    if (b.getAttribute('role') === 'tab') b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const tm = (iso) => { if (!iso) return '—'; const d = new Date(iso); return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) + ' ' + d.toLocaleTimeString('en-GB', { hour12: false }); };
  const notional = (m, q, p) => (m.quote_inverse ? q : q * p);
  const qtyFromUsd = (m, u, p) => (m.quote_inverse ? u : p ? u / p : 0);
  const pnlOf = (m, side, q, entry, p) => { const r = side === 'buy' ? p - entry : entry - p; return m.quote_inverse ? (p ? (q * r) / p : 0) : q * r; };

  /* ---------- market icons ---------- */
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

  /* ---------- state ---------- */
  const startSym = BY[root.dataset.symbol] ? root.dataset.symbol : 'BTCUSD';
  const S = {
    sym: startSym, m: BY[startSym], venue: 'hyperliquid',
    interval: ls.get('pf_term_int', '15m'), ctype: ls.get('pf_term_ctype', 'candle_solid'),
    book: { bids: [], asks: [] }, trades: [], tk: {}, mids: {}, ctx: {},
    // Size field unit: lots (default, as in MT5), USD or the base unit (BTC, oz…).
    side: 'buy', kind: 'market', unit: ['lots', 'usd', 'base'].includes(ls.get('pf_term_unit2', 'lots')) ? ls.get('pf_term_unit2', 'lots') : 'lots', slip: +ls.get('pf_term_slip', '1'),
    watch: ls.json('pf_term_watch', ['BTCUSD', 'ETHUSD', 'XAUUSD']),
    token: null,    // session of the MT5 account in view; every saved account has its own
    logins: [],     // [{ token, id, login }], saved in this browser
    closing: new Set(),      // positions leaving the screen while the server confirms
    justClosed: new Set(),   // trades whose chart marker draws itself in
    ordDrag: null,           // pending order being dragged on the chart: { id, v }
    alerts: [],
    imp: false,     // admin view opened from the panel: read-only, never saved
    accounts: [], accId: +ls.get('pf_term_acc', '0') || null, st: null, ptab: 'positions',
    // Slippage hidden by default (it's in Columns): with it the table didn't fit under the chart even at 1440 px.
    cols: ls.json('pf_term_cols3', { value: true, slippage: false, lev: true, margin: true, tpsl: true }),
    showTrades: ls.get('pf_term_trades', '1') === '1',     // trade history on the chart ("Trades" button)
    weekend: false, fee: 0.009, group: null, top: [0, 0], bookUnit: 'base', tickMode: ls.get('pf_term_tick', 'trending'),
  };
  if (!F.venuesFor(S.m).includes(S.venue)) S.venue = 'hyperliquid';
  // Leverage isn't selectable: fixed per market (the server calculates with it anyway).
  const lev = () => S.m.max_lev;

  /* ---------- toasts and dialogs ---------- */
  function toast(title, msg, bad) {
    const el = document.createElement('div');
    el.className = 't-toast' + (bad ? ' bad' : '');
    el.innerHTML = `<b>${esc(title)}</b>${msg ? `<span>${esc(msg)}</span>` : ''}`;
    $('tToasts').appendChild(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, bad ? 6500 : 4200);
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

  /* ---------- dropdown menu ---------- */
  /* The menu is `fixed` to the screen: sideways-scrolling bars (chart tools,
     market bar, table tabs) would otherwise clip it or become scrollable
     themselves. Scrolling the page closes the menu. */
  function placeMenu(btn, menu) {
    const r = btn.getBoundingClientRect();
    const up = !!menu.closest('.t-dd.up');
    Object.assign(menu.style, { position: 'fixed', right: 'auto', left: '0px', top: up ? 'auto' : r.bottom + 4 + 'px', bottom: up ? innerHeight - r.top + 6 + 'px' : 'auto' });
    const w = menu.offsetWidth;
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    if (up) return;
    // Too little room below the button (e.g. table tabs at the bottom edge) — menu opens up.
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
    const tok = S.token;
    if (tok) h.Authorization = 'Bearer ' + tok;
    let r;
    try { r = await fetch(path, { ...(opts || {}), headers: h }); } catch (e) { throw new Error('Network error. Check your connection.'); }
    let d = null;
    try { d = await r.json(); } catch (e) {}
    if (r.status === 401 && tok) { dropLogin(tok); throw new Error((d && d.detail) || 'Session expired. Connect again.'); }
    if (!r.ok) {
      const det = d && d.detail;
      throw new Error(Array.isArray(det) ? det.map((x) => x.msg).join('; ') : det || 'Error ' + r.status);
    }
    return d;
  }

  /* =====================================================================
     CHART
     ===================================================================== */
  const INT_LBL = { '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '4h': '240', '1d': '1D', '1w': '1W' };
  const PERIOD = { '1m': ['minute', 1], '3m': ['minute', 3], '5m': ['minute', 5], '15m': ['minute', 15], '30m': ['minute', 30], '1h': ['hour', 1], '4h': ['hour', 4], '1d': ['day', 1], '1w': ['week', 1] };
  let chart = null, barCb = null, histLimit = 500, lastBar = null;
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  // Narrow screen: OHLC and indicator legend only under the crosshair (chart touch) —
  // a fixed one hid a quarter of the candles and the high label.
  const narrow = matchMedia('(max-width:980px)');
  function chartStyles() {
    const up = css('--up'), dn = css('--dn'), line = css('--line'), muted = css('--muted'), txt = css('--txt'), panel = css('--panel3'), acc = css('--acc');
    const rule = narrow.matches ? 'follow_cross' : 'always';
    return {
      grid: { horizontal: { color: line }, vertical: { color: line } },
      candle: {
        type: S.ctype,
        bar: { upColor: up, downColor: dn, noChangeColor: muted, upBorderColor: up, downBorderColor: dn, noChangeBorderColor: muted, upWickColor: up, downWickColor: dn, noChangeWickColor: muted },
        area: { lineColor: acc, backgroundColor: [{ offset: 0, color: acc + '33' }, { offset: 1, color: acc + '00' }] },
        priceMark: {
          high: { color: muted }, low: { color: muted },
          last: { upColor: up, downColor: dn, noChangeColor: muted, line: { style: 'dashed', dashedValue: [1, 3], size: 1 }, text: { color: '#fff' } },
        },
        tooltip: {
          showRule: rule,
          title: { show: true, color: txt, size: 14, template: `${S.sym} · ${INT_LBL[S.interval]} · ${F.VENUES[S.venue].label}` },
          // Time is on the axis under the crosshair — without it the legend fits one line.
          legend: { color: muted, size: 12, template: [{ title: 'O ', value: '{open}' }, { title: 'H ', value: '{high}' }, { title: 'L ', value: '{low}' }, { title: 'C ', value: '{close}' }, { title: 'Chg ', value: '{change}' }, { title: 'Vol ', value: '{volume}' }] },
        },
      },
      indicator: { tooltip: { showRule: rule, title: { color: muted }, legend: { color: muted } }, bars: [{ upColor: up + '80', downColor: dn + '80', noChangeColor: muted }] },
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

  // KLineChart draws line labels in this font; we measure width the same way so the
  // P&L box lands right after the entry label.
  const LINE_FONT = 'Helvetica Neue';
  const MIARA = document.createElement('canvas').getContext('2d');
  const szerTekstu = (t) => { MIARA.font = `600 11px ${LINE_FONT}`; return MIARA.measureText(t || '').width; };
  // KLineChart places an overlay timestamp on the nearest candle centre. On H1 a
  // fill at 10:37 is closer to 11:00 than to 10:00, so a 28-minute trade is drawn
  // across two hourly candles (the same shift shows up on M15). A candle's slot is
  // one bar wide and centred on its index; the open sits on the left edge. Time
  // maps linearly across that slot, so the marker stays on the candle that contains it.
  function barIndexAt(bars, ts) {
    const n = bars && bars.length;
    if (!n || !Number.isFinite(ts)) return null;
    const t0 = bars[0].timestamp, tN = bars[n - 1].timestamp;
    if (ts >= tN) {
      const step = n >= 2 ? tN - bars[n - 2].timestamp : 0;
      return step > 0 ? (n - 1) + (ts - tN) / step - 0.5 : n - 1.5;
    }
    if (ts <= t0) {
      const step = n >= 2 ? bars[1].timestamp - t0 : 0;
      return step > 0 ? (ts - t0) / step - 0.5 : -0.5;
    }
    let lo = 0, hi = n - 1, s = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (bars[mid].timestamp <= ts) { s = mid; lo = mid + 1; } else hi = mid - 1;
    }
    const step = bars[s + 1].timestamp - bars[s].timestamp;
    return step > 0 ? s + (ts - bars[s].timestamp) / step - 0.5 : s;
  }
  function registerOverlays() {
    // Closed trade on the chart as in MT5 / cTrader: entry (▲ long, ▼ short) at the
    // open price and time, exit (circle) at the close price and time, a dashed line
    // between them in the result colour. Hovering thickens it all.
    klinecharts.registerOverlay({
      name: 'ptfTrade', totalStep: 3, lock: true, needDefaultPointFigure: false, needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
      createPointFigures: ({ coordinates, overlay, chart, xAxis }) => {
        if (coordinates.length < 2) return [];
        const bars = chart.getDataList();
        const place = (i) => {
          const c = coordinates[i];
          const ts = overlay.points[i] && overlay.points[i].timestamp;
          const idx = barIndexAt(bars, ts);
          if (idx == null) return c;
          const x = xAxis.convertToPixel(idx);
          return Number.isFinite(x) ? { x, y: c.y } : c;
        };
        const a = place(0), b0 = place(1), e = overlay.extendData || {}, hov = !!e.hover, s = hov ? 6.5 : 5;
        const t = e.t == null ? 1 : e.t, b = { x: a.x + (b0.x - a.x) * t, y: a.y + (b0.y - a.y) * t };
        const wynik = e.pnl >= 0 ? e.up : e.dn, strona = e.side === 'buy' ? e.up : e.dn;
        const tri = e.side === 'buy'
          ? [{ x: a.x, y: a.y - s }, { x: a.x - s, y: a.y + s * 0.85 }, { x: a.x + s, y: a.y + s * 0.85 }]
          : [{ x: a.x, y: a.y + s }, { x: a.x - s, y: a.y - s * 0.85 }, { x: a.x + s, y: a.y - s * 0.85 }];
        return [
          { key: 'line', type: 'line', attrs: { coordinates: [a, b] }, styles: { style: 'dashed', dashedValue: [4, 3], color: wynik, size: hov ? 2 : 1 } },
          { key: 'in', type: 'polygon', attrs: { coordinates: tri }, styles: { style: 'fill', color: strona } },
          { key: 'out', type: 'circle', attrs: { x: b.x, y: b.y, r: (hov ? 5.5 : 4) * t }, styles: { style: 'stroke_fill', color: e.bg, borderColor: wynik, borderSize: 2 } },
        ];
      },
    });
    // Shift + click measure (as in TradingView / cTrader / MT5): a box from the start point
    // to the cursor, arrows along both edges, and a label with the price move, %, pips,
    // $ per lot, bar count and time. The text is computed outside and passed in extendData.
    klinecharts.registerOverlay({
      name: 'ptfMeasure', totalStep: 3, lock: true, needDefaultPointFigure: false, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates, overlay }) => {
        if (coordinates.length < 2) return [];
        const [a, b] = coordinates, e = overlay.extendData || {};
        const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x), y1 = Math.min(a.y, b.y), y2 = Math.max(a.y, b.y);
        const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2, c = e.color, s = 5;
        const ln = (p, q) => ({ type: 'line', attrs: { coordinates: [p, q] }, styles: { color: c, size: 1 }, ignoreEvent: true });
        const vy = b.y < a.y ? 1 : -1, hx = b.x < a.x ? 1 : -1;
        const fig = [
          { type: 'rect', attrs: { x: x1, y: y1, width: x2 - x1, height: y2 - y1 }, styles: { style: 'fill', color: e.fill }, ignoreEvent: true },
          ln({ x: cx, y: a.y }, { x: cx, y: b.y }),
          ln({ x: a.x, y: cy }, { x: b.x, y: cy }),
        ];
        if (y2 - y1 > 2 * s) fig.push(ln({ x: cx - s, y: b.y + vy * s }, { x: cx, y: b.y }), ln({ x: cx + s, y: b.y + vy * s }, { x: cx, y: b.y }));
        if (x2 - x1 > 2 * s) fig.push(ln({ x: b.x + hx * s, y: cy - s }, { x: b.x, y: cy }), ln({ x: b.x + hx * s, y: cy + s }, { x: b.x, y: cy }));
        const box = { color: '#fff', size: 11, weight: 600, family: LINE_FONT, paddingLeft: 8, paddingRight: 8, paddingTop: 4, paddingBottom: 4, borderRadius: 4, borderSize: 0, backgroundColor: c };
        const up = b.y <= a.y, ty = up ? y1 - 8 : y2 + 8;
        (e.lines || []).forEach((t, i) => fig.push({
          type: 'text', ignoreEvent: true, styles: box,
          attrs: { x: cx, y: up ? ty - (e.lines.length - 1 - i) * 21 : ty + i * 21, text: t, align: 'center', baseline: up ? 'bottom' : 'top' },
        }));
        return fig;
      },
    });
    // Magnet crosshair: dashed horizontal line at a candle's O/H/L/C, exact price on the axis.
    klinecharts.registerOverlay({
      name: 'ptfSnap', totalStep: 2, lock: true, needDefaultPointFigure: false, needDefaultXAxisFigure: false, needDefaultYAxisFigure: false,
      createPointFigures: ({ coordinates, bounding }) => [{
        type: 'line', ignoreEvent: true, attrs: { coordinates: [{ x: 0, y: coordinates[0].y }, { x: bounding.width, y: coordinates[0].y }] },
        styles: { style: 'dashed', dashedValue: [4, 3], color: css('--muted'), size: 1 },
      }],
      // Price box on the axis, like the crosshair's own label (always shown, not only on hover).
      createYAxisFigures: ({ coordinates, overlay, bounding }) => [{
        type: 'text', ignoreEvent: true,
        attrs: { x: 0, y: coordinates[0].y, text: px(S.m, overlay.points[0].value), align: 'left', baseline: 'middle' },
        styles: { color: '#fff', size: 12, family: LINE_FONT, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3,
          borderRadius: 2, borderSize: 0, backgroundColor: css('--panel3') || '#3b4354' },
      }],
    });
    // Price line with a label: entry, TP, SL, liquidation, pending orders.
    klinecharts.registerOverlay({
      name: 'ptfLine', totalStep: 2, lock: true, needDefaultPointFigure: false, needDefaultXAxisFigure: false, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates, bounding, overlay }) => {
        const y = coordinates[0].y, e = overlay.extendData || {};
        const etykieta = { color: '#fff', size: 11, weight: 600, family: LINE_FONT, paddingLeft: 6, paddingRight: 6, paddingTop: 3, paddingBottom: 3, borderRadius: 4, borderSize: 0 };
        // Position (e.pos): dashed line with an outlined label; TP/SL and orders dashed with a
        // filled label; the last-price line is dotted (chartStyles), so they never look alike.
        const fig = [
          { type: 'line', attrs: { coordinates: [{ x: 0, y }, { x: bounding.width, y }] }, styles: e.pos ? { style: 'dashed', dashedValue: [7, 4], color: e.color, size: 1 } : { style: 'dashed', dashedValue: [5, 4], color: e.color, size: 1 }, ignoreEvent: true },
          { type: 'text', attrs: { x: 10, y, text: e.text, baseline: 'middle' }, styles: e.pos ? { ...etykieta, color: e.color, backgroundColor: e.bg, borderColor: e.color, borderSize: 1 } : { ...etykieta, backgroundColor: e.color }, ignoreEvent: true },
        ];
        if (e.noLabel) fig.pop();          // TP/SL being edited: the label is the draggable HTML handle
        // Position: live P&L by the entry label, in result colour (as in MT5 / cTrader).
        if (e.pnlText) fig.push({ type: 'text', attrs: { x: 10 + szerTekstu(e.text) + 12 + 4, y, text: e.pnlText, baseline: 'middle' }, styles: { ...etykieta, backgroundColor: e.pnlColor }, ignoreEvent: true });
        return fig;
      },
    });
  }

  function initChart() {
    if (!window.klinecharts) return;
    registerOverlays();
    chart = klinecharts.init('tChart', { styles: chartStyles(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, locale: 'en-US' });
    chart.setDataLoader({
      getBars: async ({ type, timestamp, callback }) => {
        // Each chart reset starts its own load; a response that arrives
        // after the next reset (other market/interval) is discarded.
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
    narrow.addEventListener('change', () => chart && chart.setStyles(chartStyles()));
    // KLineChart gives the canvas tabindex=1 (jumps ahead of the page nav) — use normal order.
    const cv = $('tChart').querySelector('[tabindex="1"]');
    if (cv) { cv.tabIndex = 0; cv.setAttribute('role', 'region'); cv.setAttribute('aria-label', 'Price chart'); }
  }
  let loadSeq = 0;
  /* Reload the chart with one call: setSymbol/setPeriod reset on their own. */
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
    loadDrawn();
    drawLines();          // new market's position lines and history markers at once, not after the next refresh
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

  // Drawing: undo/redo queue
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
  const DI = Object.fromEntries(DRAW.map((d) => [d[0], d]));
  /* Toolbar in groups (as in TradingView): 18 buttons didn't fit the chart height
     on any laptop and squeezed to ~20 px. A group shows the last used tool;
     the arrow (or a second tap) opens the rest. */
  const GROUPS = [
    ['cross'],
    ['segment', 'rayLine', 'horizontalStraightLine', 'horizontalRayLine', 'verticalStraightLine'],
    ['parallelStraightLine', 'priceChannelLine', 'fibonacciLine'],
    ['rect', 'circle', 'polygon', 'brush'],
    ['simpleAnnotation', 'priceLine'],
  ];
  const GNAME = ['', 'Lines', 'Channels and Fibonacci', 'Shapes', 'Notes'];
  const gSel = ls.json('pf_term_dgrp', {});
  /* Drawings belong to a market: a BTC line must not hang on ETH. We keep them
     per symbol in the browser, so they come back after a page reload. */
  let drawn = [], redo = [], drawnSym = null, selDraw = null;
  let magnet = ls.get('pf_term_magnet', '0') === '1';
  /* Magnet as in TradingView: points snap to the candle's open/high/low/close. Holding
     ⌘ (macOS) or Ctrl (Windows/Linux) flips it while held. The mode is applied to every
     drawing at once, including the one being drawn — set only at creation, a toggle
     mid-drawing and moving saved drawings ignored it. */
  const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const MAG_KEY = IS_MAC ? 'Meta' : 'Control';
  let magnetHeld = false;
  const magMode = () => (magnet !== magnetHeld ? 'strong_magnet' : 'normal');
  function applyMagnet() {
    if (chart) chart.overrideOverlay({ groupId: 'draw', mode: magMode() });
    const b = $('tMagnet');
    if (b) b.classList.toggle('on', magnet !== magnetHeld);
  }
  document.addEventListener('keydown', (e) => { if (e.key === MAG_KEY && !magnetHeld) { magnetHeld = true; applyMagnet(); } });
  document.addEventListener('keyup', (e) => { if (e.key === MAG_KEY && magnetHeld) { magnetHeld = false; applyMagnet(); } });
  window.addEventListener('blur', () => { if (magnetHeld) { magnetHeld = false; applyMagnet(); } });
  const drawKey = (s) => 'pf_term_draw_' + s;
  const pts = (o) => o.points.map((p) => ({ timestamp: p.timestamp, value: p.value }));
  function saveDrawn() {
    if (drawnSym) ls.set(drawKey(drawnSym), JSON.stringify(drawn.slice(-200).map(({ name, points }) => ({ name, points }))));
  }
  // Events of each drawing: right-click removes, moving saves, selecting (Delete).
  const drawEvts = {
    onRightClick: ({ overlay }) => { prawyNaRysunku = Date.now(); removeDrawn(overlay.id); return true; },
    onPressedMoveEnd: ({ overlay }) => { const o = drawn.find((x) => x.id === overlay.id); if (o) { o.points = pts(overlay); saveDrawn(); } },
    onSelected: ({ overlay }) => { selDraw = overlay.id; },
    onDeselected: () => { selDraw = null; },
  };
  function mkOverlay(name, points) {
    return chart.createOverlay({ name, groupId: 'draw', points, mode: magMode(), visible: !$('tHideDraw').classList.contains('on'), ...drawEvts });
  }
  function loadDrawn() {
    if (!chart || drawnSym === S.sym) return;
    chart.removeOverlay({ groupId: 'draw' });
    drawnSym = S.sym; redo = []; selDraw = null;
    drawn = ls.json(drawKey(S.sym), []).map((o) => ({ ...o, id: mkOverlay(o.name, o.points) })).filter((o) => o.id);
  }
  function groupBtn(gi) {
    const g = GROUPS[gi];
    const [n, t, p] = DI[g.includes(gSel[gi]) ? gSel[gi] : g[0]];
    const more = g.length > 1 ? ` aria-haspopup="menu" title="${t} (click again for more)"` : ` title="${t}"`;
    return `<button type="button" data-d="${n}" data-g="${gi}"${more} aria-label="${t}"${n === 'cross' ? ' class="on"' : ''}><svg viewBox="0 0 24 24">${p}</svg></button>`;
  }
  const drawFly = document.createElement('div');
  drawFly.className = 't-menu t-dfly';
  drawFly.hidden = true;
  drawFly.setAttribute('role', 'menu');
  document.body.appendChild(drawFly);
  function openFly(gi, anchor) {
    const was = !drawFly.hidden && drawFly.dataset.g === String(gi);
    closeMenus();
    if (was) return;
    drawFly.dataset.g = gi;
    drawFly.innerHTML = `<h6>${GNAME[gi]}</h6>` + GROUPS[gi].map((n) => `<button type="button" role="menuitem" data-pick="${n}"><svg viewBox="0 0 24 24">${DI[n][2]}</svg>${DI[n][1]}</button>`).join('');
    drawFly.hidden = false;
    const r = anchor.closest('.t-dgrp').getBoundingClientRect();
    Object.assign(drawFly.style, { position: 'fixed', left: r.right + 6 + 'px', top: '0px', right: 'auto', bottom: 'auto' });
    drawFly.style.top = Math.max(8, Math.min(r.top, innerHeight - drawFly.offsetHeight - 8)) + 'px';
  }
  /* Esc (or picking another tool) drops a drawing that isn't finished yet — e.g. a trend
     line with only its first point placed — and goes back to the cursor. */
  let armedId = null;
  function cancelDrawing(toCursor) {
    if (!armedId) return false;
    if (chart) chart.removeOverlay({ id: armedId });
    armedId = null;
    if (toCursor) $$('[data-d]', $('tDraw')).forEach((x) => x.classList.toggle('on', x.dataset.d === 'cross'));
    return true;
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !e.target.closest('input,textarea')) cancelDrawing(true); });

  /* With the magnet on, the crosshair snaps too (as in TradingView). The chart's own
     horizontal crosshair is hidden and a line of ours sits at the nearest open/high/low/close
     of the candle under the cursor, with that exact price on the axis — the chart rounds its
     crosshair to whole pixels, so moving it there would show a price a dollar or two off. */
  let snapId = null, snapOn = false;
  function snapMode(on) {
    if (on === snapOn || !chart) return;
    snapOn = on;
    chart.setStyles({ crosshair: { horizontal: { show: !on } } });
    if (!on && snapId) { chart.removeOverlay({ id: snapId }); snapId = null; }
  }
  $('tChart').addEventListener('mousemove', (ev) => {
    if (!chart) return;
    const on = magMode() !== 'normal';
    snapMode(on);
    if (!on || ev.buttons || ev.target.tagName !== 'CANVAS') return;
    const r = $('tChart').getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    try {
      const pane = chart.getSize('candle_pane');
      if (pane && (y < pane.top || y > pane.top + pane.height)) { if (snapId) { chart.removeOverlay({ id: snapId }); snapId = null; } return; }
      const pt = chart.convertFromPixel({ x, y }, { paneId: 'candle_pane', absolute: true });
      const k = pt && chart.getDataList()[pt.dataIndex];
      if (!k || !ok(pt.value)) return;
      const v = [k.open, k.high, k.low, k.close].reduce((a, c) => (Math.abs(c - pt.value) < Math.abs(a - pt.value) ? c : a));
      const points = [{ timestamp: k.timestamp, value: v }];
      if (snapId) chart.overrideOverlay({ id: snapId, points });
      else snapId = chart.createOverlay({ name: 'ptfSnap', groupId: 'snap', lock: true, points });
    } catch (e) {}
  });
  $('tChart').addEventListener('mouseleave', () => { if (snapId && chart) { chart.removeOverlay({ id: snapId }); snapId = null; } });

  function armTool(b) {
    const el = $('tDraw');
    $$('[data-d]', el).forEach((x) => x.classList.toggle('on', x === b));
    const n = b.dataset.d;
    cancelDrawing(false);
    if (n === 'cross' || !chart) return;
    loadDrawn();
    armedId = chart.createOverlay({
      name: n, groupId: 'draw', mode: magMode(), ...drawEvts,
      onDrawEnd: ({ overlay }) => {
        armedId = null;
        drawn.push({ id: overlay.id, name: overlay.name, points: pts(overlay) });
        redo = [];
        saveDrawn();
        $$('[data-d]', el).forEach((x) => x.classList.toggle('on', x.dataset.d === 'cross'));
        return false;
      },
    });
  }
  function buildDrawRail() {
    const el = $('tDraw');
    el.innerHTML = GROUPS.map((g, gi) => (gi === 1 ? '<hr>' : '') + (g.length > 1 ? `<div class="t-dgrp">${groupBtn(gi)}</div>` : groupBtn(gi))).join('')
      + '<hr>'
      + `<button type="button" id="tMagnet" title="Magnet mode (hold ${IS_MAC ? '⌘' : 'Ctrl'} to toggle)" aria-label="Magnet mode" class="${magnet ? 'on' : ''}"><svg viewBox="0 0 24 24"><path d="M6 4v8a6 6 0 0 0 12 0V4h-4v8a2 2 0 0 1-4 0V4z"/><path d="M6 8h4M14 8h4"/></svg></button>`
      + '<button type="button" id="tHideDraw" title="Hide drawings" aria-label="Hide drawings"><svg viewBox="0 0 24 24"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg></button>'
      + '<button type="button" id="tClearDraw" title="Remove all drawings" aria-label="Remove all drawings"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>';
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || !chart) return;
      e.stopPropagation();
      // The corner triangle (12 px) opens the group's list — no separate, too-small button.
      const grp = b.dataset.g && GROUPS[+b.dataset.g].length > 1;
      const r = b.getBoundingClientRect();
      if (grp && e.detail && e.clientX > r.right - 12 && e.clientY > r.bottom - 12) return openFly(+b.dataset.g, b);
      closeMenus();
      if (b.id === 'tMagnet') { magnet = !magnet; ls.set('pf_term_magnet', magnet ? '1' : '0'); applyMagnet(); return; }
      if (b.id === 'tHideDraw') {
        const hide = !b.classList.contains('on');
        b.classList.toggle('on', hide);
        drawn.forEach((o) => chart.overrideOverlay({ id: o.id, visible: !hide }));
        return;
      }
      if (b.id === 'tClearDraw') { chart.removeOverlay({ groupId: 'draw' }); drawn = []; redo = []; saveDrawn(); return; }
      // A second click (tap, Enter) on the group's active tool opens the list.
      if (grp && b.classList.contains('on')) return openFly(+b.dataset.g, b);
      armTool(b);
    });
    drawFly.addEventListener('click', (e) => {
      const b = e.target.closest('[data-pick]');
      if (!b) return;
      e.stopPropagation();
      const gi = +drawFly.dataset.g;
      gSel[gi] = b.dataset.pick;
      ls.set('pf_term_dgrp', JSON.stringify(gSel));
      drawFly.hidden = true;
      const old = $('tDraw').querySelector(`[data-g="${gi}"]`);
      old.outerHTML = groupBtn(gi);
      armTool($('tDraw').querySelector(`[data-g="${gi}"]`));
    });
  }
  function removeDrawn(id) {
    const i = drawn.findIndex((o) => o.id === id);
    if (i >= 0) redo.push(drawn.splice(i, 1)[0]);
    chart.removeOverlay({ id });
    if (selDraw === id) selDraw = null;
    saveDrawn();
  }
  $('tUndo').onclick = () => { const o = drawn[drawn.length - 1]; if (o) removeDrawn(o.id); };
  $('tRedo').onclick = () => {
    const o = redo.pop();
    if (!o || !chart) return;
    drawn.push({ ...o, id: mkOverlay(o.name, o.points) });
    saveDrawn();
  };
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input,textarea,select')) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); (e.shiftKey ? $('tRedo') : $('tUndo')).click(); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && selDraw) { e.preventDefault(); removeDrawn(selDraw); }
  });

  // Indicators
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
    if (!strm || !strm.interval || !chart) return restream();
    // Interval only: swap the candles; book and trades keep flowing without reconnecting.
    strm.interval(i);
    lastBar = null; barNext = null;
    chart.setStyles({ candle: { tooltip: { title: { template: `${S.sym} · ${INT_LBL[S.interval]} · ${F.VENUES[S.venue].label}` } } } });
    reloadChart();
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

  // clock
  function clock() {
    const d = new Date(), off = -d.getTimezoneOffset() / 60;
    $('tClock').textContent = d.toLocaleTimeString('en-GB', { hour12: false }) + ' UTC' + (off >= 0 ? '+' : '') + off;
  }
  setInterval(() => { if (!document.hidden) clock(); }, 1000); clock();

  // chart tabs
  let cTab = 'chart';
  $$('[data-ctab]').forEach((b) => b.addEventListener('click', () => {
    tabOn($$('[data-ctab]'), (x) => x === b);
    $$('[data-cpane]').forEach((p) => p.classList.toggle('on', p.dataset.cpane === b.dataset.ctab));
    cTab = b.dataset.ctab;
    if (cTab === 'chart') setTimeout(() => chart && chart.resize(), 20);
    else renderInfo(true);
  }));
  /* Trading Data and Info redraw only when they are open, and the slow parts (OI history,
     coin data) only when the market, venue or range changes — renderInfo is called on
     every market-head refresh. */
  function renderInfo(force) {
    if (cTab === 'data') drawData(force);
    else if (cTab === 'info') drawInfo(force);
  }
  const kv = (k, v, p) => `<div class="t-kv"><small>${k}</small><b class="num">${v}</b>${p ? `<p>${p}</p>` : ''}</div>`;
  const qtyBig = (v) => (!ok(v) ? '—' : v >= 1e9 ? (v / 1e9).toFixed(2) + 'B' : v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(2) + 'K' : nf(2).format(v));
  // "31 Oct '26 14:00" — fixed English, the same style as the axis (never the browser's language).
  const when = (ms) => { const d = new Date(ms); return `${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })} '${String(d.getFullYear()).slice(2)} ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false })}`; };

  /* ---------- Trading Data: open interest ---------- */
  const OI_RANGE = { '24h': ['15m', 96, '24 Hours'], '7d': ['1h', 168, '7 Days'], '30d': ['4h', 180, '30 Days'] };
  let oiRange = '24h', dataKey = '', dataSeq = 0;
  function drawData(force) {
    const m = S.m, src = F.OI_SRC(m, S.venue), key = [m.sym, src, oiRange, S.venue].join('|');
    snapshotKv();
    if (!force && key === dataKey) return;
    dataKey = key;
    const nr = ++dataSeq, [per, n, lbl] = OI_RANGE[oiRange];
    const name = m.cls === 'crypto' ? m.base : m.sym;
    $('tData').innerHTML = `<div class="t-oi">
      <div class="t-oi-h"><div><h3>${esc(name)} Futures OI</h3><p>${src ? `Open interest at historical mark prices · ${F.VENUES[src].label}` : 'Current open interest on Hyperliquid. This market publishes no open interest history.'}</p></div><small id="tOiAt"></small></div>
      <div class="t-oi-cards" id="tOiCards">${['Positions', ...(src ? ['OI Change (1h)', 'OI Change (4h)', 'OI Change (12h)', 'OI Change (24h)'] : [])].map((k) => `<div class="t-kv"><small>${k}</small><b class="num">—</b></div>`).join('')}</div>
      <div class="t-oi-ch-h"><h4>Historical ${esc(name)} ${src ? 'Futures OI' : 'Price'}</h4>
        <span class="t-lg"><i style="background:var(--brand)"></i>${esc(name)} Price</span>${src ? '<span class="t-lg"><i style="background:var(--up)"></i>Open Interest</span>' : ''}
        <div class="t-rng" role="tablist">${Object.entries(OI_RANGE).map(([k, v]) => `<button type="button" data-oir="${k}" class="${k === oiRange ? 'on' : ''}">${v[2]}</button>`).join('')}</div></div>
      <div class="t-oi-chart" id="tOiChart"><div class="t-oi-msg">Loading…</div></div>
      <h4 class="t-oi-sub">Market snapshot</h4><div class="t-oi-snap" id="tOiSnap"></div></div>`;
    snapshotKv();
    const venuePx = src || 'hyperliquid';
    Promise.all([
      src ? F.oiHistory(m, S.venue, per, n + 1) : Promise.resolve(null),
      src ? F.oiHistory(m, S.venue, '15m', 97) : Promise.resolve(null),
      F.priceHistory(venuePx, m, per, n + 1),
    ]).then(([oi, oi24, pxs]) => {
      if (nr !== dataSeq) return;
      const c = S.ctx[m.hl] || {}, last = oi && oi.rows[oi.rows.length - 1];
      const now = last || (c.oi ? { usd: c.oi * (c.mark || 0), qty: c.oi } : null);
      const ch = (k) => { const r = oi24 && oi24.rows; if (!r || r.length <= k) return null; const a = r[r.length - 1 - k].usd, b = r[r.length - 1].usd; return a ? (b / a - 1) * 100 : null; };
      $('tOiCards').innerHTML = `<div class="t-kv"><small>Positions</small><b class="num">${now ? big(now.usd) : '—'}</b><p class="num">${now ? '≈ ' + qtyBig(now.qty) + ' ' + esc(m.base) : ''}</p></div>`
        + (src ? [[4, '1h'], [16, '4h'], [48, '12h'], [96, '24h']].map(([k, l]) => { const v = ch(k); return `<div class="t-kv"><small>OI Change (${l})</small><b class="num ${cls(v)}">${pct(v)}</b></div>`; }).join('') : '');
      $('tOiAt').textContent = 'Last updated: ' + when(last ? last.t : Date.now());
      oiChart($('tOiChart'), m, pxs || [], oi ? oi.rows : null, per);
    }).catch((e) => {
      if (nr !== dataSeq) return;
      dataKey = '';
      $('tOiChart').innerHTML = `<div class="t-oi-msg">Data unavailable right now (${esc(e.message)}).</div>`;
    });
  }
  function snapshotKv() {
    const el = $('tOiSnap');
    if (!el) return;
    const m = S.m, t = S.tk, c = S.ctx[m.hl] || {};
    el.innerHTML = [
      kv('Mark price', px(m, t.mark || c.mark)), kv('24h change', `<span class="${cls(t.chg)}">${pct(t.chg)}</span>`),
      kv('24h volume', big(t.vol || c.vol)),
      kv('Funding (1h)', ok(t.funding) ? (t.funding * 100).toFixed(4) + '%' : '—', 'Exchange funding rate, for reference. PTF positions do not pay funding.'),
      kv('Spread', $('tSpread').textContent), kv('Bid / ask depth', `${$('tBidD').textContent} / ${$('tAskD').textContent}`),
    ].join('');
  }
  $('tData').addEventListener('click', (e) => {
    const b = e.target.closest('[data-oir]');
    if (b && b.dataset.oir !== oiRange) { oiRange = b.dataset.oir; drawData(true); }
  });
  /* Two-axis chart: open interest (area, left axis) and price (line, right axis). */
  function oiChart(el, m, pxs, oi, per) {
    const W = Math.max(320, el.clientWidth), H = 300, L = 62, R = 64, T = 12, B = 28;
    const t0 = Math.min(...(oi && oi.length ? [oi[0].t] : []), pxs.length ? pxs[0].timestamp : Infinity);
    const t1 = Math.max(...(oi && oi.length ? [oi[oi.length - 1].t] : []), pxs.length ? pxs[pxs.length - 1].timestamp : 0);
    if (!(t1 > t0)) { el.innerHTML = '<div class="t-oi-msg">No data for this range.</div>'; return; }
    const X = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const rng = (vs) => { const lo = Math.min(...vs), hi = Math.max(...vs), p = (hi - lo) * 0.08 || hi * 0.01 || 1; return [lo - p, hi + p]; };
    const pr = rng(pxs.map((k) => k.close)), Yp = (v) => T + (1 - (v - pr[0]) / (pr[1] - pr[0])) * (H - T - B);
    const or = oi && oi.length ? rng(oi.map((r) => r.usd)) : null, Yo = (v) => T + (1 - (v - or[0]) / (or[1] - or[0])) * (H - T - B);
    const grid = [], ticks = 6;
    for (let i = 0; i <= ticks; i++) {
      const y = T + (i / ticks) * (H - T - B);
      grid.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" class="g"/>`);
      if (or) grid.push(`<text x="${L - 8}" y="${y + 4}" text-anchor="end">${big(or[1] - (i / ticks) * (or[1] - or[0]))}</text>`);
      grid.push(`<text x="${W - R + 8}" y="${y + 4}">${px(m, pr[1] - (i / ticks) * (pr[1] - pr[0]))}</text>`);
    }
    const day = per !== '15m';
    for (let i = 0; i <= 7; i++) {
      const t = t0 + (i / 7) * (t1 - t0), d = new Date(t);
      const s = day ? d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
      grid.push(`<text x="${X(t)}" y="${H - 8}" text-anchor="middle">${s}</text>`);
    }
    let oiSvg = '';
    if (or) {
      const line = oi.map((r, i) => `${i ? 'L' : 'M'}${X(r.t).toFixed(1)},${Yo(r.usd).toFixed(1)}`).join('');
      oiSvg = `<path d="${line}L${X(oi[oi.length - 1].t).toFixed(1)},${H - B}L${X(oi[0].t).toFixed(1)},${H - B}Z" class="oa"/><path d="${line}" class="ol"/>`;
    }
    const pLine = pxs.map((k, i) => `${i ? 'L' : 'M'}${X(k.timestamp).toFixed(1)},${Yp(k.close).toFixed(1)}`).join('');
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Open interest and price history">
      <defs><linearGradient id="tOiGrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--up)" stop-opacity=".28"/><stop offset="1" stop-color="var(--up)" stop-opacity="0"/></linearGradient></defs>
      ${grid.join('')}${oiSvg}<path d="${pLine}" class="pl"/><line class="xh" y1="${T}" y2="${H - B}" x1="-10" x2="-10"/></svg><div class="t-oi-tip" hidden></div>`;
    const svg = el.querySelector('svg'), xh = svg.querySelector('.xh'), tip = el.querySelector('.t-oi-tip');
    const near = (arr, t, k) => { let best = null, bd = Infinity; for (const a of arr) { const d = Math.abs(k(a) - t); if (d < bd) { bd = d; best = a; } } return best; };
    svg.addEventListener('mousemove', (e) => {
      const r = svg.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * W;
      if (x < L || x > W - R) { tip.hidden = true; xh.setAttribute('x1', -10); xh.setAttribute('x2', -10); return; }
      const t = t0 + ((x - L) / (W - L - R)) * (t1 - t0), k = near(pxs, t, (a) => a.timestamp), o = oi && near(oi, t, (a) => a.t);
      const tx = X(k ? k.timestamp : t);
      xh.setAttribute('x1', tx); xh.setAttribute('x2', tx);
      tip.innerHTML = `<b>${when(k ? k.timestamp : t)}</b>${o ? `<span><i style="background:var(--up)"></i>Open interest <b class="num">${big(o.usd)}</b></span>` : ''}<span><i style="background:var(--brand)"></i>Price <b class="num">${k ? px(m, k.close) : '—'}</b></span>`;
      tip.hidden = false;
      const left = (tx / W) * r.width;
      tip.style.left = Math.min(r.width - tip.offsetWidth - 4, Math.max(4, left + 12)) + 'px';
    });
    svg.addEventListener('mouseleave', () => { tip.hidden = true; xh.setAttribute('x1', -10); xh.setAttribute('x2', -10); });
  }
  new ResizeObserver(() => { if (cTab === 'data' && $('tOiChart')) { dataKey = ''; drawData(true); } }).observe($('tData'));

  /* ---------- Info ---------- */
  // CoinGecko ids for crypto markets; other markets get a short overview of our own.
  const CG = { BTC: 'bitcoin', ETH: 'ethereum', SOL: 'solana', XRP: 'ripple', LTC: 'litecoin', DOGE: 'dogecoin', BNB: 'binancecoin', ADA: 'cardano', AVAX: 'avalanche-2', LINK: 'chainlink', SUI: 'sui', HYPE: 'hyperliquid' };
  const ABOUT = {
    XAUUSD: 'Gold priced in US dollars per troy ounce. Central banks hold it as a reserve asset and traders use it as a hedge against inflation, currency weakness and market stress. It tends to react to real interest rates, the US dollar and geopolitical risk.',
    XAGUSD: 'Silver priced in US dollars per troy ounce. It trades partly as a precious metal alongside gold and partly as an industrial metal used in electronics and solar panels, so it usually moves more sharply than gold.',
    XPTUSD: 'Platinum priced in US dollars per troy ounce. Most demand comes from catalytic converters, industry and jewellery, and supply is concentrated in South Africa and Russia, which makes the price sensitive to mining and car-industry news.',
    USOIL: 'West Texas Intermediate, the US benchmark crude oil, priced in US dollars per barrel. It reacts to OPEC+ decisions, US inventory data, refinery demand and the global growth outlook.',
    UKOIL: 'Brent crude, the international oil benchmark from the North Sea, priced in US dollars per barrel. Around two thirds of the world’s physically traded crude is priced against it.',
    NATGAS: 'US natural gas (Henry Hub) priced in US dollars per million British thermal units. Weather, storage reports and LNG exports drive it, and it is one of the most volatile commodity markets.',
    SPX500: 'The S&P 500 index of 500 large US companies, the most followed measure of the US stock market. Technology, healthcare and financials carry the largest weights.',
    NAS100: 'The Nasdaq 100 index of the 100 largest non-financial companies listed on the Nasdaq. It is heavily weighted towards technology and tends to move more than the broader market.',
    JPN225: 'The Nikkei 225 index of leading companies listed in Tokyo, priced in yen points. It is sensitive to the yen exchange rate, exports and Bank of Japan policy.',
    EURUSD: 'The euro against the US dollar, the most traded currency pair in the world. It moves with the interest-rate gap between the European Central Bank and the Federal Reserve and with economic data from both regions.',
    GBPUSD: 'The British pound against the US dollar, often called “Cable”. Bank of England decisions, UK inflation and US data drive it.',
    USDJPY: 'The US dollar against the Japanese yen. The gap between US and Japanese interest rates drives it, and the yen often strengthens in risk-off moves.',
  };
  let infoKey = '', infoSeq = 0;
  function conditionsKv(m) {
    return [
      kv('Leverage', '1:' + m.max_lev, 'Set for each market, the same on every account. Margin is shared across the account.'),
      kv('Contract size', `1 lot = ${fx(m.contract, 0)} ${m.base}`, 'Lots count toward the exposure cap of your account.'),
      kv('Trading fee', S.fee.toFixed(3) + '% per side', 'Charged on the order value when a position opens and when it closes.'),
      kv('Trading hours', m.cls === 'crypto' ? '24/7' : 'Mon–Fri', m.cls === 'crypto' ? 'On weekends with the Weekend Trading add-on.' : 'Closed Saturday and Sunday (server time). Open positions are valued at the Friday close.'),
      kv('Prices', 'Hyperliquid', 'Orders, TP/SL and stop-outs settle at Hyperliquid prices.'),
    ].join('');
  }
  const stat = (k, v) => `<div class="t-st"><span>${k}</span><b class="num">${v}</b></div>`;
  const plain = (html) => (new DOMParser().parseFromString(html || '', 'text/html').body.textContent || '').trim();
  const LINK_ICO = {
    web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    paper: '<path d="M4 5h6a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4zM20 5h-6a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h6z"/>',
    code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
    chain: '<path d="M12 3 4 7.5v9L12 21l8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9"/>',
    x: '<path d="M4 4l16 16M20 4 4 20"/>',
  };
  function drawInfo(force) {
    const m = S.m, key = m.sym;
    if (!force && key === infoKey) { const c = $('tInfoCond'); if (c) c.innerHTML = conditionsKv(m); return; }
    infoKey = key;
    const nr = ++infoSeq, id = m.cls === 'crypto' && CG[m.base];
    const head = (rank, tags, links) => `<div class="t-in-h">${mIcon(m).replace('class="mi', 'class="mi lg')}<div><div class="t-in-name"><h3>${esc(m.name)}</h3><span>${esc(m.cls === 'crypto' ? m.base : m.sym)}</span>${rank ? `<em>Rank #${rank}</em>` : `<em>${CLS[m.cls]}</em>`}</div>
      ${tags.length ? `<div class="t-in-tags">${tags.map((t) => `<span>${esc(t)}</span>`).join('')}</div>` : ''}</div>
      ${links.length ? `<div class="t-in-links">${links.map(([k, u, t]) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" title="${t}" aria-label="${t}"><svg viewBox="0 0 24 24">${LINK_ICO[k]}</svg></a>`).join('')}</div>` : ''}</div>`;
    const body = (h, about, stats1, stats2, credit) => `<div class="t-inf">${h}<div class="t-in-grid">
      <section><h4>Overview</h4><div class="t-in-about">${about}</div></section>
      <section>${stats1 ? `<h4>Market Statistics</h4>${stats1}` : ''}<h4>Price Statistics</h4><div id="tInfoPx">${stats2 || '<p class="t-oi-msg">Loading…</p>'}</div></section></div>
      <h4>Trading at PTF</h4><div class="t-in-cond" id="tInfoCond">${conditionsKv(m)}</div>${credit ? `<p class="t-in-credit">${credit}</p>` : ''}</div>`;
    if (!id) {
      $('tInfo').innerHTML = body(head(null, [], []), `<p>${esc(ABOUT[m.sym] || m.name)}</p>`, '', '', '');
      F.priceHistory('hyperliquid', m, '1d', 366).then((d) => {
        if (nr !== infoSeq || !d.length) return;
        const last = d[d.length - 1].close, back = (n) => { const k = d[d.length - 1 - n]; return k ? (last / k.close - 1) * 100 : null; };
        const yr = d.slice(-365), hi = Math.max(...yr.map((k) => k.high)), lo = Math.min(...yr.map((k) => k.low));
        const t = d[d.length - 1];
        $('tInfoPx').innerHTML = [stat('Day High', px(m, t.high)), stat('Day Low', px(m, t.low)),
          stat('7d Change', `<span class="${cls(back(7))}">${pct(back(7))}</span>`), stat('30d Change', `<span class="${cls(back(30))}">${pct(back(30))}</span>`),
          stat('52-Week High', px(m, hi)), stat('52-Week Low', px(m, lo))].join('');
      }).catch(() => { if (nr === infoSeq) $('tInfoPx').innerHTML = '<p class="t-oi-msg">Price history unavailable right now.</p>'; });
      return;
    }
    $('tInfo').innerHTML = body(head(null, [], []), '<p class="t-oi-msg">Loading…</p>', '', '', '');
    F.coinInfo(id).then((c) => {
      if (nr !== infoSeq) return;
      const md = c.market_data || {}, u = (o) => o && o.usd, L = c.links || {};
      const links = [
        L.homepage && L.homepage[0] && ['web', L.homepage[0], 'Website'],
        L.whitepaper && ['paper', L.whitepaper, 'Whitepaper'],
        L.repos_url && L.repos_url.github && L.repos_url.github[0] && ['code', L.repos_url.github[0], 'Source code'],
        L.blockchain_site && L.blockchain_site.find(Boolean) && ['chain', L.blockchain_site.find(Boolean), 'Explorer'],
        L.twitter_screen_name && ['x', 'https://x.com/' + L.twitter_screen_name, 'X'],
      ].filter((x) => x && /^https:\/\//.test(x[1]));
      const paras = plain(c.description && c.description.en).split(/\r?\n\s*\r?\n/).filter(Boolean).slice(0, 3);
      const sup = (v) => (ok(v) && v ? qtyBig(v) : '—');
      const ath = u(md.ath), athD = md.ath_date && md.ath_date.usd;
      $('tInfo').innerHTML = body(
        head(c.market_cap_rank, (c.categories || []).filter(Boolean).slice(0, 6), links),
        paras.map((p) => `<p>${esc(p)}</p>`).join('') || `<p>${esc(m.name)}</p>`,
        [stat('Market Cap', big(u(md.market_cap))), stat('Fully Diluted Value', big(u(md.fully_diluted_valuation))),
          stat('Circulating Supply', sup(md.circulating_supply)), stat('Total Supply', sup(md.total_supply)),
          stat('Maximum Supply', sup(md.max_supply)), stat('Market Cap Rank', c.market_cap_rank ? '#' + c.market_cap_rank : '—'),
          stat('Launch Date', c.genesis_date ? new Date(c.genesis_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—')].join(''),
        [stat('24h High', px(m, u(md.high_24h))), stat('24h Low', px(m, u(md.low_24h))),
          stat('7d Change', `<span class="${cls(md.price_change_percentage_7d)}">${pct(md.price_change_percentage_7d)}</span>`),
          stat('30d Change', `<span class="${cls(md.price_change_percentage_30d)}">${pct(md.price_change_percentage_30d)}</span>`),
          stat('1y Change', `<span class="${cls(md.price_change_percentage_1y)}">${pct(md.price_change_percentage_1y)}</span>`),
          stat('All-Time High', `${px(m, ath)}${athD ? ` <small>${new Date(athD).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</small>` : ''}`),
          stat('All-Time Low', px(m, u(md.atl)))].join(''),
        'Coin data: CoinGecko');
    }).catch(() => {
      if (nr !== infoSeq) return;
      infoKey = '';
      $('tInfo').innerHTML = body(head(null, [], []), `<p>${esc(m.name)}</p>`, '', '<p class="t-oi-msg">Coin data unavailable right now.</p>', '');
    });
  }

  /* =====================================================================
     MARKET STREAM
     ===================================================================== */
  /* All socket data goes through requestAnimationFrame: at most one render
     per frame (busy markets send dozens of trades a second), and in a hidden tab the
     browser pauses rAF, so nothing paints then. */
  let strm = null, bookRaf = 0, tradesRaf = 0, tickRaf = 0, barRaf = 0, barNext = null;
  function pushBar(k) {
    // New candle in the same frame as an update to the last one: close the last one at once.
    if (barNext && barNext.timestamp !== k.timestamp && barCb) barCb(barNext);
    barNext = k;
    if (!barRaf) barRaf = requestAnimationFrame(() => { barRaf = 0; if (barCb && barNext) barCb(barNext); barNext = null; });
  }
  /* The live candle follows the mid quote, the way MT5 and cTrader draw from bid
     ticks. A Hyperliquid candle moves only when a trade prints, and on FX, metals
     and indices that is a few prints a minute: the last bar sat flat, then jumped.
     The mid between the best bid and ask moves with every requote and carries the
     full price precision (5 decimals on EUR/USD, where trades step by 0.0001).
     Hyperliquid's own candle still brings the volume and the traded range. */
  function tickBar(p) {
    if (!barCb || !lastBar || !(p > 0)) return;
    p = Math.round(p * 1e8) / 1e8;   // (bid + ask) / 2 leaves float dust such as 4048.8500000000004
    const step = F.MS[S.interval], now = Date.now();
    if (now >= lastBar.timestamp + step) {
      // Next bar only on the exact boundary: daily and weekly bars don't start
      // on epoch multiples, and after a hidden tab the exchange candle fills the gap.
      if (now >= lastBar.timestamp + 2 * step) return;
      lastBar = { timestamp: lastBar.timestamp + step, open: p, high: p, low: p, close: p, volume: 0 };
    } else if (now < lastBar.timestamp) return;
    else lastBar = { ...lastBar, high: Math.max(lastBar.high, p), low: Math.min(lastBar.low, p), close: p };
    pushBar(lastBar);
  }
  // Read-only hook for the UI checks in .tools: the last bar the chart was given.
  window.PTFTerm = { bar: () => lastBar };
  function mergeBar(k) {
    if (!barCb) return;
    if (lastBar && k.timestamp === lastBar.timestamp) lastBar = { ...lastBar, high: Math.max(lastBar.high, k.high), low: Math.min(lastBar.low, k.low), volume: k.volume };
    else if (!lastBar || k.timestamp > lastBar.timestamp) lastBar = k;
    else return;
    pushBar(lastBar);
  }
  function restream() {
    if (strm) strm.close();
    S.book = { bids: [], asks: [] }; S.top = [0, 0]; S.trades = []; S.tk = {};
    barNext = null;
    clearMeasure();
    stopTpsl();
    $('tSpread').textContent = '—'; $('tSpreadPct').textContent = '—'; sideSpread();
    renderBook(); renderTrades(); renderTicker();
    if (chart) {
      lastBar = null;
      chart.setStyles({ candle: { tooltip: { title: { template: `${S.sym} · ${INT_LBL[S.interval]} · ${F.VENUES[S.venue].label}` } } } });
      reloadChart();
    }
    strm = F.stream(S.venue, S.m, S.interval, {
      book: (b, a) => { S.book = { bids: b, asks: a }; drawBook(); },
      top: (b, a) => { S.top = [b, a]; drawBook(); tickBar((b + a) / 2); },
      trades: (list) => {
        S.trades = list.concat(S.trades).sort((x, y) => y.t - x.t).slice(0, 80);
        if (!tradesRaf) tradesRaf = requestAnimationFrame(() => { tradesRaf = 0; renderTrades(); });
      },
      candle: mergeBar,
      ticker: (t) => { S.tk = { ...S.tk, ...t }; if (!tickRaf) tickRaf = requestAnimationFrame(() => { tickRaf = 0; renderTicker(); }); },
    });
    strm.group(S.group);
  }
  const drawBook = () => { if (!bookRaf) bookRaf = requestAnimationFrame(() => { bookRaf = 0; renderBook(); sideSpread(); updateTicket(); }); };
  // Live spread between the Buy and Sell buttons (best ask − best bid); amber when unusually wide.
  function sideSpread() {
    const el = $('tSideSpr'), [b, a] = S.top;
    const v = a && b && a >= b ? a - b : null, t = v == null ? '—' : px(S.m, v);
    const w = v != null && v / ((a + b) / 2) > 0.0005;
    const n = el.querySelector('b');
    if (n.textContent !== t) n.textContent = t;
    el.classList.toggle('wide', w);
    el.title = v == null ? 'Live spread' : `Live spread: ${t} (${((v / ((a + b) / 2)) * 100).toFixed(4)}%) on ${F.VENUES[S.venue].label}`;
  }
  let hlState = 'connecting', latency = null;
  F.onHlState((s) => { hlState = s; renderConn(); });
  F.onLatency((l) => { latency = l; renderConn(); });
  function renderConn() {
    const st = hlState;
    const el = $('tConn');
    el.className = 't-conn ' + (st === 'open' ? 'ok' : st === 'closed' ? 'bad' : '');
    el.querySelector('b').textContent = st === 'open' ? 'Connected' : st === 'closed' ? 'Reconnecting' : 'Connecting';
    $('tLat').textContent = st === 'open' && latency != null ? latency + 'ms' : '';
  }

  /* ---------- market bar ---------- */
  function markPx() {
    const [b, a] = S.top;
    return S.tk.mark || (b && a ? (b + a) / 2 : S.mids[S.m.hl]) || null;
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
    $('tMaxLev').textContent = '1:' + m.max_lev;      // leverage written as across PTF (1:100), not "100×"
    const note = 'Prices, the order book and fills come from Hyperliquid.';
    $('tSrc').textContent = note;
    $('tFillNote').textContent = note + ` Leverage on ${m.sym} is 1:${m.max_lev}, the same on every account. Margin is shared across the account; if equity falls to half of the margin in use, open positions are closed (stop-out).`;
    $('tEstLev').textContent = '1:' + m.max_lev;
    $('tSizeUnitLbl').textContent = unitLbl();
    $('tGo').textContent = (S.side === 'buy' ? 'Buy / Long ' : 'Sell / Short ') + baseName(m);
    buildBookCtl();
    renderInfo();
  }
  const baseName = (m) => (m.cls === 'crypto' ? m.base : m.sym);

  /* ---------- market switch ---------- */
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
     BOOK AND TRADES
     ===================================================================== */
  function buildBookCtl() {
    const m = S.m;
    const t = Math.pow(10, -Math.min(m.px_dec, 6));
    groupOpts = [1, 10, 100, 1000].map((k) => +(t * k).toFixed(Math.max(0, m.px_dec)));
    groupFit = groupOpts.length;
    fillGroups();
    $('tUnit').innerHTML = `<option value="base"${S.bookUnit === 'base' ? ' selected' : ''}>${esc(m.base)}</option><option value="usd"${S.bookUnit === 'usd' ? ' selected' : ''}>USD</option>`;
    const u = S.bookUnit === 'usd' ? 'USD' : m.base;
    $('tBhSize').textContent = `Size (${u})`;
    $('tBhTot').textContent = `Total (${u})`;
    $('tThSize').textContent = `Size (${u})`;
  }
  let groupOpts = [], groupFit = 0;
  function fillGroups() {
    const opts = groupOpts.slice(0, groupFit);
    const g = opts.includes(S.group) ? S.group : (S.group && S.group > opts[opts.length - 1] ? opts[opts.length - 1] : opts[0]);
    $('tGroup').innerHTML = opts.map((x) => `<option value="${x}"${x === g ? ' selected' : ''}>${x}</option>`).join('');
    if (g !== S.group) { S.group = g; if (strm) strm.group(g); }
  }
  /* Binance and Bybit send the book at the finest tick only (up to 1000 levels), so a
     step the data can't fill with rows is hidden; Hyperliquid aggregates on its side. */
  function fitGroups() {
    if (!strm || strm.aggregates || !groupOpts.length) return;
    const { bids, asks } = S.book;
    if (bids.length < 2 || asks.length < 2) return;
    const span = Math.min(bids[0][0] - bids[bids.length - 1][0], asks[asks.length - 1][0] - asks[0][0]);
    const fit = Math.max(1, groupOpts.filter((g) => span / g >= Math.max(10, bookRows)).length);
    // Only shrinks (until the next market/venue switch), so a step doesn't blink in and out.
    if (fit < groupFit) { groupFit = fit; fillGroups(); }
  }
  $('tGroup').onchange = (e) => { S.group = +e.target.value; strm.group(S.group); renderBook(); };
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
  // Book row count from a ResizeObserver: reading clientHeight every frame after
  // DOM writes forced a page layout recalculation.
  let bookRows = 0;
  new ResizeObserver(() => { const h = $('tAsks').clientHeight; if (h) { bookRows = Math.max(5, Math.floor(h / 21)); renderBook(); } }).observe($('tAsks'));
  /* Book rows stay in the DOM; only numbers and bars change. Rebuilding innerHTML
     every frame swapped the row under the cursor and the highlight flickered. */
  const setTxt = (n, v) => { if (n.textContent !== v) n.textContent = v; };
  function fillBook(el, rows, m, sd, max) {
    while (el.children.length < rows.length) {
      const r = document.createElement('div');
      r.className = 't-row';
      r.innerHTML = '<i></i><span></span><span></span><span></span>';
      el.appendChild(r);
    }
    while (el.children.length > rows.length) el.lastElementChild.remove();
    rows.forEach(([p, q, t], i) => {
      const r = el.children[i], [bar, a, b, c] = r.children;
      r.dataset.px = p;
      const w = ((t / max) * 100).toFixed(1) + '%';
      if (bar.style.width !== w) bar.style.width = w;
      setTxt(a, px(m, p)); setTxt(b, fx(q, sd)); setTxt(c, fx(t, sd));
    });
  }
  function renderBook() {
    const m = S.m, el = $('tAsks');
    if (!S.tk.mark) renderTicker();
    if (!el.offsetParent) return;          // book hidden (phone, Trades tab) — it draws once shown
    fitGroups();
    const rows = bookRows || 11;
    const asks = grouped(S.book.asks, 'ask').slice(0, rows), bids = grouped(S.book.bids, 'bid').slice(0, rows);
    const val = (p, q) => (S.bookUnit === 'usd' ? notional(m, q, p) : q);
    const sd = S.bookUnit === 'usd' ? 0 : Math.min(m.qty_dec, 4);
    let ca = 0, cb = 0;
    const aRows = asks.map(([p, q]) => { ca += val(p, q); return [p, val(p, q), ca]; });
    const bRows = bids.map(([p, q]) => { cb += val(p, q); return [p, val(p, q), cb]; });
    const max = Math.max(ca, cb) || 1;
    fillBook(el, aRows.slice().reverse(), m, sd, max);
    fillBook($('tBids'), bRows, m, sd, max);
    const [b, a] = S.top;
    if (a && b) {
      $('tSpread').textContent = px(m, a - b);
      $('tSpreadPct').textContent = (((a - b) / ((a + b) / 2)) * 100).toFixed(5) + '%';
    }
    const sum = (ca + cb) || 1;
    $('tBidD').textContent = ((cb / sum) * 100).toFixed(1) + '%';
    $('tAskD').textContent = ((ca / sum) * 100).toFixed(1) + '%';
    $('tBidBar').style.flexGrow = cb / sum;
    $('tAskBar').style.flexGrow = ca / sum;
  }
  [$('tAsks'), $('tBids')].forEach((el) => el.addEventListener('click', (e) => {
    const r = e.target.closest('[data-px]');
    if (!r) return;
    if (S.kind === 'market') setKind('limit');
    $('tPx').value = px(S.m, +r.dataset.px).replace(/,/g, '');
    updateTicket();
  }));
  const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  function renderTrades() {
    const el = $('tTrades');
    if (!el.offsetParent && S.trades.length) return;    // hidden — it draws after the tab switches
    const m = S.m, sd = S.bookUnit === 'usd' ? 0 : Math.min(m.qty_dec, 4), list = S.trades.slice(0, 60);
    while (el.children.length < list.length) {
      const r = document.createElement('div');
      r.className = 't-row';
      r.innerHTML = '<span></span><span></span><span></span>';
      el.appendChild(r);
    }
    while (el.children.length > list.length) el.lastElementChild.remove();
    list.forEach((t, i) => {
      const [a, b, c] = el.children[i].children, k = t.side === 'buy' ? 'up' : 'dn';
      if (a.className !== k) a.className = k;
      setTxt(a, px(m, t.px));
      setTxt(b, fx(S.bookUnit === 'usd' ? notional(m, t.sz, t.px) : t.sz, sd));
      setTxt(c, TIME.format(t.t));
    });
  }
  $$('[data-btab]').forEach((b) => b.addEventListener('click', () => {
    tabOn($$('[data-btab]'), (x) => x === b);
    $$('[data-bpane]').forEach((p) => p.classList.toggle('on', p.dataset.bpane === b.dataset.btab));
    renderBook(); renderTrades();
  }));

  /* =====================================================================
     TICKER BAR, SEARCH, FOOTER
     ===================================================================== */
  const mid = (m) => { const v = S.mids[m.hl]; return v != null ? +v : (S.ctx[m.hl] || {}).mid || null; };
  const chg24 = (m) => { const c = S.ctx[m.hl] || {}, p = mid(m); return c.prev && p ? (p / c.prev - 1) * 100 : null; };
  // No context (not arrived yet) = "—", not "$0" — sorting treats it as zero.
  const vol24 = (m) => { const v = (S.ctx[m.hl] || {}).vol; return v == null ? null : v; };
  function sorted(mode) {
    let list = MKTS.slice();
    if (mode === 'trending') list.sort((a, b) => Math.abs(chg24(b) || 0) - Math.abs(chg24(a) || 0));
    else if (mode === 'volume') list.sort((a, b) => vol24(b) - vol24(a));
    else if (mode === 'watch') list = list.filter((m) => S.watch.includes(m.sym));
    return list;
  }
  const MODES = { trending: 'Trending', volume: 'Top Volume', watch: 'Watchlist', all: 'All markets' };
  /* Ticker bar: when the market order hasn't changed, swap only prices and
     changes in the existing buttons (no icon rebuild every 250 ms). */
  let stripT = 0, stripKey = '';
  function renderStrip() {
    if (stripT) return;
    stripT = setTimeout(() => {
      stripT = 0;
      if (document.hidden) return;          // returning to the tab redraws anyway
      const el = $('tTicker'), list = sorted(S.tickMode);
      const key = S.tickMode + '|' + S.sym + '|' + list.map((m) => m.sym).join(',');
      if (key === stripKey && el.children.length === list.length) {
        list.forEach((m, i) => {
          const [p, ch] = el.children[i].querySelectorAll('.num'), c = chg24(m);
          const pt = px(m, mid(m)), ct = pct(c);
          if (p.textContent !== pt) p.textContent = pt;
          if (ch.textContent !== ct) { ch.textContent = ct; ch.className = 'num ' + cls(c); }
        });
        return;
      }
      stripKey = key;
      $('tTickModeLbl').textContent = MODES[S.tickMode];
      el.innerHTML = list.map((m) => {
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
    // Search refreshes prices at most every half second (mids arrive twice per tick: main exchange and xyz).
    if (!$('tSearch').hidden && !searchT) searchT = setTimeout(() => { searchT = 0; if (!$('tSearch').hidden) searchPrices(); }, 500);
  });
  let searchT = 0;
  async function loadCtx() {
    try {
      S.ctx = await F.contexts();
      renderStrip(); renderTicker();
      if (!$('tSearch').hidden) searchPrices();     // list open on arrival (?list=all) — volumes at once
    } catch (e) {}
  }
  loadCtx();
  setInterval(() => { if (!document.hidden) loadCtx(); }, 30000);   // two big requests — not in a hidden tab

  // market search
  let sCat = 'all', sIdx = 0, sList = [];
  // Prices in the open list: only numbers in existing rows (a full rebuild every
  // half second flickered the highlight under the cursor and lost clicks).
  function searchPrices() {
    const rows = $('tSearchList').querySelectorAll('.t-sr[data-sym]');
    if (rows.length !== sList.length || [...rows].some((r, i) => r.dataset.sym !== sList[i].sym)) { renderSearch(); return; }
    rows.forEach((r, i) => {
      const m = sList[i], c = chg24(m), [p, ch, v] = r.querySelectorAll('.num'), ct = pct(c);
      setTxt(p, px(m, mid(m)));
      if (ch.textContent !== ct) { ch.textContent = ct; ch.className = 'num ' + cls(c); }
      setTxt(v, big(vol24(m)));
    });
  }
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

  // footer: market lists and calculator
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
  });
  // Listeners once, outside menu filling — before, every open added another one.
  $('tCalcMenu').addEventListener('input', () => {
    const eqv = numOf($('cEq').value), r = numOf($('cRisk').value) / 100, en = numOf($('cEn').value), sl = numOf($('cSl').value);
    if (!(eqv && r && en && sl && sl !== en)) { ['cOut', 'cLots', 'cVal'].forEach((id) => ($(id).textContent = '—')); return; }
    const risk = eqv * r;
    const q = S.m.quote_inverse ? (risk * sl) / Math.abs(en - sl) : risk / Math.abs(en - sl);
    $('cOut').textContent = qtyF(S.m, q) + ' ' + S.m.base;
    $('cLots').textContent = lotsF(q / S.m.contract);
    $('cVal').textContent = usd(notional(S.m, q, en));
  });
  $('tCalcMenu').addEventListener('click', (e) => e.stopPropagation());

  /* =====================================================================
     ORDER TICKET
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
    updateTicket();
  }
  $$('[data-side]').forEach((b) => b.addEventListener('click', () => setSide(b.dataset.side)));
  $('tPxMid').onclick = () => { const p = markPx(); if (p) { $('tPx').value = p.toFixed(S.m.px_dec); updateTicket(); } };
  /* Size field in the chosen unit: Lots → USD → base unit → Lots. */
  const UNITS = ['lots', 'usd', 'base'];
  const unitLbl = () => (S.unit === 'usd' ? 'USD' : S.unit === 'lots' ? 'Lots' : S.m.base);
  function toUnit(q) {
    if (!(q > 0)) return '';
    if (S.unit === 'usd') return notional(S.m, q, refPx()).toFixed(2);
    if (S.unit === 'lots') {
      // Down to the 0.01-lot step (so 100% never exceeds the cap); below 0.01 lots there is no order.
      const l = Math.floor((q / S.m.contract) * 100 + 1e-9) / 100;
      return l >= 0.01 ? l.toFixed(2) : '';
    }
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
    if (S.kind !== 'market') { const p = numOf($('tPx').value); if (p) return p; }
    const [b, a] = S.top;
    return (S.side === 'buy' ? a : b) || markPx() || 0;
  }
  function orderQty() {
    const v = numOf($('tSize').value);
    if (!v) return 0;
    return S.unit === 'usd' ? qtyFromUsd(S.m, v, refPx()) : S.unit === 'lots' ? v * S.m.contract : v;
  }
  /* 100% of the slider = the largest order the server accepts (terminal._check_open):
     free margin AND the account's lot cap (shared with Trade BOT). An opposite position
     on this market closes first, so its size comes on top and its margin and lots free
     up; with Reduce Only the maximum is that position. */
  function maxQty() {
    if (!S.st) return 0;
    const a = S.st.account, m = S.m, p = refPx(), L = lev();
    const istn = (S.st.positions || []).find((x) => x.symbol === m.sym);
    const opp = istn && istn.side !== S.side ? istn : null;
    if ($('tReduce').checked) return opp ? opp.qty : 0;
    const base = opp ? opp.qty : 0;
    const avail = (a.available || 0) + (opp ? opp.margin || 0 : 0);
    if (!p || !(avail > 0)) return base;
    const byMargin = qtyFromUsd(m, (avail * L) / (1 + (S.fee / 100) * L), p);
    const cap = a.max_lots > 0 ? Math.max(0, a.max_lots - (openLots(a) - (opp ? opp.qty / m.contract : 0))) * m.contract : Infinity;
    return base + Math.max(0, Math.min(byMargin, cap));
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
    updateTicket();
  }
  function updateTicket() {
    const m = S.m, q = orderQty(), p = refPx(), L = lev();
    const val = q > 0 ? notional(m, q, p) : 0;
    // Converter under the Size field: lots ⇄ base units (⇄ USD).
    $('tSizeHint').textContent = q > 0 ? `${lotsF(q / m.contract)} lots = ${qtyF(m, q)} ${m.base}${S.unit !== 'usd' && val ? ' ≈ ' + usd(val, 0) : ''}` : `1 lot = ${fx(m.contract, 0)} ${m.base}`;
    $('tEstPx').textContent = p ? px(m, p) : '—';
    $('tEstVal').textContent = val ? usd(val) : '—';
    $('tEstMargin').textContent = val ? usd(val / L) : '—';
    $('tSlip').textContent = S.kind === 'market' ? S.slip + '%' : '—';
    $('tFee').textContent = S.fee.toFixed(3) + '%';
    const tp = numOf($('tTp').value), sl = numOf($('tSl').value), ref = p || markPx();
    $('tTpPct').textContent = tp > 0 && ref ? pct(((S.side === 'buy' ? tp - ref : ref - tp) / ref) * 100 * L) : 'ROE';
    $('tSlPct').textContent = sl > 0 && ref ? pct(((S.side === 'buy' ? sl - ref : ref - sl) / ref) * 100 * L) : 'ROE';
    if (S.st) {
      const a = S.st.account;
      $('tCap').textContent = `${fx(openLots(a), 2)} / ${fx(a.max_lots, 2)} lots`;
    } else $('tCap').textContent = S.token ? '—' : '—';
  }
  // The lot cap is shared with Trade BOT: its open positions count too.
  const openLots = (a) => (a.open_lots || 0) + (a.other_lots || 0);
  let sending = false;
  async function submitOrder() {
    if (!S.token) return openModal('tReady');
    if (!S.st || !S.st.account.tradable) {
      return toast('This account cannot trade', (S.st && S.st.account.reason) || 'Choose an active challenge account.', true);
    }
    if (sending) return;
    const m = S.m, raw = orderQty();
    if (!(raw > 0)) return toast('Enter a size', 'Type an amount or use the slider.', true);
    // Volume in steps of 0.01 lots, minimum 0.01 (as on MT5); USD or units are rounded down to the step.
    const q = (Math.floor((raw / m.contract) * 100 + 1e-6) / 100) * m.contract;
    if (q / m.contract < 0.01 - 1e-9) return toast('Order too small', `Minimum size is 0.01 lots (${fx(0.01 * m.contract, Math.max(0, m.qty_dec))} ${m.base}).`, true);
    const body = {
      account_id: S.accId, symbol: m.sym, side: S.side, kind: S.kind, qty: +q.toFixed(Math.max(m.qty_dec, 2) + 2),
      reduce_only: $('tReduce').checked, max_slippage_pct: S.slip,
    };
    if (S.kind !== 'market') {
      body.price = numOf($('tPx').value);
      if (!body.price) return toast('Enter a price', S.kind === 'limit' ? 'Set the limit price for this order.' : 'Set the trigger price for this order.', true);
    }
    if ($('tTpsl').checked) { const tp = numOf($('tTp').value), sl = numOf($('tSl').value); if (tp) body.tp = tp; if (sl) body.sl = sl; }
    sending = true;
    $('tGo').disabled = true;
    $('tGo').classList.add('busy');
    try {
      const st = await api('/api/terminal/orders', { method: 'POST', body: JSON.stringify(body) });
      applyState(st);
      const what = S.kind === 'market' ? 'Order filled' : 'Order placed';
      beep('fill');
      const z = st.order_history && st.order_history[0];
      toast(what, `${S.side === 'buy' ? 'Long' : 'Short'} ${lotsOf(m, body.qty)} (${qtyF(m, body.qty)} ${m.base}) ${m.sym}${z && z.fill_price && S.kind === 'market' ? ' at ' + px(m, z.fill_price) : body.price ? ' at ' + px(m, body.price) : ''}`);
      $('tSize').value = ''; $('tPct').value = 0; $('tPctIn').value = 0;
      updateTicket();
    } catch (e) {
      toast('Order rejected', e.message, true);
    } finally {
      sending = false;
      $('tGo').disabled = false;
      $('tGo').classList.remove('busy');
    }
  }
  $('tGo').onclick = () => submitOrder();

  /* =====================================================================
     ACCOUNT, SIGN-IN, STATE
     ===================================================================== */
  /* Several MT5 accounts stay signed in side by side, each with its own session. */
  function saveLogins() { if (!S.imp) ls.set('pf_term_logins', JSON.stringify(S.logins.filter((l) => !l.temp))); }
  function restoreLogins() {
    const m = /^#imp=(.+)$/.exec(location.hash);
    if (m) {
      S.imp = true;
      S.logins = [{ token: decodeURIComponent(m[1]) }];
      history.replaceState(null, '', location.pathname + location.search);
    } else {
      try { S.logins = JSON.parse(ls.get('pf_term_logins', '[]')) || []; } catch (e) { S.logins = []; }
    }
    const cur = S.logins.find((l) => l.id === S.accId) || S.logins[0];
    S.token = cur ? cur.token : null;
  }
  function clearView() {
    stateSeq++;
    S.st = null;
    S.closing.clear(); S.justClosed.clear();
    renderUser(); renderTables(); renderAccount(); drawLines(); updateTicket();
  }
  /* Removes one saved account. If it was the one in view, the next saved one takes over. */
  function dropLogin(token) {
    const gone = S.logins.find((l) => l.token === token);
    S.logins = S.logins.filter((l) => l.token !== token);
    if (gone) S.accounts = S.accounts.filter((a) => a.id !== gone.id);
    saveLogins();
    if (token !== S.token) { renderUser(); renderTables(); return; }
    const next = S.logins[0];
    S.token = next ? next.token : null;
    S.accId = next ? next.id : null;
    clearView();
    if (next) refresh(true);
  }
  function signOut(token) {
    const t = token || S.token;
    if (!t) return;
    fetch('/api/terminal/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + t } }).catch(() => {});
    dropLogin(t);
    toast('MT5 account disconnected');
  }
  function renderUser() {
    renderDock();
    $('tMMLogin').hidden = !!S.token;
    const a = S.accounts.find((x) => x.id === S.accId);
    $('tAccLbl').textContent = a ? `${a.login} · ${usd(a.balance, 0)}` : S.token ? 'Connecting…' : 'Not connected';
    $('tAccBtn').querySelector('.dot').classList.toggle('off', !(a && a.tradable));
  }
  const phaseLbl = (a) => (a.status === 'funded' || a.phase === 'funded' ? 'Funded' : a.phase === 'eval_2' ? 'Phase 2' : a.status === 'passed' ? 'Passed' : 'Phase 1');
  const statusLbl = (a) => (a.tradable ? 'Active' : a.status === 'failed' ? 'Breached' : 'Not tradable');

  async function loadAccounts() {
    if (!S.logins.length) { S.token = null; S.accounts = []; renderUser(); renderTables(); renderAccount(); return; }
    const res = await Promise.all(S.logins.map(async (l) => {
      try {
        const r = await fetch('/api/terminal/accounts', { headers: { Authorization: 'Bearer ' + l.token } });
        if (r.status === 401) return { l, gone: true };
        const d = await r.json();
        return { l, a: d.accounts && d.accounts[0] };
      } catch (e) { return { l, a: null }; }   // offline: keep the saved session, retry on the next load
    }));
    res.filter((x) => x.gone).forEach((x) => dropLogin(x.l.token));
    res.filter((x) => x.a).forEach((x) => { x.l.id = x.a.id; x.l.login = x.a.login; });
    saveLogins();
    S.accounts = res.filter((x) => x.a).map((x) => x.a);
    const cur = S.logins.find((l) => l.id === S.accId) || S.logins[0];
    S.token = cur ? cur.token : null;
    S.accId = cur ? cur.id : null;
    renderUser();
    if (S.accId) await refresh(); else { renderTables(); renderAccount(); }
  }
  function selectAccount(id) {
    if (id === S.accId && S.st) return;
    const l = S.logins.find((x) => x.id === id);
    if (!l) return;
    S.accId = id;
    S.token = l.token;
    ls.set('pf_term_acc', String(id));
    // The previous account's state goes at once: the ticket must not check margin or
    // status of the old account while an order would already go to the new one.
    clearView();
    refresh(true);
  }
  /* Each state response is numbered. A late read (poll sent before an order,
     an account switch or a sign-out) won't overwrite a newer one. */
  let refreshing = false, stateSeq = 0;
  async function refresh(force) {
    if (!S.token || !S.accId || (refreshing && !force)) return;
    refreshing = true;
    const nr = ++stateSeq, acc = S.accId;
    try {
      const st = await api('/api/terminal/state?account_id=' + acc);
      if (nr === stateSeq && acc === S.accId) setState(st);
    } catch (e) {} finally { refreshing = false; }
  }
  // State from an action response (order, close) — invalidates reads in flight.
  function applyState(st) { stateSeq++; setState(st); }
  function setState(st) {
    if (!S.token || !st || !st.account || st.account.id !== S.accId) return;
    const before = S.st;
    S.st = st;
    const a = S.accounts.find((x) => x.id === st.account.id);
    if (a) Object.assign(a, { balance: st.account.balance, status: st.account.status, tradable: st.account.tradable });
    if (before) {
      notifyFills(before, st);
      const open = new Set(st.positions.map((p) => p.id));
      before.positions.forEach((p) => { if (!open.has(p.id)) S.justClosed.add(p.id); });
    }
    S.closing.forEach((id) => { if (!st.positions.some((p) => p.id === id)) S.closing.delete(id); });
    S.fee = st.fee_pct;
    watchLimits(st);
    if (before && report && before.history[0] !== st.history[0] && (!before.history[0] || !st.history[0] || before.history[0].id !== st.history[0].id)) report = null;
    renderUser(); renderAccount(); renderTables(); drawLines(); updateTicket();
  }
  function notifyFills(a, b) {
    const was = new Set(a.positions.map((p) => p.id));
    const now = new Set(b.positions.map((p) => p.id));
    b.history.filter((h) => was.has(h.id) && !now.has(h.id) && ['tp', 'sl', 'liquidation', 'stop_out', 'breach', 'phase'].includes(h.reason)).forEach((h) => {
      const L = { tp: 'Take profit hit', sl: 'Stop loss hit', liquidation: 'Position liquidated', stop_out: 'Stop-out: margin level too low', breach: 'Closed: account rule breached', phase: 'Phase passed: position closed' }[h.reason];
      signal(h.pnl < 0 ? 'bad' : 'good', L, `${h.symbol} ${sUsd(h.pnl)}`, h.pnl < 0);
    });
    b.positions.filter((p) => p.managed && !was.has(p.id)).forEach((p) => signal('fill', 'Position opened', `${p.symbol} ${p.side === 'buy' ? 'Long' : 'Short'} ${lotsF(p.lots)} lots`));
    b.history.filter((h) => h.managed && was.has(h.id) && !now.has(h.id) && h.reason === 'close' && !S.closing.has(h.id)).forEach((h) => signal(h.pnl < 0 ? 'bad' : 'good', 'Position closed', `${h.symbol} ${sUsd(h.pnl)}`, h.pnl < 0));
    const oldOpen = new Set(a.orders.map((o) => o.id));
    b.order_history.filter((o) => oldOpen.has(o.id) && o.status === 'filled').forEach((o) => signal('fill', 'Order filled', `${o.symbol} ${o.kind} ${o.side} at ${px(BY[o.symbol] || S.m, o.fill_price)}`));
    if (a.account.status !== b.account.status && b.account.status === 'failed') toast('Account closed', 'An account rule was breached. Check your dashboard for details.', true);
  }
  setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 15000);
  // Back to the tab: account state, market context and ticker bar at once, not after the next cycle.
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { refresh(); loadCtx(); renderStrip(); } });

  // live P&L in the browser (Hyperliquid mids, as on the server)
  function livePos() {
    if (!S.st) return [];
    return S.st.positions.filter((p) => !S.closing.has(p.id)).map((p) => {
      const m = BY[p.symbol];
      if (!m) return p;                     // market not in the terminal list: server price
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
    if (S.ptab === 'positions') patchPositions();
    updatePosLines();
    checkTriggers();
  }
  // Each tick changes only price, value and P&L in existing rows. Rebuilding the whole
  // table every 400 ms swapped buttons mid-click and "Close" could fail to fire.
  function patchPositions() {
    const rows = $('tTable').querySelectorAll('tbody tr[data-pid]');
    const live = livePos();
    if (rows.length !== live.length || live.some((p, i) => rows[i].dataset.pid !== String(p.id))) { renderTables(); return; }
    live.forEach((p, i) => {
      const m = BY[p.symbol];
      const put = (f, txt, c) => {
        const td = rows[i].querySelector(`[data-f="${f}"]`);
        if (!td) return;
        if (td.textContent !== txt) td.textContent = txt;
        if (c != null) td.className = ('num ' + c).trim();
      };
      put('value', usd(p.value));
      put('mark', px(m, p.mark));
      put('pnl', `${sUsd(p.pnl)} (${pct(p.roe)})`, cls(p.pnl));
    });
  }
  // Price crossed TP/SL/limit — ask the server for a review at once (it decides).
  let trigT = 0;
  function checkTriggers() {
    if (!S.st || document.hidden || Date.now() - trigT < 4000) return;
    // A weekend-closed market triggers nothing (server prices it at Friday's) —
    // without this a crossed order asked the server every 4 s all weekend.
    const live = (m) => m && !(S.weekend && m.cls !== 'crypto');
    const hit = livePos().some((p) => live(BY[p.symbol]) && p.mark && ((p.side === 'buy' && ((p.tp && p.mark >= p.tp) || (p.sl && p.mark <= p.sl)))
      || (p.side === 'sell' && ((p.tp && p.mark <= p.tp) || (p.sl && p.mark >= p.sl)))))
      || S.st.orders.some((o) => { const m = BY[o.symbol]; const p = live(m) && S.mids[m.hl]; if (!p) return false; return o.kind === 'limit' ? (o.side === 'buy' ? p <= o.price : p >= o.price) : (o.side === 'buy' ? p >= o.price : p <= o.price); });
    if (hit) { trigT = Date.now(); refresh(); }
  }

  function renderAccount() {
    if (!S.st) {
      ['tMUsed', 'tMBal', 'tMAvail', 'tDayLeft', 'tDdLeft'].forEach((id) => ($(id).textContent = '—'));
      $('tMBar').style.width = '0';
      $('tAcLine').hidden = true;
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
    $('tAcLine').hidden = false;
    $('tAlBal').textContent = usd(a.balance);
    $('tAlEq').textContent = usd(eq);
    $('tAlMar').textContent = usd(used);
    $('tAlFree').textContent = usd(Math.max(0, eq - used));
    $('tAlLvl').textContent = used > 0 ? nf(0).format((eq / used) * 100) + '%' : '—';
    const al = $('tAlPnl');
    al.textContent = sUsd(floating);
    al.className = 'num ' + cls(floating);
  }

  /* ---------- chart lines ---------- */
  function drawLines() {
    if (!chart) return;
    chart.removeOverlay({ groupId: 'ptf' });
    drawTrades();             // also without state: after sign-out trades leave the chart
    const ts = lastBar ? lastBar.timestamp : Date.now();
    const up = css('--up'), dn = css('--dn'), acc = css('--acc'), warn = css('--warn');
    const line = (v, text, color, dod) => v && chart.createOverlay({ name: 'ptfLine', groupId: 'ptf', lock: true, points: [{ timestamp: ts, value: v }], extendData: { text, color, ...(dod || {}) } });
    S.alerts.filter((a) => a.sym === S.sym).forEach((a) => line(a.price, `Alert ${px(S.m, a.price)}`, css('--muted')));
    if (!S.st) return;
    posLines = {};
    livePos().filter((p) => p.symbol === S.sym).forEach((p) => {
      const ext = { up, dn, pos: true, bg: css('--panel'), pnlText: pnlTxt(p), pnlColor: p.pnl >= 0 ? up : dn };
      const id = line(p.entry, `${p.side === 'buy' ? 'Long' : 'Short'} ${lotsOf(S.m, p.qty)} @ ${px(S.m, p.entry)}`, p.side === 'buy' ? up : dn, ext);
      if (id) posLines[p.id] = { id, ts, entry: p.entry, ext: { text: `${p.side === 'buy' ? 'Long' : 'Short'} ${lotsOf(S.m, p.qty)} @ ${px(S.m, p.entry)}`, color: p.side === 'buy' ? up : dn, ...ext } };
      if (!(tpslEd && tpslEd.id === p.id)) {
        line(p.tp, 'TP ' + px(S.m, p.tp), acc);
        line(p.sl, `SL ${px(S.m, p.sl)}${p.trail ? ' · trailing' : ''}`, warn);
      }
    });
    S.st.orders.filter((o) => o.symbol === S.sym).forEach((o) => line(S.ordDrag && S.ordDrag.id === o.id ? S.ordDrag.v : o.price, `${o.kind === 'limit' ? 'Limit' : 'Stop'} ${o.side} ${lotsOf(S.m, o.qty)}`, o.side === 'buy' ? up : dn));
  }
  // Live P&L on the position line: each price tick changes only the result box.
  let posLines = {};
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pnlTxt = (p) => (ok(p.pnl) ? `${sUsd(p.pnl)} (${pct(p.roe)})` : '');
  function updatePosLines() {
    if (!chart || !S.st) return;
    livePos().filter((p) => p.symbol === S.sym).forEach((p) => {
      const L = posLines[p.id];
      if (!L) return;
      const t = pnlTxt(p), c = p.pnl >= 0 ? L.ext.up : L.ext.dn;
      if (L.ext.pnlText === t && L.ext.pnlColor === c) return;
      L.ext = { ...L.ext, pnlText: t, pnlColor: c };
      chart.overrideOverlay({ id: L.id, extendData: L.ext });
    });
  }

  /* Position buttons on the entry line (as in cTrader): TP/SL, Reverse, Close. They are
     HTML over the chart, right after the P&L box; each frame they follow the line's
     y (scroll, zoom, price-scale changes) and hide when it leaves the price pane. */
  const posBar = document.createElement('div');
  posBar.className = 't-posbtns';
  $('tChart').appendChild(posBar);
  function placePosBtns() {
    const ids = Object.keys(posLines);
    const k = ids.join(',');
    if (posBar.dataset.k !== k) {
      posBar.dataset.k = k;
      const mgd = (id) => !!(S.st && S.st.positions.some((x) => x.id === +id && x.managed));
      posBar.innerHTML = ids.map((id) => `<div class="t-pb" data-pb="${id}" hidden>
        <button type="button" data-pbt="tpsl" title="Set take profit / stop loss">TP/SL</button>
        <button type="button" data-pbt="be" title="Move the stop to the entry price">BE</button>
        <button type="button" data-pbt="trail" title="Trailing stop">Trail</button>
        ${mgd(id) ? '' : '<button type="button" data-pbt="rev" title="Reverse position" aria-label="Reverse position"><svg viewBox="0 0 24 24"><path d="M7 4v14M3 14l4 4 4-4M17 20V6M13 10l4-4 4 4"/></svg></button>'}
        <button type="button" data-pbt="close" class="x" title="Close position" aria-label="Close position"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>`).join('');
    }
    if (!ids.length || !chart || cTab !== 'chart') return;
    let pane = null;
    try { pane = chart.getSize('candle_pane'); } catch (e) {}
    ids.forEach((id) => {
      const L = posLines[id], el = posBar.querySelector(`[data-pb="${id}"]`);
      if (!L || !el) return;
      let y = null;
      try { const c = chart.convertToPixel({ timestamp: L.ts, value: L.entry }, { paneId: 'candle_pane', absolute: true }); y = c && c.y; } catch (e) {}
      const show = ok(y) && (!pane || (y > pane.top + 8 && y < pane.top + pane.height - 8));
      el.hidden = !show;
      if (!show) return;
      const x = 10 + szerTekstu(L.ext.text) + 16 + (L.ext.pnlText ? szerTekstu(L.ext.pnlText) + 16 : 0);
      const tr = `translate(${Math.round(x)}px,${Math.round(y)}px) translateY(-50%)`;
      if (el.style.transform !== tr) el.style.transform = tr;
    });
  }
  (function loop() { if (!document.hidden) placePosBtns(); requestAnimationFrame(loop); })();
  posBar.addEventListener('click', (e) => {
    const b = e.target.closest('[data-pbt]');
    if (!b) return;
    const id = +b.closest('[data-pb]').dataset.pb;
    if (b.dataset.pbt === 'close') closePos(id, false, b);
    else if (b.dataset.pbt === 'rev') reversePos(id, b);
    else if (b.dataset.pbt === 'be') breakEven(id, b);
    else if (b.dataset.pbt === 'trail') trailDialog(id);
    else startTpsl(id);
  });

  /* TP/SL on the chart (as in cTrader): the TP/SL button puts both levels on the chart as
     lines with draggable handles — a level not set yet starts 0.4% from the price. The
     handle shows the price and the position's P&L there, live while dragging; a small
     panel beside the position line takes typed values (kept in sync with the lines) and
     saves or cancels. Nothing is sent until Save. */
  let tpslEd = null;   // { id, tp, sl, ov: {tp, sl} }
  const tpslBar = document.createElement('div');
  tpslBar.className = 't-tpsl-ed';
  tpslBar.hidden = true;
  $('tChart').appendChild(tpslBar);
  const roundPx = (m, v) => +(+v).toFixed(m.px_dec);
  function tpslOk(p, k, v) {
    if (!v) return true;
    const ref = markPx() || p.entry, long = p.side === 'buy';
    return k === 'tp' ? (long ? v > ref : v < ref) : (long ? v < ref : v > ref);
  }
  function tpslLabel(p, k, v) {
    const m = BY[p.symbol], pnl = pnlOf(m, p.side, p.qty, p.entry, v);
    return `${k.toUpperCase()} ${px(m, v)} · ${sUsd(pnl)}`;
  }
  function tpslDraw(k) {
    const p = S.st && S.st.positions.find((x) => x.id === tpslEd.id);
    if (!p || !chart) return;
    const v = tpslEd[k], col = css(k === 'tp' ? '--acc' : '--warn');
    if (tpslEd.ov[k]) { chart.removeOverlay({ id: tpslEd.ov[k] }); tpslEd.ov[k] = null; }
    if (!v) return;
    tpslEd.ov[k] = chart.createOverlay({ name: 'ptfLine', groupId: 'tpslEd', lock: true,
      points: [{ timestamp: lastBar ? lastBar.timestamp : Date.now(), value: v }],
      extendData: { text: '', noLabel: true, color: tpslOk(p, k, v) ? col : css('--dn') } });
  }
  function tpslRender() {
    if (!tpslEd) return;
    const p = S.st && S.st.positions.find((x) => x.id === tpslEd.id);
    if (!p) return stopTpsl();
    ['tp', 'sl'].forEach((k) => {
      tpslDraw(k);
      const h = tpslBar.querySelector(`[data-h="${k}"]`), v = tpslEd[k];
      h.hidden = !v;
      if (v) { h.querySelector('span').textContent = tpslLabel(p, k, v); h.classList.toggle('bad', !tpslOk(p, k, v)); }
      const inp = tpslBar.querySelector(`[data-in="${k}"]`);
      if (inp && document.activeElement !== inp) inp.value = v ? px(BY[p.symbol], v).replace(/,/g, '') : '';
    });
  }
  /* Two ways in: the TP/SL button (both levels + a panel with typed prices), or grabbing
     the dots on an existing TP/SL line (`line`: just the lines, applied with ✓ on the line). */
  function startTpsl(id, line) {
    const p = S.st && S.st.positions.find((x) => x.id === id);
    if (!p || p.symbol !== S.sym) return editTpsl(id);
    stopTpsl();
    const m = BY[p.symbol], ref = markPx() || p.entry, d = ref * 0.004, long = p.side === 'buy';
    tpslEd = line
      ? { id, tp: p.tp || null, sl: p.sl || null, ov: {}, line: true }
      : { id, tp: p.tp || roundPx(m, long ? ref + d : ref - d), sl: p.sl || roundPx(m, long ? ref - d : ref + d), ov: {} };
    const acts = '<b class="acts"><button type="button" data-tp-act="save" title="Apply" aria-label="Apply">✓</button><button type="button" data-tp-act="cancel" title="Cancel" aria-label="Cancel">✕</button></b>';
    tpslBar.classList.toggle('line', !!line);
    tpslBar.innerHTML = `
      <div class="t-th tp" data-h="tp"><i></i><span></span>${acts}</div>
      <div class="t-th sl" data-h="sl"><i></i><span></span>${acts}</div>
      ${line ? '' : `<div class="t-tp-panel" role="dialog" aria-label="Take profit and stop loss">
        <label><span>TP</span><input data-in="tp" inputmode="decimal" aria-label="Take profit price"><button type="button" data-clr="tp" title="Remove take profit" aria-label="Remove take profit">✕</button></label>
        <label><span>SL</span><input data-in="sl" inputmode="decimal" aria-label="Stop loss price"><button type="button" data-clr="sl" title="Remove stop loss" aria-label="Remove stop loss">✕</button></label>
        <div class="row"><button type="button" class="ok" data-tp-act="save">Save</button><button type="button" data-tp-act="cancel">Cancel</button></div>
        <small>Drag the lines or type a price.</small>
      </div>`}`;
    tpslBar.hidden = false;
    drawLines();          // the position's fixed TP/SL lines step aside while editing
    tpslRender();
  }
  function stopTpsl() {
    if (!tpslEd) return;
    if (chart) chart.removeOverlay({ groupId: 'tpslEd' });
    tpslEd = null;
    tpslBar.hidden = true;
    tpslBar.innerHTML = '';
    drawLines();
  }
  async function saveTpsl() {
    if (!tpslEd) return;
    const { id, tp, sl } = tpslEd;
    await once('pos' + id, tpslBar.querySelector('[data-tp-act="save"]'), async () => {
      try {
        applyState(await api(`/api/terminal/positions/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: S.accId, tp: tp || null, sl: sl || null }) }));
        toast('TP / SL updated', `${tp ? 'TP ' + px(S.m, tp) : 'No TP'} · ${sl ? 'SL ' + px(S.m, sl) : 'No SL'}`);
        stopTpsl();
      } catch (er) { toast('Could not update', er.message, true); }
    });
  }
  tpslBar.addEventListener('click', (e) => {
    const a = e.target.closest('[data-tp-act]'), c = e.target.closest('[data-clr]');
    if (c) { tpslEd[c.dataset.clr] = null; tpslRender(); return; }
    if (a) a.dataset.tpAct === 'save' ? saveTpsl() : stopTpsl();
  });
  tpslBar.addEventListener('input', (e) => {
    const k = e.target.dataset.in;
    if (!k || !tpslEd) return;
    tpslEd[k] = numOf(e.target.value) || null;
    tpslRender();
  });
  tpslBar.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); saveTpsl(); }
    if (e.key === 'Escape') { e.stopPropagation(); stopTpsl(); }
  });
  // Dragging a handle: the chart's own pan must not start under it.
  function dragLevel(k) {
    const h = tpslBar.querySelector(`[data-h="${k}"]`), m = S.m, r = $('tChart').getBoundingClientRect();
    if (!h) return;
    h.classList.add('drag');
    const mv = (ev) => {
      let pt = null;
      try { pt = chart.convertFromPixel({ x: 40, y: ev.clientY - r.top }, { paneId: 'candle_pane', absolute: true }); } catch (er) {}
      if (pt && ok(pt.value) && pt.value > 0) { tpslEd[k] = roundPx(m, pt.value); tpslRender(); }
    };
    const up = () => { h.classList.remove('drag'); window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', mv);
    window.addEventListener('mouseup', up);
  }
  tpslBar.addEventListener('mousedown', (e) => {
    const h = e.target.closest('[data-h]');
    if (!h || !tpslEd || e.target.closest('button')) return;
    e.preventDefault(); e.stopPropagation();
    dragLevel(h.dataset.h);
  }, true);

  /* Dots on the position's TP/SL lines (as in cTrader): grab one to move that level; the
     new price shows on the line and is sent only after ✓ on it (✕ puts it back). */
  const gripBar = document.createElement('div');
  gripBar.className = 't-grips';
  $('tChart').appendChild(gripBar);
  function placeGrips() {
    const want = [];
    if (S.st && chart && cTab === 'chart') {
      S.st.positions.filter((p) => p.symbol === S.sym && !(tpslEd && tpslEd.id === p.id)).forEach((p) => {
        ['tp', 'sl'].forEach((k) => { if (p[k]) want.push({ id: p.id, k, v: p[k], text: `${k.toUpperCase()} ${px(S.m, p[k])}${k === 'sl' && p.trail ? ' · trailing' : ''}` }); });
      });
      S.st.orders.filter((o) => o.symbol === S.sym).forEach((o) => want.push({ id: o.id, k: 'ord', v: S.ordDrag && S.ordDrag.id === o.id ? S.ordDrag.v : o.price, text: `${o.kind === 'limit' ? 'Limit' : 'Stop'} ${o.side} ${lotsOf(S.m, o.qty)}` }));
    }
    const key = want.map((w) => `${w.id}${w.k}`).join(',');
    if (gripBar.dataset.k !== key) {
      gripBar.dataset.k = key;
      gripBar.innerHTML = want.map((w) => `<button type="button" class="t-grip ${w.k}" data-gp="${w.id}" data-gk="${w.k}" title="Drag to move ${w.k === 'tp' ? 'take profit' : 'stop loss'}" aria-label="Move ${w.k === 'tp' ? 'take profit' : 'stop loss'}"><i></i></button>`).join('');
    }
    if (!want.length) return;
    let pane = null;
    try { pane = chart.getSize('candle_pane'); } catch (e) {}
    want.forEach((w) => {
      const el = gripBar.querySelector(`[data-gp="${w.id}"][data-gk="${w.k}"]`);
      let y = null;
      try { const c = chart.convertToPixel({ timestamp: lastBar ? lastBar.timestamp : Date.now(), value: w.v }, { paneId: 'candle_pane', absolute: true }); y = c && c.y; } catch (e) {}
      const vis = ok(y) && (!pane || (y > pane.top + 8 && y < pane.top + pane.height - 8));
      el.style.visibility = vis ? '' : 'hidden';
      if (vis) el.style.transform = `translate(${Math.round(10 + szerTekstu(w.text) + 16)}px,${Math.round(y)}px) translateY(-50%)`;
    });
  }
  gripBar.addEventListener('mousedown', (e) => {
    const g = e.target.closest('[data-gk]');
    if (!g) return;
    e.preventDefault(); e.stopPropagation();
    if (g.dataset.gk === 'ord') return dragOrder(+g.dataset.gp);
    startTpsl(+g.dataset.gp, true);
    if (tpslEd) dragLevel(g.dataset.gk);
  }, true);
  (function loop3() { if (!document.hidden) placeGrips(); requestAnimationFrame(loop3); })();
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && tpslEd) stopTpsl(); });
  function placeTpsl() {
    if (!tpslEd || !chart || cTab !== 'chart') return;
    if (S.st && !S.st.positions.some((x) => x.id === tpslEd.id)) return stopTpsl();   // position closed meanwhile
    const L = posLines[tpslEd.id];
    let pane = null;
    try { pane = chart.getSize('candle_pane'); } catch (e) {}
    const yOf = (v) => { try { const c = chart.convertToPixel({ timestamp: lastBar ? lastBar.timestamp : Date.now(), value: v }, { paneId: 'candle_pane', absolute: true }); return c && c.y; } catch (e) { return null; } };
    ['tp', 'sl'].forEach((k) => {
      const h = tpslBar.querySelector(`[data-h="${k}"]`);
      if (!h || !tpslEd[k]) return;
      // Off-screen levels stick to the pane's top/bottom edge, so ✓ / ✕ stay reachable.
      let y = yOf(tpslEd[k]);
      if (!ok(y)) return;
      if (pane) y = Math.min(pane.top + pane.height - 12, Math.max(pane.top + 12, y));
      h.style.transform = `translate(10px,${Math.round(y)}px) translateY(-50%)`;
    });
    const panel = tpslBar.querySelector('.t-tp-panel'), g = posBar.querySelector(`[data-pb="${tpslEd.id}"]`);
    if (panel && L) {
      const y = yOf(L.entry);
      const x = g && g.style.transform ? +(g.style.transform.match(/translate\((\d+)px/) || [0, 10])[1] : 10;
      if (ok(y)) panel.style.transform = `translate(${x}px,${Math.round(y) + 16}px)`;
    }
  }
  (function loop2() { if (!document.hidden) placeTpsl(); requestAnimationFrame(loop2); })();

  /* ---------- trade history on the chart ("Trades" button) ---------- */
  let tradesKey = '', tipXY = [0, 0], tipT = 0;
  function drawTrades(force) {
    if (!chart) return;
    const hist = S.showTrades && S.st ? (S.st.history || []).filter((h) => h.symbol === S.sym && h.opened_at && h.closed_at && h.entry && h.close).slice(0, 60) : [];
    // The 15 s state refresh doesn't redraw trades when they haven't changed (hover stays).
    const key = S.sym + '|' + hist.map((h) => h.id).join(',');
    if (!force && key === tradesKey) return;
    tradesKey = key;
    chart.removeOverlay({ groupId: 'ptfTrades' });
    hideTip();
    const up = css('--up'), dn = css('--dn'), bg = css('--bg');
    hist.forEach((h) => {
      const fresh = S.justClosed.has(h.id) && !REDUCED;
      const ext = { side: h.side, pnl: h.pnl, up, dn, bg, h, t: fresh ? 0 : 1 };
      const id = chart.createOverlay({
        name: 'ptfTrade', groupId: 'ptfTrades', lock: true,
        points: [{ timestamp: Date.parse(h.opened_at), value: h.entry }, { timestamp: Date.parse(h.closed_at), value: h.close }],
        extendData: ext,
        onMouseEnter: ({ overlay }) => hoverTrade(overlay, true),
        onMouseLeave: ({ overlay }) => hoverTrade(overlay, false),
        onClick: ({ overlay }) => { hoverTrade(overlay, true); clearTimeout(tipT); tipT = setTimeout(() => hoverTrade(overlay, false), 4000); return true; },
      });
      if (fresh && id) drawIn(id, ext);
    });
    S.justClosed.clear();
  }
  function drawIn(id, ext) {
    const t0 = performance.now(), D = 520;
    (function step(now) {
      // The frame timestamp can precede t0 (taken mid-frame); a negative k gave the
      // exit circle a negative radius and the canvas threw on arc().
      const k = Math.min(1, Math.max(0, (now - t0) / D)), t = 1 - Math.pow(1 - k, 3);
      chart.overrideOverlay({ id, extendData: { ...ext, t } });
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }
  function hoverTrade(overlay, on) {
    const e = overlay.extendData || {};
    if (!!e.hover !== on) chart.overrideOverlay({ id: overlay.id, extendData: { ...e, hover: on } });
    if (on) showTip(e.h); else hideTip();
  }
  $('tChart').addEventListener('mousemove', (ev) => {
    const r = $('tChart').getBoundingClientRect();
    tipXY = [ev.clientX - r.left, ev.clientY - r.top];
    if (!$('tTradeTip').hidden) placeTip();
  });
  function showTip(h) {
    if (!h) return;
    const m = BY[h.symbol] || S.m, t = $('tTradeTip');
    const dur = Math.max(0, Date.parse(h.closed_at) - Date.parse(h.opened_at)) / 60000;
    const czas = dur >= 1440 ? `${(dur / 1440).toFixed(1)} d` : dur >= 60 ? `${Math.floor(dur / 60)} h ${Math.round(dur % 60)} min` : `${Math.max(1, Math.round(dur))} min`;
    const pips = ((h.close - h.entry) * (h.side === 'buy' ? 1 : -1)) / pipOf(m);
    t.innerHTML = `<div class="hd"><b class="${h.side === 'buy' ? 'up' : 'dn'}">${h.side === 'buy' ? 'Long' : 'Short'}</b> ${lotsF(h.lots)} lots · ${esc(h.symbol)}</div>
      <div class="row"><span>Entry</span><b class="num">${px(m, h.entry)}</b><small>${tm(h.opened_at)}</small></div>
      <div class="row"><span>Exit</span><b class="num">${px(m, h.close)}</b><small>${tm(h.closed_at)}</small></div>
      <div class="row"><span>Pips</span><b class="num ${cls(pips)}">${pips >= 0 ? '+' : '-'}${fx(Math.abs(pips), 1)}</b></div>
      <div class="row"><span>P&amp;L</span><b class="num ${cls(h.pnl)}">${sUsd(h.pnl)}</b><small>${czas}</small></div>`;
    t.hidden = false;
    placeTip();
  }
  function placeTip() {
    const t = $('tTradeTip'), c = $('tChart');
    const x = Math.min(tipXY[0] + 14, c.clientWidth - t.offsetWidth - 8), y = Math.min(tipXY[1] + 14, c.clientHeight - t.offsetHeight - 8);
    t.style.left = Math.max(8, x) + 'px';
    t.style.top = Math.max(8, y) + 'px';
  }
  function hideTip() { const t = $('tTradeTip'); if (t) t.hidden = true; }
  $('tShowTrades').classList.toggle('on', S.showTrades);
  $('tShowTrades').setAttribute('aria-pressed', S.showTrades ? 'true' : 'false');
  $('tShowTrades').onclick = () => {
    S.showTrades = !S.showTrades;
    ls.set('pf_term_trades', S.showTrades ? '1' : '0');
    $('tShowTrades').classList.toggle('on', S.showTrades);
    $('tShowTrades').setAttribute('aria-pressed', S.showTrades ? 'true' : 'false');
    drawTrades(true);
  };

  /* =====================================================================
     BOTTOM TABLES
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
    ls.set('pf_term_cols3', JSON.stringify(S.cols));
    renderTables();
  });
  $$('.t-ptabs > [data-pt]').forEach((b) => b.addEventListener('click', () => setPTab(b.dataset.pt)));
  dropdown($('tMoreBtn'), $('tMoreMenu'));
  $('tMoreMenu').addEventListener('click', (e) => { const b = e.target.closest('[data-pt]'); if (b) { $('tMoreMenu').hidden = true; setPTab(b.dataset.pt); } });
  function setPTab(t) {
    S.ptab = t;
    const tb = $('tTable');
    tb.classList.remove('t-fade'); void tb.offsetWidth; tb.classList.add('t-fade');
    $$('.t-ptabs [data-pt]').forEach((b) => b.classList.toggle('on', b.dataset.pt === t || (b.id === 'tMoreBtn' && ['rules', 'fills'].includes(t))));
    renderTables();
  }
  function renderTables() {
    drawTables();
    // column header as the cell label: on phones rows are cards (CSS td[data-l]::before)
    const el = $('tTable');
    const th = $$('thead th', el).map((x) => x.textContent);
    $$('tbody tr', el).forEach((tr) => [...tr.children].forEach((td, i) => { if (th[i]) td.dataset.l = th[i]; }));
    // A position that has just opened slides into the table.
    if (S.ptab === 'positions' && S.st) {
      const key = S.accId, known = rowsSeen.key === key;
      $$('tr[data-pid]', el).forEach((tr) => { if (known && !rowsSeen.ids.has(+tr.dataset.pid)) tr.classList.add('t-row-in'); });
      rowsSeen = { key, ids: new Set(S.st.positions.map((p) => p.id)) };
    }
  }
  let rowsSeen = { key: null, ids: new Set() };
  /* Closing feels instant: the row and the chart lines go at once, the server confirms
     behind them. If it refuses, the position comes back with the error. */
  function leaveRow(ids) {
    ids.forEach((id) => {
      S.closing.add(id);
      const tr = $('tTable').querySelector(`tr[data-pid="${id}"]`);
      if (tr) tr.classList.add('t-row-out');
    });
    drawLines();
    updateTicket();
  }
  function comeBack(ids) {
    ids.forEach((id) => S.closing.delete(id));
    renderTables(); drawLines(); updateTicket();
  }
  function drawTables() {
    const el = $('tTable');
    const st = S.st;
    $('tPosN').textContent = st && st.positions.length ? ` (${st.positions.length})` : '';
    $('tOrdN').textContent = st && st.orders.length ? ` (${st.orders.length})` : '';
    $('tCloseAll').disabled = !(st && st.positions.length);
    if (!S.token) {
      el.innerHTML = `<div class="t-empty"><div>Connect your MT5 account to see your trading activity<br><button class="t-btn" type="button" data-signin>Connect account</button></div></div>`;
      return;
    }
    if (S.ptab === 'accounts') {
      el.innerHTML = `<div class="t-accs">${S.accounts.map((a) => `<button type="button" class="t-accc${a.id === S.accId ? ' on' : ''}" data-acc="${a.id}"><small>#${esc(a.login)} · ${esc(a.server || '')}</small><b>${usd(a.balance)}</b><small>${big(a.size)} challenge · ${phaseLbl(a)} · <span class="t-pill ${a.tradable ? 'ok' : a.status === 'failed' ? 'bad' : ''}">${statusLbl(a)}</span></small></button>`).join('')}${S.imp ? '' : '<button type="button" class="t-accc add" data-add-acc="1"><b>+</b><small>Add MT5 account</small></button>'}</div>`;
      return;
    }
    if (!st) { el.innerHTML = `<div class="t-empty">${S.accounts.length ? 'Loading…' : 'No active challenge account yet.'}</div>`; return; }
    if (S.ptab === 'report') { if (!el.querySelector('input:focus')) el.innerHTML = reportHtml(); return; }
    const sym = (s) => { const m = BY[s]; return m ? `<span class="sym">${mIcon(m, true)}${s}</span>` : esc(s); };
    const side = (s) => `<span class="side ${s === 'buy' ? 'up' : 'dn'}">${s === 'buy' ? 'Long' : 'Short'}</span>`;
    const table = (head, rows, empty) => (rows.length ? `<table><thead><tr>${head.map(([h, n]) => `<th class="${n ? 'num' : ''}">${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>` : `<div class="t-empty">${empty}</div>`);
    if (S.ptab === 'positions') {
      const c = S.cols;
      const head = [['Market'], ['Side'], ['Size', 1]].concat(c.value ? [['Value (USD)', 1]] : [], [['Entry Price', 1]], c.slippage ? [['Slippage', 1]] : [], [['Mark Price', 1]], [['PnL (ROE)', 1]], c.lev ? [['Leverage', 1]] : [], c.margin ? [['Margin', 1]] : [], c.tpsl ? [['TP / SL', 1]] : [], [['', 1]]);
      const rows = livePos().map((p) => {
        const m = BY[p.symbol] || S.m;
        return `<tr data-pid="${p.id}"><td>${sym(p.symbol)}</td><td>${side(p.side)}</td><td class="num">${sizeCell(m, p.qty)}</td>${c.value ? `<td class="num" data-f="value">${usd(p.value)}</td>` : ''}<td class="num">${px(m, p.entry)}</td>${c.slippage ? `<td class="num">${ok(p.slippage) ? p.slippage.toFixed(3) + '%' : '—'}</td>` : ''}<td class="num" data-f="mark">${px(m, p.mark)}</td><td class="num ${cls(p.pnl)}" data-f="pnl">${sUsd(p.pnl)} (${pct(p.roe)})</td>${c.lev ? `<td class="num">1:${p.leverage}</td>` : ''}${c.margin ? `<td class="num">${usd(p.margin)}</td>` : ''}${c.tpsl ? `<td class="num"><button type="button" class="tpsl" data-tpsl="${p.id}">${p.tp ? px(m, p.tp) : '—'} / ${p.sl ? px(m, p.sl) : '—'}</button></td>` : ''}<td><div class="act">${p.managed ? '' : `<button type="button" data-half="${p.id}">Close 50%</button>`}<button type="button" class="dn" data-close="${p.id}">Close</button></div></td></tr>`;
      });
      el.innerHTML = table(head, rows, 'No open positions');
    } else if (S.ptab === 'orders') {
      el.innerHTML = table([['Time'], ['Market'], ['Type'], ['Side'], ['Size', 1], ['Price', 1], ['Reduce Only'], ['TP / SL', 1], ['', 1]], st.orders.map((o) => {
        const m = BY[o.symbol];
        return `<tr><td>${tm(o.created_at)}</td><td>${sym(o.symbol)}</td><td>${o.kind === 'limit' ? 'Limit' : 'Stop Market'}</td><td>${side(o.side)}</td><td class="num">${sizeCell(m, o.qty)}</td><td class="num">${px(m, o.price)}</td><td>${o.reduce_only ? 'Yes' : 'No'}</td><td class="num">${o.tp ? px(m, o.tp) : '—'} / ${o.sl ? px(m, o.sl) : '—'}</td><td><div class="act"><button type="button" class="dn" data-cancel="${o.id}">Cancel</button></div></td></tr>`;
      }), 'No open orders');
      if (st.orders.length) el.insertAdjacentHTML('afterbegin', '<div style="padding:8px 12px;text-align:right"><button class="t-danger" type="button" data-cancelall>Cancel all orders</button></div>');
    } else if (S.ptab === 'phist') {
      const R = { close: 'Closed', reduce: 'Reduced', tp: 'Take profit', sl: 'Stop loss', liquidation: 'Liquidation', stop_out: 'Stop-out', breach: 'Rule breach', phase: 'Phase passed' };
      el.innerHTML = table([['Closed'], ['Market'], ['Side'], ['Size', 1], ['Entry', 1], ['Exit', 1], ['PnL', 1], ['Fees', 1], ['Reason']], st.history.map((h) => {
        const m = BY[h.symbol] || S.m;
        // Lots from the server (account measure, as in portal and MT5) — same form for each trade.
        const size = `${lotsF(h.lots)} lots <span class="sub">${qtyF(m, h.qty)} ${esc(m.base)}</span>`;
        return `<tr><td>${tm(h.closed_at)}</td><td>${sym(h.symbol)}</td><td>${side(h.side)}</td><td class="num">${size}</td><td class="num">${px(m, h.entry)}</td><td class="num">${px(m, h.close)}</td><td class="num ${cls(h.pnl)}">${sUsd(h.pnl)}</td><td class="num">${usd(h.fee)}</td><td><span class="t-pill ${h.reason === 'tp' ? 'ok' : ['sl', 'liquidation', 'stop_out', 'breach'].includes(h.reason) ? 'bad' : ''}">${R[h.reason] || esc(h.reason || '')}</span></td></tr>`;
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
        <div class="t-kv"><small>Exposure cap</small><b class="num">${fx(openLots(a), 2)} / ${fx(a.max_lots, 2)} lots</b>${meter(openLots(a), a.max_lots)}<p>All open positions together${a.other_lots ? ', including Trade BOT' : ''}.</p></div>
      </div>`;
    }
  }
  /* Table actions one at a time per object: a double click on "Close 50%" sent two
     halves and closed the whole position. The button is disabled until the reply
     (rows are no longer rebuilt every tick, so this stays visible). */
  const busy = new Set();
  async function once(key, btn, fn) {
    if (busy.has(key)) return;
    busy.add(key);
    if (btn) btn.disabled = true;
    try { await fn(); } finally { busy.delete(key); if (btn && btn.isConnected) btn.disabled = false; }
  }
  $('tTable').addEventListener('click', async (e) => {
    const t = e.target;
    if (t.closest('[data-signin]')) return openLogin();
    if (t.closest('[data-add-acc]')) return openLogin(true);
    const acc = t.closest('[data-acc]');
    if (acc) return selectAccount(+acc.dataset.acc);
    const cl = t.closest('[data-close]'), hf = t.closest('[data-half]');
    if (cl || hf) return closePos(+(cl || hf).dataset.close || +(hf && hf.dataset.half), !!hf, cl || hf);
    const cn = t.closest('[data-cancel]');
    if (cn) {
      return once('ord' + cn.dataset.cancel, cn, async () => {
        try { applyState(await api(`/api/terminal/orders/${cn.dataset.cancel}?account_id=${S.accId}`, { method: 'DELETE' })); toast('Order cancelled'); } catch (er) { toast('Cancel failed', er.message, true); refresh(true); }
      });
    }
    const ca = t.closest('[data-cancelall]');
    if (ca) {
      return once('cancelall', ca, async () => {
        try { applyState(await api('/api/terminal/cancel-all', { method: 'POST', body: JSON.stringify({ account_id: S.accId }) })); toast('All orders cancelled'); } catch (er) { toast('Cancel failed', er.message, true); }
      });
    }
    const tps = t.closest('[data-tpsl]');
    if (tps) editTpsl(+tps.dataset.tpsl);
  });
  function closePos(id, half, btn) {
    return once('pos' + id, btn, async () => {
      const p = S.st && S.st.positions.find((x) => x.id === id);
      if (!p) return;
      try {
        const body = { account_id: S.accId, max_slippage_pct: Math.max(S.slip, 2) };
        if (half) {
          // Half rounded down to 0.01 lots; at least 0.01 lots must stay open.
          const m = BY[p.symbol], hl = Math.floor((p.qty / m.contract / 2) * 100 + 1e-9) / 100;
          if (hl < 0.01) return toast('Position too small to split', 'Use Close to close the whole position.', true);
          body.qty = +(hl * m.contract).toFixed(8);
        } else leaveRow([id]);
        applyState(await api(`/api/terminal/positions/${id}/close`, { method: 'POST', body: JSON.stringify(body) }));
        const h = S.st && S.st.history[0];
        toast(half ? 'Closed 50%' : 'Position closed', h ? `${h.symbol} ${sUsd(h.pnl)}` : '');
        beep(h && h.pnl < 0 ? 'bad' : 'good');
      } catch (er) { if (!half) comeBack([id]); toast('Close failed', er.message, true); refresh(true); }
    });
  }
  /* Reverse as in cTrader: one market order of twice the size the other way — the
     account nets it into the same size in the opposite direction. */
  function reversePos(id, btn) {
    return once('pos' + id, btn, async () => {
      const p = S.st && S.st.positions.find((x) => x.id === id);
      const m = p && BY[p.symbol];
      if (!m) return;
      const side = p.side === 'buy' ? 'sell' : 'buy';
      try {
        applyState(await api('/api/terminal/orders', { method: 'POST', body: JSON.stringify({
          account_id: S.accId, symbol: m.sym, side, kind: 'market', qty: +(p.qty * 2).toFixed(Math.max(m.qty_dec, 2) + 2),
          reduce_only: false, max_slippage_pct: Math.max(S.slip, 2),
        }) }));
        toast('Position reversed', `${side === 'buy' ? 'Long' : 'Short'} ${lotsOf(m, p.qty)} ${m.sym}`);
      } catch (er) { toast('Reverse failed', er.message, true); refresh(true); }
    });
  }
  async function editTpsl(id) {
    const p = S.st.positions.find((x) => x.id === id);
    if (!p) return;
    const m = BY[p.symbol];
    const okk = await confirmBox(`TP / SL · ${p.symbol} ${p.side === 'buy' ? 'Long' : 'Short'}`, `<p>Entry ${px(m, p.entry)} · mark ${px(m, S.mids[m.hl] || p.mark)}</p>
      <div class="t-tpsl" style="margin-top:14px"><label class="t-fld"><span>Take profit</span><div class="t-in"><input id="eTp" inputmode="decimal" value="${p.tp || ''}" placeholder="—"></div></label>
      <label class="t-fld"><span>Stop loss</span><div class="t-in"><input id="eSl" inputmode="decimal" value="${p.sl || ''}" placeholder="—"></div></label></div>
      <p class="t-hint" style="margin-top:10px">Leave a field empty to remove it.</p>`, 'Save');
    if (!okk) return;
    const tp = numOf($('eTp').value) || null, sl = numOf($('eSl').value) || null;
    await once('pos' + id, null, async () => {
      try {
        applyState(await api(`/api/terminal/positions/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: S.accId, tp, sl }) }));
        toast('TP / SL updated');
      } catch (er) { toast('Could not update', er.message, true); refresh(true); }
    });
  }
  $('tCloseAll').onclick = async () => {
    if (!S.st || !S.st.positions.length) return;
    const okk = await confirmBox('Close all positions', `<p>Close <b>${S.st.positions.length}</b> open position(s) at market?</p>`, 'Close All', 'sell');
    if (!okk) return;
    await once('closeall', $('tCloseAll'), async () => {
      const ids = S.st.positions.map((p) => p.id);
      leaveRow(ids);
      try { applyState(await api('/api/terminal/close-all', { method: 'POST', body: JSON.stringify({ account_id: S.accId }) })); toast('All positions closed'); } catch (er) { comeBack(ids); toast('Close failed', er.message, true); refresh(true); }
    });
  };

  /* =====================================================================
     TRADING TOOLS: break-even, trailing stop, orders from the chart, one-click,
     keyboard, alerts, sounds and notifications, account report
     ===================================================================== */
  const lsJson = (k, d) => { try { return JSON.parse(ls.get(k, '')) || d; } catch (e) { return d; } };
  const PREF = Object.assign({ quick: true, keys: false, sounds: true, notify: false, lots: 0.01 }, lsJson('pf_term_prefs', {}));
  const savePref = () => ls.set('pf_term_prefs', JSON.stringify(PREF));
  const tradable = () => S.token && S.st && S.st.account && S.st.account.tradable && !S.imp;

  /* ---------- sounds and notifications ---------- */
  let audio = null;
  function beep(kind) {
    if (!PREF.sounds) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const notes = { fill: [660, 880], good: [660, 990], bad: [440, 300], alert: [880, 880] }[kind] || [660];
      notes.forEach((f, i) => {
        const o = audio.createOscillator(), g = audio.createGain(), t = audio.currentTime + i * 0.11;
        o.type = 'sine'; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.09, t + 0.015);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.1);
        o.connect(g).connect(audio.destination);
        o.start(t); o.stop(t + 0.11);
      });
    } catch (e) {}
  }
  function pushNote(title, body) {
    if (!PREF.notify || !('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
    try { new Notification(title, { body, icon: '/static/img/icon-192.png' }); } catch (e) {}
  }
  function signal(kind, title, body, bad) {
    beep(kind);
    pushNote(title, body);
    if (title) toast(title, body, bad);
  }
  /* Warns once per account and day when equity gets close to a loss limit. */
  function watchLimits(st) {
    const a = st.account, m = a && a.metrics;
    if (!m || !a.tradable) return;
    const floor = Math.max(m.daily_floor || 0, m.overall_floor || 0), room = a.equity - floor;
    const near = (a.size || 0) * 0.005;
    const key = `${a.id}:${(st.server_time || '').slice(0, 10)}`;
    if (floor > 0 && room <= near && room > 0 && watchLimits.seen !== key) {
      watchLimits.seen = key;
      signal('bad', 'Close to your loss limit', `${usd(room)} left before an account rule is breached.`, true);
    }
  }

  /* ---------- break-even and trailing stop ---------- */
  function breakEven(id, btn) {
    return once('pos' + id, btn, async () => {
      try {
        applyState(await api(`/api/terminal/positions/${id}/break-even`, { method: 'POST', body: JSON.stringify({ account_id: S.accId }) }));
        toast('Stop moved to break-even');
      } catch (er) { toast('Break-even not set', er.message, true); }
    });
  }
  async function trailDialog(id) {
    const p = S.st.positions.find((x) => x.id === id);
    if (!p) return;
    const m = BY[p.symbol], mk = S.mids[m.hl] || p.mark || p.entry;
    const preset = (pc) => roundPx(m, mk * pc / 100);
    const okk = await confirmBox(`Trailing stop · ${p.symbol} ${p.side === 'buy' ? 'Long' : 'Short'}`, `
      <p>The stop follows the best price at this distance and never moves back.</p>
      <label class="t-fld" style="margin-top:12px"><span>Distance (price)</span><div class="t-in"><input id="eTrail" inputmode="decimal" value="${p.trail || preset(0.5)}"></div></label>
      <div class="t-trail-pre">${[0.25, 0.5, 1, 2].map((v) => `<button type="button" data-v="${preset(v)}">${v}%</button>`).join('')}</div>
      <p class="t-hint" style="margin-top:10px">Set 0 to turn trailing off. The current stop stays where it is.</p>`, 'Save');
    if (!okk) return;
    const trail = numOf($('eTrail').value) || 0;
    await once('pos' + id, null, async () => {
      try {
        applyState(await api(`/api/terminal/positions/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: S.accId, tp: p.tp, sl: p.sl, trail }) }));
        toast(trail ? 'Trailing stop on' : 'Trailing stop off', trail ? `Distance ${px(m, trail)}` : '');
      } catch (er) { toast('Trailing stop not set', er.message, true); }
    });
  }
  $('tConfirmBody').addEventListener('click', (e) => {
    const b = e.target.closest('.t-trail-pre [data-v]');
    if (b) $('eTrail').value = b.dataset.v;
  });

  /* ---------- candle countdown under the price label on the axis ---------- */
  const cdEl = document.createElement('div');
  cdEl.className = 't-cd'; cdEl.hidden = true;
  $('tChart').appendChild(cdEl);
  function cdText(ms) {
    const t = Math.max(0, Math.ceil(ms / 1000)), d = Math.floor(t / 86400), h = Math.floor(t / 3600) % 24;
    const m = Math.floor(t / 60) % 60, sec = t % 60, p2 = (v) => String(v).padStart(2, '0');
    if (d) return `${d}d ${p2(h)}:${p2(m)}`;
    return (h ? p2(h) + ':' : '') + p2(m) + ':' + p2(sec);
  }
  function placeCd() {
    if (!chart || !lastBar || cTab !== 'chart') { cdEl.hidden = true; return; }
    const step = F.MS[S.interval];
    let end = lastBar.timestamp + step;
    if (end <= Date.now()) end = Math.floor(Date.now() / step + 1) * step;   // no new candle yet
    let y = null, pane = null, w = 60;
    try {
      pane = chart.getSize('candle_pane');
      w = chart.getSize('candle_pane', 'yAxis').width;
      const c = chart.convertToPixel({ timestamp: lastBar.timestamp, value: lastBar.close }, { paneId: 'candle_pane', absolute: true });
      y = c && c.y;
    } catch (e) {}
    const show = ok(y) && (!pane || (y > pane.top && y + 24 < pane.top + pane.height));
    cdEl.hidden = !show;
    if (!show) return;
    const txt = cdText(end - Date.now());
    if (cdEl.textContent !== txt) cdEl.textContent = txt;
    cdEl.style.width = Math.round(Math.min(w, szerTekstu(px(S.m, lastBar.close)) + 12)) + 'px';
    cdEl.style.transform = `translate(${Math.round($('tChart').clientWidth - w)}px,${Math.round(y + 10)}px)`;
    cdEl.classList.toggle('dn', lastBar.close < lastBar.open);
  }
  (function cdLoop() { if (!document.hidden) placeCd(); requestAnimationFrame(cdLoop); })();

  /* ---------- orders and alerts from the chart ---------- */
  const plus = document.createElement('button');
  plus.type = 'button'; plus.className = 't-plus'; plus.hidden = true; plus.textContent = '+';
  plus.title = 'Order or alert at this price';
  const plusMenu = document.createElement('div');
  plusMenu.className = 't-plus-menu'; plusMenu.hidden = true;
  $('tChart').append(plus, plusMenu);
  let plusPx = null;
  const priceAtY = (y) => {
    try { const pt = chart.convertFromPixel({ x: 40, y }, { paneId: 'candle_pane', absolute: true }); return pt && ok(pt.value) && pt.value > 0 ? pt.value : null; } catch (e) { return null; }
  };
  $('tChart').addEventListener('mousemove', (e) => {
    if (!chart || !plusMenu.hidden || !matchMedia('(hover:hover)').matches) return;
    if (armedId || e.target.closest('.t-grip,.t-pb,.t-th,.t-tp-panel,.t-oc')) { plus.hidden = true; return; }
    const r = $('tChart').getBoundingClientRect(), y = e.clientY - r.top, v = priceAtY(y);
    let w = 60;
    try { w = chart.getSize('candle_pane', 'yAxis').width; } catch (er) {}
    if (v == null || !S.m) { plus.hidden = true; return; }
    plusPx = roundPx(S.m, v);
    plus.hidden = false;
    plus.style.transform = `translate(${Math.round(r.width - w - 26)}px,${Math.round(y - 10)}px)`;
  });
  $('tChart').addEventListener('mouseleave', () => { if (plusMenu.hidden) plus.hidden = true; });
  ['mousedown', 'mouseup', 'click'].forEach((t) => [plus, plusMenu].forEach((el) => el.addEventListener(t, (e) => e.stopPropagation())));
  plus.addEventListener('click', () => {
    const m = S.m, mk = S.mids[m.hl] || 0, above = plusPx > mk, l = lotsF(PREF.lots);
    const opt = (side, kind, label) => `<button type="button" data-po="${side}:${kind}"${tradable() ? '' : ' disabled'}><span class="${side === 'buy' ? 'up' : 'dn'}">${label}</span> ${l} lots</button>`;
    plusMenu.innerHTML = `<h6>${px(m, plusPx)}</h6>`
      + (above ? opt('sell', 'limit', 'Sell limit') + opt('buy', 'stop', 'Buy stop') : opt('buy', 'limit', 'Buy limit') + opt('sell', 'stop', 'Sell stop'))
      + '<button type="button" data-po="alert">Set price alert</button>';
    const r = $('tChart').getBoundingClientRect(), tr = plus.getBoundingClientRect();
    plusMenu.hidden = false;
    plusMenu.style.transform = `translate(${Math.round(tr.left - r.left - 170)}px,${Math.round(Math.min(tr.top - r.top, r.height - plusMenu.offsetHeight - 6))}px)`;
  });
  document.addEventListener('mousedown', (e) => { if (!plusMenu.hidden && !e.target.closest('.t-plus-menu,.t-plus')) { plusMenu.hidden = true; plus.hidden = true; } });
  plusMenu.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-po]');
    if (!b) return;
    plusMenu.hidden = true; plus.hidden = true;
    if (b.dataset.po === 'alert') return addAlert(S.sym, plusPx);
    const [side, kind] = b.dataset.po.split(':'), m = S.m;
    await placeOrder({ side, kind, price: plusPx, qty: +(PREF.lots * m.contract).toFixed(8) }, `${kind === 'limit' ? 'Limit' : 'Stop'} order placed`);
  });
  async function placeOrder(o, title) {
    if (!S.token) return openModal('tReady');
    const m = S.m;
    try {
      const st = await api('/api/terminal/orders', { method: 'POST', body: JSON.stringify({ account_id: S.accId, symbol: m.sym, reduce_only: false, max_slippage_pct: S.slip, ...o }) });
      applyState(st);
      toast(title, `${o.side === 'buy' ? 'Buy' : 'Sell'} ${lotsOf(m, o.qty)} ${m.sym}${o.price ? ' at ' + px(m, o.price) : ''}`);
      beep('fill');
    } catch (er) { toast('Order rejected', er.message, true); beep('bad'); }
  }
  /* Drag a pending order's dot: the line follows, the new price goes out on release. */
  function dragOrder(id) {
    const o = S.st && S.st.orders.find((x) => x.id === id);
    if (!o) return;
    const m = BY[o.symbol], r = $('tChart').getBoundingClientRect();
    S.ordDrag = { id, v: o.price };
    const mv = (ev) => {
      const v = priceAtY(ev.clientY - r.top);
      if (v != null) { S.ordDrag.v = roundPx(m, v); drawLines(); }
    };
    const up = async () => {
      window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up);
      const price = S.ordDrag.v;
      if (price === o.price) { S.ordDrag = null; return drawLines(); }
      try {
        const st = await api(`/api/terminal/orders/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: S.accId, price }) });
        S.ordDrag = null;
        applyState(st);
        toast('Order moved', `${o.symbol} at ${px(m, price)}`);
      } catch (er) { S.ordDrag = null; drawLines(); toast('Order not moved', er.message, true); }
    };
    window.addEventListener('mousemove', mv);
    window.addEventListener('mouseup', up);
  }

  /* ---------- price alerts: kept in this browser, checked while the tab is open ---------- */
  S.alerts = lsJson('pf_term_alerts', []);
  const saveAlerts = () => ls.set('pf_term_alerts', JSON.stringify(S.alerts));
  function addAlert(sym, price) {
    const m = BY[sym], mk = S.mids[m.hl];
    if (!mk) return toast('No live price yet', 'Try again in a moment.', true);
    S.alerts.push({ id: Date.now(), sym, price, above: price > mk });
    saveAlerts(); drawLines();
    toast('Price alert set', `${sym} at ${px(m, price)}`);
  }
  function checkAlerts() {
    if (!S.alerts.length) return;
    const hit = S.alerts.filter((a) => { const v = S.mids[BY[a.sym] ? BY[a.sym].hl : '']; return v && (a.above ? v >= a.price : v <= a.price); });
    if (!hit.length) return;
    S.alerts = S.alerts.filter((a) => !hit.includes(a));
    saveAlerts(); drawLines();
    hit.forEach((a) => signal('alert', `${a.sym} reached ${px(BY[a.sym], a.price)}`, 'Price alert'));
  }
  setInterval(checkAlerts, 1000);

  /* ---------- one-click trading and keyboard ---------- */
  const oc = document.createElement('div');
  oc.className = 't-oc'; oc.hidden = true;
  oc.innerHTML = `<button type="button" class="s" data-oc="sell"><small>Sell</small><b data-oc-b>—</b></button>
    <input data-oc-l inputmode="decimal" aria-label="Lots" title="Lots">
    <button type="button" class="b" data-oc="buy"><small>Buy</small><b data-oc-a>—</b></button>`;
  $('tChart').appendChild(oc);
  ['mousedown', 'mouseup', 'click', 'wheel'].forEach((t) => oc.addEventListener(t, (e) => e.stopPropagation()));
  const ocLots = oc.querySelector('[data-oc-l]');
  ocLots.addEventListener('change', () => {
    const v = Math.max(0.01, Math.round((numOf(ocLots.value) || 0.01) * 100) / 100);
    PREF.lots = v; savePref(); ocLots.value = v.toFixed(2);
  });
  function renderOc() {
    oc.hidden = !PREF.quick || cTab !== 'chart';
    if (oc.hidden || !S.m) return;
    if (document.activeElement !== ocLots) ocLots.value = (+PREF.lots).toFixed(2);
    const [bid, ask] = S.top || [];
    oc.querySelector('[data-oc-b]').textContent = bid ? px(S.m, bid) : '—';
    oc.querySelector('[data-oc-a]').textContent = ask ? px(S.m, ask) : '—';
  }
  setInterval(() => { if (!document.hidden) renderOc(); }, 200);
  const quick = (side) => {
    if (!tradable()) return S.token ? toast('Trading is off on this account', '', true) : openModal('tReady');
    return placeOrder({ side, kind: 'market', qty: +(PREF.lots * S.m.contract).toFixed(8) }, 'Order filled');
  };
  oc.addEventListener('click', (e) => { const b = e.target.closest('[data-oc]'); if (b) quick(b.dataset.oc); });
  document.addEventListener('keydown', (e) => {
    if (!PREF.keys || e.repeat || e.metaKey || e.ctrlKey || e.altKey || modalOpen || e.target.closest('input,textarea,select')) return;
    const k = e.key.toLowerCase(), p = S.st && S.st.positions.find((x) => x.symbol === S.sym);
    if (k === 'b') quick('buy');
    else if (k === 's') quick('sell');
    else if (k === 'x' && p) closePos(p.id, false, null);
    else if (k === 'r' && p) reversePos(p.id, null);
    else return;
    e.preventDefault();
  });

  /* ---------- settings menu in the footer ---------- */
  dropdown($('tFTools'), $('tToolsMenu'), (mn) => {
    const ck = (k, label, hint) => `<label class="t-tool"><input type="checkbox" data-pref="${k}"${PREF[k] ? ' checked' : ''}><span><b>${label}</b><small>${hint}</small></span></label>`;
    mn.innerHTML = ck('quick', 'Quick Trade buttons', 'Buy and Sell on the chart. One click sends a market order, with no confirmation.')
      + ck('keys', 'Keyboard trading', 'B buy, S sell, X close, R reverse. Orders go out at once.')
      + ck('sounds', 'Sounds', 'Fills, TP and SL, alerts and loss-limit warnings.')
      + ck('notify', 'Browser notifications', 'The same events while this tab is in the background.')
      + `<h6>Price alerts</h6>${S.alerts.map((a) => `<div class="t-al"><span>${esc(a.sym)} ${px(BY[a.sym], a.price)}</span><button type="button" data-al="${a.id}" aria-label="Remove alert">✕</button></div>`).join('') || '<p class="t-hint" style="padding:2px 12px 8px">Hover the chart and press + to add one.</p>'}`;
  });
  $('tToolsMenu').addEventListener('click', (e) => {
    e.stopPropagation();
    const b = e.target.closest('[data-al]');
    if (b) {
      S.alerts = S.alerts.filter((a) => a.id !== +b.dataset.al);
      saveAlerts(); drawLines();
      b.closest('.t-al').remove();
    }
  });
  $('tToolsMenu').addEventListener('change', async (e) => {
    const c = e.target.closest('[data-pref]');
    if (!c) return;
    const k = c.dataset.pref;
    if (k === 'notify' && c.checked && 'Notification' in window && Notification.permission !== 'granted') {
      const got = await Notification.requestPermission().catch(() => 'denied');
      if (got !== 'granted') { c.checked = false; toast('Notifications are blocked', 'Allow them for this site in your browser settings.', true); return; }
    }
    PREF[k] = c.checked; savePref(); renderOc();
    if (k === 'sounds' && c.checked) beep('fill');
  });

  /* ---------- account report and journal ---------- */
  let report = null;
  async function loadReport() {
    const acc = S.accId;
    try {
      const d = await api('/api/terminal/history?account_id=' + acc);
      if (acc !== S.accId) return;
      report = { acc, rows: d.history, start: d.initial_balance };
    } catch (er) { report = { acc, rows: [], start: 0, err: er.message }; }
    if (S.ptab === 'report') renderTables();
  }
  function reportHtml() {
    if (!report || report.acc !== S.accId) { loadReport(); return '<div class="t-empty">Loading…</div>'; }
    if (report.err) return `<div class="t-empty">${esc(report.err)}</div>`;
    const rows = report.rows;
    if (!rows.length) return '<div class="t-empty">The report starts with your first closed trade.</div>';
    const wins = rows.filter((r) => r.pnl > 0), losses = rows.filter((r) => r.pnl < 0);
    const gw = wins.reduce((s, r) => s + r.pnl, 0), gl = -losses.reduce((s, r) => s + r.pnl, 0);
    const net = gw - gl;
    // Drawdown on closed results, oldest trade first, from the starting balance.
    let bal = report.start || 0, peak = bal, dd = 0;
    [...rows].reverse().forEach((r) => { bal += r.pnl; peak = Math.max(peak, bal); dd = Math.max(dd, peak - bal); });
    const sym = {};
    rows.forEach((r) => { const x = sym[r.symbol] = sym[r.symbol] || { n: 0, pnl: 0 }; x.n++; x.pnl += r.pnl; });
    const kv = (l, v, c) => `<div class="t-rep-k"><small>${l}</small><b class="num ${c || ''}">${v}</b></div>`;
    return `<div class="t-rep"><div class="t-rep-head"><b>Account report</b><button type="button" class="t-link acc" data-rep-csv>Export CSV</button></div>
      <div class="t-rep-grid">
        ${kv('Net result', sUsd(net), cls(net))}${kv('Trades', rows.length)}${kv('Win rate', (wins.length / rows.length * 100).toFixed(1) + '%')}
        ${kv('Profit factor', gl ? (gw / gl).toFixed(2) : '—')}${kv('Average win', wins.length ? usd(gw / wins.length) : '—')}${kv('Average loss', losses.length ? usd(gl / losses.length) : '—')}
        ${kv('Best trade', sUsd(Math.max(...rows.map((r) => r.pnl))))}${kv('Worst trade', sUsd(Math.min(...rows.map((r) => r.pnl))))}${kv('Max drawdown', usd(dd))}
      </div>
      <div class="t-rep-syms">${Object.entries(sym).sort((a, b) => b[1].n - a[1].n).map(([s, x]) => `<span>${esc(s)} <em>${x.n}</em> <b class="num ${cls(x.pnl)}">${sUsd(x.pnl)}</b></span>`).join('')}</div>
      <table><thead><tr><th>Closed</th><th>Market</th><th>Side</th><th class="num">PnL</th><th>Journal note</th></tr></thead><tbody>
      ${rows.slice(0, 200).map((r) => `<tr><td>${tm(r.closed_at)}</td><td>${esc(r.symbol)}</td><td class="${r.side === 'buy' ? 'up' : 'dn'}">${r.side === 'buy' ? 'Long' : 'Short'}</td><td class="num ${cls(r.pnl)}">${sUsd(r.pnl)}</td><td class="t-note"><input data-note="${r.id}" maxlength="500" value="${esc(r.note || '')}" placeholder="Add a note"></td></tr>`).join('')}
      </tbody></table></div>`;
  }
  $('tTable').addEventListener('change', async (e) => {
    const n = e.target.closest('[data-note]');
    if (!n) return;
    try {
      await api(`/api/terminal/trades/${n.dataset.note}/note`, { method: 'PUT', body: JSON.stringify({ account_id: S.accId, note: n.value }) });
      const r = report && report.rows.find((x) => x.id === +n.dataset.note);
      if (r) r.note = n.value.trim() || null;
      n.classList.add('saved');
      setTimeout(() => n.classList.remove('saved'), 900);
    } catch (er) { toast('Note not saved', er.message, true); }
  });
  $('tTable').addEventListener('click', (e) => {
    if (!e.target.closest('[data-rep-csv]') || !report) return;
    const q = (v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
    const lines = [['Closed', 'Opened', 'Market', 'Side', 'Lots', 'Entry', 'Exit', 'Fee', 'PnL', 'Reason', 'Note'].join(',')]
      .concat(report.rows.map((r) => [r.closed_at, r.opened_at, r.symbol, r.side, r.lots, r.entry, r.close, r.fee, r.pnl, r.reason, r.note].map(q).join(',')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = `account-${(S.accounts.find((x) => x.id === S.accId) || {}).login || S.accId}-report.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  });

  /* ---------- sign-in with MT5 credentials ---------- */
  function deviceId() {
    let d = ls.get('pf_term_dev', '');
    if (!d) {
      d = [...crypto.getRandomValues(new Uint8Array(12))].map((x) => x.toString(16).padStart(2, '0')).join('');
      ls.set('pf_term_dev', d);
    }
    return d;
  }
  /* Account bar in the chart toolbar (as in cTrader): shows the account in view and opens
     a panel over the chart with the connected accounts and the MT5 connect form. */
  const panel = $('tLogin');
  let formOpen = false;
  function renderDock() {
    const off = !S.token;
    const list = $('tApList');
    list.hidden = off;
    list.innerHTML = S.accounts.map((a) => {
      const l = S.logins.find((x) => x.id === a.id);
      return `<div class="t-ap-acc${a.id === S.accId ? ' on' : ''}"><button type="button" data-acc="${a.id}"><i class="dot${a.tradable ? '' : ' off'}"></i><b>${esc(a.login)}</b><small>${esc(a.server || '')} · ${a.status === 'failed' ? 'Breached' : phaseLbl(a)}</small><em>${usd(a.balance)}</em></button>${!S.imp && l ? `<button type="button" class="x" data-out="${a.id}" title="Disconnect" aria-label="Disconnect ${esc(a.login)}">×</button>` : ''}</div>`;
    }).join('');
    $('tApAdd').hidden = off || formOpen || S.imp;
    $('tLoginMt5').hidden = !(off || formOpen);
    $('tLoginH').textContent = off ? 'Connect to account' : 'Add account';
  }
  function openPanel(show) {
    panel.hidden = !show;
    $('tAccBtn').classList.toggle('open', show);
    if (show) renderDock();
  }
  function focusForm() {
    const f = $('tLoginMt5');
    f.reset();
    f.keep.checked = true;
    f.querySelector('.t-err').hidden = true;
    f.login.value = ls.get('pf_term_prefill', '') || '';
    ls.del('pf_term_prefill');
    f.server.value = ls.get('pf_term_prefill_srv', '') || ls.get('pf_term_srv_last', '') || '';
    ls.del('pf_term_prefill_srv');
    (f.login.value ? f.password : f.login).focus();
  }
  function openLogin(adding) {
    formOpen = adding === true;
    if (window.matchMedia('(max-width:760px)').matches) setMPane('chart');
    const tab = document.querySelector('[data-ctab="chart"]');
    if (tab && !tab.classList.contains('on')) tab.click();
    closeModal();
    openPanel(true);
    window.scrollTo({ top: 0 });
    if (!S.token || formOpen) focusForm();
  }
  $('tAccBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    if (!panel.hidden) return openPanel(false);
    openLogin(false);
  });
  $('tReadySignIn').onclick = openLogin;
  $('tApAdd').onclick = () => { formOpen = true; renderDock(); focusForm(); };
  panel.addEventListener('click', (e) => {
    e.stopPropagation();
    const out = e.target.closest('[data-out]');
    if (out) {
      const l = S.logins.find((x) => x.id === +out.dataset.out);
      if (l) signOut(l.token);
      renderDock();
      return;
    }
    const b = e.target.closest('[data-acc]');
    if (b) { selectAccount(+b.dataset.acc); openPanel(false); }
    if (!$('tSrvList').hidden && !e.target.closest('.t-ap-srv')) $('tSrvList').hidden = true;
  });
  document.addEventListener('click', (e) => { if (!panel.hidden && !e.target.closest('#tLogin,#tAccBtn,#tReadySignIn,#tMMLogin,[data-signin],[data-add-acc]')) { formOpen = false; openPanel(false); } });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) { formOpen = false; openPanel(false); } });

  /* Servers as in MT5: type a name once, it stays on the list for next time. */
  const srvs = () => lsJson('pf_term_servers', []);
  function saveServer(name) {
    const list = srvs().filter((x) => x.toLowerCase() !== name.toLowerCase());
    list.unshift(name);
    ls.set('pf_term_servers', JSON.stringify(list.slice(0, 20)));
    ls.set('pf_term_srv_last', name);
  }
  function renderServers() {
    const inp = $('tSrvIn'), q = inp.value.trim(), box = $('tSrvList'), all = srvs();
    const shown = all.filter((x) => !q || x.toLowerCase().includes(q.toLowerCase()) || x === q);
    const isNew = q && !all.some((x) => x.toLowerCase() === q.toLowerCase());
    box.innerHTML = shown.map((x) => `<div class="t-ap-sv"><button type="button" data-srv="${esc(x)}">${esc(x)}</button><button type="button" class="x" data-srvx="${esc(x)}" aria-label="Remove ${esc(x)}">×</button></div>`).join('')
      + (isNew ? `<button type="button" class="t-ap-svadd" data-srvadd="${esc(q)}">+ Add server “${esc(q)}”</button>` : '')
      + (!shown.length && !isNew ? '<p>Type your server name to add it. It stays on this list.</p>' : '');
    box.hidden = false;
  }
  $('tSrvIn').addEventListener('input', renderServers);
  $('tSrvIn').addEventListener('focus', renderServers);
  $('tSrvBtn').addEventListener('click', () => { if ($('tSrvList').hidden) renderServers(); else $('tSrvList').hidden = true; });
  $('tSrvList').addEventListener('click', (e) => {
    const pick = e.target.closest('[data-srv]'), add = e.target.closest('[data-srvadd]'), del = e.target.closest('[data-srvx]');
    if (del) {
      ls.set('pf_term_servers', JSON.stringify(srvs().filter((x) => x !== del.dataset.srvx)));
      return renderServers();
    }
    if (add) saveServer(add.dataset.srvadd);
    if (pick || add) {
      $('tSrvIn').value = (pick ? pick.dataset.srv : add.dataset.srvadd);
      $('tSrvList').hidden = true;
      $('tLoginMt5').password.focus();
    }
  });
  $('tLoginMt5').onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, err = f.querySelector('.t-err'), btn = f.querySelector('button[type=submit]');
    err.hidden = true; btn.disabled = true; btn.textContent = 'Connecting…';
    try {
      const r = await fetch('/api/terminal/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ login: f.login.value.trim(), password: f.password.value, server: f.server.value.trim(), device: deviceId() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Connection failed');
      S.logins = S.logins.filter((l) => l.id !== d.account.id).concat({ token: d.token, id: d.account.id, login: d.account.login, temp: !f.keep.checked });
      saveLogins();
      S.accId = d.account.id; S.token = d.token; S.st = null;
      ls.set('pf_term_acc', String(d.account.id));
      saveServer(f.server.value.trim());
      formOpen = false;
      f.password.value = '';
      openPanel(false);
      toast('Connected', `MT5 account #${d.account.login}`);
      await loadAccounts();
    } catch (er) { err.textContent = er.message; err.hidden = false; } finally { btn.disabled = false; btn.textContent = 'Connect'; }
  };

  /* ---------- phone: panel tabs ---------- */
  function setMPane(p) {
    root.dataset.mpane = p;
    tabOn($$('#tMTabs [data-pane]'), (b) => b.dataset.pane === p);
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

  /* ---------- theme, sharing ---------- */
  $('tTheme').onclick = () => {
    const light = document.documentElement.dataset.theme !== 'light';
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    ls.set('pf_term_theme', light ? 'light' : 'dark');
    if (chart) { chart.setStyles(chartStyles()); drawLines(); drawTrades(true); }
  };
  $('tShare').onclick = () => {
    const u = location.origin + '/trade/' + S.sym;
    // writeText returns a promise — a refusal (no permission, http) arrives asynchronously.
    const cb = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(u) : Promise.reject();
    cb.then(() => toast('Link copied', u), () => toast('Copy this link', u));
  };

  /* ---------- "Free Trade Case" prompt for guests ---------- */
  function casePrompt() {
    if (S.token || ls.get('pf_token', null) || ls.get('pf_case_prompt', '0') === '1') return;
    const need = 10 + Math.floor(Math.random() * 30);   // 10–40 s viewing the page
    let seen = 0;
    const t = setInterval(() => {
      if (document.visibilityState !== 'visible' || modalOpen || S.token) return;
      if (++seen < need) return;
      clearInterval(t);
      ls.set('pf_case_prompt', '1');
      openModal('tCase');
    }, 1000);
  }

  /* ---------- chart right-click (as in TradingView) ---------- */
  // Order at the cursor price (limit or stop — depending on the market side), view,
  // copy price, trades and drawings on the chart, indicators.
  let prawyNaRysunku = 0, ctxPx = null;
  /* ---------- measure: Shift + left click ----------
     Shift + press starts the box at the cursor; drag and release, or move and click
     again, to finish. It stays on the chart until the next click or Esc. */
  // Pip as on MT5 / cTrader: 0.0001 FX (0.01 JPY), 0.1 gold, 0.01 oil, 1 point on indices and BTC.
  const pipOf = (m) => (m.cls === 'energy' ? Math.pow(10, -m.px_dec) : Math.pow(10, -Math.max(0, m.px_dec - 1)));
  const durTxt = (min) => {
    const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), mm = Math.round(min % 60);
    return [d && `${d}d`, h && `${h}h`, (mm || (!d && !h)) && `${mm}m`].filter(Boolean).join(' ');
  };
  let meas = null;
  function measLabel(p0, p1) {
    const m = S.m, d = p1.value - p0.value, up = d >= 0, sg = up ? '+' : '-';
    const pct = p0.value ? (d / p0.value) * 100 : 0;
    const perLot = m.quote_inverse ? (p1.value ? (d * m.contract) / p1.value : 0) : d * m.contract;
    const bars = Math.abs((p1.dataIndex || 0) - (p0.dataIndex || 0));
    const col = css(up ? '--up' : '--dn');
    return {
      color: col, fill: col + '26',
      lines: [
        `${sg}${px(m, Math.abs(d))} (${sg}${fx(Math.abs(pct), 2)}%) · ${sg}${fx(Math.abs(d) / pipOf(m), 1)} pips`,
        `${sUsd(perLot)} per lot · ${bars} ${bars === 1 ? 'bar' : 'bars'}, ${durTxt((bars * F.MS[S.interval]) / 60000)}`,
      ],
    };
  }
  function measPt(ev, inPane) {
    const r = $('tChart').getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    try {
      const pane = chart.getSize('candle_pane');
      if (inPane && pane && (y < pane.top || y > pane.top + pane.height || x > pane.left + pane.width)) return null;
      const pt = chart.convertFromPixel({ x, y }, { paneId: 'candle_pane', absolute: true });
      if (!pt || !ok(pt.value) || !Number.isFinite(pt.dataIndex)) return null;
      const out = { dataIndex: pt.dataIndex, value: pt.value };
      if (ok(pt.timestamp)) out.timestamp = pt.timestamp;
      return out;
    } catch (e) { return null; }
  }
  function clearMeasure() { if (meas && chart) chart.removeOverlay({ id: meas.id }); meas = null; }
  $('tChart').addEventListener('mousedown', (ev) => {
    if (!chart || ev.button !== 0) return;
    if (meas && meas.live) { ev.stopPropagation(); ev.preventDefault(); meas.live = false; return; }
    if (!ev.shiftKey) { clearMeasure(); return; }
    const pt = measPt(ev, true);
    if (!pt) return;
    // Capture phase: the chart must not start panning under the measure.
    ev.stopPropagation(); ev.preventDefault();
    clearMeasure();
    const id = chart.createOverlay({ name: 'ptfMeasure', groupId: 'measure', lock: true, points: [pt, pt], extendData: measLabel(pt, pt) });
    if (id) meas = { id, p0: pt, live: true, x0: ev.clientX, y0: ev.clientY };
  }, true);
  window.addEventListener('mousemove', (ev) => {
    if (!meas || !meas.live) return;
    const pt = measPt(ev, false);
    if (pt) chart.overrideOverlay({ id: meas.id, points: [meas.p0, pt], extendData: measLabel(meas.p0, pt) });
  });
  window.addEventListener('mouseup', (ev) => {
    if (meas && meas.live && Math.hypot(ev.clientX - meas.x0, ev.clientY - meas.y0) > 4) meas.live = false;
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') clearMeasure(); });

  const ctxMenu = document.createElement('div');
  ctxMenu.className = 't-menu t-ctx';
  ctxMenu.hidden = true;
  ctxMenu.setAttribute('role', 'menu');
  document.body.appendChild(ctxMenu);
  function resetView() { if (!chart) return; chart.setBarSpace(8); chart.scrollToRealTime(); }
  $('tChart').addEventListener('contextmenu', (ev) => {
    if (!chart) return;
    ev.preventDefault();
    const r = $('tChart').getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    setTimeout(() => {
      if (Date.now() - prawyNaRysunku < 400) return;      // right click removed a drawing — no menu
      let cena = null;
      try {
        const pane = chart.getSize('candle_pane');
        if (!pane || y <= pane.top + pane.height) {
          const pt = chart.convertFromPixel({ x, y }, { paneId: 'candle_pane', absolute: true });
          if (pt && ok(pt.value) && pt.value > 0) cena = pt.value;
        }
      } catch (e) {}
      ctxPx = cena;
      openCtx(ev.clientX, ev.clientY);
    }, 0);
  });
  function openCtx(cx, cy) {
    const m = S.m, mark = markPx(), it = [];
    const btn = (a, t, extra) => `<button type="button" role="menuitem" data-ctx="${a}">${t}${extra || ''}</button>`;
    if (ctxPx) {
      const ps = px(m, ctxPx), pod = !mark || ctxPx < mark;
      it.push(btn('buy', `${pod ? 'Buy Limit' : 'Buy Stop'} at <b class="num">${ps}</b>`), btn('sell', `${pod ? 'Sell Stop' : 'Sell Limit'} at <b class="num">${ps}</b>`), btn('alert', `Price alert at <b class="num">${ps}</b>`), '<hr>');
    }
    it.push(btn('reset', 'Reset chart view', '<kbd>⌥ R</kbd>'));
    if (ctxPx) it.push(btn('copy', `Copy price <span class="num">${px(m, ctxPx)}</span>`));
    it.push('<hr>', btn('trades', 'Show trades on chart', S.showTrades ? '<span class="chk">✓</span>' : ''),
      btn('hidedraw', 'Hide drawings', $('tHideDraw').classList.contains('on') ? '<span class="chk">✓</span>' : ''));
    if (drawn.length) it.push(btn('cleardraw', `Remove ${drawn.length} drawing${drawn.length > 1 ? 's' : ''}`));
    if (inds.length) it.push(btn('rmind', `Remove ${inds.length} indicator${inds.length > 1 ? 's' : ''}`));
    closeMenus();
    ctxMenu.innerHTML = it.join('');
    ctxMenu.hidden = false;
    Object.assign(ctxMenu.style, { position: 'fixed', left: '0px', top: '0px', right: 'auto', bottom: 'auto' });
    ctxMenu.style.left = Math.max(8, Math.min(cx, innerWidth - ctxMenu.offsetWidth - 8)) + 'px';
    ctxMenu.style.top = Math.max(8, Math.min(cy, innerHeight - ctxMenu.offsetHeight - 8)) + 'px';
  }
  ctxMenu.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ctx]');
    if (!b) return;
    e.stopPropagation();
    ctxMenu.hidden = true;
    const a = b.dataset.ctx, m = S.m;
    if (a === 'alert') { ctxMenu.hidden = true; return addAlert(S.sym, roundPx(m, ctxPx)); }
    if (a === 'buy' || a === 'sell') {
      const pod = !markPx() || ctxPx < markPx();
      setSide(a);
      setKind((a === 'buy') === pod ? 'limit' : 'stop');
      $('tPx').value = ctxPx.toFixed(m.px_dec);
      updateTicket();
      if (narrow.matches) setMPane('ticket');
      $('tSize').focus();
    } else if (a === 'reset') resetView();
    else if (a === 'copy') {
      const v = ctxPx.toFixed(m.px_dec);
      const cb = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(v) : Promise.reject();
      cb.then(() => toast('Price copied', v), () => toast('Copy this price', v));
    } else if (a === 'trades') $('tShowTrades').click();
    else if (a === 'hidedraw') $('tHideDraw').click();
    else if (a === 'cleardraw') $('tClearDraw').click();
    else if (a === 'rmind') {
      inds.forEach((n) => chart.removeIndicator({ name: n }));
      inds = [];
      ls.set('pf_term_inds', '[]');
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.altKey && e.code === 'KeyR' && !e.target.closest('input,textarea,select')) { e.preventDefault(); resetView(); }
  });

  /* ---------- menu (burger) — as on the PTF site, when links don't fit the header ---------- */
  function setMenu(open) {
    $('tMMenu').hidden = !open;
    $('tBurger').setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  $('tBurger').addEventListener('click', (e) => { e.stopPropagation(); closeMenus(); setMenu($('tMMenu').hidden); });
  $('tMMenu').addEventListener('click', (e) => {
    if (e.target.closest('#tMMLogin')) { setMenu(false); openLogin(); return; }
    if (e.target.closest('a')) setMenu(false);
  });
  document.addEventListener('click', (e) => { if (!$('tMMenu').hidden && !e.target.closest('#tMMenu')) setMenu(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('tMMenu').hidden) setMenu(false); });
  window.addEventListener('scroll', () => { if (!$('tMMenu').hidden) setMenu(false); }, { passive: true });

  // Market bar scrolling sideways: a soft edge only when there is more beyond it.
  const mktBar = document.querySelector('.t-mkt');
  const mktMore = () => mktBar.classList.toggle('more', mktBar.scrollLeft + mktBar.clientWidth < mktBar.scrollWidth - 2);
  mktBar.addEventListener('scroll', mktMore, { passive: true });
  new ResizeObserver(mktMore).observe(mktBar);

  /* ---------- start ---------- */
  function boot() {
    renderMarketHead();
    setSide('buy');
    $$('[data-i]', $('tInts')).forEach((b) => b.classList.toggle('on', b.dataset.i === S.interval));
    buildDrawRail();
    initChart();
    restream();
    renderStrip();
    setPTab('positions');
    setMPane('chart');
    restoreLogins();
    if (S.imp) toast('Admin view', 'Read-only view of this account. Trading is off.');
    loadAccounts();
    fetch('/api/terminal/markets').then((r) => r.json()).then((d) => { S.weekend = !!d.weekend; S.fee = d.fee_pct; updateTicket(); renderInfo(); }).catch(() => {});
    if (root.dataset.missing) toast(`${root.dataset.missing} trades in MT5`, 'The web terminal has crypto, metals, energy, indices and EUR/GBP/JPY for now.');
    // "See the full list" from the home page: /trade?list=all opens the list of all markets.
    if (new URLSearchParams(location.search).has('list')) {
      history.replaceState(null, '', location.pathname);
      openSearch();
    }
    casePrompt();
  }
  if (window.klinecharts) boot(); else window.addEventListener('load', boot);
})();
