/* Landing page: a small live terminal. Candles, order book and trades come straight from
   the exchange feed, like in /trade. Scripts load only when the section nears the screen,
   and the stream stops while it is out of view. */
(function () {
  'use strict';
  /* Stats band: spreads measured from the live book of each market, while on screen. */
  const tiles = [...document.querySelectorAll('[data-spread]')];
  if (tiles.length) {
    let t = 0;
    const read = () => tiles.forEach((el) => fetch('https://api.hyperliquid.xyz/info', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'l2Book', coin: el.dataset.spread }) })
      .then((r) => r.json())
      .then((d) => {
        const bid = +d.levels[0][0].px, ask = +d.levels[1][0].px;
        const v = ask - bid;
        el.textContent = (el.dataset.pre || '') + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: v < 0.1 ? 3 : 2 }) + (el.dataset.post || '');
      }).catch(() => {}));
    new IntersectionObserver((en) => {
      clearInterval(t);
      if (en[0].isIntersecting) { read(); t = setInterval(read, 15000); }
    }).observe(tiles[0]);
  }

  const box = document.getElementById('lpTerm');
  if (!box) return;
  const $ = (s) => box.querySelector(s);
  const v = box.dataset.v || '';
  const SYMS = ['BTCUSD', 'ETHUSD', 'XAUUSD', 'SOLUSD'];
  const PERIODS = { '1m': { type: 'minute', span: 1 }, '5m': { type: 'minute', span: 5 }, '15m': { type: 'minute', span: 15 }, '1h': { type: 'hour', span: 1 } };
  const ROWS = 7;
  const UP = '#2ebd85', DN = '#e0555f', LINE = '#26282d', MUTED = '#8b9099', TXT = '#e8eaee', PANEL = '#17181b';
  let F, chart, m, list = [], interval = '15m', strm = null, barCb = null, seq = 0, tab = 'book';
  let book = [[], []], tape = [], drawT = 0, lastMark = null, seen = false;

  const load = (src) => new Promise((ok, no) => {
    const s = document.createElement('script');
    s.src = src + '?v=' + v; s.onload = ok; s.onerror = no;
    document.head.appendChild(s);
  });
  const px = (x) => (x == null ? '—' : (+x).toLocaleString('en-US', { minimumFractionDigits: m.px_dec, maximumFractionDigits: m.px_dec }));
  const sz = (x) => (+x).toLocaleString('en-US', { maximumFractionDigits: x >= 100 ? 0 : 3 });

  function styles() {
    return {
      grid: { horizontal: { color: LINE }, vertical: { color: LINE } },
      candle: {
        bar: { upColor: UP, downColor: DN, noChangeColor: MUTED, upBorderColor: UP, downBorderColor: DN, noChangeBorderColor: MUTED, upWickColor: UP, downWickColor: DN, noChangeWickColor: MUTED },
        priceMark: { high: { show: false }, low: { show: false }, last: { upColor: UP, downColor: DN, noChangeColor: MUTED, line: { style: 'dashed', dashedValue: [1, 3], size: 1 }, text: { color: '#fff' } } },
        tooltip: { showRule: 'none' },
      },
      indicator: { tooltip: { showRule: 'none' }, bars: [{ upColor: UP + '70', downColor: DN + '70', noChangeColor: MUTED }] },
      xAxis: { axisLine: { color: LINE }, tickLine: { color: LINE }, tickText: { color: MUTED } },
      yAxis: { axisLine: { color: LINE }, tickLine: { color: LINE }, tickText: { color: MUTED } },
      separator: { color: LINE },
      crosshair: {
        horizontal: { line: { color: MUTED }, text: { backgroundColor: PANEL, borderColor: PANEL, color: TXT } },
        vertical: { line: { color: MUTED }, text: { backgroundColor: PANEL, borderColor: PANEL, color: TXT } },
      },
    };
  }

  function draw() {
    drawT = 0;
    const el = $('[data-side]');
    if (tab === 'book') {
      const a = book[1].slice(0, ROWS).reverse(), b = book[0].slice(0, ROWS);
      let t = 0;
      const askT = book[1].slice(0, ROWS).map((r) => (t += r[1])).reverse();
      t = 0;
      const bidT = b.map((r) => (t += r[1]));
      const max = Math.max(askT[0] || 0, bidT[bidT.length - 1] || 0) || 1;
      const row = (r, tot, c) => `<div class="lpt-row ${c}"><i style="width:${(tot / max * 100).toFixed(1)}%"></i><span>${px(r[0])}</span><span>${sz(r[1])}</span><span>${sz(tot)}</span></div>`;
      const sp = a.length && b.length ? a[a.length - 1][0] - b[0][0] : null;
      el.innerHTML = `<div class="lpt-head"><span>Price</span><span>Size</span><span>Total</span></div>`
        + a.map((r, i) => row(r, askT[i], 'dn')).join('')
        + `<div class="lpt-spread"><span>Spread</span><span>${sp == null ? '—' : px(sp)}</span><span>${sp == null ? '' : (sp / b[0][0] * 100).toFixed(4) + '%'}</span></div>`
        + b.map((r, i) => row(r, bidT[i], 'up')).join('');
    } else {
      el.innerHTML = `<div class="lpt-head"><span>Price</span><span>Size</span><span>Time</span></div>`
        + tape.slice(0, ROWS * 2 + 1).map((x) => `<div class="lpt-row ${x.side === 'buy' ? 'up' : 'dn'}"><span>${px(x.px)}</span><span>${sz(x.sz)}</span><span>${new Date(x.t).toLocaleTimeString('en-GB')}</span></div>`).join('');
    }
  }
  const later = () => { if (!drawT) drawT = setTimeout(draw, 250); };

  function stop() { if (strm) { strm.close(); strm = null; } }
  function start() {
    stop();
    strm = F.stream('hyperliquid', m, interval, {
      candle: (k) => { if (barCb) barCb(k); },
      book: (b, a) => { book = [b, a]; later(); },
      trades: (t) => { tape = t.concat(tape).slice(0, 40); if (tab === 'trades') later(); },
      top: () => {},
      ticker: (x) => {
        if (x.mark != null && x.mark !== lastMark) { lastMark = x.mark; $('[data-mark]').textContent = px(x.mark); }
        if (x.chg != null) {
          const c = $('[data-chg]');
          c.textContent = (x.chg >= 0 ? '+' : '') + x.chg.toFixed(2) + '%';
          c.className = x.chg >= 0 ? 'up' : 'dn';
        }
      },
      status: () => {},
    });
  }
  function pick(sym) {
    m = list.find((x) => x.sym === sym) || list[0];
    book = [[], []]; tape = []; lastMark = null;
    $('[data-mark]').textContent = '—';
    $('[data-chg]').textContent = '';
    $('[data-lev]').textContent = m.max_lev + 'x';
    $('[data-open]').href = '/trade/' + m.sym;
    box.querySelectorAll('[data-sym]').forEach((b) => b.classList.toggle('on', b.dataset.sym === m.sym));
    chart.setSymbol({ ticker: m.sym, pricePrecision: m.px_dec, volumePrecision: Math.min(m.qty_dec, 4) });
    start();
    draw();
  }

  async function boot() {
    try {
      await load('/static/lib/klinecharts.min.js');
      await load('/static/js/terminal-feed.js');
      F = window.PTFeed;
      const d = await (await fetch('/api/terminal/markets')).json();
      list = SYMS.map((s) => d.markets.find((x) => x.sym === s)).filter(Boolean);
      $('[data-syms]').innerHTML = list.map((x) => `<button type="button" data-sym="${x.sym}">${x.sym.replace('USD', '')}</button>`).join('');
      chart = klinecharts.init($('[data-chart]'), { styles: styles(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, locale: 'en-US' });
      // The page keeps scrolling over the chart; zoom stays on Ctrl/⌘ + wheel and on pinch.
      $('[data-chart]').addEventListener('wheel', (e) => { if (!e.ctrlKey && !e.metaKey) e.stopPropagation(); }, { capture: true, passive: true });
      // Same on touch: a vertical swipe scrolls the page, a sideways one pans the chart.
      const area = $('[data-chart]');
      let tx = 0, ty = 0, dir = null;
      area.addEventListener('touchstart', (e) => { dir = null; if (e.touches.length === 1) { tx = e.touches[0].clientX; ty = e.touches[0].clientY; } else dir = 'x'; }, { capture: true, passive: true });
      area.addEventListener('touchmove', (e) => {
        if (dir === null) {
          const dx = Math.abs(e.touches[0].clientX - tx), dy = Math.abs(e.touches[0].clientY - ty);
          if (dx + dy > 6) dir = dy > dx ? 'y' : 'x';
        }
        if (dir !== 'x') e.stopPropagation();
      }, { capture: true, passive: true });
      chart.setDataLoader({
        getBars: async ({ type, timestamp, callback }) => {
          const nr = ++seq;
          try {
            if (type === 'init') {
              const h = await F.history('hyperliquid', m, interval, Date.now(), 200);
              if (nr === seq) callback(h, { forward: h.length > 10, backward: false });
            } else if (type === 'forward') {
              const h = await F.history('hyperliquid', m, interval, timestamp - 1, 200);
              if (nr === seq) callback(h.filter((k) => k.timestamp < timestamp), { forward: h.length > 10, backward: false });
            } else callback([], { forward: false, backward: false });
          } catch (e) { if (nr === seq) callback([], { forward: false, backward: false }); }
        },
        subscribeBar: ({ callback }) => { barCb = callback; },
        unsubscribeBar: () => { barCb = null; },
      });
      chart.createIndicator({ name: 'VOL', calcParams: [], paneId: 'pane_VOL' });
      chart.setPaneOptions({ id: 'pane_VOL', height: 60 });
      chart.setPeriod(PERIODS[interval]);
      new ResizeObserver(() => chart.resize()).observe($('[data-chart]'));
      box.addEventListener('click', (e) => {
        const s = e.target.closest('[data-sym]');
        if (s) return pick(s.dataset.sym);
        const i = e.target.closest('[data-int]');
        if (i) {
          interval = i.dataset.int;
          box.querySelectorAll('[data-int]').forEach((b) => b.classList.toggle('on', b === i));
          chart.setPeriod(PERIODS[interval]);
          if (strm) strm.interval(interval);
          return;
        }
        const t = e.target.closest('[data-tab]');
        if (t) {
          tab = t.dataset.tab;
          box.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('on', b === t));
          draw();
        }
      });
      pick(SYMS[0]);
      box.classList.add('live');
      new IntersectionObserver((en) => { if (en[0].isIntersecting) { if (!strm) start(); } else stop(); }, { rootMargin: '200px' }).observe(box);
    } catch (e) {
      box.classList.add('off');
    }
  }
  new IntersectionObserver((en, o) => {
    if (en[0].isIntersecting && !seen) { seen = true; o.disconnect(); boot(); }
  }, { rootMargin: '400px' }).observe(box);
})();
