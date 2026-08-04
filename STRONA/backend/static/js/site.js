/* Public site: nav, reveals, counters, pricing/objectives from /api/products,
   stats from /api/public/stats (zero tiles are HIDDEN — no fake numbers),
   leaderboard from /api/leaderboard (masked on the API side). */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const money = (n) => '$' + fmt(Math.round(n));

  /* ---------- nav ---------- */
  const nav = $('.nav');
  const onScroll = () => nav && nav.classList.toggle('on', scrollY > 10);
  addEventListener('scroll', onScroll, { passive: true }); onScroll();

  const burger = $('#burger'), mob = $('#mobileMenu');
  function menuSet(open) {
    if (!mob) return;
    mob.classList.toggle('open', open);
    if (burger) { burger.classList.toggle('open', open); burger.setAttribute('aria-expanded', String(open)); }
    document.body.classList.toggle('menu-open', open);
  }
  if (burger) burger.addEventListener('click', () => menuSet(!mob.classList.contains('open')));
  if (mob) {
    $$('a', mob).forEach(a => a.addEventListener('click', () => menuSet(false)));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') menuSet(false); });
    document.addEventListener('click', e => {
      if (!mob.classList.contains('open')) return;
      if (e.target.closest('#mobileMenu') || e.target.closest('#burger')) return;
      menuSet(false);
    });
    addEventListener('resize', () => { if (innerWidth > 960) menuSet(false); }, { passive: true });
  }

  /* ---------- promo bar: dismiss + Apply-promo code flow ---------- */
  /* Dismiss class goes on <html>: it zeroes --promo-h, so every top offset on
     the site (nav, hero, anchors) snaps back in one step. Both flags (dismissed,
     applied) are read by an inline script in <head>, so neither state flashes
     on reload. The accepted code lives in localStorage.pf_promo_code — checkout
     sends it, so the bar and the purchase can never tell two different stories. */
  const promoBar = $('#promoBar'), promoTxt = $('#promoTxt'),
        promoForm = $('#promoForm'), promoInp = $('#promoInp');

  /* ---------- ONE state for the applied code ----------
     There used to be three: localStorage (the bar), a boot-time snapshot (the
     pricing card) and in-memory variables (the coupon). A code entered in one
     place therefore did not exist for the other two — the pricing card needed
     a page reload to notice the bar, and a percentage coupon never reached it
     at all. Everything below reads and writes through these five functions. */
  const readCode = () => {
    try {
      return { promo: localStorage.getItem('pf_promo_code') || null,
               coupon: localStorage.getItem('pf_coupon_code') || null,
               pct: +(localStorage.getItem('pf_coupon_pct') || 0) || 0 };
    } catch (e) { return { promo: null, coupon: null, pct: 0 }; }
  };
  const writeCode = ({ promo = null, coupon = null, pct = 0 }) => {
    try {
      /* The two kinds never stack: the upgrade code keeps the fee and moves the
         size, a coupon cuts the fee. The last code entered wins. */
      if (promo) { localStorage.setItem('pf_promo_code', promo); }
      else { localStorage.removeItem('pf_promo_code'); }
      if (coupon) { localStorage.setItem('pf_coupon_code', coupon);
                    localStorage.setItem('pf_coupon_pct', String(pct)); }
      else { localStorage.removeItem('pf_coupon_code'); localStorage.removeItem('pf_coupon_pct'); }
    } catch (e) {}
  };
  /** Sprawdza kod w API. Zwraca null, gdy nie jest ani promocją, ani kuponem. */
  async function validateCode(code) {
    try {
      if ((await (await fetch('/api/promo?code=' + encodeURIComponent(code))).json()).valid) {
        return { promo: code, coupon: null, pct: 0 };
      }
    } catch (e) {}
    try {
      const r = await fetch('/api/coupon/' + encodeURIComponent(code));
      if (r.ok) {
        const d = await r.json();
        if (d.pct) return { promo: null, coupon: d.code, pct: d.pct };
      }
    } catch (e) {}
    return null;
  }
  /** Zapisuje wynik walidacji i odświeża WSZYSTKIE trzy miejsca naraz. */
  function applyCode(state) { writeCode(state || {}); syncCode(); }
  function syncCode() {
    renderConfigurator();
    renderPricing();
    paintPromoBar();
  }
  function paintPromoBar() {
    if (!promoTxt) return;
    const { promo, coupon } = readCode();
    document.documentElement.classList.toggle('promo-applied', !!(promo || coupon));
    if (promoBar) { promoBar.classList.remove('promo-open'); if (promoForm) promoForm.hidden = true; }
    promoTxt.textContent = promo ? promoTxt.dataset.applied
                         : coupon ? couponMsg()
                         : promoTxt.dataset.default || promoTxt.textContent;
  }
  const promoApplied = () => readCode().promo || '';
  const couponApplied = () => readCode().coupon || '';
  const couponMsg = () => {
    const { coupon, pct } = readCode();
    return coupon + ' applied: ' + (pct ? pct + '% off' : 'discount') + ' your challenge fee at checkout.';
  };
  const promoX = $('#promoX');
  if (promoX) promoX.addEventListener('click', () => {
    document.documentElement.classList.add('promo-off');
    try { localStorage.setItem('promoOff', '1'); } catch (e) {}
  });
  if (promoTxt && promoApplied()) promoTxt.textContent = promoTxt.dataset.applied;
  else if (promoTxt && couponApplied()) promoTxt.textContent = couponMsg();
  function openPromoInput() {
    if (!promoBar || promoApplied()) return;
    document.documentElement.classList.remove('promo-off');
    try { localStorage.removeItem('promoOff'); } catch (e) {}
    promoBar.classList.add('promo-open');
    promoForm.hidden = false;
    scrollTo({ top: 0, behavior: 'smooth' });
    promoInp.focus();
    /* The code ships pre-filled — one click on "Apply promo" shows it landing
       in the input and redeems it by itself; typing stays possible if the
       auto-submit ever fails. */
    if (promoInp.value.trim()) setTimeout(() => promoForm.requestSubmit(), 400);
  }
  const promoApplyBtn = $('#promoApply');
  if (promoApplyBtn) promoApplyBtn.addEventListener('click', openPromoInput);
  if (promoForm) promoForm.addEventListener('submit', async e => {
    e.preventDefault();
    const code = (promoInp.value || '').trim().toUpperCase();
    if (!code) { promoInp.focus(); return; }
    /* The field takes EVERY kind of code and the shared validator decides which
       one it is, so the bar, the hero and the pricing card can never disagree
       about what a code means. */
    const state = await validateCode(code);
    if (!state) {
      promoForm.classList.remove('promo-bad'); void promoForm.offsetWidth;  /* restart the shake */
      promoForm.classList.add('promo-bad');
      promoInp.value = ''; promoInp.placeholder = 'Invalid code';
      promoInp.focus();
      return;
    }
    applyCode(state);
    promoTxt.textContent = state.promo ? 'Upgrade your challenge promo applied!'
                                       : code + ' applied: ' + state.pct + '% off!';
    promoBar.classList.add('promo-flash');
    setTimeout(() => { promoBar.classList.remove('promo-flash'); paintPromoBar(); }, 2600);
  });

  /* ---------- ?ref= capture (partner code attaches at signup) ---------- */
  /* Timestamped: the signup form only prefills codes from a recent visit —
     a partner code stored forever kept resurfacing months later. */
  const ref = new URLSearchParams(location.search).get('ref');
  if (ref) try {
    localStorage.setItem('pf_ref', ref);
    localStorage.setItem('pf_ref_ts', String(Date.now()));
  } catch (e) {}

  const yearEl = $('#year'); if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ---------- GSAP reveals + counters ---------- */
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hasGsap = typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined';
  if (hasGsap) gsap.registerPlugin(ScrollTrigger);

  function revealize(root) {
    const els = $$('[data-rv]', root).filter(el => !el.classList.contains('rv'));
    els.forEach(el => el.classList.add('rv'));
    if (reduced || !hasGsap) { els.forEach(el => el.classList.add('in')); return; }
    ScrollTrigger.batch(els, {
      start: 'top 90%', once: true,
      onEnter: b => b.forEach((el, i) => setTimeout(() => el.classList.add('in'), i * 70)),
    });
  }

  function countUp(el) {
    const target = parseFloat(el.dataset.count || '0');
    const prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    const render = v => { el.textContent = prefix + fmt(Math.round(v)) + suffix; };
    if (reduced || !hasGsap) { render(target); return; }
    const o = { v: 0 };
    ScrollTrigger.create({
      trigger: el, start: 'top 92%', once: true,
      onEnter: () => gsap.to(o, { v: target, duration: 1.6, ease: 'power2.out', onUpdate: () => render(o.v) }),
    });
    render(0);
  }

  /* ---------- mockup: progress bars ---------- */
  setTimeout(() => $$('.mbar i').forEach(b => { b.style.width = (b.dataset.w || 0) + '%'; }), 500);

  /* ---------- /api/public/stats — tiles only for non-zero metrics ---------- */
  async function stats() {
    const band = $('#statsBand'); if (!band) return;
    try {
      const s = await (await fetch('/api/public/stats')).json();
      const defs = [
        { v: s.accounts_total, label: 'Trading accounts created' },
        { v: s.traders_total, label: 'Traders on the platform' },
        { v: s.payouts_total_usd, label: 'Paid in rewards', prefix: '$' },
        { v: s.funded_accounts, label: 'Funded accounts' },
        { v: s.countries_count, label: 'Countries' },
      ].filter(d => d.v > 0).slice(0, 4);
      if (defs.length < 2) { band.remove(); return; }   // fresh install: fake nothing
      $('#statsGrid').innerHTML = defs.map(d => {
        /* Long figures (9+ digits incl. separators) get a smaller size class —
           the tile must never clip the number. */
        const dl = fmt(Math.round(d.v)).length + (d.prefix ? 1 : 0);
        return `<div class="stat"><div class="stat-num grad-text${dl > 9 ? ' stat-num-l' : ''}" data-count="${Math.round(d.v)}"${d.prefix ? ` data-prefix="${d.prefix}"` : ''}></div>
         <div class="stat-label">${d.label}</div></div>`;
      }).join('');
      $$('.stat-num', band).forEach(countUp);
    } catch (e) { band.remove(); }
  }

  /* ---------- /api/products — pricing + trading objectives ---------- */
  let PRODUCTS = [];

  /* Pricing configurator: type toggle + size grid + add-on + coupon on the
     left, live program rules on the right. Data comes from /api/products. */
  /* The applied code is NOT kept here any more — it lives in readCode(). It used
     to be a snapshot taken once at boot, so a code entered in the bar or in the
     hero stayed invisible to this card until the page was reloaded. */
  let cfgType = '2step', cfgSize = null, cfgWt = false;
  const typeItems = t => PRODUCTS
    .filter(p => (t === 'instant' ? p.steps === 0 : p.steps === 2) && p.price_usd > 0)
    .sort((a, b) => a.account_size - b.account_size);
  const sizeLabel = v => v >= 1e6 ? '$' + (v / 1e6) + 'M' : '$' + Math.round(v / 1e3) + 'K';

  function renderPricing() {
    const box = $('#pcfg'); if (!box) return;
    const { promo: cfgPromo, coupon: cfgCoupon, pct: cfgPct } = readCode();
    let items = typeItems(cfgType);
    if (!items.length) { cfgType = cfgType === '2step' ? 'instant' : '2step'; items = typeItems(cfgType); }
    if (!items.length) { box.closest('.pcfg-grid')?.remove(); return; }
    if (!items.some(x => x.account_size === cfgSize)) cfgSize = items[0].account_size;
    const p = items.find(x => x.account_size === cfgSize);

    $('#pcfg-title').textContent = cfgType === 'instant' ? 'Instant Funding' : '2-Step Evaluation';
    $('#pcfg-toggle').innerHTML = [['2step', '2-Step Evaluation'], ['instant', 'Instant Funding']]
      .filter(([id]) => typeItems(id).length)
      .map(([id, nm]) => `<button class="ptog${id === cfgType ? ' on' : ''}" data-t="${id}">${nm}</button>`).join('');
    $$('#pcfg-toggle .ptog').forEach(b => b.addEventListener('click', () => { cfgType = b.dataset.t; renderPricing(); }));

    $('#pcfg-sizes').innerHTML = items.map(x =>
      `<button class="psize${x.account_size === cfgSize ? ' on' : ''}${x.popular ? ' hot' : ''}" data-s="${x.account_size}">${x.popular ? '<i>Best value</i>' : ''}${sizeLabel(x.account_size)}</button>`).join('');
    $$('#pcfg-sizes .psize').forEach(b => b.addEventListener('click', () => { cfgSize = +b.dataset.s; renderPricing(); }));

    const wt = $('#pcfg-wt');
    wt.textContent = cfgWt ? '✓ Added' : 'Add $199';
    wt.classList.toggle('on', cfgWt);

    /* The upgrade promo does not touch the fee — it moves the account one tier
       up, so the size has to be stated where the price is, or the offer reads
       like nothing happened. */
    const upSize = cfgPromo && p.promo_upgrade_size ? p.promo_upgrade_size : null;
    const fee = Math.round((p.price_usd * (1 - cfgPct / 100) + (cfgWt ? 199 : 0)) * 100) / 100;
    $('#pcfg-fee').textContent = '$' + fee.toLocaleString('en-US',
      { minimumFractionDigits: fee % 1 ? 2 : 0, maximumFractionDigits: 2 });
    $('#pcfg-feesub').textContent = (cfgPct ? `${cfgPct}% coupon applied · ` : '')
      + (upSize ? `${cfgPromo} applied: you trade ${sizeLabel(upSize)} for the price of ${sizeLabel(p.account_size)} · ` : '')
      + (cfgType === 'instant' ? 'one-time fee, funded from day one'
         : 'one-time fee for evaluation access, refunded with your first payout');

    const q = new URLSearchParams({ buy: p.key });
    if (cfgWt) q.set('wt', '1');
    if (cfgCoupon) q.set('coupon', cfgCoupon);
    $('#pcfg-cta').href = '/portal?' + q.toString();

    const wkVal = cfgWt ? '<span class="ok">✓ Added to your order</span>' : '$199 bonus add-on';
    const rows = cfgType === 'instant' ? [
      ['Profit target', 'None. Funded from day one'],
      ['Maximum daily drawdown', p.max_daily_loss_pct + '%'],
      ['Maximum total drawdown', p.max_overall_loss_pct + '%'],
      ['Drawdown type', 'Balance-based'],
      ['Minimum trading days', p.min_trading_days + ' days before first payout'],
      ['Reward frequency', 'Every 7 days'],
      ['Profit split', p.profit_split_pct + '%'],
      ['Max open volume', p.max_lots + ' lots'],
      ['Weekend trading', wkVal],
      ['News trading', '<span class="ok">✓ Allowed</span>'],
      ['Leverage', 'Up to 1:100'],
    ] : [
      ['Profit target', `Phase 1: ${p.profit_target_p1}% | Phase 2: ${p.profit_target_p2}%`],
      ['Maximum daily drawdown', p.max_daily_loss_pct + '%'],
      ['Maximum total drawdown', p.max_overall_loss_pct + '%'],
      ['Drawdown type', 'Balance-based'],
      ['Minimum trading days', p.min_trading_days + ' days'],
      ['Reward frequency', 'Bi-weekly'],
      ['Profit split', 'Up to ' + p.profit_split_pct + '%'],
      ['Max open volume', p.max_lots + ' lots'],
      ['Fee refund', '<span class="ok">✓ With your first payout</span>'],
      ['Weekend trading', wkVal],
      ['News trading', '<span class="ok">✓ Allowed</span>'],
      ['Leverage', 'Up to 1:100'],
    ];
    /* The promo message is rebuilt on every render, because it depends on the
       size picked: the largest tier has nothing above it to upgrade to. */
    if (cfgPromo) {
      codeMsg('ok', upSize
        ? `<b>${cfgPromo}</b> applied: you pay for ${sizeLabel(p.account_size)} and trade `
          + `<b>${sizeLabel(upSize)}</b>. Pick any size, the upgrade follows.`
        : `<b>${cfgPromo}</b> applied, but ${sizeLabel(p.account_size)} is our largest account — `
          + `there is nothing above it. Pick a smaller size to trade one tier up.`);
    } else if (cfgCoupon) {
      codeMsg('ok', `<b>${cfgCoupon}</b> applied: <b>${cfgPct}% off</b> your challenge fee.`);
    } else {
      codeMsg(null);
    }
    const pinp = $('#pcfg-coupon');
    if (pinp && document.activeElement !== pinp) pinp.value = cfgPromo || cfgCoupon || '';
    if (upSize) rows.unshift(['Account size',
      `<span class="ok">${sizeLabel(upSize)} — upgraded from ${sizeLabel(p.account_size)}</span>`]);
    $('#prules-rows').innerHTML = rows.map(([l, v]) =>
      `<div class="prule"><span>${l}</span><b>${v}</b></div>`).join('');
  }

  /* The field takes EVERY kind of code, exactly like the promo bar and the
     portal checkout: the "Upgrade Your Size" code first, anything else as a
     discount coupon. Checking only coupons here was the bug — the upgrade code
     is not in the coupon table, so a perfectly valid code came back red. */
  /* The applied code confirms itself right under the input. Without it the
     upgrade code looked dead: it does not move the price, and the only hint sat
     in small grey type further down the card. */
  /* Jedno pudelko potwierdzenia, dwa miejsca. Komunikaty sa OSOBNE, bo kazdy
     kreator ma wlasny wybrany rozmiar, a tresc mowi wprost o rozmiarze. */
  function codeBox(msgSel, inpSel, kind, html) {
    const el = $(msgSel), inp = $(inpSel);
    if (inp) { inp.classList.remove('ok', 'bad'); if (kind) inp.classList.add(kind); }
    if (!el) return;
    if (!kind) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    el.className = 'code-msg ' + kind;
    el.innerHTML = `<span>${kind === 'ok' ? '✓' : '✕'}</span><span>${html}</span>`;
  }
  const codeMsg = (kind, html) => codeBox('#pcfg-codemsg', '#pcfg-coupon', kind, html);
  const heroMsg = (kind, html) => codeBox('#cfg-codemsg', '#cfg-code', kind, html);

  /** Wspolna obsluga pola z kodem: hero i cennik roznia sie tylko pudelkiem. */
  async function submitCodeField(inp, msg) {
    const code = (inp.value || '').trim().toUpperCase();
    if (code) inp.value = code;
    if (!code) { applyCode(null); return; }      /* puste pole czysci stan wszedzie */
    const state = await validateCode(code);
    /* Odrzucony kod kasuje takze to, co bylo zastosowane wczesniej: pole trzyma
       jeden kod naraz, wiec „UPGRADE applied" pod bledna wartoscia klamaloby. */
    applyCode(state);
    if (!state) { inp.classList.add('bad'); msg('bad', `<b>${code}</b> is not a valid code.`); }
  }

  function renderObjectives() {
    const body = $('#objBody'); if (!body) return;
    const g2 = PRODUCTS.filter(p => p.steps === 2 && p.price_usd > 0);
    const gi = PRODUCTS.filter(p => p.steps === 0 && p.price_usd > 0);
    if (!g2.length) return;
    const r2 = g2[g2.length - 1], ri = gi.length ? gi[gi.length - 1] : null;
    const col = v => ri ? [v] : [];
    const rows = [
      ['Profit target — Phase 1', r2.profit_target_p1 + '%', ...col('<span class="ok">none</span>')],
      ['Profit target — Phase 2', r2.profit_target_p2 + '%', ...col('—')],
      ['Maximum daily loss', r2.max_daily_loss_pct + '%', ...col(ri && ri.max_daily_loss_pct + '%')],
      ['Maximum overall loss', r2.max_overall_loss_pct + '%', ...col(ri && ri.max_overall_loss_pct + '%')],
      ['Drawdown type', 'Balance-based', ...col('Balance-based')],
      ['Minimum trading days', r2.min_trading_days + ' days', ...col(ri && ri.min_trading_days + ' days')],
      ['Reward frequency', 'Bi-weekly', ...col('Every 7 days')],
      ['News trading', '<span class="ok">✓ Allowed</span>', ...col('<span class="ok">✓ Allowed</span>')],
      ['Weekend trading', '$199 add-on', ...col('$199 add-on')],
      /* Oba modele dostaja 1:100, bo z taka dzwignia provisioning ZAKLADA konta
         (config: METAAPI_DEMO_LEVERAGE / METAQUOTES_WEB_LEVERAGE = 100). Kolumna
         Instant mowila 1:50 i klamala wobec tego, co trader dostawal w MT5. */
      ['Leverage', 'Up to 1:100', ...col('Up to 1:100')],
      ['Profit split', 'up to ' + Math.max(...g2.map(p => p.profit_split_pct)) + '%', ...col(ri && ri.profit_split_pct + '%')],
      /* The engine refunds the fee on the FIRST payout for every plan
         (main.py:1342) — Instant Funding included. */
      ['Refundable fee', '<span class="ok">✓ With first payout</span>',
        ...col('<span class="ok">✓ With first payout</span>')],
      ['One-time fee', 'from ' + money(Math.min(...g2.map(p => p.price_usd))), ...col(ri && 'from ' + money(Math.min(...gi.map(p => p.price_usd))))],
    ];
    body.innerHTML = rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('');
    const head = document.querySelector('#objectives thead tr');
    if (head) {
      head.innerHTML = '<th>Objective</th><th>2-Step Evaluation</th>' + (ri ? '<th>Instant Funding</th>' : '');
    }
  }
  const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;

  /* ---------- hero: challenge configurator ---------- */
  /* First paint uses server-inlined data — numbers are written directly, the
     0 -> value ramp would look like the panel loading twice. */
  let instantRender = false;
  function tweenNum(el, to, fmtFn) {
    if (!el) return;
    const from = parseFloat(el.dataset.v || '0');
    el.dataset.v = to;
    if (reduced || instantRender) { el.textContent = fmtFn(to); return; }
    const t0 = performance.now(), D = 340;
    (function step(t) {
      const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmtFn(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }
  let cfgStep = '2step', cfgKey = null;

  function renderConfigurator() {
    const root = $('#cfg'); if (!root) return;
    /* Two models in the catalog: 2-Step Evaluation (steps=2) and Instant Funding
       (steps=0). Matching on steps===1 left the second tab permanently empty. */
    const gs = {
      '2step': PRODUCTS.filter(p => p.steps === 2 && p.price_usd > 0).sort((a, b) => a.account_size - b.account_size),
      'instant': PRODUCTS.filter(p => p.steps === 0 && p.price_usd > 0).sort((a, b) => a.account_size - b.account_size),
    };
    if (!gs['2step'].length && !gs['instant'].length) { root.remove(); return; }
    if (!gs[cfgStep].length) cfgStep = gs['2step'].length ? '2step' : 'instant';
    const items = gs[cfgStep];
    if (!items.some(p => p.key === cfgKey)) {
      cfgKey = (items.find(p => p.account_size === 100000) || items[items.length - 1]).key;
    }
    $('#cfg-tabs').innerHTML = [['2step', '2-Step Evaluation'], ['instant', 'Instant Funding']]
      .filter(([id]) => gs[id].length)
      .map(([id, nm]) => `<button class="cfg-tab${id === cfgStep ? ' on' : ''}" data-step="${id}">${nm}<small>from $${fmt(Math.min(...gs[id].map(p => p.price_usd)))}</small></button>`).join('');
    $$('#cfg-tabs .cfg-tab').forEach(b => b.addEventListener('click', () => { cfgStep = b.dataset.step; cfgKey = null; renderConfigurator(); }));
    $('#cfg-sizes').innerHTML = items.map(p =>
      `<button class="cfg-size${p.key === cfgKey ? ' on' : ''}" data-key="${p.key}">${sizeLabel(p.account_size)}</button>`).join('');
    $$('#cfg-sizes .cfg-size').forEach(b => b.addEventListener('click', () => { cfgKey = b.dataset.key; renderConfigurator(); }));

    const p = items.find(x => x.key === cfgKey);
    const instant = p.steps === 0;
    const { promo: kodPromo, coupon: kodKupon, pct: kodPct } = readCode();
    /* Rabat musi byc widoczny TU, a nie dopiero w cenniku nizej: pole na kod
       stoi w tej karcie, wiec bez tego wpisanie WELCOME10 nie robilo nic. */
    const oplata = Math.round(p.price_usd * (1 - kodPct / 100) * 100) / 100;
    tweenNum($('#cfg-fee'), oplata, v => '$' + fmt(Math.round(v)));
    /* Instant Funding has no phases — the row says so instead of printing
       a "+0% = $0" target. */
    $('#cfg-target-label').textContent = instant ? 'Profit target' : 'Profit target — Phase 1';
    if (instant) {
      $('#cfg-target').textContent = 'None. Funded from day one';
    } else {
      $('#cfg-target').innerHTML = `+${p.profit_target_p1}% = $<span id="cfg-tusd"></span>`;
      tweenNum($('#cfg-tusd'), p.account_size * p.profit_target_p1 / 100, v => fmt(Math.round(v)));
    }
    $('#cfg-split').textContent = p.profit_split_pct + '%';

    /* Promo block: the size the account is actually created with. The API sends
       promo_upgrade_size only while the promo is live, and the upgrade itself
       fires only with the applied code — so the block, the bar and the checkout
       can never tell three different stories. */
    const promo = $('#cfg-promo'), fine = $('#cfg-fine');
    const big = p.promo_upgrade_size;
    const anyPromo = PRODUCTS.some(x => x.promo_upgrade_size);
    const applied = !!(kodPromo || kodKupon);
    if (promo) {
      /* Blok zostaje takze bez trwajacej promocji: pole na kod ma byc zawsze
         pod reka, bo kupony procentowe dzialaja niezaleznie od niej. */
      promo.hidden = false;
      promo.classList.toggle('is-max', !!kodPromo && anyPromo && !big);
      promo.classList.toggle('applied', applied);
      const inp = $('#cfg-code');
      if (inp && document.activeElement !== inp) inp.value = kodPromo || kodKupon || '';
      const row = $('#cfg-code-row');
      if (row) row.hidden = applied;
      const hint = $('#cfg-promo-val');
      if (hint) {
        hint.hidden = applied;
        hint.innerHTML = anyPromo
          ? 'Have a promo code? Get the <b>next size up for the same fee</b>.'
          : 'Have a promo or coupon code?';
      }
      if (kodPromo && big) {
        heroMsg('ok', `<b>${kodPromo}</b> applied successfully. You pay for `
          + `${sizeLabel(p.account_size)} and trade <b>${sizeLabel(big)}</b>.`);
      } else if (kodPromo && anyPromo) {
        heroMsg('ok', `<b>${kodPromo}</b> applied successfully, but ${sizeLabel(p.account_size)} `
          + 'is our largest account. Pick a smaller size to trade one tier up.');
      } else if (kodPromo) {
        heroMsg('ok', `<b>${kodPromo}</b> applied successfully.`);
      } else if (kodKupon) {
        heroMsg('ok', `<b>${kodKupon}</b> applied successfully: <b>${kodPct}% off</b> this fee.`);
      } else {
        heroMsg(null);
      }
      const zmien = $('#cfg-code-change');
      if (zmien) {
        zmien.hidden = !applied;
        zmien.onclick = () => { applyCode(null); const i = $('#cfg-code'); if (i) i.focus(); };
      }
    }
    if (fine) {
      fine.textContent = anyPromo && applied
        ? '*You pay the price of the size you pick; with the promo applied we create the account one size up. Fee still refunded with your first payout.'
        : "*One-time fee, refunded with your first payout. Rewards depend on your trading.";
    }
    const cta = $('#cfg-cta');
    const q = new URLSearchParams({ buy: p.key });
    if (kodKupon) q.set('coupon', kodKupon);   /* bez tego rabat ginal po klikniciu */
    cta.href = '/portal?' + q.toString();
    cta.textContent = `Start with ${sizeLabel(p.account_size)} → $${fmt(Math.round(oplata))}`;
  }

  async function products() {
    /* #objBody counts too: /objectives is a page with the rules table and no
       configurator at all, and without it here the table stayed empty. */
    if (!$('#pcfg') && !$('#cfg') && !$('#objBody')) return;
    const wire = () => {
      const ci = $('#pcfg-coupon');
      const send = () => ci && submitCodeField(ci, codeMsg);
      const ap = $('#pcfg-apply'); if (ap) ap.addEventListener('click', send);
      if (ci) ci.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
      const hi = $('#cfg-code');
      const hsend = () => hi && submitCodeField(hi, heroMsg);
      const hb = $('#cfg-code-apply'); if (hb) hb.addEventListener('click', hsend);
      if (hi) hi.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); hsend(); } });
      const wt = $('#pcfg-wt'); if (wt) wt.addEventListener('click', () => { cfgWt = !cfgWt; renderPricing(); });
    };
    /* The server inlines the live catalog into the page (#pf-products) so the
       hero configurator paints together with the rest of the document; the
       fetch below is only a fallback for documents without the node. */
    const inline = document.getElementById('pf-products');
    if (inline) {
      try {
        const data = JSON.parse(inline.textContent);
        if (Array.isArray(data) && data.length) {
          PRODUCTS = data;
          instantRender = true;
          try { renderPricing(); renderObjectives(); renderConfigurator(); }
          finally { instantRender = false; }
          wire();
          return;
        }
      } catch (e) { /* malformed inline data — fall back to the API */ }
    }
    try {
      PRODUCTS = await (await fetch('/api/products')).json();
      renderPricing(); renderObjectives(); renderConfigurator();
      wire();
    } catch (e) {
      if ($('#pcfg')) $('#pcfg').innerHTML = '<p class="muted">Could not load plans. Please refresh.</p>';
    }
  }

  /* ---------- /api/leaderboard — the section hides when there is no data ---------- */
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ---------- the certificate strip drifts, and you can grab it ----------
     Each row moves under rAF instead of @keyframes, because a CSS animation
     cannot be nudged: the old strip could only be watched. A row keeps its
     offset in pixels, wrapped by the length of ONE content copy, so the loop
     stays seamless no matter how far it is dragged or flung. Mouse, finger and
     a horizontal trackpad swipe all push the same offset. */
  function stripMotion(box) {
    const track = $('.cs-marquee', box);
    if (!track) return;
    const rows = $$('.cs-row', track).filter(el => el.children.length);
    if (!rows.length) return;
    /* Resting speed in px/s — opposite directions, slightly out of step, so the
       two rows never line up into one moving block. Zero when the visitor asked
       for less motion; dragging still works. */
    const lanes = rows.map((el, i) => ({
      el, x: 0, v: 0, period: 0, drift: reduced ? 0 : (i % 2 ? -13 : 16),
    }));

    /* One copy is half the row PLUS one gap: the seam between the two halves
       needs the gap that the browser does not draw after the last card. */
    const gap = parseFloat(getComputedStyle(lanes[0].el).columnGap) || 0;
    const measure = () => lanes.forEach(l => { l.period = (l.el.scrollWidth + gap) / 2; });
    measure();
    addEventListener('resize', measure, { passive: true });

    let hover = false, dragging = false, onScreen = true, prev = 0, raf = 0;
    const wrap = (x, p) => ((x % p) + p) % p;
    const paint = () => lanes.forEach(l => {
      if (l.period > 0) l.el.style.transform = `translate3d(${-wrap(l.x, l.period)}px,0,0)`;
    });
    /* Read live rather than trusting enter/leave: pointer capture can swallow a
       leave event, and a strip stuck in "paused" would never move again. Only
       real pointers pause it — a phone keeps :hover on the last tapped element,
       which would freeze the strip for good after one swipe. */
    const canHover = matchMedia('(hover: hover)').matches;
    const target = l => (hover || dragging ? 0 : l.drift);
    const busy = () => dragging || lanes.some(l => l.v || target(l));

    const frame = now => {
      const dt = Math.min(.05, prev ? (now - prev) / 1000 : 0);
      prev = now;
      hover = canHover && track.matches(':hover');
      if (!dragging) for (const l of lanes) {
        /* A fling decays into the resting drift — or into a full stop while the
           pointer rests on the strip, so a card can be read. The rate matches
           what browsers use for their own flings, so it feels borrowed. */
        l.v += (target(l) - l.v) * Math.min(1, dt * 3);
        if (Math.abs(l.v - target(l)) < .05) l.v = target(l);
        l.x += l.v * dt;
      }
      paint();
      if (onScreen && !document.hidden && busy()) raf = requestAnimationFrame(frame);
      else { raf = 0; prev = 0; }
    };
    /* Every wake-up goes through here, and the first frame runs with dt = 0 —
       a strip that was paused for a minute must not jump a minute forward. */
    const kick = () => {
      if (!raf && onScreen && !document.hidden) { prev = 0; raf = requestAnimationFrame(frame); }
    };

    let pid = null, lastX = 0, lastT = 0, fling = 0;
    track.addEventListener('pointerdown', e => {
      if (e.button > 0) return;                       // left button, finger or pen
      pid = e.pointerId; dragging = true; fling = 0;
      lastX = e.clientX; lastT = e.timeStamp;
      track.classList.add('is-drag');
      try { track.setPointerCapture(pid); } catch (err) {}
      kick();
    });
    track.addEventListener('pointermove', e => {
      if (!dragging || e.pointerId !== pid) return;
      const dx = e.clientX - lastX, dt = Math.max(1, e.timeStamp - lastT);
      lastX = e.clientX; lastT = e.timeStamp;
      for (const l of lanes) l.x -= dx;               // the cards follow the hand
      /* Smoothed, so one twitchy sample at the end cannot launch the strip. */
      fling = fling * .7 + (-dx / dt * 1000) * .3;
    });
    const release = e => {
      if (!dragging || (e && e.pointerId !== pid)) return;
      dragging = false;
      track.classList.remove('is-drag');
      try { track.releasePointerCapture(pid); } catch (err) {}
      pid = null;
      const v = Math.max(-2600, Math.min(2600, fling));
      for (const l of lanes) l.v = v;
      kick();
    };
    track.addEventListener('pointerup', release);
    track.addEventListener('pointercancel', release);
    track.addEventListener('pointerenter', kick);
    track.addEventListener('pointerleave', kick);
    track.addEventListener('dragstart', e => e.preventDefault());

    track.addEventListener('wheel', e => {
      /* Only a clearly sideways gesture is ours — vertical scrolling belongs to
         the page. preventDefault also stops a trackpad swipe from being read as
         "go back" by the browser. */
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      e.preventDefault();
      const step = e.deltaMode === 1 ? e.deltaX * 16 : e.deltaX;
      for (const l of lanes) { l.x += step; l.v = 0; }
      paint();
      kick();
    }, { passive: false });

    if ('IntersectionObserver' in window) {
      new IntersectionObserver(es => { onScreen = es.some(x => x.isIntersecting); kick(); },
        { rootMargin: '150px 0px' }).observe(track);
    }
    document.addEventListener('visibilitychange', kick);
    kick();
  }

  /* ---------- recently issued certificates (real data, masked names) ----------
     Each entry is the REAL certificate artwork (cert.css classes, same as the
     document itself) rendered full-size and scaled down — the strip shows the
     graphic people actually share, not an info tile. No tokens are published:
     the QR is the generic /verify code, the token line is omitted. */
  const CS_SCALE = 280 / 620;                 // mini width / .cert-card design width
  async function certsStrip() {
    const box = $('#certsStrip'); if (!box) return;
    try {
      const rows = await (await fetch('/api/public/certificates/recent')).json();
      if (!Array.isArray(rows) || !rows.length) return;   // no data -> no strip
      /* Brand bits are cloned from the server-rendered sample card, so the
         minis can never drift from the real template. */
      const qr = ($('#pv-qr') && $('#pv-qr').innerHTML) || '';
      const brand = (document.querySelector('#pv-card .cert-logo span') || {}).textContent || '';
      const signer = (document.querySelector('#pv-card .sig') || {}).textContent || '';
      const card = r => {
        const payout = r.kind === 'payout';
        const when = r.issued_at
          ? new Date(r.issued_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
          : '';
        const meta = payout
          ? `<div><b>${when}</b><span>Date</span></div><div><b>$${fmt(r.account_size)}</b><span>Account size</span></div>`
          : `<div><b>${when}</b><span>Date</span></div>${r.program ? `<div><b>${esc(r.program)}</b><span>Program</span></div>` : ''}`;
        return `<div class="cs-mini"><div class="cert-card${payout ? ' cert-variant-payout' : ''}">
          <div class="cert-inner">
            <div class="cert-logo"><img src="/static/img/logo.png" alt=""><span>${esc(brand)}</span></div>
            <div class="cert-eyebrow"><s></s>${payout ? 'Payout' : esc(r.kind_label)}<s></s></div>
            <h3 class="cert-title">Certificate</h3>
            <div class="cert-amountlabel">${payout ? 'for the amount of' : 'Account size'}</div>
            <div class="cert-amount">$${fmt(payout ? r.amount_usd : r.account_size)}</div>
            <div class="cert-presented">presented to</div>
            <div class="cert-person">${esc(r.trader)}</div>
            <div class="cert-meta">${meta}</div>
            <div class="cert-foot">
              <div class="cert-signblock"><b class="sig">${esc(signer)}</b><i class="sig-line"></i><span>Chief Executive Officer</span></div>
              <div class="cert-qr"><div class="qr-box">${qr}</div><span>Scan to verify</span></div>
            </div>
          </div>
        </div></div>`;
      };
      /* Two rows, every other card in the lower one, so they never show the
         same set. Each row gets an EVEN number of copies: the loop wraps at
         half the row, so after half a cycle the frame is identical and nothing
         jumps. Three certificates would not fill a wide screen with one copy,
         hence the count comes from a measurement: row width >= 2x the strip. */
      const topRow = rows.filter((_, i) => i % 2 === 0);
      const botRow = rows.filter((_, i) => i % 2 === 1);
      /* Unhide FIRST: w [hidden] KAZDY pomiar to zero — a poniewaz liczba kopii
         wynika z pomiaru, zero oznaczaloby tysiace kart i zawieszona strone.
         Stad tez twardy sufit na liczbe kopii. */
      box.hidden = false;
      const fill = (el, items) => {
        if (!el) return;
        if (!items.length) { el.innerHTML = ''; el.hidden = true; return; }
        const one = items.map(card).join('');
        el.innerHTML = one + one;
        const oneW = el.scrollWidth / 2;
        const target = (box.clientWidth || 1200) * 2;
        if (oneW > 0 && oneW * 2 < target) {
          const need = Math.min(8, 2 * Math.ceil(target / (oneW * 2)));
          el.innerHTML = one.repeat(need);
        }
      };
      fill($('#certsStripRow'), topRow);
      fill($('#certsStripRow2'), botRow);
      /* transform:scale() keeps the 620px layout box — each wrapper gets the
         SCALED height of its card. */
      requestAnimationFrame(() => {
        $$('.cs-mini').forEach(m => {
          const c = m.firstElementChild;
          if (c) m.style.height = Math.round(c.offsetHeight * CS_SCALE) + 'px';
        });
        stripMotion(box);       // rows are final and measurable — start moving
      });
    } catch (e) { /* optional social proof — the section simply stays hidden */ }
  }

  /* ---------- verify quick lookup ---------- */
  /* Verify on the landing morphs the sample certificate into the REAL one —
     the token works exactly like opening /certificate/{token}. */
  const vf = $('#verifyForm');
  if (vf) vf.addEventListener('submit', async e => {
    e.preventDefault();
    const v = $('#verifyInput').value.trim();
    if (!v) return;
    const err = $('#verifyErr'), open = $('#pv-open');
    try {
      const r = await fetch('/api/verify/' + encodeURIComponent(v));
      if (!r.ok) throw new Error();
      const d = await r.json();
      const card = $('#pv-card');
      card.classList.toggle('cert-variant-payout', d.variant === 'payout');
      $('#pv-eyebrow').textContent = d.eyebrow;
      $('#pv-amountlabel').textContent = d.amount_label;
      $('#pv-amount').textContent = d.amount;
      $('#pv-person').textContent = d.trader_name;
      $('#pv-meta').innerHTML = d.meta.map(m =>
        `<div><b>${esc(m.value)}</b><span>${esc(m.label)}</span></div>`).join('');
      $('#pv-qr').innerHTML = d.qr_svg;
      $('#pv-token').textContent = d.cert_token;
      if (open) { open.href = d.open_url; open.style.display = ''; }
      if (err) err.style.display = 'none';
      card.classList.remove('pv-flash'); void card.offsetWidth; card.classList.add('pv-flash');
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (_) {
      if (open) open.style.display = 'none';
      if (err) err.style.display = '';
    }
  });

  /* ---------- init ---------- */
  revealize(document);
  $$('.stat-num[data-count]').forEach(countUp);
  stats(); products(); certsStrip();
})();
