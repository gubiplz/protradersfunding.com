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
  const promoApplied = () => { try { return localStorage.getItem('pf_promo_code') || ''; } catch (e) { return ''; } };
  const couponApplied = () => { try { return localStorage.getItem('pf_coupon_code') || ''; } catch (e) { return ''; } };
  const couponMsg = () => {
    let pct = ''; try { pct = localStorage.getItem('pf_coupon_pct') || ''; } catch (e) {}
    return couponApplied() + ' applied — ' + (pct ? pct + '% off' : 'discount') + ' your challenge fee at checkout.';
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
    /* The input takes EVERY kind of code: the "Upgrade Your Size" code
       activates the promo, anything else is checked as a discount coupon
       (WELCOME10 -> -10%). Last applied code wins — they don't stack here. */
    let promo = false, pct = 0;
    try { promo = (await (await fetch('/api/promo?code=' + encodeURIComponent(code))).json()).valid; } catch (err) {}
    if (!promo) {
      try {
        const r = await fetch('/api/coupon/' + encodeURIComponent(code));
        if (r.ok) pct = (await r.json()).pct || 0;
      } catch (err) {}
    }
    if (!promo && !pct) {
      promoForm.classList.remove('promo-bad'); void promoForm.offsetWidth;  /* restart the shake */
      promoForm.classList.add('promo-bad');
      promoInp.value = ''; promoInp.placeholder = 'Invalid code';
      promoInp.focus();
      return;
    }
    try {
      if (promo) {
        localStorage.setItem('pf_promo_code', code);
        localStorage.removeItem('pf_coupon_code'); localStorage.removeItem('pf_coupon_pct');
      } else {
        localStorage.setItem('pf_coupon_code', code);
        localStorage.setItem('pf_coupon_pct', String(pct));
        localStorage.removeItem('pf_promo_code');
      }
    } catch (err) {}
    promoBar.classList.remove('promo-open'); promoForm.hidden = true;
    document.documentElement.classList.add('promo-applied');
    promoTxt.textContent = promo ? 'Upgrade your challenge promo applied!'
                                 : code + ' applied — ' + pct + '% off!';
    promoBar.classList.add('promo-flash');
    setTimeout(() => {
      promoBar.classList.remove('promo-flash');
      promoTxt.textContent = promo ? promoTxt.dataset.applied : couponMsg();
    }, 2600);
    renderConfigurator();
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
  let cfgType = '2step', cfgSize = null, cfgWt = false, cfgCoupon = null, cfgPct = 0;
  const typeItems = t => PRODUCTS
    .filter(p => (t === 'instant' ? p.steps === 0 : p.steps === 2) && p.price_usd > 0)
    .sort((a, b) => a.account_size - b.account_size);
  const sizeLabel = v => v >= 1e6 ? '$' + (v / 1e6) + 'M' : '$' + Math.round(v / 1e3) + 'K';

  function renderPricing() {
    const box = $('#pcfg'); if (!box) return;
    let items = typeItems(cfgType);
    if (!items.length) { cfgType = cfgType === '2step' ? 'instant' : '2step'; items = typeItems(cfgType); }
    if (!items.length) { box.closest('.pcfg-grid')?.remove(); return; }
    if (!items.some(x => x.account_size === cfgSize)) cfgSize = items[0].account_size;
    const p = items.find(x => x.account_size === cfgSize);
    const maxSize = Math.max(...items.map(x => x.account_size));

    $('#pcfg-title').textContent = cfgType === 'instant' ? 'Instant Funding' : '2-Step Evaluation';
    $('#pcfg-toggle').innerHTML = [['2step', '2-Step Evaluation'], ['instant', 'Instant Funding']]
      .filter(([id]) => typeItems(id).length)
      .map(([id, nm]) => `<button class="ptog${id === cfgType ? ' on' : ''}" data-t="${id}">${nm}</button>`).join('');
    $$('#pcfg-toggle .ptog').forEach(b => b.addEventListener('click', () => { cfgType = b.dataset.t; renderPricing(); }));

    $('#pcfg-sizes').innerHTML = items.map(x =>
      `<button class="psize${x.account_size === cfgSize ? ' on' : ''}${x.account_size === maxSize ? ' hot' : ''}" data-s="${x.account_size}">${x.account_size === maxSize ? '<i>HOT</i>' : ''}${sizeLabel(x.account_size)}</button>`).join('');
    $$('#pcfg-sizes .psize').forEach(b => b.addEventListener('click', () => { cfgSize = +b.dataset.s; renderPricing(); }));

    const wt = $('#pcfg-wt');
    wt.textContent = cfgWt ? '✓ Added' : 'Add $199';
    wt.classList.toggle('on', cfgWt);

    const fee = Math.round((p.price_usd * (1 - cfgPct / 100) + (cfgWt ? 199 : 0)) * 100) / 100;
    $('#pcfg-fee').textContent = '$' + fee.toLocaleString('en-US',
      { minimumFractionDigits: fee % 1 ? 2 : 0, maximumFractionDigits: 2 });
    $('#pcfg-feesub').textContent = (cfgPct ? `${cfgPct}% coupon applied · ` : '')
      + (cfgType === 'instant' ? 'one-time fee — funded from day one'
         : 'one-time fee for evaluation access, refunded with your first payout');

    const q = new URLSearchParams({ buy: p.key });
    if (cfgWt) q.set('wt', '1');
    if (cfgCoupon) q.set('coupon', cfgCoupon);
    $('#pcfg-cta').href = '/portal?' + q.toString();

    const wkVal = cfgWt ? '<span class="ok">✓ Added to your order</span>' : '$199 bonus add-on';
    const rows = cfgType === 'instant' ? [
      ['Profit target', 'None — funded from day one'],
      ['Maximum daily drawdown', p.max_daily_loss_pct + '%'],
      ['Maximum total drawdown', p.max_overall_loss_pct + '%'],
      ['Drawdown type', 'Balance-based'],
      ['Minimum trading days', p.min_trading_days + ' days before first payout'],
      ['Reward frequency', 'Every 7 days'],
      ['Profit split', p.profit_split_pct + '%'],
      ['Max open volume', p.max_lots + ' lots'],
      ['Weekend trading', wkVal],
      ['News trading', '<span class="ok">✓ Allowed</span>'],
      ['Leverage', 'Up to 1:50'],
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
    $('#prules-rows').innerHTML = rows.map(([l, v]) =>
      `<div class="prule"><span>${l}</span><b>${v}</b></div>`).join('');
  }

  async function applyPricingCoupon() {
    const inp = $('#pcfg-coupon'), code = (inp.value || '').trim();
    inp.classList.remove('bad');
    if (!code) { cfgCoupon = null; cfgPct = 0; renderPricing(); return; }
    try {
      const r = await fetch('/api/coupon/' + encodeURIComponent(code));
      if (!r.ok) throw new Error();
      const d = await r.json();
      cfgCoupon = d.code; cfgPct = d.pct;
    } catch (e) { cfgCoupon = null; cfgPct = 0; inp.classList.add('bad'); }
    renderPricing();
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
      ['Leverage', 'Up to 1:100', ...col('Up to 1:50')],
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
    tweenNum($('#cfg-fee'), p.price_usd, v => '$' + fmt(Math.round(v)));
    /* Instant Funding has no phases — the row says so instead of printing
       a "+0% = $0" target. */
    $('#cfg-target-label').textContent = instant ? 'Profit target' : 'Profit target — Phase 1';
    if (instant) {
      $('#cfg-target').textContent = 'None — funded from day one';
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
    const applied = !!promoApplied();
    if (promo) {
      promo.hidden = !anyPromo;
      if (anyPromo) {
        promo.classList.toggle('is-max', applied && !big);
        promo.classList.toggle('applied', applied && !!big);
        if (!applied) {
          $('#cfg-promo-badge').textContent = 'Upgrade your size';
          $('#cfg-promo-val').innerHTML = 'Have a promo code? Get the <b>next size up — same fee</b>.';
          $('#cfg-promo-sub').innerHTML =
            '<button type="button" class="cfg-promo-link" id="cfg-promo-open">Apply your code</button> — the account is created one size up at checkout.';
          const open = $('#cfg-promo-open');
          if (open) open.addEventListener('click', openPromoInput);
        } else if (big) {
          $('#cfg-promo-badge').textContent = '✓ Promo applied';
          $('#cfg-promo-val').textContent = 'Code redeemed successfully!';
          $('#cfg-promo-sub').innerHTML =
            `Pay for ${sizeLabel(p.account_size)} — your account is created at <b>$${fmt(big)}</b>.`;
        } else {
          $('#cfg-promo-badge').textContent = 'Largest size';
          $('#cfg-promo-val').innerHTML = `${sizeLabel(p.account_size)} is our biggest account`;
          $('#cfg-promo-sub').textContent =
            'The size upgrade applies to every smaller plan.';
        }
      }
    }
    if (fine) {
      fine.textContent = anyPromo && applied
        ? '*You pay the price of the size you pick; with the promo applied we create the account one size up. Fee still refunded with your first payout.'
        : "*One-time fee, refunded with your first payout. Rewards depend on your trading.";
    }
    const cta = $('#cfg-cta');
    cta.href = '/portal?buy=' + encodeURIComponent(p.key);
    cta.textContent = `Start with ${sizeLabel(p.account_size)} → $${fmt(Math.round(p.price_usd))}`;
  }

  async function products() {
    if (!$('#pcfg') && !$('#cfg')) return;
    const wire = () => {
      const ap = $('#pcfg-apply'); if (ap) ap.addEventListener('click', applyPricingCoupon);
      const ci = $('#pcfg-coupon'); if (ci) ci.addEventListener('keydown', e => { if (e.key === 'Enter') applyPricingCoupon(); });
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
      if ($('#pcfg')) $('#pcfg').innerHTML = '<p class="muted">Could not load plans — please refresh.</p>';
    }
  }

  /* ---------- /api/leaderboard — the section hides when there is no data ---------- */
  async function board() {
    const sec = $('#lbSec'); if (!sec) return;
    try {
      const rows = (await (await fetch('/api/leaderboard')).json()).slice(0, 5);
      if (!rows.length) { sec.remove(); return; }
      $('#lbBody').innerHTML = rows.map((r, i) => `<tr>
        <td class="rank">${String(i + 1).padStart(2, '0')}</td>
        <td>${esc(r.trader)}${r.country ? ` <span class="muted" style="font-size:12px">· ${esc(r.country)}</span>` : ''}</td>
        <td class="mono muted">$${fmt(r.account_size)}</td>
        <td class="muted">${r.status === 'funded' ? 'Funded' : 'Evaluation'}</td>
        <td class="pl ${r.profit_pct >= 0 ? 'up' : 'down'}">${r.profit_pct >= 0 ? '+' : ''}${r.profit_pct.toFixed(2)}%</td>
      </tr>`).join('');
    } catch (e) { sec.remove(); }
  }
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
      /* Dwa rzedy: co druga karta idzie do dolnego, wiec oba jada innym zestawem.
         Kazdy rzad dostaje PARZYSTA liczbe kopii — animacja przesuwa o -50%,
         wiec po polowie cyklu klatka jest identyczna i petla nie skacze.
         Przy trzech certyfikatach jedna kopia nie wypelnilaby ekranu, dlatego
         liczba kopii wynika z pomiaru: szerokosc rzedu >= 2x szerokosc pasa. */
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
  stats(); products(); board(); certsStrip();
})();
