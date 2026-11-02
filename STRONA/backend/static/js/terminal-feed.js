/* Terminal PTF — dane rynkowe prosto z giełd (przeglądarka ↔ WebSocket).
   Serwer Vercela nie trzyma połączeń, więc wykres, księga i transakcje płyną
   bezpośrednio z Hyperliquid, Binance albo Bybit. Rozliczenie zleceń i tak
   robi serwer po cenach Hyperliquid (app/markets.py).

   Binance od 2026 dzieli strumienie: księga na /public, reszta na /market —
   stary adres /stream oddaje już tylko księgę. */
(function () {
  'use strict';

  const HL_WS = 'wss://api.hyperliquid.xyz/ws';
  const HL_INFO = 'https://api.hyperliquid.xyz/info';
  const BN_REST = 'https://fapi.binance.com';
  const BN_PUB = 'wss://fstream.binance.com/public/stream?streams=';
  const BN_MKT = 'wss://fstream.binance.com/market/stream?streams=';
  const BY_REST = 'https://api.bybit.com';
  const BY_WS = 'wss://stream.bybit.com/v5/public/linear';

  const MS = { '1m': 6e4, '3m': 18e4, '5m': 3e5, '15m': 9e5, '30m': 18e5, '1h': 36e5, '4h': 144e5, '1d': 864e5, '1w': 6048e5 };
  const BY_INT = { '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30', '1h': '60', '4h': '240', '1d': 'D', '1w': 'W' };

  const VENUES = {
    hyperliquid: { key: 'hyperliquid', label: 'Hyperliquid', sym: (m) => m.hl },
    binance: { key: 'binance', label: 'Binance', sym: (m) => m.bn },
    bybit: { key: 'bybit', label: 'Bybit', sym: (m) => m.by },
  };
  // Fills settle at Hyperliquid prices. Binance/Bybit stay as a chart view only for crypto,
  // where the gap is ~0.01–0.03%; gold, silver or oil there trade ~0.1–0.2% away, so an
  // entry line drawn over their candles looked wrong.
  const venuesFor = (m) => Object.values(VENUES).filter((v) => !!v.sym(m) && (v.key === 'hyperliquid' || m.cls === 'crypto')).map((v) => v.key);

  const num = (x) => (x == null || x === '' ? null : +x);
  const post = (body) => fetch(HL_INFO, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  const getJ = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });

  /* ---------- historia świec ---------- */
  async function history(venue, m, interval, endMs, limit) {
    limit = limit || 500;
    const end = endMs || Date.now();
    const step = MS[interval] || 6e4;
    if (venue === 'binance') {
      const d = await getJ(`${BN_REST}/fapi/v1/klines?symbol=${m.bn}&interval=${interval}&limit=${Math.min(limit, 1500)}&endTime=${end}`);
      return d.map((k) => ({ timestamp: k[0], open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] }));
    }
    if (venue === 'bybit') {
      const d = await getJ(`${BY_REST}/v5/market/kline?category=linear&symbol=${m.by}&interval=${BY_INT[interval]}&limit=${Math.min(limit, 1000)}&end=${end}`);
      return ((d.result && d.result.list) || []).map((k) => ({ timestamp: +k[0], open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] })).reverse();
    }
    const d = await post({ type: 'candleSnapshot', req: { coin: m.hl, interval, startTime: end - step * Math.min(limit, 5000), endTime: end } });
    return (d || []).map((k) => ({ timestamp: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v }));
  }

  /* ---------- gniazdo z ponawianiem ---------- */
  function socket(url, { onopen, onmessage, onstate }) {
    let ws, closed = false, tries = 0, timer;
    const open = () => {
      onstate && onstate('connecting');
      ws = new WebSocket(url);
      ws.onopen = () => { tries = 0; onstate && onstate('open'); onopen && onopen(ws); };
      ws.onmessage = (e) => { let d; try { d = JSON.parse(e.data); } catch (_) { return; } onmessage(d, ws); };
      ws.onclose = () => {
        if (closed) return;
        onstate && onstate('closed');
        timer = setTimeout(open, Math.min(15000, 500 * 2 ** tries++));
      };
      ws.onerror = () => { try { ws.close(); } catch (_) {} };
    };
    open();
    return {
      send: (o) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); },
      close: () => { closed = true; clearTimeout(timer); try { ws && ws.close(); } catch (_) {} },
      get ready() { return ws && ws.readyState === 1; },
    };
  }

  /* ---------- Hyperliquid: jedno gniazdo, subskrypcje dokładane i zdejmowane ---------- */
  const hl = (function () {
    const subs = new Map();          // klucz → {sub, cb}
    let lat = null, pingAt = 0;
    const latCbs = [];
    const state = { v: 'connecting' };
    const stCbs = [];
    const key = (s) => JSON.stringify(s);
    const s = socket(HL_WS, {
      onstate: (v) => { state.v = v; stCbs.forEach((f) => f(v)); },
      onopen: () => { subs.forEach((x) => s.send({ method: 'subscribe', subscription: x.sub })); ping(); },
      onmessage: (d) => {
        if (d.channel === 'pong') { lat = Date.now() - pingAt; latCbs.forEach((f) => f(lat)); return; }
        subs.forEach((x) => { if (x.match(d)) x.cb(d.data); });
      },
    });
    function ping() { pingAt = Date.now(); s.send({ method: 'ping' }); }
    setInterval(() => { if (!document.hidden) ping(); }, 10000);   // opóźnienie tylko dla widocznej karty
    function sub(subscription, match, cb) {
      const k = key(subscription) + Math.random();
      subs.set(k, { sub: subscription, match, cb });
      s.send({ method: 'subscribe', subscription });
      return () => { subs.delete(k); if (![...subs.values()].some((x) => key(x.sub) === key(subscription))) s.send({ method: 'unsubscribe', subscription }); };
    }
    // onState oddaje funkcję wypisania: strumień rynku rejestruje się przy każdej
    // zmianie rynku/giełdy, a stare wywołania ustawiałyby status innej giełdy.
    const onState = (f) => { stCbs.push(f); f(state.v); return () => { const i = stCbs.indexOf(f); if (i >= 0) stCbs.splice(i, 1); }; };
    return { sub, onLatency: (f) => { latCbs.push(f); if (lat != null) f(lat); }, onState };
  })();

  /* Wszystkie midy (pasek tickerów, wyszukiwarka): główna giełda + HIP-3 xyz. */
  function allMids(cb) {
    const u1 = hl.sub({ type: 'allMids' }, (d) => d.channel === 'allMids' && !(d.data && d.data.dex), (d) => cb(d.mids || {}));
    const u2 = hl.sub({ type: 'allMids', dex: 'xyz' }, (d) => d.channel === 'allMids' && d.data && d.data.dex === 'xyz', (d) => cb(d.mids || {}));
    return () => { u1(); u2(); };
  }

  /* 24h zmiana, wolumen, OI dla wszystkich rynków Hyperliquid (co 30 s z REST). */
  async function contexts() {
    const out = {};
    const [a, b] = await Promise.all([post({ type: 'metaAndAssetCtxs' }), post({ type: 'metaAndAssetCtxs', dex: 'xyz' })]);
    [a, b].forEach((d) => {
      if (!d || !d[0]) return;
      d[0].universe.forEach((u, i) => {
        const c = d[1][i] || {};
        out[u.name] = { mark: num(c.markPx), mid: num(c.midPx), prev: num(c.prevDayPx), vol: num(c.dayNtlVlm), oi: num(c.openInterest), funding: num(c.funding) };
      });
    });
    return out;
  }

  /* ---------- strumień jednego rynku ---------- */
  function stream(venue, m, interval, h) {
    if (venue === 'binance') return streamBinance(m, interval, h);
    if (venue === 'bybit') return streamBybit(m, interval, h);
    return streamHl(m, interval, h);
  }

  /* Each stream has `interval(i)`: an interval change swaps only the candles, while
     the book and trades keep flowing without reconnecting. `group(g)` = price step of
     the book; `h.top(bid, ask)` = exact best prices (spread, ticket) even when the book
     arrives in coarse buckets. */
  const sortBook = (bids, asks, cap) => [
    [...bids].map(([p, q]) => [+p, q]).sort((x, y) => y[0] - x[0]).slice(0, cap),
    [...asks].map(([p, q]) => [+p, q]).sort((x, y) => x[0] - y[0]).slice(0, cap),
  ];

  function streamHl(m, interval, h) {
    const c = m.hl;
    const candles = (i) => hl.sub({ type: 'candle', coin: c, interval: i }, (d) => d.channel === 'candle' && d.data.s === c && d.data.i === i, (k) => {
      h.candle({ timestamp: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v });
    });
    let offCandles = candles(interval);
    /* Hyperliquid sends 20 levels per side. A coarser step is aggregated by the exchange
       (nSigFigs 2–5), otherwise grouping 20 fine levels would leave a few rows. */
    let grp = 0, mid = 0, sig, offBook = () => {};
    const book = (n) => {
      offBook();
      sig = n;
      const s = n ? { type: 'l2Book', coin: c, nSigFigs: n } : { type: 'l2Book', coin: c };
      offBook = hl.sub(s, (d) => d.channel === 'l2Book' && d.data.coin === c, (d) => {
        const lv = d.levels || [[], []];
        h.book(lv[0].map((x) => [+x.px, +x.sz]), lv[1].map((x) => [+x.px, +x.sz]));
      });
    };
    const regroup = () => {
      if (!grp || !mid) return;
      const n = Math.max(2, Math.round(Math.floor(Math.log10(mid)) + 1 - Math.log10(grp)));
      const want = n >= 5 ? null : n;
      if (want !== sig) book(want);
    };
    book(null);
    const off = [
      hl.sub({ type: 'bbo', coin: c }, (d) => d.channel === 'bbo' && d.data.coin === c, (d) => {
        const [b, a] = d.bbo || [];
        if (!b || !a) return;
        h.top(+b.px, +a.px);
        mid = (+b.px + +a.px) / 2;
        regroup();
      }),
      hl.sub({ type: 'trades', coin: c }, (d) => d.channel === 'trades' && d.data[0] && d.data[0].coin === c, (d) => {
        h.trades(d.map((t) => ({ px: +t.px, sz: +t.sz, side: t.side === 'B' ? 'buy' : 'sell', t: t.time })));
      }),
      hl.sub({ type: 'activeAssetCtx', coin: c }, (d) => (d.channel === 'activeAssetCtx' || d.channel === 'activeSpotAssetCtx') && d.data.coin === c, (d) => {
        const x = d.ctx || {};
        const mark = num(x.markPx), prev = num(x.prevDayPx);
        h.ticker({ mark, mid: num(x.midPx), chg: prev ? (mark / prev - 1) * 100 : null, oiUsd: num(x.openInterest) * mark, vol: num(x.dayNtlVlm), funding: num(x.funding) });
      }),
      hl.onState(h.status || (() => {})),
    ];
    return {
      close: () => { off.forEach((f) => f()); offCandles(); offBook(); },
      interval: (i) => { offCandles(); offCandles = candles(i); },
      group: (g) => { grp = g; regroup(); },
      aggregates: true,
    };
  }

  function streamBinance(m, interval, h) {
    const s = m.bn.toLowerCase();
    let last = {};
    /* Local book: REST snapshot (1000 levels) + diff stream, per Binance's recipe
       (drop u < lastUpdateId, every next event continues pu = previous u, else resync).
       depth20 gave only ~20 ticks, so grouping had almost nothing to merge. */
    const bids = new Map(), asks = new Map();
    let lastId = 0, prevU = 0, buf = [], loading = false, gen = 0, emitT = 0, lo = 0, hi = Infinity;
    // Levels outside the snapshot's range are known only if they changed afterwards,
    // so the book there has holes; drop them.
    const trim = (map, ok) => { if (map.size > 1200) map.forEach((_, p) => { if (!ok(+p)) map.delete(p); }); };
    const emit = () => {
      if (emitT) return;
      emitT = setTimeout(() => {
        emitT = 0;
        trim(bids, (p) => p >= lo); trim(asks, (p) => p <= hi);
        let [b, a] = sortBook(bids, asks, 1000);
        b = b.filter((x) => x[0] >= lo); a = a.filter((x) => x[0] <= hi);
        if (b[0] && a[0]) h.top(b[0][0], a[0][0]);
        h.book(b, a);
      }, 100);
    };
    const put = (map, rows) => rows.forEach(([p, q]) => (+q ? map.set(p, +q) : map.delete(p)));
    const apply = (x) => {
      if (!lastId) { buf.push(x); if (buf.length > 600) buf.shift(); return; }
      if (x.u < lastId) return;
      if (prevU ? x.pu !== prevU : x.U > lastId) return resync();
      prevU = x.u;
      put(bids, x.b); put(asks, x.a);
      emit();
    };
    async function resync() {
      if (loading) return;
      loading = true; lastId = 0; prevU = 0;
      const my = ++gen;
      try {
        const d = await getJ(`${BN_REST}/fapi/v1/depth?symbol=${m.bn}&limit=1000`);
        if (my !== gen) return;
        bids.clear(); asks.clear();
        put(bids, d.bids); put(asks, d.asks);
        lo = d.bids.length ? +d.bids[d.bids.length - 1][0] : 0;
        hi = d.asks.length ? +d.asks[d.asks.length - 1][0] : Infinity;
        lastId = d.lastUpdateId;
        loading = false;
        const q = buf; buf = [];
        q.forEach(apply);
        emit();
      } catch (_) {
        if (my === gen) setTimeout(() => { loading = false; resync(); }, 3000);
      }
    }
    const pub = socket(BN_PUB + `${s}@depth@100ms`, {
      onstate: h.status,
      onopen: () => { buf = []; loading = false; resync(); },
      onmessage: (d) => { const x = d.data; if (x && x.e === 'depthUpdate') apply(x); },
    });
    const mkt = socket(BN_MKT + [`${s}@aggTrade`, `${s}@ticker`, `${s}@markPrice@1s`].join('/'), {
      onmessage: (d) => {
        const x = d.data || {};
        if (x.e === 'aggTrade') h.trades([{ px: +x.p, sz: +x.q, side: x.m ? 'sell' : 'buy', t: x.T }]);
        else if (x.e === '24hrTicker') { last = { ...last, chg: +x.P, vol: +x.q, lastPx: +x.c }; h.ticker({ ...last }); }
        else if (x.e === 'markPriceUpdate') { last = { ...last, mark: +x.p, funding: +x.r }; if (last.oi) last.oiUsd = last.oi * last.mark; h.ticker({ ...last }); }
      },
    });
    // Świece osobnym gniazdem: zmiana interwału łączy od nowa tylko je.
    const klines = (i) => socket(BN_MKT + `${s}@kline_${i}`, {
      onmessage: (d) => { const x = d.data || {}; if (x.e === 'kline') { const k = x.k; h.candle({ timestamp: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v }); } },
    });
    let kl = klines(interval);
    const oi = () => getJ(`${BN_REST}/fapi/v1/openInterest?symbol=${m.bn}`).then((d) => { last.oi = +d.openInterest; if (last.mark) { last.oiUsd = last.oi * last.mark; h.ticker({ ...last }); } }).catch(() => {});
    oi();
    const t = setInterval(() => { if (!document.hidden) oi(); }, 30000);
    return {
      close: () => { gen++; clearTimeout(emitT); pub.close(); mkt.close(); kl.close(); clearInterval(t); },
      interval: (i) => { kl.close(); kl = klines(i); },
      group: () => {},
    };
  }

  function streamBybit(m, interval, h) {
    const sym = m.by;
    const bids = new Map(), asks = new Map();
    let tk = {};
    // 1000 levels (pushed every 200 ms) so a coarser step still fills the rows;
    // orderbook.1 keeps the best bid/ask exact between those pushes.
    let emitT = 0;
    const emitBook = () => {
      if (emitT) return;
      emitT = setTimeout(() => { emitT = 0; const [b, a] = sortBook(bids, asks, 1000); h.book(b, a); }, 100);
    };
    let ping, kInt = interval;
    const s = socket(BY_WS, {
      onstate: h.status,
      onopen: (ws) => {
        ws.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.1000.${sym}`, `orderbook.1.${sym}`, `publicTrade.${sym}`, `kline.${BY_INT[kInt]}.${sym}`, `tickers.${sym}`] }));
        clearInterval(ping); ping = setInterval(() => s.send({ op: 'ping' }), 20000);
      },
      onmessage: (d) => {
        const tp = d.topic || '';
        if (tp === `orderbook.1.${sym}`) {
          const x = d.data;
          if (x.b[0] && x.a[0]) h.top(+x.b[0][0], +x.a[0][0]);
        } else if (tp.startsWith('orderbook.')) {
          const x = d.data;
          if (d.type === 'snapshot') { bids.clear(); asks.clear(); }
          x.b.forEach(([p, q]) => (+q ? bids.set(p, +q) : bids.delete(p)));
          x.a.forEach(([p, q]) => (+q ? asks.set(p, +q) : asks.delete(p)));
          emitBook();
        } else if (tp.startsWith('publicTrade.')) {
          h.trades(d.data.map((t) => ({ px: +t.p, sz: +t.v, side: t.S === 'Buy' ? 'buy' : 'sell', t: t.T })));
        } else if (tp === `kline.${BY_INT[kInt]}.${sym}`) {      // spóźnione świece starego interwału odpadają
          d.data.forEach((k) => h.candle({ timestamp: +k.start, open: +k.open, high: +k.high, low: +k.low, close: +k.close, volume: +k.volume }));
        } else if (tp.startsWith('tickers.')) {
          const x = d.data;
          tk = { ...tk, ...Object.fromEntries(Object.entries(x).filter(([, v]) => v !== undefined && v !== '')) };
          h.ticker({ mark: num(tk.markPrice), chg: num(tk.price24hPcnt) * 100, oiUsd: num(tk.openInterestValue), vol: num(tk.turnover24h), funding: num(tk.fundingRate), lastPx: num(tk.lastPrice) });
        }
      },
    });
    return {
      close: () => { clearInterval(ping); clearTimeout(emitT); s.close(); },
      interval: (i) => {
        s.send({ op: 'unsubscribe', args: [`kline.${BY_INT[kInt]}.${sym}`] });
        kInt = i;
        s.send({ op: 'subscribe', args: [`kline.${BY_INT[kInt]}.${sym}`] });
      },
      group: () => {},
    };
  }

  /* ---------- Trading Data / Info ----------
     Open interest history: Binance (value at historical mark prices) or Bybit (contracts,
     valued at the candle close). Hyperliquid publishes no OI history. Results are cached
     for a minute; CoinGecko coin data for 15 minutes (its free API is rate limited). */
  const cache = new Map();
  const cached = (key, ttl, load) => {
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < ttl) return hit.p;
    const p = load();
    cache.set(key, { at: Date.now(), p });
    p.catch(() => cache.delete(key));
    return p;
  };
  const OI_SRC = (m, venue) => (venue === 'bybit' && m.by ? 'bybit' : m.bn ? 'binance' : m.by ? 'bybit' : null);
  const BY_OI = { '15m': '15min', '1h': '1h', '4h': '4h' };
  function oiHistory(m, venue, period, n) {
    const src = OI_SRC(m, venue);
    if (!src) return Promise.resolve(null);
    return cached(`oi:${src}:${m.sym}:${period}:${n}`, 60000, async () => {
      if (src === 'binance') {
        const d = await getJ(`${BN_REST}/futures/data/openInterestHist?symbol=${m.bn}&period=${period}&limit=${n}`);
        return { src, rows: d.map((x) => ({ t: x.timestamp, qty: +x.sumOpenInterest, usd: +x.sumOpenInterestValue })) };
      }
      const [d, k] = await Promise.all([
        getJ(`${BY_REST}/v5/market/open-interest?category=linear&symbol=${m.by}&intervalTime=${BY_OI[period]}&limit=${Math.min(n, 200)}`),
        history('bybit', m, period, Date.now(), n + 2),
      ]);
      const close = new Map(k.map((c) => [c.timestamp, c.close]));
      const rows = ((d.result && d.result.list) || []).map((x) => ({ t: +x.timestamp, qty: +x.openInterest }))
        .map((r) => ({ ...r, usd: close.has(r.t) ? r.qty * close.get(r.t) : null })).filter((r) => r.usd != null);
      return { src, rows: rows.sort((a, b) => a.t - b.t) };
    });
  }
  const priceHistory = (venue, m, period, n) => cached(`px:${venue}:${m.sym}:${period}:${n}`, 60000, () => history(venue, m, period, Date.now(), n));
  const coinInfo = (id) => cached('cg:' + id, 900000, () => getJ(`https://api.coingecko.com/api/v3/coins/${id}?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false`));

  window.PTFeed = { VENUES, venuesFor, history, stream, allMids, contexts, onLatency: hl.onLatency, onHlState: hl.onState, MS, OI_SRC, oiHistory, priceHistory, coinInfo };
})();
