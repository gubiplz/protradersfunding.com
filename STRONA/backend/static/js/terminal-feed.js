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
  const venuesFor = (m) => Object.values(VENUES).filter((v) => !!v.sym(m)).map((v) => v.key);

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

  /* Każdy strumień ma `interval(i)`: zmiana interwału podmienia tylko świece —
     księga i transakcje płyną dalej bez ponownego łączenia. */
  function streamHl(m, interval, h) {
    const c = m.hl;
    const candles = (i) => hl.sub({ type: 'candle', coin: c, interval: i }, (d) => d.channel === 'candle' && d.data.s === c && d.data.i === i, (k) => {
      h.candle({ timestamp: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v });
    });
    let offCandles = candles(interval);
    const off = [
      hl.sub({ type: 'l2Book', coin: c }, (d) => d.channel === 'l2Book' && d.data.coin === c, (d) => {
        const lv = d.levels || [[], []];
        h.book(lv[0].map((x) => [+x.px, +x.sz]), lv[1].map((x) => [+x.px, +x.sz]));
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
      close: () => { off.forEach((f) => f()); offCandles(); },
      interval: (i) => { offCandles(); offCandles = candles(i); },
    };
  }

  function streamBinance(m, interval, h) {
    const s = m.bn.toLowerCase();
    let last = {};
    const pub = socket(BN_PUB + `${s}@depth20@100ms`, {
      onstate: h.status,
      onmessage: (d) => {
        const x = d.data;
        if (x && x.e === 'depthUpdate') h.book(x.b.map((v) => [+v[0], +v[1]]), x.a.map((v) => [+v[0], +v[1]]));
      },
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
      close: () => { pub.close(); mkt.close(); kl.close(); clearInterval(t); },
      interval: (i) => { kl.close(); kl = klines(i); },
    };
  }

  function streamBybit(m, interval, h) {
    const sym = m.by;
    const bids = new Map(), asks = new Map();
    let tk = {};
    const emitBook = () => {
      const b = [...bids].map(([p, q]) => [+p, q]).sort((x, y) => y[0] - x[0]).slice(0, 25);
      const a = [...asks].map(([p, q]) => [+p, q]).sort((x, y) => x[0] - y[0]).slice(0, 25);
      h.book(b, a);
    };
    let ping, kInt = interval;
    const s = socket(BY_WS, {
      onstate: h.status,
      onopen: (ws) => {
        ws.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.50.${sym}`, `publicTrade.${sym}`, `kline.${BY_INT[kInt]}.${sym}`, `tickers.${sym}`] }));
        clearInterval(ping); ping = setInterval(() => s.send({ op: 'ping' }), 20000);
      },
      onmessage: (d) => {
        const tp = d.topic || '';
        if (tp.startsWith('orderbook.')) {
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
      close: () => { clearInterval(ping); s.close(); },
      interval: (i) => {
        s.send({ op: 'unsubscribe', args: [`kline.${BY_INT[kInt]}.${sym}`] });
        kInt = i;
        s.send({ op: 'subscribe', args: [`kline.${BY_INT[kInt]}.${sym}`] });
      },
    };
  }

  window.PTFeed = { VENUES, venuesFor, history, stream, allMids, contexts, onLatency: hl.onLatency, onHlState: hl.onState, MS };
})();
