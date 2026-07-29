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

  /* ---------- ?ref= capture (partner code attaches at signup) ---------- */
  const ref = new URLSearchParams(location.search).get('ref');
  if (ref) try { localStorage.setItem('pf_ref', ref); } catch (e) {}

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
      $('#statsGrid').innerHTML = defs.map(d =>
        `<div class="stat"><div class="stat-num grad-text" data-count="${d.v}"${d.prefix ? ` data-prefix="${d.prefix}"` : ''}></div>
         <div class="stat-label">${d.label}</div></div>`).join('');
      $$('.stat-num', band).forEach(countUp);
    } catch (e) { band.remove(); }
  }

  /* ---------- /api/products — pricing + trading objectives ---------- */
  const GROUPS = [
    { id: '2step', name: '2-Step Evaluation', match: p => p.steps === 2 && p.price_usd > 0 },
    { id: '1step', name: '1-Step Evaluation', match: p => p.steps === 1 && p.price_usd > 0 },
    { id: 'instant', name: 'Instant Funding', match: p => p.steps === 0 && p.price_usd > 0 },
  ];
  let PRODUCTS = [], activeTab = '2step';

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
      ['Refundable fee', '<span class="ok">✓ With first payout</span>', ...col('—')],
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
  function tweenNum(el, to, fmtFn) {
    if (!el) return;
    const from = parseFloat(el.dataset.v || '0');
    el.dataset.v = to;
    if (reduced) { el.textContent = fmtFn(to); return; }
    const t0 = performance.now(), D = 340;
    (function step(t) {
      const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmtFn(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }
  const kfmt = n => n >= 1000 ? (n / 1000) + 'K' : String(n);
  let cfgStep = '2step', cfgKey = null;

  function renderConfigurator() {
    const root = $('#cfg'); if (!root) return;
    const gs = {
      '2step': PRODUCTS.filter(p => p.steps === 2 && p.price_usd > 0).sort((a, b) => a.account_size - b.account_size),
      '1step': PRODUCTS.filter(p => p.steps === 1 && p.price_usd > 0).sort((a, b) => a.account_size - b.account_size),
    };
    if (!gs['2step'].length && !gs['1step'].length) { root.remove(); return; }
    if (!gs[cfgStep].length) cfgStep = gs['2step'].length ? '2step' : '1step';
    const items = gs[cfgStep];
    if (!items.some(p => p.key === cfgKey)) {
      cfgKey = (items.find(p => p.account_size === 100000) || items[items.length - 1]).key;
    }
    $('#cfg-tabs').innerHTML = [['2step', '2-Step Evaluation'], ['1step', '1-Step Evaluation']]
      .filter(([id]) => gs[id].length)
      .map(([id, nm]) => `<button class="cfg-tab${id === cfgStep ? ' on' : ''}" data-step="${id}">${nm}<small>from $${fmt(Math.min(...gs[id].map(p => p.price_usd)))}</small></button>`).join('');
    $$('#cfg-tabs .cfg-tab').forEach(b => b.addEventListener('click', () => { cfgStep = b.dataset.step; cfgKey = null; renderConfigurator(); }));
    $('#cfg-sizes').innerHTML = items.map(p =>
      `<button class="cfg-size${p.key === cfgKey ? ' on' : ''}" data-key="${p.key}">$${kfmt(p.account_size)}</button>`).join('');
    $$('#cfg-sizes .cfg-size').forEach(b => b.addEventListener('click', () => { cfgKey = b.dataset.key; renderConfigurator(); }));

    const p = items.find(x => x.key === cfgKey);
    const share = p.account_size * 0.10 * p.profit_split_pct / 100;   // +10% × split
    tweenNum($('#cfg-fee'), p.price_usd, v => '$' + fmt(Math.round(v)));
    $('#cfg-target').innerHTML = `+${p.profit_target_p1}% = $<span id="cfg-tusd"></span>`;
    tweenNum($('#cfg-tusd'), p.account_size * p.profit_target_p1 / 100, v => fmt(Math.round(v)));
    $('#cfg-split').textContent = p.profit_split_pct + '%';
    tweenNum($('#cfg-payout'), share, v => '$' + fmt(Math.round(v)));
    $('#cfg-payout-sub').textContent = `+ $${fmt(p.price_usd)} challenge fee refunded with your first payout`;
    const cta = $('#cfg-cta');
    cta.href = '/portal?buy=' + encodeURIComponent(p.key);
    cta.textContent = `Start with $${kfmt(p.account_size)} → $${fmt(Math.round(p.price_usd))}`;
  }

  async function products() {
    if (!$('#pcfg') && !$('#cfg')) return;
    try {
      PRODUCTS = await (await fetch('/api/products')).json();
      renderPricing(); renderObjectives(); renderConfigurator();
      const ap = $('#pcfg-apply'); if (ap) ap.addEventListener('click', applyPricingCoupon);
      const ci = $('#pcfg-coupon'); if (ci) ci.addEventListener('keydown', e => { if (e.key === 'Enter') applyPricingCoupon(); });
      const wt = $('#pcfg-wt'); if (wt) wt.addEventListener('click', () => { cfgWt = !cfgWt; renderPricing(); });
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
  stats(); products(); board();
})();
