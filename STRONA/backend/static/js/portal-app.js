const $=id=>document.getElementById(id);
const fmt=n=>(n??0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmt0=n=>(n??0).toLocaleString('en-US',{maximumFractionDigits:0});
/* Catalog models: 0 = Instant Funding, 2 = 2-Step (1-Step is legacy, kept for old accounts). */
const planKind=s=>s===0?'Instant':s===1?'1-Step':'2-Step';
/* Baza oddaje nagie UTC (bez "Z") — new Date() wziąłby to za czas lokalny.
   Doklejamy "Z" i renderujemy w strefie przeglądarki: klient widzi SWÓJ czas. */
const dutc=iso=>new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso||'')?iso:iso+'Z');
const dstr=iso=>dutc(iso).toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
// Date only — trade history has no time component. `YYYY-MM-DD` is read as
// UTC so the browser timezone does not shift the day back by one.

const dday=d=>new Date(d+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
/* Impersonacja z panelu admina: token przyjeżdża w ?impersonate= i mieszka
   w sessionStorage (per KARTA) — localStorage jest wspólny dla wszystkich
   kart, więc zapis tam nadpisałby prawdziwą sesję właściciela w /admin. */
let IMP=null;
try{
  const _qi=new URLSearchParams(location.search);
  if(_qi.get('impersonate')){
    sessionStorage.setItem('pf_imp',_qi.get('impersonate'));
    _qi.delete('impersonate');
    history.replaceState(null,'',location.pathname+(_qi.toString()?'?'+_qi:'')+location.hash);
  }
  IMP=sessionStorage.getItem('pf_imp');
}catch(e){}
let TOKEN=IMP||localStorage.getItem('pf_token'), ME=null, AUTHMODE='login', chart=null, anCharts=[], CURV=null;
/* Ostatni stan programu lojalnosciowego z /api/me/loyalty (punkty, nagrody). */
let LOY=null;
const H=()=>TOKEN?{'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'}:{'Content-Type':'application/json'};
/* Sieć bywa mobilna: twardy timeout 15 s (AbortController) + JEDEN retry, ale
   wyłącznie dla GET i wyłącznie po błędzie sieci/timeoucie. Odpowiedź HTTP —
   nawet 500 — nigdy nie jest ponawiana: POST /checkout nie może się zdublować. */
async function api(path,opts={},_retry){
  const ctl=new AbortController(),tm=setTimeout(()=>ctl.abort(),15000);
  let r;
  try{r=await fetch(path,{headers:H(),...opts,signal:ctl.signal})}
  catch(e){
    clearTimeout(tm);
    if(!_retry&&(opts.method||'GET').toUpperCase()==='GET')return api(path,opts,1);
    throw new Error(e&&e.name==='AbortError'?'Request timed out — check your connection':'Network error — check your connection');
  }
  clearTimeout(tm);
  /* 401 przy ZAŁOŻONEJ sesji = token wygasł/unieważniony. W standalone PWA nie
     ma paska adresu, więc „Try again" na widoku nigdy by nie pomogło — jedyna
     droga wyjścia to od razu ekran logowania. 401 bez tokenu (złe hasło przy
     logowaniu) przechodzi niżej normalną ścieżką błędu. */
  if(r.status===401&&TOKEN){sessionExpired();throw new Error('Your session has expired — please sign in again')}
  if(!r.ok){throw new Error((await r.json().catch(()=>({}))).detail||r.status)}
  return r.json();
}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/* Anty-zawieszka przycisków: gasnie na czas fn(), finally ZAWSZE przywraca —
   restore tylko w catchu zostawiał martwy przycisk, gdy wywrócił się success
   path. Drugi klik w trakcie = no-op. fn zwraca 'keep' => przycisk zostaje
   wyłączony (np. redirect do Stripe, gdzie strona zaraz znika). */
async function busy(btn,label,fn){
  if(!btn)return fn();
  if(btn.disabled)return;
  const html=btn.innerHTML;
  btn.disabled=true;if(label)btn.textContent=label;
  let keep=false;
  try{const w=await fn();keep=(w==='keep');return w}
  finally{if(!keep&&btn.isConnected){btn.disabled=false;btn.innerHTML=html}}
}
/* "Upgrade Your Size" promo code applied on the public site (or typed in the buy
   modal). Shared key with the landing page — one applied state everywhere. */
function pfPromo(){try{return localStorage.getItem('pf_promo_code')||''}catch(e){return ''}}
/* Discount coupon applied on the landing promo bar — prefills the buy modal. */
function pfCoupon(){try{return localStorage.getItem('pf_coupon_code')||''}catch(e){return ''}}
/* Referral code captured from a ?ref= visit. Prefills signup ONLY when the
   visit was recent (90 days) — entries without a timestamp (legacy) are
   dropped, a months-old partner code kept resurfacing in the form. */
function pfRef(){
  try{
    const c=localStorage.getItem('pf_ref'),ts=+localStorage.getItem('pf_ref_ts')||0;
    if(!c)return '';
    if(!ts||Date.now()-ts>90*864e5){localStorage.removeItem('pf_ref');localStorage.removeItem('pf_ref_ts');return ''}
    return c;
  }catch(e){return ''}
}
function clearRef(){try{localStorage.removeItem('pf_ref');localStorage.removeItem('pf_ref_ts')}catch(e){}}

/* ---------- theme (light by default; "dark" persisted in pf_theme2) ---------- */
const THEME_SUN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.4M12 19.6V22M2 12h2.4M19.6 12H22M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M19.1 4.9l-1.7 1.7M6.6 17.4l-1.7 1.7"/></svg>';
const THEME_MOON='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.4 14.2A8.5 8.5 0 0 1 9.8 3.6 8.5 8.5 0 1 0 20.4 14.2z"/></svg>';
function themeNow(){return document.documentElement.dataset.theme==='dark'?'dark':'light'}
function paintTheme(){
  const t=themeNow();
  /* One painter for every toggle placement (sidebar bottom, "More" sheet). */
  document.querySelectorAll('.theme-toggle').forEach(b=>{
    b.innerHTML=(t==='dark'?THEME_SUN:THEME_MOON)
      +'<span class="sb-txt">'+(t==='dark'?'Light mode':'Dark mode')+'</span>';
    b.title=t==='dark'?'Switch to light mode':'Switch to dark mode';
  });
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',t==='dark'?'#0b0d12':'#f6f7fb');
  const sw=$('s-theme'); if(sw)sw.checked=(t==='dark');
}
function toggleTheme(){
  const next=themeNow()==='dark'?'light':'dark';
  document.documentElement.dataset.theme=next;
  try{localStorage.setItem('pf_theme2',next)}catch(e){}
  paintTheme();
  /* Charts snapshot token colors at render time — repaint what is on screen
     (the account detail is not a VIEWS entry, so it re-opens itself). */
  if(ME){if(window._onDetail&&window._accId)openAcc(window._accId);else if(CURV)go(CURV)}
}
paintTheme();

/* ---------- cookie notice (essential cookies only; shared key with the site) ----------
   The banner is fixed to the bottom and on a phone it is ~175px tall — tall
   enough to sit right on top of the sign-up form's terms checkbox and its
   Create account button, so taps landed on the notice and nothing happened.
   --ck-h gives that strip back to the page as padding (portal.css). */
function ckSpace(){
  const el=$('cookie-note');
  const open=el&&!el.classList.contains('hidden');
  document.documentElement.style.setProperty('--ck-h',
    open?Math.ceil(innerHeight-el.getBoundingClientRect().top)+'px':'0px');
}
function cookieOk(v){try{localStorage.setItem('pf_cookie_ok',v?'1':'0')}catch(e){}
  $('cookie-note').classList.add('hidden');ckSpace()}
try{if(localStorage.getItem('pf_cookie_ok')===null)$('cookie-note').classList.remove('hidden')}catch(e){}
ckSpace();addEventListener('resize',ckSpace);addEventListener('load',ckSpace);

/* ---------- live refresh + push deep links ---------- */
/* Fresh ME (credits, streak) + bell badge without a page reload. Re-render of
   the current view only on demand (pull-to-refresh / push navigation) or when
   the credit balance changed — no screen flashing every minute. */
let _refreshing=false; /* re-entrancy: minutowy interval + visibilitychange potrafią się nałożyć */
async function refreshLive(rerender=false){
  if(!TOKEN||!ME||_refreshing)return;
  _refreshing=true;
  refreshNotif();
  try{
    const stare=ME.credits_usd;
    ME=await api('/api/auth/me');
    if(!rerender&&ME.credits_usd!==stare)rerender=true;
  }catch(e){}
  finally{_refreshing=false}
  if(rerender&&window._view&&VIEWS[window._view])go(window._view);
}
/* Push-click target stored by sw.js in Cache Storage: read on startup AND on
   every return to the app — iOS can drop a postMessage to a suspended page,
   so this is the only reliable path. */
async function pendingNavView(){
  let v=window._pendingView;window._pendingView=null;
  try{
    const c=await caches.open('pf-nav');const r=await c.match('/__pending-nav');
    if(r){const d=await r.json();await c.delete('/__pending-nav');
      if(!v&&Date.now()-d.ts<30000)v=new URL(d.url,location.origin).searchParams.get('view')}
  }catch(_){}
  return v||null;
}
async function applyPendingNav(){
  const v=await pendingNavView();
  if(!v||!VIEWS[v])return;
  if(ME){go(v);refreshLive()}else window._pendingView=v; /* boot() finishes it */
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible')return;
  refreshLive();
  applyPendingNav();setTimeout(applyPendingNav,600); /* retry: the SW write may still be in flight */
});
setInterval(()=>{if(document.visibilityState==='visible')refreshLive()},60000);

/* Telemetria produktowa (fire-and-forget): otwarcie portalu + instalacja PWA. */
function track(name,props){try{
  if(TOKEN)api('/api/telemetry',{method:'POST',body:JSON.stringify({name,props:props||{}})}).catch(()=>{})
}catch(e){}}
addEventListener('appinstalled',()=>track('pwa_install'));

/* Globalny łapacz błędów JS → telemetria (drill-down w adminie). Dedupe po
   treści + limit 5/sesję, fire-and-forget — raportowanie nie może samo
   wywrócić appki ani zaspamować bazy pętlą błędów. */
const _jsErrSeen=new Set();
function reportJsError(msg,src){
  try{
    const key=String(msg||'unknown').slice(0,80);
    if(!TOKEN||_jsErrSeen.has(key)||_jsErrSeen.size>=5)return;
    _jsErrSeen.add(key);
    track('js_error',{msg:key,src:String(src||'').slice(0,80),view:String(window._view||'')});
  }catch(e){}
}
addEventListener('error',e=>reportJsError(e.message,(e.filename||'')+':'+(e.lineno||0)));
addEventListener('unhandledrejection',e=>reportJsError(e.reason&&e.reason.message||e.reason,'promise'));

/* Push click with the portal open: sw.js does postMessage instead of
   navigate() (a reload loses SPA state). Top-level listener + startMessages()
   — without it the spec QUEUES SW messages forever (addEventListener alone
   does not unblock the queue; only the onmessage setter or startMessages). */
if('serviceWorker' in navigator){
  navigator.serviceWorker.addEventListener('message',e=>{
    const d=e.data||{};
    if(d.type!=='navigate')return;
    try{caches.open('pf-nav').then(c=>c.delete('/__pending-nav'))}catch(_){}
    let v=null;
    try{v=new URL(d.url,location.origin).searchParams.get('view')}catch(_){}
    if(!v||!VIEWS[v])return;
    /* ME is still loading (iOS waking the PWA) => boot() finishes the navigation */
    if(ME){go(v);refreshLive()}else window._pendingView=v;
  });
  navigator.serviceWorker.startMessages?.();
}

/* Pull-to-refresh (mobile PWA): a >70px drag while scrolled to the top. */
(function(){
  let y0=null,pulled=false;
  const top=()=>((document.scrollingElement||document.documentElement).scrollTop||0)<=0;
  document.addEventListener('touchstart',e=>{
    y0=(ME&&top()&&e.touches.length===1)?e.touches[0].clientY:null;pulled=false;
  },{passive:true});
  document.addEventListener('touchmove',e=>{
    if(y0===null||pulled)return;
    if(e.touches[0].clientY-y0>70&&top()){
      pulled=true;
      const s=document.createElement('div');s.className='ptr-spin';s.id='ptr-spin';
      document.body.appendChild(s);
      Promise.resolve(refreshLive(true)).finally(()=>setTimeout(()=>$('ptr-spin')?.remove(),400));
    }
  },{passive:true});
  document.addEventListener('touchend',()=>{y0=null},{passive:true});
})();

const ICO={
  copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M20 6 9 17l-5-5"/></svg>',
  key:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="8" cy="14" r="4.2"/><path d="M11 11 20 2M16 6l3 3"/></svg>',
  dollar:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2v20M17 6.5c0-2-2.2-3-5-3s-5 1-5 3 2 2.8 5 3.4 5 1.6 5 3.6-2.2 3-5 3-5-1-5-3"/></svg>',
  trend:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
  layers:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 2 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></svg>',
  wallet:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="6" width="20" height="14" rx="3"/><path d="M2 10h20M16 15h2"/></svg>',
  alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 2 20h20z"/><path d="M12 9.5V14M12 17h.01"/></svg>',
  target:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="2"/></svg>',
  cal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M8 2.5V7M16 2.5V7M3 10.5h18"/></svg>',
  eye:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.7"/></svg>',
  spark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2l1.8 5.6L19 9l-5.2 1.4L12 16l-1.8-5.6L5 9l5.2-1.4z"/><path d="M19 15l.9 2.6L22 18l-2.1.7L19 21l-.9-2.3L16 18l2.1-.4z"/></svg>',
  trophy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 6H4v1.5A3.5 3.5 0 0 0 7.5 11M17 6h3v1.5A3.5 3.5 0 0 1 16.5 11"/></svg>',
  crown:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 8 4.5 4L12 5l4.5 7L21 8l-1.6 10.5a1 1 0 0 1-1 .5H5.6a1 1 0 0 1-1-.5z"/></svg>',
  book:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19V5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z"/><path d="M19 17H6a2 2 0 0 0-2 2"/><path d="M9 7h6M9 11h4"/></svg>',
  bars:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  gift:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 12.5h18M12 8c-2.5 0-4.5-1.3-4.5-3S9.5 2.6 12 5c2.5-2.4 4.5-1.7 4.5 0S14.5 8 12 8z"/></svg>',
  payout:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="8" width="20" height="12" rx="2.5"/><circle cx="12" cy="14" r="2.6"/><path d="M6 8V6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/></svg>',
  file:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></svg>',
  shield:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  help:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M9.2 9a3 3 0 0 1 5.8 1c0 2-3 2.4-3 4"/><path d="M12 17.5h.01"/></svg>',
  gear:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10.3 4.3a2 2 0 0 1 3.4 0l.6 1a2 2 0 0 0 1.7 1h1.2a2 2 0 0 1 1.7 3l-.6 1a2 2 0 0 0 0 2l.6 1a2 2 0 0 1-1.7 3h-1.2a2 2 0 0 0-1.7 1l-.6 1a2 2 0 0 1-3.4 0l-.6-1a2 2 0 0 0-1.7-1H6.8a2 2 0 0 1-1.7-3l.6-1a2 2 0 0 0 0-2l-.6-1a2 2 0 0 1 1.7-3H8a2 2 0 0 0 1.7-1z"/><circle cx="12" cy="12" r="2.6"/></svg>',
  medal:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="14.5" r="5.5"/><path d="m8.5 10 -3-7M15.5 10l3-7M9.5 3h5"/><path d="m12 12 .9 1.8 2 .3-1.4 1.4.3 2-1.8-1-1.8 1 .3-2-1.4-1.4 2-.3z"/></svg>',
  chat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.8A8 8 0 1 1 21 12z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></svg>',
  print:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 9V3h12v6M6 17H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="7"/></svg>',
  grid:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="7" r="2.5"/><circle cx="7" cy="17" r="2.5"/><circle cx="17" cy="17" r="2.5"/></svg>',
  flame:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 22c4.4 0 7-2.9 7-6.5 0-2.5-1.3-4.5-2.8-6.1-.4 1-1 1.9-1.9 2.5.2-2.9-1.1-6.4-4.3-7.9.3 2.9-1.1 4.5-2.6 6.1C6 11.6 5 13.4 5 15.5 5 19.1 7.6 22 12 22z"/><path d="M12 22c1.9 0 3.2-1.3 3.2-3 0-1.4-.9-2.3-1.6-3.3-1 1-3.6 1.8-3.6 3.5 0 1.5 1.1 2.8 3 2.8z"/></svg>',
};

/* Kazda odznaka ma WLASNA ikone — wczesniej osiem kart pokazywalo ten sam medal,
   wiec roznily sie wylacznie tekstem. Mapa siedzi w widoku, bo ikona to sprawa
   prezentacji: serwer oddaje `key`, a nie SVG. */
const BADGE_ICO={
  first_challenge:ICO.dollar, phase_passed:ICO.target, funded:ICO.crown,
  first_payout:ICO.payout,    days_5:ICO.cal,         scaled:ICO.trend,
  referrer:ICO.chat,          kyc:ICO.shield,
};

const NAV=[
  {v:'accounts',label:'Challenges',ico:'trend'},
  {v:'board',label:'Leaderboard',ico:'trophy'},
  {v:'achievements',label:'Achievements',ico:'medal'},
  {v:'loyalty',label:'Loyalty',ico:'crown'},
  {v:'journal',label:'Journal',ico:'book'},
  {v:'analytics',label:'Analytics',ico:'bars'},
  {v:'rewards',label:'Rewards',ico:'gift'},
  {v:'payouts',label:'Payouts',ico:'payout'},
  {v:'certificates',label:'Certificates',ico:'medal'},
  {v:'orders',label:'Invoice',ico:'file'},
  {v:'kyc',label:'KYC',ico:'shield'},
  {v:'support',label:'Support',ico:'help'},
  {v:'settings',label:'Settings',ico:'gear'},
];
$('side-nav').innerHTML='<div class="side-sec sb-txt">Main menu</div>'+NAV.map(n=>
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}')" title="${n.label}">${ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`).join('')+
  `<a class="sb-link" href="/academy" target="_blank" rel="noopener" title="Academy">${ICO.book}<span class="sb-txt">Academy</span></a>`;

/* ---------- mobile tab bar + "More" sheet ---------- */
const TABS=[
  {v:'accounts',label:'Home',ico:'trend'},
  {v:'board',label:'Board',ico:'trophy'},
  {v:'rewards',label:'Rewards',ico:'gift'},
  {v:'payouts',label:'Payouts',ico:'payout'},
];
$('tabbar').innerHTML=TABS.map(t=>
  `<button class="tab-item" data-v="${t.v}" onclick="go('${t.v}')"><span class="tab-ic">${ICO[t.ico]}${t.v==='rewards'?'<span class="tab-dot hidden"></span>':''}</span><span class="tab-lbl">${t.label}</span></button>`).join('')+
  `<button class="tab-item" data-v="more" onclick="openSheet()"><span class="tab-ic">${ICO.grid}</span><span class="tab-lbl">More</span></button>`;
$('sheet-nav').innerHTML=NAV.filter(n=>!TABS.some(t=>t.v===n.v)).map(n=>
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}');closeSheet()">${ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`).join('')+
  `<a class="sb-link" href="/academy" target="_blank" rel="noopener" onclick="closeSheet()">${ICO.book}<span class="sb-txt">Academy</span></a>
   <div class="sheet-div"></div>
   <button class="sb-link theme-toggle" onclick="toggleTheme()"></button>
   <button class="sb-link sheet-signout" onclick="logout()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3"/></svg><span class="sb-txt">Sign Out</span></button>`;
paintTheme();   /* the sheet's toggle was just rendered — give it its icon/label */
function openSheet(){$('sheetVeil').classList.remove('hidden');$('sheet').classList.add('open')}
function closeSheet(){$('sheetVeil').classList.add('hidden');$('sheet').classList.remove('open')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSheet()});

/* ---------- toasts ---------- */
function toast(msg,kind='ok',ms=6000){
  const t=document.createElement('div');
  t.className='toast '+kind; t.textContent=msg;
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),350)},ms);
}

/* ---------- auth ---------- */
/* Back arrow on the auth screen: real browser-back when the visitor came from
   our own site (pricing, FAQ…), otherwise the landing page. */
function authBack(e){
  try{
    if(document.referrer&&new URL(document.referrer).origin===location.origin&&history.length>1){
      e.preventDefault();history.back();return false;
    }
  }catch(err){}
  return true;   /* follow href="/" */
}

function authTab(m){AUTHMODE=m;
  const login=m==='login',signup=m==='signup',forgot=m==='forgot',reset=m==='reset';
  $('tab-login').classList.toggle('on',login);
  $('tab-signup').classList.toggle('on',signup);
  $('a-name').classList.toggle('hidden',!signup);
  $('a-ref').classList.toggle('hidden',!signup);
  $('a-terms-row').classList.toggle('hidden',!signup);
  $('a-pass').classList.toggle('hidden',forgot);
  $('a-pass2').classList.toggle('hidden',!(reset||signup));
  $('a-pass2').placeholder=signup?'Repeat password':'Repeat new password';
  $('a-email').classList.toggle('hidden',reset);
  $('a-forgot').classList.toggle('hidden',!login);
  $('a-back').classList.toggle('hidden',!(forgot||reset));
  document.querySelector('.auth-tabs').classList.toggle('hidden',forgot||reset);
  const gAuth=document.getElementById('g-auth');
  /* In an app's built-in browser the whole Google block stays away — Google
     will not sign anyone in there, so offering it is offering a dead end.
     Its place is taken by the one-line way out (#g-inapp). */
  const wApce=inAppBrowser();
  if(gAuth){gAuth.classList.toggle('hidden',forgot||reset||wApce);
    if(!(forgot||reset||wApce))renderGoogleBtn()}
  const wyjscie=document.getElementById('g-inapp');
  if(wyjscie)wyjscie.classList.toggle('hidden',!wApce||forgot||reset);
  $('a-pass').placeholder=reset?'New password (min. 8 characters)':signup?'Password (min. 8 characters)':'Password';
  /* Password managers must OFFER a generated password in signup/reset and the
     saved one only in login — the wrong hint makes them autofill the old one. */
  $('a-pass').setAttribute('autocomplete',(signup||reset)?'new-password':'current-password');
  if(signup&&!$('a-ref').value){$('a-ref').value=pfRef()}
  $('a-submit').textContent=login?'Log in':signup?'Create account':forgot?'Send reset link':'Set new password';
  $('auth-title').textContent=login?'Welcome back':signup?'Create your account':forgot?'Reset your password':'Choose a new password';
  $('auth-sub').textContent=login?'Log in to your trader dashboard.'
    :signup?'One account for challenges, payouts and the affiliate program.'
    :forgot?'Enter your account e-mail. We will send you a reset link.'
    :'Set a new password for your account.';
}
async function doAuth(){
  const btn=$('a-submit');
  if(btn.disabled)return;                    /* double submit = double POST */
  const label=btn.textContent;
  btn.disabled=true;btn.textContent='Please wait…';
  const unlock=()=>{btn.disabled=false;btn.textContent=label};
  if(AUTHMODE==='forgot'){
    try{const r=await api('/api/auth/forgot',{method:'POST',body:JSON.stringify({email:$('a-email').value})});
      toast(r.message||'Reset link sent. Check your inbox.','ok',8000);authTab('login');
    }catch(e){toast('Error: '+e.message,'err')}
    unlock();return;
  }
  if(AUTHMODE==='reset'){
    const p1=$('a-pass').value,p2=$('a-pass2').value;
    if(p1!==p2){toast('Passwords do not match.','err');unlock();return}
    try{const r=await api('/api/auth/reset',{method:'POST',body:JSON.stringify({token:window._resetToken,password:p1})});
      window._resetToken=null;history.replaceState(null,'','/portal');
      $('a-pass').value='';$('a-pass2').value='';
      /* The reset response signs the user straight in — no re-typing. */
      if(r.token){TOKEN=r.token;localStorage.setItem('pf_token',TOKEN);
        toast('Password changed. Welcome back!','ok',7000);unlock();await boot();return}
      toast('Password changed. Log in with your new password.','ok',8000);authTab('login');
    }catch(e){toast('Error: '+e.message,'err',9000);
      /* "already set" = marker z backendu: hasło już istnieje (np. drugi mail
         przy BOGO niesie własny, martwy już token) — formularz resetu jest
         wtedy ślepą uliczką, więc od razu pokazujemy logowanie. */
      if(/already set/i.test(String(e.message))){
        window._resetToken=null;history.replaceState(null,'','/portal');authTab('login');
      }
    }
    unlock();return;
  }
  /* Checked here so the customer sees it next to the field instead of getting
     a bare error toast back from the server. */
  ['a-name','a-email'].forEach(clearFieldErr);
  if(AUTHMODE==='signup'){
    /* Signup only. Refusing to even TRY a login because the address looks odd
       to us would lock out anyone whose account predates this rule. */
    const mail=emailCheck($('a-email').value);
    if(!mail.ok){fieldErr('a-email',mail.msg);unlock();return}
  }
  if(AUTHMODE==='signup'&&($('a-name').value||'').trim()){
    const nazwa=nameCheck($('a-name').value,'Full name');
    if(!nazwa.ok){fieldErr('a-name',nazwa.msg);unlock();return}
  }
  if(AUTHMODE==='signup'&&$('a-pass').value!==$('a-pass2').value){
    toast('Passwords do not match.','err');unlock();return}
  if(AUTHMODE==='signup'&&!$('a-terms').checked){
    toast('Please accept the Terms of Service and Privacy Policy.','err');unlock();return}
  const body={email:$('a-email').value,password:$('a-pass').value};
  try{let res;
    if(AUTHMODE==='signup'){body.full_name=$('a-name').value;body.referral=$('a-ref').value||null;
      body.terms_accepted=$('a-terms').checked;res=await api('/api/auth/signup',{method:'POST',body:JSON.stringify(body)});
      clearRef();  /* attribution consumed — the code must not resurface later */}
    else res=await api('/api/auth/login',{method:'POST',body:JSON.stringify(body)});
    TOKEN=res.token;localStorage.setItem('pf_token',TOKEN);
    // An administrator account has ONLY the admin panel — the trader portal
    // is never rendered for it, no matter how the login started.
    if(res.trader&&res.trader.is_admin){location.replace('/admin');return}
    // There is ONE login — the admin panel redirects here with ?next=/admin and
    // after signing in we go exactly back there instead of losing the user here.
    const nextUrl=new URLSearchParams(location.search).get('next');
    if(nextUrl && nextUrl.startsWith('/') && !nextUrl.startsWith('//')){location.href=nextUrl;return}
    unlock();await boot();
  }catch(e){toast('Error: '+e.message,'err');unlock()}
}
/* ---------- Sign in with Google (GIS; only when the server has a client id) ---------- */
/* Google refuses OAuth in browsers embedded in other apps ("disallowed_useragent"),
   so opening our link from Telegram or Instagram leads to a button that cannot
   work no matter what we do here. We cannot fix that — we can say it. */
function inAppBrowser(){
  const ua=navigator.userAgent||'';
  if(/FBAN|FBAV|FB_IAB|Instagram|Line\/|Snapchat|Twitter|MicroMessenger|Telegram|; wv\)/i.test(ua))return true;
  /* iOS: real Safari always carries "Safari/" in the UA, an embedded WKWebView
     does not. An installed PWA looks the same, hence the standalone check —
     there Google works fine. */
  return /iPhone|iPad|iPod/.test(ua)&&/AppleWebKit/.test(ua)
    &&!/Safari\//.test(ua)&&!navigator.standalone;
}
/* Independent of GIS loading at all — inside those browsers Google's script is
   often blocked too, and then nothing would ever explain the empty spot. */
const IOS=/iPhone|iPad|iPod/.test(navigator.userAgent);
/* authTab() decides WHETHER the line shows; here we only word it for the phone
   in hand. Runs before the first authTab() call, which happens inside boot(). */
if(inAppBrowser()&&$('g-escape'))$('g-escape').textContent=IOS?'open in Safari':'open in your browser';
/* The only way to reach Google from here is to LEAVE this browser, so hand the
   address to the operating system: iOS answers x-safari-https:// with Safari,
   Android answers intent:// with whatever it uses for https. No package is
   pinned — not everyone has Chrome — and browser_fallback_url means a phone
   that cannot resolve the intent at least reloads the page instead of erroring. */
function browserEscapeUrl(){
  const url=location.href;
  if(IOS)return'x-safari-'+url;
  return'intent://'+url.replace(/^https?:\/\//,'')
    +'#Intent;scheme=https;S.browser_fallback_url='+encodeURIComponent(url)+';end';
}
/* Neither scheme is guaranteed — an app may swallow it — so if we are still
   here a moment later, the button turns into the copy fallback. */
function openInBrowser(){
  const btn=$('g-escape');
  location.href=browserEscapeUrl();
  setTimeout(()=>{
    if(document.hidden||!btn)return;
    btn.textContent='Did not open? Copy the link';
    btn.onclick=()=>copyPortalLink(btn);
  },1500);
}
async function copyPortalLink(btn){
  const url=location.href;
  try{await navigator.clipboard.writeText(url);toast('Link copied. Paste it in Safari or Chrome.','ok',7000)}
  catch(e){
    /* Bez uprawnien do schowka zostaje stara droga: zaznaczony tekst do recznego
       skopiowania. Lepsze to niz komunikat „nie udalo sie". */
    const pole=document.createElement('input');
    pole.value=url;pole.setAttribute('readonly','');
    pole.style.cssText='position:fixed;left:12px;right:12px;width:auto;z-index:95;font-size:16px;padding:10px';
    document.body.appendChild(pole);pole.select();pole.setSelectionRange(0,url.length);
    try{document.execCommand('copy');toast('Link copied. Paste it in Safari or Chrome.','ok',7000)}
    catch(e2){toast('Copy the address from the bar above and open it in Safari.','err',8000)}
    setTimeout(()=>pole.remove(),400);
  }
}
function initGoogle(){
  if(!GOOGLE_CLIENT_ID||!window.google||!google.accounts)return;
  google.accounts.id.initialize({client_id:GOOGLE_CLIENT_ID,callback:onGoogleCred});
  renderGoogleBtn();
}
function renderGoogleBtn(){
  const holder=document.getElementById('g-btn');
  if(!holder||!window.google||!google.accounts)return;
  holder.innerHTML='';
  google.accounts.id.renderButton(holder,{
    theme:document.documentElement.dataset.theme==='dark'?'filled_black':'outline',
    size:'large',shape:'pill',width:Math.min(holder.offsetWidth||320,380),
    /* the label follows the active tab — one button serves both flows;
       locale pinned: the whole site is English, GIS must not localize */
    locale:'en',
    text:AUTHMODE==='signup'?'signup_with':'signin_with'});
}
async function onGoogleCred(resp){
  try{
    const res=await api('/api/auth/google',{method:'POST',
      body:JSON.stringify({credential:resp.credential,referral:pfRef()||null})});
    clearRef();
    TOKEN=res.token;localStorage.setItem('pf_token',TOKEN);
    if(res.trader&&res.trader.is_admin){location.replace('/admin');return}
    const nextUrl=new URLSearchParams(location.search).get('next');
    if(nextUrl&&nextUrl.startsWith('/')&&!nextUrl.startsWith('//')){location.href=nextUrl;return}
    await boot();
  }catch(e){toast('Error: '+e.message,'err')}
}

async function verifyEmailCode(){
  const c=$('vg-code').value.trim();
  if(c.length!==6){toast('Enter the 6-digit code from the e-mail.','err');return}
  try{await api('/api/me/verify-email',{method:'POST',body:JSON.stringify({code:c})});
    ME.email_verified=true;$('verify-gate').classList.add('hidden');
    toast('E-mail confirmed ✅','ok',8000);
    await boot();
  }catch(e){toast('Error: '+e.message,'err')}
}
async function resendVerify(btn){
  if(btn&&btn.disabled)return;
  try{await api('/api/me/verify-email/resend',{method:'POST'});
    toast('New code sent. Check your inbox.','ok',8000);
    if(btn){/* cooldown — serwer i tak limituje, ale nie kuśmy klikaniem */
      btn.disabled=true;let s=30;const t0=btn.textContent;
      const iv=setInterval(()=>{s--;btn.textContent=s>0?`Resend code (${s}s)`:t0;
        if(s<=0){clearInterval(iv);btn.disabled=false}},1000);
      btn.textContent=`Resend code (${s}s)`}
  }catch(e){toast('Error: '+e.message,'err')}
}
function vgToggleChange(on){
  $('vg-change').classList.toggle('hidden',!on);
  if(on)$('vg-new-mail').focus();
}
async function changeVerifyEmail(){
  const em=$('vg-new-mail').value.trim();
  if(!em){toast('Enter the correct e-mail address.','err');return}
  try{
    const r=await api('/api/me/verify-email/change-address',{method:'POST',body:JSON.stringify({email:em})});
    ME.email=r.email;$('vg-mail').textContent=r.email;
    $('vg-new-mail').value='';vgToggleChange(false);
    toast('Address updated. A new code is on its way to '+r.email,'ok',8000);
  }catch(e){toast('Error: '+e.message,'err')}
}
function logout(){
  /* Tryb podglądu: NIE wolno dotknąć localStorage (sesja właściciela) ani
     /api/auth/logout (skasowałby cookie serwera bramkujące /admin). */
  if(IMP){try{sessionStorage.removeItem('pf_imp')}catch(e){}location.reload();return}
  // The session cookie is held by the server (it gates /admin), so clearing
  // localStorage alone would not be enough.
  fetch('/api/auth/logout',{method:'POST'}).finally(()=>{
    TOKEN=null;localStorage.removeItem('pf_token');location.reload()});
}
/* Token wygasł w trakcie sesji. Bez location.reload(): offline reload w PWA
   zostawiłby gołą stronę błędu Safari zamiast naszego ekranu logowania.
   Flaga gasi lawinę równoległych 401 (kilka widoków odpytuje naraz);
   boot() zdejmuje ją po ponownym zalogowaniu. */
let _expired=false;
function sessionExpired(){
  if(_expired)return;_expired=true;
  /* Wygasł token PODGLĄDU (2 h) — wracamy do własnej sesji, nie ruszając
     tokenu właściciela w localStorage. */
  if(IMP){try{sessionStorage.removeItem('pf_imp')}catch(e){}location.reload();return}
  TOKEN=null;ME=null;
  try{localStorage.removeItem('pf_token')}catch(e){}
  $('app').classList.add('hidden');$('verify-gate')?.classList.add('hidden');
  $('auth').classList.remove('hidden');
  authTab('login');loadAuthStats();
  toast('Your session has expired. Please sign in again.','err',8000);
}
/* Żółty pasek trybu podglądu — przypomina, że klikane akcje (check-in,
   zakupy, zgłoszenia) dzieją się NAPRAWDĘ na koncie klienta. */
function impBanner(){
  if($('imp-bar'))return;
  const b=document.createElement('div');b.id='imp-bar';
  b.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#7a5d00;color:#fff;padding:8px 14px;font-size:13px;display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap';
  b.innerHTML=`<span>Admin preview — signed in as <b>${esc(ME.email)}</b>. Actions here are real.</span>
    <button style="background:none;border:1px solid #fff;color:#fff;border-radius:8px;padding:4px 12px;cursor:pointer" onclick="logout()">Exit preview</button>`;
  document.body.appendChild(b);
}
/* Zimny start bez zasięgu NIE wylogowuje: token zostaje w localStorage,
   klient dostaje pełnoekranowe „Try again". Wcześniej KAŻDY błąd /api/auth/me
   (także timeout w metrze) kasował token i wymuszał ponowne logowanie. */
function bootRetry(msg){
  let el=$('boot-retry');
  if(!el){
    el=document.createElement('div');el.id='boot-retry';
    el.innerHTML='<div class="br-card"><h3>Can’t reach the server</h3><p></p>'
      +'<button class="btn-p">Try again</button></div>';
    el.querySelector('button').onclick=()=>{el.remove();boot()};
    document.body.appendChild(el);
  }
  el.querySelector('p').textContent=msg||'Check your connection and try again.';
}
/* Benefit numbers on the login screen — REAL platform stats only; each card
   renders only past an honesty threshold, an empty strip simply stays hidden. */
async function loadAuthStats(){
  try{
    const s=await api('/api/public/stats');
    const items=[];
    if(s.traders_total>=25)items.push([fmt0(s.traders_total),'Traders on board']);
    if(s.funded_accounts>=5)items.push([fmt0(s.funded_accounts),'Funded accounts']);
    if(s.payouts_total_usd>=1000)items.push(['$'+fmt0(s.payouts_total_usd),'Paid in rewards']);
    if(s.countries_count>=5)items.push([fmt0(s.countries_count),'Countries']);
    if(!items.length)return;
    const el=$('auth-stats');
    el.innerHTML=items.slice(0,3).map(([v,l])=>`<div class="as-card"><b>${v}</b><span>${l}</span></div>`).join('');
    el.classList.remove('hidden');
  }catch(e){}
}

/* ---------- return from payment ---------- */
async function handlePaymentReturn(){
  const q=new URLSearchParams(location.search);
  const mo=q.get('mock_order');
  /* Parametry powrotu z płatności są JEDNORAZOWE. Bez replaceState zostawały
     w adresie, a iOS po ubiciu appki przeładowuje bieżący URL — klient widział
     „Payment received… account is being created" tygodnie po zakupie. */
  if(q.get('paid')||q.get('canceled')||mo)history.replaceState(null,'','/portal');
  if(q.get('paid')){toast('✅ Payment received. Your MT5 account is being created. Credentials will appear under Challenges and in your inbox.','ok',9000);go('accounts');return true}
  if(q.get('canceled')){toast('Payment canceled. Nothing was charged.','err');return false}
  if(mo){try{
    const prov=await api(`/api/checkout/${mo}/mock-complete`,{method:'POST'});
    toast(prov.provisioning?'✅ Payment received. Your MT5 account is being created (up to a minute).':'✅ Account created. Credentials under Challenges.','ok',9000);
    go('accounts');return true
  }catch(e){toast('Error: '+e.message,'err')}}
  return false;
}

/* ---------- boot / navigation ---------- */
async function boot(){
  const q0=new URLSearchParams(location.search);
  /* E-mail verification deep link: works without logging in (token from mail). */
  const vt=q0.get('verify');
  if(vt){
    try{await api('/api/auth/verify-email',{method:'POST',body:JSON.stringify({token:vt})});
      toast('E-mail confirmed ✅','ok',8000)}
    catch(e){toast('Verification failed: '+e.message,'err',8000)}
    history.replaceState(null,'','/portal');
  }
  const rt=q0.get('reset');
  if(rt){window._resetToken=rt;
    $('auth').classList.remove('hidden');$('app').classList.add('hidden');authTab('reset');loadAuthStats();return}
  /* Z maila z poswiadczeniami MT5: klient, ktory nie ma hasla do portalu, ma
     wejsc od razu na ekran resetu. Bez tego lezy na logowaniu i musi jeszcze
     znalezc „Forgot password". Adres w ?email= to jego wlasny adres. */
  if(q0.get('forgot')){
    $('auth').classList.remove('hidden');$('app').classList.add('hidden');
    authTab('forgot');
    const em=q0.get('email');if(em)$('a-email').value=em;
    loadAuthStats();return}
  if(!TOKEN){
    $('auth').classList.remove('hidden');$('app').classList.add('hidden');
    /* Wejscie z cennika (?buy=) to najczesciej NOWY klient — startujemy od
       rejestracji i mowimy wprost, ze zamowienie czeka tuz za tym krokiem. */
    authTab(q0.get('buy')?'signup':'login');
    if(q0.get('buy'))$('auth-buynote').classList.remove('hidden');
    loadAuthStats();return}
  _expired=false; /* świeże logowanie — łapacz 401 znowu uzbrojony */
  try{ME=await api('/api/auth/me')}
  catch(e){
    /* 401: sessionExpired() w api() już wyczyścił token i pokazał logowanie.
       Wszystko inne to sieć/timeout — token ZOSTAJE, dajemy przycisk Retry. */
    if(!TOKEN)return;
    bootRetry(e&&e.message);return;
  }
  /* Admin accounts live in /admin only. The server already bounces them off
     /portal by the session cookie; this covers the leftover state where the
     cookie is gone but the localStorage token still works. */
  if(ME.is_admin){location.replace('/admin');return}
  /* Hard gate: an unconfirmed address never reaches the dashboard. */
  if(ME.email_verified===false){
    $('auth').classList.add('hidden');$('app').classList.add('hidden');
    $('vg-mail').textContent=ME.email;
    $('verify-gate').classList.remove('hidden');
    $('vg-code').focus();
    return;
  }
  $('verify-gate').classList.add('hidden');
  $('auth').classList.add('hidden');$('app').classList.remove('hidden');
  if(IMP)impBanner();
  const nm=(ME.full_name||ME.email).trim();
  $('ava').textContent=nm[0].toUpperCase();
  $('who-name').textContent=ME.full_name||'Trader';
  $('who-mail').textContent=ME.email;
  if(localStorage.getItem('pf_side_collapsed')==='1')$('side').classList.add('collapsed');
  /* Portal wstrzymany do weryfikacji (kyc_locked): serwer odpowiada 403 na
     wszystko poza KYC, supportem i wlasnym profilem, wiec pelny panel pokazalby
     same bledy. Wychodzimy z boota przed warstwa engagementu — jej zapytania
     tez sie odbija, a kartka z passa nie jest tym, co ten klient ma teraz
     zobaczyc. */
  if(ME.kyc_locked&&ME.kyc_status!=='approved'){wstrzymajPortal();go('kyc');return}
  initEngagement();
  flagsWarm();
  if(await handlePaymentReturn())return;
  const q=new URLSearchParams(location.search);
  /* Deep links from notifications: _pendingView / fresh sw.js entry in Cache
     Storage (wins over a STALE ?view= left in the address after a previous
     deep link), finally ?view= from the URL (cold start via openWindow). */
  const widok=(await pendingNavView())||q.get('view');
  if(q.get('upsell')==='1')window._upsellJump=true;
  /* ?view/?upsell skonsumowane — bez sprzątnięcia każdy kolejny reload PWA
     skakał do starego deep linka. ?buy zostaje: konsumuje go (i czyści)
     highlightPlanFromUrl() dopiero przy renderze sklepu. */
  if(q.get('view')||q.get('upsell'))
    history.replaceState(null,'','/portal'+(q.get('buy')?'?buy='+encodeURIComponent(q.get('buy')):''));
  /* view_open leci z go() — kazda nawigacja, nie tylko start appki */
  go(q.get('buy')?'store':(widok&&VIEWS[widok]?widok:'accounts'));
  refreshNotif();
  maybeReviewNudge();
}

/* Zostawiamy dokladnie dwie zakladki: te, ktora zdejmuje blokade (KYC), i te,
   ktora pozwala o nia dopytac (Support). Pozycje nawigacji USUWAMY, zamiast je
   wygaszac — link, ktory po kliknieciu oddaje blad, wyglada jak zepsuta appka,
   a nie jak swiadoma pauza. */
function wstrzymajPortal(){
  const wolne=['kyc','support'];
  document.querySelectorAll('#side-nav .sb-link[data-v],#sheet-nav .sb-link[data-v]')
    .forEach(b=>{if(!wolne.includes(b.dataset.v))b.remove()});
  $('tabbar').classList.add('hidden');
  $('bell-btn')?.classList.add('hidden');
  $('streakChip')?.classList.add('hidden');
  document.querySelector('.top-right .btn-p')?.classList.add('hidden');
}

/* Kampania "free challenge": wybrane konta (lista na serwerze — review_nudge
   z /api/auth/me) dostają po zalogowaniu przypomnienie o opinii na Trustpilot.
   Raz na 3 dni; znacznik w localStorage liczy się od pokazania, nie od kliku,
   żeby zamknięcie tłem nie pokazywało popupu przy każdym wejściu. */
function maybeReviewNudge(){
  if(!ME||!ME.review_nudge)return;
  const last=+localStorage.getItem('pf_tp_nudge')||0;
  if(Date.now()-last<3*86400000)return;
  localStorage.setItem('pf_tp_nudge',String(Date.now()));
  const w=document.createElement('div'); w.id='tp-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Enjoying your funded account?</h3></div>
    <p class="muted" style="font-size:13px;margin:2px 0 6px">Your challenge account was set up for you free of charge. If you like how it's going, a short Trustpilot review helps other traders find us — it takes a minute.</p>
    <div style="font-size:20px;letter-spacing:3px;color:#00b67a;margin:4px 0 12px">★★★★★</div>
    <div style="display:flex;gap:10px;margin-top:6px">
      <button class="btn-p" onclick="window.open('https://www.trustpilot.com/review/protradersfunding.com','_blank','noopener');$('tp-modal').remove()">Leave a review</button>
      <button class="btn-o" onclick="$('tp-modal').remove()">Maybe later</button>
    </div></div>`;
  document.body.appendChild(w);
}

const TITLES={
  accounts:['Challenges','Live overview of your challenge accounts'],
  store:['New Challenge','One-time fee · refunded with your first payout'],
  board:['Leaderboard','Top traders across the platform, all time, live data'],
  achievements:['Achievements','Milestones earned from your real activity'],
  loyalty:['Loyalty','Trade your points for a discount code'],
  journal:['Journal','Your private trading notes'],
  analytics:['Analytics','Daily P&L computed from your account history'],
  rewards:['Rewards','Programs built into the platform'],
  payouts:['Payouts','Performance rewards across all your accounts'],
  certificates:['Certificates','Your verifiable documents: evaluation stages and payouts'],
  orders:['Invoice','Payment history, delivered accounts and invoices'],
  kyc:['KYC','Identity verification, required once before your first payout'],
  support:['Support','Get help from our team'],
  settings:['Settings','Manage your account settings and preferences'],
};
/* Ten sam ekran przejscia co w panelu admina — patrz `.view-load` w portal.css. */
const LOADING_HTML=(h=240)=>`<div class="view-load">
  <div class="skel" style="height:110px;margin-bottom:16px"></div>
  <div class="skel" style="height:${h}px"></div>
  <div class="vl-mid"><span class="vl-ring"></span><span class="vl-txt">Loading…</span></div>
</div>`;

/* Numer przelaczenia — patrz ten sam mechanizm w admin-panel.js. Wolniejszy
   POPRZEDNI widok potrafi skonczyc sie po zmianie zakladki i nadpisac nowy. */
let PRZEJSCIE = 0;

function go(v){
  CURV=v; window._view=v; window._onDetail=false;
  /* Nazwa widoku w propsach: dziennik w adminie rozpisuje z tego, co klient
     faktycznie przegladal, nie tylko ze "otworzyl portal". */
  track('view_open',{view:v,pwa:document.documentElement.classList.contains('pwa')?'1':'0'});
  document.querySelectorAll('.sb-link[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  const tv={store:'accounts',achievements:'rewards',loyalty:'rewards'}[v]||v;
  const tabs=[...document.querySelectorAll('.tab-item[data-v]')];
  const hit=tabs.some(b=>b.dataset.v===tv);
  tabs.forEach(b=>b.classList.toggle('on',b.dataset.v===(hit?tv:'more')));
  const t=TITLES[v]||['',''];
  $('pg-title').textContent=t[0]; $('pg-crumb').textContent=t[1];
  toggleSide(false);
  $('notif-panel')?.classList.add('hidden');
  const moj=++PRZEJSCIE;
  $('view').innerHTML=LOADING_HTML();
  Promise.resolve(VIEWS[v]())
    .then(()=>{if(moj!==PRZEJSCIE&&VIEWS[CURV])VIEWS[CURV]()})
    .catch(e=>{
      /* Blad zostaje W widoku z przyciskiem ponowienia (wzor admina) — bez
         catcha na ekranie wisial wieczny szkielet ladowania. Zgloszenie do
         telemetrii, zeby blad nie zniknal razem z konsola uzytkownika. */
      reportJsError(e&&e.message||e,'view:'+v);
      if(moj!==PRZEJSCIE)return;
      $('view').innerHTML=`<div class="empty"><h3>Couldn't load this view</h3>
        <p>${esc(e&&e.message||'Something went wrong')}</p>
        <button class="btn-p" style="margin-top:14px" onclick="go('${v}')">Try again</button></div>`;
    });
}
function toggleSide(open){
  $('side').classList.toggle('open',open);
  document.body.classList.toggle('nav-open',open);
}
/* scrim + Escape close the drawer (mobile only — the scrim lives in the <=960 MQ) */
document.addEventListener('click',e=>{
  if(!document.body.classList.contains('nav-open'))return;
  /* .botnav: the "More" button has just opened the drawer with this same click —
     without the exception the bubbling event would close it immediately */
  if(e.target.closest('#side')||e.target.closest('.burger')||e.target.closest('.botnav'))return;
  toggleSide(false);
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&document.body.classList.contains('nav-open'))toggleSide(false);
});
function toggleCollapse(){
  const c=$('side').classList.toggle('collapsed');
  localStorage.setItem('pf_side_collapsed',c?'1':'0');
}

/* Deep link from pricing: /portal?buy=2step-100k -> straight to the purchase modal. */
function highlightPlanFromUrl(){
  const key=new URLSearchParams(location.search).get('buy');
  if(!key)return;
  /* Jednorazowy deep link: bez sprzątnięcia każdy reload PWA znowu otwierał
     modal zakupu. Czyścimy TU (nie w boot), bo sklep renderuje się async. */
  history.replaceState(null,'','/portal');
  const card=document.querySelector(`[data-plan="${CSS.escape(key)}"]`);
  if(!card)return;
  card.classList.add('hl');
  card.scrollIntoView({behavior:'smooth',block:'center'});
  openBuy(key);
}

/* ================= PHASE 2 — engagement layer =================
   Hooks run only after ME loads; every network call is wrapped so a
   failing engagement endpoint never blocks boot. Nothing here reacts
   to trade events — only open / check-in / milestone renders. */
window.RFX=window.RFX||{reduced:true,confetti(){},burstFrom(){},pop(){},celebrate(){},GOLD:[],
  rollUp(el,v,o){if(el)el.textContent=((o||{}).prefix||'')+(((o||{}).formatter)?o.formatter(v):v)},
  flip(el,swap){if(swap)swap();return Promise.resolve()}};

const utcToday=()=>new Date().toISOString().slice(0,10);
function revealDot(show){document.querySelectorAll('.tab-dot').forEach(d=>d.classList.toggle('hidden',!show))}

async function initEngagement(){
  try{
    const chip=$('streakChip');
    chip.innerHTML=ICO.flame+`<b id="streakN">${ME.checkin_streak||0}</b>`;
    chip.classList.remove('hidden');
    revealDot(ME.reveal_last!==utcToday());
    try{
      const r=await api('/api/me/checkin',{method:'POST'});
      if(!r.already){
        ME.checkin_streak=r.streak; ME.checkin_last=utcToday(); ME.streak_freezes=r.freezes;
        $('streakN').textContent=r.streak;
        RFX.pop(chip);
        if(r.freeze_used)toast('Streak freeze used. Your '+r.streak+'-day streak survived');
        if(r.reward&&r.reward.type==='points'){
          ME.bonus_points=(ME.bonus_points||0)+r.reward.amount;
          RFX.burstFrom(chip,{count:30});
          toast('Streak bonus: +'+r.reward.amount+' loyalty points');
        }
      }
    }catch(e){}
    checkNewAchievements();
  }catch(e){}
}

/* dramatic reveal of achievements unlocked since the last visit */
async function checkNewAchievements(){
  try{
    const list=await api('/api/me/achievements');
    const key=b=>String(b.id??b.key??b.name);
    const unlocked=list.filter(b=>b.unlocked).map(key);
    const seenRaw=localStorage.getItem('pf_ach_seen');
    if(!seenRaw){localStorage.setItem('pf_ach_seen',JSON.stringify(unlocked));return}  // first run: seed silently
    const seen=JSON.parse(seenRaw);
    const fresh=list.filter(b=>b.unlocked&&!seen.includes(key(b)));
    fresh.forEach(b=>RFX.celebrate({icon:ICO[b.icon]||ICO.medal,title:b.name,sub:'ACHIEVEMENT UNLOCKED',toastFn:toast}));
    if(fresh.length)localStorage.setItem('pf_ach_seen',JSON.stringify(unlocked));
  }catch(e){}
}

/* roll-up on plain money stats only (e.g. "$1,234.56", "+$80.00") */
function rollStats(){
  document.querySelectorAll('#view .stat-tile .val,#view .wide-card .val').forEach((el,i)=>{
    const m=el.textContent.trim().match(/^([+-]?)\$([\d,]+(?:\.\d+)?)$/);
    if(!m)return;
    const v=parseFloat(m[2].replace(/,/g,''));
    setTimeout(()=>RFX.rollUp(el,v,{prefix:(m[1]||'')+'$',formatter:m[2].includes('.')?fmt:fmt0}),i*80);
  });
}

/* discipline milestones: celebrate LOGGED TRADING DAYS, never P&L progress —
   day count can't be accelerated by trading bigger, so the reward reinforces
   consistency, not risk appetite. Max one per render, keyed per account+phase. */
function checkMilestone(id,a,m){
  const days=m.trading_days||0, min=m.min_trading_days||0;
  if(!min)return;
  const key=`pf_days_${id}_${a.phase||''}`;
  const prevRaw=localStorage.getItem(key);
  localStorage.setItem(key,String(days));
  if(prevRaw===null)return;                     // first look at this phase: seed silently
  const prev=parseInt(prevRaw,10);
  if(days<=prev)return;
  if(prev<min&&days>=min){
    RFX.celebrate({icon:ICO.cal,title:'Days requirement complete',sub:'DISCIPLINE MILESTONE',toastFn:toast});
  }else if(days<min){
    RFX.burstFrom(document.querySelector('.wide-card .tile-ic.orange'),{count:40});
    RFX.pop(document.querySelector('.wide-card .val'));
    toast(`Trading day ${days} of ${min} logged. Consistency wins`);
  }
}

/* ---------- daily reveal ---------- */
const TIPS=[
  'Risk a fixed fraction, 0.5-1% per trade. Survival first, profits second.',
  'No setup, no trade. Boredom is not a signal.',
  'Journal every trade. The pattern you refuse to see is the one costing you money.',
  'Two losses in a row? Step away for an hour. The market will still be there.',
  'Size down after a losing week — earn the right to size back up.',
  'Skip the first 15 minutes after a red-folder news release.',
  'Your daily loss limit is a hard stop, not a suggestion.',
  'Trade the session you can actually watch. London open is useless if you are asleep.',
  'One good trade a day compounds faster than ten forced ones.',
  'Revenge trading turns one loss into three. Close the platform, not the gap.',
  'Define the invalidation before entry. If you cannot, you have no trade.',
  'Move stops to protect capital, never to give a loser more room.',
  'Drawdown is tuition. Review the losers weekly and stop repeating the lesson.',
  'Pass the challenge at marathon pace: small consistent days beat one hero day.',
  'Correlated pairs are one trade in disguise. Count exposure, not tickets.',
  'Plan the trade when the market is closed. Execute when it is open. Never invert.',
  'If the lot size makes your heart race, it is too big — halve it.',
  'Win rate means nothing without average R. Track both.',
  'Cut losers at plan, let winners hit target. Discipline is symmetric.',
  'Near your drawdown limit, A+ setups only. Protect the account first.',
  'Three green days do not make you invincible. Same size, same rules.',
  'Spreads widen around news. A perfect entry with a 5-pip spread is not perfect.',
  'Weekend gaps ignore stops. Flatten swing risk before Friday close.',
  'Consistency passes challenges. Intensity blows them up.',
];
function revealBack(){
  return `<img class="reveal-logo" src="/static/img/logo.png" alt="">
    <div class="reveal-tag">Daily reveal</div>
    <div class="reveal-t">Trading tips, bonus points or a rare coupon. One flip per day</div>
    <div class="reveal-hint">Tap to flip</div>`;
}
function revealFace(p){
  if(p.type==='tip')return `<div class="reveal-tag">Today's edge</div>
    <blockquote class="reveal-quote">${esc(TIPS[(p.index||0)%TIPS.length])}</blockquote>
    <div class="reveal-hint">New reveal tomorrow</div>`;
  if(p.type==='points')return `<div class="reveal-tag">Bonus drop</div>
    <div class="reveal-big mono">+${p.amount}</div>
    <div class="reveal-t">loyalty points added to your account</div>
    <div class="reveal-hint">New reveal tomorrow</div>`;
  if(p.type==='freeze')return `<div class="reveal-tag">Streak insurance</div>
    <div class="reveal-ic-big">${ICO.shield}</div>
    <div class="reveal-t"><b>+1 Streak Freeze</b> — if you miss a day, your streak survives automatically</div>
    <div class="reveal-hint">New reveal tomorrow</div>`;
  return `<div class="reveal-tag">Rare drop · -${p.pct}% next challenge</div>
    <div class="reveal-code"><code>${esc(p.code)}</code>
      <button class="copy" onclick="event.stopPropagation();copyVal(this,'${esc(p.code)}')" title="Copy">${ICO.copy}</button></div>
    <div class="reveal-t">valid 48h, personal, works only on your account</div>`;
}
function paintReveal(p){
  const c=$('revealCard'); if(!c)return;
  c.classList.add('face-up');
  c.classList.toggle('reveal-rare',p.type==='coupon');
  c.onclick=null; c.removeAttribute('role'); c.style.cursor='default';
  c.innerHTML=revealFace(p);
}
async function doReveal(){
  const c=$('revealCard');
  if(!c||c.classList.contains('face-up')||c.dataset.busy)return;
  c.dataset.busy='1';
  try{
    const p=await api('/api/me/daily-reveal',{method:'POST'});
    localStorage.setItem('pf_reveal_cache',JSON.stringify({date:utcToday(),payload:p}));
    ME.reveal_last=utcToday(); revealDot(false);
    await RFX.flip(c,()=>paintReveal(p),600);
    if(!p.already){
      if(p.type==='points'){ME.bonus_points=(ME.bonus_points||0)+p.amount;RFX.burstFrom(c,{count:50})}
      if(p.type==='freeze'){ME.streak_freezes=(ME.streak_freezes||0)+1;RFX.burstFrom(c,{count:40})}
      if(p.type==='coupon')RFX.burstFrom(c,{count:80,palette:RFX.GOLD});
    }
  }catch(e){toast('Error: '+e.message,'err')}
  finally{delete c.dataset.busy}
}
/* Prowizja afiliacyjna -> kredyty sklepowe (min $10, pilnuje serwer). */
async function claimAffiliate(btn){
  await busy(btn,'Claiming…',async()=>{
    try{
      const r=await api('/api/me/affiliate/claim',{method:'POST'});
      ME=await api('/api/auth/me');
      toast(`✅ $${fmt(r.claimed_usd)} added to your store credit.\nIt applies automatically at checkout.`,'ok',7000);
      go('rewards');
      return 'keep';
    }catch(e){toast('Claim failed: '+e.message,'err',6000)}
  });
}

function initRevealCard(){
  if(ME.reveal_last!==utcToday())return;       // stays face-down until claimed
  let p=null;
  try{const j=JSON.parse(localStorage.getItem('pf_reveal_cache')||'null');if(j&&j.date===utcToday())p=j.payload}catch(e){}
  if(p){paintReveal(p);return}
  api('/api/me/daily-reveal',{method:'POST'}).then(r=>{  // same-day call replays the cached payload
    localStorage.setItem('pf_reveal_cache',JSON.stringify({date:utcToday(),payload:r}));
    paintReveal(r);
  }).catch(()=>{});
}

/* ---------- push opt-in banner (dashboard entry) + notification center ---------- */
function pushSupported(){return 'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window}
function iosNeedsInstall(){
  const ios=/iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone=matchMedia('(display-mode: standalone)').matches||navigator.standalone;
  return ios&&!standalone&&!pushSupported();
}
async function pushCfg(){
  if(window._pushCfg===undefined){
    try{window._pushCfg=await api('/api/push/public-key')}catch(e){window._pushCfg={enabled:false,key:null}}
  }
  return window._pushCfg;
}
/* Opt-in banner on dashboard entry: only when the server has keys, permission
   was never asked, and the trader didn't dismiss it in the last 14 days. */
function pushBannerHtml(){
  try{
    if(!window._pushCfg||!window._pushCfg.enabled)return '';
    if(Date.now()-parseInt(localStorage.getItem('pf_push_ask_ts')||'0')<14*864e5)return '';
    if(iosNeedsInstall())return `<div class="gradient-banner" id="push-banner">
      <span class="gb-tag">🔔 Notifications</span><span class="gb-sep"></span>
      <span class="gb-txt">On iPhone/iPad: <a href="/install" style="color:inherit"><b>install the
        app</b></a> (Share → Add to Home Screen), then enable notifications in Settings.</span>
      <button class="gb-x" onclick="dismissPush()" aria-label="Not now">✕</button></div>`;
    if(!pushSupported()||Notification.permission!=='default')return '';
    return `<div class="gradient-banner" id="push-banner">
      <span class="gb-tag">🔔 Notifications</span><span class="gb-sep"></span>
      <span class="gb-txt">Know the moment you <b>pass a phase</b> or a <b>payout lands</b>,
        max one scheduled message a day.</span>
      <button class="gb-btn" onclick="enablePush(this)">Enable ›</button>
      <button class="gb-x" onclick="dismissPush()" aria-label="Not now">✕</button></div>`;
  }catch(e){return ''}
}
async function enablePush(btn){
  await busy(btn,'Enabling…',async()=>{
    try{
      const cfg=await pushCfg();
      if(!cfg.enabled)throw new Error('push is not configured yet');
      const reg=await navigator.serviceWorker.ready;
      if(await Notification.requestPermission()!=='granted')throw new Error('permission was not granted');
      const sub=await reg.pushManager.subscribe({userVisibleOnly:true,
        applicationServerKey:b64ToU8(cfg.key)});
      await api('/api/me/push/subscribe',{method:'POST',body:JSON.stringify(sub.toJSON())});
      document.getElementById('push-banner')?.remove();
      toast('🔔 Notifications enabled on this device.','ok');
    }catch(e){toast('Could not enable notifications: '+e.message,'err')}
  });
}
function dismissPush(){
  try{localStorage.setItem('pf_push_ask_ts',String(Date.now()))}catch(e){}
  document.getElementById('push-banner')?.remove();
}

async function refreshNotif(){
  try{
    window._notif=await api('/api/me/notifications?limit=20');
    const b=$('bell-badge'); if(!b)return;
    const n=window._notif.unread||0;
    b.textContent=n>9?'9+':n;
    b.classList.toggle('hidden',!n);
  }catch(e){}
}
function toggleNotif(){
  const p=$('notif-panel'); if(!p)return;
  if(!p.classList.contains('hidden')){p.classList.add('hidden');return}
  const r=window._notif||{items:[],unread:0};
  p.innerHTML=`<div class="notif-head"><b>Notifications</b>
      ${r.unread?`<button class="linklike" onclick="readNotif()">Mark all read</button>`:''}</div>`
    +((r.items&&r.items.length)?r.items.map(n=>{
      const kiedy=n.created_at?dutc(n.created_at)
        .toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';
      return `<a class="notif-row${n.read?'':' unread'}" href="${esc(n.url||'/portal')}" onclick="return notifGo(this)">
        <div class="t">${esc(n.title)}</div>${n.body?`<div class="b">${esc(n.body)}</div>`:''}
        <div class="d">${kiedy}</div></a>`}).join('')
      :'<div class="notif-empty">Nothing yet. Pass a phase and it lands here.</div>');
  p.classList.remove('hidden');
  if(r.unread)readNotif(false);
}
/* Bell row: switch the SPA view instead of a full navigation; with no
   matching view fall back to the row's URL. */
function notifGo(a){
  let v=null,up=false;
  try{const u=new URL(a.getAttribute('href'),location.origin);
    v=u.searchParams.get('view'); up=u.searchParams.get('upsell')==='1'}catch(_){}
  if(v&&VIEWS[v]){if(up)window._upsellJump=true;go(v);return false}
  return true;
}
/* Deep link z powiadomienia „Scale your progress": po wejsciu w Challenges
   przewijamy do panelu i podswietlamy go na moment — inaczej user laduje na
   gorze listy i nie wie, po co go tu przenieslismy. Gdy panelu nie ma (wynik
   przestal byc dodatni), zostaje po prostu na liscie kont. */
function flashUpsell(){
  const el=document.querySelector('.upsell');
  if(!el)return;
  el.scrollIntoView({behavior:'smooth',block:'center'});
  el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');
  setTimeout(()=>el.classList.remove('flash'),2800);
}
async function readNotif(rerender=true){
  try{await api('/api/me/notifications/read',{method:'POST'});await refreshNotif()}catch(e){}
  if(rerender)$('notif-panel')?.classList.add('hidden');
}
document.addEventListener('click',e=>{
  const p=document.getElementById('notif-panel');
  if(!p||p.classList.contains('hidden'))return;
  if(e.target.closest('#notif-panel')||e.target.closest('#bell-btn'))return;
  p.classList.add('hidden');
});

/* Installed app (standalone): the PWA scope is /portal — everything else
   (landing, /verify, certificates, legal pages) is the WEBSITE. A plain
   navigation would swallow it inside the app window with no browser UI, so
   any link leaving /portal (or /admin, which is the other app screen) is
   routed through window.open and the OS hands it to the browser. */
document.addEventListener('click',e=>{
  if(e.defaultPrevented)return;                    /* in-app handlers won */
  if(!document.documentElement.classList.contains('pwa'))return;
  const a=e.target.closest&&e.target.closest('a[href]');
  if(!a)return;
  if((a.getAttribute('href')||'').startsWith('#')||a.protocol==='javascript:')return;
  const inApp=a.origin===location.origin&&/^\/(portal|admin)(\/|$)/.test(a.pathname);
  if(inApp)return;
  e.preventDefault();
  window.open(a.href,'_blank','noopener');
});

/* UI preferences live on the account (ME.ui_prefs). Always mutate the SAME
   object sortable.js uses — PATCH /api/me replaces the whole blob, so a copy
   would clobber saved table sorts (and vice versa). */
let _prefT=null;
function setUiPref(key,val){
  if(!ME)return;
  ME.ui_prefs=ME.ui_prefs||{};
  if(val==null)delete ME.ui_prefs[key];else ME.ui_prefs[key]=val;
  clearTimeout(_prefT);
  _prefT=setTimeout(()=>{api('/api/me',{method:'PATCH',body:JSON.stringify({ui_prefs:ME.ui_prefs})}).catch(()=>{})},800);
}
function chalFilter(f){setUiPref('chalFilter',f==='all'?null:f);VIEWS.accounts()}

/* Same-level-upgrade banner: applies the live Upgrade code (one promo state
   shared with the landing — coupons cleared) and opens the buy modal. */
async function upgradeNow(key){
  try{
    const d=await api('/api/promo');
    if(d&&d.code){localStorage.setItem('pf_promo_code',d.code);
      localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct')}
  }catch(e){}
  openBuy(key);
}

/* ============================ VIEWS ============================ */
const VIEWS={
 async accounts(){
  /* Trzy niezalezne zapytania szly SZEREGOWO — kazde czekalo na zakonczenie
     poprzedniego, wiec wejscie w Challenges kosztowalo trzy pelne obiegi do
     serwera (a baza stoi za oceanem) zamiast jednego. Zadne z nich nie potrzebuje
     wyniku pozostalych:
       pushCfg()          — baner opt-in musi wiedziec, czy serwer ma klucze push,
       /api/me/accounts   — dane widoku,
       /api/products      — katalog dla pasa upsellu i dla openBuy(); pobierany raz.
     Katalog swiadomie NIE blokuje widoku przy bledzie: pas upsellu jest dodatkiem,
     lista kont ma sie pokazac tak czy tak. */
  const [,accs]=await Promise.all([
    pushCfg(),
    api('/api/me/accounts'),
    PRODUCTS.length?null:api('/api/products').then(p=>{PRODUCTS=p}).catch(()=>{}),
  ]);
  const banner=accs.length
    ?`<div class="gradient-banner">
        <span class="gb-tag">${ICO.spark} Refer &amp; earn</span><span class="gb-sep"></span>
        <span class="gb-txt">Share your link and earn <b>10%</b> of every challenge your referrals buy — tracked automatically.</span>
        <button class="gb-btn" onclick="goAffiliate()">Get your link ›</button>
      </div>`
    :'';
  if(!accs.length){
    $('view').innerHTML=`<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>
      <h3>No challenges yet</h3><p>Start a challenge and your MT5 account will appear here within a minute.</p>
      <button class="btn-p" onclick="go('store')">Browse challenges</button></div>`;
    return;
  }
  /* Challenge list: one row per account. Kazdy wiersz odpowiada od razu na
     "ile mi brakuje do zdania" i "ile zostalo dziennego limitu" — oba paski
     uzywaja prog() z widoku szczegolow, wiec kolory znacza wszedzie to samo.
     One banner per visit: the push opt-in (when eligible) beats refer&earn. */
  const pushB=pushBannerHtml();
  /* Status filter — the choice is saved on the account (ui_prefs.chalFilter).
     "Active" is the evaluation side (incl. provisioning), "Funded" follows the
     card's own funded flag, "Failed" collects ended challenges. */
  const cf=(ME&&ME.ui_prefs&&ME.ui_prefs.chalFilter)||'all';
  const isFunded=a=>a.phase==='funded'&&!['breached','failed'].includes(a.status);
  const isDead=a=>['breached','failed'].includes(a.status);
  /* "Scale your progress": real math only — the best live account with a
     positive return drives the strip; nothing renders without one. */
  const up=(()=>{
    const cands=accs.filter(a=>!isDead(a)&&a.metrics&&(a.metrics.profit_pct||0)>0);
    if(!cands.length||!PRODUCTS.length)return {html:'',banner:''};
    const ref=cands.reduce((b,a)=>a.metrics.profit_pct>b.metrics.profit_pct?a:b);
    const pct=ref.metrics.profit_pct;
    const fam=PRODUCTS.filter(p=>p.steps===ref.steps&&p.price_usd>0&&p.account_size>ref.initial_balance)
      .sort((a,b)=>a.account_size-b.account_size);
    if(!fam.length)return {html:'',banner:''};
    const who=ref.status==='provisioning'?'your account':esc(ref.login);
    /* Wstazka "Best value" wisiala na planie oznaczonym w katalogu jako popularny
       (POPULAR_SIZE = 100k). Pas pokazuje WYLACZNIE plany wieksze od konta tradera,
       wiec od 100k w gore popularnego w nim nie ma i zaden kafelek nie byl
       wyrozniony — a to wlasnie ten klient, ktoremu podpowiedz przydaje sie
       najbardziej. Gdy popularnego brak, bierzemy DRUGI plan w gore: pierwszy jest
       oczywistym nastepnym krokiem i wybiera sie sam, a najwieksze sa poza
       zasiegiem. Przy jednym wiekszym planie wstazke dostaje ten jeden. */
    const wyroznik=(fam.find(p=>p.popular)||fam[Math.min(1,fam.length-1)]).key;
    /* Dwa rowne rzedy zamiast pelnego pierwszego i ogona w drugim. Przy 9
       planach `auto-fill` dawal 6+3; teraz liczba kolumn to polowa kafelkow
       zaokraglona w gore (9 -> 5+4, 8 -> 4+4, 7 -> 4+3). Do czterech kafelkow
       drugi rzad nie ma sensu — zostaje jeden. */
    const kolumn=fam.length<=4?fam.length:Math.ceil(fam.length/2);
    /* Gorny limit szerokosci rzedu (~300px na kolumne). Bez niego konto blisko
       szczytu oferty — gdzie wiekszych planow zostaja dwa albo jeden — rozrzucalo
       kafelki po calej szerokosci panelu. Przy dziewieciu planach limit jest
       wiekszy niz panel, wiec nic nie zmienia. */
    const rzadMax=kolumn*300;
    const html=`<div class="upsell sec-card">
      <div class="upsell-head">
        <div>
          <div class="up-eyebrow">Scale your progress</div>
          <h3>Here's how much you could have earned with a larger account</h3>
          <div class="up-based">Based on your <b class="up">+${pct.toFixed(2)}%</b> from ${who}</div>
        </div>
        <button class="btn-p" onclick="go('store')">Upgrade →</button>
      </div>
      <div class="upsell-row" style="--up-cols:${kolumn};--up-max:${rzadMax}px">${fam.map(p=>`<div class="upsell-card${p.key===wyroznik?' pop':''}"
        role="button" tabindex="0" onclick="openBuy('${p.key}')"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openBuy('${p.key}')}">
        ${p.key===wyroznik?'<span class="uc-ribbon">Best value</span>':''}
        <div class="uc-if">If you had</div>
        <div class="uc-size">$${fmt0(p.account_size)}</div>
        <div class="uc-sub">you could have earned</div>
        <div class="uc-earn">+$${fmt(p.account_size*pct/100)}</div>
        <div class="uc-go">Upgrade →</div>
      </div>`).join('')}</div>
      <p class="uc-fine">Illustrative math based on your current return, not a promise. Rewards depend on your trading.</p>
    </div>`;
    /* Same-level banner rides the Upgrade promo: pay your current size, the
       account is created one size up. Only while the promo is live. */
    const samePlan=PRODUCTS.find(p=>p.steps===ref.steps&&p.account_size===ref.initial_balance&&p.promo_upgrade_size);
    const banner=samePlan?`<div class="gradient-banner">
        <span class="gb-tag">${ICO.spark} Same level upgrade</span><span class="gb-sep"></span>
        <span class="gb-txt">Keep your <b>+${pct.toFixed(2)}%</b> momentum from ${who} — pay for $${fmt0(samePlan.account_size)}, trade <b>$${fmt0(samePlan.promo_upgrade_size)}</b>.</span>
        <button class="gb-btn" onclick="upgradeNow('${samePlan.key}')">Upgrade now ›</button>
      </div>`:'';
    return {html,banner};
  })();
  const shown=accs.filter(a=>cf==='funded'?isFunded(a):cf==='failed'?isDead(a)
    :cf==='active'?(!isFunded(a)&&!isDead(a)):true);
  /* Podsumowania licza SIE Z FILTRU, nie z calego zbioru. Wczesniej staly nad
     lista jako stale: po wybraniu "Failed" lista mowila "0 of 1" i "No failed
     accounts", a kafelki dalej pokazywaly +$375 zysku z konta, ktorego w tym
     widoku nie bylo. Kazda liczba nad lista ma opisywac to, co pod nia widac.
     Salda i zysk dalej licza tylko konta ZYWE (prowizjonowane nie maja jeszcze
     rachunku, a zamkniete nie sa juz kapitalem) — filtr zaweza zbior, nie zmienia
     definicji. */
  const zywe=a=>['active','funded','passed'].includes(a.status);
  const live=shown.filter(zywe);
  const balance=live.reduce((s,a)=>s+a.balance,0);
  const profit=live.reduce((s,a)=>s+(a.balance-a.initial_balance),0);
  const payouts=shown.reduce((s,a)=>s+(a.paid_out||0),0);
  const zakres=cf==='all'?'':{active:'Evaluation only',funded:'Funded only',failed:'Ended only'}[cf];
  const podpis=zakres?`<div class="sub">${zakres}</div>`:'';
  const stats=`<div class="stats-row">
    <div class="stat-tile"><div class="tile-ic purple">${ICO.dollar}</div>
      <div><div class="lbl">Total Balance</div><div class="val">$${fmt(balance)}</div>${podpis}</div></div>
    <div class="stat-tile"><div class="tile-ic green">${ICO.trend}</div>
      <div><div class="lbl">Total Profit</div><div class="val ${profit>=0?'up':'down'}">${profit>=0?'+':''}$${fmt(profit)}</div>${podpis}</div></div>
    <div class="stat-tile"><div class="tile-ic blue">${ICO.layers}</div>
      <div><div class="lbl">Total Accounts</div><div class="val">${shown.length} <small>${live.length} live</small></div>${podpis}</div></div>
    <div class="stat-tile"><div class="tile-ic orange">${ICO.wallet}</div>
      <div><div class="lbl">Total Payouts</div><div class="val">$${fmt(payouts)}</div>${podpis}</div></div>
  </div>`;
  const filterBar=`<div class="chal-filter">
    <div class="shop-tabs">${[['all','All'],['active','Active'],['funded','Funded'],['failed','Failed']]
      .map(([k,l])=>`<button class="shop-tab${cf===k?' on':''}" onclick="chalFilter('${k}')">${l}</button>`).join('')}</div>
    <span class="chal-count">${shown.length} of ${accs.length}</span>
  </div>`;
  const list=shown.length?`<div class="chal-list">`+shown.map(a=>{
    const m=a.metrics||{};
    const funded=a.phase==='funded'&&!['breached','failed'].includes(a.status);
    const dead=['breached','failed'].includes(a.status);
    const reach=m.profit_target_pct?Math.max(0,(m.profit_pct||0)/m.profit_target_pct*100):0;
    const openPnl=+(a.open_pnl||0);
    const dayLeft=(m.daily_floor!=null&&a.equity!=null)?Math.max(0,a.equity-m.daily_floor):null;
    const bars=dead
      ?`<div class="chal-breach">Challenge ended${a.breach_reason?': '+esc(a.breach_reason):''}</div>`
      /* Etykiety krotsze niz w widoku szczegolow: w wierszu listy kolumna paskow
         dzieli miejsce z kwotami, a przy koncie na $2,000,000 "Progress to target"
         lamalo sie na dwie linie. */
      :`${funded
          ?prog('Max drawdown',m.overall_dd_used_pct,true,
             `${(m.overall_dd_used_pct||0).toFixed(1)}% / ${m.max_overall_loss_pct}%`)
          :prog('Target',reach,false,
             `${(m.profit_pct||0).toFixed(1)}% / ${m.profit_target_pct||0}%`)}
        ${prog('Daily loss',m.daily_loss_used_pct,true,
           dayLeft!=null?`$${fmt(dayLeft)} left today`:`${(m.daily_loss_used_pct||0).toFixed(1)}% / ${m.max_daily_loss_pct}%`)}`;
    /* Caly wiersz otwiera konto; przyciski w srodku musza zatrzymac bubbling,
       bo "Restart challenge" prowadzi gdzie indziej niz klikniecie w wiersz. */
    return `<div class="chal-row${dead?' dead':''}" role="button" tabindex="0" onclick="openAcc(${a.id})"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openAcc(${a.id})}">
      <div class="cr-id">
        <div class="acct-ava"><img src="/static/img/mt5.png" alt="MT5"></div>
        <div class="cr-txt">
          <div class="login">${a.status==='provisioning'?'<span class="muted">MT5 account pending…</span>':esc(a.login)}
            <span class="status ${esc(a.status)}"><span class="dot"></span>${a.status==='active'?'evaluation':esc(a.status)}</span>
            <span class="phase-chip">${PHASE_LABEL[a.phase]||esc(a.phase)}</span></div>
          <div class="sub">${planKind(a.steps)} · $${fmt0(a.initial_balance)} · split ${a.profit_split_pct}%${
            a.status==='provisioning'?' · creating on MetaQuotes-Demo':''}</div>
        </div>
      </div>
      <div class="cr-prog">${bars}</div>
      <div class="cr-nums">
        <div class="cr-num"><div class="l">Balance</div><div class="v">$${fmt(a.balance)}</div></div>
        <div class="cr-num"><div class="l">Equity${openPnl?' <i class="live-dot" title="Open position"></i>':''}</div>
          <div class="v">$${fmt(a.equity)}${openPnl?` <small class="${openPnl>=0?'up':'down'}">${money(openPnl)}</small>`:''}</div></div>
        ${funded
          ?`<div class="cr-num"><div class="l">Paid out</div><div class="v">$${fmt(a.paid_out||0)}</div></div>`
          :`<div class="cr-num"><div class="l">Trading days</div><div class="v">${m.trading_days??0}<small>/${m.min_trading_days??0}</small></div></div>`}
      </div>
      <div class="cr-go">${dead
        ?`<button class="btn-o sm" onclick="event.stopPropagation();openAcc(${a.id})">${ICO.eye} Review</button>
          <button class="btn-o sm" onclick="event.stopPropagation();go('store')">Restart</button>`
        :`<button class="btn-o sm" onclick="event.stopPropagation();openAcc(${a.id})">${ICO.eye} Dashboard</button>`}
      </div>
    </div>`}).join('')+`</div>`
    :`<div class="sec-card chal-nomatch">No ${cf==='all'?'':cf+' '}accounts to show.</div>`;
  /* Upsell siedzi POD lista: zakladka ma najpierw pokazac konta, a nie blok
     sprzedazowy — wczesniej pierwsza karta zaczynala sie na 720px (desktop)
     i 971px (telefon), czyli ponizej pierwszego ekranu. */
  $('view').innerHTML=(pushB||up.banner||banner)+stats+filterBar+list+up.html;
  rollStats();
  if(window._upsellJump){window._upsellJump=false;setTimeout(flashUpsell,60)}
 },

 async store(){
  const ps=await api('/api/products');
  PRODUCTS=ps;
  const groups=[
    {id:'2step',name:'2-Step',match:p=>p.steps===2&&p.price_usd>0},
    {id:'instant',name:'Instant Funding',match:p=>p.steps===0&&p.price_usd>0},
  ].filter(g=>ps.some(g.match));
  window._shopTab=window._shopTab||(groups[0]&&groups[0].id);
  const buyKey=new URLSearchParams(location.search).get('buy');
  if(buyKey){const g=groups.find(g=>ps.some(p=>g.match(p)&&p.key===buyKey));if(g)window._shopTab=g.id}
  const g=groups.find(x=>x.id===window._shopTab)||groups[0];
  if(!g){$('view').innerHTML='<div class="empty"><h3>No plans available</h3></div>';return}
  const items=ps.filter(g.match).sort((a,b)=>a.account_size-b.account_size);
  $('view').innerHTML=`
    <div class="shop-tabs">${groups.map(x=>`<button class="shop-tab${x.id===g.id?' on':''}" onclick="window._shopTab='${x.id}';VIEWS.store()">${x.name}</button>`).join('')}</div>
    ${ME.credits_usd>0?`<div class="note" style="margin-bottom:14px"><b>Store credit: $${fmt(ME.credits_usd)}</b>, applied to your total automatically at checkout.</div>`:''}
    <div class="plan-grid">`+items.map(p=>`
    <div class="plan-card${p.popular?' pop':''}" data-plan="${esc(p.key)}">
      ${p.popular?'<span class="plan-ribbon">Best value</span>':''}
      <div class="plan-size">$${fmt0(p.account_size)}</div>
      ${p.promo_upgrade_size&&pfPromo()?`<div class="plan-badge">→ trade $${fmt0(p.promo_upgrade_size)} with your promo</div>`:''}
      <div class="plan-kind">${p.steps===0?'Instant Funding, no evaluation':'2-Step Evaluation'}</div>
      <div class="plan-price">$${fmt0(p.price_usd)} <small>one-time</small></div>
      <div class="plan-refund">✓ Refunded with your first payout</div>
      <ul class="plan-feats">
        <li>${p.steps===0?'Funded from day one, <b>no profit target</b>'
          :`Profit target <b>${p.profit_target_p1}%${p.steps>1?' / '+p.profit_target_p2+'%':''}</b>`}</li>
        <li>Max daily loss <b>${p.max_daily_loss_pct}%</b></li>
        <li>Max overall loss <b>${p.max_overall_loss_pct}% ${p.drawdown_type}</b></li>
        <li>Max open volume <b>${p.max_lots} lots</b></li>
        <li>Min trading days <b>${p.min_trading_days}</b> · split <b>${p.profit_split_pct}%</b></li>
      </ul>
      <button onclick="openBuy('${esc(p.key)}')" class="btn-p">Start Challenge</button>
    </div>`).join('')+`</div>
    <p class="muted" style="font-size:12px;margin-top:16px">Coupon and promo codes accepted at checkout.</p>`;
  highlightPlanFromUrl();
 },

 async board(){
  const b=await api('/api/leaderboard');
  if(!b.length){$('view').innerHTML='<div class="empty"><h3>No ranked accounts yet</h3><p>The leaderboard fills up as traders make progress.</p></div>';return}
  /* Kafelek pokazywal SUME EQUITY kont funded, czyli w ogromnej wiekszosci nasz
     wlasny kapital: konto $200k z zyskiem $14k liczylo sie jako "$214,311". Ranking
     jest o wynikach traderow, wiec sumujemy to, co faktycznie wypracowali —
     equity ponad saldo startowe. Ujemne wyniki wchodza normalnie: to ma byc
     dorobek grupy, a nie sama smietanka. */
  const totalProfit=b.reduce((s,r)=>s+(r.profit_usd!=null?r.profit_usd:((r.equity||0)-(r.account_size||0))),0);
  const best=b[0];
  const medal=['gold','silver','bronze'];
  let prevRanks=null;try{prevRanks=JSON.parse(localStorage.getItem('pf_board_prev')||'null')}catch(e){}
  const mv=(r,rank)=>{if(!prevRanks||prevRanks[r.trader]==null)return'';const d=prevRanks[r.trader]-rank;
    return d>0?` <span class="rank-up">▲${d}</span>`:d<0?` <span class="rank-down">▼${-d}</span>`:''};
  const podium=b.slice(0,3).map((r,i)=>{
    const pu=r.profit_usd!=null?r.profit_usd:(r.equity||0)-(r.account_size||0);
    return `
    <div class="pod-card${i===0?' first':''}">
      <div class="pod-rank">Rank #${i+1}</div>
      <div class="pod-medal ${medal[i]}">${ICO.trophy}</div>
      <div class="pod-name">${esc(r.trader)}${mv(r,i+1)}</div>
      <div style="margin:6px 0 2px"><span class="status ${r.status==='funded'?'funded':'active'}"><span class="dot"></span>${r.status==='funded'?'Funded':'Evaluation'}</span></div>
      <div class="pod-ret">${r.profit_pct>=0?'+':''}${r.profit_pct.toFixed(2)}%</div>
      <div class="pod-profit ${pu>=0?'up':'down'}">${pu>=0?'+':'−'}$${fmt0(Math.abs(pu))} profit</div>
      <div class="pod-mini">
        <div><div class="l">Account</div><div class="v">$${fmt0(r.account_size)}</div></div>
        <div><div class="l">Stage</div><div class="v">${r.status==='funded'?'Funded':'Eval'}</div></div>
      </div>
    </div>`}).join('');
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic blue">${ICO.layers}</div>
        <div><div class="lbl">Traders ranked</div><div class="val">${b.length}</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.trend}</div>
        <div><div class="lbl">Profit earned</div><div class="val ${totalProfit>=0?'up':'down'}">${totalProfit<0?'−':'+'}$${fmt0(Math.abs(totalProfit))}</div>
          <div class="sub">by ranked traders</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.trophy}</div>
        <div><div class="lbl">Best return</div><div class="val up">+${best.profit_pct.toFixed(2)}%</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.cal}</div>
        <div><div class="lbl">Updates</div><div class="val" style="font-size:17px">Live</div><div class="sub">all-time ranking</div></div></div>
    </div>
    <div class="podium">${podium}</div>
    ${b.length>3?`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.board">
      <thead><tr><th style="width:52px">#</th><th>Trader</th><th>Account</th><th>Stage</th><th style="text-align:right">Profit</th></tr></thead>
      <tbody>`+b.slice(3).map((r,i)=>{
        const pu=r.profit_usd!=null?r.profit_usd:(r.equity||0)-(r.account_size||0);
        return `<tr>
        <td class="num muted">${String(i+4).padStart(2,'0')}</td>
        <td>${esc(r.trader)}${mv(r,i+4)}</td>
        <td class="num muted">$${fmt0(r.account_size)}</td>
        <td><span class="status ${r.status==='funded'?'funded':'active'}"><span class="dot"></span>${r.status==='funded'?'Funded':'Evaluation'}</span></td>
        <td class="num ${r.profit_pct>=0?'up':'down'}" style="text-align:right" data-sort="${r.profit_pct}">${r.profit_pct>=0?'+':''}${r.profit_pct.toFixed(2)}%
          <div style="font-size:11px;opacity:.78">${pu>=0?'+':'−'}$${fmt0(Math.abs(pu))}</div></td></tr>`}).join('')+`
      </tbody></table></div>`:''}
    <p class="muted" style="font-size:11.5px;margin-top:12px">Funded accounts only, ranked by profit. Names are masked for privacy.</p>`;
  localStorage.setItem('pf_board_prev',JSON.stringify(Object.fromEntries(b.map((r,i)=>[r.trader,i+1]))));
 },

 async achievements(){
  const d=await api('/api/me/achievements');
  const list=d.badges, done=d.unlocked;
  /* Wlasny prefiks klas (mrw-), bo `.rw-code` nalezy juz do kodow wymienianych
     za punkty w Loyalty — dwie rozne rzeczy pod jedna nazwa to prosta droga do
     tego, co zrobila klasa `.fl` etykietom w KYC. */
  const nagroda=r=>{
    const ikona=r.plan?ICO.crown:ICO.gift;
    const stan=r.status==='claimed'
      ?(r.code?`<div class="mrw-code"><b>${esc(r.code)}</b>
           <button class="icon-btn" aria-label="Copy code"
             onclick="copyVal(this,'${esc(r.code)}')">${ICO.copy}</button></div>`
          :`<div class="badge-state on">✓ Account created</div>`)
      :r.status==='ready'
        ?`<button class="btn-p sm mrw-go" onclick="claimReward(${r.tier},this)">Claim reward</button>`
        :`<div class="mrw-need">${r.remaining} more to unlock</div>`;
    return `<div class="mrw-card ${r.status}">
      <div class="mrw-top"><div class="mrw-ic">${ikona}</div><b>${r.tier} / ${d.total}</b></div>
      <div class="mrw-label">${esc(r.label)}</div>${stan}</div>`;
  };
  $('view').innerHTML=`
    <div class="panel" style="margin-bottom:16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div class="tile-ic purple">${ICO.medal}</div>
      <div style="flex:1"><h3>${done} / ${d.total} unlocked</h3>
        <p class="muted" style="font-size:13px">Milestones are earned automatically from your real activity on the platform.</p></div>
      <div class="phase-bar" style="width:180px"><i style="width:${d.total?done/d.total*100:0}%"></i></div>
    </div>
    <div class="sec-card">
      <h3>Milestone rewards</h3>
      <p class="muted" style="font-size:13px;margin:-8px 0 14px">Each reward is issued once and belongs to your account only.</p>
      <div class="mrw-grid">${d.rewards.map(nagroda).join('')}</div>
    </div>
    <div class="badge-grid">`+list.map(b=>`
      <div class="badge-card${b.unlocked?'':' locked'}">
        <div class="badge-ic">${BADGE_ICO[b.key]||ICO.medal}</div>
        <div><h4>${esc(b.name)}</h4><p>${esc(b.desc)}</p>
          <div class="badge-state ${b.unlocked?'on':'off'}">${b.unlocked?'✓ UNLOCKED':'LOCKED'}</div></div>
      </div>`).join('')+`</div>`;
  document.querySelectorAll('.badge-card:not(.locked) .badge-ic').forEach((el,i)=>setTimeout(()=>RFX.pop(el),150+i*60));
 },

 async loyalty(){
  /* Wszystko liczy serwer (/api/me/loyalty). Wczesniej punkty sumowala ta
     funkcja z listy zamowien — przy wymianie oznaczaloby to, ze trader sam
     sobie ustala, na co go stac. */
  const d=await api('/api/me/loyalty');
  LOY=d;
  const next=d.next_tier_at, prog=next?Math.min(100,d.points_lifetime/next*100):100;
  const nearMiss=!!next&&prog>=80;
  const tierIdx=d.tiers.findIndex(t=>t.name===d.tier);
  const kody=d.codes.filter(c=>c.status==='active');
  const zuzyte=d.codes.filter(c=>c.status!=='active');
  $('view').innerHTML=`
    <div class="panel" style="margin-bottom:4px">
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <div class="tile-ic orange">${ICO.crown}</div>
        <div style="flex:1;min-width:220px">
          <h3>${fmt0(d.points_available)} points to spend</h3>
          <p class="muted" style="font-size:13px">You earn 1 point for every $1 spent on challenges, plus bonuses from check-ins and daily reveals. Trade them for a discount code below.</p>
          ${d.points_spent>0?`<p class="muted" style="font-size:11.5px;margin-top:2px">${fmt0(d.points_lifetime)} earned all-time, <b class="mono">${fmt0(d.points_spent)}</b> already redeemed</p>`:''}
        </div>
        <div style="min-width:220px;flex:1">
          <div class="prog-top"><span>${next?(nearMiss?`Only ${fmt0(next-d.points_lifetime)} points to ${d.next_tier}`:`Progress to ${d.next_tier}`):'Top tier reached'}</span><b>${next?fmt0(d.points_lifetime)+' / '+fmt0(next):d.tier+' · MAX'}</b></div>
          <div class="phase-bar${nearMiss?' near-miss':''}"><i style="width:${prog}%"></i></div>
          <p class="muted" style="font-size:11px;margin-top:6px">Your ${d.tier} status comes from what you have earned all-time, so spending points never takes it away.</p>
        </div>
      </div>
    </div>

    <h3 style="font-size:15px;margin:22px 0 2px">Trade your points</h3>
    <p class="muted" style="font-size:12.5px;margin-bottom:4px">Each code is yours alone, works once and is valid for ${d.code_ttl_days} days.</p>
    <div class="tier-track">`+d.rewards.map(r=>`
      <div class="tier-card${r.affordable?' on':' locked'}">
        <div class="nm">${ICO.crown.replace('stroke-width="1.8"','stroke-width="1.8" width="18" height="18"')} ${r.pct}% off</div>
        <div class="req">${fmt0(r.cost)} points</div>
        <div class="ben">One-time code for any challenge, on top of the plan price.</div>
        <button class="btn-p sm rw-go" ${r.affordable?'':'disabled'} onclick="redeemReward('${r.key}',this)">
          ${r.affordable?`Redeem for ${fmt0(r.cost)} pts`:`Need ${fmt0(r.cost-d.points_available)} more`}</button>
      </div>`).join('')+`</div>

    <h3 style="font-size:15px;margin:24px 0 2px">Your codes</h3>
    ${kody.length||zuzyte.length?`<div class="rw-codes">`+[...kody,...zuzyte].map(c=>`
      <div class="rw-code${c.status==='active'?'':' spent'}">
        <div>
          <b class="mono">${esc(c.code)}</b>
          <span class="muted" style="font-size:11.5px;display:block;margin-top:2px">
            ${c.pct}% off · ${fmt0(c.points_spent)} pts${c.status==='active'?` · expires ${dstr(c.expires_at)}`:c.status==='used'?` · used ${dstr(c.used_at)}`:' · expired'}</span>
        </div>
        ${c.status==='active'
          ?`<button class="btn-o sm" onclick="copyVal(this,'${esc(c.code)}')">Copy</button>`
          :`<span class="status ${c.status==='used'?'paid':'failed'}"><span class="dot"></span>${c.status}</span>`}
      </div>`).join('')+`</div>`
      :`<p class="muted" style="font-size:12.5px">No codes yet. Redeem your points above and the code shows up here.</p>`}
    <p class="muted" style="font-size:12px;margin-top:14px">Paste the code in the checkout box when you buy a challenge. Points come off the moment you redeem, the code itself is used up on your next purchase.</p>`;
 },

 async journal(){
  const rows=await api('/api/me/journal');
  const head=`<div style="display:flex;justify-content:flex-end;margin-bottom:14px">
    <button class="btn-p sm" onclick="openJournalModal()">+ New Entry</button></div>`;
  if(!rows.length){
    $('view').innerHTML=head+`<div class="empty">
      ${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:38px;height:38px;margin:0 auto 14px;display:block;color:var(--dim)"><path d="M4 19V5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z"/><path d="M19 17H6a2 2 0 0 0-2 2"/></svg>'}
      <h3>No entries yet</h3><p>Write down your setups, mistakes and lessons — future you will thank you.</p>
      <button class="btn-p" onclick="openJournalModal()">Write your first entry</button></div>`;
    return;
  }
  $('view').innerHTML=head+rows.map(e=>`
    <div class="panel" style="margin-bottom:12px">
      <div style="display:flex;align-items:flex-start;gap:12px">
        <div style="flex:1;min-width:0">
          <h3 style="font-size:15.5px">${esc(e.title)}</h3>
          <div class="muted" style="font-size:11.5px;margin:2px 0 8px">${dstr(e.ts)}${e.account?` · account ${esc(e.account)}`:''}</div>
          <p style="font-size:13.5px;color:var(--muted);white-space:pre-line">${esc(e.content)}</p>
        </div>
        <button class="icon-btn" onclick="delJournal(${e.id})" title="Delete">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/></svg></button>
      </div>
    </div>`).join('');
 },

 async analytics(){
  anCharts.forEach(c=>c.destroy());anCharts=[];
  const accs=await api('/api/me/accounts');
  if(!accs.length){$('view').innerHTML='<div class="empty"><h3>No accounts yet</h3><p>Analytics appear once you have a challenge account.</p></div>';return}
  window._anAcc=window._anAcc||accs[0].id;
  if(!accs.some(a=>a.id===window._anAcc))window._anAcc=accs[0].id;
  const [act,st]=await Promise.all([
    api(`/api/me/accounts/${window._anAcc}/activity`),
    api(`/api/me/accounts/${window._anAcc}/stats`)]);
  const days=act.days;
  const total=days.reduce((s,d)=>s+d.pnl,0);
  const bestD=days.reduce((m,d)=>d.pnl>(m?.pnl??-1e18)?d:m,null);
  const worstD=days.reduce((m,d)=>d.pnl<(m?.pnl??1e18)?d:m,null);
  const green=days.filter(d=>d.pnl>0).length;
  const has=st.trades>0;
  const dur=s=>{if(s==null)return'—';const m=Math.round(s/60);
    return m>=60?Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m':m+'m'};
  const pct=b=>b.trades?Math.round(b.wins/b.trades*100):0;
  const pf=has?(st.profit_factor===null?(st.wins?'∞':'—'):st.profit_factor.toFixed(2)):'—';
  const maxSym=has?Math.max(1,...st.by_symbol.map(r=>Math.abs(r.pnl))):1;
  const maxLS=has?Math.max(1,Math.abs(st.long.pnl),Math.abs(st.short.pnl)):1;
  const lsRow=(label,b)=>`<div class="sym-row">
    <b>${label}</b>
    <span class="muted">${b.trades} trade${b.trades===1?'':'s'} · ${pct(b)}% won</span>
    <div class="sym-bar"><i class="${b.pnl>=0?'ok':'bad'}" style="width:${Math.abs(b.pnl)/maxLS*100}%"></i></div>
    <span class="num ${b.pnl>=0?'up':'down'}">${money(b.pnl)}</span>
  </div>`;
  $('view').innerHTML=`
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
      <select id="an-sel" class="inp" style="max-width:260px" onchange="window._anAcc=parseInt(this.value);VIEWS.analytics()">
        ${accs.map(a=>`<option value="${a.id}"${a.id===window._anAcc?' selected':''}>${esc(a.login)} · ${esc(a.product_key)}</option>`).join('')}
      </select>
      <span class="muted" style="font-size:12px">Computed server-side from every closed trade on this account.</span>
    </div>
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic ${total>=0?'green':'orange'}">${ICO.trend}</div>
        <div><div class="lbl">Total P&amp;L</div><div class="val ${total>=0?'up':'down'}">${total>=0?'+':''}$${fmt(total)}</div></div></div>
      ${has?`
      <div class="stat-tile"><div class="tile-ic purple">${ICO.target}</div>
        <div><div class="lbl">Win rate</div><div class="val">${st.win_rate.toFixed(1)}%</div>
        <div class="sub">${st.wins} of ${st.trades} trades</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.bars}</div>
        <div><div class="lbl">Profit factor</div><div class="val">${pf}</div>
        <div class="sub">$${fmt(st.gross_profit)} won / $${fmt(-st.gross_loss)} lost</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.spark}</div>
        <div><div class="lbl">Expectancy</div><div class="val ${st.expectancy>=0?'up':'down'}">${money(st.expectancy)}</div>
        <div class="sub">per trade</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.dollar}</div>
        <div><div class="lbl">Avg win / loss</div>
        <div class="val">${st.avg_win!==null?'+$'+fmt(st.avg_win):'—'} <span class="muted" style="font-weight:400">/</span> ${st.avg_loss!==null?'-$'+fmt(-st.avg_loss):'—'}</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.eye}</div>
        <div><div class="lbl">Avg duration</div><div class="val">${dur(st.avg_duration_sec)}</div></div></div>
      <div class="stat-tile"><div class="tile-ic ${st.streak>=0?'green':'orange'}">${ICO.flame}</div>
        <div><div class="lbl">Streak</div><div class="val ${st.streak>0?'up':st.streak<0?'down':''}">${st.streak===0?'—':Math.abs(st.streak)+(st.streak>0?' wins':' losses')}</div>
        <div class="sub">most recent trades</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.trophy}</div>
        <div><div class="lbl">Best trade</div><div class="val up">${money(st.best_trade.pnl)}</div>
        <div class="sub">${esc(st.best_trade.symbol)}</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.alert}</div>
        <div><div class="lbl">Worst trade</div><div class="val ${st.worst_trade.pnl<0?'down':''}">${money(st.worst_trade.pnl)}</div>
        <div class="sub">${esc(st.worst_trade.symbol)}</div></div></div>`:''}
      <div class="stat-tile"><div class="tile-ic green">${ICO.dollar}</div>
        <div><div class="lbl">Best day</div><div class="val up">${bestD?'+$'+fmt(Math.max(0,bestD.pnl)):'—'}</div><div class="sub">${bestD?bestD.day:''}</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.alert}</div>
        <div><div class="lbl">Worst day</div><div class="val ${worstD&&worstD.pnl<0?'down':''}">${worstD?(worstD.pnl<0?'-$'+fmt(-worstD.pnl):'$'+fmt(worstD.pnl)):'—'}</div><div class="sub">${worstD?worstD.day:''}</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.cal}</div>
        <div><div class="lbl">Profitable days</div><div class="val">${green} / ${days.length}</div></div></div>
    </div>
    ${has?`
    <div class="card-cols">
      <div class="sec-card"><h3>P&amp;L by weekday</h3>
        <div class="chart-box"><canvas id="an-wd"></canvas></div></div>
      <div class="sec-card"><h3>P&amp;L by hour <span class="muted" style="font-weight:400;font-size:12px">(UTC)</span></h3>
        <div class="chart-box"><canvas id="an-hr"></canvas></div></div>
    </div>
    <div class="sec-card"><h3>Long vs short</h3>
      <div class="sym-list">${lsRow('Long',st.long)}${lsRow('Short',st.short)}</div>
    </div>
    ${st.by_symbol.length?`<div class="sec-card"><h3>Instruments</h3>
      <div class="sym-list">${st.by_symbol.map(r=>`
        <div class="sym-row">
          <b>${esc(r.symbol)}</b>
          <span class="muted">${r.trades} trade${r.trades===1?'':'s'} · ${r.win_rate.toFixed(0)}% won</span>
          <div class="sym-bar"><i class="${r.pnl>=0?'ok':'bad'}" style="width:${Math.abs(r.pnl)/maxSym*100}%"></i></div>
          <span class="num ${r.pnl>=0?'up':'down'}">${money(r.pnl)}</span>
        </div>`).join('')}</div>
    </div>`:''}`
    :`<div class="sec-card"><h3>Trade statistics</h3>
      <p class="muted" style="font-size:13px">No closed trades on this account yet — win rate, profit factor and the breakdown charts appear after your first closed position.</p>
    </div>`}
    <div class="sec-card"><h3>Daily P&amp;L</h3>
      ${days.length?'<div class="chart-box"><canvas id="an-chart"></canvas></div>'
        :'<p class="muted" style="font-size:13px">No daily data yet. The chart appears after the first risk-engine readings.</p>'}
    </div>`;
  if(chart){chart.destroy();chart=null}
  const th=chartTheme();
  const bar=(el,labels,data,maxTicks)=>anCharts.push(new Chart($(el),{type:'bar',
    data:{labels,datasets:[{data,backgroundColor:data.map(v=>v>=0?'rgba(16,185,129,.75)':'rgba(239,68,68,.75)'),borderRadius:5}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{ticks:{color:th.dim,font:{size:10},autoSkip:true,maxRotation:0,maxTicksLimit:maxTicks},grid:{display:false}},
        y:{ticks:{color:th.dim,font:{size:10},callback:v=>'$'+v},grid:{color:th.line}}}}}));
  if(has){
    bar('an-wd',['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],st.by_weekday.map(b=>b.pnl));
    bar('an-hr',st.by_hour.map((_,h)=>h+'h'),st.by_hour.map(b=>b.pnl),
        matchMedia('(max-width:640px)').matches?8:12);
  }
  if(days.length)chart=new Chart($('an-chart'),{type:'bar',
    data:{labels:days.map(d=>d.day),datasets:[{data:days.map(d=>d.pnl),
      backgroundColor:days.map(d=>d.pnl>=0?'rgba(16,185,129,.75)':'rgba(239,68,68,.75)'),borderRadius:5}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{ticks:{color:th.dim,font:{size:10},autoSkip:true,maxRotation:0,
          maxTicksLimit:matchMedia('(max-width:640px)').matches?6:undefined},grid:{display:false}},
        y:{ticks:{color:th.dim,font:{size:10},callback:v=>'$'+v},grid:{color:th.line}}}}});
 },

 async certificates(){
  const d=await api('/api/me/certificates');
  const noCerts=!d.accounts.length&&!d.payouts.length;
  if(noCerts){$('view').innerHTML='<div class="empty"><h3>No certificates yet</h3>'
    +'<p>Pass an evaluation stage or receive a payout and the document appears here.</p></div>';return}

  const certRow=(accId,it)=>{
    if(it.url) return `<div class="cert-row done">
      <div><b>${esc(it.label)}</b><span>Issued · verifiable by its ID</span></div>
      <div class="acts">
        <a class="btn-p sm" href="${it.url}" target="_blank" rel="noopener">Open</a>
        <button class="btn-o sm" onclick="copyLink('${location.origin}${it.url}')">Copy link</button>
      </div></div>`;
    if(it.available) return `<div class="cert-row">
      <div><b>${esc(it.label)}</b><span>Achieved, ready to issue</span></div>
      <div class="acts"><button class="btn-p sm" onclick="issueCert(${accId},'${it.kind}')">Get certificate</button></div>
    </div>`;
    return `<div class="cert-row lock">
      <div><b>${esc(it.label)}</b><span>Not reached yet</span></div>
      <div class="acts"><span class="muted" style="font-size:12.5px">Locked</span></div></div>`;
  };

  $('view').innerHTML=`
    ${d.accounts.map(a=>`<div class="sec-card">
      <div class="cert-head">
        <h3>${esc(a.login)}</h3>
        <span class="kyc-chip">${esc(a.product)} · $${fmt0(a.size)}</span>
        <span class="status ${esc(a.status)}"><span class="dot"></span>${a.status==='active'?'evaluation':esc(a.status)}</span>
      </div>
      <div class="cert-list">${a.items.map(it=>certRow(a.account_id,it)).join('')}</div>
    </div>`).join('')}

    <div class="sec-card">
      <h3>Payout certificates</h3>
      ${d.payouts.length?`<div class="cert-list">${d.payouts.map(p=>`
        <div class="cert-row ${p.url?'done':''}">
          <div><b>$${fmt(p.amount)} payout</b><span>${esc(p.account)} · ${p.ts?dday(p.ts.slice(0,10)):'—'}</span></div>
          <div class="acts">${p.url
            ? `<a class="btn-p sm" href="${p.url}" target="_blank" rel="noopener">Open</a>
               <button class="btn-o sm" onclick="copyLink('${location.origin}${p.url}')">Copy link</button>`
            : `<button class="btn-p sm" onclick="issuePayoutCert(${p.id})">Get certificate</button>`}
          </div></div>`).join('')}</div>`
        :'<p class="muted" style="font-size:13px">No payouts yet. Request one from a funded account.</p>'}
    </div>

    <p class="muted" style="font-size:12px">Every document carries a unique ID and a QR code.
      Anyone can check it at <a href="/verify" target="_blank" rel="noopener" style="color:var(--acc)">/verify</a> —
      recruiters, communities, or you in two years.</p>`;
 },

 async rewards(){
  const aff=ME.affiliate||{};
  const revealHtml=`<div class="reveal-wrap"><div class="reveal-card" id="revealCard" role="button" tabindex="0" onclick="doReveal()">${revealBack()}</div></div>`;
  const link=location.origin+'/?ref='+ME.referral_code;
  const affPanel=`
    <div class="panel" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div class="tile-ic blue">${ICO.spark}</div>
        <div style="flex:1;min-width:240px">
          <h3 style="font-size:15.5px">Your affiliate link</h3>
          <div class="cred-row" style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px 13px;margin-top:8px;max-width:520px">
            <code style="font-family:var(--mono);font-size:12.5px">${esc(link)}</code>
            <button class="copy" onclick="copyVal(this,'${esc(link)}')" title="Copy">${ICO.copy}</button>
          </div>
        </div>
        <div style="display:flex;gap:22px;text-align:center">
          <div><div class="muted" style="font-size:11px">Referred</div><div class="mono" style="font-weight:700;font-size:18px">${aff.referred??0}</div></div>
          <div><div class="muted" style="font-size:11px">Rate</div><div class="mono" style="font-weight:700;font-size:18px">${aff.commission_pct??10}%</div></div>
          <div><div class="muted" style="font-size:11px">Earned</div><div class="mono up" style="font-weight:700;font-size:18px">$${fmt(aff.commission_earned??0)}</div></div>
          <div><div class="muted" style="font-size:11px">Unclaimed</div><div class="mono" style="font-weight:700;font-size:18px">$${fmt(aff.commission_unclaimed??0)}</div></div>
        </div>
        ${(aff.commission_unclaimed??0)>=10?`<button class="btn-p sm" onclick="claimAffiliate(this)">Claim $${fmt(aff.commission_unclaimed)} as store credit</button>`
          :(aff.commission_unclaimed??0)>0?`<span class="muted" style="font-size:12px">Claim unlocks at $10 unclaimed</span>`:''}
      </div>
    </div>`;
  const cards=[
    {ic:'wallet',cls:'green',badge:'Active',name:'Refundable Fee',desc:'Your one-time challenge fee is refunded in full, automatically added to your first payout from a funded account.',cond:['Pass the evaluation','Complete KYC','Request your first payout']},
    {ic:'trend',cls:'purple',badge:'Your call',name:'Scaling Plan',desc:'Grow a funded account by +15% and you choose: take the payout, or move up to the next plan in our pricing. Repeatable, no applications.',cond:['Funded account','+15% growth','You pick payout or a bigger plan']},
    {ic:'spark',cls:'blue',badge:'10% recurring',name:'Affiliate Program',desc:'Share your referral link and earn 10% of every challenge purchased by traders you refer — for life, not just the first order.',cond:['Share your link','Friend buys a challenge','Commission tracked live']},
    {ic:'crown',cls:'orange',badge:'Spend-based',name:'Loyalty Points',desc:'Every $1 spent earns a loyalty point, and check-ins add more. Trade the points for your own one-time discount code on the Loyalty page.',cond:['500 points: 15% off','1,000 points: 25% off','2,000 points: 35% off']},
  ];
  $('view').innerHTML=revealHtml+affPanel+`<div class="badge-grid" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">`+cards.map(c=>`
    <div class="panel">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
        <div class="tile-ic ${c.cls}">${ICO[c.ic]}</div>
        <div><h3 style="font-size:15.5px">${c.name}</h3>
          <span class="status paid" style="margin-top:2px"><span class="dot"></span>${c.badge}</span></div>
      </div>
      <p class="muted" style="font-size:13px;margin-bottom:12px">${c.desc}</p>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 14px">
        ${c.cond.map(x=>`<div style="font-size:12px;color:var(--muted);padding:3px 0">· ${x}</div>`).join('')}
      </div>
    </div>`).join('')+`</div>
  <p class="muted" style="font-size:12px;margin-top:14px">Every program above is a real platform mechanism. Nothing here requires manual approval games.</p>`;
  initRevealCard();
 },

 async payouts(){
  const [data,accs]=await Promise.all([api('/api/me/payouts'),api('/api/me/accounts')]);
  const funded=accs.filter(a=>a.status==='funded');
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic green">${ICO.wallet}</div>
        <div><div class="lbl">Total paid out</div><div class="val up">$${fmt(data.summary.total_paid)}</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.cal}</div>
        <div><div class="lbl">Pending requests</div><div class="val">${data.summary.pending}</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.dollar}</div>
        <div><div class="lbl">Available to request</div><div class="val">$${fmt(data.summary.available)}</div><div class="sub">your split of current profits</div></div></div>
    </div>
    <p class="muted" style="font-size:13px;margin:-6px 0 14px">Payouts are <b>on demand</b> — request whenever
      you are in profit. Every request is reviewed within <b>${data.summary.review_hours||24} hours</b>.</p>
    ${funded.filter(a=>a.scale_up_to).map(a=>{
      const av=Math.max(0,(a.balance-a.initial_balance)*(a.profit_split_pct||80)/100);
      return `<div class="scale-offer">
        <div class="so-txt">
          <b>${esc(a.login)} is up ${a.scale_trigger_pct}%. Now you choose.</b>
          <span>Take the profit as a payout, or move up to the $${fmt0(a.scale_up_to)} plan. One or
            the other: moving up puts the profit back in as capital, so there is nothing left to
            pay out.</span>
        </div>
        <div class="so-act">
          <button class="btn-o sm" onclick="openPayoutModal(${a.id},${av.toFixed(2)})">Take $${fmt(av)} payout</button>
          <button class="btn-p sm" onclick="openScaleModal(${a.id},${a.initial_balance},${a.scale_up_to})">Move up to $${fmt0(a.scale_up_to)}</button>
        </div>
      </div>`}).join('')}
    ${funded.length?`<div class="panel" style="margin-bottom:16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <span class="muted" style="font-size:13px;margin-right:6px">Request a payout:</span>
      ${funded.map(a=>{const av=Math.max(0,(a.balance-a.initial_balance)*(a.profit_split_pct||80)/100);
        return `<button class="btn-o sm" onclick="openPayoutModal(${a.id},${av.toFixed(2)})">${esc(a.login)} · $${fmt(av)} available</button>`}).join('')}
    </div>`:''}
    ${data.requests.length?`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.payout-req">
      <thead><tr><th>Date</th><th>Account</th><th>Profit</th><th>Requested</th><th>Method</th><th>Status</th></tr></thead>
      <tbody>`+data.requests.map(r=>`<tr>
        <td class="muted" data-sort="${esc(r.ts||'')}">${dstr(r.ts)}</td>
        <td class="num">${esc(r.account)}${r.express?' <span style="font-size:10px;font-weight:700;letter-spacing:.4px;color:var(--orange,#f59e0b);border:1px solid currentColor;border-radius:6px;padding:1px 5px;vertical-align:1px">EXPRESS</span>':''}</td>
        <td class="num">$${fmt(r.profit_amount)}</td><td class="num">$${fmt(r.trader_share)}</td>
        <td class="muted">${esc(payoutMethodLabel(r.method))}</td>
        <td><span class="status ${r.status==='paid'?'paid':r.status==='pending'?'pending':'failed'}"><span class="dot"></span>${esc(r.status)}</span>
          ${r.status==='pending'&&r.expected_by?`<div class="muted" style="font-size:11px">decision by ${dstr(r.expected_by)}</div>`:''}
          ${r.status==='rejected'&&r.reject_reason?`<div class="muted" style="font-size:11px;max-width:220px">${esc(r.reject_reason)}</div>`:''}</td></tr>`).join('')+`
      </tbody></table></div>`
      :`<div class="empty"><h3>No payout requests yet</h3><p>Pass a challenge, get funded and request your first performance reward. Your challenge fee comes back with it.</p></div>`}

    ${(data.history||[]).length?`<div class="sec-card" style="margin-top:18px">
      <h3>Payout history</h3>
      <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Rewards already paid out to you.</p>
      <div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.payout-hist">
        <thead><tr><th>Date</th><th>Account</th><th>Account profit</th><th>Paid to you</th><th>Method</th><th class="no-sort"></th></tr></thead>
        <tbody>${data.history.map(h=>`<tr>
          <td class="muted" data-sort="${esc(h.ts||'')}">${dstr(h.ts)}</td>
          <td class="num">${esc(h.account)}</td>
          <td class="num">$${fmt(h.profit_amount)}</td>
          <td class="num up">+$${fmt(h.trader_share)}</td>
          <td class="muted">${esc(h.method||'—')}</td>
          <td>${h.cert_token?`<a class="btn-o sm" href="/payout/${esc(h.cert_token)}" target="_blank" rel="noopener">Certificate</a>`
                            :`<button class="btn-o sm" onclick="makePayoutCert(${h.id})">Get certificate</button>`}</td>
        </tr>`).join('')}</tbody></table></div>
      <p class="muted" style="font-size:12.5px;margin-top:12px">Total paid out: <b>$${fmt(data.summary.total_paid)}</b></p>
    </div>`:''}`;
 },

 async orders(){
  const [os,cr]=await Promise.all([api('/api/orders'),api('/api/me/credits').catch(()=>null)]);
  window._orders=os;
  /* Store credits card shows up only once the trader has any history — a
     zero-balance wall of empty ledger would just be noise. */
  const credits=(cr&&(cr.balance_usd>0||(cr.ledger||[]).length))?`
    <div class="sec-card" style="margin-top:18px">
      <h3>Store credits</h3>
      <p class="muted" style="font-size:12.5px;margin:4px 0 12px">1 credit = $1, applied to your
        next challenge at checkout. Current balance: <b class="mono">$${fmt(cr.balance_usd)}</b></p>
      <div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.credits">
        <thead><tr><th>Date</th><th>Amount</th><th>Note</th><th>Order</th></tr></thead>
        <tbody>${cr.ledger.map(l=>`<tr>
          <td class="muted" data-sort="${esc(l.ts||'')}">${dstr(l.ts)}</td>
          <td class="num ${l.amount>=0?'up':'down'}">${l.amount>=0?'+':'−'}$${fmt(Math.abs(l.amount))}</td>
          <td class="muted">${esc(l.note||(l.amount<0?'Used at checkout':'Credit granted'))}</td>
          <td class="num">${l.order_id?'#'+l.order_id:'—'}</td></tr>`).join('')}</tbody></table></div>
    </div>`:'';
  $('view').innerHTML=`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.orders">
    <thead><tr><th>#</th><th>Product</th><th>Amount</th><th>Status</th><th>Account</th><th class="no-sort"></th></tr></thead>
    <tbody>`+(os.map(o=>`<tr>
      <td class="num">${o.id}</td><td>${esc(o.product_label||o.product_key)}</td>
      <td class="num">$${fmt(orderAmount(o))}${o.coupon?` <span class="up" style="font-size:11px">(${esc(o.coupon)})</span>`:''}</td>
      <td><span class="status ${o.status==='paid'?'paid':'pending'}"><span class="dot"></span>${esc(o.status)}</span></td>
      <td class="num">${o.account_id||'—'}</td>
      <td style="text-align:right;white-space:nowrap">
        ${o.account?`<button class="btn-o sm" id="cbtn-${o.id}" onclick="toggleOrderCreds(${o.id})">${ICO.key} Show credentials</button> `:''}
        ${o.status==='paid'?`<button class="btn-o sm" onclick="openInvoice(${o.id})">${ICO.print} Invoice</button>`:''}</td></tr>
      ${o.account?`<tr class="hidden tr-sub" id="crow-${o.id}"><td colspan="6" class="sub-row">${credsBlock(o.account,true)}</td></tr>`:''}`)
      .join('')||'<tr><td colspan="6" class="muted" style="padding:26px 18px">No orders yet</td></tr>')+`
    </tbody></table></div>`+credits;
 },

 async kyc(){
  const s=ME.kyc_status;
  /* Powod pauzy stoi NAD formularzem, nie w mailu: klient trafia tu takze
     wprost z zakladki i musi wiedziec, czemu reszta panelu zniknela. */
  const hold=ME.kyc_locked&&s!=='approved'?`<div class="panel" style="margin-bottom:14px;border-color:var(--red-line);background:var(--red-bg)">
      <b style="font-size:13.5px">Your dashboard is paused until we verify your identity.</b>
      <p class="muted" style="font-size:12.5px;margin-top:4px">Your trading account keeps running — nothing changes on the platform.
         We review documents within one business day and the dashboard opens the moment it's approved.</p>
    </div>`:'';
  /* Weryfikacja otwiera sie dopiero po przejsciu ewaluacji. Pokazujemy powod
     zamiast formularza — wypelnienie go i tak skonczyloby sie odmowa z serwera,
     a tak trader od razu wie, czego brakuje. Zlozone juz zgloszenia (pending /
     approved / rejected) obslugujemy dalej normalnie: bramka dotyczy SKLADANIA,
     a nie ogladania wlasnego statusu. */
  if(ME.kyc_available===false&&(!s||s==='none')){
    $('view').innerHTML=`<div class="empty">
      ${ICO.shield}
      <h3>Verification opens after your first funded account</h3>
      <p>Pass an evaluation and this page unlocks. We only ask for identity documents
         from traders who have a payout to claim — if you would rather verify now,
         write to support and we will open it for you.</p>
      <button class="btn-p" onclick="go('accounts')">View my challenges</button></div>`;
    return;
  }
  if(s==='approved'){
    $('view').innerHTML=`<div class="panel" style="display:flex;gap:14px;align-items:center">
      <div class="tile-ic green">${ICO.shield}</div>
      <div><h3>Identity verified</h3><p class="muted" style="font-size:13.5px">Your KYC is approved. You can request payouts on funded accounts.</p></div></div>`;
    return;
  }
  /* Poprawka w trakcie oczekiwania: rozmazany skan dowodu widac dopiero po
     wyslaniu, a bez tej furtki trader musialby czekac na odrzucenie, zeby
     wgrac czytelny. Serwer nadpisuje to samo zgloszenie — nie zaklada nowego. */
  if(s==='pending'&&!window._kycPoprawka){
    $('view').innerHTML=hold+`<div class="panel" style="display:flex;gap:14px;align-items:center">
      <div class="tile-ic orange">${ICO.shield}</div>
      <div style="flex:1"><h3>Documents under review</h3><p class="muted" style="font-size:13.5px">Our team is reviewing your submission. You'll get an e-mail once it's approved.</p></div>
      <button class="btn-o" onclick="window._kycPoprawka=true;go('kyc')">Replace documents</button></div>`;
    return;
  }
  $('view').innerHTML=hold+`
    ${s==='rejected'?`<div class="panel" style="margin-bottom:14px;border-color:var(--red-line);background:var(--red-bg)">
      <b style="font-size:13.5px">Your previous verification was declined.</b>
      ${ME.kyc_reject_reason?`<p style="font-size:12.5px;margin-top:4px"><b>Reason:</b> ${esc(ME.kyc_reject_reason)}</p>`:''}
      <p class="muted" style="font-size:12.5px;margin-top:4px">Please double-check your details and documents, then submit again. Reach out via Support if you need help.</p>
    </div>`:''}
    ${s==='pending'?`<div class="panel" style="margin-bottom:14px">
      <b style="font-size:13.5px">Replacing your submission</b>
      <p class="muted" style="font-size:12.5px;margin-top:4px">What you send now replaces the documents already waiting for review —
         it does not start a second request, and it keeps your place in the queue.
         <a href="#" onclick="window._kycPoprawka=false;go('kyc');return false">Cancel</a></p>
    </div>`:''}
    <div class="sec-card">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
        <div class="tile-ic blue">${ICO.shield}</div><h3 style="margin:0">Submit KYC Documents</h3>
      </div>
      <p class="muted" style="font-size:13px;margin-bottom:18px">Required once, before your first payout. Data is used only for verification.</p>
      <div class="grid2" style="margin-bottom:10px">
        <div><label class="muted" style="font-size:12px">Full Name (as on ID)</label><input id="k-name" class="inp" placeholder="John Smith"></div>
        <div><label class="muted" style="font-size:12px">Date of Birth</label>
          <div class="dob-wrap">
            <input id="k-dob" class="inp" placeholder="MM/DD/YYYY" inputmode="numeric" maxlength="10"
              autocomplete="bday" oninput="dobMask(this)">
            <button type="button" class="dob-btn" aria-label="Pick from calendar" onclick="dobCalToggle(event)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>
            </button>
            <div id="dobcal" class="dobcal hidden"></div>
          </div></div>
        <div><label class="muted" style="font-size:12px">Country</label>
          <select id="k-country" class="inp">
            <option value="">Select a country…</option>
            ${COUNTRY_NAMES.map(k=>`<option${ME.kyc_country===k?' selected':''}>${esc(k)}</option>`).join('')}
          </select></div>
        <div><label class="muted" style="font-size:12px">ID Type</label>
          <select id="k-idtype" class="inp"><option>Passport</option><option>National ID</option><option>Driver's License</option></select></div>
      </div>
      <div style="margin-bottom:10px"><label class="muted" style="font-size:12px">Full Address</label>
        <input id="k-address" class="inp" placeholder="Street, city, postal code"></div>
      <div style="margin-bottom:18px"><label class="muted" style="font-size:12px">ID Number</label>
        <input id="k-idnum" class="inp" placeholder="Document number"></div>

      <h3 style="font-size:15px;margin-bottom:6px">Document Uploads</h3>
      ${[['f-front','ID Front *','JPG, PNG or PDF, max 5 MB'],
         ['f-back','ID Back (optional)','JPG, PNG or PDF, max 5 MB'],
         ['f-res','Proof of Residence *','Utility bill or bank statement, max 3 months old']]
        .map(([id,label,hint])=>`
      <div class="file-row"><div class="fl"><b>${label}</b><p>${hint}</p></div>
        <div class="file-pick">
          <input type="file" id="${id}" accept=".jpg,.jpeg,.png,.pdf" onchange="filePicked(this)">
          <button type="button" class="btn-o sm" onclick="this.closest('.file-pick').querySelector('input').click()">Choose file</button>
          <span class="fp-name">No file chosen</span>
        </div></div>`).join('')}
      <p id="kyc-err" class="form-err hidden" style="margin-top:10px"></p>
      <button class="btn-p lg" style="margin-top:16px" onclick="submitKyc()">Submit KYC</button>
    </div>`;
 },

 async support(){
  const tv=window._ticketView;
  if(tv==='new'){
    $('view').innerHTML=`
      <button class="backlink" onclick="window._ticketView=null;go('support')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg> Back to Tickets</button>
      <div class="sec-card"><h3>Create New Ticket</h3>
        <div style="display:flex;flex-direction:column;gap:11px;margin-top:12px">
          <input id="t-subject" class="inp" placeholder="Subject">
          <textarea id="t-msg" class="inp" rows="6" placeholder="Describe your issue…"></textarea>
          <button class="btn-p" onclick="submitTicket()">Create Ticket</button>
        </div></div>`;
    return;
  }
  if(typeof tv==='number'){
    const t=await api('/api/me/tickets/'+tv);
    $('view').innerHTML=`
      <button class="backlink" onclick="window._ticketView=null;go('support')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg> Back to Tickets</button>
      <div class="sec-card">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px">
          <h3 style="margin:0">${esc(t.subject)}</h3>
          <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${t.status}</span>
        </div>
        <div class="thread">${t.thread.map(m=>`
          <div class="msg ${m.author}"><div class="who">${m.author==='admin'?'Support team':'You'} · ${dstr(m.ts)}</div>${esc(m.body)}</div>`).join('')}
        </div>
        ${t.status!=='closed'?`
        <div style="display:flex;gap:10px;margin-top:16px">
          <input id="t-reply" class="inp" placeholder="Write a reply…">
          <button class="btn-p" onclick="replyTicket(${t.id})">Send</button>
        </div>`:'<p class="muted" style="font-size:12.5px;margin-top:14px">This ticket is closed. Create a new one if you need more help.</p>'}
      </div>`;
    return;
  }
  const rows=await api('/api/me/tickets');
  const head=`<div style="display:flex;justify-content:flex-end;margin-bottom:14px">
    <button class="btn-p sm" onclick="window._ticketView='new';go('support')">+ New Ticket</button></div>`;
  if(!rows.length){
    $('view').innerHTML=head+`<div class="empty">
      ${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:38px;height:38px;margin:0 auto 14px;display:block;color:var(--dim)"><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.8A8 8 0 1 1 21 12z"/></svg>'}
      <h3>No Tickets Yet</h3><p>Need help with anything? Our team replies by the next business day.</p>
      <button class="btn-p" onclick="window._ticketView='new';go('support')">Create Your First Ticket</button></div>`;
    return;
  }
  $('view').innerHTML=head+`<div class="tbl-wrap">`+rows.map(t=>`
    <div class="ticket-row" onclick="window._ticketView=${t.id};go('support')">
      <div class="tile-ic blue" style="width:36px;height:36px;flex:0 0 36px">${ICO.chat}</div>
      <div class="sub"><b>${esc(t.subject)}</b><span>#${t.id} · ${t.messages} message${t.messages>1?'s':''} · updated ${dstr(t.last_ts)}</span></div>
      <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${t.status}</span>
    </div>`).join('')+`</div>`;
 },

 async settings(){
  const n=ME.notify||{};
  const sw=(key,label,desc)=>`
    <div class="switch-row"><div><h4>${label}</h4><p>${desc}</p></div>
      <label class="switch"><input type="checkbox" ${n[key]?'checked':''}
        onchange="patchPref('${key}',this.checked)"><i></i></label></div>`;
  $('view').innerHTML=`
    <div class="card-cols">
    <div class="sec-card" style="max-width:640px"><h3>Profile Information</h3>
      <div style="display:flex;flex-direction:column;gap:11px;margin-top:12px">
        <div><label class="muted" style="font-size:12px">Full Name</label>
          <input id="s-name" class="inp" value="${esc(ME.full_name||'')}"></div>
        <div><label class="muted" style="font-size:12px">Email Address</label>
          <input class="inp" value="${esc(ME.email)}" disabled>
          <p class="muted" style="font-size:11.5px;margin-top:4px">Email cannot be changed.</p></div>
        <button class="btn-p" style="align-self:flex-start" onclick="saveProfile()">Save Changes</button>
      </div></div>

    <div class="sec-card" style="max-width:640px"><h3>Change Password</h3>
      <div style="display:flex;flex-direction:column;gap:11px;margin-top:12px">
        <input id="s-cur" type="password" class="inp" placeholder="Current password" autocomplete="current-password">
        <input id="s-new" type="password" class="inp" placeholder="New password (min. 8 characters)" autocomplete="new-password">
        <input id="s-new2" type="password" class="inp" placeholder="Confirm new password" autocomplete="new-password">
        <button class="btn-p" style="align-self:flex-start" onclick="savePassword()">Change Password</button>
      </div></div>

    <div class="sec-card" style="max-width:640px"><h3>Appearance</h3>
      <div class="switch-row" style="border-bottom:0"><div><h4>Dark Mode</h4>
        <p>Switch the dashboard to a dark look. Saved on this device.</p></div>
        <label class="switch"><input type="checkbox" id="s-theme" ${themeNow()==='dark'?'checked':''}
          onchange="if((this.checked?'dark':'light')!==themeNow())toggleTheme()"><i></i></label></div>
    </div>

    <div class="sec-card" style="max-width:640px"><h3>Notification Preferences</h3>
      <div style="margin-top:6px">
        ${sw('updates','Email Updates','KYC results, support replies and store-credit e-mails. Essential e-mails (verification, MT5 credentials) always arrive.')}
        ${sw('trading','Trading Alerts','Phase passed, funded status and rule-breach notifications.')}
        ${sw('payouts','Payout Notifications','Updates about your payout requests.')}
        ${sw('marketing','Daily Recap & Offers','A short recap of the previous trading day (only when you traded) and occasional product news.')}
      </div>
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--line)">
        <div class="switch-row"><div><h4>Push Notifications</h4>
          <p id="pushStatus">Checking this device…</p></div>
          <button class="btn-p sm" id="pushBtn" onclick="togglePush()" style="display:none"></button></div>
        <a class="btn-o sm" id="pushInstallLink" href="/install" target="_blank" rel="noopener" style="display:none;margin-top:8px">How to install the app</a>
      </div></div>

    <div class="sec-card" style="max-width:640px;border-color:var(--red-line)"><h3 style="color:var(--red)">Danger Zone</h3>
      <p class="muted" style="font-size:13px;margin:8px 0 14px">Deleting your account anonymizes your personal data and permanently disables login.
        Trading records are retained for accounting. This cannot be undone.</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <input id="s-delpass" type="password" class="inp" placeholder="Confirm with your password" style="max-width:280px">
        <button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="deleteAccount()">Delete Account</button>
      </div></div>
    </div>`;
  initPushUI();
 },
};

/* ---------- view actions ---------- */
function goAffiliate(){go('rewards')}

function openJournalModal(){
  api('/api/me/accounts').then(accs=>{
    const box=document.createElement('div');
    box.id='j-modal'; box.className='modal-wrap';
    box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head"><h3>New Journal Entry</h3>
        <button class="icon-btn" onclick="document.getElementById('j-modal').remove()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      <div class="stack" style="margin-top:8px">
        <input id="j-title" class="inp" placeholder="Title, e.g. 'NY session — gold short'">
        <select id="j-acc" class="inp"><option value="">No account</option>
          ${accs.map(a=>`<option value="${a.id}">${esc(a.login)} · ${esc(a.product_key)}</option>`).join('')}</select>
        <textarea id="j-content" class="inp" rows="6" placeholder="What happened? What did you learn?"></textarea>
        <button class="btn-p" onclick="saveJournal()">Save entry</button>
      </div></div>`;
    box.onclick=()=>box.remove();
    document.body.appendChild(box);
    setTimeout(()=>$('j-title').focus(),50);
  });
}
async function saveJournal(){
  try{
    await api('/api/me/journal',{method:'POST',body:JSON.stringify({
      title:$('j-title').value,content:$('j-content').value,
      account_id:$('j-acc').value?parseInt($('j-acc').value):null})});
    document.getElementById('j-modal')?.remove();
    toast('Entry saved.','ok');go('journal');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function delJournal(id){
  try{await api('/api/me/journal/'+id,{method:'DELETE'});go('journal')}
  catch(e){toast('Error: '+e.message,'err')}
}

async function submitTicket(){
  try{
    await api('/api/me/tickets',{method:'POST',body:JSON.stringify({
      subject:$('t-subject').value,message:$('t-msg').value})});
    window._ticketView=null;toast('Ticket created. We usually reply within one business day.','ok');go('support');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function replyTicket(id){
  try{
    await api(`/api/me/tickets/${id}/reply`,{method:'POST',body:JSON.stringify({message:$('t-reply').value})});
    go('support');
  }catch(e){toast('Error: '+e.message,'err')}
}

async function saveProfile(){
  const nazwa=nameCheck($('s-name').value,'Full name');
  if(!nazwa.ok){toast(nazwa.msg,'err');return}
  try{await api('/api/me',{method:'PATCH',body:JSON.stringify({full_name:nazwa.value})});
    ME=await api('/api/auth/me');boot();toast('Profile saved.','ok');go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function savePassword(){
  if($('s-new').value!==$('s-new2').value){toast('Passwords do not match.','err');return}
  try{const r=await api('/api/me/password',{method:'POST',body:JSON.stringify({
    current_password:$('s-cur').value,new_password:$('s-new').value})});
    /* Older sessions just died (password fingerprint in the token) — swap in
       the fresh token so THIS session survives the change. W podglądzie admina
       NIE dotykamy localStorage: nadpisałby token właściciela. */
    if(r.token&&!IMP){TOKEN=r.token;localStorage.setItem('pf_token',TOKEN)}
    toast('Password changed.','ok');go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function patchPref(key,val){
  try{const r=await api('/api/me',{method:'PATCH',body:JSON.stringify({['notify_'+key]:val})});
    ME.notify=r.notify;
  }catch(e){toast('Error: '+e.message,'err')}
}

/* ---------- web push (PWA) ---------- */
const b64ToU8=b64=>{const p='='.repeat((4-b64.length%4)%4);
  const raw=atob((b64+p).replace(/-/g,'+').replace(/_/g,'/'));
  return Uint8Array.from(raw,c=>c.charCodeAt(0))};

async function initPushUI(){
  const st=$('pushStatus'),btn=$('pushBtn'),lnk=$('pushInstallLink');
  if(!st)return;
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent);
  /* iOS exposes PushManager only inside a PWA installed on the home screen */
  if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){
    st.textContent=ios?'On iPhone: install the app to your home screen first, then enable push here.'
                      :'Push is not supported in this browser.';
    if(ios)lnk.style.display='inline-flex';
    return;
  }
  let cfg;try{cfg=await api('/api/push/public-key')}catch(_){cfg={enabled:false}}
  if(!cfg.enabled){st.textContent='Push is not configured on the server yet.';return}
  window._pushKey=cfg.key;
  if(Notification.permission==='denied'){
    st.textContent='Notifications are blocked for this site in your browser settings.';return}
  const reg=await navigator.serviceWorker.ready;
  const sub=await reg.pushManager.getSubscription();
  btn.style.display='inline-flex';
  if(sub){st.textContent='Enabled on this device.';btn.textContent='Disable';}
  else{st.textContent='Payout updates, MT5 credentials and streak reminders, straight to this device.';
       btn.textContent='Enable';}
}

async function togglePush(){
  const btn=$('pushBtn');btn.disabled=true;
  try{
    const reg=await navigator.serviceWorker.ready;
    let sub=await reg.pushManager.getSubscription();
    if(sub){
      await api('/api/me/push/unsubscribe',{method:'POST',body:JSON.stringify({endpoint:sub.endpoint})});
      await sub.unsubscribe();
      toast('Push notifications disabled on this device.');
    }else{
      const perm=await Notification.requestPermission();
      if(perm!=='granted'){toast('Notifications were not allowed.','err');return}
      sub=await reg.pushManager.subscribe({userVisibleOnly:true,
        applicationServerKey:b64ToU8(window._pushKey)});
      await api('/api/me/push/subscribe',{method:'POST',body:JSON.stringify(sub.toJSON())});
      toast('Push notifications enabled.','ok');
    }
  }catch(e){toast('Push setup failed: '+e.message,'err')}
  finally{btn.disabled=false;initPushUI()}
}
async function deleteAccount(){
  const pass=$('s-delpass').value;
  if(!pass){toast('Enter your password to confirm.','err');return}
  try{await api('/api/me/delete',{method:'POST',body:JSON.stringify({password:pass})});
    toast('Account deleted.','ok');setTimeout(logout,800);
  }catch(e){toast('Error: '+e.message,'err')}
}

/* A phone photo is 3-5 MB, and serverless hosting cuts the WHOLE request body
   above ~4.5 MB — three documents at once ended in a 413 before the backend
   ever saw them. So images are scaled in the browser: the longer side down to
   1600 px is enough to read a document, and the file drops to ~200-400 KB.
   A PDF cannot be reprocessed like that — it goes as-is, only size-checked. */
const MAX_SIDE=1600, MAX_BYTES=4*1024*1024;
function shrinkImage(fileObj){
  return new Promise((ok,rej)=>{
    if(!fileObj.type.startsWith('image/'))return ok(fileObj);
    const url=URL.createObjectURL(fileObj), img=new Image();
    img.onload=()=>{
      URL.revokeObjectURL(url);
      const imgScale=Math.min(1,MAX_SIDE/Math.max(img.width,img.height));
      if(imgScale===1&&fileObj.size<=800*1024)return ok(fileObj);
      const c=document.createElement('canvas');
      c.width=Math.round(img.width*imgScale); c.height=Math.round(img.height*imgScale);
      c.getContext('2d').drawImage(img,0,0,c.width,c.height);
      c.toBlob(b=>{
        if(!b)return ok(fileObj);
        const jpgName=fileObj.name.replace(/\.[^.]+$/,'')+'.jpg';
        ok(new File([b],jpgName,{type:'image/jpeg'}));
      },'image/jpeg',0.82);
    };
    img.onerror=()=>{URL.revokeObjectURL(url);ok(fileObj)};
    img.src=url;
  });
}
async function uploadDocument(fieldName,fileObj){
  const fd=new FormData(); fd.append(fieldName,fileObj);
  const r=await fetch('/api/me/kyc/docs',{method:'POST',
    headers:{'Authorization':'Bearer '+TOKEN},body:fd});
  if(!r.ok){
    // a 413 is cut by the server/hosting, so the body may be empty — explain it ourselves.
    const errDetail=(await r.json().catch(()=>({}))).detail;
    throw new Error(errDetail||(r.status===413
      ? 'File too large for upload. Please use a smaller photo or a PDF under 4 MB.'
      : r.status));
  }
}
/* Native file inputs speak the BROWSER's language ("Wybierz plik" for Polish
   visitors) — the site is English-only, so the control is ours. */
function filePicked(inp){
  const n=inp.closest('.file-pick').querySelector('.fp-name');
  const f=inp.files&&inp.files[0];
  n.textContent=f?f.name:'No file chosen';
  n.classList.toggle('has',!!f);
}
/* US date entry (MM/DD/YYYY) — digits auto-slash while typing. */
function dobMask(i){
  let v=i.value.replace(/\D/g,'').slice(0,8);
  if(v.length>=5)v=v.slice(0,2)+'/'+v.slice(2,4)+'/'+v.slice(4);
  else if(v.length>=3)v=v.slice(0,2)+'/'+v.slice(2);
  i.value=v;
}
/* Small English calendar for the DOB field — the native date picker follows
   the browser language, ours stays English like the rest of the site. */
const DOB_MONTHS=['January','February','March','April','May','June','July',
  'August','September','October','November','December'];
let _dobY=1995,_dobM=0;
function _dobSel(){
  const m=($('k-dob').value||'').match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if(!m)return null;
  const mo=+m[1]-1,d=+m[2],y=+m[3];
  return (mo>=0&&mo<=11&&d>=1&&d<=31&&y>=1900)?{y,mo,d}:null;
}
function dobCalToggle(e){
  e.stopPropagation();
  const c=$('dobcal');
  if(c.classList.contains('hidden')){
    const s=_dobSel();
    _dobY=s?s.y:1995;_dobM=s?s.mo:0;
    dobCalRender();c.classList.remove('hidden');
  }else c.classList.add('hidden');
}
function dobCalRender(){
  const startDow=new Date(_dobY,_dobM,1).getDay();       /* US week: Sunday first */
  const days=new Date(_dobY,_dobM+1,0).getDate();
  const maxY=new Date().getFullYear();
  const sel=_dobSel();
  let years='';for(let y=maxY;y>=1900;y--)years+=`<option value="${y}"${y===_dobY?' selected':''}>${y}</option>`;
  $('dobcal').innerHTML=`
    <div class="dc-head">
      <select class="dc-sel" onchange="_dobM=+this.value;dobCalRender()">
        ${DOB_MONTHS.map((n,i)=>`<option value="${i}"${i===_dobM?' selected':''}>${n}</option>`).join('')}
      </select>
      <select class="dc-sel dc-year" onchange="_dobY=+this.value;dobCalRender()">${years}</select>
    </div>
    <div class="dc-grid">
      ${['Su','Mo','Tu','We','Th','Fr','Sa'].map(d=>`<span class="dc-dow">${d}</span>`).join('')}
      ${Array.from({length:startDow},()=>'<span></span>').join('')}
      ${Array.from({length:days},(_,i)=>{const d=i+1;
        const on=sel&&sel.d===d&&sel.mo===_dobM&&sel.y===_dobY;
        return `<button type="button" class="dc-day${on?' on':''}" onclick="dobPick(${d})">${d}</button>`}).join('')}
    </div>`;
}
function dobPick(d){
  $('k-dob').value=`${String(_dobM+1).padStart(2,'0')}/${String(d).padStart(2,'0')}/${_dobY}`;
  $('dobcal').classList.add('hidden');
}
document.addEventListener('click',e=>{
  const c=document.getElementById('dobcal');
  if(c&&!c.classList.contains('hidden')&&!e.target.closest('.dob-wrap'))c.classList.add('hidden');
});
async function submitKyc(){
  const err=m=>{const el=$('kyc-err');el.textContent=m;el.classList.remove('hidden')};
  const name=$('k-name').value.trim(),country=$('k-country').value.trim();
  if(!name||!country){err('Full name and country are required.');return}
  const kycNazwa=nameCheck(name,'Full name');
  if(!kycNazwa.ok){err(kycNazwa.msg);return}
  const dob=$('k-dob').value.trim();
  if(dob){
    const m=dob.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    const ok=m&&+m[1]>=1&&+m[1]<=12&&+m[2]>=1&&+m[2]<=31
      &&+m[3]>=1900&&+m[3]<=new Date().getFullYear();
    if(!ok){err('Date of birth must be MM/DD/YYYY.');return}
  }
  const front=$('f-front').files[0],back=$('f-back').files[0],res=$('f-res').files[0];
  if(!front||!res){err('ID Front and Proof of Residence are required.');return}
  const btn=document.querySelector("button[onclick='submitKyc()']");
  if(btn){btn.disabled=true;btn.textContent='Uploading…'}
  try{
    await api('/api/me/kyc',{method:'POST',body:JSON.stringify({
      full_name:name,country,dob:dob||null,address:$('k-address').value||null,
      id_type:$('k-idtype').value,id_number:$('k-idnum').value||null})});
    // One request per document — even large PDFs never add up to the limit then.
    for(const [fieldName,fileObj] of [['id_front',front],['id_back',back],['residence',res]]){
      if(!fileObj)continue;
      const prepared=await shrinkImage(fileObj);
      if(prepared.size>MAX_BYTES){
        throw new Error(`${fileObj.name} is ${(prepared.size/1024/1024).toFixed(1)} MB. The limit is 4 MB, please use a smaller file.`);
      }
      await uploadDocument(fieldName,prepared);
    }
    const bylaPoprawka=!!window._kycPoprawka;
    window._kycPoprawka=false;
    ME=await api('/api/auth/me');boot();
    toast(bylaPoprawka?'Documents replaced. Your submission is still under review.'
                      :'KYC submitted. Documents are under review.','ok');
    go('kyc');
  }catch(e){err('Error: '+e.message)}
  finally{if(btn){btn.disabled=false;btn.textContent='Submit KYC'}}
}

/* ---------- invoice ---------- */
/* Order amount on the list and the invoice.
   Grants store 0 USD in the database, but an invoice with a zero looks like a
   free product — so we fall back to the CATALOG price. When a paid tier was
   set on the grant (BOGO), `amount_usd` already holds it and it wins. */
function orderAmount(o){return (o.amount_usd>0||o.credits_used>0)?o.amount_usd:(o.list_price||0)}

/* Invoice line items. On an upgraded order the invoice names the tier the client
   ACTUALLY paid for, plus a line describing the upgrade — and the rows always
   add up to the total (the add-on row used to be dropped on promo orders). */
function invRows(o){
  const amountCell=v=>`<td style="text-align:right;font-family:var(--mono)">${v}</td>`;
  const wk=o.weekend_trading?`<tr><td>Weekend Trading add-on — 2 extra trading days/week</td>${amountCell('$'+fmt(199))}</tr>`:'';
  /* Store credit is a negative row, so the rows still add up to the charged
     total; the plan row shows the PRE-credit price. */
  const kredyt=o.credits_used>0?o.credits_used:0;
  const cr=kredyt?`<tr><td style="color:#64748b">Store credit applied</td>${amountCell('−$'+fmt(kredyt))}</tr>`:'';
  const plan=Math.max(0,orderAmount(o)+kredyt-(o.weekend_trading?199:0));
  const coupon=o.coupon?` (${esc(o.coupon)})`:'';
  const upgraded=o.bogo_paid_label&&o.bogo_paid_size&&o.account_size&&o.bogo_paid_size<o.account_size;
  if(upgraded){
    const k=v=>v>=1e6?'$'+(v/1e6)+'M':'$'+Math.round(v/1000)+'K';
    return `<tr>
        <td>Evaluation program access — ${esc(o.bogo_paid_label)}${coupon}</td>
        ${amountCell('$'+fmt(plan))}</tr>
      <tr><td style="color:#64748b">Free size upgrade — ${k(o.bogo_paid_size)} →
        <b style="color:#0f172a">${k(o.account_size)}</b> allocation</td>
        ${amountCell('included')}</tr>`+wk+cr;
  }
  return `<tr>
    <td>Evaluation program access — ${esc(o.product_label||o.product_key)}${coupon}</td>
    ${amountCell('$'+fmt(plan))}</tr>`+wk+cr;
}

function openInvoice(orderId){
  const o=(window._orders||[]).find(x=>x.id===orderId);
  if(!o)return;
  const box=document.createElement('div');
  box.id='inv-modal'; box.className='modal-wrap';
  const date=dutc(o.created_at).toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric'});
  box.innerHTML=`<div class="inv-print" onclick="event.stopPropagation()">
    <div class="inv-head">
      <div style="display:flex;align-items:center;gap:10px"><img src="/static/img/logo.png" alt="">
        <div><div class="inv-h1">${esc(SITE_NAME)}</div><div style="font-size:11px;color:#64748b">Trading skills evaluation platform</div></div></div>
      <div class="inv-meta"><b>INVOICE #${o.id}</b><br>${date}<br>Status: ${esc(o.status)}</div>
    </div>
    <div style="font-size:12.5px;color:#64748b">Billed to<br><b style="color:#0f172a">${esc(ME.full_name||ME.email)}</b><br>${esc(ME.email)}</div>
    <table class="inv-tbl">
      <thead><tr><th>Description</th><th style="text-align:right">Amount</th></tr></thead>
      <tbody>${invRows(o)}</tbody>
    </table>
    <div class="inv-total"><span>Total</span><span>$${fmt(orderAmount(o))} USD</span></div>
    <div class="inv-foot">One-time fee for access to a simulated trading evaluation on a demo account with virtual funds.
      No real-market trading service is provided. Fee refundable with the first payout under the Refund Policy.</div>
    <div class="noprint" style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px">
      <button class="btn-o sm" onclick="document.getElementById('inv-modal').remove()">Close</button>
      <button class="btn-p sm" onclick="window.print()">${ICO.print} Print / Save PDF</button>
    </div>
  </div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
}

/* ---------- credentials (compact) ---------- */
function credsBlock(a, compact){
  if(!a) return '';
  if(a.status==='provisioning' || !a.platform_password){
    return `<div class="creds-wait" onclick="event.stopPropagation()"><span class="spin"></span>
      Opening your account on <b>MetaQuotes-Demo</b>. Credentials appear here and in your inbox within a minute.</div>`;
  }
  const row=(label,val)=>`
    <div class="cred-row">
      <span class="k">${label}</span>
      <span class="v"><code>${esc(val)}</code>
        <button class="copy" onclick="event.stopPropagation();copyVal(this,'${esc(String(val)).replace(/'/g,"\\'")}')" title="Copy">${ICO.copy}</button>
      </span>
    </div>`;
  return `
    <div class="creds" onclick="event.stopPropagation()">
      <div class="creds-h">${ICO.key} MT5 credentials</div>
      ${row('Server', a.platform_server||'—')}
      ${row('Login', a.platform_login||'—')}
      ${row('Password', a.platform_password)}
      ${compact?'':'<p style="font-size:11px;color:var(--dim);margin-top:6px">Works in MetaTrader 5 desktop, mobile and web, so pick this server name.</p>'}
    </div>`;
}

/* Odbior nagrody za prog odznak. Przycisk gasnie NA CZAS zadania, zeby dwuklik
   nie wyslal dwoch prosb — poza tym baza i tak trzyma UNIQUE (trader, prog),
   wiec drugie zadanie dostanie 409 zamiast drugiej nagrody. */
async function claimReward(tier, btn){
  await busy(btn,'Claiming…',async()=>{
    try{
      const r=await api('/api/me/achievements/claim',{method:'POST',body:JSON.stringify({tier})});
      toast(r.account?'Your free challenge is being set up.':'Reward code added to your account.','ok');
      go('achievements');
      if(r.account)setTimeout(()=>go('accounts'),1400);
    }catch(e){toast('Error: '+e.message,'err')}
  });
}

function copyVal(btn, val){
  navigator.clipboard.writeText(val).then(()=>{
    const old=btn.innerHTML; btn.innerHTML=ICO.check; btn.style.color='var(--green)';
    setTimeout(()=>{btn.innerHTML=old;btn.style.color=''},1200);
  });
}

/* Pytanie w oknie PORTALU, nie przegladarki. Natywny confirm() przedstawia sie
   jako "Komunikat ze strony protradersfunding.com", ma przyciski w jezyku
   systemu i wyglada jak ostrzezenie o zagrozeniu — czyli dokladnie odwrotnie
   niz zaproszenie do odebrania nagrody. Zwraca Promise<bool>, wiec podmiana
   `confirm(x)` na `await askConfirm({...})` jest jeden do jednego. */
function askConfirm({title,body,ok='Confirm',cancel='Cancel',danger=false}){
  return new Promise(resolve=>{
    const w=document.createElement('div');
    w.className='modal-wrap'; w.id='ask-modal';
    let zamkniete=false;
    const koniec=v=>{
      if(zamkniete)return; zamkniete=true;
      document.removeEventListener('keydown',klawisz);
      w.remove(); resolve(v);
    };
    const klawisz=e=>{if(e.key==='Escape')koniec(false)};
    w.onclick=e=>{if(e.target===w)koniec(false)};
    w.innerHTML=`<div class="modal" onclick="event.stopPropagation()" role="dialog" aria-modal="true">
      <div class="modal-head"><h3>${esc(title)}</h3>
        <button class="icon-btn" id="ask-x" aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      <p class="muted" style="font-size:13px;line-height:1.6;margin:2px 0 16px">${body}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="${danger?'btn-danger':'btn-p'}" id="ask-ok">${esc(ok)}</button>
        <button class="btn-o" id="ask-no">${esc(cancel)}</button>
      </div></div>`;
    document.body.appendChild(w);
    w.querySelector('#ask-ok').onclick=()=>koniec(true);
    w.querySelector('#ask-no').onclick=()=>koniec(false);
    w.querySelector('#ask-x').onclick=()=>koniec(false);
    document.addEventListener('keydown',klawisz);
    w.querySelector('#ask-ok').focus();
  });
}

/* Wymiana punktow na wlasny kod. Serwer jest jedynym miejscem, ktore liczy
   saldo i odejmuje punkty — tu tylko blokujemy podwojny klik i odswiezamy widok. */
async function redeemReward(key,btn){
  const r=(LOY&&LOY.rewards||[]).find(x=>x.key===key);
  if(!r)return;
  const zgoda=await askConfirm({
    title:`Trade ${fmt0(r.cost)} points for ${r.pct}% off?`,
    body:`You get a one-time code worth <b>${r.pct}% off</b> any challenge. The points come off `
      +`your balance straight away, and the code is yours alone.`,
    ok:`Redeem for ${fmt0(r.cost)} pts`,
    cancel:'Not yet',
  });
  if(!zgoda)return;
  await busy(btn,null,async()=>{
    try{
      const d=await api('/api/me/loyalty/redeem',{method:'POST',body:JSON.stringify({reward:key})});
      await VIEWS.loyalty();
      toast(`🎟️ Your code ${d.code.code} is ready — ${d.code.pct}% off your next challenge.`,'ok',10000);
      const el=document.querySelector('.rw-code');
      if(el&&window.RFX&&RFX.burstFrom)RFX.burstFrom(el,{count:60,palette:RFX.GOLD});
    }catch(e){toast('Error: '+e.message,'err')}
  });
}

/* ---------- purchase modal ---------- */
let PRODUCTS=[];

function openBuy(key){
  const p=PRODUCTS.find(x=>x.key===key);
  if(!p)return;
  const nm=(ME.full_name||'').trim().split(/\s+/);
  /* Kraj MUSI byc znany, zanim powstanie pole numeru: `phoneNational`
     odcina nim kierunkowy z zapisanego numeru. */
  const kraj=guessCountry();
  const box=document.createElement('div');
  box.id='buy-modal';
  box.className='modal-wrap';
  box.innerHTML=`
    <div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head">
        <h3>${esc(p.label)}</h3>
        <button class="icon-btn" onclick="closeBuy()" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      </div>
      <div class="plan-line">
        Simulated capital <span id="buy-cap">$${fmt0(pfPromo()&&p.promo_upgrade_size?p.promo_upgrade_size:p.account_size)}</span> · split ${p.profit_split_pct}%
      </div>
      ${p.promo_upgrade_size?`<div class="plan-promo" id="buy-promo" ${pfPromo()?'':'hidden'}><b>Upgrade Your Size</b> — you pay for the
        $${fmt0(p.account_size)} plan and we create a <b>$${fmt0(p.promo_upgrade_size)}</b> account.</div>`:''}
      <div class="quote-box" id="buy-quote"></div>
      <div class="note">Your MT5 demo account is registered with MetaQuotes <b>under these details</b>,
        they must be real; your credentials are delivered to them.</div>
      <div class="stack">
        <div class="grid2">
          <div><input id="c-first" placeholder="First name" class="inp" autocomplete="given-name"
            oninput="clearFieldErr('c-first')" value="${esc(ME.first_name||nm[0]||'')}">
            <p class="field-err hidden" id="c-first-err"></p></div>
          <div><input id="c-last" placeholder="Last name" class="inp" autocomplete="family-name"
            oninput="clearFieldErr('c-last')" value="${esc(ME.last_name||nm.slice(1).join(' ')||'')}">
            <p class="field-err hidden" id="c-last-err"></p></div>
        </div>
        <div>
          <div class="tel-wrap">
            <button type="button" class="tel-cc" id="c-cc" onclick="ccToggle(event)"
              aria-haspopup="listbox" aria-expanded="false" aria-label="Country calling code">
              <span class="flag" id="c-cc-flag"></span><span id="c-cc-dial">+1</span>
              <svg class="tel-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m6 9 6 6 6-6"/></svg>
            </button>
            <input id="c-phone" class="inp" inputmode="tel" autocomplete="tel-national"
              placeholder="Phone number" oninput="clearFieldErr('c-phone')" value="${esc(phoneNational(ME.phone,kraj))}">
            <div id="ccpop" class="ccpop hidden">
              <input id="cc-search" class="inp" placeholder="Search country or code"
                autocomplete="off" spellcheck="false" oninput="ccRender()">
              <div class="cc-list" id="cc-list" role="listbox"></div>
            </div>
          </div>
          <p class="field-err hidden" id="c-phone-err"></p>
        </div>
        <input id="c-email" class="inp" value="${esc(ME.email)}" disabled aria-label="Account e-mail">
        <p class="hint" style="text-align:left;margin:-6px 0 0">Purchases are tied to your <b>account e-mail</b>, and credentials
          and the invoice are delivered there. Different address?
          <button type="button" class="linklike" onclick="closeBuy();logout()">Log out</button> and register with it.</p>
        <input id="c-code" placeholder="Discount / promo code (optional)" class="inp"
          autocomplete="off" spellcheck="false"
          value="${esc(new URLSearchParams(location.search).get('coupon')||pfCoupon()||(p.promo_upgrade_size?pfPromo():''))}"
          oninput="codeInput()">
        <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer;padding:9px 12px;border:1px solid var(--line);border-radius:10px">
          <input type="checkbox" id="c-weekend" onchange="quoteRefresh(true)"
            ${new URLSearchParams(location.search).get('wt')==='1'?'checked':''}
            style="width:15px;height:15px;accent-color:var(--acc)">
          <span><b>Weekend Trading</b> — 2 extra trading days/week <b>+$199</b></span>
        </label>
        ${p.steps===0?`<label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer;padding:9px 12px;border:1px solid var(--line);border-radius:10px">
          <input type="checkbox" id="c-boost" onchange="quoteRefresh(true)"
            style="width:15px;height:15px;accent-color:var(--acc)">
          <span><b>Profit Split Boost</b> — keep ${Math.min(100,(p.profit_split_pct||70)+10)}% instead of ${p.profit_split_pct||70}% <b>+$149</b></span>
        </label>`:''}
        <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer;padding:9px 12px;border:1px solid var(--line);border-radius:10px">
          <input type="checkbox" id="c-express" onchange="quoteRefresh(true)"
            style="width:15px;height:15px;accent-color:var(--acc)">
          <span><b>Express Payout</b> — your payout requests jump the review queue <b>+$49</b></span>
        </label>
        ${ME.credits_usd>0?`<label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer;padding:9px 12px;border:1px solid var(--line);border-radius:10px">
          <input type="checkbox" id="c-usecr" checked onchange="quoteRefresh(true)"
            style="width:15px;height:15px;accent-color:var(--acc)">
          <span><b>Use my store credit</b> — $${fmt(ME.credits_usd)} available, 1 credit = $1</span>
        </label>`:''}
        <p id="buy-err" class="form-err hidden"></p>
        <button id="buy-go" onclick="buy('${esc(p.key)}')" class="btn-p lg" style="width:100%">Buy &amp; create my account</button>
        <p class="hint">One-time payment · fee refunded with your first payout</p>
      </div>
    </div>`;
  box.onclick=closeBuy;
  document.body.appendChild(box);
  window._buyKey=key;
  window._buyCode={coupon:null,promo:null};
  ccSet(kraj, false);
  codeCheck();
  setTimeout(()=>$('c-first').focus(),50);
}

/* Price breakdown in the buy modal, fed by /api/checkout/preview — the exact
   math the real checkout will run (coupon -> weekend add-on -> store credit).
   Display only: the server recomputes everything again at POST /api/checkout. */
let _quoteT=null;
function quoteRefresh(now){clearTimeout(_quoteT);_quoteT=setTimeout(quoteNow,now===true?0:400)}
async function quoteNow(){
  const p=PRODUCTS.find(x=>x.key===window._buyKey),box=$('buy-quote');
  if(!p||!box)return;
  const wk=!!($('c-weekend')&&$('c-weekend').checked);
  const sb=!!($('c-boost')&&$('c-boost').checked);
  const ex=!!($('c-express')&&$('c-express').checked);
  const uc=!$('c-usecr')||$('c-usecr').checked;
  const bc=window._buyCode||{};
  let q=null,previewFailed=false;
  try{
    q=await api('/api/checkout/preview?product_key='+encodeURIComponent(p.key)
      +'&coupon='+encodeURIComponent(bc.coupon||'')
      +'&promo_code='+encodeURIComponent(bc.promo||'')
      +'&weekend='+(wk?'1':'0')+'&split_boost='+(sb?'1':'0')
      +'&express='+(ex?'1':'0')+'&use_credits='+(uc?'1':'0'));
  }catch(e){
    /* The preview must never block buying — fall back to the catalog price. */
    previewFailed=true;
    if(e&&e.message&&/coupon/i.test(e.message))buyErr(e.message);
    q={plan_price_usd:p.price_usd,discount_pct:0,discount_usd:0,
       weekend_fee_usd:wk?199:0,split_boost_fee_usd:sb?149:0,
       express_payout_fee_usd:ex?49:0,credits_used:0,
       total_due_usd:Math.round((p.price_usd+(wk?199:0)+(sb?149:0)+(ex?49:0))*100)/100};
  }
  /* A coupon the server does not recognize changes nothing — say so instead
     of quietly showing the full price (promo codes are confirmed separately). */
  if(!previewFailed&&bc.coupon&&!(q.discount_usd>0)){buyErr('This code is not valid right now. You can still buy without it.')}
  else if(!previewFailed){const e=$('buy-err'); if(e)e.classList.add('hidden')}
  const row=(l,v,cls)=>`<div class="q-row${cls?' '+cls:''}"><span>${l}</span><b class="mono">${v}</b></div>`;
  let h=row('Plan fee','$'+fmt(q.plan_price_usd));
  if(q.discount_usd>0)h+=row(`Coupon (−${q.discount_pct}%)`,'−$'+fmt(q.discount_usd),'good');
  if(q.weekend_fee_usd>0)h+=row('Weekend Trading','+$'+fmt(q.weekend_fee_usd));
  if(q.split_boost_fee_usd>0)h+=row('Profit Split Boost','+$'+fmt(q.split_boost_fee_usd));
  if(q.express_payout_fee_usd>0)h+=row('Express Payout','+$'+fmt(q.express_payout_fee_usd));
  if(q.credits_used>0)h+=row('Store credit','−$'+fmt(q.credits_used),'good');
  h+=`<div class="q-row total"><span>Total due</span><b class="mono" id="buy-total">$${fmt(q.total_due_usd)}</b></div>`;
  box.innerHTML=h;
}
function closeBuy(){document.getElementById('buy-modal')?.remove()}

/* ONE field for every kind of code. A code recognized by /api/promo is the
   "Upgrade Your Size" promo (bigger account, same fee) — anything else goes
   to the server as a discount coupon. The server validates AGAIN at checkout,
   this is display only. Classification lands in window._buyCode. */
let _codeT=null;
function codeInput(){clearTimeout(_codeT);_codeT=setTimeout(codeCheck,400)}
async function codeCheck(){
  const p=PRODUCTS.find(x=>x.key===window._buyKey), inp=$('c-code');
  if(!p||!inp)return;
  const code=(inp.value||'').trim().toUpperCase();
  inp.value=code;
  let promo=false;
  if(code&&p.promo_upgrade_size){
    try{promo=(await api('/api/promo?code='+encodeURIComponent(code))).valid}catch(e){}
  }
  window._buyCode=promo?{coupon:null,promo:code}:{coupon:code||null,promo:null};
  try{
    if(promo)localStorage.setItem('pf_promo_code',code);
    else if(!code)localStorage.removeItem('pf_promo_code');
  }catch(e){}
  const cap=$('buy-cap'); if(cap)cap.textContent='$'+fmt0(promo?p.promo_upgrade_size:p.account_size);
  const line=$('buy-promo'); if(line)line.hidden=!promo;
  quoteRefresh(true);
}

function buyErr(msg){
  const el=$('buy-err');
  if(!el){toast(msg,'err');return}
  el.textContent=msg; el.classList.remove('hidden');
}

/* ---------- field validation ----------
   Deliberately a mirror of app/fields.py and app/countries.py, not a second
   opinion: the browser check exists so the customer sees the problem while
   typing, and the server check is the one that actually protects the data.
   If you change a rule here, change it there in the same commit. */
const NAME_OK_EXTRA=" -'’.";
function nameCheck(value,label){
  const t=String(value||'').trim().replace(/\s+/g,' ');
  if(!t)return{ok:false,msg:`${label} is required.`};
  if(t.length>60)return{ok:false,msg:`${label} is too long (max 60 characters).`};
  if(/\d/.test(t))return{ok:false,msg:`${label} cannot contain digits.`};
  for(const c of t){
    if(!(/\p{L}/u.test(c)||NAME_OK_EXTRA.includes(c)))
      return{ok:false,msg:`${label} contains an invalid character: “${c}”.`};
  }
  if([...t].filter(c=>/\p{L}/u.test(c)).length<2)return{ok:false,msg:`Enter your real ${label.toLowerCase()}.`};
  return{ok:true,value:t};
}
function emailCheck(value){
  const t=String(value||'').trim().toLowerCase();
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(t))
    return{ok:false,msg:'Enter a valid e-mail address, for example name@example.com.'};
  return{ok:true,value:t};
}
function phoneCheck(iso,value){
  const c=COUNTRY_BY_ISO[String(iso||'').toUpperCase()];
  if(!c)return{ok:false,msg:'Pick your country from the list.'};
  const text=String(value||'').trim();
  let d=text.replace(/\D/g,'');
  if(!d)return{ok:false,msg:'Enter your phone number.'};
  const intl=text.startsWith('+')||d.startsWith('00');
  if(d.startsWith('00'))d=d.slice(2);
  if(intl){
    if(!d.startsWith(c.d)){
      const other=COUNTRIES.slice().sort((a,b)=>b.d.length-a.d.length).find(x=>d.startsWith(x.d));
      return{ok:false,msg:other?`That number starts with +${other.d}, not +${c.d} (${c.n}). Pick the matching country.`
                             :`That does not look like a ${c.n} number (+${c.d}).`};
    }
    d=d.slice(c.d.length);
    if(!d)return{ok:false,msg:'Enter your phone number.'};
  }
  const fits=n=>n.length>=c.mn&&n.length<=c.mx;
  /* Leading zero is stripped ONLY as a repair. In Italy, for one, the zero is
     part of the national number and dropping it would break valid input. */
  if(!fits(d)&&d.startsWith('0')&&fits(d.slice(1)))d=d.slice(1);
  if(!fits(d)){
    const many=c.mn===c.mx?`${c.mn}`:`${c.mn}–${c.mx}`;
    return{ok:false,msg:`A phone number in ${c.n} has ${many} digits after +${c.d} — you entered ${d.length}.`};
  }
  if(c.d.length+d.length>15)return{ok:false,msg:'That phone number is too long.'};
  return{ok:true,value:'+'+c.d+d,national:d};
}
/* Splits a stored E.164 number back into the national part, so re-opening the
   modal shows "512345678" next to the +48 button instead of the whole string. */
function phoneNational(stored,iso){
  const c=COUNTRY_BY_ISO[String(iso||'').toUpperCase()];
  const d=String(stored||'').replace(/\D/g,'');
  if(!d)return'';
  if(c&&d.startsWith(c.d))return d.slice(c.d.length);
  return stored&&stored.startsWith('+')?d:String(stored||'');
}
function fieldErr(id,msg){
  const inp=$(id); if(inp)inp.classList.add('bad');
  const el=$(id+'-err'); if(el){el.textContent=msg;el.classList.remove('hidden')}
  if(inp&&inp.focus)inp.focus();
}
function clearFieldErr(id){
  const inp=$(id); if(inp)inp.classList.remove('bad');
  const el=$(id+'-err'); if(el)el.classList.add('hidden');
}

/* ---------- country calling code picker ----------
   Same shape as the date-of-birth calendar above: a button next to the input
   opens a panel, and a document-level click closes it. */
let CC='US';
function localeCountry(){
  const langs=navigator.languages&&navigator.languages.length?navigator.languages:[navigator.language||''];
  for(const l of langs){
    const m=/[-_]([A-Za-z]{2})$/.exec(l||'');
    if(m&&COUNTRY_BY_ISO[m[1].toUpperCase()])return m[1].toUpperCase();
  }
  return null;
}
/* Strefa czasowa urzadzenia -> kraj. Lepszy pierwszy strzal niz jezyk: ktos w
   Nowym Jorku z polskim interfejsem ma `pl-PL`, ale `America/New_York`. */
function tzCountry(){
  try{
    const z=Intl.DateTimeFormat().resolvedOptions().timeZone;
    const iso=z&&TZ_COUNTRY[z];
    return (iso&&COUNTRY_BY_ISO[iso])?iso:null;
  }catch(e){return null}
}
/* Kraj, gdy nie mamy o kliencie NICZEGO: strefa czasowa, potem jezyk, a na
   koncu Stany. Fallback jest twardo +1 i nie moze byc krajem, ktory akurat
   wypadl pierwszy w tablicy. */
function defaultCountry(){
  return tzCountry()||localeCountry()||(COUNTRY_BY_ISO['US']?'US':null)
    ||(COUNTRIES.find(c=>c.d==='1')||COUNTRIES[0]||{i:''}).i;
}
function guessCountry(){
  if(ME&&ME.phone_country&&COUNTRY_BY_ISO[ME.phone_country])return ME.phone_country;
  /* Customers who bought before the picker existed have a phone but no saved
     country. Reading the country back out of their own number beats guessing
     from the browser locale, which would show them a mismatched flag and a
     validation error on a number that was fine all along. */
  const zapisany=String((ME&&ME.phone)||'').trim();
  if(zapisany.startsWith('+')){
    const d=zapisany.replace(/\D/g,'');
    const pasuje=COUNTRIES.slice().sort((a,b)=>b.d.length-a.d.length)
      .filter(c=>d.startsWith(c.d)&&d.length-c.d.length>=c.mn&&d.length-c.d.length<=c.mx);
    if(pasuje.length){
      /* +44 is the UK, Guernsey, Jersey and the Isle of Man; +1 is the US,
         Canada and a dozen more. Alphabetical order would answer "Guernsey",
         whose allowed length differs from the UK's — so the customer's own,
         perfectly valid number would stop validating. Order of preference:
         the browser's region, then the country libphonenumber marks as the
         main one for that code. */
      for(const skad of [tzCountry(),localeCountry()]){
        if(skad&&pasuje.some(c=>c.i===skad))return skad;
      }
      return (pasuje.find(c=>c.m)||pasuje[0]).i;
    }
  }
  return defaultCountry();
}
/* ~110 KB obrazkow flag w jednym pliku: nie wchodzi w krytyczna sciezke strony,
   tylko dogrywa sie osobno. Wolane z KAZDEGO miejsca, ktore nadaje klase flagi —
   wczesniej tylko z otwarcia listy krajow, wiec flaga przy numerze kierunkowym
   w kasie zostawala szarym prostokatem az do klikniecia w liste. */
function flagsCss(){
  if(document.getElementById('pf-flags'))return;
  const l=document.createElement('link');
  l.id='pf-flags';l.rel='stylesheet';l.href='/static/css/flags.css?v='+ASSET_V;
  document.head.appendChild(l);
}
/* Rozgrzewka w bezczynnosci po zalogowaniu: plik i tak bedzie potrzebny przy
   pierwszym otwarciu kasy, a pobrany zawczasu jest juz w cache, wiec flaga
   pojawia sie razem z okienkiem, a nie dogania je z opoznieniem. */
function flagsWarm(){
  (window.requestIdleCallback||(f=>setTimeout(f,1500)))(()=>flagsCss(),{timeout:5000});
}
function ccSet(iso,revalidate){
  const c=COUNTRY_BY_ISO[iso]; if(!c)return;
  flagsCss();
  CC=c.i;
  const f=$('c-cc-flag'),d=$('c-cc-dial');
  if(f)f.className="flag flag-"+c.i.toLowerCase();
  if(d)d.textContent='+'+c.d;
  const btn=$('c-cc'); if(btn)btn.title=c.n;
  if(revalidate!==false)clearFieldErr('c-phone');
}
function ccToggle(e){
  e.stopPropagation();
  const pop=$('ccpop'); if(!pop)return;
  const open=pop.classList.contains('hidden');
  if(open){flagsCss();ccRender();$('cc-search').value='';ccRender();}
  pop.classList.toggle('hidden',!open);
  const btn=$('c-cc'); if(btn)btn.setAttribute('aria-expanded',String(open));
  if(open)setTimeout(()=>{const s=$('cc-search');if(s)s.focus()},30);
}
function ccRender(){
  const box=$('cc-list'); if(!box)return;
  const q=(($('cc-search')||{}).value||'').trim().toLowerCase().replace(/^\+/,'');
  /* Ranked, not just filtered: plain alphabetical order answers "pol" with
     French Polynesia before Poland, and the first row is the one people hit. */
  const rank=c=>{
    const n=c.n.toLowerCase();
    if(c.i.toLowerCase()===q)return 0;
    if(n===q)return 1;
    if(n.startsWith(q))return 2;
    if(c.d===q)return 3;
    if(n.split(/[\s-]+/).some(w=>w.startsWith(q)))return 4;
    if(c.d.startsWith(q))return 5;
    if(n.includes(q))return 6;
    return 99;
  };
  const hit=q?COUNTRIES.map(c=>[rank(c),c]).filter(([r])=>r<99)
               .sort((a,b)=>a[0]-b[0]||a[1].n.localeCompare(b[1].n)).map(([,c])=>c)
             :COUNTRIES;
  box.innerHTML=hit.length?hit.map(c=>
    `<button type="button" class="cc-item${c.i===CC?' on':''}" role="option" aria-selected="${c.i===CC}"
       onclick="ccPick('${c.i}')"><span class="flag flag-${c.i.toLowerCase()}"></span>
       <span class="cc-n">${esc(c.n)}</span><span class="cc-d">+${c.d}</span></button>`).join('')
    :'<p class="cc-none">No country matches that.</p>';
}
function ccPick(iso){
  ccSet(iso);
  const pop=$('ccpop'); if(pop)pop.classList.add('hidden');
  const btn=$('c-cc'); if(btn)btn.setAttribute('aria-expanded','false');
  const inp=$('c-phone'); if(inp)inp.focus();
}
document.addEventListener('click',e=>{
  const pop=document.getElementById('ccpop');
  if(pop&&!pop.classList.contains('hidden')&&!e.target.closest('.tel-wrap'))pop.classList.add('hidden');
});
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  const pop=document.getElementById('ccpop');
  if(pop&&!pop.classList.contains('hidden')){pop.classList.add('hidden');e.stopPropagation()}
});

async function buy(key){
  await codeCheck();   /* fresh code classification, even if the debounce is still pending */
  const bc=window._buyCode||{};
  const coupon=bc.coupon||null, promo=bc.promo||null;
  /* The MT5 demo account is registered with the broker under exactly these
     details, so a typo here means a paid order that cannot be provisioned. */
  ['c-first','c-last','c-phone'].forEach(clearFieldErr);
  const imie=nameCheck($('c-first').value,'First name');
  if(!imie.ok){fieldErr('c-first',imie.msg);return}
  const nazwisko=nameCheck($('c-last').value,'Last name');
  if(!nazwisko.ok){fieldErr('c-last',nazwisko.msg);return}
  const tel=phoneCheck(CC,$('c-phone').value);
  if(!tel.ok){fieldErr('c-phone',tel.msg);return}
  const first=imie.value, last=nazwisko.value, phone=tel.value;
  await busy($('buy-go'),'Processing…',async()=>{
    try{
      const res=await api('/api/checkout',{method:'POST',
        body:JSON.stringify({product_key:key,coupon,promo_code:promo,first_name:first,last_name:last,phone,phone_country:CC,
          weekend_trading:!!($('c-weekend')&&$('c-weekend').checked),
          split_boost:!!($('c-boost')&&$('c-boost').checked),
          express_payout:!!($('c-express')&&$('c-express').checked),
          use_credits:!$('c-usecr')||$('c-usecr').checked})});
      /* real Stripe: strona zaraz znika — przycisk ma zostać wyłączony */
      if(res.checkout_url && !res.mock){window.location=res.checkout_url;return 'keep'}
      let prov=res;
      if(res.mock){prov=await api(`/api/checkout/${res.order_id}/mock-complete`,{method:'POST'});}
      ME=await api('/api/auth/me');
      closeBuy();
      if(prov.provisioning){
        toast('✅ Payment received.\nYour MT5 demo account is being created, up to a minute.\nCredentials arrive by e-mail and under Challenges.','ok',9000);
      }else{
        toast(`✅ Account created!\nServer: ${prov.platform_server||'—'}\nLogin: ${prov.platform_login}\nPassword: ${prov.platform_password}\n(also sent by e-mail)`,'ok',12000);
      }
      go('accounts');
    }catch(e){buyErr('Error: '+e.message)}
  });
}

/* ---------- ACCOUNT DETAIL ---------- */
/* Countries come from the server (app/countries.py) so the dial codes, the
   allowed phone lengths and the KYC list all read from ONE table. */
const PF_GEO=window.PF_GEO||{};
const COUNTRIES=PF_GEO.c||[];
/* Strefa czasowa -> kraj (IANA). Mowi, GDZIE ktos jest; jezyk przegladarki
   tylko, jak woli czytac — dlatego przy numerze kierunkowym strefa idzie
   pierwsza. */
const TZ_COUNTRY=PF_GEO.tz||{};
const COUNTRY_BY_ISO=Object.fromEntries(COUNTRIES.map(c=>[c.i,c]));
const COUNTRY_NAMES=COUNTRIES.map(c=>c.n);
const PHASE_LABEL={eval_1:'Phase 1',eval_2:'Phase 2',funded:'Funded'};

async function openAcc(id){
  $('pg-title').textContent='Account Dashboard'; $('pg-crumb').textContent='Trader / Client Area / '+id;
  $('view').innerHTML='<div class="skel" style="height:110px;margin-bottom:16px"></div><div class="skel" style="height:300px"></div>';
  const [a,act,pos]=await Promise.all([
    api('/api/me/accounts/'+id),
    api(`/api/me/accounts/${id}/activity`),
    api(`/api/me/accounts/${id}/positions`).catch(()=>[]),
  ]);
  const m=a.metrics||{};
  window._act=act; window._accId=id; window._acc=a; window._onDetail=true;
  $('pg-crumb').textContent='Trader / Client Area / '+(a.login||id);
  const latest=act.days.length?act.days[act.days.length-1].day:null;
  const base=latest?new Date(latest+'T00:00:00Z'):new Date();
  window._calM=[base.getUTCFullYear(),base.getUTCMonth()];
  /* Wybrany dzien nie moze przejsc na inne konto — historia jest per konto. */
  window._calSel=null;
  const profitPct=m.profit_pct||0;
  const targetPct=m.profit_target_pct||0;
  const targetUsd=a.initial_balance*targetPct/100;
  const reach=targetPct?Math.min(100,Math.max(0,profitPct/targetPct*100)):100;
  const ddPct=(m.overall_dd_used_pct||0)/100*(m.max_overall_loss_pct||0);
  const ddUsd=a.initial_balance*ddPct/100, ddLimUsd=a.initial_balance*(m.max_overall_loss_pct||0)/100;
  const dlPct=(m.daily_loss_used_pct||0)/100*(m.max_daily_loss_pct||0);
  const dlUsd=a.initial_balance*dlPct/100, dlLimUsd=a.initial_balance*(m.max_daily_loss_pct||0)/100;
  const curve=a.equity_curve||[];
  const openPnl=a.open_pnl||0;
  const started=a.created_at?dutc(a.created_at).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'—';
  const split=a.profit_split_pct??90;
  const objOn=objLinesOn();
  $('view').innerHTML=`
    <button class="backlink" onclick="go('accounts')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>
      Back to Challenges</button>
    ${a.source==='grant'?`<div class="gradient-banner" style="margin-bottom:18px">
      <span class="gb-tag">${ICO.spark} ${esc(a.grant_note||'BOGO promotion')}</span><span class="gb-sep"></span>
      <span class="gb-txt">${bogoText(a)}</span>
    </div>`:''}
    <div class="detail-head">
      <h2>${a.status==='provisioning'?'MT5 account pending…':esc(a.login)}</h2>
      <span class="status passed" style="text-transform:none">${PHASE_LABEL[a.phase]||esc(a.phase)}</span>
      <span class="status ${esc(a.status)}"><span class="dot"></span>${a.status==='active'?'evaluation':esc(a.status)}</span>
    </div>
    <div class="acts-row">
      <button class="act-chip" id="creds-btn" onclick="toggleCreds()">${ICO.key} Credentials</button>
      <button class="act-chip" onclick="openAcc(${id})">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.6-6.3M21 3v6h-6"/></svg>
        Refresh</button>
      ${a.cert_token?`<a class="act-chip" href="/certificate/${esc(a.cert_token)}" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="9" r="5.5"/><path d="m8.6 13.5-1.8 7 5.2-2.6 5.2 2.6-1.8-7"/></svg>
        Share certificate</a>`:''}
      ${openPnl!==0?`<span class="act-chip live"><span class="live-dot"></span> Live position</span>`:''}
      <span class="act-chip" style="cursor:default">${planKind(a.steps)} · $${fmt0(a.initial_balance)}</span>
    </div>

    <div class="detail-grid">
      <div class="sec-card" style="margin-bottom:0">
        <div class="res-head"><h3>Current Results</h3>
          <div class="seg-mini"><span>Objective lines</span>
            <span class="sw">
              <button data-on="1" class="${objOn?'on':''}" onclick="setObjLines(true)">On</button>
              <button data-on="0" class="${objOn?'':'on'}" onclick="setObjLines(false)">Off</button>
            </span></div>
        </div>
        <div class="res-stats">
          <div class="res-stat"><div class="l">Balance</div><div class="v">$${fmt(a.balance)}</div></div>
          <div class="res-stat"><div class="l">Equity</div>
            <div class="v ${a.equity>=a.initial_balance?'up':'down'}">$${fmt(a.equity)}</div></div>
          <div class="res-stat"><div class="l">Open P&amp;L</div>
            <div class="v ${openPnl>0?'up':openPnl<0?'down':''}">${openPnl>=0?'+':'-'}$${fmt(Math.abs(openPnl))}</div></div>
          ${targetPct?`<div class="res-stat"><div class="l">Progress to target</div>
            <div class="v ${profitPct>=0?'up':'down'}">${reach.toFixed(1)}%</div></div>`:''}
        </div>
        ${curve.length>1
          ?'<div class="chart-box tall"><canvas id="d-chart"></canvas></div>'
          :'<p class="muted" style="font-size:13px">The equity chart appears after the first risk-engine readings of this account.</p>'}
      </div>

      <div class="sec-card info-card" style="margin-bottom:0">
        <h3 style="margin-bottom:6px">${planKind(a.steps)} Account</h3>
        <div class="kv2"><span class="k">Status</span>
          <span class="status ${esc(a.status)}"><span class="dot"></span>${a.status==='active'?'evaluation':esc(a.status)}</span></div>
        <div class="kv2"><span class="k">Account Number</span><span class="v">${esc(a.login)}</span></div>
        <div class="kv2"><span class="k">Start</span><span class="v">${started}</span></div>
        <div class="kv2"><span class="k">Account Size</span><span class="v">$${fmt0(a.initial_balance)}</span></div>
        <div class="kv2"><span class="k">Profit Split</span>
          <span class="v">${split}:${100-split}<span class="split-under"><i style="width:${split}%"></i></span></span></div>
        <div class="kv2"><span class="k">Account Type</span><span class="v">${planKind(a.steps)} · ${PHASE_LABEL[a.phase]||esc(a.phase)}</span></div>
        <div class="kv2"><span class="k">Trading Days</span><span class="v">${m.trading_days??0} / ${m.min_trading_days??0} min</span></div>
        <div class="kv2"><span class="k">Platform (MT5)</span>
          <button class="linklike" onclick="toggleCreds()">Credentials</button></div>
      </div>
    </div>

    ${pos.length?`<div class="sec-card">
      <h3 class="ot-title"><span class="live-dot"></span> Open trades</h3>
      <div class="tbl-wrap" style="box-shadow:none;border:0;border-radius:0"><table class="tbl sortable" data-tkey="portal.positions" style="min-width:520px">
        <thead><tr><th>Ticket</th><th>Open</th><th>Side</th><th>Volume</th><th>Symbol</th><th>P&amp;L</th></tr></thead>
        <tbody>${pos.map(t=>`<tr>
          <td class="num">${t.ticket}</td>
          <td class="num" data-sort="${esc(t.opened_at||'')}">${t.opened_at?dstr(t.opened_at):'—'}</td>
          <td><span class="tside ${t.side==='buy'?'buy':'sell'}">${esc(t.side)}</span></td>
          <td class="num">${(t.lots??0).toFixed(2)}</td>
          <td class="num">${esc(t.symbol)}</td>
          <td><span class="pnl-pill ${t.pnl>=0?'up':'down'}">${t.pnl>=0?'+':'-'}$${fmt(Math.abs(t.pnl))}</span></td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>`
    :openPnl!==0?`<div class="sec-card">
      <h3 class="ot-title"><span class="live-dot"></span> Open position</h3>
      <p class="muted" style="font-size:13px;margin-top:8px">Floating P&amp;L right now:
        <span class="pnl-pill ${openPnl>=0?'up':'down'}" style="margin-left:6px">${openPnl>=0?'+':'-'}$${fmt(Math.abs(openPnl))}</span></p>
    </div>`:''}

    <div class="sec-card hidden" id="creds-card">
      <h3>Trading Credentials</h3>
      <div class="warn-box">${ICO.alert}
        <div><b>MT5 access is limited to one device per account</b>
        Do not log in from multiple devices at the same time. Passing these credentials to someone
        else requires an approved management arrangement. See Section 7 of the
        <a href="/terms">Terms</a>; undisclosed sharing can trigger an account review.</div>
      </div>
      <div class="creds-grid"${a.platform_password?'':' style="display:none"'}>
        <div class="cred-cell"><div class="l">Platform</div><div class="v"><img src="/static/img/mt5.png" alt="MetaTrader 5"></div></div>
        ${credCell('Server',a.platform_server)}
        ${credCell('Login',a.platform_login)}
        ${a.platform_password?credCell('Password',a.platform_password):''}
        <div class="cred-cell"><div class="l">Leverage</div><div class="v">1:100</div></div>
      </div>
      ${(!a.platform_password)?'<div class="creds-wait"><span class="spin"></span> Setting up your trading account: server, login and password appear here in a moment.</div>':''}
    </div>

    <div class="sec-card">
      <h3>Objectives</h3>
      <div class="progress-list">
        ${prog('Profit target',targetPct?(profitPct/targetPct*100):0,false,
          targetPct?`${profitPct.toFixed(1)}% / ${targetPct}% ($${fmt0(targetUsd)})`:'funded, no target')}
        ${prog('Daily loss used',m.daily_loss_used_pct,true,`${dlPct.toFixed(2)}% / ${m.max_daily_loss_pct}% ($${fmt0(dlUsd)} / $${fmt0(dlLimUsd)})`)}
        ${prog('Max drawdown used',m.overall_dd_used_pct,true,`${ddPct.toFixed(2)}% / ${m.max_overall_loss_pct}% ($${fmt0(ddUsd)} / $${fmt0(ddLimUsd)})`)}
        ${a.max_lots?`<div><div class="prog-top"><span>Open volume limit (all positions combined)</span><b>max ${a.max_lots} lots</b></div></div>`:''}
      </div>
      ${a.breach_reason?`<div class="warn-box" style="margin:16px 0 0;background:var(--red-bg);border-color:var(--red-line);color:var(--red)">${ICO.alert}<div><b style="color:var(--red)">Rule breached</b>${esc(a.breach_reason)}</div></div>`:''}
    </div>

    <div class="sec-card" id="cal-card"></div>

    <div class="sec-card" id="tx-card">
      <h3>Transaction History</h3>
      <div id="tx-filter" class="tx-filter"></div>
      ${txTable(a,act)}
    </div>

    ${(a.payout_requests||[]).length?`<p class="muted" style="font-size:12.5px;margin-top:12px">Payout requests: ${a.payout_requests.map(r=>`${r.status} ($${fmt(r.trader_share)})`).join(', ')}</p>`:''}`;
  calRender();
  drawDetailChart();
  rollStats();
  txInit();
  checkMilestone(id,a,m);
}

/* ---------- objective lines on the detail chart (FTMO-style) ---------- */
function objLinesOn(){try{return localStorage.getItem('pf_obj_lines')!=='off'}catch(e){return true}}
function setObjLines(on){
  try{localStorage.setItem('pf_obj_lines',on?'on':'off')}catch(e){}
  document.querySelectorAll('.seg-mini button').forEach(b=>b.classList.toggle('on',(b.dataset.on==='1')===on));
  drawDetailChart();
}
function detailLines(){
  const a=window._acc; if(!a)return [];
  const m=a.metrics||{},th=chartTheme(),L=[];
  if(m.target_equity)L.push({y:m.target_equity,label:'Target',color:th.green});
  if(m.daily_floor)L.push({y:m.daily_floor,label:'Daily loss limit',color:th.gold});
  if(m.overall_floor)L.push({y:m.overall_floor,label:'Max loss',color:th.red});
  L.push({y:a.initial_balance,label:'Account size',color:th.dim});
  return L;
}
function drawDetailChart(){
  const a=window._acc,curve=(a&&a.equity_curve)||[];
  if(chart){chart.destroy();chart=null}
  if(!$('d-chart')||curve.length<2)return;
  chart=new Chart($('d-chart'),equityChartConfig(curve,{lines:objLinesOn()?detailLines():[]}));
}

function toggleCreds(){
  const card=$('creds-card'),btn=$('creds-btn');
  const show=card.classList.toggle('hidden');
  btn.innerHTML=(show?ICO.key+' Show Credentials':ICO.key+' Hide Credentials');
  if(!show)card.scrollIntoView({behavior:'smooth',block:'center'});
}
function calShift(d){const [y,m]=window._calM;const nd=new Date(Date.UTC(y,m+d,1));
  window._calM=[nd.getUTCFullYear(),nd.getUTCMonth()];calRender()}
/* Klikniecie w dzien z obrotem zawęża Transaction History do tego dnia.
   Drugie klikniecie w ten sam dzien zdejmuje filtr — komorka jest przelacznikiem,
   nie jednokierunkowym przejsciem. */
function calPick(day){
  window._calSel=(window._calSel===day)?null:day;
  calRender();
  txPage(1);
  const tbl=$('tx-tbl');
  if(tbl&&window._calSel)tbl.closest('.sec-card').scrollIntoView({behavior:'smooth',block:'start'});
}
function calClear(){window._calSel=null;calRender();txPage(1)}
function calRender(){
  const el=$('cal-card'); if(!el||!window._act)return;
  const [y,m]=window._calM;
  const map={}; window._act.days.forEach(d=>map[d.day]=d.pnl);
  const first=new Date(Date.UTC(y,m,1));
  const dow=first.getUTCDay();
  const dim=new Date(Date.UTC(y,m+1,0)).getUTCDate();
  const title=first.toLocaleString('en-US',{month:'long',year:'numeric',timeZone:'UTC'});
  const pad=n=>String(n).padStart(2,'0');
  let cells='';
  for(let i=0;i<dow;i++)cells+='<div class="cal-day blank"></div>';
  for(let d=1;d<=dim;d++){
    const key=`${y}-${pad(m+1)}-${pad(d)}`;
    const pnl=map[key];
    let cls='',txt='',atryb='';
    if(pnl!==undefined){
      cls=pnl>0?'profit':pnl<0?'loss':'flat';
      /* Klikalne sa WYLACZNIE dni z obrotem — pusta komorka nie ma czego
         pokazac na liscie ponizej. */
      cls+=' has'+(key===window._calSel?' sel':'');
      atryb=` role="button" tabindex="0" aria-pressed="${key===window._calSel}"`
        +` onclick="calPick('${key}')"`
        +` onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();calPick('${key}')}"`;
      if(matchMedia('(max-width:640px)').matches){
        // waskie komorki: sama wielkosc (12345 -> 12.3k) — znak niesie kolor komorki
        const a=Math.abs(pnl);
        txt=`<div class="pnl">${a>=1000?(a/1000).toFixed(1).replace(/\.0$/,'')+'k':fmt0(a)}</div>`;
      }else{
        txt=`<div class="pnl">${pnl>0?'+':''}$${fmt0(pnl)}</div>`;
      }
    }
    cells+=`<div class="cal-day ${cls}"${atryb}><div class="d">${d}</div>${txt}</div>`;
  }
  el.innerHTML=`
    <div class="cal-head"><h3 style="margin:0">Trading Calendar</h3>
      <div style="display:flex;align-items:center;gap:10px"><b>${title}</b>
        <div class="cal-nav">
          <button class="icon-btn" onclick="calShift(-1)" aria-label="Previous month"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg></button>
          <button class="icon-btn" onclick="calShift(1)" aria-label="Next month"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg></button>
        </div></div></div>
    <div class="cal-grid">
      ${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(d=>`<div class="cal-dow">${d}</div>`).join('')}
      ${cells}
    </div>
    <div class="cal-legend">
      <span><i style="background:var(--green2)"></i> Profit Day</span>
      <span><i style="background:var(--red)"></i> Loss Day</span>
      <span><i style="background:var(--line2)"></i> No Trading</span>
    </div>`;
}
/* A granted account is described as the BOGO promotion. The sentence about
   the paid tier appears ONLY when we truly know it — anything else is made up. */
function tier(v){return v>=1000?'$'+Math.round(v/1000)+'K':'$'+fmt0(v)}
/* Sign BEFORE the dollar: "-$8.44", not "$-8.44". */
function money(v){return (v<0?'-$':'+$')+fmt(Math.abs(v))}
function bogoText(a){
  if(a.bogo_paid_size&&a.bogo_paid_size<a.initial_balance)
    return `Your promotion has been applied: you paid for the <b>${tier(a.bogo_paid_size)} tier</b>
      and we upgraded your allocation to <b>$${fmt0(a.initial_balance)}</b>.
      Same rules and the same profit split as a standard account.`;
  return `<b>Buy one, get one free.</b> This bonus challenge is live on your account.
    Same rules and the same profit split as a purchased account.`;
}

/* Account ledger: trades AND payouts, newest first.
   The balance comes from the server (a snapshot taken at the event). Do NOT
   recompute it backwards from the current balance: a payout or a stage
   promotion resets the balance, shifting the whole history by that amount. */
const TX_PER_PAGE=15;
/* Historia rosnie z kazdym zamknietym trejdem, wiec idzie stronami.
   Wiersze zostaja W CALOSCI w DOM i sa tylko ukrywane, bo sortowanie kolumn
   (sortable.js) przestawia wiersze TABELI — gdyby renderowana byla jedna
   strona, klikniecie naglowka posortowaloby wylacznie te pietnascie
   widocznych wierszy zamiast calej historii. */
function txPager(ile,strona){
  const stron=Math.ceil(ile/TX_PER_PAGE);
  if(stron<2)return '';
  /* Zawsze pierwsza, ostatnia i sasiedztwo biezacej; reszta pod wielokropkiem,
     zeby przy kilkudziesieciu stronach pasek nie zajal calej szerokosci. */
  const okno=new Set([1,stron,strona,strona-1,strona+1]);
  if(strona<=3)[2,3,4].forEach(n=>okno.add(n));
  if(strona>=stron-2)[stron-1,stron-2,stron-3].forEach(n=>okno.add(n));
  const lista=[...okno].filter(n=>n>=1&&n<=stron).sort((a,b)=>a-b);
  let srodek='',poprzednia=0;
  for(const n of lista){
    if(n-poprzednia>1)srodek+='<span class="pg-dots">…</span>';
    srodek+=`<button class="pg-btn${n===strona?' on':''}" onclick="txPage(${n})"
      ${n===strona?'aria-current="page"':''}>${n}</button>`;
    poprzednia=n;
  }
  return `<button class="pg-btn pg-nav" onclick="txPage(${strona-1})" ${strona===1?'disabled':''}
      aria-label="Previous page">‹</button>${srodek}<button class="pg-btn pg-nav"
      onclick="txPage(${strona+1})" ${strona===stron?'disabled':''} aria-label="Next page">›</button>`;
}
function txPage(n){
  const tbl=$('tx-tbl'); if(!tbl||!tbl.tBodies[0])return;
  const wszystkie=[...tbl.tBodies[0].rows];
  const dzien=window._calSel;
  /* Data siedzi w `data-sort` pierwszej komorki (to samo pole, po ktorym
     sortuje sortable.js), wiec filtr nie zalezy od formatu wyswietlania. */
  const rows=dzien?wszystkie.filter(tr=>tr.cells[0]&&tr.cells[0].dataset.sort===dzien):wszystkie;
  const stron=Math.max(1,Math.ceil(rows.length/TX_PER_PAGE));
  n=Math.min(Math.max(1,n),stron);
  const od=(n-1)*TX_PER_PAGE;
  /* Najpierw chowamy wszystko, potem odslaniamy strone z przefiltrowanych —
     inaczej wiersze spoza dnia zostalyby widoczne z poprzedniego przebiegu. */
  wszystkie.forEach(tr=>{tr.style.display='none'});
  rows.forEach((tr,i)=>{if(i>=od&&i<od+TX_PER_PAGE)tr.style.display=''});
  const pager=$('tx-pager'); if(pager)pager.innerHTML=txPager(rows.length,n);
  const chip=$('tx-filter');
  if(chip)chip.innerHTML=dzien
    ? `<span class="tx-chip">${dday(dzien)}<button type="button" class="tx-chip-x"
        onclick="calClear()" aria-label="Show all days">×</button></span>
       <span class="tx-chip-hint">Showing this day only — click the day again, or ×, for the full history.</span>`
    : '';
  const info=$('tx-info');
  if(!info)return;
  if(dzien&&!rows.length)info.textContent=`No entries for ${dday(dzien)} in the loaded history.`;
  else info.textContent=rows.length<=TX_PER_PAGE
    ? `${rows.length} entr${rows.length===1?'y':'ies'}${dzien?' on '+dday(dzien):''}.`
    : `Showing ${od+1}–${Math.min(od+TX_PER_PAGE,rows.length)} of ${rows.length} entries${dzien?' on '+dday(dzien):''}.`;
}
function txInit(){
  const tbl=$('tx-tbl'); if(!tbl||!tbl.tBodies[0])return;
  txPage(1);
  /* Sortowanie kolumny przestawia wiersze, wiec po nim „pierwsza strona"
     znaczy co innego — wracamy na nia i przeliczamy widocznosc. */
  if(tbl._txObs)tbl._txObs.disconnect();
  tbl._txObs=new MutationObserver(()=>txPage(1));
  tbl._txObs.observe(tbl.tBodies[0],{childList:true});
}
function txTable(a,act){
  const rows=act.ledger||[];
  if(!rows.length)
    return '<p class="muted" style="font-size:13px">Nothing here yet. Close a trade and it will show up.</p>';
  const bal=v=>v==null?'—':'$'+fmt(v);
  return `<div class="scroll-x"><table id="tx-tbl" class="tbl sortable" data-tkey="portal.history" style="min-width:560px">
    <thead><tr><th>Date</th><th>Instrument</th><th>Side</th><th style="text-align:right">Lots</th>
      <th style="text-align:right">P&amp;L</th><th style="text-align:right">Balance</th></tr></thead>
    <tbody>${rows.map(r=>r.kind==='payout'?`<tr>
      <td class="muted" data-sort="${esc(r.day||'')}">${dday(r.day)}</td>
      <td style="font-weight:600">Payout<span class="muted" style="font-weight:400"> · $${fmt(r.trader_share)} to you</span></td>
      <td><span class="tside payout">out</span></td>
      <td class="num muted" style="text-align:right">—</td>
      <td class="num down" style="text-align:right">${money(r.pnl)}</td>
      <td class="num" style="text-align:right">${bal(r.balance)}</td></tr>`:`<tr>
      <td class="muted" data-sort="${esc(r.day||'')}">${dday(r.day)}</td>
      <td class="num" style="font-weight:600">${esc(r.symbol)}</td>
      <td><span class="tside ${esc(r.side)}">${esc(r.side)}</span></td>
      <td class="num" style="text-align:right">${(r.lots||0).toFixed(2)}</td>
      <td class="num ${r.pnl>=0?'up':'down'}" style="text-align:right">${money(r.pnl)}</td>
      <td class="num" style="text-align:right">${bal(r.balance)}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="pager" id="tx-pager"></div>
    <p class="muted" style="font-size:11px;margin-top:10px"><span id="tx-info"></span>
      A payout removes the earned profit from the account, and your share is paid out to you.</p>`;
}
async function issueCert(accId,kind){
  try{const r=await api('/api/me/certificates',{method:'POST',
      body:JSON.stringify({account_id:accId,kind})});
    toast('Certificate ready.','ok'); window.open(r.url,'_blank','noopener'); go('certificates');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function issuePayoutCert(id){
  try{const r=await api(`/api/me/payouts/${id}/certificate`,{method:'POST'});
    toast('Certificate ready.','ok'); window.open(r.url,'_blank','noopener'); go('certificates');
  }catch(e){toast('Error: '+e.message,'err')}
}
/* MT5 credentials start HIDDEN and are revealed per account.
   The MT5 password gives full control over the account — no reason for it to
   sit on screen the moment the orders list opens. */
function toggleOrderCreds(id){
  const row=$('crow-'+id), btn=$('cbtn-'+id);
  if(!row||!btn)return;
  const ukryte=row.classList.toggle('hidden');
  btn.innerHTML=ICO.key+(ukryte?' Show credentials':' Hide credentials');
}
function copyLink(url){navigator.clipboard.writeText(url)
  .then(()=>toast('Link copied.','ok'),()=>toast('Could not copy.','err'))}

function credCell(label,val){
  if(!val)return '';
  return `<div class="cred-cell"><div class="l">${label}</div>
    <div class="v"><span class="mono">${esc(val)}</span>
      <button class="copy" onclick="copyVal(this,'${esc(String(val)).replace(/'/g,"\\'")}')" title="Copy">${ICO.copy}</button>
    </div></div>`;
}
function prog(label,pct,danger,suffix){const p=Math.min(100,Math.max(0,pct||0));
  const cls=danger?(p>=100?'bad':p>=70?'warn':'mid'):'ok';
  return `<div><div class="prog-top"><span>${label}</span><b>${suffix}</b></div>
   <div class="prog-bar"><i class="${cls}" style="width:${p}%"></i></div></div>`;}
async function makePayoutCert(id){
  // Older payouts predate certificates — the token is minted on demand.
  try{const r=await api(`/api/me/payouts/${id}/certificate`,{method:'POST'});
    window.open(r.url,'_blank','noopener'); go('payouts');
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Payout request: amount + method + the details that method requires.
   The server validates the same rules again — the modal only saves an error round-trip. */
function payoutMethodLabel(m){return m==='usdt'?'USDT (crypto)':m==='wise'?'Wise':'Bank transfer'}
function openPayoutModal(id,avail){
  const w=document.createElement('div'); w.id='po-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Request a payout</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 12px">Available on this account: <b>$${fmt(avail)}</b>, your split of current profit. You can request part of it.</p>
    <label class="muted" style="font-size:12px">Amount (USD)</label>
    <input id="po-amount" class="inp" type="number" min="1" max="${avail}" step="0.01" value="${avail}" style="margin-bottom:10px">
    <label class="muted" style="font-size:12px">Payout method</label>
    <select id="po-method" class="inp" onchange="poFields()" style="margin-bottom:10px">
      <option value="usdt">USDT — crypto</option>
      <option value="bank">Bank transfer</option>
      <option value="wise">Wise</option>
    </select>
    <div id="po-fields"></div>
    <div style="display:flex;gap:10px;margin-top:14px">
      <button class="btn-p" onclick="submitPayout(${id})" id="po-send">Submit request</button>
      <button class="btn-o" onclick="$('po-modal').remove()">Cancel</button>
    </div></div>`;
  document.body.appendChild(w); poFields();
}
function poFields(){
  const m=$('po-method').value, F=$('po-fields'), L=t=>`<label class="muted" style="font-size:12px">${t}</label>`;
  if(m==='usdt')F.innerHTML=L('Network')+`<select id="po-network" class="inp" style="margin-bottom:10px">
      <option value="TRC20">TRC-20 (Tron) — lowest fees</option>
      <option value="BEP20">BEP-20 (BNB Smart Chain)</option>
      <option value="POLYGON">Polygon</option></select>`
    +L('USDT wallet address')+`<input id="po-address" class="inp" placeholder="T… / 0x…" autocomplete="off">`;
  else if(m==='bank')F.innerHTML=L('Account holder')+`<input id="po-holder" class="inp" placeholder="Full name" style="margin-bottom:10px">`
    +L('IBAN / account number')+`<input id="po-iban" class="inp" style="margin-bottom:10px">`
    +L('SWIFT / BIC')+`<input id="po-swift" class="inp" style="margin-bottom:10px">`
    +L('Bank name (optional)')+`<input id="po-bank" class="inp">`;
  else F.innerHTML=L('Wise account email')+`<input id="po-email" class="inp" type="email" placeholder="you@example.com">`;
}
/* Skalowanie to decyzja ZAMIAST wypłaty, więc modal mówi wprost, co trader
   oddaje — i że dostaje NOWY rachunek, bo starego nie da się powiększyć: saldo
   siedzi u brokera, a nie w naszej bazie. */
function openScaleModal(id,from,to){
  const w=document.createElement('div'); w.id='sc-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Move up to the $${fmt0(to)} plan</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 14px">
      You leave the <b>$${fmt0(from)}</b> account behind and we set up a fresh
      <b>$${fmt0(to)}</b> account, funded from day one, with that plan's limits. The profit you
      made pays for the upgrade, so there is no payout this time. Your new login and password
      arrive by email, usually within minutes.</p>
    <div style="display:flex;gap:10px">
      <button class="btn-p" onclick="scaleUp(${id})" id="sc-go">Move up to $${fmt0(to)}</button>
      <button class="btn-o" onclick="$('sc-modal').remove()">Cancel</button>
    </div></div>`;
  document.body.appendChild(w);
}
async function scaleUp(id){
  await busy($('sc-go'),null,async()=>{
    try{const r=await api(`/api/accounts/${id}/scale-up`,{method:'POST'});
      $('sc-modal').remove();
      toast(`📈 You are moving up to a $${fmt0(r.new_size)} account. We are setting it up now — `
        +`your credentials arrive by email.`,'ok',9000);
      go('accounts');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function submitPayout(id){
  const m=$('po-method').value, amount=parseFloat($('po-amount').value||'0');
  const details=m==='usdt'?{network:$('po-network').value,address:$('po-address').value.trim()}
    :m==='bank'?{holder:$('po-holder').value.trim(),iban:$('po-iban').value.trim(),
                 swift:$('po-swift').value.trim(),bank_name:$('po-bank').value.trim()}
    :{email:$('po-email').value.trim()};
  await busy($('po-send'),null,async()=>{
    try{const r=await api(`/api/accounts/${id}/payout-request`,{method:'POST',
        body:JSON.stringify({method:m,amount,details})});
      $('po-modal').remove();
      toast(`✅ Request submitted: $${fmt(r.trader_share)} via ${payoutMethodLabel(m)}. Awaiting review.`,'ok',8000);
      go('payouts');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}

/* Klawiatura ekranowa vs dolny tabbar: position:fixed na iOS nie wie o niej
   nic i pasek zawisa nad polem edycji w polowie ekranu — na czas pisania
   znika (body.kb-open w portal.css), wraca po zamknieciu klawiatury. */
addEventListener('focusin',e=>{
  if(e.target.matches&&e.target.matches('input,textarea,select'))
    document.body.classList.add('kb-open');
});
addEventListener('focusout',()=>setTimeout(()=>{
  const a=document.activeElement;
  if(!(a&&a.matches&&a.matches('input,textarea,select')))
    document.body.classList.remove('kb-open');
},80));

/* ---------- offline ----------
   PWA otwarta bez sieci pokazuje ostatnie dane, ale kazdy zapis przepadnie.
   Pasek mowi to wprost (wzor z panelu admina) — w ukladzie nad naglowkiem,
   nie na fixed, zeby nie zaslanial hamburgera i przyciskow. */
function paintOffline(){
  const bar=document.querySelector('.offline-bar');
  if(navigator.onLine){bar&&bar.remove();return}
  if(bar)return;
  const b=document.createElement('div');b.className='offline-bar';
  b.textContent='Offline — showing the last loaded data, changes will not save.';
  const m=document.querySelector('.main');
  m?m.prepend(b):document.body.prepend(b);
}
addEventListener('online',()=>{paintOffline();toast('Back online.','ok',3000)});
addEventListener('offline',paintOffline);
paintOffline();

/* gsi (async) mogl zaladowac sie PRZED tym plikiem (defer) — wtedy jego
   onload trafil w pustke i przycisk Google nikt by juz nie narysowal. */
if(window.google)initGoogle();
boot();
