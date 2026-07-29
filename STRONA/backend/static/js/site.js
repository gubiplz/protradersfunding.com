/* Strona publiczna: nav, reveals, countery, cennik/objectives z /api/products,
   statystyki z /api/public/stats (kafelki zerowe UKRYWANE — bez lipnych liczb),
   ranking z /api/leaderboard (maskowany po stronie API). */
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
  if (burger) burger.addEventListener('click', () => mob.classList.toggle('open'));
  if (mob) $$('a', mob).forEach(a => a.addEventListener('click', () => mob.classList.remove('open')));

  /* ---------- ?ref= capture (kod partnera dopina się przy rejestracji) ---------- */
  const ref = new URLSearchParams(location.search).get('ref');
  if (ref) try { localStorage.setItem('pf_ref', ref); } catch (e) {}

  const yearEl = $('#year'); if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ---------- GSAP reveals + countery ---------- */
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

  /* ---------- mockup: paski postępu ---------- */
  setTimeout(() => $$('.mbar i').forEach(b => { b.style.width = (b.dataset.w || 0) + '%'; }), 500);

  /* ---------- /api/public/stats — kafelki tylko dla niezerowych metryk ---------- */
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
      if (defs.length < 2) { band.remove(); return; }   // świeża instalacja: nic nie udajemy
      $('#statsGrid').innerHTML = defs.map(d =>
        `<div class="stat"><div class="stat-num grad-text" data-count="${d.v}"${d.prefix ? ` data-prefix="${d.prefix}"` : ''}></div>
         <div class="stat-label">${d.label}</div></div>`).join('');
      $$('.stat-num', band).forEach(countUp);
    } catch (e) { band.remove(); }
  }

  /* ---------- /api/products — cennik + trading objectives ---------- */
  const GROUPS = [
    { id: '2step', name: '2-Step Evaluation', match: p => p.steps === 2 && p.price_usd > 0 },
    { id: '1step', name: '1-Step Evaluation', match: p => p.steps === 1 && p.price_usd > 0 },
    { id: 'instant', name: 'Instant Funding', match: p => p.steps === 0 && p.price_usd > 0 },
  ];
  let PRODUCTS = [], activeTab = '2step';

  function planCard(p, hot) {
    const size = '$' + fmt(p.account_size);
    return `<div class="plan${hot ? ' hot' : ''}" data-rv>
      ${hot ? '<div class="plan-badge">MOST POPULAR</div>' : ''}
      <div class="plan-size">${size}</div>
      <div class="plan-type">${p.steps === 0 ? 'Instant Funding — no evaluation' : p.steps === 1 ? '1-Step Evaluation' : '2-Step Evaluation'}</div>
      <div class="plan-price">$${fmt(p.price_usd)} <small>one-time</small></div>
      <div class="plan-refund">${p.steps === 0 ? '✓ Funded from day one' : '✓ Refunded with your first payout'}</div>
      <ul>
        <li>${p.steps === 0 ? 'No profit target — <b>trade and get paid</b>'
          : `Profit target <b>${p.profit_target_p1}%${p.steps > 1 ? ' / ' + p.profit_target_p2 + '%' : ''}</b>`}</li>
        <li>Max daily loss <b>${p.max_daily_loss_pct}%</b></li>
        <li>Max overall loss <b>${p.max_overall_loss_pct}% ${p.drawdown_type}</b></li>
        <li>Max open volume <b>${p.max_lots} lots</b></li>
        <li>Profit split up to <b>${p.profit_split_pct}%</b></li>
        <li>Real MT5 account · news &amp; weekend trading <b>allowed</b></li>
      </ul>
      <a class="btn" href="/portal?buy=${encodeURIComponent(p.key)}">Start Challenge</a>
    </div>`;
  }

  function renderPlans() {
    const g = GROUPS.find(g => g.id === activeTab);
    const items = PRODUCTS.filter(g.match).sort((a, b) => a.account_size - b.account_size);
    const hotIdx = items.length > 2 ? Math.floor(items.length / 2) : -1;
    $('#plans').innerHTML = items.map((p, i) => planCard(p, i === hotIdx)).join('') ||
      '<p class="muted">No plans available right now.</p>';
    revealize($('#plans'));
  }

  function renderTabs() {
    const withItems = GROUPS.filter(g => PRODUCTS.some(g.match));
    $('#tabs').innerHTML = withItems.map(g =>
      `<button class="tab${g.id === activeTab ? ' on' : ''}" data-tab="${g.id}">${g.name}</button>`).join('');
    $$('#tabs .tab').forEach(b => b.addEventListener('click', () => {
      activeTab = b.dataset.tab; renderTabs(); renderPlans();
    }));
  }

  function renderObjectives() {
    const body = $('#objBody'); if (!body) return;
    const g2 = PRODUCTS.filter(p => p.steps === 2 && p.price_usd > 0);
    const g1 = PRODUCTS.filter(p => p.steps === 1 && p.price_usd > 0);
    if (!g2.length || !g1.length) return;
    const r2 = g2[g2.length - 1], r1 = g1[g1.length - 1];
    const range = (arr, f) => {
      const vs = [...new Set(arr.map(f))].sort((a, b) => a - b);
      return vs.length > 1 ? vs[0] + '–' + vs[vs.length - 1] : String(vs[0]);
    };
    const gi = PRODUCTS.filter(p => p.steps === 0 && p.price_usd > 0);
    const ri = gi.length ? gi[gi.length - 1] : null;
    const col = (v) => ri ? [v] : [];
    const rows = [
      ['Profit target — Phase 1', r2.profit_target_p1 + '%', r1.profit_target_p1 + '%', ...col('<span class="ok">none</span>')],
      ['Profit target — Phase 2', r2.profit_target_p2 + '%', '—', ...col('—')],
      ['Maximum daily loss', r2.max_daily_loss_pct + '%', r1.max_daily_loss_pct + '%', ...col(ri && ri.max_daily_loss_pct + '%')],
      ['Maximum overall loss', r2.max_overall_loss_pct + '%', r1.max_overall_loss_pct + '%', ...col(ri && ri.max_overall_loss_pct + '%')],
      ['Drawdown type', cap(r2.drawdown_type), cap(r1.drawdown_type), ...col(ri && cap(ri.drawdown_type))],
      ['Max open volume', r2.max_lots + ' lots', r1.max_lots + ' lots', ...col(ri && ri.max_lots + ' lots')],
      ['Minimum trading days', range(g2, p => p.min_trading_days), range(g1, p => p.min_trading_days), ...col(ri && String(ri.min_trading_days))],
      ['News trading', '<span class="ok">✓ Allowed</span>', '<span class="ok">✓ Allowed</span>', ...col('<span class="ok">✓ Allowed</span>')],
      ['Weekend holding', '<span class="ok">✓ Allowed</span>', '<span class="ok">✓ Allowed</span>', ...col('<span class="ok">✓ Allowed</span>')],
      ['Profit split', 'up to ' + Math.max(...g2.map(p => p.profit_split_pct)) + '%', 'up to ' + Math.max(...g1.map(p => p.profit_split_pct)) + '%', ...col(ri && ri.profit_split_pct + '%')],
      ['Refundable fee', '<span class="ok">✓ With first payout</span>', '<span class="ok">✓ With first payout</span>', ...col('—')],
      ['One-time fee', 'from ' + money(Math.min(...g2.map(p => p.price_usd))), 'from ' + money(Math.min(...g1.map(p => p.price_usd))), ...col(ri && 'from ' + money(Math.min(...gi.map(p => p.price_usd))))],
    ];
    body.innerHTML = rows.map(r => '<tr>' + r.map((c, i) => `<td>${c}</td>`).join('') + '</tr>').join('');
    const head = document.querySelector('#objectives thead tr');
    if (head && ri && head.children.length === 3) {
      const th = document.createElement('th'); th.textContent = 'Instant Funding'; head.appendChild(th);
    }
  }
  const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;

  /* ---------- hero: konfigurator challenge'u ---------- */
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
    if (!$('#plans') && !$('#cfg')) return;
    try {
      PRODUCTS = await (await fetch('/api/products')).json();
      renderTabs(); renderPlans(); renderObjectives(); renderConfigurator();
    } catch (e) {
      if ($('#plans')) $('#plans').innerHTML = '<p class="muted">Could not load plans — please refresh.</p>';
    }
  }

  /* ---------- /api/leaderboard — sekcja znika, gdy brak danych ---------- */
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
  const vf = $('#verifyForm');
  if (vf) vf.addEventListener('submit', e => {
    e.preventDefault();
    const v = $('#verifyInput').value.trim();
    if (v) location.href = '/verify/' + encodeURIComponent(v);
  });

  /* ---------- init ---------- */
  revealize(document);
  $$('.stat-num[data-count]').forEach(countUp);
  stats(); products(); board();
})();
