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
/* ?next= po logowaniu: tylko ta sama domena. startsWith('/') przepuszczal
   „/\evil.com" (przegladarka czyta /\ jak //) i „/%09/evil.com". */
function bezpiecznyNext(n){
  if(!n)return '';
  try{const u=new URL(n,location.origin);return u.origin===location.origin?u.pathname+u.search+u.hash:''}catch(e){return ''}
}
let IMP=null;
function impToken(t){
  try{const b=String(t).split('.')[0].replace(/-/g,'+').replace(/_/g,'/');
    return JSON.parse(atob(b+'==='.slice((b.length+3)%4))).imp===1}catch(e){return false}
}
try{
  const _qi=new URLSearchParams(location.search);
  const _hi=new URLSearchParams(location.hash.slice(1));
  const _aq=window.__authQ||{};
  const _qimp=_aq.impersonate||_qi.get('impersonate');
  const _t=_aq.imp||_hi.get('imp')||(_qimp&&_qimp!=='1'?_qimp:'');
  if(_t||_qi.has('impersonate')||_hi.has('imp')){
    /* Przyjmujemy WYLACZNIE token podgladu (flaga imp w podpisanym payloadzie).
       Zwykla sesja podrzucona w linku logowalaby ofiare na cudze konto —
       a ta potem wpisywalaby tam swoje dane do wyplat i KYC. */
    if(_t&&impToken(_t))sessionStorage.setItem('pf_imp',_t);
    _qi.delete('impersonate');_hi.delete('imp');
    history.replaceState(null,'',location.pathname+(_qi.toString()?'?'+_qi:'')+(_hi.toString()?'#'+_hi:''));
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
/* Do interpolacji w onclick="fn('...')": samo esc() nie wystarcza, bo parser HTML
   odwija &#39; z powrotem do apostrofu PRZED parsowaniem JS-a. Najpierw escape
   JS-a, potem HTML calego wyniku (lustro jsq z admin-panel.js). */
const jsq=s=>esc(String(s??'').replace(/\\/g,'\\\\').replace(/'/g,"\\'"));
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
const THEME_SUN='<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M184,128a56,56,0,1,1-56-56A56,56,0,0,1,184,128Z" opacity="0.2"/><path d="M120,40V16a8,8,0,0,1,16,0V40a8,8,0,0,1-16,0Zm72,88a64,64,0,1,1-64-64A64.07,64.07,0,0,1,192,128Zm-16,0a48,48,0,1,0-48,48A48.05,48.05,0,0,0,176,128ZM58.34,69.66A8,8,0,0,0,69.66,58.34l-16-16A8,8,0,0,0,42.34,53.66Zm0,116.68-16,16a8,8,0,0,0,11.32,11.32l16-16a8,8,0,0,0-11.32-11.32ZM192,72a8,8,0,0,0,5.66-2.34l16-16a8,8,0,0,0-11.32-11.32l-16,16A8,8,0,0,0,192,72Zm5.66,114.34a8,8,0,0,0-11.32,11.32l16,16a8,8,0,0,0,11.32-11.32ZM48,128a8,8,0,0,0-8-8H16a8,8,0,0,0,0,16H40A8,8,0,0,0,48,128Zm80,80a8,8,0,0,0-8,8v24a8,8,0,0,0,16,0V216A8,8,0,0,0,128,208Zm112-88H216a8,8,0,0,0,0,16h24a8,8,0,0,0,0-16Z"/></svg>';
const THEME_MOON='<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M227.89,147.89A96,96,0,1,1,108.11,28.11,96.09,96.09,0,0,0,227.89,147.89Z" opacity="0.2"/><path d="M233.54,142.23a8,8,0,0,0-8-2,88.08,88.08,0,0,1-109.8-109.8,8,8,0,0,0-10-10,104.84,104.84,0,0,0-52.91,37A104,104,0,0,0,136,224a103.09,103.09,0,0,0,62.52-20.88,104.84,104.84,0,0,0,37-52.91A8,8,0,0,0,233.54,142.23ZM188.9,190.34A88,88,0,0,1,65.66,67.11a89,89,0,0,1,31.4-26A106,106,0,0,0,96,56,104.11,104.11,0,0,0,200,160a106,106,0,0,0,14.92-1.06A89,89,0,0,1,188.9,190.34Z"/></svg>';
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

/* The privacy choices bar lives in templates/_consent.html (shared with the site);
   it sets --ck-h, the strip portal.css gives back as padding while it is open. */

/* ---------- live refresh + push deep links ---------- */
/* Fresh ME (credits, streak) + bell badge without a page reload. Re-render of
   the current view only on demand (pull-to-refresh / push navigation) or when
   the credit balance changed — no screen flashing every minute. */
let _refreshing=false; /* re-entrancy: minutowy interval + visibilitychange potrafią się nałożyć */
async function refreshLive(rerender=false){
  if(!TOKEN||!ME||_refreshing)return;
  _refreshing=true;
  refreshNotif();
  let cicho=false;
  try{
    const stare=ME.credits_usd;
    ME=await api('/api/auth/me');
    if(!rerender&&ME.credits_usd!==stare)cicho=true;
    /* Do tej pory ten przebieg pytal WYLACZNIE o saldo kredytow, wiec liczby na
       kartach kont staly do nastepnego wejscia w zakladke. Dla kont stojacych na
       poswiadczeniach lokalnych to bez znaczenia — ich equity i tak sie nie rusza.
       Dla konta z realnym rachunkiem MT5 znaczylo, ze trader patrzy na wczorajszy
       stan podczas otwartej pozycji. Przerysowujemy tylko liste Challenges i tylko
       wtedy, gdy stoi na niej cos zywego: jedno zapytanie na minute, nie dla
       kazdego i nie na kazdym ekranie. */
    if(!rerender&&window._view==='accounts'&&window._zywaLista)cicho=true;
  }catch(e){}
  finally{_refreshing=false}
  if(rerender&&window._view&&VIEWS[window._view])go(window._view);
  else if(cicho&&window._view&&VIEWS[window._view])przerysujCicho(window._view);
}
/* Odświeżenie z minutnika, a nie z ręki klienta. go() pokazywało szkielet
   (strona migała i traciła scroll), wysyłało sztuczne `view_open` do dziennika
   i wyrzucało z karty konta z powrotem na listę. Tu: bez szkieletu, bez
   telemetrii, z powrotem w to samo miejsce — i wcale, gdy klient coś wpisuje,
   ma otwarte okno albo ogląda kartę jednego konta. */
function przerysujCicho(v){
  if(window._onDetail||document.querySelector('.modal'))return;
  const a=document.activeElement;
  if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.tagName==='SELECT'))return;
  const moj=PRZEJSCIE,y=scrollY;
  Promise.resolve(VIEWS[v]()).then(()=>{
    if(moj!==PRZEJSCIE){if(VIEWS[CURV])VIEWS[CURV]();return}
    scrollTo(0,y);
  }).catch(()=>{});
}
/* Push-click target stored by sw.js in Cache Storage: read on startup AND on
   every return to the app — iOS can drop a postMessage to a suspended page,
   so this is the only reliable path. */
/* Cel deep linka to para (widok, konto): powiadomienie o koncu fazy prowadzi do
   `?view=recap&acc=N`, czyli do karty JEDNEGO konta. Bez `acc` link ladowal na
   liscie kont i trader musial sam znalezc to, o ktorym byl push. */
function navFromUrl(u){
  try{const p=new URL(u,location.origin).searchParams;
    return {v:p.get('view'),acc:+p.get('acc')||0,chat:+p.get('chat')||0};
  }catch(_){return {v:null,acc:0}}
}
function goNav(n){
  if(!n||!n.v)return false;
  if(n.v==='recap'){if(!n.acc)return false;openAcc(n.acc);return true}
  /* Push/mail o odpowiedzi supportu: dymek czatu na tej rozmowie, a pod nim
     zwykły ekran startowy (sam dymek nad pustym widokiem wyglądał jak awaria). */
  if(n.v==='support'&&window.openHelpdesk){if(!CURV)go('accounts');window.openHelpdesk(n.chat);return true}
  if(!Object.prototype.hasOwnProperty.call(VIEWS,n.v))return false;
  go(n.v);return true;
}
async function pendingNavView(){
  let v=window._pendingView;window._pendingView=null;
  try{
    const c=await caches.open('pf-nav');const r=await c.match('/__pending-nav');
    if(r){const d=await r.json();await c.delete('/__pending-nav');
      if(!v&&Date.now()-d.ts<30000)v=navFromUrl(d.url)}
  }catch(_){}
  return v||null;
}
async function applyPendingNav(){
  const n=await pendingNavView();
  if(!n)return;
  if(ME){if(goNav(n))refreshLive()}else window._pendingView=n; /* boot() finishes it */
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
    const n=navFromUrl(d.url);
    if(!n.v)return;
    /* ME is still loading (iOS waking the PWA) => boot() finishes the navigation */
    if(ME){if(goNav(n))refreshLive()}else window._pendingView=n;
  });
  navigator.serviceWorker.startMessages?.();
}

/* Pull-to-refresh (mobile PWA): a >70px drag while scrolled to the top. */
(function(){
  let y0=null,pulled=false;
  const top=()=>((document.scrollingElement||document.documentElement).scrollTop||0)<=0;
  document.addEventListener('touchstart',e=>{
    /* gest w oknie czatu pomocy przewija rozmowę, nie stronę */
    const wCzacie=e.target.closest&&e.target.closest('#hd-panel');
    y0=(ME&&top()&&!wCzacie&&e.touches.length===1)?e.touches[0].clientY:null;pulled=false;
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

/* Ikony: Phosphor Icons v2.0.8 (duotone), MIT, (c) Phosphor Icons, https://github.com/phosphor-icons/core */
const ICO={
  copy:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M216,40V168H168V88H88V40Z" opacity="0.2"/><path d="M216,32H88a8,8,0,0,0-8,8V80H40a8,8,0,0,0-8,8V216a8,8,0,0,0,8,8H168a8,8,0,0,0,8-8V176h40a8,8,0,0,0,8-8V40A8,8,0,0,0,216,32ZM160,208H48V96H160Zm48-48H176V88a8,8,0,0,0-8-8H96V48H208Z"/></svg>',
  check:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232,56V200a16,16,0,0,1-16,16H40a16,16,0,0,1-16-16V56A16,16,0,0,1,40,40H216A16,16,0,0,1,232,56Z" opacity="0.2"/><path d="M205.66,85.66l-96,96a8,8,0,0,1-11.32,0l-40-40a8,8,0,0,1,11.32-11.32L104,164.69l90.34-90.35a8,8,0,0,1,11.32,11.32Z"/></svg>',
  key:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232,96a72,72,0,0,1-98.83,66.83h0L120,176H96v24H72v24H32V184l61.17-61.17h0A72,72,0,1,1,232,96Z" opacity="0.2"/><path d="M160,16A80.07,80.07,0,0,0,83.91,120.78L26.34,178.34A8,8,0,0,0,24,184v40a8,8,0,0,0,8,8H72a8,8,0,0,0,8-8V208H96a8,8,0,0,0,8-8V184h16a8,8,0,0,0,5.66-2.34l9.56-9.57A80,80,0,1,0,160,16Zm0,144a63.7,63.7,0,0,1-23.65-4.51,8,8,0,0,0-8.84,1.68L116.69,168H96a8,8,0,0,0-8,8v16H72a8,8,0,0,0-8,8v16H40V187.31l58.83-58.82a8,8,0,0,0,1.68-8.84A64,64,0,1,1,160,160Zm32-84a12,12,0,1,1-12-12A12,12,0,0,1,192,76Z"/></svg>',
  dollar:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M192,168a40,40,0,0,1-40,40H128V128h24A40,40,0,0,1,192,168ZM112,48a40,40,0,0,0,0,80h16V48Z" opacity="0.2"/><path d="M152,120H136V56h8a32,32,0,0,1,32,32,8,8,0,0,0,16,0,48.05,48.05,0,0,0-48-48h-8V24a8,8,0,0,0-16,0V40h-8a48,48,0,0,0,0,96h8v64H104a32,32,0,0,1-32-32,8,8,0,0,0-16,0,48.05,48.05,0,0,0,48,48h16v16a8,8,0,0,0,16,0V216h16a48,48,0,0,0,0-96Zm-40,0a32,32,0,0,1,0-64h8v64Zm40,80H136V136h16a32,32,0,0,1,0,64Z"/></svg>',
  trend:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232,56v64L168,56Z" opacity="0.2"/><path d="M232,48H168a8,8,0,0,0-5.66,13.66L188.69,88,136,140.69l-34.34-34.35a8,8,0,0,0-11.32,0l-72,72a8,8,0,0,0,11.32,11.32L96,123.31l34.34,34.35a8,8,0,0,0,11.32,0L200,99.31l26.34,26.35A8,8,0,0,0,240,120V56A8,8,0,0,0,232,48Zm-8,52.69L187.31,64H224Z"/></svg>',
  layers:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,80l-96,56L32,80l96-56Z" opacity="0.2"/><path d="M230.91,172A8,8,0,0,1,228,182.91l-96,56a8,8,0,0,1-8.06,0l-96-56A8,8,0,0,1,36,169.09l92,53.65,92-53.65A8,8,0,0,1,230.91,172ZM220,121.09l-92,53.65L36,121.09A8,8,0,0,0,28,134.91l96,56a8,8,0,0,0,8.06,0l96-56A8,8,0,1,0,220,121.09ZM24,80a8,8,0,0,1,4-6.91l96-56a8,8,0,0,1,8.06,0l96,56a8,8,0,0,1,0,13.82l-96,56a8,8,0,0,1-8.06,0l-96-56A8,8,0,0,1,24,80Zm23.88,0L128,126.74,208.12,80,128,33.26Z"/></svg>',
  wallet:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,88V200a8,8,0,0,1-8,8H56a16,16,0,0,1-16-16V64A16,16,0,0,0,56,80H216A8,8,0,0,1,224,88Z" opacity="0.2"/><path d="M216,72H56a8,8,0,0,1,0-16H192a8,8,0,0,0,0-16H56A24,24,0,0,0,32,64V192a24,24,0,0,0,24,24H216a16,16,0,0,0,16-16V88A16,16,0,0,0,216,72Zm0,128H56a8,8,0,0,1-8-8V86.63A23.84,23.84,0,0,0,56,88H216Zm-48-60a12,12,0,1,1,12,12A12,12,0,0,1,168,140Z"/></svg>',
  alert:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M215.46,216H40.54C27.92,216,20,202.79,26.13,192.09L113.59,40.22c6.3-11,22.52-11,28.82,0l87.46,151.87C236,202.79,228.08,216,215.46,216Z" opacity="0.2"/><path d="M236.8,188.09,149.35,36.22h0a24.76,24.76,0,0,0-42.7,0L19.2,188.09a23.51,23.51,0,0,0,0,23.72A24.35,24.35,0,0,0,40.55,224h174.9a24.35,24.35,0,0,0,21.33-12.19A23.51,23.51,0,0,0,236.8,188.09ZM222.93,203.8a8.5,8.5,0,0,1-7.48,4.2H40.55a8.5,8.5,0,0,1-7.48-4.2,7.59,7.59,0,0,1,0-7.72L120.52,44.21a8.75,8.75,0,0,1,15,0l87.45,151.87A7.59,7.59,0,0,1,222.93,203.8ZM120,144V104a8,8,0,0,1,16,0v40a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,180Z"/></svg>',
  target:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M176,128a48,48,0,1,1-48-48A48,48,0,0,1,176,128Z" opacity="0.2"/><path d="M221.87,83.16A104.1,104.1,0,1,1,195.67,49l22.67-22.68a8,8,0,0,1,11.32,11.32l-96,96a8,8,0,0,1-11.32-11.32l27.72-27.72a40,40,0,1,0,17.87,31.09,8,8,0,1,1,16-.9,56,56,0,1,1-22.38-41.65L184.3,60.39a87.88,87.88,0,1,0,23.13,29.67,8,8,0,0,1,14.44-6.9Z"/></svg>',
  cal:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M216,48V88H40V48a8,8,0,0,1,8-8H208A8,8,0,0,1,216,48Z" opacity="0.2"/><path d="M208,32H184V24a8,8,0,0,0-16,0v8H88V24a8,8,0,0,0-16,0v8H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,48v8a8,8,0,0,0,16,0V48h80v8a8,8,0,0,0,16,0V48h24V80H48V48ZM208,208H48V96H208V208Z"/></svg>',
  eye:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M128,56C48,56,16,128,16,128s32,72,112,72,112-72,112-72S208,56,128,56Zm0,112a40,40,0,1,1,40-40A40,40,0,0,1,128,168Z" opacity="0.2"/><path d="M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z"/></svg>',
  spark:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M194.82,151.43l-51.66,19a7.88,7.88,0,0,0-4.69,4.69l-19,51.66a7.92,7.92,0,0,1-14.86,0l-19-51.66a7.88,7.88,0,0,0-4.69-4.69l-51.66-19a7.92,7.92,0,0,1,0-14.86l51.66-19a7.88,7.88,0,0,0,4.69-4.69l19-51.66a7.92,7.92,0,0,1,14.86,0l19,51.66a7.88,7.88,0,0,0,4.69,4.69l51.66,19A7.92,7.92,0,0,1,194.82,151.43Z" opacity="0.2"/><path d="M197.58,129.06l-51.61-19-19-51.65a15.92,15.92,0,0,0-29.88,0L78.07,110l-51.65,19a15.92,15.92,0,0,0,0,29.88L78,178l19,51.62a15.92,15.92,0,0,0,29.88,0l19-51.61,51.65-19a15.92,15.92,0,0,0,0-29.88ZM140.39,163a15.87,15.87,0,0,0-9.43,9.43l-19,51.46L93,172.39A15.87,15.87,0,0,0,83.61,163h0L32.15,144l51.46-19A15.87,15.87,0,0,0,93,115.61l19-51.46,19,51.46a15.87,15.87,0,0,0,9.43,9.43l51.46,19ZM144,40a8,8,0,0,1,8-8h16V16a8,8,0,0,1,16,0V32h16a8,8,0,0,1,0,16H184V64a8,8,0,0,1-16,0V48H152A8,8,0,0,1,144,40ZM248,88a8,8,0,0,1-8,8h-8v8a8,8,0,0,1-16,0V96h-8a8,8,0,0,1,0-16h8V72a8,8,0,0,1,16,0v8h8A8,8,0,0,1,248,88Z"/></svg>',
  trophy:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M200,56v55.1c0,39.7-31.75,72.6-71.45,72.9A72,72,0,0,1,56,112V56a8,8,0,0,1,8-8H192A8,8,0,0,1,200,56Z" opacity="0.2"/><path d="M232,64H208V56a16,16,0,0,0-16-16H64A16,16,0,0,0,48,56v8H24A16,16,0,0,0,8,80V96a40,40,0,0,0,40,40h3.65A80.13,80.13,0,0,0,120,191.61V216H96a8,8,0,0,0,0,16h64a8,8,0,0,0,0-16H136V191.58c31.94-3.23,58.44-25.64,68.08-55.58H208a40,40,0,0,0,40-40V80A16,16,0,0,0,232,64ZM48,120A24,24,0,0,1,24,96V80H48v32q0,4,.39,8Zm144-8.9c0,35.52-28.49,64.64-63.51,64.9H128a64,64,0,0,1-64-64V56H192ZM232,96a24,24,0,0,1-24,24h-.5a81.81,81.81,0,0,0,.5-8.9V80h24Z"/></svg>',
  crown:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M239.78,86.62,202.78,200S176,184,128,184s-74.78,16-74.78,16l-37-113.37a4.1,4.1,0,0,1,5.72-5l53.41,26.62a4.11,4.11,0,0,0,5.36-1.56L124.48,34a4.11,4.11,0,0,1,7,0l43.77,72.74a4.12,4.12,0,0,0,5.35,1.56l53.43-26.64A4.1,4.1,0,0,1,239.78,86.62Z" opacity="0.2"/><path d="M243.84,76.19a12.08,12.08,0,0,0-13.34-1.7l-50.21,25L138.37,29.86a12.11,12.11,0,0,0-20.74,0L75.71,99.52l-50.19-25A12.11,12.11,0,0,0,8.62,89.12l37,113.36a8,8,0,0,0,11.68,4.4C57.55,206.73,83.12,192,128,192s70.45,14.73,70.68,14.87a8,8,0,0,0,11.71-4.39l37-113.33A12.06,12.06,0,0,0,243.84,76.19ZM198,188.83C186,183.74,162.08,176,128,176s-58,7.74-70,12.83L26.71,93l45.07,22.47a12.17,12.17,0,0,0,15.78-4.59L128,43.66l40.44,67.2a12.18,12.18,0,0,0,15.77,4.59L229.29,93Zm-22.13-32a8,8,0,0,1-7.87,6.61,8.36,8.36,0,0,1-1.4-.12,228.2,228.2,0,0,0-77.22,0,8,8,0,0,1-2.78-15.76,244.42,244.42,0,0,1,82.78,0A8,8,0,0,1,175.88,156.8Z"/></svg>',
  book:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232,64V192a8,8,0,0,1-8,8H160a32,32,0,0,0-32,32,32,32,0,0,0-32-32H32a8,8,0,0,1-8-8V64a8,8,0,0,1,8-8H96a32,32,0,0,1,32,32,32,32,0,0,1,32-32h64A8,8,0,0,1,232,64Z" opacity="0.2"/><path d="M224,48H160a40,40,0,0,0-32,16A40,40,0,0,0,96,48H32A16,16,0,0,0,16,64V192a16,16,0,0,0,16,16H96a24,24,0,0,1,24,24,8,8,0,0,0,16,0,24,24,0,0,1,24-24h64a16,16,0,0,0,16-16V64A16,16,0,0,0,224,48ZM96,192H32V64H96a24,24,0,0,1,24,24V200A39.81,39.81,0,0,0,96,192Zm128,0H160a39.81,39.81,0,0,0-24,8V88a24,24,0,0,1,24-24h64Z"/></svg>',
  bars:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M208,40V208H152V40Z" opacity="0.2"/><path d="M224,200h-8V40a8,8,0,0,0-8-8H152a8,8,0,0,0-8,8V80H96a8,8,0,0,0-8,8v40H48a8,8,0,0,0-8,8v64H32a8,8,0,0,0,0,16H224a8,8,0,0,0,0-16ZM160,48h40V200H160ZM104,96h40V200H104ZM56,144H88v56H56Z"/></svg>',
  gift:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M208,128v72a8,8,0,0,1-8,8H56a8,8,0,0,1-8-8V128Z" opacity="0.2"/><path d="M216,72H180.92c.39-.33.79-.65,1.17-1A29.53,29.53,0,0,0,192,49.57,32.62,32.62,0,0,0,158.44,16,29.53,29.53,0,0,0,137,25.91a54.94,54.94,0,0,0-9,14.48,54.94,54.94,0,0,0-9-14.48A29.53,29.53,0,0,0,97.56,16,32.62,32.62,0,0,0,64,49.57,29.53,29.53,0,0,0,73.91,71c.38.33.78.65,1.17,1H40A16,16,0,0,0,24,88v32a16,16,0,0,0,16,16v64a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V136a16,16,0,0,0,16-16V88A16,16,0,0,0,216,72ZM149,36.51a13.69,13.69,0,0,1,10-4.5h.49A16.62,16.62,0,0,1,176,49.08a13.69,13.69,0,0,1-4.5,10c-9.49,8.4-25.24,11.36-35,12.4C137.7,60.89,141,45.5,149,36.51Zm-64.09.36A16.63,16.63,0,0,1,96.59,32h.49a13.69,13.69,0,0,1,10,4.5c8.39,9.48,11.35,25.2,12.39,34.92-9.72-1-25.44-4-34.92-12.39a13.69,13.69,0,0,1-4.5-10A16.6,16.6,0,0,1,84.87,36.87ZM40,88h80v32H40Zm16,48h64v64H56Zm144,64H136V136h64Zm16-80H136V88h80v32Z"/></svg>',
  payout:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M16,152H48v56H16a8,8,0,0,1-8-8V160A8,8,0,0,1,16,152ZM204,56a28,28,0,0,0-12,2.71h0A28,28,0,1,0,176,85.29h0A28,28,0,1,0,204,56Z" opacity="0.2"/><path d="M230.33,141.06a24.43,24.43,0,0,0-21.24-4.23l-41.84,9.62A28,28,0,0,0,140,112H89.94a31.82,31.82,0,0,0-22.63,9.37L44.69,144H16A16,16,0,0,0,0,160v40a16,16,0,0,0,16,16H120a7.93,7.93,0,0,0,1.94-.24l64-16a6.94,6.94,0,0,0,1.19-.4L226,182.82l.44-.2a24.6,24.6,0,0,0,3.93-41.56ZM16,160H40v40H16Zm203.43,8.21-38,16.18L119,200H56V155.31l22.63-22.62A15.86,15.86,0,0,1,89.94,128H140a12,12,0,0,1,0,24H112a8,8,0,0,0,0,16h32a8.32,8.32,0,0,0,1.79-.2l67-15.41.31-.08a8.6,8.6,0,0,1,6.3,15.9ZM164,96a36,36,0,0,0,5.9-.48,36,36,0,1,0,28.22-47A36,36,0,1,0,164,96Zm60-12a20,20,0,1,1-20-20A20,20,0,0,1,224,84ZM164,40a20,20,0,0,1,19.25,14.61,36,36,0,0,0-15,24.93A20.42,20.42,0,0,1,164,80a20,20,0,0,1,0-40Z"/></svg>',
  file:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,56V208l-32-16-32,16-32-16L96,208,64,192,32,208V56a8,8,0,0,1,8-8H216A8,8,0,0,1,224,56Z" opacity="0.2"/><path d="M72,104a8,8,0,0,1,8-8h96a8,8,0,0,1,0,16H80A8,8,0,0,1,72,104Zm8,40h96a8,8,0,0,0,0-16H80a8,8,0,0,0,0,16ZM232,56V208a8,8,0,0,1-11.58,7.15L192,200.94l-28.42,14.21a8,8,0,0,1-7.16,0L128,200.94,99.58,215.15a8,8,0,0,1-7.16,0L64,200.94,35.58,215.15A8,8,0,0,1,24,208V56A16,16,0,0,1,40,40H216A16,16,0,0,1,232,56Zm-16,0H40V195.06l20.42-10.22a8,8,0,0,1,7.16,0L96,199.06l28.42-14.22a8,8,0,0,1,7.16,0L160,199.06l28.42-14.22a8,8,0,0,1,7.16,0L216,195.06Z"/></svg>',
  shield:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M216,56v58.77c0,84.18-71.31,112.07-85.54,116.8a7.54,7.54,0,0,1-4.92,0C111.31,226.86,40,199,40,114.79V56a8,8,0,0,1,8-8H208A8,8,0,0,1,216,56Z" opacity="0.2"/><path d="M208,40H48A16,16,0,0,0,32,56v58.78c0,89.61,75.82,119.34,91,124.39a15.53,15.53,0,0,0,10,0c15.2-5.05,91-34.78,91-124.39V56A16,16,0,0,0,208,40Zm0,74.79c0,78.42-66.35,104.62-80,109.18-13.53-4.51-80-30.69-80-109.18V56H208ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.68l50.34-50.34a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z"/></svg>',
  help:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,128a96,96,0,1,1-96-96A96,96,0,0,1,224,128Z" opacity="0.2"/><path d="M140,180a12,12,0,1,1-12-12A12,12,0,0,1,140,180ZM128,72c-22.06,0-40,16.15-40,36v4a8,8,0,0,0,16,0v-4c0-11,10.77-20,24-20s24,9,24,20-10.77,20-24,20a8,8,0,0,0-8,8v8a8,8,0,0,0,16,0v-.72c18.24-3.35,32-17.9,32-35.28C168,88.15,150.06,72,128,72Zm104,56A104,104,0,1,1,128,24,104.11,104.11,0,0,1,232,128Zm-16,0a88,88,0,1,0-88,88A88.1,88.1,0,0,0,216,128Z"/></svg>',
  gear:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M230.1,108.76,198.25,90.62c-.64-1.16-1.31-2.29-2-3.41l-.12-36A104.61,104.61,0,0,0,162,32L130,49.89c-1.34,0-2.69,0-4,0L94,32A104.58,104.58,0,0,0,59.89,51.25l-.16,36c-.7,1.12-1.37,2.26-2,3.41l-31.84,18.1a99.15,99.15,0,0,0,0,38.46l31.85,18.14c.64,1.16,1.31,2.29,2,3.41l.12,36A104.61,104.61,0,0,0,94,224l32-17.87c1.34,0,2.69,0,4,0L162,224a104.58,104.58,0,0,0,34.08-19.25l.16-36c.7-1.12,1.37-2.26,2-3.41l31.84-18.1A99.15,99.15,0,0,0,230.1,108.76ZM128,168a40,40,0,1,1,40-40A40,40,0,0,1,128,168Z" opacity="0.2"/><path d="M128,80a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Zm109.94-52.79a8,8,0,0,0-3.89-5.4l-29.83-17-.12-33.62a8,8,0,0,0-2.83-6.08,111.91,111.91,0,0,0-36.72-20.67,8,8,0,0,0-6.46.59L128,41.85,97.88,25a8,8,0,0,0-6.47-.6A111.92,111.92,0,0,0,54.73,45.15a8,8,0,0,0-2.83,6.07l-.15,33.65-29.83,17a8,8,0,0,0-3.89,5.4,106.47,106.47,0,0,0,0,41.56,8,8,0,0,0,3.89,5.4l29.83,17,.12,33.63a8,8,0,0,0,2.83,6.08,111.91,111.91,0,0,0,36.72,20.67,8,8,0,0,0,6.46-.59L128,214.15,158.12,231a7.91,7.91,0,0,0,3.9,1,8.09,8.09,0,0,0,2.57-.42,112.1,112.1,0,0,0,36.68-20.73,8,8,0,0,0,2.83-6.07l.15-33.65,29.83-17a8,8,0,0,0,3.89-5.4A106.47,106.47,0,0,0,237.94,107.21Zm-15,34.91-28.57,16.25a8,8,0,0,0-3,3c-.58,1-1.19,2.06-1.81,3.06a7.94,7.94,0,0,0-1.22,4.21l-.15,32.25a95.89,95.89,0,0,1-25.37,14.3L134,199.13a8,8,0,0,0-3.91-1h-.19c-1.21,0-2.43,0-3.64,0a8.1,8.1,0,0,0-4.1,1l-28.84,16.1A96,96,0,0,1,67.88,201l-.11-32.2a8,8,0,0,0-1.22-4.22c-.62-1-1.23-2-1.8-3.06a8.09,8.09,0,0,0-3-3.06l-28.6-16.29a90.49,90.49,0,0,1,0-28.26L61.67,97.63a8,8,0,0,0,3-3c.58-1,1.19-2.06,1.81-3.06a7.94,7.94,0,0,0,1.22-4.21l.15-32.25a95.89,95.89,0,0,1,25.37-14.3L122,56.87a8,8,0,0,0,4.1,1c1.21,0,2.43,0,3.64,0a8,8,0,0,0,4.1-1l28.84-16.1A96,96,0,0,1,188.12,55l.11,32.2a8,8,0,0,0,1.22,4.22c.62,1,1.23,2,1.8,3.06a8.09,8.09,0,0,0,3,3.06l28.6,16.29A90.49,90.49,0,0,1,222.9,142.12Z"/></svg>',
  medal:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M176,96a48,48,0,1,1-48-48A48,48,0,0,1,176,96Z" opacity="0.2"/><path d="M216,96A88,88,0,1,0,72,163.83V240a8,8,0,0,0,11.58,7.16L128,225l44.43,22.21A8.07,8.07,0,0,0,176,248a8,8,0,0,0,8-8V163.83A87.85,87.85,0,0,0,216,96ZM56,96a72,72,0,1,1,72,72A72.08,72.08,0,0,1,56,96ZM168,227.06l-36.43-18.21a8,8,0,0,0-7.16,0L88,227.06V174.37a87.89,87.89,0,0,0,80,0ZM128,152A56,56,0,1,0,72,96,56.06,56.06,0,0,0,128,152Zm0-96A40,40,0,1,1,88,96,40,40,0,0,1,128,56Z"/></svg>',
  chat:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,128A96,96,0,0,1,79.93,211.11h0L42.54,223.58a8,8,0,0,1-10.12-10.12l12.47-37.39h0A96,96,0,1,1,224,128Z" opacity="0.2"/><path d="M128,24A104,104,0,0,0,36.18,176.88L24.83,210.93a16,16,0,0,0,20.24,20.24l34.05-11.35A104,104,0,1,0,128,24Zm0,192a87.87,87.87,0,0,1-44.06-11.81,8,8,0,0,0-4-1.08,7.85,7.85,0,0,0-2.53.42L40,216,52.47,178.6a8,8,0,0,0-.66-6.54A88,88,0,1,1,128,216Zm12-88a12,12,0,1,1-12-12A12,12,0,0,1,140,128Zm-44,0a12,12,0,1,1-12-12A12,12,0,0,1,96,128Zm88,0a12,12,0,1,1-12-12A12,12,0,0,1,184,128Z"/></svg>',
  print:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232,96v80H192V152H64v24H24V96c0-8.84,7.76-16,17.33-16H214.67C224.24,80,232,87.16,232,96Z" opacity="0.2"/><path d="M214.67,72H200V40a8,8,0,0,0-8-8H64a8,8,0,0,0-8,8V72H41.33C27.36,72,16,82.77,16,96v80a8,8,0,0,0,8,8H56v32a8,8,0,0,0,8,8H192a8,8,0,0,0,8-8V184h32a8,8,0,0,0,8-8V96C240,82.77,228.64,72,214.67,72ZM72,48H184V72H72ZM184,208H72V160H184Zm40-40H200V152a8,8,0,0,0-8-8H64a8,8,0,0,0-8,8v16H32V96c0-4.41,4.19-8,9.33-8H214.67c5.14,0,9.33,3.59,9.33,8Zm-24-52a12,12,0,1,1-12-12A12,12,0,0,1,200,116Z"/></svg>',
  grid:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M112,56v48a8,8,0,0,1-8,8H56a8,8,0,0,1-8-8V56a8,8,0,0,1,8-8h48A8,8,0,0,1,112,56Zm88-8H152a8,8,0,0,0-8,8v48a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V56A8,8,0,0,0,200,48Zm-96,96H56a8,8,0,0,0-8,8v48a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V152A8,8,0,0,0,104,144Zm96,0H152a8,8,0,0,0-8,8v48a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V152A8,8,0,0,0,200,144Z" opacity="0.2"/><path d="M200,136H152a16,16,0,0,0-16,16v48a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V152A16,16,0,0,0,200,136Zm0,64H152V152h48v48ZM104,40H56A16,16,0,0,0,40,56v48a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V56A16,16,0,0,0,104,40Zm0,64H56V56h48v48Zm96-64H152a16,16,0,0,0-16,16v48a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V56A16,16,0,0,0,200,40Zm0,64H152V56h48v48Zm-96,32H56a16,16,0,0,0-16,16v48a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V152A16,16,0,0,0,104,136Zm0,64H56V152h48v48Z"/></svg>',
  flame:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M208,144a80,80,0,0,1-160,0c0-30.57,14.42-58.26,31-80l33,32,26.27-72C159.86,41.92,208,88.15,208,144Z" opacity="0.2"/><path d="M183.89,153.34a57.6,57.6,0,0,1-46.56,46.55A8.75,8.75,0,0,1,136,200a8,8,0,0,1-1.32-15.89c16.57-2.79,30.63-16.85,33.44-33.45a8,8,0,0,1,15.78,2.68ZM216,144a88,88,0,0,1-176,0c0-27.92,11-56.47,32.66-84.85a8,8,0,0,1,11.93-.89l24.12,23.41,22-60.41a8,8,0,0,1,12.63-3.41C165.21,36,216,84.55,216,144Zm-16,0c0-46.09-35.79-85.92-58.21-106.33L119.52,98.74a8,8,0,0,1-13.09,3L80.06,76.16C64.09,99.21,56,122,56,144a72,72,0,0,0,144,0Z"/></svg>',
  cert:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,56V92.23a48,48,0,1,0-64,71.57h0V192H40a8,8,0,0,1-8-8V56a8,8,0,0,1,8-8H216A8,8,0,0,1,224,56Z" opacity="0.2"/><path d="M248,128a56,56,0,1,0-96,39.14V224a8,8,0,0,0,11.58,7.16L192,216.94l28.42,14.22A8,8,0,0,0,232,224V167.14A55.81,55.81,0,0,0,248,128ZM192,88a40,40,0,1,1-40,40A40,40,0,0,1,192,88Zm3.58,112.84a8,8,0,0,0-7.16,0L168,211.06V178.59a55.94,55.94,0,0,0,48,0v32.47ZM136,192a8,8,0,0,1-8,8H40a16,16,0,0,1-16-16V56A16,16,0,0,1,40,40H216a16,16,0,0,1,16,16,8,8,0,0,1-16,0H40V184h88A8,8,0,0,1,136,192Zm-16-56a8,8,0,0,1-8,8H72a8,8,0,0,1,0-16h40A8,8,0,0,1,120,136Zm0-32a8,8,0,0,1-8,8H72a8,8,0,0,1,0-16h40A8,8,0,0,1,120,104Z"/></svg>',
  trade:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M224,56V208H32V48H216A8,8,0,0,1,224,56Z" opacity="0.2"/><path d="M232,208a8,8,0,0,1-8,8H32a8,8,0,0,1-8-8V48a8,8,0,0,1,16,0V156.69l50.34-50.35a8,8,0,0,1,11.32,0L128,132.69,180.69,80H160a8,8,0,0,1,0-16h40a8,8,0,0,1,8,8v40a8,8,0,0,1-16,0V91.31l-58.34,58.35a8,8,0,0,1-11.32,0L96,123.31l-56,56V200H224A8,8,0,0,1,232,208Z"/></svg>',
  pie:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M96,37.5v72l-62.4,36A96,96,0,0,1,96,37.5Z" opacity="0.2"/><path d="M100,116.43a8,8,0,0,0,4-6.93v-72A8,8,0,0,0,93.34,30,104.06,104.06,0,0,0,25.73,147a8,8,0,0,0,4.52,5.81,7.86,7.86,0,0,0,3.35.74,8,8,0,0,0,4-1.07ZM88,49.62v55.26L40.12,132.51C40,131,40,129.48,40,128A88.12,88.12,0,0,1,88,49.62Zm130.34,26.9c-.09-.18-.18-.37-.29-.55s-.2-.33-.31-.49A104.05,104.05,0,0,0,128,24a8,8,0,0,0-8,8v91.83l-78.81,45.9a8,8,0,0,0-2.87,11A104,104,0,0,0,232,128,103.34,103.34,0,0,0,218.34,76.52ZM136,40.36A88.05,88.05,0,0,1,199.89,77.3L136,114.51ZM128,216a88.45,88.45,0,0,1-71.49-36.68l75.4-43.91.22-.14L207.9,91.14A88,88,0,0,1,128,216Z"/></svg>',
  notebook:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M80,40V216H48a8,8,0,0,1-8-8V48a8,8,0,0,1,8-8Z" opacity="0.2"/><path d="M184,112a8,8,0,0,1-8,8H112a8,8,0,0,1,0-16h64A8,8,0,0,1,184,112Zm-8,24H112a8,8,0,0,0,0,16h64a8,8,0,0,0,0-16Zm48-88V208a16,16,0,0,1-16,16H48a16,16,0,0,1-16-16V48A16,16,0,0,1,48,32H208A16,16,0,0,1,224,48ZM48,208H72V48H48Zm160,0V48H88V208H208Z"/></svg>',
  grad:'<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M216,113.07v53.22a8,8,0,0,1-2,5.31c-11.3,12.59-38.9,36.4-86,36.4s-74.68-23.81-86-36.4a8,8,0,0,1-2-5.31V113.07L128,160Z" opacity="0.2"/><path d="M251.76,88.94l-120-64a8,8,0,0,0-7.52,0l-120,64a8,8,0,0,0,0,14.12L32,117.87v48.42a15.91,15.91,0,0,0,4.06,10.65C49.16,191.53,78.51,216,128,216a130,130,0,0,0,48-8.76V240a8,8,0,0,0,16,0V199.51a115.63,115.63,0,0,0,27.94-22.57A15.91,15.91,0,0,0,224,166.29V117.87l27.76-14.81a8,8,0,0,0,0-14.12ZM128,200c-43.27,0-68.72-21.14-80-33.71V126.4l76.24,40.66a8,8,0,0,0,7.52,0L176,143.47v46.34C163.4,195.69,147.52,200,128,200Zm80-33.75a97.83,97.83,0,0,1-16,14.25V134.93l16-8.53ZM188,118.94l-.22-.13-56-29.87a8,8,0,0,0-7.52,14.12L171,128l-43,22.93L25,96,128,41.07,231,96Z"/></svg>',
};
/* Kolorowe ikony menu: Microsoft Fluent UI System Icons (Color), MIT, https://github.com/microsoft/fluentui-system-icons
   Kolory przesunięte po kole barw: każda pozycja ma własny odcień (--fh), żeby sąsiednie się nie powtarzały. */
const NICO={
  accounts:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:172"><path d="M15 6C14.4477 6 14 6.44772 14 7C14 7.55228 14.4477 8 15 8H17.0858L13.25 11.8358L11.4571 10.0429C11.0666 9.65237 10.4334 9.65237 10.0429 10.0429L6.79289 13.2929C6.40237 13.6834 6.40237 14.3166 6.79289 14.7071C7.18342 15.0976 7.81658 15.0976 8.20711 14.7071L10.75 12.1642L12.5429 13.9571C12.9334 14.3476 13.5666 14.3476 13.9571 13.9571L18.5007 9.41353L18.5 11.4997C18.4998 12.052 18.9474 12.4998 19.4997 12.5C20.052 12.5002 20.4998 12.0526 20.5 11.5003L20.5015 7.00033C20.5016 6.73505 20.3962 6.48062 20.2087 6.29301C20.0211 6.1054 19.7667 6 19.5015 6H15Z" fill="url(#fc-accounts-0)"/><path d="M5 4C5 3.44772 4.55228 3 4 3C3.44772 3 3 3.44772 3 4V17.5C3 19.433 4.567 21 6.5 21H20C20.5523 21 21 20.5523 21 20C21 19.4477 20.5523 19 20 19H6.5C5.67157 19 5 18.3284 5 17.5V4Z" fill="url(#fc-accounts-1)"/><defs><linearGradient id="fc-accounts-0" x1="5.42296" y1="14.5" x2="24.4583" y2="7.09661" gradientUnits="userSpaceOnUse"><stop stop-color="#28C2A5"/><stop offset="0.445478" stop-color="#67E0D7"/><stop offset="0.956907" stop-color="#44D9C6"/></linearGradient><linearGradient id="fc-accounts-1" x1="19.3125" y1="4.125" x2="3.56041" y2="27.5804" gradientUnits="userSpaceOnUse"><stop stop-color="#70777D"/><stop offset="1" stop-color="#B9C0C7"/></linearGradient></defs></svg>',
  trade:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:212"><path d="M5 18V21C5 21.5523 4.55228 22 4 22C3.44772 22 3 21.5523 3 21V18C3 17.4477 3.44772 17 4 17C4.55228 17 5 17.4477 5 18ZM10 14C10 13.4477 9.55228 13 9 13C8.44772 13 8 13.4477 8 14V21C8 21.5523 8.44772 22 9 22C9.55228 22 10 21.5523 10 21V14ZM14 15C14.5523 15 15 15.4477 15 16V21C15 21.5523 14.5523 22 14 22C13.4477 22 13 21.5523 13 21V16C13 15.4477 13.4477 15 14 15ZM20 11C20 10.4477 19.5523 10 19 10C18.4477 10 18 10.4477 18 11V21C18 21.5523 18.4477 22 19 22C19.5523 22 20 21.5523 20 21V11Z" fill="url(#fc-trade-0)"/><path d="M17 4C16.4477 4 16 3.55228 16 3C16 2.44772 16.4477 2 17 2H21C21.5523 2 22 2.44772 22 3V7C22 7.55228 21.5523 8 21 8C20.4477 8 20 7.55228 20 7V5.41421L14.2071 11.2071C13.8166 11.5976 13.1834 11.5976 12.7929 11.2071L10 8.41421L4.70711 13.7071C4.31658 14.0976 3.68342 14.0976 3.29289 13.7071C2.90237 13.3166 2.90237 12.6834 3.29289 12.2929L9.29289 6.29289C9.48043 6.10536 9.73478 6 10 6C10.2652 6 10.5196 6.10536 10.7071 6.29289L13.5 9.08579L18.5858 4H17Z" fill="url(#fc-trade-1)"/><defs><linearGradient id="fc-trade-0" x1="21.1088" y1="9.30809" x2="0.977791" y2="20.3344" gradientUnits="userSpaceOnUse"><stop stop-color="#1F7BD1"/><stop offset="1" stop-color="#2E84E0"/></linearGradient><linearGradient id="fc-trade-1" x1="3.66826" y1="14" x2="22.3494" y2="1.53633" gradientUnits="userSpaceOnUse"><stop stop-color="#1868A0"/><stop offset="1" stop-color="#1B70B1"/></linearGradient></defs></svg>',
  board:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:45"><path d="M11 18.5V14.8184H12.5V18.5H11Z" fill="#D3A32B"/><path d="M11 18.5V14.8184H12.5V18.5H11Z" fill="url(#fc-board-0)"/><path d="M14.253 17.5C15.9861 17.5 17.4023 18.8565 17.4979 20.5656L17.503 20.75V21.25C17.503 21.6297 17.2209 21.9435 16.8548 21.9932L16.753 22H6.75C6.3703 22 6.05651 21.7178 6.00685 21.3518L6 21.25V20.75C6 19.017 7.35645 17.6008 9.06558 17.5051L9.25 17.5H14.253Z" fill="#D9D9D9"/><path d="M14.253 17.5C15.9861 17.5 17.4023 18.8565 17.4979 20.5656L17.503 20.75V21.25C17.503 21.6297 17.2209 21.9435 16.8548 21.9932L16.753 22H6.75C6.3703 22 6.05651 21.7178 6.00685 21.3518L6 21.25V20.75C6 19.017 7.35645 17.6008 9.06558 17.5051L9.25 17.5H14.253Z" fill="url(#fc-board-1)"/><path fill-rule="evenodd" clip-rule="evenodd" d="M3 5.75C3 4.7835 3.7835 4 4.75 4H7.75C8.7165 4 9.5 4.7835 9.5 5.75V8.75C9.5 10.5449 8.04493 12 6.25 12C4.45507 12 3 10.5449 3 8.75V5.75ZM4.75 5.5C4.61193 5.5 4.5 5.61193 4.5 5.75V8.75C4.5 9.7165 5.2835 10.5 6.25 10.5C7.2165 10.5 8 9.7165 8 8.75V5.75C8 5.61193 7.88807 5.5 7.75 5.5H4.75Z" fill="#D9B646"/><path fill-rule="evenodd" clip-rule="evenodd" d="M3 5.75C3 4.7835 3.7835 4 4.75 4H7.75C8.7165 4 9.5 4.7835 9.5 5.75V8.75C9.5 10.5449 8.04493 12 6.25 12C4.45507 12 3 10.5449 3 8.75V5.75ZM4.75 5.5C4.61193 5.5 4.5 5.61193 4.5 5.75V8.75C4.5 9.7165 5.2835 10.5 6.25 10.5C7.2165 10.5 8 9.7165 8 8.75V5.75C8 5.61193 7.88807 5.5 7.75 5.5H4.75Z" fill="url(#fc-board-2)"/><path fill-rule="evenodd" clip-rule="evenodd" d="M14 5.75C14 4.7835 14.7835 4 15.75 4H18.75C19.7165 4 20.5 4.7835 20.5 5.75V8.75C20.5 10.5449 19.0449 12 17.25 12C15.4551 12 14 10.5449 14 8.75V5.75ZM15.75 5.5C15.6119 5.5 15.5 5.61193 15.5 5.75V8.75C15.5 9.7165 16.2835 10.5 17.25 10.5C18.2165 10.5 19 9.7165 19 8.75V5.75C19 5.61193 18.8881 5.5 18.75 5.5H15.75Z" fill="#D9B646"/><path fill-rule="evenodd" clip-rule="evenodd" d="M14 5.75C14 4.7835 14.7835 4 15.75 4H18.75C19.7165 4 20.5 4.7835 20.5 5.75V8.75C20.5 10.5449 19.0449 12 17.25 12C15.4551 12 14 10.5449 14 8.75V5.75ZM15.75 5.5C15.6119 5.5 15.5 5.61193 15.5 5.75V8.75C15.5 9.7165 16.2835 10.5 17.25 10.5C18.2165 10.5 19 9.7165 19 8.75V5.75C19 5.61193 18.8881 5.5 18.75 5.5H15.75Z" fill="url(#fc-board-3)"/><path d="M8.25 2C7.00736 2 6 3.00736 6 4.25V10.25C6 13.4256 8.57436 16 11.75 16C14.9256 16 17.5 13.4256 17.5 10.25V4.25C17.5 3.00736 16.4926 2 15.25 2H8.25Z" fill="black"/><path d="M8.25 2C7.00736 2 6 3.00736 6 4.25V10.25C6 13.4256 8.57436 16 11.75 16C14.9256 16 17.5 13.4256 17.5 10.25V4.25C17.5 3.00736 16.4926 2 15.25 2H8.25Z" fill="url(#fc-board-4)"/><defs><linearGradient id="fc-board-0" x1="10.25" y1="14.082" x2="16.1064" y2="22.2841" gradientUnits="userSpaceOnUse"><stop offset="0.174107" stop-color="#C39328"/><stop offset="0.5625" stop-color="#D0A02B" stop-opacity="0.988235"/><stop offset="0.852679" stop-color="#C39328"/></linearGradient><linearGradient id="fc-board-1" x1="38.5919" y1="35.5" x2="31.5346" y2="10.6196" gradientUnits="userSpaceOnUse"><stop offset="0.183934" stop-color="#C29228"/><stop offset="1" stop-color="#DAB749"/></linearGradient><linearGradient id="fc-board-2" x1="7.875" y1="7.33333" x2="1.375" y2="7.33333" gradientUnits="userSpaceOnUse"><stop stop-color="#C39328"/><stop offset="1" stop-color="#C29228" stop-opacity="0.988235"/></linearGradient><linearGradient id="fc-board-3" x1="16.1667" y1="7.33333" x2="22.125" y2="7.33333" gradientUnits="userSpaceOnUse"><stop stop-color="#C39328"/><stop offset="1" stop-color="#C29228" stop-opacity="0.988235"/></linearGradient><radialGradient id="fc-board-4" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(17.5 15.3913) rotate(-131.738) scale(16.3147 17.4942)"><stop stop-color="#DAB749"/><stop offset="1" stop-color="#C29228"/></radialGradient></defs></svg>',
  achievements:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:330"><path d="M17.0019 13.244L17.0011 21.2451C17.0011 21.8528 16.3163 22.2082 15.8194 21.8584L12.001 19.1706L8.18456 21.8583C7.68767 22.2082 7.00271 21.8529 7.00271 21.2451L7.00098 13.2459C8.37047 14.3434 10.1087 14.9999 12.0003 14.9999C13.8929 14.9999 15.632 14.3427 17.0019 13.244Z" fill="url(#fc-achievements-0)"/><path d="M12 2C15.866 2 19 5.13401 19 9C19 12.866 15.866 16 12 16C8.13401 16 5 12.866 5 9C5 5.13401 8.13401 2 12 2Z" fill="url(#fc-achievements-1)"/><defs><radialGradient id="fc-achievements-0" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(12.0014 11.879) rotate(90) scale(13.7943 22.0759)"><stop stop-color="#A0186D"/><stop offset="1" stop-color="#E34591"/></radialGradient><radialGradient id="fc-achievements-1" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(-25.25 -47) rotate(56.615) scale(78.1445 66.9271)"><stop offset="0.771547" stop-color="#E02E89"/><stop offset="0.990707" stop-color="#CC1F80"/></radialGradient></defs></svg>',
  loyalty:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:270"><path d="M17.999 3C18.2398 3 18.4638 3.11546 18.6038 3.30649L18.6585 3.39288L21.922 9.42119L21.9599 9.51408L21.972 9.5539L21.9921 9.65608L21.9979 9.75C21.9979 9.81904 21.9886 9.88589 21.9711 9.94938L21.9474 10.0124H15.5L13.5 3H17.999Z" fill="#D9D9D9"/><path d="M17.999 3C18.2398 3 18.4638 3.11546 18.6038 3.30649L18.6585 3.39288L21.922 9.42119L21.9599 9.51408L21.972 9.5539L21.9921 9.65608L21.9979 9.75C21.9979 9.81904 21.9886 9.88589 21.9711 9.94938L21.9474 10.0124H15.5L13.5 3H17.999Z" fill="url(#fc-loyalty-0)"/><path d="M2.00569 9.84286L2 9.75L2.00285 9.68414L2.01606 9.59539C2.02225 9.56547 2.03015 9.53671 2.03966 9.5087L2.06248 9.45021L2.09048 9.39288L5.33943 3.39288C5.45413 3.18107 5.66229 3.03913 5.89695 3.00695L5.99895 3L10.5 3.00695L8.25 10L2.03966 9.99128C2.02352 9.94377 2.01202 9.89412 2.00569 9.84286Z" fill="#D9D9D9"/><path d="M2.00569 9.84286L2 9.75L2.00285 9.68414L2.01606 9.59539C2.02225 9.56547 2.03015 9.53671 2.03966 9.5087L2.06248 9.45021L2.09048 9.39288L5.33943 3.39288C5.45413 3.18107 5.66229 3.03913 5.89695 3.00695L5.99895 3L10.5 3.00695L8.25 10L2.03966 9.99128C2.02352 9.94377 2.01202 9.89412 2.00569 9.84286Z" fill="url(#fc-loyalty-1)"/><path d="M21.9599 9.51408L21.9221 9.42119L21.694 9H15.7657L14.9971 10.5L11.999 18.187L11.9994 21.0003L12.1236 20.9904L12.2008 20.973L12.299 20.94C12.4129 20.8917 12.5162 20.8144 12.5963 20.7081L21.8301 10.2208L21.8706 10.1683C21.876 10.1602 21.8811 10.1523 21.8862 10.144L21.9238 10.0754L21.9712 9.94938C21.9886 9.88589 21.998 9.81904 21.998 9.75L21.9921 9.65608L21.9721 9.5539L21.9599 9.51408Z" fill="url(#fc-loyalty-2)"/><path d="M11.9993 21.0003L11.899 20.9937L11.7811 20.9682C11.7209 20.9509 11.6623 20.9254 11.6075 20.8922L11.5983 20.8847C11.5512 20.8564 11.5072 20.8218 11.4672 20.7815L2.17809 10.2352L2.15185 10.2025L2.11178 10.144C2.0563 10.0544 2.01921 9.9523 2.00569 9.84286L2 9.75L2.00285 9.68414L2.01606 9.59539C2.02225 9.56547 2.03015 9.53671 2.03966 9.5087L2.06248 9.45021L2.09048 9.39288L2.30322 9H8.34375L8.998 10.5L11.9989 18.187L11.9993 21.0003Z" fill="#A9A9A9"/><path d="M11.9993 21.0003L11.899 20.9937L11.7811 20.9682C11.7209 20.9509 11.6623 20.9254 11.6075 20.8922L11.5983 20.8847C11.5512 20.8564 11.5072 20.8218 11.4672 20.7815L2.17809 10.2352L2.15185 10.2025L2.11178 10.144C2.0563 10.0544 2.01921 9.9523 2.00569 9.84286L2 9.75L2.00285 9.68414L2.01606 9.59539C2.02225 9.56547 2.03015 9.53671 2.03966 9.5087L2.06248 9.45021L2.09048 9.39288L2.30322 9H8.34375L8.998 10.5L11.9989 18.187L11.9993 21.0003Z" fill="url(#fc-loyalty-3)"/><path d="M9.2857 3.52142C9.38511 3.21078 9.67387 3 10 3H14C14.3262 3 14.6149 3.21078 14.7143 3.52142L16.6275 9.5H7.37256L9.2857 3.52142Z" fill="#7C7A7A"/><path d="M9.2857 3.52142C9.38511 3.21078 9.67387 3 10 3H14C14.3262 3 14.6149 3.21078 14.7143 3.52142L16.6275 9.5H7.37256L9.2857 3.52142Z" fill="url(#fc-loyalty-4)"/><path d="M7.53254 9L7.28568 9.77142C7.23323 9.93534 7.23875 10.1123 7.30132 10.2727L11.3013 20.5227C11.4136 20.8105 11.691 21 12 21C12.309 21 12.5864 20.8105 12.6987 20.5227L16.6987 10.2727C16.7613 10.1123 16.7668 9.93534 16.7143 9.77142L16.4675 9H7.53254Z" fill="#3F3F3F"/><path d="M7.53254 9L7.28568 9.77142C7.23323 9.93534 7.23875 10.1123 7.30132 10.2727L11.3013 20.5227C11.4136 20.8105 11.691 21 12 21C12.309 21 12.5864 20.8105 12.6987 20.5227L16.6987 10.2727C16.7613 10.1123 16.7668 9.93534 16.7143 9.77142L16.4675 9H7.53254Z" fill="url(#fc-loyalty-5)"/><path d="M17.9989 3C18.2398 3 18.4638 3.11546 18.6038 3.30649L18.6585 3.39288L21.922 9.42119L21.9599 9.51408L21.972 9.5539L21.9921 9.65608L21.9979 9.75C21.9979 9.81904 21.9886 9.88589 21.9711 9.94938L21.9237 10.0754L21.8862 10.144C21.8641 10.1796 21.839 10.2136 21.8107 10.2457L21.8705 10.1683L21.846 10.2025L12.5962 20.7081C12.5161 20.8144 12.4128 20.8917 12.2989 20.94L12.2007 20.973L12.1235 20.9904L11.9993 21.0003L11.899 20.9937L11.7811 20.9682C11.7209 20.9509 11.6623 20.9254 11.6075 20.8922L11.5983 20.8847C11.5512 20.8564 11.5072 20.8218 11.4672 20.7815L2.17809 10.2352L2.15185 10.2025L2.11178 10.144C2.0563 10.0544 2.01921 9.9523 2.00569 9.84286L2 9.75L2.00285 9.68414L2.01606 9.59539C2.02225 9.56547 2.03015 9.53671 2.03966 9.5087L2.06248 9.45021L2.09048 9.39288L5.33943 3.39288C5.45413 3.18107 5.66229 3.03913 5.89695 3.00695L5.99895 3H17.9989Z" fill="url(#fc-loyalty-6)" fill-opacity="0.7"/><defs><linearGradient id="fc-loyalty-0" x1="16.535" y1="3" x2="21.0906" y2="13.6471" gradientUnits="userSpaceOnUse"><stop stop-color="#852EE0"/><stop offset="1" stop-color="#4B18A0"/></linearGradient><linearGradient id="fc-loyalty-1" x1="9.28571" y1="0.0833332" x2="5.30781" y2="8.95533" gradientUnits="userSpaceOnUse"><stop stop-color="#D49DF0"/><stop offset="1" stop-color="#9745E3"/></linearGradient><linearGradient id="fc-loyalty-2" x1="24.3506" y1="2.99985" x2="13.4623" y2="19.6027" gradientUnits="userSpaceOnUse"><stop stop-color="#571BB1"/><stop offset="1" stop-color="#6B1FCC"/></linearGradient><linearGradient id="fc-loyalty-3" x1="3.76457" y1="6.59994" x2="11.0503" y2="20.8843" gradientUnits="userSpaceOnUse"><stop stop-color="#6F1FD1"/><stop offset="1" stop-color="#C17FEC"/></linearGradient><linearGradient id="fc-loyalty-4" x1="12" y1="3" x2="12" y2="11.125" gradientUnits="userSpaceOnUse"><stop stop-color="#A354E6"/><stop offset="1" stop-color="#9745E3"/></linearGradient><linearGradient id="fc-loyalty-5" x1="11.9936" y1="4.8" x2="11.9936" y2="21" gradientUnits="userSpaceOnUse"><stop stop-color="#6B1FCC"/><stop offset="1" stop-color="#852EE0"/></linearGradient><linearGradient id="fc-loyalty-6" x1="-0.0310272" y1="-13.9503" x2="15.1233" y2="22.7985" gradientUnits="userSpaceOnUse"><stop offset="0.53288" stop-color="#C17FEC" stop-opacity="0"/><stop offset="1" stop-color="#C17FEC"/></linearGradient></defs></svg>',
  journal:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:25"><path d="M18.5 15L20.25 15C20.6297 15 20.9435 15.2822 20.9932 15.6482L21 15.75V17.2481C21 17.6278 20.7178 17.9416 20.3518 17.9912L20.25 17.9981L18.5 17.9981V15L20.25 15L18.5 15ZM18.5 11L20.25 11C20.6297 11 20.9435 11.2822 20.9932 11.6482L21 11.75V13.2481C21 13.6278 20.7178 13.9416 20.3518 13.9912L20.25 13.9981L18.5 13.9981V11L20.25 11L18.5 11ZM18.5 7L20.25 7.00001C20.6297 7.00001 20.9435 7.28217 20.9932 7.64824L21 7.75001V9.24807C21 9.62777 20.7178 9.94156 20.3518 9.99123L20.25 9.99807L18.5 9.99806V7L20.25 7.00001L18.5 7Z" fill="url(#fc-journal-0)"/><path d="M16.7488 2C17.9915 2 18.9988 3.00736 18.9988 4.25V19.75C18.9988 20.9926 17.9915 22 16.7488 22H6.25C5.00736 22 4 20.9926 4 19.75V4.25C4 3.00736 5.00736 2 6.25 2H16.7488Z" fill="url(#fc-journal-1)"/><path d="M16.7488 2C17.9915 2 18.9988 3.00736 18.9988 4.25V19.75C18.9988 20.9926 17.9915 22 16.7488 22H6.25C5.00736 22 4 20.9926 4 19.75V4.25C4 3.00736 5.00736 2 6.25 2H16.7488Z" fill="url(#fc-journal-2)" fill-opacity="0.5"/><path d="M15.2488 5C15.663 5 15.9988 5.33579 15.9988 5.75V8.2485C15.9988 8.66272 15.663 8.9985 15.2488 8.9985H7.75C7.33579 8.9985 7 8.66272 7 8.2485V5.75C7 5.33579 7.33579 5 7.75 5H15.2488Z" fill="url(#fc-journal-3)"/><defs><linearGradient id="fc-journal-0" x1="18.5" y1="0.501139" x2="27.0522" y2="20.7891" gradientUnits="userSpaceOnUse"><stop stop-color="#ECBB83"/><stop offset="1" stop-color="#CC5D1F"/></linearGradient><linearGradient id="fc-journal-1" x1="2.12515" y1="2" x2="1.00599" y2="24.3815" gradientUnits="userSpaceOnUse"><stop stop-color="#EEC18B"/><stop offset="1" stop-color="#E8A362"/></linearGradient><radialGradient id="fc-journal-2" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(10.8745 0.75) rotate(75.3788) scale(22.9007 101.326)"><stop offset="0.5" stop-color="#E2823C" stop-opacity="0"/><stop offset="1" stop-color="#E2823C"/></radialGradient><linearGradient id="fc-journal-3" x1="8.68728" y1="5" x2="17.1338" y2="10.1278" gradientUnits="userSpaceOnUse"><stop stop-color="#F0CD9D"/><stop offset="1" stop-color="#F0CD9D"/></linearGradient></defs></svg>',
  analytics:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:190"><path d="M11 4.78369C11 4.57308 10.9114 4.37216 10.756 4.23008C10.6005 4.08799 10.3925 4.01782 10.1827 4.03672C5.59476 4.45016 2 8.30489 2 13.0001C2 17.9706 6.02944 22.0001 11 22.0001C15.6952 22.0001 19.5499 18.4053 19.9633 13.8174C19.9822 13.6076 19.9121 13.3996 19.77 13.2441C19.6279 13.0886 19.427 13.0001 19.2164 13.0001H11V4.78369Z" fill="url(#fc-analytics-0)"/><path d="M12.7276 2.2155C12.8731 2.07426 13.0696 1.99796 13.2723 2.00399C18.0267 2.1455 21.8545 5.97326 21.996 10.7276C22.002 10.9303 21.9257 11.1268 21.7844 11.2723C21.6432 11.4178 21.4491 11.4999 21.2463 11.4999H13.25C12.8358 11.4999 12.5 11.1642 12.5 10.7499V2.75366C12.5 2.55088 12.5821 2.35674 12.7276 2.2155Z" fill="url(#fc-analytics-1)"/><defs><linearGradient id="fc-analytics-0" x1="19.9664" y1="22.0001" x2="-8.77982" y2="-6.74613" gradientUnits="userSpaceOnUse"><stop stop-color="#25C5DF"/><stop offset="0.640625" stop-color="#76CCEA"/></linearGradient><linearGradient id="fc-analytics-1" x1="21.2049" y1="9.75896" x2="14.3993" y2="2.00366" gradientUnits="userSpaceOnUse"><stop stop-color="#3CC5E2"/><stop offset="1" stop-color="#76CCEA"/></linearGradient></defs></svg>',
  rewards:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:0"><path d="M20.0009 12.5L19.9999 18.75C19.9999 20.483 18.6435 21.8992 16.9344 21.9949L16.7499 22H7.24998C5.51696 22 4.10075 20.6435 4.00514 18.9344L4 18.75L4.00005 12.5002L20.0009 12.5Z" fill="url(#fc-rewards-0)"/><path d="M19.75 7C20.4404 7 21 7.55964 21 8.25V11.75C21 12.3544 20.571 12.8586 20.0009 12.9748L4.00005 12.975C3.42947 12.8592 3 12.3548 3 11.75V8.25C3 7.55964 3.55964 7 4.25 7H19.75Z" fill="url(#fc-rewards-1)"/><path d="M11.25 22V12H12.75V22H11.25Z" fill="url(#fc-rewards-2)"/><path d="M11.25 13V8H12.75V13H11.25Z" fill="url(#fc-rewards-3)"/><path fill-rule="evenodd" clip-rule="evenodd" d="M9.5 8.5C7.70507 8.5 6.25 7.04493 6.25 5.25C6.25 3.45507 7.70507 2 9.5 2C10.5052 2 11.4038 2.45637 12 3.17321C12.5962 2.45637 13.4948 2 14.5 2C16.2949 2 17.75 3.45507 17.75 5.25C17.75 7.04493 16.2949 8.5 14.5 8.5H9.5ZM7.75 5.25C7.75 4.2835 8.5335 3.5 9.5 3.5C10.4665 3.5 11.25 4.2835 11.25 5.25V7H9.5C8.5335 7 7.75 6.2165 7.75 5.25ZM12.75 7H14.5C15.4665 7 16.25 6.2165 16.25 5.25C16.25 4.2835 15.4665 3.5 14.5 3.5C13.5335 3.5 12.75 4.2835 12.75 5.25V7Z" fill="black"/><path fill-rule="evenodd" clip-rule="evenodd" d="M9.5 8.5C7.70507 8.5 6.25 7.04493 6.25 5.25C6.25 3.45507 7.70507 2 9.5 2C10.5052 2 11.4038 2.45637 12 3.17321C12.5962 2.45637 13.4948 2 14.5 2C16.2949 2 17.75 3.45507 17.75 5.25C17.75 7.04493 16.2949 8.5 14.5 8.5H9.5ZM7.75 5.25C7.75 4.2835 8.5335 3.5 9.5 3.5C10.4665 3.5 11.25 4.2835 11.25 5.25V7H9.5C8.5335 7 7.75 6.2165 7.75 5.25ZM12.75 7H14.5C15.4665 7 16.25 6.2165 16.25 5.25C16.25 4.2835 15.4665 3.5 14.5 3.5C13.5335 3.5 12.75 4.2835 12.75 5.25V7Z" fill="url(#fc-rewards-4)"/><defs><linearGradient id="fc-rewards-0" x1="10.9796" y1="25.7331" x2="10.9796" y2="8.79988" gradientUnits="userSpaceOnUse"><stop stop-color="#E44F4B"/><stop offset="0.347899" stop-color="#E7655D"/><stop offset="1" stop-color="#C21D29"/></linearGradient><radialGradient id="fc-rewards-1" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(12 4.75937) scale(23.183 9.33595)"><stop offset="0.196406" stop-color="#C21D29"/><stop offset="0.762565" stop-color="#E7655D"/><stop offset="1" stop-color="#E44F4B"/></radialGradient><linearGradient id="fc-rewards-2" x1="11.25" y1="6.375" x2="11.25" y2="20.2609" gradientUnits="userSpaceOnUse"><stop stop-color="#E4534E"/><stop offset="1" stop-color="#E02E31"/></linearGradient><linearGradient id="fc-rewards-3" x1="11.25" y1="-13.4286" x2="11.25" y2="21.5714" gradientUnits="userSpaceOnUse"><stop stop-color="#E4534E"/><stop offset="1" stop-color="#E02E31"/></linearGradient><linearGradient id="fc-rewards-4" x1="15.1944" y1="8.49838" x2="11.9054" y2="-4.9006" gradientUnits="userSpaceOnUse"><stop stop-color="#E7675F"/><stop offset="1" stop-color="#E02E31"/></linearGradient></defs></svg>',
  payouts:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:142"><path d="M12 22C15.866 22 19 20.2091 19 18V16H5V18C5 20.2091 8.13401 22 12 22Z" fill="url(#fc-payouts-0)"/><ellipse cx="12" cy="16" rx="7" ry="4" fill="url(#fc-payouts-1)"/><path d="M12 17C15.866 17 19 15.2091 19 13V11H5V13C5 15.2091 8.13401 17 12 17Z" fill="url(#fc-payouts-2)"/><ellipse cx="12" cy="11" rx="7" ry="4" fill="url(#fc-payouts-3)"/><path d="M12 12C15.866 12 19 10.2091 19 8V6H5V8C5 10.2091 8.13401 12 12 12Z" fill="url(#fc-payouts-4)"/><ellipse cx="12" cy="6" rx="7" ry="4" fill="url(#fc-payouts-5)"/><defs><linearGradient id="fc-payouts-0" x1="8.7133" y1="17.4724" x2="10.8409" y2="22.4422" gradientUnits="userSpaceOnUse"><stop stop-color="#40D878"/><stop offset="1" stop-color="#38D670"/></linearGradient><linearGradient id="fc-payouts-1" x1="19.02" y1="19.9969" x2="11.7413" y2="9.57683" gradientUnits="userSpaceOnUse"><stop stop-color="#65DF99"/><stop offset="1" stop-color="#28C257"/></linearGradient><linearGradient id="fc-payouts-2" x1="8.7133" y1="12.4724" x2="10.8409" y2="17.4422" gradientUnits="userSpaceOnUse"><stop stop-color="#40D878"/><stop offset="1" stop-color="#38D670"/></linearGradient><linearGradient id="fc-payouts-3" x1="19.02" y1="14.9969" x2="11.7413" y2="4.57683" gradientUnits="userSpaceOnUse"><stop stop-color="#65DF99"/><stop offset="1" stop-color="#28C257"/></linearGradient><linearGradient id="fc-payouts-4" x1="8.7133" y1="7.47241" x2="10.8409" y2="12.4422" gradientUnits="userSpaceOnUse"><stop stop-color="#40D878"/><stop offset="1" stop-color="#38D670"/></linearGradient><linearGradient id="fc-payouts-5" x1="19.02" y1="9.9969" x2="11.7413" y2="-0.423166" gradientUnits="userSpaceOnUse"><stop stop-color="#65DF99"/><stop offset="1" stop-color="#28C257"/></linearGradient></defs></svg>',
  certificates:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:240"><path d="M4.75 3C3.23122 3 2 4.23122 2 5.75V14H2.5C2.5 14.9537 2.88141 15.8182 3.5 16.4495V18H19.25C20.7688 18 22 16.7688 22 15.25V5.75C22 4.23122 20.7688 3 19.25 3H4.75Z" fill="url(#fc-certificates-0)"/><path d="M6.75 7C6.33579 7 6 7.33579 6 7.75C6 8.16421 6.33579 8.5 6.75 8.5H17.25C17.6642 8.5 18 8.16421 18 7.75C18 7.33579 17.6642 7 17.25 7H6.75ZM12.75 12C12.3358 12 12 12.3358 12 12.75C12 13.1642 12.3358 13.5 12.75 13.5H17.25C17.6642 13.5 18 13.1642 18 12.75C18 12.3358 17.6642 12 17.25 12H12.75Z" fill="url(#fc-certificates-1)" fill-opacity="0.9"/><path d="M9.00078 15.9998C8.16489 16.6278 7.12575 17 5.99969 17C4.87452 17 3.83614 16.6284 3.0006 16.0013L3.00012 21.2487C3.00012 21.8195 3.60462 22.1682 4.09021 21.9176L4.17969 21.8635L6.00004 20.5912L7.81969 21.8635C8.28759 22.1904 8.91961 21.8946 8.99235 21.353L8.99925 21.2487L9.00078 15.9998Z" fill="url(#fc-certificates-2)"/><path d="M5.99967 10C3.79017 10 1.99902 11.7911 1.99902 14.0006C1.99902 16.2101 3.79017 18.0013 5.99967 18.0013C8.20916 18.0013 10.0003 16.2101 10.0003 14.0006C10.0003 11.7911 8.20916 10 5.99967 10Z" fill="url(#fc-certificates-3)"/><defs><linearGradient id="fc-certificates-0" x1="2.71429" y1="5.8125" x2="12.5469" y2="19.4334" gradientUnits="userSpaceOnUse"><stop stop-color="#2E31E0"/><stop offset="1" stop-color="#2E31E0"/></linearGradient><linearGradient id="fc-certificates-1" x1="23.4" y1="21.95" x2="15.8241" y2="0.869433" gradientUnits="userSpaceOnUse"><stop stop-color="#AA9DF0"/><stop offset="1" stop-color="white"/></linearGradient><radialGradient id="fc-certificates-2" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(6.00045 15.0639) rotate(90) scale(9.45684 13.2458)"><stop stop-color="#1829A0"/><stop offset="1" stop-color="#4845E3"/></radialGradient><radialGradient id="fc-certificates-3" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(-15.2895 -18.0045) rotate(56.615) scale(44.6612 38.2502)"><stop offset="0.771547" stop-color="#2E31E0"/><stop offset="0.990707" stop-color="#1F29CC"/></radialGradient></defs></svg>',
  orders:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:300"><path d="M15 14H21V17.75C21 19.5449 19.5449 21 17.75 21H15V14Z" fill="url(#fc-orders-0)"/><path d="M3 5.25C3 4.00736 4.00736 3 5.25 3H14.75C15.9926 3 17 4.00736 17 5.25V21H6.25C4.45507 21 3 19.5449 3 17.75V5.25Z" fill="url(#fc-orders-1)"/><path d="M6.5 7.75C6.5 7.33579 6.83579 7 7.25 7H12.75C13.1642 7 13.5 7.33579 13.5 7.75C13.5 8.16421 13.1642 8.5 12.75 8.5H7.25C6.83579 8.5 6.5 8.16421 6.5 7.75Z" fill="url(#fc-orders-2)"/><path d="M6.5 7.75C6.5 7.33579 6.83579 7 7.25 7H12.75C13.1642 7 13.5 7.33579 13.5 7.75C13.5 8.16421 13.1642 8.5 12.75 8.5H7.25C6.83579 8.5 6.5 8.16421 6.5 7.75Z" fill="url(#fc-orders-3)"/><path d="M6.5 11.75C6.5 11.3358 6.83579 11 7.25 11H12.75C13.1642 11 13.5 11.3358 13.5 11.75C13.5 12.1642 13.1642 12.5 12.75 12.5H7.25C6.83579 12.5 6.5 12.1642 6.5 11.75Z" fill="url(#fc-orders-4)"/><path d="M6.5 11.75C6.5 11.3358 6.83579 11 7.25 11H12.75C13.1642 11 13.5 11.3358 13.5 11.75C13.5 12.1642 13.1642 12.5 12.75 12.5H7.25C6.83579 12.5 6.5 12.1642 6.5 11.75Z" fill="url(#fc-orders-5)"/><path d="M6.5 15.75C6.5 15.3358 6.83579 15 7.25 15H10.25C10.6642 15 11 15.3358 11 15.75C11 16.1642 10.6642 16.5 10.25 16.5H7.25C6.83579 16.5 6.5 16.1642 6.5 15.75Z" fill="url(#fc-orders-6)"/><path d="M6.5 15.75C6.5 15.3358 6.83579 15 7.25 15H10.25C10.6642 15 11 15.3358 11 15.75C11 16.1642 10.6642 16.5 10.25 16.5H7.25C6.83579 16.5 6.5 16.1642 6.5 15.75Z" fill="url(#fc-orders-7)"/><defs><linearGradient id="fc-orders-0" x1="21" y1="16.8" x2="15.346" y2="18.1988" gradientUnits="userSpaceOnUse"><stop stop-color="#D821DE"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-1" x1="5.54545" y1="3.64286" x2="11.3782" y2="20.9645" gradientUnits="userSpaceOnUse"><stop stop-color="#EC7EE0"/><stop offset="0.644682" stop-color="#E96DDF"/><stop offset="1" stop-color="#E23CE1"/></linearGradient><linearGradient id="fc-orders-2" x1="5.6" y1="15" x2="6.38285" y2="17.8183" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-3" x1="5.1" y1="7" x2="14.6408" y2="15.436" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-4" x1="5.6" y1="15" x2="6.38285" y2="17.8183" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-5" x1="5.1" y1="7" x2="14.6408" y2="15.436" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-6" x1="5.6" y1="15" x2="6.38285" y2="17.8183" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient><linearGradient id="fc-orders-7" x1="5.1" y1="7" x2="14.6408" y2="15.436" gradientUnits="userSpaceOnUse"><stop stop-color="#E23CE1"/><stop offset="1" stop-color="#A71BB6"/></linearGradient></defs></svg>',
  kyc:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:215"><path d="M3 5.75C3 5.33579 3.33579 5 3.75 5C6.41341 5 9.00797 4.05652 11.55 2.15C11.8167 1.95 12.1833 1.95 12.45 2.15C14.992 4.05652 17.5866 5 20.25 5C20.6642 5 21 5.33579 21 5.75V11C21 16.0012 18.0424 19.6757 12.2749 21.9478C12.0982 22.0174 11.9018 22.0174 11.7251 21.9478C5.95756 19.6757 3 16.0012 3 11V5.75Z" fill="url(#fc-kyc-0)"/><path d="M3 5.75C3 5.33579 3.33579 5 3.75 5C6.41341 5 9.00797 4.05652 11.55 2.15C11.8167 1.95 12.1833 1.95 12.45 2.15C14.992 4.05652 17.5866 5 20.25 5C20.6642 5 21 5.33579 21 5.75V11C21 16.0012 18.0424 19.6757 12.2749 21.9478C12.0982 22.0174 11.9018 22.0174 11.7251 21.9478C5.95756 19.6757 3 16.0012 3 11V5.75Z" fill="url(#fc-kyc-1)" fill-opacity="0.5"/><path d="M17.5 12C20.5376 12 23 14.4624 23 17.5C23 20.5376 20.5376 23 17.5 23C14.4624 23 12 20.5376 12 17.5C12 14.4624 14.4624 12 17.5 12Z" fill="url(#fc-kyc-2)"/><path fill-rule="evenodd" clip-rule="evenodd" d="M20.8536 15.1464C21.0488 15.3417 21.0488 15.6583 20.8536 15.8536L16.8536 19.8536C16.6583 20.0488 16.3417 20.0488 16.1464 19.8536L14.1464 17.8536C13.9512 17.6583 13.9512 17.3417 14.1464 17.1464C14.3417 16.9512 14.6583 16.9512 14.8536 17.1464L16.5 18.7929L20.1464 15.1464C20.3417 14.9512 20.6583 14.9512 20.8536 15.1464Z" fill="url(#fc-kyc-3)"/><defs><radialGradient id="fc-kyc-0" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(-4.07143 -9.25) rotate(54.4973) scale(47.5993 42.0478)"><stop offset="0.337728" stop-color="#2E7BE0"/><stop offset="0.529425" stop-color="#4584E3"/><stop offset="0.682436" stop-color="#4E87E4"/><stop offset="0.860903" stop-color="#2074D4"/></radialGradient><radialGradient id="fc-kyc-1" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(17.7857 18.875) rotate(90) scale(8.125 8.35714)"><stop offset="0.5" stop-color="#1862A0"/><stop offset="1" stop-color="#1862A0" stop-opacity="0"/></radialGradient><linearGradient id="fc-kyc-2" x1="12.3929" y1="14.0625" x2="19.9842" y2="21.9496" gradientUnits="userSpaceOnUse"><stop stop-color="#4082E3"/><stop offset="1" stop-color="#1862A0"/></linearGradient><linearGradient id="fc-kyc-3" x1="15.3125" y1="15.51" x2="16.4497" y2="21.1303" gradientUnits="userSpaceOnUse"><stop stop-color="white"/><stop offset="1" stop-color="#9DB3F0"/></linearGradient></defs></svg>',
  settings:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false"><path d="M12.0122 2.25C12.7462 2.25846 13.4773 2.34326 14.1937 2.50304C14.5064 2.57279 14.7403 2.83351 14.7758 3.15196L14.946 4.67881C15.0231 5.37986 15.615 5.91084 16.3206 5.91158C16.5103 5.91188 16.6979 5.87238 16.8732 5.79483L18.2738 5.17956C18.5651 5.05159 18.9055 5.12136 19.1229 5.35362C20.1351 6.43464 20.8889 7.73115 21.3277 9.14558C21.4223 9.45058 21.3134 9.78203 21.0564 9.9715L19.8149 10.8866C19.4607 11.1468 19.2516 11.56 19.2516 11.9995C19.2516 12.4389 19.4607 12.8521 19.8157 13.1129L21.0582 14.0283C21.3153 14.2177 21.4243 14.5492 21.3297 14.8543C20.8911 16.2685 20.1377 17.5649 19.1261 18.6461C18.9089 18.8783 18.5688 18.9483 18.2775 18.8206L16.8712 18.2045C16.4688 18.0284 16.0068 18.0542 15.6265 18.274C15.2463 18.4937 14.9933 18.8812 14.945 19.3177L14.7759 20.8444C14.741 21.1592 14.5122 21.4182 14.204 21.4915C12.7556 21.8361 11.2465 21.8361 9.79803 21.4915C9.48991 21.4182 9.26105 21.1592 9.22618 20.8444L9.05736 19.32C9.00777 18.8843 8.75434 18.498 8.37442 18.279C7.99451 18.06 7.5332 18.0343 7.1322 18.2094L5.72557 18.8256C5.43422 18.9533 5.09403 18.8833 4.87678 18.6509C3.86462 17.5685 3.11119 16.2705 2.6732 14.8548C2.57886 14.5499 2.68786 14.2186 2.94485 14.0293L4.18818 13.1133C4.54232 12.8531 4.75147 12.4399 4.75147 12.0005C4.75147 11.561 4.54232 11.1478 4.18771 10.8873L2.94516 9.97285C2.6878 9.78345 2.5787 9.45178 2.67337 9.14658C3.11212 7.73215 3.86594 6.43564 4.87813 5.35462C5.09559 5.12236 5.43594 5.05259 5.72724 5.18056L7.12762 5.79572C7.53056 5.97256 7.9938 5.94585 8.37577 5.72269C8.75609 5.50209 9.00929 5.11422 9.05817 4.67764L9.22824 3.15196C9.26376 2.83335 9.49786 2.57254 9.8108 2.50294C10.5281 2.34342 11.26 2.25865 12.0122 2.25ZM11.9997 8.99995C10.3428 8.99995 8.9997 10.3431 8.9997 12C8.9997 13.6568 10.3428 15 11.9997 15C13.6565 15 14.9997 13.6568 14.9997 12C14.9997 10.3431 13.6565 8.99995 11.9997 8.99995Z" fill="url(#fc-settings-0)"/><defs><linearGradient id="fc-settings-0" x1="16.6816" y1="20.9953" x2="5.7845" y2="3.99641" gradientUnits="userSpaceOnUse"><stop stop-color="#70777D"/><stop offset="1" stop-color="#B9C0C7"/></linearGradient></defs></svg>',
  academy:'<svg class="fc" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false" style="--fh:55"><path d="M2 6C2 4.89543 2.89543 4 4 4H10C10.7684 4 12 5 12 6L13 12.5L12 19.2361C11.4692 19.7111 10.7684 20 10 20H4C2.89543 20 2 19.1046 2 18V6Z" fill="url(#fc-academy-0)"/><path d="M22 6C22 4.89543 21.1046 4 20 4H14C13.2316 4 12 5 12 6L11 12.5L12 19.2361C12.5308 19.7111 13.2316 20 14 20H20C21.1046 20 22 19.1046 22 18V6Z" fill="url(#fc-academy-1)"/><path d="M3.5 5C3.5 4.44771 3.94772 4 4.5 4H10.5C11.3284 4 12 4.67157 12 5.5L12.5547 11L12 16.5C12 17.6046 11.1046 18.5 10 18.5H4.5C3.94772 18.5 3.5 18.0523 3.5 17.5V5Z" fill="url(#fc-academy-2)"/><path d="M20.5 5C20.5 4.44771 20.0523 4 19.5 4H13.5C12.6716 4 12 4.67157 12 5.5V16.5C12 17.6046 12.8954 18.5 14 18.5H19.5C20.0523 18.5 20.5 18.0523 20.5 17.5V5Z" fill="url(#fc-academy-3)"/><defs><linearGradient id="fc-academy-0" x1="3.875" y1="4.57143" x2="7.28894" y2="24.1569" gradientUnits="userSpaceOnUse"><stop stop-color="#987F1F"/><stop offset="0.908213" stop-color="#A58C22"/></linearGradient><linearGradient id="fc-academy-1" x1="20.125" y1="4.57143" x2="16.7111" y2="24.1569" gradientUnits="userSpaceOnUse"><stop stop-color="#987F1F"/><stop offset="0.908213" stop-color="#A58C22"/></linearGradient><linearGradient id="fc-academy-2" x1="2.28571" y1="3.44231" x2="27.6583" y2="15.2127" gradientUnits="userSpaceOnUse"><stop stop-color="#E7ECA2"/><stop offset="0.851323" stop-color="#DCD656"/></linearGradient><linearGradient id="fc-academy-3" x1="19.9688" y1="4.90625" x2="0.23148" y2="9.07664" gradientUnits="userSpaceOnUse"><stop offset="0.12337" stop-color="#E5E890"/><stop offset="0.61551" stop-color="#DACF49"/></linearGradient></defs></svg>',
};
const sheetIco=k=>(NICO[k]||'').replace(/fc-/g,'fcs-');

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
  {v:'journal',label:'Journal',ico:'notebook'},
  {v:'analytics',label:'Analytics',ico:'pie'},
  {v:'rewards',label:'Rewards',ico:'gift'},
  {v:'payouts',label:'Payouts',ico:'payout'},
  {v:'certificates',label:'Certificates',ico:'cert'},
  {v:'orders',label:'Invoice',ico:'file'},
  {v:'kyc',label:'KYC',ico:'shield'},
  /* Support nie ma już pozycji w menu: pomoc to dymek czatu (helpdesk.js). */
  {v:'settings',label:'Settings',ico:'gear'},
];
/* Terminal /trade to osobna strona (pełny ekran), więc link, nie widok SPA — zaraz pod Challenges. */
$('side-nav').innerHTML='<div class="side-sec sb-txt">Main menu</div>'+NAV.map((n,i)=>
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}')" title="${n.label}">${NICO[n.v]||ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`+
  (i===0?`<a class="sb-link" href="/trade" title="Trade">${NICO.trade}<span class="sb-txt">Trade</span></a>`:'')).join('')+
  `<a class="sb-link" href="/academy" target="_blank" rel="noopener" title="Academy">${NICO.academy}<span class="sb-txt">Academy</span></a>`;

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
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}');closeSheet()">${sheetIco(n.v)||ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`).join('')+
  `<a class="sb-link" href="/academy" target="_blank" rel="noopener" onclick="closeSheet()">${sheetIco('academy')}<span class="sb-txt">Academy</span></a>
   <div class="sheet-div"></div>
   <button class="sb-link theme-toggle" onclick="toggleTheme()"></button>
   <button class="sb-link sheet-signout" onclick="logout()"><svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M216,128l-40,40V88Z" opacity="0.2"/><path d="M112,216a8,8,0,0,1-8,8H48a16,16,0,0,1-16-16V48A16,16,0,0,1,48,32h56a8,8,0,0,1,0,16H48V208h56A8,8,0,0,1,112,216Zm109.66-82.34-40,40A8,8,0,0,1,168,168V136H104a8,8,0,0,1,0-16h64V88a8,8,0,0,1,13.66-5.66l40,40A8,8,0,0,1,221.66,133.66Zm-17-5.66L184,107.31v41.38Z"/></svg><span class="sb-txt">Sign Out</span></button>`;
paintTheme();   /* the sheet's toggle was just rendered — give it its icon/label */
function openSheet(){$('sheetVeil').classList.remove('hidden');$('sheet').classList.add('open')}
function closeSheet(){$('sheetVeil').classList.add('hidden');$('sheet').classList.remove('open')}
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  closeSheet();
  /* ask-modal zostaje: potwierdzenie ma callback i Escape bez odpowiedzi
     zostawiłby decyzję w zawieszeniu. pop() zdejmuje wierzchni modal. */
  const m=[...document.querySelectorAll('.modal-wrap')].filter(x=>x.id!=='ask-modal').pop();
  if(m)m.remove();
});

/* ---------- toasts ---------- */
function toast(msg,kind='ok',ms=6000){
  const t=document.createElement('div');
  t.className='toast '+kind; t.textContent=msg;
  $('toasts').appendChild(t);
  setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),380)},ms);
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

/* Przełączanie trybów karty logowania jak w iOS: pigułka przesuwa się pod
   zakładkami (CSS), karta płynnie zmienia wysokość zamiast skakać, a pola,
   które właśnie się pojawiły, łagodnie wjeżdżają. Bez animacji, gdy karta
   jest niewidoczna (start) albo system prosi o ograniczenie ruchu. */
function authTab(m){
  const card=document.querySelector('.auth-card');
  const spokoj=!card||!card.offsetParent||matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(spokoj||m===AUTHMODE){_authTabUstaw(m);return}
  const pola=[...card.querySelectorAll('.stack > *, #g-auth, #auth-buynote, #auth-casenote')];
  const bylo=new Set(pola.filter(e=>e.offsetParent));
  const tytul=$('auth-title').textContent;
  const przed=card.getBoundingClientRect().height;
  card.style.height='';card.style.transition='';
  _authTabUstaw(m);
  const po=card.getBoundingClientRect().height;
  pola.forEach(e=>{if(e.offsetParent&&!bylo.has(e)){e.classList.remove('a-in');void e.offsetWidth;e.classList.add('a-in')}});
  if($('auth-title').textContent!==tytul)['auth-title','auth-sub'].forEach(id=>{
    const e=$(id);e.classList.remove('a-swap');void e.offsetWidth;e.classList.add('a-swap')});
  if(Math.abs(po-przed)<1)return;
  card.style.height=przed+'px';card.style.overflow='hidden';
  void card.offsetHeight;
  card.style.transition='height .38s cubic-bezier(.22,1,.36,1)';
  card.style.height=po+'px';
  clearTimeout(card._authT);
  card._authT=setTimeout(()=>{card.style.height='';card.style.overflow='';card.style.transition=''},420);
}
function _authTabUstaw(m){AUTHMODE=m;
  const login=m==='login',signup=m==='signup',forgot=m==='forgot',reset=m==='reset';
  $('tab-login').classList.toggle('on',login);
  $('tab-signup').classList.toggle('on',signup);
  $('tab-login').setAttribute('aria-selected',String(login));
  $('tab-signup').setAttribute('aria-selected',String(signup));
  document.querySelector('.auth-tabs').classList.toggle('is-signup',signup);
  $('a-name').classList.toggle('hidden',!signup);
  $('a-ref').classList.toggle('hidden',!signup);
  $('a-terms-row').classList.toggle('hidden',!signup);
  $('a-mkt-row').classList.toggle('hidden',!signup);
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
      body.terms_accepted=$('a-terms').checked;body.marketing_opt_in=$('a-mkt').checked;res=await api('/api/auth/signup',{method:'POST',body:JSON.stringify(body)});
      clearRef();  /* attribution consumed — the code must not resurface later */}
    else res=await api('/api/auth/login',{method:'POST',body:JSON.stringify(body)});
    TOKEN=res.token;localStorage.setItem('pf_token',TOKEN);
    // An administrator account has ONLY the admin panel — the trader portal
    // is never rendered for it, no matter how the login started.
    if(res.trader&&res.trader.is_admin){location.replace('/admin');return}
    // There is ONE login — the admin panel redirects here with ?next=/admin and
    // after signing in we go exactly back there instead of losing the user here.
    const nextUrl=bezpiecznyNext(new URLSearchParams(location.search).get('next'));
    if(nextUrl){location.href=nextUrl;return}
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
  /* Przycisk Google to iframe od Google — kasowanie go przy KAŻDYM przełączeniu
     Log in / Create account zostawiało na chwilę pustą dziurę (miganie).
     Jeden napis „Continue with Google" pasuje do obu zakładek, więc renderujemy
     ponownie tylko wtedy, gdy zmienił się motyw albo szerokość. */
  const theme=document.documentElement.dataset.theme==='dark'?'filled_black':'outline';
  const width=Math.min(holder.offsetWidth||320,380);
  const klucz=theme+'|'+width;
  if(holder.dataset.k===klucz&&holder.childElementCount)return;
  holder.dataset.k=klucz;
  holder.innerHTML='';
  google.accounts.id.renderButton(holder,{
    theme,size:'large',shape:'pill',width,
    /* locale pinned: the whole site is English, GIS must not localize */
    locale:'en',text:'continue_with'});
}
async function onGoogleCred(resp){
  try{
    const res=await api('/api/auth/google',{method:'POST',
      body:JSON.stringify({credential:resp.credential,referral:pfRef()||null})});
    clearRef();
    TOKEN=res.token;localStorage.setItem('pf_token',TOKEN);
    if(res.trader&&res.trader.is_admin){location.replace('/admin');return}
    const nextUrl=bezpiecznyNext(new URLSearchParams(location.search).get('next'));
    if(nextUrl){location.href=nextUrl;return}
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
// Powrót przyciskiem „Wstecz” z pamięci przeglądarki (bfcache): po wylogowaniu nie pokazuj starego widoku konta.
addEventListener('pageshow',e=>{if(e.persisted)location.reload()});
function logout(){
  /* Tryb podglądu: NIE wolno dotknąć localStorage (sesja właściciela) ani
     /api/auth/logout (skasowałby cookie serwera bramkujące /admin). */
  if(IMP){try{sessionStorage.removeItem('pf_imp')}catch(e){}location.reload();return}
  // The session cookie is held by the server (it gates /admin), so clearing
  // localStorage alone would not be enough.
  fetch('/api/auth/logout',{method:'POST',headers:TOKEN?{Authorization:'Bearer '+TOKEN}:{}}).finally(()=>{
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
  const b=document.createElement('div');b.id='imp-bar';b.setAttribute('role','status');
  b.innerHTML=`<span class="imp-txt">Admin preview — <b>${esc(ME.email)}</b><span class="imp-real">. Actions here are real.</span></span>
    <button type="button" onclick="logout()">Exit preview</button>`;
  document.body.appendChild(b);
  document.body.classList.add('imp-on');
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
/* Sign-in screen: the market count from the terminal's list, live prices and spreads from
   the order book (the same source as the terminal), and the glass cube. No head counts:
   the owner does not want to advertise them (2026-11-06). Everything stops once the
   trader is in — the strip polls only while the auth screen is on. */
const AUTH_LIVE=[['Gold','xyz:GOLD',1,'$'],['S&P 500','xyz:SP500',1,''],['Bitcoin','BTC',0,'$'],['EUR/USD','xyz:EUR',5,'']];
let authLiveT=0;
async function loadAuthStats(){
  authCube();
  try{const m=await api('/api/terminal/markets');if(m.markets&&m.markets.length>=10)$('auth-mkts').textContent=m.markets.length}catch(e){}
  clearInterval(authLiveT);
  const tick=async()=>{
    if(!$('auth')||$('auth').classList.contains('hidden')||document.hidden){return}
    const rows=await Promise.all(AUTH_LIVE.map(async([name,coin,dec,pre])=>{
      try{const r=await fetch('https://api.hyperliquid.xyz/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'l2Book',coin})});
        const d=await r.json();const bid=+d.levels[0][0].px,ask=+d.levels[1][0].px,mid=(bid+ask)/2,sp=ask-bid;
        const f=(v,n)=>v.toLocaleString('en-US',{minimumFractionDigits:n,maximumFractionDigits:n});
        return `<span class="al-i"><small>${name}</small><b>${pre}${f(mid,dec)}</b><em>spread ${f(sp,sp<0.1?Math.max(dec,3):2)}</em></span>`;
      }catch(e){return ''}}));
    const html=rows.filter(Boolean).join('');
    const el=$('auth-live');if(!el)return;
    if(html){el.innerHTML=`<span class="al-t"><i></i>Live</span>${html}`;el.classList.remove('hidden')}
  };
  tick();authLiveT=setInterval(tick,15000);
}
/* Gold coin: idle spin, tilts after the pointer, drag to spin with inertia. The edge is a
   stack of thin discs between the two faces. Plain CSS 3D — nothing to download. */
function authCube(){
  const box=$('authCoin'),c=$('authCoinInner');
  if(!box||!c||box.dataset.on)return;
  box.dataset.on='1';
  if(!c.children.length){
    const T=7,n=14,h=[];
    for(let i=0;i<n;i++)h.push(`<i class="ac-l${i%2?' edge':''}" style="--z:${(-T+i*(2*T)/(n-1)).toFixed(2)}px"></i>`);
    h.push(`<div class="ac-face back" style="--z:${T}px"><img src="/static/img/logo.png" alt=""></div>`);
    h.push(`<div class="ac-face front" style="--z:${T}px"><img src="/static/img/logo.png" alt=""></div>`);
    c.innerHTML=h.join('');
  }
  /* The coin faces the visitor and sways a little; a drag spins it (with inertia) and it
     settles face-on again. A coin that never stops spinning is seen edge-on half the time. */
  const RED=matchMedia('(prefers-reduced-motion:reduce)').matches;
  let spin=0,tilt=0,vy=0,vx=0,drag=null,hx=0,hy=0;
  const paint=(t)=>{
    const sway=RED?0:Math.sin(t/1900)*13,bob=RED?0:Math.sin(t/2400)*5;
    c.style.transform=`rotateX(${(-10+bob+tilt+hy).toFixed(2)}deg) rotateY(${(-22+sway+spin+hx).toFixed(2)}deg)`;
  };
  const step=(t)=>{
    if(!$('auth')||$('auth').classList.contains('hidden')){box.dataset.on='';return}
    if(!drag){
      spin+=vy;tilt+=vx;vy*=.95;vx*=.9;
      if(Math.abs(vy)<.6){const home=Math.round(spin/360)*360;spin+=(home-spin)*.04}
      tilt*=.96;
    }
    paint(t);requestAnimationFrame(step);
  };
  box.addEventListener('pointerdown',(e)=>{drag={x:e.clientX,y:e.clientY,t:performance.now()};box.setPointerCapture(e.pointerId);e.preventDefault()});
  box.addEventListener('pointermove',(e)=>{
    if(!drag)return;
    const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
    spin+=dx*.6;tilt=Math.max(-30,Math.min(30,tilt-dy*.3));
    vy=dx*.6;vx=-dy*.3;drag={x:e.clientX,y:e.clientY,t:performance.now()};
  });
  const up=()=>{if(drag&&performance.now()-drag.t>80){vx=0;vy=0}drag=null};
  box.addEventListener('pointerup',up);box.addEventListener('pointercancel',up);
  const brand=box.parentElement;
  if(!RED&&matchMedia('(hover:hover)').matches)brand.addEventListener('mousemove',(e)=>{
    const r=brand.getBoundingClientRect();hx=((e.clientX-r.left)/r.width-.5)*14;hy=-((e.clientY-r.top)/r.height-.5)*10;
  });
  brand.addEventListener('mouseleave',()=>{hx=0;hy=0});
  paint(0);requestAnimationFrame(step);
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
    toast(prov.provisioning?'✅ Payment received. Your MT5 account is being created.':'✅ Account created. Credentials under Challenges.','ok',9000);
    go('accounts');return true
  }catch(e){toast('Error: '+e.message,'err')}}
  return false;
}

/* ---------- boot / navigation ---------- */
async function boot(){
  const q0=new URLSearchParams(location.search);
  caseLinkSeen();
  /* E-mail verification deep link: works without logging in (token from mail). */
  const vt=(window.__authQ||{}).verify||q0.get('verify');
  if(vt){
    try{await api('/api/auth/verify-email',{method:'POST',body:JSON.stringify({token:vt})});
      toast('E-mail confirmed ✅','ok',8000)}
    catch(e){toast('Verification failed: '+e.message,'err',8000)}
    history.replaceState(null,'','/portal');
  }
  const rt=(window.__authQ||{}).reset||q0.get('reset');
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
    /* Tak samo wejscie z popupu skrzynki (?case=1): to nowy klient, a skrzynka
       otworzy sie sama zaraz po rejestracji i potwierdzeniu maila.
       ?signup=1 — „Sign Up" z terminala /trade: też od razu rejestracja. */
    const zCase=!q0.get('buy')&&casePending();
    authTab(q0.get('buy')||zCase||q0.get('signup')?'signup':'login');
    if(q0.get('buy'))$('auth-buynote').classList.remove('hidden');
    else if(zCase)$('auth-casenote')?.classList.remove('hidden');
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
  const nav=(await pendingNavView())||{v:q.get('view'),acc:+q.get('acc')||0,chat:+q.get('chat')||0};
  if(q.get('upsell')==='1')window._upsellJump=true;
  /* ?view/?upsell skonsumowane — bez sprzątnięcia każdy kolejny reload PWA
     skakał do starego deep linka. ?buy zostaje: konsumuje go (i czyści)
     highlightPlanFromUrl() dopiero przy renderze sklepu. */
  if(q.get('view')||q.get('upsell'))
    history.replaceState(null,'','/portal'+(q.get('buy')?'?buy='+encodeURIComponent(q.get('buy')):''));
  /* view_open leci z go() — kazda nawigacja, nie tylko start appki */
  if(q.get('buy'))go('store');
  else if(!goNav(nav))go('accounts');
  refreshNotif();
  maybeReviewNudge();
  maybeOpenCaseFromLink();
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
  w.innerHTML=`<div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Enjoying your funded account?</h3></div>
    <p class="muted" style="font-size:13px;margin:2px 0 6px">Your challenge account was set up for you free of charge. If you like how it's going, a short Trustpilot review helps other traders find us — it takes a minute.</p>
    <div style="font-size:20px;letter-spacing:3px;color:#00b67a;margin:4px 0 12px">★★★★★</div>
    <div style="display:flex;gap:10px;margin-top:6px">
      <button class="btn-p" onclick="window.open('https://www.trustpilot.com/review/protradersfunding.com','_blank','noopener');$('tp-modal').remove()">Leave a review</button>
      <button class="btn-o" onclick="$('tp-modal').remove()">Maybe later</button>
    </div></div>`;
  document.body.appendChild(w);
}

/* Lustro DISCIPLINE_MIN_DAYS z main.py. Serwer i tak odsiewa krotsze serie —
   ta liczba stoi tu tylko po to, zeby ekran umial powiedziec, DLACZEGO kogos
   na liscie nie ma. */
const DISC_MIN_DAYS=10;

const TITLES={
  accounts:['Challenges','Live overview of your challenge accounts'],
  store:['New Challenge','One-time fee · 2-Step fee credited with your first payout'],
  board:['Leaderboard','Top traders across the platform — by profit, and by discipline'],
  achievements:['Achievements','Milestones earned from your real activity'],
  loyalty:['Loyalty','Trade your points for a discount code'],
  journal:['Journal','Your private trading notes'],
  analytics:['Analytics','Daily P&L computed from your account history'],
  weekly:['Week in review','The week that closed, day by day'],
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
  /* Stare wejścia do Support (powiadomienia, deep linki, przyciski) otwierają
     dymek czatu zamiast widoku — widok zostaje w kodzie jako zapas bez JS czatu. */
  if(v==='support'&&window.openHelpdesk){window.openHelpdesk();return}
  CURV=v; window._view=v; window._onDetail=false;
  /* Nazwa widoku w propsach: dziennik w adminie rozpisuje z tego, co klient
     faktycznie przegladal, nie tylko ze "otworzyl portal". */
  track('view_open',{view:v,pwa:document.documentElement.classList.contains('pwa')?'1':'0'});
  /* Przeglad tygodnia nie ma wlasnej pozycji w menu — wchodzi sie w niego z
     powiadomienia. Bez tego aliasu nawigacja zostawala bez zaznaczenia. */
  const sv={weekly:'analytics'}[v]||v;
  document.querySelectorAll('.sb-link[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===sv));
  const tv={store:'accounts',achievements:'rewards',loyalty:'rewards'}[v]||v;
  const tabs=[...document.querySelectorAll('.tab-item[data-v]')];
  const hit=tabs.some(b=>b.dataset.v===tv);
  tabs.forEach(b=>b.classList.toggle('on',b.dataset.v===(hit?tv:'more')));
  const t=TITLES[v]||['',''];
  $('pg-title').textContent=t[0]; $('pg-crumb').textContent=t[1];
  toggleSide(false);
  $('notif-panel')?.classList.add('hidden');
  const moj=++PRZEJSCIE;
  /* Wykresy Analytics (Chart.js z własnymi ResizeObserverami) żyły dalej na
     odpiętych canvasach aż do NASTĘPNEGO wejścia w Analytics. */
  if(anCharts.length){anCharts.forEach(c=>{try{c.destroy()}catch(_){}});anCharts=[]}
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
/* Ten sam układ co karta Free Trade Case: grafika po lewej, treść po prawej. */
const revealRow=(ico,body)=>`<div class="reveal-art" aria-hidden="true"><i>${ICO[ico]}</i></div>
  <div class="reveal-body">${body}</div>`;
const REVEAL_NEXT='<div class="reveal-next">New reveal tomorrow</div>';
function revealBack(){
  return revealRow('spark',`<div class="reveal-tag">Daily reveal</div>
    <h3 class="reveal-h">Today's card is ready</h3>
    <p class="reveal-t">A trading tip, bonus loyalty points or a rare coupon. One flip per day.</p>
    <span class="reveal-hint">Flip today's card</span>`);
}
function revealFace(p){
  if(p.type==='tip')return revealRow('book',`<div class="reveal-tag">Today's edge</div>
    <blockquote class="reveal-quote">${esc(TIPS[(p.index||0)%TIPS.length])}</blockquote>${REVEAL_NEXT}`);
  if(p.type==='points')return revealRow('crown',`<div class="reveal-tag">Bonus drop</div>
    <div class="reveal-big mono">+${p.amount}</div>
    <p class="reveal-t">loyalty points added to your account</p>${REVEAL_NEXT}`);
  if(p.type==='freeze')return revealRow('shield',`<div class="reveal-tag">Streak insurance</div>
    <h3 class="reveal-h">+1 Streak Freeze</h3>
    <p class="reveal-t">If you miss a day, your streak survives automatically.</p>${REVEAL_NEXT}`);
  return revealRow('gift',`<div class="reveal-tag">Rare drop · -${p.pct}% next challenge</div>
    <div class="reveal-code"><code>${esc(p.code)}</code>
      <button class="copy" onclick="event.stopPropagation();copyVal(this,'${jsq(p.code)}')" title="Copy">${ICO.copy}</button></div>
    <p class="reveal-t">Valid 48h, personal, works only on your account.</p>`);
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
/* ---------- Free Trade Case: jedna skrzynka na tradera, trzy karty ----------
   Wynik losuje serwer (POST /api/me/case/open) — karta, w ktora trader kliknie,
   niczego nie zmienia. Dwie pozostale karty dostaja po odslonie inne nagrody
   z puli, wylacznie dla wygladu. Stan z GET /api/me/case trzyma CASE. */
let CASE=null;
async function loadCase(force){
  if(CASE&&!force)return CASE;
  try{CASE=await api('/api/me/case')}catch(e){CASE=CASE||null}
  return CASE;
}
/* Deep link /portal?case=1 (popup na /trade): flaga w localStorage przezywa
   rejestracje, bramke weryfikacji maila i link weryfikacyjny otwarty w nowej
   karcie. Wygasa po 7 dniach, zeby stary link nie wyskakiwal miesiac pozniej. */
const CASE_PENDING='pf_case_pending';
function caseLinkSeen(){
  try{if(new URLSearchParams(location.search).get('case')==='1')localStorage.setItem(CASE_PENDING,String(Date.now()))}catch(e){}
}
function casePending(){
  try{const t=+localStorage.getItem(CASE_PENDING)||0;
    if(t&&Date.now()-t<7*864e5)return true;
    if(t)localStorage.removeItem(CASE_PENDING);
  }catch(e){}
  return false;
}
async function maybeOpenCaseFromLink(){
  if(!casePending())return;
  try{localStorage.removeItem(CASE_PENDING)}catch(e){}
  const q=new URLSearchParams(location.search);
  if(q.has('case')){q.delete('case');history.replaceState(null,'','/portal'+(q.toString()?'?'+q:''))}
  const c=await loadCase(true);
  if(c&&(c.available||c.opened))openCase();
}
/* Duza wartosc na karcie z samej etykiety — pozostale karty niosa tylko ja. */
function caseBig(label){
  const s=String(label||'');
  let m=s.match(/(\d+(?:\.\d+)?)\s*%/);if(m)return {big:m[1]+'%',tag:'Discount code'};
  m=s.match(/\$\s?\d+(?:[.,]\d+)?\s?[KkMm]\b/);if(m)return {big:m[0].replace(/\s/g,'').toUpperCase(),tag:'Free challenge'};
  m=s.match(/([\d,]+)\s*(?:bonus\s*)?points?/i);if(m)return {big:'+'+m[1],tag:'Bonus points'};
  return {big:'★',tag:'Prize'};
}
function caseFaceHtml(label,win){
  const b=caseBig(label);
  return `<span class="cc-tag">${win?'Your prize':esc(b.tag)}</span>
    <span class="cc-big">${esc(b.big)}</span>
    <span class="cc-lbl">${esc(label)}</span>`;
}
function caseBackHtml(i){
  return `<span class="cc-emblem"><img src="/static/img/logo.png" alt=""></span>
    <span class="cc-num">${i+1}</span>
    <span class="cc-hint">Tap to reveal</span>`;
}
function caseBannerHtml(){
  if(!CASE||!CASE.available)return '';
  return `<div class="gradient-banner case-banner" id="case-banner">
    <span class="gb-tag">${ICO.gift} Free Trade Case</span><span class="gb-sep"></span>
    <span class="gb-txt">One free case per trader. Pick a card and win a discount code or a <b>free challenge</b>.</span>
    <button class="gb-btn" onclick="openCase()">Open your case ›</button></div>`;
}
function caseExpires(iso){
  return iso?dutc(iso).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'';
}
/* Ten sam opis wygranej w modalu i na karcie w Rewards. */
function caseWinHtml(o,inModal){
  if(o.type==='code'){
    const zywy=o.code_status==='active';
    return `<div class="case-win-t">You won <b>${esc(o.label)}</b></div>
      ${o.code?`<div class="case-code${zywy?'':' spent'}"><code>${esc(o.code)}</code>
        ${zywy?`<button class="btn-o sm" onclick="copyVal(this,'${jsq(o.code)}')">Copy</button>`
          :`<span class="status ${o.code_status==='used'?'paid':'failed'}"><span class="dot"></span>${esc(o.code_status)}</span>`}</div>`:''}
      <p class="case-note">${zywy?`Personal one-time code, works only on your account. Valid until ${esc(caseExpires(o.expires_at))}.`
        :o.code_status==='used'?'You already used this code at checkout.':'This code has expired.'}</p>
      ${zywy?`<button class="btn-p${inModal?' lg':' sm'}" onclick="useCaseCode('${jsq(o.code)}')">Use at checkout</button>`:''}`;
  }
  if(o.type==='account'){
    return `<div class="case-win-t">Your challenge is ready</div>
      <p class="case-note"><b>${esc(o.label)}</b>. ${o.account_status==='provisioning'
        ?'Your MT5 account is being set up, credentials arrive in your inbox.'
        :'It is under Challenges, and the credentials are in your inbox.'}</p>
      ${o.account_id?`<button class="btn-p${inModal?' lg':' sm'}" onclick="closeCase();openAcc(${+o.account_id})">Open my challenge</button>`:''}`;
  }
  return `<div class="case-win-t">You won <b>+${fmt0(o.points||0)} loyalty points</b></div>
    <p class="case-note">They are already on your balance. Trade them for a discount code on the Loyalty page.</p>
    <button class="btn-p${inModal?' lg':' sm'}" onclick="closeCase();go('loyalty')">See my points</button>`;
}
function caseOddsHtml(){
  const o=(CASE&&CASE.odds)||[];
  return `<ul class="case-odds-list">${o.map(x=>`<li><span>${esc(x.label)}</span><b class="mono">${x.pct}%</b></li>`).join('')}</ul>`;
}
function toggleCaseOdds(btn,id){
  const box=$(id);if(!box)return;
  const open=box.classList.toggle('hidden')===false;
  btn.setAttribute('aria-expanded',open?'true':'false');
}
/* „Co jest w skrzynce" z puli na zywo: admin moze zmienic nagrody w panelu. */
function caseTeaser(){
  const o=(CASE&&CASE.odds)||[];
  const pct=Math.max(0,...o.filter(x=>x.type==='code').map(x=>{const m=String(x.label).match(/(\d+(?:\.\d+)?)\s*%/);return m?+m[1]:0}));
  const czesci=[];
  if(o.some(x=>x.type==='code'))czesci.push(pct?`a discount code of up to ${pct}%`:'a discount code');
  if(o.some(x=>x.type==='points'))czesci.push('bonus points');
  if(o.some(x=>x.type==='account'))czesci.push('a free challenge account');
  return czesci.length>1?czesci.slice(0,-1).join(', ')+' or '+czesci[czesci.length-1]:(czesci[0]||'a prize');
}
/* Karta w Rewards: zaproszenie przed otwarciem, wynik po nim. */
function caseCardHtml(){
  if(!CASE||!(CASE.available||CASE.opened))return '';
  const art=`<div class="case-art" aria-hidden="true"><i></i><i></i><i></i></div>`;
  if(CASE.opened){
    return `<div class="panel case-panel opened" id="case-panel">
      <div class="case-row">${art}
        <div class="case-body"><div class="case-eyebrow">Free Trade Case · opened ${esc(caseExpires(CASE.opened.opened_at))}</div>
          ${caseWinHtml(CASE.opened,false)}</div></div></div>`;
  }
  return `<div class="panel case-panel" id="case-panel">
    <div class="case-row">${art}
      <div class="case-body"><div class="case-eyebrow">Free Trade Case</div>
        <h3>Your free case is waiting</h3>
        <p class="case-note">Pick one of three cards. Every card pays: ${esc(caseTeaser())}.</p>
        <div class="case-acts"><button class="btn-p sm" onclick="openCase()">Open your case</button>
          <button type="button" class="linklike" aria-expanded="false" aria-controls="case-odds-rw"
            onclick="toggleCaseOdds(this,'case-odds-rw')">Odds</button></div>
        <div class="case-odds hidden" id="case-odds-rw">${caseOddsHtml()}</div>
        <p class="case-fine">No purchase necessary. One case per trader.</p></div></div></div>`;
}
function closeCase(){$('case-modal')?.remove()}
function openCase(){
  if(!CASE||$('case-modal'))return;
  const w=document.createElement('div');w.id='case-modal';w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)closeCase()};
  const cards=[0,1,2].map(i=>`<button type="button" class="case-card" data-i="${i}" onclick="pickCase(${i})"
      aria-label="Card ${i+1} of 3, face down">
      <span class="cc-inner"><span class="cc-face cc-back">${caseBackHtml(i)}</span><span class="cc-face cc-front"></span></span>
    </button>`).join('');
  w.innerHTML=`<div class="modal case-modal" role="dialog" aria-modal="true" aria-labelledby="case-t" onclick="event.stopPropagation()">
    <div class="modal-head"><div><div class="case-eyebrow">Free Trade Case</div><h3 id="case-t">Your Free Trade Case</h3></div>
      <button class="icon-btn" onclick="closeCase()" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="case-sub" id="case-sub">Pick a card to reveal your prize</p>
    <div class="case-cards" id="case-cards" role="group" aria-label="Three face-down cards">${cards}</div>
    <div class="case-result" id="case-result" aria-live="polite"></div>
    <div class="case-foot"><button type="button" class="linklike" aria-expanded="false" aria-controls="case-odds-m"
        onclick="toggleCaseOdds(this,'case-odds-m')">Odds</button><span>No purchase necessary. One case per trader.</span></div>
    <div class="case-odds hidden" id="case-odds-m">${caseOddsHtml()}</div>
  </div>`;
  document.body.appendChild(w);
  if(CASE.opened)caseShow(CASE.opened,false);
  else setTimeout(()=>w.querySelector('.case-card')?.focus(),60);
}
/* Odslona: wybrana karta od razu, pozostale chwile pozniej. `anim=false` =
   skrzynka byla juz otwarta (refresh, drugie urzadzenie) — stan koncowy bez gry. */
function caseShow(o,anim){
  const cards=[...document.querySelectorAll('#case-cards .case-card')];
  if(!cards.length)return;
  const win=Math.min(2,Math.max(0,+o.card||0));
  const inne=(o.others||[]).slice(0,2);
  const reszta=cards.filter((_,i)=>i!==win);
  cards.forEach(c=>{c.disabled=true;c.onclick=null});
  const odkryj=(c,label,wygrana)=>{
    c.querySelector('.cc-front').innerHTML=caseFaceHtml(label,wygrana);
    c.classList.add('flipped',wygrana?'win':'other');
    c.setAttribute('aria-label',(wygrana?'Your prize: ':'Also in the case: ')+label);
  };
  const wynik=()=>{
    $('case-sub').textContent=o.type==='account'?'Congratulations! A free challenge is yours.':'Here is your prize';
    $('case-result').innerHTML=`<div class="case-win">${caseWinHtml(o,true)}</div>`;
  };
  if(!anim){
    odkryj(cards[win],o.label,true);reszta.forEach((c,i)=>odkryj(c,inne[i]||'',false));
    cards.forEach(c=>c.classList.add('no-anim'));wynik();return;
  }
  odkryj(cards[win],o.label,true);
  const krok=RFX.reduced?0:1;
  setTimeout(()=>{
    if(!RFX.reduced)RFX.burstFrom(cards[win],{count:o.type==='account'?80:60,palette:RFX.GOLD});
    wynik();
  },560*krok);
  setTimeout(()=>reszta.forEach((c,i)=>odkryj(c,inne[i]||'',false)),1150*krok);
}
async function pickCase(i){
  const box=$('case-cards');
  if(!box||box.dataset.busy)return;
  box.dataset.busy='1';
  const cards=[...box.querySelectorAll('.case-card')];
  cards.forEach((c,k)=>{c.disabled=true;c.classList.toggle('picked',k===i)});
  $('case-sub').textContent='Revealing…';
  try{
    const o=await api('/api/me/case/open',{method:'POST',body:JSON.stringify({card:i})});
    CASE={...(CASE||{}),available:false,reason:'opened',opened:o};
    /* Pod modalem: baner znika, karta w Rewards od razu pokazuje wynik. */
    document.getElementById('case-banner')?.remove();
    const pan=$('case-panel');if(pan)pan.outerHTML=caseCardHtml();
    if(!o.already&&o.type==='points')ME.bonus_points=(ME.bonus_points||0)+(o.points||0);
    caseShow(o,!o.already);
  }catch(e){
    toast('Error: '+e.message,'err');
    cards.forEach(c=>{c.disabled=false;c.classList.remove('picked')});
    $('case-sub').textContent='Pick a card to reveal your prize';
    delete box.dataset.busy;
  }
}
/* „Use at checkout": istniejacy deep link ?coupon= wypelnia pole kodu w oknie
   zakupu — ten sam, ktorym przychodzi kod z cennika. */
function useCaseCode(code){
  history.replaceState(null,'','/portal?coupon='+encodeURIComponent(code));
  closeCase();go('store');
  toast('Pick a challenge. Your code is already filled in at checkout.','ok',7000);
}

/* Prowizja afiliacyjna -> store credit albo vUSDT (próg claimu pilnuje serwer). */
async function claimAffiliate(btn,method){
  const m=method==='vusdt'?'vusdt':'store_credit';
  await busy(btn,'Claiming…',async()=>{
    try{
      const r=await api('/api/me/affiliate/claim',{method:'POST',body:JSON.stringify({method:m})});
      ME=await api('/api/auth/me');
      if(r.method==='vusdt')
        toast(`✅ $${fmt(r.claimed_usd)} claimed as vUSDT.\nQueued to your saved Polygon wallet.`,'ok',7000);
      else
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

/* ---------- dzwonek tradera: panel powiadomień ----------
   Ten sam panel co u admina (static/js/np-kit.js + notifications.css):
   zakładki, gesty, push na tym urządzeniu, kółko zębate z ustawieniami.
   Klik w pozycję prowadzi prosto do widoku (payout, konto, bilet…) przez
   `notifGo`; przeczytane/usuń — tylko gestem przesunięcia i hurtem w Edit. */
const UN={tab:'all',open:false,nav:'list',edit:false,picked:new Set(),fresh:new Set(),
  hiding:new Set(),gone:new Set(),known:null,loaded:false,dirty:false,items:[],unread:0};
try{const t=localStorage.getItem('pf_notif_tab');if(['all','trading','payouts','updates'].includes(t))UN.tab=t}catch(_){}
const UN_TABS=[['all','All'],['trading','Trading'],['payouts','Payouts'],['updates','Updates']];
const UN_KIND={
  phase_passed:['trophy','green','Phase passed'],account_funded:['badge','acc','Funded'],
  account_scaled:['trend','purple','Scaled up'],breached:['octagon','red','Breach'],
  limit_warning:['gauge','gold','Limit warning'],target_50:['target','green','Profit target'],
  target_75:['target','green','Profit target'],min_days_met:['calCheck','blue','Trading days'],
  payout_ready:['wallet','green','Payout ready'],payout_requested:['wallet','purple','Payout requested'],
  payout_approved:['checkCircle','green','Payout approved'],payout_rejected:['xCircle','red','Payout rejected'],
  kyc_approved:['shield','blue','Verified'],kyc_rejected:['shieldX','red','Verification'],
  ticket_reply:['chat','acc','Support'],credits_granted:['gift','gold','Credit'],
  daily_recap:['chart','acc','Daily recap'],weekly_review:['chart','purple','Weekly review'],
  checkout_recovery:['bag','gold','Checkout'],flash_offer:['tag','purple','Offer'],
  upsell_scale:['trend','acc','Scale up'],
};
const unKind=i=>UN_KIND[i.event]||['bell','acc','Update'];
const unPref=()=>{try{return localStorage.getItem('pf_notif_seen')!=='0'}catch(_){return true}};
const unVisible=()=>UN.items.filter(i=>!UN.hiding.has(String(i.id))&&(UN.tab==='all'||i.cat===UN.tab));
const unUnread=tab=>UN.items.filter(i=>!i.read&&!UN.hiding.has(String(i.id))&&(tab==='all'||i.cat===tab)).length;
const unItem=id=>UN.items.find(i=>String(i.id)===String(id));

async function refreshNotif(){
  try{
    const r=await api('/api/me/notifications?limit=50');
    const items=(r.items||[]).filter(i=>!UN.gone.has(String(i.id)))
      .map(i=>({...i,ts:i.created_at||new Date().toISOString(),cat:i.cat||'updates'}))
      .sort((a,b)=>a.ts<b.ts?1:a.ts>b.ts?-1:0);
    // „Nowe" tylko, gdy nowsze od wszystkiego, co już było — starsze, które
    // dosunęły się po usunięciu, nie wjeżdżają jak świeże powiadomienie.
    if(UN.known){
      const top=UN.items.reduce((m,i)=>i.ts>m?i.ts:m,'');
      items.forEach(i=>{if(!UN.known.has(i.id)&&i.ts>top)UN.fresh.add(String(i.id))});
    }
    UN.known=new Set(items.map(i=>i.id));
    UN.items=items;UN.loaded=true;
    unBadge();
    if(UN.open){if(npSwiping())UN.dirty=true;else unRenderList();unSeg(true)}
  }catch(e){}
}
function unBadge(){
  const b=$('bell-badge');if(!b)return;
  const n=unUnread('all');
  b.textContent=n>9?'9+':n;
  b.classList.toggle('hidden',!n);
  npBumpNum(b,n);
}
function unEnsure(){
  if($('np'))return;
  document.body.insertAdjacentHTML('beforeend',`
  <div class="np-scrim" id="np-scrim"></div>
  <section class="np" id="np" role="dialog" aria-modal="true" aria-label="Notifications" inert>
    <div class="np-body">
      <div class="np-pg np-list" id="np-list">
        <div class="np-grab" data-np-drag></div>
        <header class="np-bar" data-np-drag>
          <div class="np-bar-l"><button type="button" class="np-tbtn" data-un="edit">Edit</button></div>
          <div class="np-bar-t">Notifications</div>
          <div class="np-bar-r">
            <button type="button" class="np-ibtn np-gear" data-un="settings" aria-label="Notification settings">${npI('gear')}</button>
            <button type="button" class="np-ibtn" data-un="close" aria-label="Close">${npI('x',2.2)}</button>
            <span class="np-editacts">
              <button type="button" class="np-act" data-un="bulkread" disabled>Mark read</button>
              <button type="button" class="np-act danger" data-un="bulkdel" disabled>Delete</button>
            </span>
          </div>
        </header>
        <div class="np-scroll" id="np-scroll">
          <div class="np-lt"><div><h2>Notifications</h2><small id="np-sub"></small></div>
            <button type="button" class="np-tbtn np-noedit" data-un="allread">Mark all read</button>
            <button type="button" class="np-tbtn np-onedit" data-un="selall">Select all</button></div>
          <div class="np-pr" data-push="${PUSH.st}" data-un="pushrow">${npPushInner()}</div>
          <div class="np-segw"><div class="np-seg" id="np-seg" role="tablist" aria-label="Filter"></div></div>
          <div class="np-items" id="np-items" role="list"></div>
        </div>
      </div>
      <div class="np-pg np-settings" id="np-settings" inert></div>
    </div>
  </section>`);
  const seg=$('np-seg');
  seg.style.setProperty('--n',UN_TABS.length);
  seg.innerHTML='<span class="np-pill"></span>'+UN_TABS.map(([k,l])=>
    `<button type="button" role="tab" data-un-tab="${k}"><span>${l}</span><b class="np-n zero"></b></button>`).join('');
  npPaintPush();unSeg(false);unNav('list');
}
function toggleNotif(){unSetOpen(!UN.open)}
function unSetOpen(open){
  unEnsure();
  UN.open=open;
  const np=$('np');
  np.classList.toggle('open',open);
  $('np-scrim').classList.toggle('open',open);
  np.inert=!open;
  document.documentElement.classList.toggle('np-lock',open);
  if(open){np.style.transform='';refreshPush();unRenderList({anim:'f'});unSeg(false);refreshNotif()}
  else{if(UN.edit)unSetEdit(false);unObserve(false);if(UN.nav!=='list')setTimeout(()=>{if(!UN.open)unNav('list')},400)}
}
function unSeg(bump){
  const seg=$('np-seg');if(!seg)return;
  seg.style.setProperty('--i',Math.max(0,UN_TABS.findIndex(t=>t[0]===UN.tab)));
  npQ('button',seg).forEach(b=>{
    const on=b.dataset.unTab===UN.tab;
    b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;
    const el=b.querySelector('.np-n'),n=b.dataset.unTab==='all'?0:unUnread(b.dataset.unTab);
    el.textContent=n>99?'99+':n;el.classList.toggle('zero',!n);
    if(bump)npBumpNum(el,n);else el.dataset.n=n;
  });
  const all=unUnread('all'),sub=$('np-sub');
  if(sub)sub.textContent=all?`${all} unread`:'All caught up';
  const ar=document.querySelector('[data-un="allread"]');if(ar)ar.disabled=!unUnread(UN.tab);
  const ed=document.querySelector('[data-un="edit"]');if(ed)ed.disabled=!unVisible().length&&!UN.edit;
}
function unSetTab(tab){
  if(tab===UN.tab)return;
  const dir=UN_TABS.findIndex(t=>t[0]===tab)>UN_TABS.findIndex(t=>t[0]===UN.tab)?'r':'l';
  UN.tab=tab;UN.picked.clear();
  try{localStorage.setItem('pf_notif_tab',tab)}catch(_){}
  unSeg(false);
  const sc=$('np-scroll'),segw=document.querySelector('.np-segw');
  if(sc&&segw&&sc.scrollTop>segw.offsetTop)sc.scrollTop=segw.offsetTop;
  unRenderList({anim:dir});
  if(UN.edit)unEditbar();
}
function unRow(i,sep){
  const k=unKind(i);
  return `<div class="np-row${i.read?'':' unread'}${UN.picked.has(String(i.id))?' picked':''}" data-id="${i.id}" role="listitem">
    <div class="np-ra" aria-hidden="true">
      <button type="button" class="np-ra-l" data-un-act="read" tabindex="-1"><span class="if-unread">${npI('check',2.4)}</span><span class="if-read">${npI('dot')}</span><span class="if-unread">Read</span><span class="if-read">Unread</span></button>
      <button type="button" class="np-ra-r" data-un-act="del" tabindex="-1"><span>Delete</span>${npI('trash')}</button></div>
    <div class="np-rb${sep?' np-sepd':''}" tabindex="0" data-un-open="${i.id}">
      <span class="np-ck" aria-hidden="true">${npI('check',3)}</span><span class="np-dot" aria-hidden="true"></span>
      <span class="np-ico" style="--k:var(--${k[1]})">${npI(k[0])}</span>
      <div class="np-txt"><div class="np-l1"><span class="np-who">${esc(i.title||k[2])}</span><time data-ts="${esc(i.ts)}">${esc(npRel(i.ts))}</time></div>
        ${i.body?`<div class="np-l2">${esc(i.body)}</div>`:''}</div>
    </div></div>`;
}
function unRenderList(o={}){
  const box=$('np-items');if(!box)return;
  if(!UN.loaded){
    box.innerHTML=`<div class="np-sk">${'<div class="np-sk-r"><span class="np-sk-i"></span><span class="np-sk-t"><span class="np-sk-l" style="display:block;width:55%"></span><span class="np-sk-l" style="display:block;width:85%"></span></span></div>'.repeat(5)}</div>`;
    return;
  }
  const items=unVisible();
  if(!items.length){
    box.innerHTML=`<div class="np-empty"><span class="np-e-ic">${npI('checkCircle')}</span><b>You're all caught up</b><span>${
      UN.tab==='payouts'?'Payout requests and approvals land here.'
      :UN.tab==='trading'?'Phase passes, targets and limit warnings land here.'
      :'Pass a phase, request a payout or get a support reply — it lands here.'}</span></div>`;
  }else{
    const dni=[];let cur=null;
    for(const i of items){const lab=npDayLabel(i.ts);if(!cur||cur.lab!==lab)dni.push(cur={lab,items:[]});cur.items.push(i)}
    box.innerHTML=dni.map(d=>`<section class="np-day"><h3 class="np-day-h">${d.lab}</h3><div class="np-grp">${
      d.items.map((i,n)=>unRow(i,n>0)).join('')}</div></section>`).join('')
      +`<button type="button" class="np-clr" data-un="clear">${UN.tab==='all'?'Delete all':'Delete all in '+(UN_TABS.find(t=>t[0]===UN.tab)||[])[1]}</button>`;
  }
  if(o.anim&&!npRM()){box.classList.remove('in-l','in-r','in-f');void box.offsetWidth;box.classList.add('in-'+o.anim)}
  UN.fresh.forEach(id=>{const r=box.querySelector(`.np-row[data-id="${CSS.escape(id)}"]`);if(r)npGrow(r)});
  UN.fresh.clear();
  unObserve(true);
}
function unPaintRead(){
  npQ('#np-items .np-row[data-id]').forEach(r=>{const i=unItem(r.dataset.id);r.classList.toggle('unread',!!i&&!i.read)});
}
function unMark(ids,read=true){
  const zm=UN.items.filter(i=>ids.map(String).includes(String(i.id))&&i.read!==read);
  if(!zm.length)return;
  zm.forEach(i=>i.read=read);
  unPaintRead();unSeg(false);unBadge();
  api('/api/me/notifications/mark',{method:'POST',body:JSON.stringify({ids:zm.map(i=>+i.id),read})}).catch(()=>{});
}
function unDelete(ids){
  ids=ids.map(String).filter(id=>!UN.hiding.has(id));
  if(!ids.length)return;
  npCloseSwipes();
  const set=new Set(ids);
  npQ('#np-items .np-row[data-id]').filter(r=>set.has(r.dataset.id)).forEach(npCollapse);
  // Ukryte OD RAZU: kolejne usunięcie w trakcie animacji przerysowuje listę
  // i dawniej wskrzeszało zwijany wiersz. Z UN.hiding schodzą dopiero po
  // potwierdzonym zapisie; nieudany zapis przywraca je jawnie.
  ids.forEach(id=>{UN.hiding.add(id);UN.picked.delete(id)});
  unSeg(false);unBadge();if(UN.edit)unEditbar();
  setTimeout(()=>{if(!npSwiping())unRenderList()},npRM()?0:300);
  const przywroc=()=>{ids.forEach(id=>UN.hiding.delete(id));unRenderList();unSeg(true);unBadge()};
  npUndoToast(ids.length===1?'Notification deleted':`${ids.length} notifications deleted`,
    ()=>{
      api('/api/me/notifications/delete',{method:'POST',keepalive:true,body:JSON.stringify({ids:ids.map(Number)})})
        .then(()=>{ids.forEach(id=>{UN.gone.add(id);UN.hiding.delete(id)});UN.items=UN.items.filter(i=>!set.has(String(i.id)))})
        .catch(()=>{przywroc();toast("Couldn't delete — the notifications are back.",'err')});
    },
    ()=>{ids.forEach(id=>UN.fresh.add(id));przywroc()});
}
function unRowAct(act,row){
  const ids=npIds(row);if(!ids.length)return;
  if(act==='del')return unDelete(ids);
  if(act==='read'){unMark(ids,ids.some(id=>{const i=unItem(id);return i&&!i.read}));npCloseSwipes()}
}
function unSetEdit(on){
  UN.edit=on;UN.picked.clear();
  $('np').classList.toggle('editing',on);
  const b=document.querySelector('[data-un="edit"]');if(b)b.textContent=on?'Done':'Edit';
  npQ('#np-items .picked').forEach(r=>r.classList.remove('picked'));
  npCloseSwipes();unEditbar();
}
function unEditbar(){
  const n=UN.picked.size,vis=unVisible();
  const del=document.querySelector('[data-un="bulkdel"]'),rd=document.querySelector('[data-un="bulkread"]'),
        all=document.querySelector('[data-un="selall"]');
  if(!del)return;
  del.textContent=n?`Delete ${n}`:'Delete';del.disabled=!n;rd.disabled=!n;
  rd.textContent=n&&![...UN.picked].some(id=>{const i=unItem(id);return i&&!i.read})?'Mark unread':'Mark read';
  all.textContent=vis.length&&vis.every(i=>UN.picked.has(String(i.id)))?'Deselect all':'Select all';
}
function unNav(nav){
  UN.nav=nav;
  const np=$('np');if(!np)return;
  np.classList.toggle('nav-settings',nav==='settings');
  if(nav==='settings')unRenderSettings();
  $('np-list').inert=nav==='settings';
  $('np-settings').inert=nav!=='settings';
}
/* Kategorie = te same przełączniki co w Settings (notify_*): gaszą mail,
   push i wpis w dzwonku naraz — tak działa backend (`notify._PREF_BY_EVENT`). */
const UN_CATS=[['trading','Trading','Phase passes, funding, targets, limit warnings, daily recap and weekly review','trophy','green'],
  ['payouts','Payouts','Requested, approved and sent','wallet','purple'],
  ['updates','Account','KYC results and store credit','shield','blue'],
  /* Support ma własny przełącznik: mail + push o odpowiedzi zespołu w czacie.
     W portalu odpowiedź pokazuje plakietka na dymku czatu, nie dzwonek. */
  ['support','Support chat','Replies from our team in the chat (e-mail and push)','chat','acc'],
  ['marketing','Offers & reminders','Personal offers and reminders about an unfinished checkout','chart','acc']];
function unCatsHtml(){
  const n=(ME&&ME.notify)||{};
  return `<div class="np-grp flat">${UN_CATS.map(([k,t,d,ic,c])=>`<div class="np-cell"><span class="np-ci" style="--k:var(--${c})">${npI(ic,2)}</span>
    <span class="np-ct"><b>${t}</b><span>${d}</span></span>
    <button type="button" class="np-sw" role="switch" data-un-cat="${k}" aria-checked="${!!n[k]}" aria-label="${t}"><i></i></button></div>`).join('')}</div>`;
}
function unRenderSettings(){
  const box=$('np-settings');if(!box)return;
  box.innerHTML=`
    <header class="np-bar"><div class="np-bar-l"><button type="button" class="np-tbtn np-back" data-un="back">${npI('chevL',2.4)}<span>Back</span></button></div>
      <div class="np-bar-t">Notification settings</div><div class="np-bar-r"></div></header>
    <div class="np-scroll"><div class="np-set">
      <h4 class="np-sec-h">This device</h4>
      <div class="np-grp flat"><div class="np-pr" data-push="${PUSH.st}" style="margin:0;border-radius:0;box-shadow:none">${npPushInner()}</div>
        <div data-np-push-help></div></div>
      <p class="np-sec-f">Push is per device. Switch it on on every phone and computer you want alerts on.</p>
      <h4 class="np-sec-h">What you hear about</h4>
      ${unCatsHtml()}
      <p class="np-sec-f">Turning a category off stops its email, push and bell notifications. Essential emails (verification, MT5 credentials) always arrive.</p>
      <h4 class="np-sec-h">This list</h4>
      <div class="np-grp flat"><div class="np-cell"><span class="np-ci" style="--k:var(--blue)">${npI('eye',2)}</span>
        <span class="np-ct"><b>Mark as read when seen</b><span>After two seconds on screen</span></span>
        <button type="button" class="np-sw" role="switch" data-un="seen" aria-checked="${unPref()}" aria-label="Mark as read when seen"><i></i></button></div></div>
      <div class="np-grp flat" style="margin-top:28px"><button type="button" class="np-cell danger" data-un="clearall"><b>Delete all notifications</b></button></div>
    </div></div>`;
  npPaintPush();
}
async function unSetCat(k,on){
  const maluj=v=>npQ(`[data-un-cat="${k}"]`).forEach(s=>s.setAttribute('aria-checked',String(v)));
  maluj(on);
  try{const r=await api('/api/me',{method:'PATCH',body:JSON.stringify({['notify_'+k]:on})});ME.notify=r.notify;
    /* support siedzi w ui_prefs — lokalna kopia musi go znać, inaczej następny
       zapis preferencji UI (cały obiekt) przywróciłby stary stan. Ten SAM obiekt
       (patrz setUiPref), więc zmiana w miejscu. */
    if(k==='support'){ME.ui_prefs=ME.ui_prefs||{};
      if(on)delete ME.ui_prefs.notify_support;else ME.ui_prefs.notify_support=false}}
  catch(e){maluj(!on);toast('Error: '+e.message,'err')}
}
function unObserve(on){
  npObserveSeen(on&&UN.open&&unPref()?$('np-scroll'):null,on?npQ('#np-items .np-row.unread'):[],
    ids=>unMark(ids,true),()=>UN.open&&UN.nav!=='settings'&&!UN.edit);
}
/* Klik = prosto do widoku z adresu powiadomienia (notifGo trzyma nawigację
   w SPA); przeczytane od razu. */
function unOpen(id){
  const i=unItem(id);if(!i)return;
  unMark([id],true);
  const href=i.url||'/portal';
  unSetOpen(false);
  if(notifGo({getAttribute:()=>href}))location.href=href;
}
addEventListener('click',e=>{
  const t=e.target;
  if(!t.closest)return;
  if(t.id==='np-scrim')return unSetOpen(false);
  const cat=t.closest('[data-un-cat]');
  if(cat)return unSetCat(cat.dataset.unCat,cat.getAttribute('aria-checked')!=='true');
  const tab=t.closest('[data-un-tab]');
  if(tab)return unSetTab(tab.dataset.unTab);
  const push=t.closest('[data-np="push"]');
  if(push)return npTogglePush();
  const a=t.closest('[data-un]');
  if(a){
    switch(a.dataset.un){
      case 'pushrow':if(['ios','denied','nokey','unsupported'].includes(PUSH.st))unNav('settings');return;
      case 'close':return unSetOpen(false);
      case 'settings':return unNav('settings');
      case 'back':return unNav('list');
      case 'edit':return unSetEdit(!UN.edit);
      case 'allread':return unMark(unVisible().filter(x=>!x.read).map(x=>x.id),true);
      case 'selall':{
        const vis=unVisible(),all=vis.every(x=>UN.picked.has(String(x.id)));
        vis.forEach(x=>all?UN.picked.delete(String(x.id)):UN.picked.add(String(x.id)));
        npQ('#np-items .np-row[data-id]').forEach(r=>r.classList.toggle('picked',UN.picked.has(r.dataset.id)));
        return unEditbar();
      }
      case 'bulkread':{const ids=[...UN.picked];unMark(ids,ids.some(id=>{const x=unItem(id);return x&&!x.read}));return unEditbar()}
      case 'bulkdel':return unDelete([...UN.picked]);
      case 'clear':return npArm(a,unVisible().length,()=>unDelete(unVisible().map(x=>x.id)));
      case 'clearall':{const ids=UN.items.filter(x=>!UN.hiding.has(String(x.id))).map(x=>x.id);
        return npArm(a,ids.length,()=>{unNav('list');unDelete(ids)})}
      case 'seen':{
        const on=a.getAttribute('aria-checked')!=='true';a.setAttribute('aria-checked',String(on));
        try{localStorage.setItem('pf_notif_seen',on?'1':'0')}catch(_){}
        return unObserve(true);
      }
    }
    return;
  }
  const act=t.closest('[data-un-act]');
  if(act)return unRowAct(act.dataset.unAct,act.closest('.np-row'));
  const rb=t.closest('#np-items .np-rb');
  if(rb){
    if(+rb.dataset.off)return npSlide(rb,0);
    if(UN.edit){
      const id=rb.closest('.np-row').dataset.id;
      UN.picked.has(id)?UN.picked.delete(id):UN.picked.add(id);
      rb.closest('.np-row').classList.toggle('picked',UN.picked.has(id));
      return unEditbar();
    }
    return unOpen(rb.dataset.unOpen);
  }
});
addEventListener('keydown',e=>{
  if(!UN.open)return;
  if(e.key==='Escape'){if(UN.edit)return unSetEdit(false);if(UN.nav==='settings')return unNav('list');return unSetOpen(false)}
  const el=document.activeElement,rb=el&&el.closest&&el.closest('#np-items .np-rb');
  if(!rb||UN.edit)return;
  if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();return unRowAct('del',rb.closest('.np-row'))}
  if(e.key==='Enter'||e.key===' '){e.preventDefault();return rb.click()}
});
npGestures({items:'#np-items',panel:'#np',list:'#np-list',editing:()=>UN.edit,open:()=>UN.open,
  onAct:(act,row)=>unRowAct(act,row),onDismiss:()=>unSetOpen(false),
  onTabSwipe:dir=>{
    if(UN.nav!=='list')return false;
    const nast=UN_TABS[UN_TABS.findIndex(t=>t[0]===UN.tab)+dir];
    if(!nast)return false;
    unSetTab(nast[0]);return true;
  },
  onEnd:()=>{if(UN.dirty){UN.dirty=false;setTimeout(()=>unRenderList(),350)}}});
setInterval(()=>{if(UN.open)npQ('#np-items time[data-ts]').forEach(t=>t.textContent=npRel(t.dataset.ts))},30000);

/* Bell row: switch the SPA view instead of a full navigation; with no
   matching view fall back to the row's URL. */
function notifGo(a){
  const href=a.getAttribute('href');
  let up=false;
  try{up=new URL(href,location.origin).searchParams.get('upsell')==='1'}catch(_){}
  if(up)window._upsellJump=true;
  /* `false` zatrzymuje przejscie linkiem: nawigacja zostaje w SPA. Gdy celu nie
     umiemy obsluzyc, puszczamy przegladarke i wchodzi zwykle przeladowanie. */
  if(goNav(navFromUrl(href))){$('notif-panel')?.classList.add('hidden');return false}
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
function boardTab(k){setUiPref('board_tab',k==='profit'?null:k);VIEWS.board()}

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

/* ===== Flash sale: baner z odliczaniem + przekreślone ceny =====
   Oferty przychodzą z /api/me/offers (globalne + imienne zalogowanego); ceny
   po rabacie liczy SERWER w /api/products (offer_price_usd) — ta sama funkcja,
   która wycenia checkout, więc kafelek nigdy nie obieca innej kwoty niż kasa. */
let OFFERS=[];
async function loadOffers(){
  try{const d=await api('/api/me/offers');OFFERS=(d&&d.offers)||[]}catch(e){OFFERS=[]}
}
function flashBannerHtml(){
  if(!OFFERS.length)return'';
  const o=OFFERS.reduce((b,x)=>x.discount_pct>b.discount_pct?x:b);
  const pct=o.discount_pct%1?o.discount_pct.toFixed(2):o.discount_pct;
  return `<div class="gradient-banner">
    <span class="gb-tag">${ICO.spark} Flash sale</span><span class="gb-sep"></span>
    <span class="gb-txt">${esc(o.title||'Limited-time offer')} — <b>${pct}% off</b>.
      Ends in <b id="offer-cd" data-ends="${esc(o.ends_at)}"></b>.</span>
    <button class="gb-btn" onclick="go('store')">Shop the sale ›</button>
  </div>`;
}
/* Timer w window._offerTimer i kasowany przy KAŻDYM starcie: go() przerysowuje
   #view, więc bez tego po kilku przełączeniach zakładek biegłoby kilka
   interwałów naraz na nieistniejących elementach. */
function startOfferTimer(){
  clearInterval(window._offerTimer);
  const el=document.getElementById('offer-cd');
  if(!el)return;
  const koniec=dutc(el.dataset.ends).getTime();
  const tick=()=>{
    const e=document.getElementById('offer-cd');
    if(!e){clearInterval(window._offerTimer);return}
    let s=Math.floor((koniec-Date.now())/1000);
    if(s<=0){clearInterval(window._offerTimer);e.textContent='0:00:00';return}
    const d=Math.floor(s/86400);s%=86400;
    const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=s%60;
    e.textContent=(d?d+'d ':'')+h+':'+String(m).padStart(2,'0')+':'+String(sec).padStart(2,'0');
  };
  tick();window._offerTimer=setInterval(tick,1000);
}

/* ====================== Today's risk budget ======================
   Ekran, ktory trader ma sprawdzic PRZED otwarciem pozycji. Portal pokazywal
   limity wylacznie jako procent ZUZYCIA ("Daily loss 38%"), a decyzja o
   wielkosci pozycji zapada w dolarach POZOSTALYCH — kazdy przeliczal to sobie
   w glowie z salda i regulaminu. Tu nie powstaje zadna nowa liczba: wszystko
   wychodzi z `metrics`, ktore serwer i tak liczy tym samym kodem co bramki
   breachow, wiec pasek nie moze rozjechac sie z prawdziwym limitem. */
function riskOf(a){
  const m=a.metrics||{};
  if(m.daily_floor==null||m.overall_floor==null||a.equity==null)return null;
  const init=a.initial_balance||0;
  const dayCap=(m.max_daily_loss_pct||0)/100*init;
  const ddCap=(m.max_overall_loss_pct||0)/100*init;
  if(dayCap<=0&&ddCap<=0)return null;
  return {dayCap,ddCap,
    dayLeft:Math.max(0,a.equity-m.daily_floor),
    ddLeft:Math.max(0,a.equity-m.overall_floor),
    /* Equity na otwarciu dnia = podloga dnia + caly dzienny limit. Serwer tej
       liczby nie oddaje, a bez niej nie da sie pokazac wyniku DNIA — saldo
       mowi tylko o calej ewaluacji. */
    today:a.equity-(m.daily_floor+dayCap)};
}
/* Jak blisko limitu jest konto (0 = na podlodze, 1 = pelny budzet). Sluzy do
   wyboru konta, ktore karta pokazuje domyslnie. */
function riskRatio(r){
  return r.dayCap>0?r.dayLeft/r.dayCap:r.ddCap>0?r.ddLeft/r.ddCap:1;
}
function riskBar(label,left,cap,foot){
  const pct=cap>0?Math.max(0,Math.min(100,left/cap*100)):0;
  const k=pct<=20?'bad':pct<=45?'warn':'ok';
  return `<div class="rb">
    <div class="rb-top"><span>${label}</span><b class="${k}">$${fmt(left)}</b></div>
    <div class="rb-track"><i class="${k}" style="width:${pct.toFixed(1)}%"></i></div>
    <div class="rb-foot">${foot}</div>
  </div>`;
}

/* Migawka kont z poprzedniej wizyty — zrodlo linii "co sie zmienilo, odkad
   ostatnio patrzyles". Trzymana lokalnie, bo to stan PRZEGLADARKI, nie konta:
   ten sam trader na telefonie i na laptopie patrzyl ostatnio w innym momencie. */
const SEEN_KEY='pf_seen_acc';
function seenMap(){try{return JSON.parse(localStorage.getItem(SEEN_KEY)||'{}')||{}}catch(e){return{}}}
function sinceLine(a){
  /* Policzone RAZ na zaladowanie strony i zapamietane: migawka odswieza sie
     zaraz po wyrenderowaniu, wiec bez tego cache linia znikalaby przy pierwszym
     przelaczeniu zakladki — czyli dokladnie wtedy, gdy trader do niej wraca. */
  window._sinceCache=window._sinceCache||{};
  if(a.id in window._sinceCache)return window._sinceCache[a.id];
  const s=seenMap()[a.id]||{};
  const h=s.ts?(Date.now()-s.ts)/36e5:0;
  let out='';
  /* Ponizej 6 godzin to nie jest "poprzednia wizyta", tylko odswiezenie
     strony — a podsumowanie zmian z ostatnich dziesieciu minut to szum. */
  if(h>=6){
    const db=+(a.balance-(s.b||0)).toFixed(2), dd=((a.metrics||{}).trading_days||0)-(s.td||0);
    const zm=[];
    if(Math.abs(db)>=0.01)zm.push(`balance ${money(db)}`);
    if(dd>0)zm.push(`${dd} trading day${dd>1?'s':''}`);
    if(s.ph&&s.ph!==a.phase)zm.push(`moved to ${PHASE_LABEL[a.phase]||esc(a.phase)}`);
    if(zm.length)out=`<div class="rc-since">${ICO.eye} Since you last looked,
      ${h<48?Math.round(h)+'h':Math.round(h/24)+' days'} ago: ${zm.join(' · ')}</div>`;
  }
  window._sinceCache[a.id]=out;
  return out;
}
function markSeen(accs){
  const m=seenMap(),now=Date.now();
  let zmiana=false;
  accs.forEach(a=>{
    /* Najwyzej raz na 30 minut: przy odswiezaniu strony co minute roznica
       zawsze bylaby zerowa i linia nigdy by sie nie pokazala. */
    if(m[a.id]&&m[a.id].ts&&now-m[a.id].ts<18e5)return;
    m[a.id]={b:a.balance,td:(a.metrics||{}).trading_days||0,ph:a.phase,ts:now};
    zmiana=true;
  });
  if(zmiana)try{localStorage.setItem(SEEN_KEY,JSON.stringify(m))}catch(e){}
}

function riskCardHtml(accs){
  const live=accs.filter(a=>!['breached','failed','provisioning'].includes(a.status)&&riskOf(a));
  if(!live.length)return'';
  /* Domyslnie konto NAJBLIZEJ limitu — to ono wymaga decyzji. Wybor tradera
     wygrywa, dopoki wskazane konto zyje. */
  const wybor=(ME&&ME.ui_prefs&&ME.ui_prefs.riskAcc)|0;
  const a=live.find(x=>x.id===wybor)
    ||live.reduce((b,x)=>riskRatio(riskOf(x))<riskRatio(riskOf(b))?x:b);
  const r=riskOf(a),m=a.metrics||{};
  const chips=live.length>1?`<div class="rc-tabs" data-slide data-slide-key="rc">${live.map(x=>
    `<button class="rc-tab${x.id===a.id?' on':''}" onclick="pickRiskAcc(${x.id})">${esc(x.login)}</button>`).join('')}</div>`:'';
  return `<div class="sec-card risk-card">
    <div class="rc-head">
      <div>
        <div class="rc-eyebrow">Today · ${esc(a.login)} · ${PHASE_LABEL[a.phase]||esc(a.phase)}</div>
        <div class="rc-pnl ${r.today>=0?'up':'down'}">${money(r.today)}</div>
        <div class="rc-sub">since the daily reset</div>
      </div>
      <div class="rc-clock">${ICO.cal} Daily limit resets in
        <b id="risk-cd" data-ends="${esc(a.day_reset_at||'')}">—</b></div>
    </div>
    <div class="rc-bars">
      ${/* Bez mianownika "of $X": konto na plusie ma WIECEJ miejsca niz wynosi
            limit (zysk dnia podnosi podloge o tyle samo), wiec "$3,000 of
            $2,500" czytalo sie jak blad. Zostaje kwota i twarda granica. */''}
      ${r.dayCap>0?riskBar('Room left today',r.dayLeft,r.dayCap,
        `Daily stop-out at $${fmt(m.daily_floor)} · ${m.max_daily_loss_pct}% rule`):''}
      ${r.ddCap>0?riskBar('Buffer above max drawdown',r.ddLeft,r.ddCap,
        `Account closes at $${fmt(m.overall_floor)} · ${m.max_overall_loss_pct}% max drawdown`):''}
    </div>
    ${sinceLine(a)}${chips}
  </div>`;
}
function pickRiskAcc(id){setUiPref('riskAcc',id);VIEWS.accounts()}
/* Ten sam wzorzec co startOfferTimer: jeden interwal, kasowany przy kazdym
   starcie, bo go() przerysowuje #view. Minuty, nie sekundy — do polnocy jest
   kilka godzin i tykajace sekundy robilyby z tego zegar odliczajacy do straty. */
function startRiskTimer(){
  clearInterval(window._riskTimer);
  const el=document.getElementById('risk-cd');
  if(!el||!el.dataset.ends)return;
  const koniec=dutc(el.dataset.ends).getTime();
  const tick=()=>{
    const e=document.getElementById('risk-cd');
    if(!e){clearInterval(window._riskTimer);return}
    const s=Math.floor((koniec-Date.now())/1000);
    if(s<=0){clearInterval(window._riskTimer);e.textContent='moments';return}
    const h=Math.floor(s/3600),mi=Math.floor(s%3600/60);
    e.textContent=h?`${h}h ${mi}m`:`${mi}m`;
  };
  tick();window._riskTimer=setInterval(tick,30000);
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
    loadOffers(),
    loadCase(),
  ]);
  /* Darmowa skrzynka wygrywa z kazdym innym banerem: jest jednorazowa i znika
     sama po otwarciu. Stoi tez nad pustym stanem — nowy klient z /trade nie
     ma jeszcze zadnego konta. */
  const caseB=caseBannerHtml();
  /* Konto z realnym rachunkiem MT5 (add-on Copytrading) ma equity, ktore ZMIENIA
     sie samo miedzy odslonami — reszta kont stoi. Minutowe odswiezanie wlacza sie
     wiec tylko wtedy, gdy jest co odswiezac; patrz refreshLive(). */
  window._zywaLista=accs.some(a=>a.mt5_backed);
  const banner=accs.length
    ?`<div class="gradient-banner">
        <span class="gb-tag">${ICO.spark} Refer &amp; earn</span><span class="gb-sep"></span>
        <span class="gb-txt">Share your link and earn <b>10%</b> of every challenge your referrals buy — tracked automatically.</span>
        <button class="gb-btn" onclick="goAffiliate()">Get your link ›</button>
      </div>`
    :'';
  if(!accs.length){
    $('view').innerHTML=caseB+`<div class="empty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>
      <h3>No challenges yet</h3><p>Start a challenge and your MT5 account will appear here automatically.</p>
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
        <div class="acct-ava acct-ava-txt">MT5</div>
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
        <div class="cr-num"><div class="l">Balance</div><div class="v">$${fmt(a.balance)}</div>
          ${funded?`<div class="ret ${(m.profit_pct||0)>=0?'up':'down'}">${(m.profit_pct||0)>=0?'+':''}${(m.profit_pct||0).toFixed(2)}%</div>`:''}</div>
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
  /* Flash sale wygrywa z refer&earn i upsellem — to promocja, którą właściciel
     wystawił świadomie i na czas. Push opt-in zostaje przed nią (jednorazowy). */
  const flashB=flashBannerHtml();
  /* Budzet ryzyka NAD podsumowaniami: kafelki mowia, ile trader ma, a ta karta
     — ile moze dzis stracic, zanim konto zniknie. Przy filtrze liczy z calego
     zbioru, bo limit dnia obowiazuje niezaleznie od tego, co jest na ekranie. */
  $('view').innerHTML=(caseB||pushB||flashB||up.banner||banner)+riskCardHtml(accs)+stats+filterBar+list+up.html;
  startOfferTimer();
  startRiskTimer();
  markSeen(accs);
  rollStats();
  if(window._upsellJump){window._upsellJump=false;setTimeout(flashUpsell,60)}
 },

 async store(){
  const [ps]=await Promise.all([api('/api/products'),loadOffers()]);
  PRODUCTS=ps;
  const models=[
    {id:'2step',name:'2-Step Evaluation',match:p=>p.steps===2&&p.price_usd>0},
    {id:'instant',name:'Instant Funding',match:p=>p.steps===0&&p.price_usd>0},
  ].filter(g=>ps.some(g.match));
  const TIERS=[['starter','Starter',v=>v<200000],['pro','Pro',v=>v>=200000&&v<800000],['elite','Elite',v=>v>=800000]];
  window._shopTab=window._shopTab||(models[0]&&models[0].id);
  window._shopTier=window._shopTier||'starter';
  /* One-shot hydrate from LP deep link (?wt=1&sb=1&…). After that the
     strip toggles own the flags — don't keep OR-ing the URL on every buy. */
  if(!window._shopHydrated){
    const boot=new URLSearchParams(location.search);
    if(boot.get('wt')==='1')window._shopWt=true;
    if(boot.get('od')==='1')window._shopOd=true;
    if(boot.get('sb')==='1')window._shopSb=true;
    if(boot.get('ct')==='1')window._shopCt=true;
    window._shopHydrated=true;
  }
  window._shopWt=!!window._shopWt;
  window._shopOd=!!window._shopOd;
  window._shopCt=!!window._shopCt;
  window._shopSb=!!window._shopSb;
  window._shopFlip=window._shopFlip||new Set();
  const buyKey=new URLSearchParams(location.search).get('buy');
  if(buyKey){
    const hit=ps.find(p=>p.key===buyKey);
    if(hit){
      window._shopTab=hit.steps===0?'instant':'2step';
      const tier=TIERS.find(([, ,f])=>f(hit.account_size));
      if(tier)window._shopTier=tier[0];
    }
  }
  const g=models.find(x=>x.id===window._shopTab)||models[0];
  if(!g){$('view').innerHTML='<div class="empty"><h3>No plans available</h3></div>';return}
  const typeItems=ps.filter(g.match).sort((a,b)=>a.account_size-b.account_size);
  const tiers=TIERS.filter(([, ,f])=>typeItems.some(x=>f(x.account_size)));
  if(!tiers.some(([id])=>id===window._shopTier))window._shopTier=tiers[0]?tiers[0][0]:'starter';
  const [,tierName,inTier]=TIERS.find(([id])=>id===window._shopTier)||TIERS[0];
  const shown=typeItems.filter(x=>inTier(x.account_size));
  /* Starter/Pro/Elite to tylko przedzialy rozmiaru — notka mowi to wprost (jak na landingu). */
  const kLbl=v=>v>=1e6?'$'+(v/1e6)+'M':'$'+(v/1e3)+'K';
  const shopTierNote=()=>TIERS.map(([,nm,f])=>{const s=typeItems.filter(x=>f(x.account_size)).map(x=>x.account_size).sort((a,b)=>a-b);
      return s.length?`${nm} ${kLbl(s[0])}${s.length>1?'–'+kLbl(s[s.length-1]):''}`:null}).filter(Boolean).join(' · ')
    +'. Same rules on every size: only the price and the max open volume grow with the account.';
  const model=g.id==='instant'?'Instant':'2-Step';
  const anyCt=shown.some(p=>p.copytrading_offered);
  if(!anyCt)window._shopCt=false;
  const showSb=g.id==='instant'&&shown.some(p=>p.split_boost_offered);
  if(!showSb)window._shopSb=false;
  const odCena=g.id==='instant'?399:299;
  const ctCena=(shown[0]&&shown[0].copytrading_fee_usd)||299;
  const sbCena=(shown[0]&&shown[0].split_boost_fee_usd)||149;
  const hot=shown.findIndex(x=>x.popular);
  const star=hot>=0?hot:Math.floor(shown.length/2);
  const usd=v=>'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:v%1?2:0,maximumFractionDigits:2});
  /* Same back-face rows as LP Challenges (site.js cardRules). */
  const splitPct=p=>{
    const base=p.profit_split_pct||0;
    const boost=(p.split_boost_pp||10);
    return (window._shopSb&&showSb&&p.steps===0)?base+boost:base;
  };
  const rules=p=>{
    const instant=p.steps===0;
    const lev='Up to 1:'+(window.LEVERAGE_MAX||100);
    const rewards=window._shopOd?'On demand'
      :((window.PAYOUT_DAYS|0)===0?'On demand'
        :(window.PAYOUT_DAYS===14||window.PAYOUT_DAYS==null?'Bi-weekly':`Every ${window.PAYOUT_DAYS} days`));
    return [
      ['Profit target',instant?'None':`${p.profit_target_p1}% / ${p.profit_target_p2}%`],
      ['Daily loss limit',p.max_daily_loss_pct+'%'],
      ['Max drawdown',p.max_overall_loss_pct+'%'],
      ['Min. trading days',String(p.min_trading_days)],
      ['Profit split',(instant?'':'Up to ')+splitPct(p)+'%'],
      ['Rewards',rewards],
      ['Max open volume',p.max_lots+' lots'],
      ['Leverage',lev],
      ['Fee credit',instant?'Not included':'With first payout'],
    ];
  };
  const cards=shown.map((p,i)=>{
    const addons=(window._shopWt?199:0)+(window._shopOd?odCena:0)
      +(window._shopCt&&p.copytrading_offered?ctCena:0)
      +(window._shopSb&&showSb?sbCena:0);
    const base=p.offer_price_usd!=null?p.offer_price_usd:p.price_usd;
    const fee=Math.round((base+addons)*100)/100;
    const open=window._shopFlip.has(p.key);
    return `<article class="chal-card pal-${window._shopTier}-${i}${i===star?' star':''}${p.popular?' hot':''}${open?' flip':''}" data-plan="${esc(p.key)}">
      <div class="chal-flip" role="button" tabindex="0" aria-label="Show or hide the rules">
        <div class="chal-inner">
          <div class="chal-face chal-front">
            <i class="chal-lines"></i><i class="chal-glare"></i><i class="chal-mark"></i><i class="chal-logo"></i>
            <div class="chal-price">${p.offer_price_usd!=null?`<s>${usd(p.price_usd+addons)}</s>`:''}<b>${usd(fee)}</b> <small>USD</small>${addons?`<em>incl. ${usd(addons)} add-ons</em>`:''}</div>
            <div class="chal-size">
              ${p.popular?'<i>Best value</i>':''}
              ${p.promo_upgrade_size&&pfPromo()?`<i>You trade $${fmt0(p.promo_upgrade_size)}</i>`:''}
              <strong>$${p.account_size.toLocaleString('en-US')}</strong>
              <span>${model} ${tierName} · min. ${p.min_trading_days} days</span>
            </div>
            <ul class="chal-specs">
              <li><small>Target</small><b>${p.steps===0?'None':p.profit_target_p1+'%'}</b></li>
              <li><small>Daily</small><b>${p.max_daily_loss_pct}%</b></li>
              <li><small>Max DD</small><b>${p.max_overall_loss_pct}%</b></li>
              <li><small>Split</small><b>${splitPct(p)}%</b></li>
            </ul>
          </div>
          <div class="chal-face chal-back">
            <i class="chal-lines"></i><i class="chal-logo"></i>
            <h3><small>${model} ${tierName}</small>$${p.account_size.toLocaleString('en-US')}</h3>
            <dl>${rules(p).map(([l,v])=>`<div><dt>${l}</dt><dd>${v}</dd></div>`).join('')}</dl>
          </div>
        </div>
      </div>
      <button type="button" class="btn-ghost chal-btn" data-flip="${jsq(p.key)}" aria-expanded="${open}">${open?'Hide Rules':'View Rules'}</button>
      <button type="button" class="btn chal-btn go" data-buy="${jsq(p.key)}">Start Challenge</button>
    </article>`;
  }).join('');
  $('view').innerHTML=`
    ${flashBannerHtml()}
    <div class="chal-switches store-chal">
      <div class="chal-tabs" id="shopModels">${models.map(x=>`<button type="button" class="${x.id===g.id?'on':''}" data-m="${x.id}">${x.name}</button>`).join('')}</div>
      <div class="chal-tabs" id="shopTiers">${tiers.map(([id,nm])=>`<button type="button" class="${id===window._shopTier?'on':''}" data-t="${id}">${nm}</button>`).join('')}</div>
      <p class="chal-tier-note">${esc(shopTierNote())}</p>
    </div>
    ${ME.credits_usd>0?`<div class="note" style="margin:0 auto 14px;max-width:920px"><b>Store credit: $${fmt(ME.credits_usd)}</b>, applied at checkout.</div>`:''}
    <div class="chal-grid" id="shopGrid">${cards||'<div class="empty"><h3>No plans in this tier</h3></div>'}</div>
    <div class="chal-extras" id="shopExtras">
      <div class="chal-opt"><span><b>Weekend trading</b><small>+$199</small></span>
        <button class="chal-sw" id="shop-wt" type="button" role="switch" aria-checked="${!!window._shopWt}" aria-label="Add weekend trading"></button></div>
      ${anyCt?`<div class="chal-opt"><span><b>EA Allowed</b><small>+$${ctCena}</small></span>
        <button class="chal-sw" id="shop-ct" type="button" role="switch" aria-checked="${!!window._shopCt}" aria-label="Add EA Allowed"></button></div>`:''}
      <div class="chal-opt"><span><b>On-demand payouts</b><small>+$${odCena}</small></span>
        <button class="chal-sw" id="shop-od" type="button" role="switch" aria-checked="${!!window._shopOd}" aria-label="Add on-demand payout"></button></div>
      ${showSb?`<div class="chal-opt"><span><b>Split Boost</b><small>+$${sbCena}</small></span>
        <button class="chal-sw" id="shop-sb" type="button" role="switch" aria-checked="${!!window._shopSb}" aria-label="Add split boost"></button></div>`:''}
      <div class="chal-opt"><span><b>News trading</b><small>Included</small></span></div>
      <div class="chal-opt chal-code">
        <input id="shop-coupon" autocomplete="off" placeholder="Coupon code" aria-label="Coupon code"
          value="${esc(window._shopCode||pfCoupon()||pfPromo()||'')}">
        <button id="shop-apply" class="code-btn" type="button">Apply</button>
      </div>
    </div>
    <div class="code-msg chal-msg" id="shop-codemsg" hidden></div>
    <p class="chal-fine">One-time fee for access to an evaluation on a simulated account. No real money is deposited or
      traded at any point. Performance rewards, including the 2-Step fee credit, are discretionary and not guaranteed.
      <a href="/terms">Terms of Service</a></p>`;
  /* Same as LP site.js slidePill: remounted tabs start the pill where it was
     last, then glide — without that the portal jump looked broken. */
  const slidePill=(box,key)=>{
    const on=box&&box.querySelector('button.on'); if(!on)return;
    window._shopPill=window._shopPill||{};
    const prev=window._shopPill[key];
    let ind=box.querySelector('.chal-ind');
    if(!ind){
      ind=document.createElement('i'); ind.className='chal-ind'; box.prepend(ind);
      if(prev){ind.style.left=prev.il+'px'; ind.style.width=prev.iw+'px'; void ind.offsetWidth}
    }
    const l=on.offsetLeft, w=on.offsetWidth;
    ind.style.left=l+'px'; ind.style.width=w+'px';
    window._shopPill[key]={il:l,iw:w};
  };
  const q1=(s,r)=> (r||document).querySelector(s);
  const qa=(s,r)=>[...(r||document).querySelectorAll(s)];
  slidePill($('shopModels'),'models'); slidePill($('shopTiers'),'tiers');
  /* Defer full re-render: replacing the DOM on the same click lets the
     synthetic mouseup/click land on a challenge card underneath (flip/buy). */
  const shopRedraw=()=>{clearTimeout(window._shopRedraw);window._shopRedraw=setTimeout(()=>VIEWS.store(),80)};
  const shopTab=(fn)=>e=>{
    e.preventDefault(); e.stopPropagation();
    fn();
    if(window._shopFlip)window._shopFlip.clear();
    shopRedraw();
  };
  qa('#shopModels button').forEach(b=>b.onclick=shopTab(()=>{window._shopTab=b.dataset.m}));
  qa('#shopTiers button').forEach(b=>b.onclick=shopTab(()=>{window._shopTier=b.dataset.t}));
  const shopToggle=(flag,btn)=>e=>{
    e.preventDefault(); e.stopPropagation();
    window[flag]=!window[flag];
    if(btn)btn.setAttribute('aria-checked',window[flag]?'true':'false');
    /* Keep ?sb=1 (etc.) in sync — buyAddonsFromShop used to OR the URL and
       resurrect Split Boost after the switch was turned off. */
    const q=new URLSearchParams(location.search);
    const map={_shopWt:'wt',_shopOd:'od',_shopSb:'sb',_shopCt:'ct'};
    const k=map[flag];
    if(k){if(window[flag])q.set(k,'1');else q.delete(k)}
    const qs=q.toString();
    history.replaceState({},'',location.pathname+(qs?'?'+qs:'')+location.hash);
    shopRedraw();
  };
  const wt=$('shop-wt'); if(wt)wt.onclick=shopToggle('_shopWt',wt);
  const od=$('shop-od'); if(od)od.onclick=shopToggle('_shopOd',od);
  const sbSw=$('shop-sb'); if(sbSw)sbSw.onclick=shopToggle('_shopSb',sbSw);
  const ct=$('shop-ct'); if(ct)ct.onclick=shopToggle('_shopCt',ct);
  const shopApply=$('shop-apply'); if(shopApply)shopApply.onclick=e=>{e.preventDefault();e.stopPropagation();shopApplyCode()};
  const shopInp=$('shop-coupon');
  if(shopInp){
    shopInp.onclick=e=>e.stopPropagation();
    shopInp.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();shopApplyCode()}};
  }
  if(window._shopCodeMsg)shopCodeMsg(window._shopCodeMsg.kind,window._shopCodeMsg.html);
  qa('#shopGrid .chal-card').forEach(card=>{
    const turn=()=>{
      const on=card.classList.toggle('flip');
      const b=q1('[data-flip]',card);
      const key=b.dataset.flip;
      if(on)window._shopFlip.add(key); else window._shopFlip.delete(key);
      b.textContent=on?'Hide Rules':'View Rules';
      b.setAttribute('aria-expanded',on);
    };
    q1('[data-flip]',card).onclick=e=>{e.stopPropagation();turn()};
    q1('.chal-flip',card).onclick=turn;
    q1('[data-buy]',card).onclick=e=>{
      e.stopPropagation();
      const key=e.currentTarget.dataset.buy;
      const q=new URLSearchParams(location.search);
      q.set('buy',key);
      if(window._shopWt)q.set('wt','1'); else q.delete('wt');
      if(window._shopOd)q.set('od','1'); else q.delete('od');
      if(window._shopSb&&showSb)q.set('sb','1'); else q.delete('sb');
      if(window._shopCt)q.set('ct','1'); else q.delete('ct');
      const code=(($('shop-coupon')&&$('shop-coupon').value)||window._shopCode||'').trim().toUpperCase();
      if(code)q.set('coupon',code); else q.delete('coupon');
      history.replaceState({},'',location.pathname+'?'+q.toString()+location.hash);
      openBuy(key);
    };
  });
  startOfferTimer();
  highlightPlanFromUrl();
 },

 async board(){
  const t=(ME&&ME.ui_prefs&&ME.ui_prefs.board_tab)==='discipline'?'discipline':'profit';
  $('view').innerHTML=`<div class="shop-tabs" style="margin-bottom:18px">${
    [['profit','Profit'],['discipline','Discipline']].map(([k,l])=>
      `<button class="shop-tab${t===k?' on':''}" onclick="boardTab('${k}')">${l}</button>`).join('')
    }</div><div id="board-body"></div>`;
  await (t==='discipline'?VIEWS._boardDiscipline():VIEWS._boardProfit());
 },

 async _boardProfit(){
  const b=await api('/api/leaderboard');
  if(!b.length){$('board-body').innerHTML='<div class="empty"><h3>No ranked accounts yet</h3><p>The leaderboard fills up as traders make progress.</p></div>';return}
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
  $('board-body').innerHTML=`
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

 /* Druga lista mowi co innego niz pierwsza, wiec i wyglada inaczej: bez podium,
    bez medali i bez strzalek zmiany miejsca. Podium znaczy „wyscig wygrany", a
    tu nie ma czego wygrac — trzy konta na czele stoja rowno, bo dyscyplina to
    stan, nie zdobycz. Strzalek nie ma z tego samego powodu, dla ktorego nie ma
    konfetti: nie budujemy powodu, zeby zagladac tu codziennie. */
 async _boardDiscipline(){
  const [b,ja]=await Promise.all([api('/api/discipline'),api('/api/me/discipline').catch(()=>null)]);
  if(!b.length){$('board-body').innerHTML=`<div class="empty"><h3>The Discipline board is being compiled</h3>
    <p>Accounts join the board after ${DISC_MIN_DAYS} trading days. Nothing to do — it fills in on its own.</p></div>`;return}

  /* Skladowe wyniku niosa cala tresc tej listy: samo „87" nie mowi traderowi,
     co ma robic dalej. Pasmo jest jedno i podzielone w proporcji 40/30/30, bo
     te wagi sa czescia komunikatu — trzy osobne mierniki to trzy osobne
     spojrzenia, a pierscien chowa podzial za katem obrotu. */
  const pasmo=r=>`<div class="disc-bar" aria-hidden="true">
      <span class="disc-seg s1" style="flex:40"><i style="width:${r.clean_pts/40*100}%"></i></span>
      <span class="disc-seg s2" style="flex:30"><i style="width:${r.buffer_pts/30*100}%"></i></span>
      <span class="disc-seg s3" style="flex:30"><i style="width:${r.consist_pts/30*100}%"></i></span>
    </div>`;
  const legenda=r=>`<div class="disc-leg">
      <span style="flex:40"><b>${r.clean_pts}</b>/40 daily risk</span>
      <span style="flex:30"><b>${r.buffer_pts}</b>/30 buffer</span>
      <span style="flex:30"><b>${r.consist_pts}</b>/30 spread</span>
    </div>`;

  const dni=b.reduce((s,r)=>s+r.trading_days,0);
  const czyste=b.reduce((s,r)=>s+r.clean_days,0);
  const zSpreadem=b.filter(r=>r.top_day_share!=null);
  const rowne=zSpreadem.filter(r=>r.top_day_share<40).length;
  const sredni=Math.round(b.reduce((s,r)=>s+r.score,0)/b.length);

  const karta=(r,i)=>`
    <div class="disc-card a${i+1}">
      <div class="pod-rank">${String(i+1).padStart(2,'0')}</div>
      <div class="disc-badge">${ICO.shield}</div>
      <div class="pod-name">${esc(r.trader)}</div>
      <div style="margin:6px 0 10px"><span class="status ${r.status==='funded'?'funded':'active'}"><span class="dot"></span>${r.status==='funded'?'Funded':'Evaluation'}</span></div>
      <div class="disc-score">${r.score}<small>/100</small></div>
      ${pasmo(r)}${legenda(r)}
      <div class="pod-mini">
        <div><div class="l">Account</div><div class="v">$${fmt0(r.account_size)}</div></div>
        <div><div class="l">Clean days</div><div class="v">${r.clean_days}/${r.trading_days}</div></div>
      </div>
    </div>`;

  /* Wlasne miejsce mowi serwer, bo ekran nie ma jak go poznac: lista jest
     maskowana, wiec trader sie na niej nie rozpozna, i uciecia do dziesieciu,
     wiec nieobecnosc na niej nie znaczy „nie zakwalifikowales sie". Bez rady,
     bez „jeszcze troche" — samo miejsce i zasada wejscia. */
  const uwaga=!ja?''
    :ja.ranked
      ?`<div class="disc-note">${ICO.shield}<div>Your account is <b>#${ja.rank} of ${ja.of}</b> ranked accounts,
          scoring <b>${ja.score}/100</b> over ${ja.trading_days} trading days.</div></div>`
      :`<div class="disc-note">${ICO.shield}<div>Your account is not ranked yet. Accounts join after
          ${ja.min_days} trading days — no action is needed, the board updates on its own.</div></div>`;

  $('board-body').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic blue">${ICO.layers}</div>
        <div><div class="lbl">Accounts ranked</div><div class="val">${b.length}</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.shield}</div>
        <div><div class="lbl">Average score</div><div class="val">${sredni}<span style="font-size:14px;color:var(--dim)">/100</span></div>
          <div class="sub">across ranked accounts</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.cal}</div>
        <div><div class="lbl">Clean-day rate</div><div class="val">${dni?Math.round(czyste/dni*100):0}%</div>
          <div class="sub">days inside half the daily limit</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.bars}</div>
        <div><div class="lbl">Balanced spread</div><div class="val">${zSpreadem.length?Math.round(rowne/zSpreadem.length*100)+'%':'—'}</div>
          <div class="sub">no single day over 40% of profit</div></div></div>
    </div>
    ${uwaga}
    <div class="podium">${b.slice(0,3).map(karta).join('')}</div>
    ${b.length>3?`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.discipline">
      <thead><tr><th style="width:52px">#</th><th>Trader</th><th>Account</th><th>Stage</th>
        <th style="min-width:190px">Score breakdown</th><th style="text-align:right">Score</th></tr></thead>
      <tbody>`+b.slice(3).map((r,i)=>`<tr>
        <td class="num muted">${String(i+4).padStart(2,'0')}</td>
        <td>${esc(r.trader)}</td>
        <td class="num muted">$${fmt0(r.account_size)}</td>
        <td><span class="status ${r.status==='funded'?'funded':'active'}"><span class="dot"></span>${r.status==='funded'?'Funded':'Evaluation'}</span></td>
        <td>${pasmo(r)}<div class="disc-leg tiny"><span style="flex:40">daily risk</span><span style="flex:30">buffer</span><span style="flex:30">spread</span></div></td>
        <td class="num" style="text-align:right" data-sort="${r.score}"><b style="font-size:15px">${r.score}</b>
          <div style="font-size:11px;opacity:.78">${r.clean_days}/${r.trading_days} clean days</div></td></tr>`).join('')+`
      </tbody></table></div>`:''}
    <p class="muted" style="font-size:11.5px;margin-top:12px">
      100 points: up to 40 for how little of the daily loss limit gets used, up to 30 for how
      much of the overall drawdown is still unspent, up to 30 for a result that does not rest
      on one single day. Length of history is not scored — accounts join after ${DISC_MIN_DAYS} trading days;
      evaluation and funded accounts are ranked together. Names are masked for privacy.</p>`;
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
             onclick="copyVal(this,'${jsq(r.code)}')">${ICO.copy}</button></div>`
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
          ?`<button class="btn-o sm" onclick="copyVal(this,'${jsq(c.code)}')">Copy</button>`
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
  /* Każda faza osobno: po awansie konto startuje od zera, więc Phase 1,
     Phase 2 i Funded to trzy różne historie, a nie jedna wspólna. Domyślnie
     faza bieżąca; wybór pamiętamy per konto. */
  const accSel=accs.find(a=>a.id===window._anAcc);
  const fazy=(accSel&&accSel.phases&&accSel.phases.length)?accSel.phases:[{phase:accSel?.phase,label:'',current:true}];
  window._anPhase=window._anPhase||{};
  let faza=window._anPhase[window._anAcc];
  if(!fazy.some(f=>f.phase===faza))faza=(fazy.find(f=>f.current)||fazy[fazy.length-1]).phase;
  const fazaInfo=fazy.find(f=>f.phase===faza)||{};
  const q=faza?`?phase=${encodeURIComponent(faza)}`:'';
  const [act,st]=await Promise.all([
    api(`/api/me/accounts/${window._anAcc}/activity${q}`),
    api(`/api/me/accounts/${window._anAcc}/stats${q}`)]);
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
      <select id="an-sel" class="inp" style="max-width:340px" onchange="const [i,f]=this.value.split('|');window._anAcc=parseInt(i);window._anPhase[window._anAcc]=f;VIEWS.analytics()">
        ${accs.map(a=>((a.phases&&a.phases.length)?a.phases:[{phase:a.phase,label:''}]).slice().reverse().map(f=>
          `<option value="${a.id}|${esc(f.phase||'')}"${a.id===window._anAcc&&f.phase===faza?' selected':''}>${esc(a.login)} · ${esc(a.product_key)}${f.label?' · '+esc(f.label)+(f.current&&a.phases.length>1?' (now)':''):''}</option>`).join('')).join('')}
      </select>
      <span class="muted" style="font-size:12px">${fazaInfo.label
        ?`${esc(fazaInfo.label)} only${fazaInfo.from?` · since ${dstr(fazaInfo.from)}`:''}${fazaInfo.to?` until ${dstr(fazaInfo.to)}`:''} — every phase starts from zero.`
        :'Computed server-side from every closed trade on this account.'}</span>
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
    bar('an-hr',st.by_hour.map((_,h)=>h+':00'),st.by_hour.map(b=>b.pnl),
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

 /* Przeglad ZAMKNIETEGO tygodnia — cel poniedzialkowego powiadomienia. Nie ma
    go w menu i nie da sie tu wejsc "sprawdzic wyniku na zywo": okno jest zawsze
    to samo, niezaleznie od dnia wejscia. Obserwacje opisuja przeszlosc i konczy
    je zdanie, ktore mowi to wprost — to nie sa sygnaly ani cele. */
 async weekly(){
  const d=await api('/api/me/weekly');
  const wd=s=>new Date(s+'T00:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
  /* Os NIE stoi w polowie wykresu: polowki dostaja wysokosc w proporcji do
     najlepszego i najgorszego dnia, a slupek liczy sie wzgledem swojej polowki.
     Dzieki temu jeden dolar to tyle samo pikseli po obu stronach (czyli wykres
     nie klamie), a tydzien z jednym glebokim minusem nie zostawia u gory
     polowy pustej karty. */
  const mp=Math.max(...d.days.map(x=>Math.max(0,x.pnl)));
  const mn=Math.max(...d.days.map(x=>Math.max(0,-x.pnl)));
  /* Dzien, ktory ledwo drgnal, nie moze zniknac do zera — 4% wysokosci zostawia
     slad, ze handel BYL, tylko wynik wyszedl blisko zera. */
  const h=(v,skala)=>Math.max(4,Math.abs(v)/skala*100);
  /* Kwota jest pozycjonowana ABSOLUTNIE na koncu slupka, nie ustawiona obok niego
     w flexie. Gdy siedziala w tym samym flexie, zabierala wysokosc najwyzszemu
     slupkowi — i najlepszy dzien tygodnia wychodzil niemal rowny slabszemu.
     Wykres ma nie klamac, wiec podpis nie moze zabierac slupkowi wysokosci; ta
     sama wartosc procentowa niesie wiec i slupek, i odsuniecie podpisu. */
  const slupek=x=>{
    const gora=x.trades&&x.pnl>=0, dol=x.trades&&x.pnl<0;
    const p=gora?h(x.pnl,mp):dol?h(x.pnl,mn):0;
    return `<div class="wk-day${x.trades?'':' off'}" title="${x.label} ${x.day} · ${
      x.trades?`${money(x.pnl)} · ${x.trades} trade${x.trades===1?'':'s'}`:'no trades'}">
      <div class="wk-half up">${gora?`<b style="bottom:${p}%">${money(x.pnl)}</b>
        <i style="height:${p}%"></i>`:''}</div>
      <div class="wk-half dn">${dol?`<i style="height:${p}%"></i>
        <b style="top:${p}%">${money(x.pnl)}</b>`:''}</div>
      <span class="wk-lbl">${x.label}</span>
    </div>`;
  };
  const zmiana=d.prev_trades
    ?rp('Week before',`${money(d.prev_net_pnl)}<small> · ${d.prev_trades} trade${d.prev_trades===1?'':'s'}</small>`,
        d.prev_net_pnl>=0?'ok':'bad')
    :rp('Week before','—');
  $('view').innerHTML=`
    <div class="sec-card wk-card">
      <div class="rc-head">
        <div>
          <div class="rc-eyebrow">Week of ${wd(d.from)} – ${wd(d.to)}</div>
          <div class="recap-title">${d.trades
            ?`${d.trades} trade${d.trades===1?'':'s'} over ${d.trading_days} day${d.trading_days===1?'':'s'}`
            :'No closed trades this week'}</div>
        </div>
        <div style="text-align:right">
          <div class="rc-pnl ${d.net_pnl>=0?'up':'down'}">${money(d.net_pnl)}</div>
          <div class="rc-sub">net over the week</div>
        </div>
      </div>
      <div class="wk-days" style="--wk-rows:${mp||1}fr ${mn||1}fr;--wk-up:${(mp||1)/((mp||1)+(mn||1))}">${d.days.map(slupek).join('')}</div>
    </div>
    ${d.trades?`
    <div class="recap-grid wk-grid">
      ${rp('Trading days',d.trading_days)}
      ${rp('Win rate',d.win_rate!=null?`${d.win_rate}%`:'—')}
      ${rp('Green days',d.green_days,d.green_days?'ok':'')}
      ${rp('Red days',d.red_days,d.red_days?'bad':'')}
      ${rp('Best day',d.best_day?money(d.best_day.pnl):'—',d.best_day&&d.best_day.pnl>0?'ok':'')}
      ${rp('Worst day',d.worst_day?money(d.worst_day.pnl):'—',d.worst_day&&d.worst_day.pnl<0?'bad':'')}
      ${rp('Accounts',d.accounts)}
      ${zmiana}
    </div>
    <div class="sec-card"><h3>What happened</h3>
      <ul class="wk-obs">${d.observations.map(o=>`<li>${esc(o)}</li>`).join('')}</ul>
      <p class="wk-note">Observations describe what already happened. They are not signals,
        advice, or targets.</p>
    </div>`
    :`<div class="sec-card"><h3>What happened</h3>
      <p class="muted" style="font-size:13px">Nothing closed between ${wd(d.from)} and ${wd(d.to)}.
        Weeks without trades stay empty here — the day-by-day history of every account is in
        <a href="#" onclick="go('analytics');return false" style="color:var(--acc)">Analytics</a>.</p>
    </div>`}`;
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
  await loadCase(true);
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
            <button class="copy" onclick="copyVal(this,'${jsq(link)}')" title="Copy">${ICO.copy}</button>
          </div>
        </div>
        <div style="display:flex;gap:22px;text-align:center">
          <div><div class="muted" style="font-size:11px">Referred</div><div class="mono" style="font-weight:700;font-size:18px">${aff.referred??0}</div></div>
          <div><div class="muted" style="font-size:11px">Rate</div><div class="mono" style="font-weight:700;font-size:18px">${aff.commission_pct??10}%</div></div>
          <div><div class="muted" style="font-size:11px">Earned</div><div class="mono up" style="font-weight:700;font-size:18px">$${fmt(aff.commission_earned??0)}</div></div>
          <div><div class="muted" style="font-size:11px">Unclaimed</div><div class="mono" style="font-weight:700;font-size:18px">$${fmt(aff.commission_unclaimed??0)}</div></div>
        </div>
        ${(aff.commission_unclaimed??0)>=10?`<div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn-p sm" onclick="claimAffiliate(this,'store_credit')">Claim $${fmt(aff.commission_unclaimed)} as store credit</button>
            <button class="btn-o sm" onclick="claimAffiliate(this,'vusdt')">Claim as vUSDT</button>
          </div>`:''}
      </div>
      <p class="muted" style="font-size:12px;margin-top:12px">Affiliate commission is claimed here as
        <b>store credit</b> or as <b>vUSDT</b> (saved Polygon wallet) — not cash, not via support.
        <a href="/affiliate-terms" target="_blank" rel="noopener" style="color:var(--acc)">Affiliate Terms</a>.</p>
    </div>`;
  const cards=[
    {ic:'wallet',cls:'green',badge:'Active',name:'Fee credit',desc:'On 2-Step plans your one-time challenge fee is credited in full as additional vUSDT on your first payout from a funded account — a credit, not a card refund. Instant Funding has no fee credit.',cond:['Pass the evaluation','Complete KYC','Request your first payout']},
    {ic:'trend',cls:'purple',badge:'Your call',name:'Scaling Plan',desc:'Grow a funded account by +15% and you choose: take the payout, or move up to the next plan in our pricing. Repeatable, no applications.',cond:['Funded account','+15% growth','You pick payout or a bigger plan']},
    {ic:'spark',cls:'blue',badge:'10% recurring',name:'Affiliate Program',desc:'Share your referral link and earn 10% of every challenge purchased by traders you refer — for life. Claim as store credit or vUSDT in the portal.',cond:['Share your link','Friend buys a challenge','Claim store credit or vUSDT']},
    {ic:'crown',cls:'orange',badge:'Spend-based',name:'Loyalty Points',desc:'Every $1 spent earns a loyalty point, and check-ins add more. Trade the points for your own one-time discount code on the Loyalty page.',cond:['500 points: 15% off','1,000 points: 25% off','2,000 points: 35% off']},
  ];
  $('view').innerHTML=caseCardHtml()+revealHtml+affPanel+`<div class="badge-grid" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">`+cards.map(c=>`
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
  const czeka=funded.find(a=>(a.payout_days_left||0)>0);
  /* „Available to request” obok wyszarzonego przycisku byłoby sprzecznością —
     kwota jest prawdziwa, ale dziś nie do wzięcia. Podpis to mówi wprost. */
  const zablokowane=funded.filter(a=>(a.payout_days_left||0)>0)
                          .reduce((s,a)=>s+(a.payout_available||0),0);
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic green">${ICO.wallet}</div>
        <div><div class="lbl">Total paid out</div><div class="val up">$${fmt(data.summary.total_paid)}</div></div></div>
      <div class="stat-tile"><div class="tile-ic orange">${ICO.cal}</div>
        <div><div class="lbl">Pending requests</div><div class="val">${data.summary.pending}</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.dollar}</div>
        <div><div class="lbl">Available to request</div><div class="val">$${fmt(data.summary.available)}</div><div class="sub">${zablokowane<=0
          ?'your split of current profits'
          :zablokowane>=(data.summary.available||0)-0.005
            ?'unlocks at the trading-day minimum'
            :`$${fmt(zablokowane)} of it unlocks at the trading-day minimum`}</div></div></div>
    </div>
    <p class="muted" style="font-size:13px;margin:-6px 0 14px">${(()=>{
      const dni=data.summary.interval_days||14;
      const maOd=funded.some(a=>a.ondemand_payout)||(data.requests||[]).some(r=>r.ondemand);
      if(!dni)return `Payouts are <b>on demand</b> — request whenever you are in profit.`;
      return `Payouts are available <b>every ${dni} days</b> (bi-weekly) by default`+
        (maOd?`; accounts with the <b>On-demand Payout</b> add-on can request anytime`:'')+
        ` — request whenever you are in profit and the window is open.`;
    })()}${
      czeka?` Your ${esc(czeka.login)} plan unlocks its first payout after
        <b>${czeka.metrics?.min_trading_days??0} trading days</b> — a day counts once you trade on it.`:''}</p>
    ${funded.filter(a=>a.scale_up_to).map(a=>{
      const av=a.payout_available??0;
      return `<div class="scale-offer">
        <div class="so-txt">
          <b>${esc(a.login)} is up ${a.scale_trigger_pct}%. Now you choose.</b>
          <span>Take the profit as a payout, or move up to the $${fmt0(a.scale_up_to)} plan. One or
            the other: moving up puts the profit back in as capital, so there is nothing left to
            pay out.</span>
        </div>
        <div class="so-act">
          ${payoutBtn(a,av,'btn-o sm',`Take $${fmt(av)} payout`)}
          <button class="btn-p sm" onclick="openScaleModal(${a.id},${a.initial_balance},${a.scale_up_to})">Move up to $${fmt0(a.scale_up_to)}</button>
        </div>
      </div>`}).join('')}
    ${funded.length?`<div class="panel" style="margin-bottom:16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <span class="muted" style="font-size:13px;margin-right:6px">Request a payout:</span>
      ${funded.map(a=>{const av=a.payout_available??0;
        return payoutBtn(a,av,'btn-o sm',`${esc(a.login)} · $${fmt(av)} available`)}).join('')}
    </div>`:''}
    ${data.requests.length?`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="portal.payout-req">
      <thead><tr><th>Date</th><th>Account</th><th>Profit</th><th>Requested</th><th>Method</th><th>Status</th></tr></thead>
      <tbody>`+data.requests.map(r=>`<tr>
        <td class="muted" data-sort="${esc(r.ts||'')}">${dstr(r.ts)}</td>
        <td class="num">${esc(r.account)}${r.ondemand?' <span style="font-size:10px;font-weight:700;letter-spacing:.4px;color:var(--orange,#f59e0b);border:1px solid currentColor;border-radius:6px;padding:1px 5px;vertical-align:1px">ON-DEMAND</span>':''}</td>
        <td class="num">$${fmt(r.profit_amount)}</td><td class="num">$${fmt(r.total??r.trader_share)}</td>
        <td class="muted">${esc(payoutMethodLabel(r.method,r.network))}${r.vusdt?`<div style="font-size:11px">${
          r.vusdt.explorer_url?`<a href="${esc(r.vusdt.explorer_url)}" target="_blank" rel="noopener">${esc(r.vusdt.status)} · view tx ↗</a>`
          :esc(r.vusdt.status)}</div>`:''}</td>
        <td><span class="status ${r.status==='paid'?'paid':r.status==='unlocked'?'active'
          :(r.status==='pending'||r.status==='review')?'pending':'failed'}"><span class="dot"></span>${
          esc(r.status==='review'?'under review':r.status==='unlocked'?'approved':r.status==='pending'?(r.method==='vusdt'?'sending':'processing'):r.status)}</span>
          ${r.status==='unlocked'?`<div class="muted" style="font-size:11px">Add your payment details above</div>`:''}
          ${r.status==='rejected'&&r.reject_reason?`<div class="muted" style="font-size:11px;max-width:220px">${esc(r.reject_reason)}</div>`:''}</td></tr>`).join('')+`
      </tbody></table></div>`
      :`<div class="empty"><h3>No payout requests yet</h3><p>Pass a challenge, get funded and request your first performance reward. On 2-Step plans the challenge fee is credited with it.</p></div>`}

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
          <td class="muted">${esc(payoutMethodLabel(h.method))}</td>
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
         We e-mail you once your documents are reviewed, and the dashboard opens the moment they're approved.</p>
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
          <div><h3 style="margin:0">${esc(t.subject)}</h3>
            ${/* Ten sam znak, ktory klient ma w temacie maila — po to, zeby
                  wiedzial, ze trafil na wlasciwa sprawe. */''}
            ${t.ref?`<span class="muted" style="font-size:12px">#${esc(t.ref)}</span>`:''}</div>
          <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${t.status}</span>
        </div>
        ${chatHtml(t.thread,{me:'trader',them:'Support team'})}
        ${t.status!=='closed'?`<div class="chat-dock">${chatCompose({id:'t-reply',send:`replyTicket(${t.id})`,
          placeholder:'Message support…'})}</div>`
          :'<p class="chat-closed">This ticket is closed. Create a new one if you need more help.</p>'}
      </div>`;
    /* Jak w komunikatorze: rozmowa otwiera się na OSTATNIEJ wiadomości. */
    requestAnimationFrame(()=>scrollTo(0,document.documentElement.scrollHeight));
    return;
  }
  const rows=await api('/api/me/tickets');
  const head=`<div style="display:flex;justify-content:flex-end;margin-bottom:14px">
    <button class="btn-p sm" onclick="window._ticketView='new';go('support')">+ New Ticket</button></div>`;
  if(!rows.length){
    $('view').innerHTML=head+`<div class="empty">
      ${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:38px;height:38px;margin:0 auto 14px;display:block;color:var(--dim)"><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.8A8 8 0 1 1 21 12z"/></svg>'}
      <h3>No Tickets Yet</h3><p>Need help with anything? Open a ticket and our team will get back to you.</p>
      <button class="btn-p" onclick="window._ticketView='new';go('support')">Create Your First Ticket</button></div>`;
    return;
  }
  $('view').innerHTML=head+`<div class="tbl-wrap">`+rows.map(t=>`
    <div class="ticket-row" onclick="window._ticketView=${t.id};go('support')">
      <div class="tile-ic blue" style="width:36px;height:36px;flex:0 0 36px">${ICO.chat}</div>
      <div class="sub"><b>${esc(t.subject)}</b><span>${t.ref?`#${esc(t.ref)} · `:''}${t.messages} message${t.messages>1?'s':''} · updated ${dstr(t.last_ts)}</span></div>
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
          <div class="em-row"><input class="inp" value="${esc(ME.email)}" disabled>
            ${IMP?'':`<button type="button" class="btn-o" id="em-open" onclick="emailChangeOpen()">Change</button>`}</div>
          <div id="em-flow"></div></div>
        <button class="btn-p" style="align-self:flex-start" onclick="saveProfile()">Save Changes</button>
      </div></div>

    <div class="sec-card" style="max-width:640px"><h3>${ME.password_set===false?'Set a Password':'Change Password'}</h3>
      ${ME.password_set===false?`<p class="muted" style="font-size:12.5px;margin-top:6px">You signed up with Google. Set a password to also log in with your e-mail — “Continue with Google” keeps working.</p>`:''}
      <div style="display:flex;flex-direction:column;gap:11px;margin-top:12px">
        ${ME.password_set===false?'':`<input id="s-cur" type="password" class="inp" placeholder="Current password" autocomplete="current-password">`}
        <input id="s-new" type="password" class="inp" placeholder="New password (min. 8 characters)" autocomplete="new-password">
        <input id="s-new2" type="password" class="inp" placeholder="Confirm new password" autocomplete="new-password">
        <button class="btn-p" style="align-self:flex-start" onclick="savePassword()">${ME.password_set===false?'Set Password':'Change Password'}</button>
      </div></div>

    <div class="sec-card" style="max-width:640px"><h3>Payout Wallet</h3>
      <div id="pw-body" style="margin-top:10px"><p class="muted" style="font-size:13px;margin:0">Loading…</p></div></div>

    <div class="sec-card" style="max-width:640px"><h3>Appearance</h3>
      <div class="switch-row" style="border-bottom:0"><div><h4>Dark Mode</h4>
        <p>Switch the dashboard to a dark look. Saved on this device.</p></div>
        <label class="switch"><input type="checkbox" id="s-theme" ${themeNow()==='dark'?'checked':''}
          onchange="if((this.checked?'dark':'light')!==themeNow())toggleTheme()"><i></i></label></div>
    </div>

    <div class="sec-card" style="max-width:640px"><h3>Notification Preferences</h3>
      <div class="np-pr" data-push="${PUSH.st}" style="margin-top:12px">${npPushInner()}</div>
      <div data-np-push-help></div>
      <div style="margin-top:14px">${unCatsHtml()}</div>
      <p class="muted" style="font-size:12px;margin-top:8px">Turning a category off stops its email, push and bell notifications.
        Essential e-mails (verification, MT5 credentials) always arrive.</p>
    </div>

    <div class="sec-card" style="max-width:640px;border-color:var(--red-line)"><h3 style="color:var(--red)">Danger Zone</h3>
      <p class="muted" style="font-size:13px;margin:8px 0 14px">Deleting your account anonymizes your personal data and permanently disables login.
        Trading records are retained for accounting. This cannot be undone.</p>
      <div id="s-del" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="deleteCode()">Delete Account</button>
        <span class="muted" style="font-size:12.5px">We e-mail you a 6-digit code first.</span>
      </div></div>
    </div>`;
  npPaintPush();refreshPush();pwLoad();
 },
};

/* ---------- view actions ---------- */
function goAffiliate(){go('rewards')}

function openJournalModal(){
  api('/api/me/accounts').then(accs=>{
    const box=document.createElement('div');
    box.id='j-modal'; box.className='modal-wrap';
    box.innerHTML=`<div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
      <div class="modal-head"><h3>New Journal Entry</h3>
        <button class="icon-btn" aria-label="Close" onclick="document.getElementById('j-modal').remove()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
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
  }).catch(e=>{toast('Error: '+e.message,'err')});
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
    window._ticketView=null;toast('Ticket created. Our team will get back to you here.','ok');go('support');
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
/* Zmiana e-maila w dwóch krokach: nowy adres (+ hasło, jeśli konto je ma)
   → kod 6 cyfr na OBECNY adres → wpisanie kodu. Stary adres potwierdza
   zmianę, bo to on dowodzi, że robi ją właściciel konta. */
let _emNowy='',_emHaslo='';
function emailChangeOpen(){
  const f=$('em-flow');if(!f)return;
  $('em-open')?.classList.add('hidden');
  /* Konto z Google: najpierw hasło w PTF, potem zmiana adresu (to samo
     pilnuje serwer). Przycisk prowadzi prosto do karty „Set a Password". */
  if(ME.password_set===false){
    f.innerHTML=`<div class="em-step a-in">
      <p class="em-hint">You signed up with Google. <b>Set a password first</b> — then you can change your e-mail,
        and you'll be able to log in with the new address and that password.</p>
      <div class="em-btns"><button type="button" class="btn-p" onclick="emailChangeClose();$('s-new').scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>$('s-new').focus(),350)">Set a password</button>
        <button type="button" class="btn-o" onclick="emailChangeClose()">Cancel</button></div></div>`;
    return;
  }
  f.innerHTML=`<div class="em-step a-in">
    <input id="em-new" type="email" class="inp" placeholder="New e-mail address" autocomplete="email" value="${esc(_emNowy)}">
    <input id="em-pass" type="password" class="inp" placeholder="Your current password" autocomplete="current-password">
    <p class="muted em-hint">We'll send a 6-digit code to <b>${esc(ME.email)}</b> to confirm it's you.</p>
    <div class="em-btns"><button type="button" class="btn-p" onclick="emailChangeSend(this)">Send code</button>
      <button type="button" class="btn-o" onclick="emailChangeClose()">Cancel</button></div></div>`;
  $('em-new').focus();
}
function emailChangeClose(){
  _emNowy=_emHaslo='';
  const f=$('em-flow');if(f)f.innerHTML='';
  $('em-open')?.classList.remove('hidden');
}
async function emailChangeSend(btn,ponownie){
  if(btn.disabled)return;
  if(!ponownie){_emNowy=($('em-new').value||'').trim();_emHaslo=$('em-pass')?$('em-pass').value:''}
  if(!_emNowy){toast('Enter the new e-mail address.','err');return}
  const napis=btn.textContent;btn.disabled=true;btn.textContent='Sending…';
  try{
    const r=await api('/api/me/email/start',{method:'POST',body:JSON.stringify({new_email:_emNowy,password:_emHaslo})});
    _emNowy=r.new_email;
    $('em-flow').innerHTML=`<div class="em-step a-in">
      <p class="em-hint">Code sent to <b>${esc(r.sent_to)}</b>. Enter it to switch your login to <b>${esc(r.new_email)}</b>.</p>
      <input id="em-code" class="inp em-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••"
        oninput="this.value=this.value.replace(/\\D/g,'').slice(0,6);if(this.value.length===6)emailChangeConfirm($('em-go'))">
      <div class="em-btns"><button type="button" class="btn-p" id="em-go" onclick="emailChangeConfirm(this)">Confirm</button>
        <button type="button" class="btn-o" onclick="emailChangeSend(this,1)">Resend</button>
        <button type="button" class="btn-o" onclick="emailChangeClose()">Cancel</button></div>
      <p class="muted em-hint">Valid for ${r.minutes} minutes. Check spam if it doesn't arrive.</p></div>`;
    $('em-code').focus();
    if(ponownie)toast('New code sent.','ok');
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false;btn.textContent=napis}
}
async function emailChangeConfirm(btn){
  if(!btn||btn.disabled)return;
  const kod=($('em-code').value||'').trim();
  if(kod.length!==6){toast('Enter the 6-digit code.','err');return}
  btn.disabled=true;btn.textContent='Checking…';
  try{
    const r=await api('/api/me/email/confirm',{method:'POST',body:JSON.stringify({code:kod})});
    _emNowy=_emHaslo='';
    ME=await api('/api/auth/me');
    toast('E-mail changed to '+r.email+'. Use it to log in from now on.','ok',8000);
    go('settings');
  }catch(e){
    toast('Error: '+e.message,'err');btn.disabled=false;btn.textContent='Confirm';
    if(/start the e-mail change again/i.test(e.message))emailChangeOpen();
  }
}
async function savePassword(){
  if($('s-new').value!==$('s-new2').value){toast('Passwords do not match.','err');return}
  try{const pierwsze=ME&&ME.password_set===false;
    const r=await api('/api/me/password',{method:'POST',body:JSON.stringify({
    current_password:$('s-cur')?$('s-cur').value:'',new_password:$('s-new').value})});
    if(ME)ME.password_set=true;
    /* Older sessions just died (password fingerprint in the token) — swap in
       the fresh token so THIS session survives the change. W podglądzie admina
       NIE dotykamy localStorage: nadpisałby token właściciela. */
    if(r.token&&!IMP){TOKEN=r.token;localStorage.setItem('pf_token',TOKEN)}
    toast(pierwsze?'Password set. You can now log in with your e-mail too.':'Password changed.','ok');go('settings');
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
/* Deleting the account: a code from the e-mail instead of the password, because an
   account opened with Google has no password the trader knows. */
async function deleteCode(){
  const box=$('s-del');
  box.querySelector('button').disabled=true;
  try{const r=await api('/api/me/delete/code',{method:'POST'});
    box.innerHTML=`<input id="s-delcode" class="inp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6-digit code" style="max-width:160px;letter-spacing:.2em">
      <button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="deleteAccount()">Confirm deletion</button>
      <span class="muted" style="font-size:12.5px">Code sent to ${esc(r.email)}. It works for 10 minutes. <a href="#" onclick="deleteCode();return false">Send again</a></span>`;
    $('s-delcode').focus();
  }catch(e){box.querySelector('button').disabled=false;toast('Error: '+e.message,'err')}
}
async function deleteAccount(){
  const code=($('s-delcode')||{}).value||'';
  if(code.replace(/\D/g,'').length!==6){toast('Enter the 6-digit code from the e-mail.','err');return}
  if(!confirm('Delete your account for good? This cannot be undone.'))return;
  try{await api('/api/me/delete',{method:'POST',body:JSON.stringify({code:code.replace(/\D/g,'')})});
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
  box.innerHTML=`<div class="inv-print" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
    <div class="inv-head">
      <div style="display:flex;align-items:center;gap:10px"><img src="/static/img/logo.png" alt="">
        <div><div class="inv-h1">${esc(SITE_NAME)}</div><div style="font-size:11px;color:#64748b">Trading skills evaluation platform</div>
          ${/* Seller identity: printed only once the operator details are configured. */''}
          ${(typeof LEGAL_SELLER!=='undefined'&&LEGAL_SELLER.name)?`<div style="font-size:11px;color:#64748b;margin-top:4px">${esc(LEGAL_SELLER.name)}${LEGAL_SELLER.address?'<br>'+esc(LEGAL_SELLER.address):''}${LEGAL_SELLER.reg_no?'<br>'+esc(LEGAL_SELLER.reg_no):''}${LEGAL_SELLER.tax_id?'<br>'+esc(LEGAL_SELLER.tax_id):''}</div>`:''}</div></div>
      <div class="inv-meta"><b>INVOICE #${o.id}</b><br>${date}<br>Status: ${esc(o.status)}</div>
    </div>
    <div style="font-size:12.5px;color:#64748b">Billed to<br><b style="color:#0f172a">${esc(ME.full_name||ME.email)}</b><br>${esc(ME.email)}</div>
    <table class="inv-tbl">
      <thead><tr><th>Description</th><th style="text-align:right">Amount</th></tr></thead>
      <tbody>${invRows(o)}</tbody>
    </table>
    <div class="inv-total"><span>Total</span><span>$${fmt(orderAmount(o))} USD</span></div>
    <div class="inv-foot">One-time fee for access to a simulated trading evaluation on a demo account with virtual funds.
      No real-market trading service is provided. On 2-Step plans the fee is credited with the first payout as vUSDT under the Refund Policy.
      Terms, Refund Policy and Right of Withdrawal: version ${esc(typeof LEGAL_DOCS_V!=='undefined'?LEGAL_DOCS_V:'')}.</div>
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
      Opening your account on <b>MetaQuotes-Demo</b>. Credentials appear here and in your inbox once it is ready.</div>`;
  }
  const row=(label,val)=>`
    <div class="cred-row">
      <span class="k">${label}</span>
      <span class="v"><code>${esc(val)}</code>
        <button class="copy" onclick="event.stopPropagation();copyVal(this,'${jsq(val)}')" title="Copy">${ICO.copy}</button>
      </span>
    </div>`;
  return `
    <div class="creds" onclick="event.stopPropagation()">
      <div class="creds-h">${ICO.key} MT5 credentials</div>
      ${row('Server', a.platform_server||'—')}
      ${row('Login', a.platform_login||'—')}
      ${row('Password', a.platform_password)}
      ${compact?'':(a.platform_server==='PTF-sim'
        /* A PTF-sim login exists only in our terminal: saying it works in the
           MetaTrader apps would send the customer to a login that always fails. */
        ?'<p style="font-size:11px;color:var(--dim);margin-top:6px">These credentials open the Built-in Terminal in your browser. They do not work in the MetaTrader 5 apps.</p>'
        :'<p style="font-size:11px;color:var(--dim);margin-top:6px">Works in MT5 desktop, mobile and web, so pick this server name. The same login and password open the Built-in Terminal.</p>')}
      <a class="creds-term" href="/trade" onclick="event.stopPropagation();try{localStorage.setItem('pf_term_prefill','${jsq(a.platform_login||'')}');localStorage.setItem('pf_term_prefill_srv','${jsq(a.platform_server||'')}')}catch(e){}">Open Built-in Terminal</a>
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
    <div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
      <div class="modal-head">
        <h3>${esc(p.label)}</h3>
        <button class="icon-btn" onclick="closeBuy()" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
      </div>
      <div class="plan-line">
        Simulated capital <span id="buy-cap">$${fmt0(pfPromo()&&p.promo_upgrade_size?p.promo_upgrade_size:p.account_size)}</span> · split <span id="buy-split">${p.profit_split_pct + (buyAddonsFromShop().split_boost ? (p.split_boost_pp||10) : 0)}%</span>
      </div>
      ${p.promo_upgrade_size?`<div class="plan-promo" id="buy-promo" ${pfPromo()?'':'hidden'}><b>Upgrade Your Size</b> — you pay for the
        $${fmt0(p.account_size)} plan and we create a <b>$${fmt0(p.promo_upgrade_size)}</b> account.</div>`:''}
      <div class="quote-box" id="buy-quote"></div>
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
        ${ME.credits_usd>0?`<label class="buy-addon buy-addon-full">
          <input type="checkbox" id="c-usecr" checked onchange="quoteRefresh(true)">
          <span><b>Use my store credit</b><small>$${fmt(ME.credits_usd)} available · 1 credit = $1</small></span>
        </label>`:''}
        <div class="buy-consent" id="buy-consent">
          <h4 class="buy-consent-h">Before you purchase</h4>
          <label class="buy-consent-item">
            <input type="checkbox" id="c-consent-terms" onchange="syncBuyConsent()">
            <span class="bc-body">I agree to the <a href="/terms" target="_blank" rel="noopener">Terms</a> and <a href="/refund-policy" target="_blank" rel="noopener">Refund Policy</a>.</span>
          </label>
          <label class="buy-consent-item">
            <input type="checkbox" id="c-consent-risk" onchange="syncBuyConsent()">
            <span class="bc-body">I understand that evaluations are simulated, that rewards are not guaranteed, and I have read the <a href="/risk-disclosure" target="_blank" rel="noopener">Risk Disclosure</a>.</span>
          </label>
          <label class="buy-consent-item">
            <input type="checkbox" id="c-consent-immediate" onchange="syncBuyConsent()">
            <span class="bc-body">I request immediate commencement and, where applicable, lose the <a href="/withdrawal" target="_blank" rel="noopener">right of withdrawal</a> once the service is fully performed, which is when my MT5 credentials are delivered.</span>
          </label>
          <p class="buy-privacy">Data processed per our <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>.</p>
        </div>
        <p id="buy-err" class="form-err hidden"></p>
        <button id="buy-go" onclick="buy('${jsq(p.key)}')" class="btn-p lg" style="width:100%" disabled>Buy &amp; create my account</button>
        <p class="hint">${p.steps===0?'One-time payment':'One-time payment · fee credited with your first payout'}</p>
      </div>
    </div>`;
  box.onclick=closeBuy;
  document.body.appendChild(box);
  window._buyKey=key;
  window._buyCode={coupon:null,promo:null};
  ccSet(kraj, false);
  hydrateBuyCode();
  syncBuyConsent();
  setTimeout(()=>$('c-first').focus(),50);
}

/* Checkout consent panel: all three declarations must be ticked before Buy. */
const BUY_CONSENT_V=(typeof LEGAL_DOCS_V!=='undefined'&&LEGAL_DOCS_V)||'checkout-consent-2026-11';
function buyConsentsOk(){
  return !!($('c-consent-terms')&&$('c-consent-terms').checked)
    && !!($('c-consent-risk')&&$('c-consent-risk').checked)
    && !!($('c-consent-immediate')&&$('c-consent-immediate').checked);
}
function syncBuyConsent(){
  const btn=$('buy-go'); if(!btn)return;
  btn.disabled=!buyConsentsOk();
}

/* Add-ons are chosen on the shop strip (like LP), not in the buy modal. */
function buyAddonsFromShop(){
  const p=PRODUCTS.find(x=>x.key===window._buyKey);
  const instant=p&&p.steps===0;
  const sbOk=instant&&!!(p&&p.split_boost_offered)&&!!window._shopSb;
  return {
    weekend: !!window._shopWt,
    ondemand: !!window._shopOd,
    copytrading: !!window._shopCt,
    split_boost: sbOk,
  };
}

/* Price breakdown in the buy modal, fed by /api/checkout/preview — the exact
   math the real checkout will run (coupon -> weekend add-on -> store credit).
   Display only: the server recomputes everything again at POST /api/checkout. */
let _quoteT=null;
function quoteRefresh(now){clearTimeout(_quoteT);_quoteT=setTimeout(quoteNow,now===true?0:400)}
async function quoteNow(){
  const p=PRODUCTS.find(x=>x.key===window._buyKey),box=$('buy-quote');
  if(!p||!box)return;
  const a=buyAddonsFromShop();
  const wk=a.weekend, sb=a.split_boost, od=a.ondemand, cp=a.copytrading;
  const uc=!$('c-usecr')||$('c-usecr').checked;
  const odCena=p.steps===0?399:299;
  const bc=window._buyCode||{};
  let q=null,previewFailed=false;
  try{
    q=await api('/api/checkout/preview?product_key='+encodeURIComponent(p.key)
      +'&coupon='+encodeURIComponent(bc.coupon||'')
      +'&promo_code='+encodeURIComponent(bc.promo||'')
      +'&weekend='+(wk?'1':'0')+'&split_boost='+(sb?'1':'0')
      +'&ondemand='+(od?'1':'0')+'&copytrading='+(cp?'1':'0')
      +'&use_credits='+(uc?'1':'0'));
  }catch(e){
    /* The preview must never block buying — fall back to the catalog price. */
    previewFailed=true;
    if(e&&e.message&&/coupon|personal|expired|already been used/i.test(e.message)){
      buyErr(e.message);
      try{localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct')}catch(_){}
      window._shopCode=''; window._buyCode={...(window._buyCode||{}),coupon:null};
    }
    q={plan_price_usd:p.price_usd,discount_pct:0,discount_usd:0,
       weekend_fee_usd:wk?199:0,split_boost_fee_usd:sb?149:0,
       ondemand_payout_fee_usd:od?odCena:0,copytrading_fee_usd:cp?299:0,credits_used:0,
       total_due_usd:Math.round((p.price_usd+(wk?199:0)+(sb?149:0)+(od?odCena:0)+(cp?299:0))*100)/100};
  }
  /* A coupon the server does not recognize changes nothing — say so instead
     of quietly showing the full price (promo codes are confirmed separately). */
  if(previewFailed&&bc.coupon){
    /* Keep the server's reason (e.g. personal/expired) — don't overwrite with a generic line. */
  }else if(!previewFailed&&bc.coupon&&!(q.discount_usd>0)){
    buyErr('This code is not valid right now. You can still buy without it.');
    try{localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct')}catch(e){}
    window._shopCode=''; window._buyCode={coupon:null,promo:null};
  }else if(!previewFailed){const e=$('buy-err'); if(e)e.classList.add('hidden')}
  const row=(l,v,cls)=>`<div class="q-row${cls?' '+cls:''}"><span>${l}</span><b class="mono">${v}</b></div>`;
  let h=row('Plan fee','$'+fmt(q.plan_price_usd));
  if(q.discount_usd>0)h+=row(q.discount_source==='offer'?(q.offer_title||`Flash sale (−${q.discount_pct}%)`):`Coupon (−${q.discount_pct}%)`,'−$'+fmt(q.discount_usd),'good');
  if(q.weekend_fee_usd>0)h+=row('Weekend trading','+$'+fmt(q.weekend_fee_usd));
  if(q.split_boost_fee_usd>0)h+=row('Profit Split Boost','+$'+fmt(q.split_boost_fee_usd));
  if(q.ondemand_payout_fee_usd>0)h+=row('On-demand payouts','+$'+fmt(q.ondemand_payout_fee_usd));
  if(q.copytrading_fee_usd>0)h+=row('EA Allowed','+$'+fmt(q.copytrading_fee_usd));
  if(q.credits_used>0)h+=row('Store credit','−$'+fmt(q.credits_used),'good');
  h+=`<div class="q-row total"><span>Total due</span><b class="mono" id="buy-total">$${fmt(q.total_due_usd)}</b></div>`;
  box.innerHTML=h;
}
function closeBuy(){document.getElementById('buy-modal')?.remove()}

/* Coupon lives on the shop strip (like LP). A /api/promo hit is the
   "Upgrade Your Size" promo; anything else is a discount coupon. Server
   re-validates at checkout — this only feeds the buy modal quote. */
function shopCodeMsg(kind,html){
  window._shopCodeMsg=kind?{kind,html}:null;
  const el=$('shop-codemsg'), inp=$('shop-coupon');
  if(inp){inp.classList.toggle('ok',kind==='ok');inp.classList.toggle('bad',kind==='bad')}
  if(!el)return;
  if(!kind){el.hidden=true;el.innerHTML='';return}
  el.hidden=false; el.className='code-msg chal-msg '+kind;
  el.innerHTML=`<span>${kind==='ok'?'✓':'✕'}</span><span>${html}</span>`;
}
async function shopValidateCode(code){
  try{if((await api('/api/promo?code='+encodeURIComponent(code))).valid)return{promo:code,coupon:null,pct:0}}catch(e){}
  try{
    const d=await api('/api/coupon/'+encodeURIComponent(code));
    if(d&&d.pct)return{promo:null,coupon:d.code||code,pct:d.pct};
  }catch(e){
    return{error:(e&&e.message)||'This code cannot be used right now.'};
  }
  return null;
}
async function shopApplyCode(){
  const inp=$('shop-coupon'); if(!inp)return;
  const code=(inp.value||'').trim().toUpperCase();
  inp.value=code;
  if(!code){
    window._shopCode='';
    try{localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct');localStorage.removeItem('pf_promo_code')}catch(e){}
    shopCodeMsg(null); return;
  }
  const state=await shopValidateCode(code);
  if(!state||state.error){
    window._shopCode='';
    try{localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct')}catch(e){}
    shopCodeMsg('bad', state&&state.error
      ? `<b>${esc(code)}</b> — ${esc(state.error)}`
      : `<b>${esc(code)}</b> is not a valid code.`);
    return;
  }
  window._shopCode=code;
  try{
    if(state.promo){localStorage.setItem('pf_promo_code',code);localStorage.removeItem('pf_coupon_code');localStorage.removeItem('pf_coupon_pct')}
    else{localStorage.setItem('pf_coupon_code',state.coupon);localStorage.setItem('pf_coupon_pct',String(state.pct||0));localStorage.removeItem('pf_promo_code')}
  }catch(e){}
  shopCodeMsg('ok', state.promo
    ? `<b>${esc(code)}</b> applied: pay this size, trade one tier up.`
    : `<b>${esc(state.coupon)}</b> applied: <b>${state.pct}% off</b> your challenge fee.`);
}
async function hydrateBuyCode(){
  const p=PRODUCTS.find(x=>x.key===window._buyKey);
  const code=(new URLSearchParams(location.search).get('coupon')
    ||window._shopCode||($('shop-coupon')&&$('shop-coupon').value)||pfCoupon()||pfPromo()||'').trim().toUpperCase();
  if(!p){window._buyCode={coupon:null,promo:null};return}
  if(!code){
    window._buyCode={coupon:null,promo:null};
    const cap=$('buy-cap'); if(cap)cap.textContent='$'+fmt0(p.account_size);
    const line=$('buy-promo'); if(line)line.hidden=true;
    quoteRefresh(true); return;
  }
  let promo=false;
  if(p.promo_upgrade_size){
    try{promo=(await api('/api/promo?code='+encodeURIComponent(code))).valid}catch(e){}
  }
  window._buyCode=promo?{coupon:null,promo:code}:{coupon:code,promo:null};
  try{
    if(promo)localStorage.setItem('pf_promo_code',code);
    else{localStorage.setItem('pf_coupon_code',code);localStorage.removeItem('pf_promo_code')}
  }catch(e){}
  const cap=$('buy-cap'); if(cap)cap.textContent='$'+fmt0(promo?p.promo_upgrade_size:p.account_size);
  const line=$('buy-promo'); if(line)line.hidden=!promo;
  quoteRefresh(true);
}
async function codeCheck(){return hydrateBuyCode()}

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
  if(!buyConsentsOk()){
    buyErr('Please accept the required declarations before purchasing.');
    syncBuyConsent();
    return;
  }
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
      const a=buyAddonsFromShop();
      const res=await api('/api/checkout',{method:'POST',
        body:JSON.stringify({product_key:key,coupon,promo_code:promo,first_name:first,last_name:last,phone,phone_country:CC,
          weekend_trading:a.weekend,
          split_boost:a.split_boost,
          ondemand_payout:a.ondemand,
          copytrading:a.copytrading,
          use_credits:!$('c-usecr')||$('c-usecr').checked,
          consent_terms:true,consent_risk:true,consent_immediate:true,
          consent_version:BUY_CONSENT_V})});
      /* real Stripe: strona zaraz znika — przycisk ma zostać wyłączony */
      if(res.checkout_url && !res.mock){window.location=res.checkout_url;return 'keep'}
      let prov=res;
      if(res.mock){prov=await api(`/api/checkout/${res.order_id}/mock-complete`,{method:'POST'});}
      ME=await api('/api/auth/me');
      closeBuy();
      if(prov.provisioning){
        toast('✅ Payment received.\nYour MT5 demo account is being created.\nCredentials arrive by e-mail and under Challenges.','ok',9000);
      }else{
        toast(`✅ Account created!\nServer: ${prov.platform_server||'—'}\nLogin: ${prov.platform_login}\nPassword: ${prov.platform_password}\n(also sent by e-mail)`,'ok',12000);
      }
      go('accounts');
    }catch(e){buyErr('Error: '+e.message)}
  });
  syncBuyConsent();   /* busy() re-enables the button — restore consent gating */
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

/* Podsumowanie zakonczonej fazy. Do dzis oblane konto dostawalo jednego maila
   "rule breached" i pasek z powodem — tu trader dostaje liczby z TEJ fazy
   i jedno zdanie, ktore je czyta. Bez konfetti przy zdanej i bez "niewiele
   brakowalo" przy oblanej: obie wersje opisuja to, co juz sie stalo. */
const BREACH_LABEL={daily_loss:'Daily loss limit',max_drawdown:'Max drawdown',
  time_limit:'Time limit',max_lots:'Open volume limit',manual:'Account review',rule:'Rule'};
const RECAP_DATE=iso=>dutc(iso).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});

function recapSub(a,d){
  const kiedy=RECAP_DATE(d.to);
  if(d.outcome!=='failed')return `${PHASE_LABEL[d.phase]||esc(d.phase)} closed ${kiedy} after ${d.trading_days} trading day${d.trading_days===1?'':'s'}`;
  const b=d.breach;
  if(!b)return `Ended ${kiedy}`;
  const nazwa=BREACH_LABEL[b.type]||'Rule';
  const m=a.metrics||{};
  const prog=b.type==='daily_loss'?m.daily_floor:b.type==='max_drawdown'?m.overall_floor:null;
  const gdzie=b.equity!=null?` at $${fmt(b.equity)}`:'';
  return `Ended ${kiedy} — ${nazwa.toLowerCase()} crossed${gdzie}${prog?` (floor $${fmt(prog)})`:''}`;
}

function rp(label,val,cls){
  return `<div class="rp"><span>${label}</span><b${cls?` class="${cls}"`:''}>${val}</b></div>`;
}

function recapCardHtml(a,d){
  if(!d||!d.available)return '';
  const zdana=d.outcome==='passed';
  /* Konto fundowane tez potrafi sie skonczyc na zlamaniu reguly, a wtedy nie ma
     "fazy do powtorzenia" — jest zamkniete konto z wyplatami w historii. */
  const fundowane=!zdana&&d.phase==='funded';
  const dni=Math.max(1,d.days_active);
  const cele=d.target_pct
    ?rp('To target',`${d.net_pct>=0?'+':''}${d.net_pct.toFixed(1)}% / ${d.target_pct}%`,
        d.net_pct>=d.target_pct?'ok':'')
    :rp('Return',`${d.net_pct>=0?'+':''}${d.net_pct.toFixed(1)}%`,d.net_pct>=0?'ok':'bad');
  const stopka=zdana
    ?(a.phase==='funded'
        ?'This account is funded now. The recap above covers the evaluation that just ended — balance, drawdown floors and the trading-day counter all restarted with the funded phase.'
        :'Phase 2 is running on the same account. Balance, drawdown floors and the trading-day counter all restarted, so the numbers above stay here as history.')
    :fundowane
      ?'This funded account is closed. Payouts already approved stay in your history and in Payouts — the recap above covers the funded run only.'
      :`You can run ${PHASE_LABEL[d.phase]||'this phase'} again on a new account. Nothing carries over — the recap above stays in your history.`;
  return `<div class="sec-card recap ${zdana?'ok':'bad'}" id="recap">
    <div class="rc-head">
      <div>
        <div class="rc-eyebrow">${zdana?`${PHASE_LABEL[d.phase]||esc(d.phase)} passed`:fundowane?'Account closed':'Challenge ended'}</div>
        <div class="recap-title">Account ${esc(d.login)} · ${PHASE_LABEL[d.phase]||esc(d.phase)} · ${dni} day${dni===1?'':'s'}</div>
        <div class="rc-sub">${esc(recapSub(a,d))}</div>
      </div>
      <div style="text-align:right">
        <div class="rc-pnl ${d.net_pnl>=0?'up':'down'}">${money(d.net_pnl)}</div>
        <div class="rc-sub">net over the phase</div>
      </div>
    </div>
    <div class="recap-grid">
      ${rp('Trading days',`${d.trading_days}${d.min_trading_days&&d.phase!=='funded'?`<small> / ${d.min_trading_days} min</small>`:''}`)}
      ${/* Konta czytane z MT5 nie maja wierszy transakcji — feed oddaje samo
           equity. "Trades 0 / Win rate —" wygladaloby wtedy jak zepsuta karta,
           wiec te dwa pola ustepuja miejsca bilansowi dni. */
        d.trades
        ?rp('Trades',d.trades)+rp('Win rate',d.win_rate!=null?`${d.win_rate}%`:'—')
        :rp('Green days',d.green_days,d.green_days?'ok':'')+rp('Red days',d.red_days,d.red_days?'bad':'')}
      ${rp('Best day',d.best_day?money(d.best_day.pnl):'—',d.best_day&&d.best_day.pnl>0?'ok':'')}
      ${rp('Worst day',d.worst_day?money(d.worst_day.pnl):'—',d.worst_day&&d.worst_day.pnl<0?'bad':'')}
      ${cele}
    </div>
    ${d.diagnosis?`<div class="recap-read">
      <span class="rr-ic">${ICO.bars}</span>
      <div><b>What the numbers say</b><p>${esc(d.diagnosis.text)}</p></div>
    </div>`:''}
    <div class="recap-foot">
      <div class="rf-txt">${stopka}</div>
      ${zdana?'':`<button class="btn-p" onclick="go('store')">Start a new challenge</button>`}
    </div>
  </div>`;
}

async function openAcc(id){
  $('pg-title').textContent='Account Dashboard'; $('pg-crumb').textContent='Trader / Client Area / '+id;
  /* Karta konta to podwidok Challenges. Z listy trafia tu po go('accounts'),
     ale z deep linka powiadomienia — prosto, i wtedy nawigacja zostawala bez
     zaznaczonej pozycji. */
  document.querySelectorAll('.sb-link[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v==='accounts'));
  document.querySelectorAll('.tab-item[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v==='accounts'));
  $('view').innerHTML='<div class="skel" style="height:110px;margin-bottom:16px"></div><div class="skel" style="height:300px"></div>';
  let a,act,pos,rc;
  try{
    [a,act,pos,rc]=await Promise.all([
      api('/api/me/accounts/'+id),
      api(`/api/me/accounts/${id}/activity`),
      api(`/api/me/accounts/${id}/positions`).catch(()=>[]),
      /* Podsumowanie fazy nie moze wywrocic karty konta — na starych kontach
         bez dat awansu endpoint po prostu nie ma czego policzyc. */
      api(`/api/me/accounts/${id}/recap`).catch(()=>({available:false})),
    ]);
  }catch(e){
    $('view').innerHTML=`<div class="card" style="text-align:center;padding:34px 18px">
      <p class="muted" style="margin:0 0 14px">Couldn't load this account — check your connection.</p>
      <button class="btn-p" onclick="openAcc(${id})">Try again</button></div>`;
    return;
  }
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
  /* Po awansie liczby konta opisują bieżącą fazę od zera, więc i data startu
     jest datą startu fazy (Phase 2 / Funded), a nie założenia konta. */
  const fazaOd=(a.phases&&a.phases.length>1&&a.phase_started_at)?a.phase_started_at:a.created_at;
  const started=fazaOd?dutc(fazaOd).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}):'—';
  const startLbl=(a.phases&&a.phases.length>1&&a.phase_started_at)?((a.phases.find(f=>f.current)||{}).label||'Phase')+' started':'Start';
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

    ${recapCardHtml(a,rc)}

    <div class="detail-grid">
      <div class="sec-card" style="margin-bottom:0">
        <div class="res-head"><h3>Current Results</h3>
          <div class="seg-mini"><span>Objective lines</span>
            <span class="sw" data-slide data-slide-key="objl">
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
            <div class="v ${profitPct>=0?'up':'down'}">${reach.toFixed(1)}%</div></div>`
           :`<div class="res-stat"><div class="l">Return</div>
            <div class="v ${profitPct>=0?'up':'down'}">${profitPct>=0?'+':''}${profitPct.toFixed(2)}%</div></div>`}
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
        <div class="kv2"><span class="k">${startLbl}</span><span class="v">${started}</span></div>
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
        <div class="cred-cell"><div class="l">Platform</div><div class="v">MT5</div></div>
        ${credCell('Server',a.platform_server)}
        ${credCell('Login',a.platform_login)}
        ${a.platform_password?credCell('Password',a.platform_password):''}
        <div class="cred-cell"><div class="l">Leverage</div><div class="v" title="Forex 1:100, lower on metals, indices, energy and crypto">Up to 1:100</div></div>
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
      ${/* Powod zlamania niesie karta podsumowania na gorze — razem z liczbami,
            ktore do niego doprowadzily. Pasek zostaje tylko dla kont sprzed
            wprowadzenia dat fazy, gdzie podsumowania nie ma z czego zlozyc. */
        a.breach_reason&&!(rc&&rc.available)?`<div class="warn-box" style="margin:16px 0 0;background:var(--red-bg);border-color:var(--red-line);color:var(--red)">${ICO.alert}<div><b style="color:var(--red)">Rule breached</b>${esc(a.breach_reason)}</div></div>`:''}
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
      <button class="copy" onclick="copyVal(this,'${jsq(val)}')" title="Copy">${ICO.copy}</button>
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
/* Etykieta metody wypłaty. Pusta metoda = trader nie podał jeszcze danych
   płatniczych (krok 2), więc nie zgadujemy tokena — stąd „—". */
const PO_NET={TRC20:'TRC-20',BEP20:'BEP-20',POLYGON:'Polygon'};
function payoutMethodLabel(m,net){
  if(!m)return '—';
  if(m==='vusdt')return 'vUSDT · Polygon';
  if(m==='usdt')return 'USDT'+(net?' · '+(PO_NET[net]||net):'');
  if(m==='crypto')return 'USDT (crypto)';
  if(m==='wise')return 'Wise';
  if(m==='bank'||m==='bank transfer')return 'Bank transfer';
  return m.charAt(0).toUpperCase()+m.slice(1);
}
/* Przycisk wypłaty albo licznik dni, jeśli konto jeszcze nie dorosło do pierwszej.
   Instant jest funded od pierwszego dnia, więc bez tego liczydła jedyną informacją
   o 30 dniach był błąd z API — dopiero po wypełnieniu całego wniosku. */
function payoutBtn(a,av,cls,label){
  const left=a.payout_days_left||0, m=a.metrics||{}, o=a.payout_open;
  /* Otwarty wniosek ma pierwszeństwo przed oknem wypłat: on sam to okno
     zajmuje, a zatwierdzony przez admina czeka właśnie na ten przycisk. */
  if(o&&o.status==='unlocked')return `<button class="${cls.replace('btn-o','btn-p')}" onclick="openPayoutDetails(${o.id},${+o.amount||0},${+a.payout_fee_refund||0})">${
    esc(a.login)} · approved, add payment details</button>`;
  if(o)return `<button class="${cls}" disabled title="${o.status==='review'?'We are reviewing the account. You add your payment details once it is approved.':'Your payout is being processed.'}">${
    esc(a.login)} · ${o.status==='review'?'request under review':'payout in progress'}</button>`;
  /* Okno wypłat (PAYOUT_INTERVAL_DAYS): serwer i tak odrzuci wniosek przed
     datą, przycisk tylko mówi to wcześniej. */
  if(!left&&a.payout_next_at)return `<button class="${cls}" disabled title="Payouts open once per payout window on this account.">${
    esc(a.login)} · next payout ${new Date(a.payout_next_at).toLocaleDateString('en-US',{month:'short',day:'numeric'})}</button>`;
  return left
    ?`<button class="${cls}" disabled title="This plan unlocks its first payout after ${m.min_trading_days??0} trading days.">${
        esc(a.login)} · ${m.trading_days??0}/${m.min_trading_days??0} trading days</button>`
    :`<button class="${cls}" onclick="openPayoutModal(${a.id},${av.toFixed(2)},${+a.initial_balance||0})">${label}</button>`;
}
/* Minimalna wypłata = wyższa z: $500 i 2% wielkości konta (to samo pilnuje
   serwer w request_payout). */
const PO_MIN_USD=500, PO_MIN_PCT=2;
/* Krok 1: trader podaje samą kwotę. Admin ogląda konto, historię i KYC;
   dane płatnicze przychodzą dopiero po akceptacji (krok 2). Bez terminów —
   strona nie obiecuje, w jakim czasie zespół coś zrobi. */
function openPayoutModal(id,avail,size){
  const pct=Math.round((size||0)*PO_MIN_PCT)/100, min=Math.max(PO_MIN_USD,pct), max=Math.floor(+avail||0);
  const w=document.createElement('div'); w.id='po-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Request a payout</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 10px">Available on this account: <b>$${fmt(avail)}</b>, your split of current profit.</p>
    ${avail<min?`<div class="po-min bad" role="alert"><b>Minimum payout: $${fmt(min)}</b> on this account. Your available share is $${fmt(avail)}, so you can request a payout once it reaches $${fmt(min)}.</div>`
    :`<label class="muted" style="font-size:12px" for="po-amount">Amount (USD, whole dollars)</label>
    <div class="po-amt"><input id="po-amount" class="inp" type="number" inputmode="numeric" min="${min}" max="${max}" step="1" value="${max}"
      oninput="this.classList.remove('bad')"><button type="button" class="btn-o sm" onclick="$('po-amount').value=${max};$('po-amount').classList.remove('bad')">Max</button></div>
    <p class="muted" style="font-size:11.5px;margin:6px 0 2px">Minimum $${fmt0(min)} on this account.</p>`}
    <ol class="po-steps">
      <li><b>Request.</b> Choose the amount and send the request.</li>
      <li><b>Review.</b> Our risk team checks the account, its trading history and your verification.</li>
      <li><b>Approved.</b> Your reward is confirmed. You add your payment details, or confirm the saved ones, and the transfer starts.</li>
    </ol>
    <div style="display:flex;gap:10px;margin-top:14px">
      <button class="btn-p" onclick="submitPayoutAsk(${id},${min},${max})" id="po-send"${avail<min?' disabled':''}>Send request</button>
      <button class="btn-o" onclick="$('po-modal').remove()">Cancel</button>
    </div></div>`;
  document.body.appendChild(w);
  setTimeout(()=>$('po-amount')?.focus(),50);
}
async function submitPayoutAsk(id,min,max){
  const pole=$('po-amount'), amount=Number((pole?.value||'').trim().replace(',','.'));
  const zle=t=>{pole.classList.add('bad');pole.focus();toast(t,'err')};
  if(!Number.isFinite(amount)||amount!==Math.floor(amount))return zle('Enter a whole-dollar amount (no cents).');
  if(amount<min)return zle(`The minimum payout on this account is $${fmt0(min)}.`);
  if(amount>max)return zle(`The most you can request is $${fmt0(max)}.`);
  await busy($('po-send'),null,async()=>{
    try{await api(`/api/accounts/${id}/payout-request`,{method:'POST',body:JSON.stringify({amount})});
      $('po-modal').remove();
      toast('✅ Request sent. We e-mail you once it is approved, then you add your payment details.','ok',9000);
      go('payouts');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
/* Krok 2: dane płatnicze. Kwota jest już zatwierdzona. Pierwszy raz trader
   podaje portfel (i potwierdza, że go sprawdził); od drugiej wypłaty zapisany
   portfel jest podpowiadany, a inny wymaga kodu z maila.
   Przy VUSDT_TEST: tylko Polygon, captcha, token wychodzi od razu. */
const poAdresOk=(a,v)=>v?/^0x[0-9a-fA-F]{40}$/.test(a):a.length>=15;
async function openPayoutDetails(reqId,amount,refund){
  refund=+refund||0; amount=Math.floor(+amount||0);
  const v=!!VUSDT_TEST, tok=v?'vUSDT':'USDT';
  let wal={saved:false};
  try{wal=await api('/api/me/payout-wallet')}catch(_){}
  /* Zapisany portfel spoza trybu (np. TRC-20 przy vUSDT) nie nadaje się do tej
     wypłaty — trader i tak musi podać nowy, kodem, bo to zmiana zapisanego. */
  const zap=wal.saved&&poAdresOk(wal.address||'',v)?wal:null;
  const w=document.createElement('div'); w.id='po-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  const pola=`<label class="muted" style="font-size:12px" for="po-network">Network</label>
    <select id="po-network" class="inp" style="margin-bottom:10px">${v
      ?`<option value="POLYGON">Polygon</option>`
      :`<option value="TRC20">TRC-20 (Tron) — lowest fees</option>
      <option value="BEP20">BEP-20 (BNB Smart Chain)</option>
      <option value="POLYGON">Polygon</option>`}</select>
    <label class="muted" style="font-size:12px" for="po-address">${v?'Polygon wallet address':'USDT wallet address'}</label>
    <input id="po-address" class="inp mono" placeholder="${v?'0x…':'T… / 0x…'}" autocomplete="off" spellcheck="false" autocapitalize="off"
      oninput="this.classList.remove('bad')">
    <p class="muted" style="font-size:11.5px;margin:6px 0 0">Check the address and network carefully. A transfer sent to a wrong address cannot be reversed.</p>`;
  w.innerHTML=`<div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Your payout is approved</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 12px">Approved means your reward is confirmed. The transfer starts once you confirm where to send it.</p>
    <div class="po-sum">
      <span>Approved payout</span><b>$${fmt0(amount)}</b>
      ${refund?`<span>Challenge fee credit</span><b>+$${fmt(refund)}</b><span>You receive</span><b>$${fmt(amount+refund)} ${tok}</b>`
        :`<span>You receive</span><b>$${fmt0(amount)} ${tok}</b>`}
    </div>
    ${zap?`<div class="po-wsel" role="radiogroup" aria-label="Payout wallet">
      <label class="po-wopt"><input type="radio" name="po-w" value="saved" checked onchange="poWallet()">
        <span><b>Saved wallet</b><span class="mono">${esc(PO_NET[zap.network]||zap.network)} · ${esc(poShort(zap.address))}</span></span></label>
      <label class="po-wopt"><input type="radio" name="po-w" value="new" onchange="poWallet()">
        <span><b>A different wallet</b><span>We e-mail you a code to confirm the change.</span></span></label>
    </div>`:''}
    <div id="po-new"${zap?' hidden':''}>
      ${pola}
      ${wal.saved?`<div class="po-code">
          <button type="button" class="btn-o sm" id="po-codebtn" onclick="poSendCode()">E-mail me a code</button>
          <input id="po-code" class="inp mono" inputmode="numeric" maxlength="6" placeholder="6-digit code" autocomplete="one-time-code"
            oninput="this.classList.remove('bad')">
        </div>`
      :`<label class="ck"><input type="checkbox" id="po-ok" onchange="poWallet()"><span>I checked the address and the network. This wallet can receive ${tok}${v?' on Polygon':''}.</span></label>`}
    </div>
    ${v?`<div id="po-ts" class="po-ts"></div>`:''}
    <div style="display:flex;gap:10px;margin-top:14px">
      <button class="btn-p" onclick="submitPayoutDetails(${reqId})" id="po-send">${v?'Withdraw':'Confirm payment details'}</button>
      <button class="btn-o" onclick="$('po-modal').remove()">Later</button>
    </div></div>`;
  document.body.appendChild(w);
  poWallet();
  if(v)poTurnstile();
}
function poWallet(){
  const nowy=!document.querySelector('#po-modal input[name=po-w]')||document.querySelector('#po-modal input[name=po-w]:checked')?.value==='new';
  const box=$('po-new');if(box)box.hidden=!nowy;
  /* Pierwszy portfel: przycisk czeka na potwierdzenie, że adres jest sprawdzony. */
  const ok=$('po-ok');if($('po-send'))$('po-send').disabled=!!(nowy&&ok&&!ok.checked);
}
async function poSendCode(){
  const v=!!VUSDT_TEST, address=$('po-address').value.trim();
  if(!poAdresOk(address,v)){$('po-address').classList.add('bad');$('po-address').focus();
    toast(v?'Enter a Polygon wallet address: 0x followed by 40 characters.':'Enter your USDT wallet address.','err');return}
  await busy($('po-codebtn'),null,async()=>{
    try{await api('/api/me/payout-wallet/code',{method:'POST',body:JSON.stringify({network:$('po-network').value,address})});
      toast(`📧 We e-mailed a 6-digit code to ${ME.email}. Enter it to confirm the new wallet.`,'ok',8000);
      $('po-code').focus();
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function submitPayoutDetails(reqId){
  const v=!!VUSDT_TEST;
  const zapisany=document.querySelector('#po-modal input[name=po-w]:checked')?.value==='saved';
  let body;
  if(zapisany)body={use_saved:true};
  else{
    const address=$('po-address').value.trim();
    if(!poAdresOk(address,v)){$('po-address').classList.add('bad');$('po-address').focus();
      toast(v?'Enter a Polygon wallet address: 0x followed by 40 characters.':'Enter your USDT wallet address.','err');return}
    body={network:$('po-network').value,address};
    if($('po-code')){
      const code=$('po-code').value.trim();
      if(!/^\d{6}$/.test(code)){$('po-code').classList.add('bad');$('po-code').focus();
        toast('Enter the 6-digit code from the e-mail. Tap “E-mail me a code” first.','err');return}
      body.code=code;
    }else if(!$('po-ok')?.checked){toast('Confirm that you checked the address and the network.','err');return}
  }
  if(v){
    if(!PO_TS.token){toast('Complete the Cloudflare check above the button first.','err');return}
    body.turnstile_token=PO_TS.token;
  }
  await busy($('po-send'),null,async()=>{
    try{const r=await api(`/api/me/payout-requests/${reqId}/details`,{method:'POST',body:JSON.stringify(body)});
      if(r.vusdt){poSent(r.id,r.vusdt);return 'keep'}
      $('po-modal').remove();
      toast(`✅ Payment details confirmed: $${fmt(r.total??r.trader_share)} in USDT. We e-mail you once it is sent.`,'ok',8000);
      go('payouts');
    }catch(e){toast('Error: '+e.message,'err');if(v)poTurnstileReset()}
  });
}
/* Ustawienia: zapisany portfel. Dodaje się go przy pierwszej wypłacie;
   tu można go tylko zmienić — kodem z maila, jak w kroku 2. */
async function pwLoad(){
  const box=$('pw-body');if(!box)return;
  let wal;try{wal=await api('/api/me/payout-wallet')}catch(e){box.textContent='Could not load your wallet.';return}
  if(!wal.saved){box.innerHTML=`<p class="muted" style="font-size:13px;margin:0">Your wallet is added with your first approved payout and suggested for every payout after that.</p>`;return}
  box.innerHTML=`<div class="pw-row"><div><div class="muted" style="font-size:12px">${esc(PO_NET[wal.network]||wal.network)}</div>
      <div class="mono" style="font-size:13.5px;word-break:break-all">${esc(wal.address)}</div></div>
      <button type="button" class="btn-o sm" onclick="pwOpen()">Change</button></div>
    <div id="pw-flow"></div>`;
}
function pwOpen(){
  const v=!!VUSDT_TEST;$('pw-flow').innerHTML=`<div class="pw-flow">
    <select id="pw-network" class="inp">${v?`<option value="POLYGON">Polygon</option>`
      :`<option value="TRC20">TRC-20 (Tron)</option><option value="BEP20">BEP-20 (BNB Smart Chain)</option><option value="POLYGON">Polygon</option>`}</select>
    <input id="pw-address" class="inp mono" placeholder="${v?'0x…':'New USDT wallet address'}" autocomplete="off" spellcheck="false" autocapitalize="off">
    <div class="po-code"><button type="button" class="btn-o sm" id="pw-codebtn" onclick="pwCode()">E-mail me a code</button>
      <input id="pw-code" class="inp mono" inputmode="numeric" maxlength="6" placeholder="6-digit code" autocomplete="one-time-code"></div>
    <button type="button" class="btn-p" id="pw-save" onclick="pwSave()">Save new wallet</button></div>`;
}
async function pwCode(){
  const address=$('pw-address').value.trim();
  if(!poAdresOk(address,!!VUSDT_TEST)){$('pw-address').classList.add('bad');$('pw-address').focus();return}
  await busy($('pw-codebtn'),null,async()=>{
    try{await api('/api/me/payout-wallet/code',{method:'POST',body:JSON.stringify({network:$('pw-network').value,address})});
      toast(`📧 We e-mailed a 6-digit code to ${ME.email}.`,'ok',7000);$('pw-code').focus();
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function pwSave(){
  const code=$('pw-code').value.trim();
  if(!/^\d{6}$/.test(code)){$('pw-code').classList.add('bad');$('pw-code').focus();return}
  await busy($('pw-save'),null,async()=>{
    try{await api('/api/me/payout-wallet',{method:'POST',body:JSON.stringify({network:$('pw-network').value,
        address:$('pw-address').value.trim(),code})});
      toast('✅ Payout wallet updated.','ok');pwLoad();
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
/* Skalowanie to decyzja ZAMIAST wypłaty, więc modal mówi wprost, co trader
   oddaje — i że dostaje NOWY rachunek, bo starego nie da się powiększyć: saldo
   siedzi u brokera, a nie w naszej bazie. */
function openScaleModal(id,from,to){
  const w=document.createElement('div'); w.id='sc-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" role="dialog" aria-modal="true" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Move up to the $${fmt0(to)} plan</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 14px">
      You leave the <b>$${fmt0(from)}</b> account behind and we set up a fresh
      <b>$${fmt0(to)}</b> account, funded from day one, with that plan's limits. The profit you
      made pays for the upgrade, so there is no payout this time. The new account starts with a
      clean track record: equity, trades and stats count from the move. Your new login and
      password arrive by email.</p>
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
/* ---------- testowe wypłaty vUSDT (VUSDT_TEST) ----------
   Po odblokowaniu przez admina trader podaje kwotę + adres 0x + Turnstile.
   Serwer podpisuje transfer na Polygonie; modal pokazuje Sending → Sent
   i dopytuje o potwierdzenie z łańcucha. Token Turnstile jest jednorazowy —
   po każdym błędzie widget się resetuje. */
const PO_TS={token:'',widget:null};
function poTurnstile(){
  PO_TS.token='';PO_TS.widget=null;
  const draw=()=>{const box=$('po-ts');if(!box||!window.turnstile)return;
    PO_TS.widget=turnstile.render(box,{sitekey:VUSDT_TEST.site_key,theme:themeNow()==='dark'?'dark':'light',language:'en',size:'flexible',
      callback:t=>{PO_TS.token=t},'expired-callback':()=>{PO_TS.token=''},'error-callback':()=>{PO_TS.token=''}})};
  if(window.turnstile)return draw();
  let s=document.getElementById('cf-turnstile-js');
  if(!s){s=document.createElement('script');s.id='cf-turnstile-js';s.async=true;
    s.src='https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';document.head.appendChild(s)}
  s.addEventListener('load',draw,{once:true});
}
function poTurnstileReset(){PO_TS.token='';if(window.turnstile&&PO_TS.widget!==null){try{turnstile.reset(PO_TS.widget)}catch(_){}}}
function poShort(a){return a?a.slice(0,6)+'…'+a.slice(-4):''}
function poSent(reqId,v,n=0){
  const box=document.querySelector('#po-modal .modal');if(!box)return;
  const done=v.status==='confirmed',failed=v.status==='failed',queued=v.status==='queued';
  box.innerHTML=`<div class="modal-head"><h3>${failed?'Transfer failed':queued?'Payout saved':'Sent!'}</h3></div>
    <p style="font-size:14px;margin:2px 0 10px"><b>${esc(v.amount)} ${esc(v.symbol)}</b> ${
      failed?'could not be delivered to':queued?'will be sent to':'is on its way to'} <span class="mono">${esc(poShort(v.address))}</span> on Polygon.</p>
    <div class="po-vstat ${failed?'bad':done?'ok':''}" role="status">${
      failed?esc(v.note||'The transfer failed.'):done?(v.request_status==='paid'?'✓ Confirmed on Polygon · payout paid':'✓ Confirmed on Polygon'):queued?esc(v.note||'In line…'):'Confirming on Polygon…'}</div>
    ${v.explorer_url?`<p style="margin:10px 0 0"><a href="${esc(v.explorer_url)}" target="_blank" rel="noopener">View on Polygonscan ↗</a></p>`:''}
    ${failed?'':`<p class="muted" style="font-size:12px;margin:10px 0 0">vUSDT is a custom token on <b>Polygon</b> — add it in your wallet if the balance does not show.</p>
    <div class="po-token" style="margin-top:10px">
      <code class="mono" style="font-size:11.5px;word-break:break-all">${esc(VUSDT_TEST.token)}</code>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
        <button class="btn-o sm" type="button" onclick="poCopyToken(this)">Copy contract</button>
        <button class="btn-o sm" type="button" onclick="poAddToken(this)">Add to wallet</button>
      </div>
    </div>`}
    <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap">
      <button class="btn-p" onclick="$('po-modal').remove();go('payouts')">Done</button>
    </div>`;
  // Do potwierdzenia ORAZ rozliczenia wniosku (paid przychodzi z tego samego odczytu).
  if(!failed&&!(done&&v.request_status==='paid'))setTimeout(()=>poPoll(reqId,n+1),4000);
}
async function poPoll(reqId,n){
  if(!$('po-modal')||n>45)return;
  try{poSent(reqId,await api(`/api/me/payout-requests/${reqId}/vusdt`),n)}
  catch(e){setTimeout(()=>poPoll(reqId,n+1),6000)}
}
/* EIP-1193 (MetaMask, Trust in-app browser, Rainbow…). Copy i Add to wallet
   to osobne akcje — Add nie woła Copy, żeby nie dublować toastów. */
function poEth(){
  const e=window.ethereum;
  if(!e)return null;
  if(Array.isArray(e.providers)&&e.providers.length){
    return e.providers.find(p=>p.isMetaMask||p.isTrust||p.isCoinbaseWallet||p.isRainbow)||e.providers[0];
  }
  return e;
}
async function poCopyToken(btn){
  const a=VUSDT_TEST&&VUSDT_TEST.token;if(!a)return;
  try{await navigator.clipboard.writeText(a);
    toast('Contract copied.','ok',4000);
    if(btn){const t=btn.textContent;btn.textContent='Copied';setTimeout(()=>btn.textContent=t,1500)}
  }catch(_){toast('Copy failed — select the address above.','err')}
}
async function poAddToken(btn){
  const eth=poEth(), token=VUSDT_TEST&&VUSDT_TEST.token;
  if(!token)return;
  if(!eth){
    toast('Open this page in your wallet’s browser (Trust / MetaMask), then tap Add to wallet again. Or use Copy contract and add a custom token on Polygon.','err',11000);
    return;
  }
  await busy(btn,'Check your wallet…',async()=>{
    try{
      await eth.request({method:'eth_requestAccounts'});
      const chain='0x'+VUSDT_TEST.chain_id.toString(16);
      try{await eth.request({method:'wallet_switchEthereumChain',params:[{chainId:chain}]})}
      catch(e){if(e&&e.code===4902)await eth.request({method:'wallet_addEthereumChain',params:[{chainId:chain,
        chainName:'Polygon',nativeCurrency:{name:'POL',symbol:'POL',decimals:18},
        rpcUrls:['https://polygon-rpc.com'],blockExplorerUrls:['https://polygonscan.com']}]});else throw e}
      const ok=await eth.request({method:'wallet_watchAsset',params:{type:'ERC20',options:{
        address:token,symbol:VUSDT_TEST.symbol,decimals:VUSDT_TEST.decimals,
        image:location.origin+VUSDT_TEST.image}}});
      toast(ok?'vUSDT added — check your token list on Polygon.':'Wallet did not add the token.',ok?'ok':'err');
    }catch(e){
      if(e&&e.code===4001){toast('Cancelled in the wallet.','err');return}
      toast('Could not add automatically. Use Copy contract, then Custom token → Polygon in your wallet.','err',11000);
    }
  });
}

/* Klawiatura ekranowa vs dolny pasek: position:fixed na iOS nie wie o niej nic
   i pasek zawisa nad polem edycji w połowie ekranu — na czas pisania znika
   (body.kb-open w portal.css). Stan liczymy z FAKTÓW (aktywne pole w DOM-ie +
   zwężony visualViewport), a nie z samego focusout: zamknięcie okna z polem
   odpowiedzi usuwało pole z DOM-u bez focusout i pasek znikał do przeładowania. */
function kbSprawdz(){
  const a=document.activeElement;
  const pole=!!(a&&a.isConnected&&a.matches&&
    a.matches('input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=button]),textarea,select'));
  const vv=window.visualViewport;
  const klawiatura=pole&&(!vv||vv.height<window.innerHeight-120);
  document.body.classList.toggle('kb-open',klawiatura);
}
addEventListener('focusin',()=>setTimeout(kbSprawdz,60));
addEventListener('focusout',()=>setTimeout(kbSprawdz,80));
addEventListener('click',()=>setTimeout(kbSprawdz,150),true);
if(window.visualViewport)visualViewport.addEventListener('resize',kbSprawdz);

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
