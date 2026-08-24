const $=id=>document.getElementById(id);
const fmt=n=>(n??0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmt0=n=>(n??0).toLocaleString('en-US',{maximumFractionDigits:0});
/* Baza zapisuje nagie UTC (bez "Z") — new Date() wziąłby to za czas lokalny.
   Dokładamy "Z" i renderujemy w Europe/Warsaw: dział czyta panel po polsku. */
const dutc=iso=>new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso||'')?iso:iso+'Z');
const dstr=iso=>dutc(iso).toLocaleString('en-US',{timeZone:'Europe/Warsaw',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
const wawIso=iso=>dutc(iso).toLocaleString('sv-SE',{timeZone:'Europe/Warsaw'});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/* Do interpolacji w onclick="fn('...')". Samo esc() nie wystarcza: parser HTML
   odwija &#39; z powrotem do apostrofu PRZED parsowaniem JS-a, wiec nazwisko
   w rodzaju O'Brien uciety string i przycisk przestawal dzialac. Najpierw
   escape JS-a, potem HTML calego wyniku. */
const jsq=s=>esc(String(s??'').replace(/\\/g,'\\\\').replace(/'/g,"\\'"));
/* Anty-zawieszka przycisków: gasnie na czas fn(), finally ZAWSZE przywraca.
   Drugi klik w trakcie = no-op. fn zwraca 'keep' => zostaje wyłączony. */
async function busy(btn,label,fn){
  if(!btn)return fn();
  if(btn.disabled)return;
  const html=btn.innerHTML;
  btn.disabled=true;if(label)btn.textContent=label;
  let keep=false;
  try{const w=await fn();keep=(w==='keep');return w}
  finally{if(!keep&&btn.isConnected){btn.disabled=false;btn.innerHTML=html}}
}

/* The panel is opened by an administrator ACCOUNT, not a shared token. The
   session is the same as the trader portal (`pf_token`), so a signed-in admin
   moves between the panel and the portal without a second login. */
let TOKEN=localStorage.getItem('pf_token')||null, ME=null;
const adminH=()=>{const h={'Content-Type':'application/json'};
  if(TOKEN)h['Authorization']='Bearer '+TOKEN; return h};
/* Timeout 15 s + JEDEN retry tylko dla GET po błędzie sieci/timeoucie — HTTP
   error (nawet 500) nigdy nie jest ponawiany, żeby mutacje się nie dublowały. */
async function api(path,opts={},_retry){
  const ms=opts.timeoutMs||15000; delete opts.timeoutMs;
  const ctl=new AbortController(),tm=setTimeout(()=>ctl.abort(),ms);
  let r;
  try{r=await fetch(path,{headers:adminH(),...opts,signal:ctl.signal})}
  catch(e){
    clearTimeout(tm);
    if(!_retry&&(opts.method||'GET').toUpperCase()==='GET')return api(path,{...opts,timeoutMs:ms},1);
    throw new Error(e&&e.name==='AbortError'?'Request timed out':'Network error');
  }
  clearTimeout(tm);
  if(r.status===401||r.status===403){signInForm();throw new Error('Access denied')}
  if(!r.ok)throw new Error((await r.json().catch(()=>({}))).detail||r.status);
  return r.json();
}
function signInForm(){
  // The panel has no login of its own and shows NOTHING: there is one entry,
  // the same as for traders. `replace` instead of `href` so the browser
  // back button does not land on an empty panel.
  ME=null;
  // Drop the server-side session cookie first: /portal bounces any admin
  // cookie straight back here, so an orphaned cookie (valid cookie, missing
  // or expired local token) would otherwise loop between the two pages.
  fetch('/api/auth/logout',{method:'POST'}).finally(()=>location.replace('/portal?next=/admin'));
}

function signOut(){
  fetch('/api/auth/logout',{method:'POST'}).finally(()=>{
    TOKEN=null;ME=null;localStorage.removeItem('pf_token');location.replace('/portal')});
}

/* Globalny łapacz błędów JS → telemetria. Dedupe + limit 5/sesję,
   fire-and-forget: prosty fetch zamiast api(), żeby raportowanie nie weszło
   w pętlę z signInForm() przy wygasłej sesji. */
const _jsErrSeen=new Set();
function reportJsError(msg,src){
  try{
    const key=String(msg||'unknown').slice(0,80);
    if(!TOKEN||_jsErrSeen.has(key)||_jsErrSeen.size>=5)return;
    _jsErrSeen.add(key);
    fetch('/api/telemetry',{method:'POST',headers:adminH(),
      body:JSON.stringify({name:'js_error',props:{msg:key,src:String(src||'').slice(0,80),view:'admin'}})}).catch(()=>{});
  }catch(e){}
}
addEventListener('error',e=>reportJsError(e.message,(e.filename||'')+':'+(e.lineno||0)));
addEventListener('unhandledrejection',e=>reportJsError(e.reason&&e.reason.message||e.reason,'promise'));

const ICO={
  layers:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 2 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></svg>',
  grid:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
  wallet:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="6" width="20" height="14" rx="3"/><path d="M2 10h20M16 15h2"/></svg>',
  shield:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  chat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.8A8 8 0 1 1 21 12z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></svg>',
  file:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></svg>',
  bank:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 9 9-6 9 6"/><path d="M5 9v11M19 9v11M9 9v11M15 9v11M3 20h18"/></svg>',
  gear:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10.3 4.3a2 2 0 0 1 3.4 0l.6 1a2 2 0 0 0 1.7 1h1.2a2 2 0 0 1 1.7 3l-.6 1a2 2 0 0 0 0 2l.6 1a2 2 0 0 1-1.7 3h-1.2a2 2 0 0 0-1.7 1l-.6 1a2 2 0 0 1-3.4 0l-.6-1a2 2 0 0 0-1.7-1H6.8a2 2 0 0 1-1.7-3l.6-1a2 2 0 0 0 0-2l-.6-1a2 2 0 0 1 1.7-3H8a2 2 0 0 0 1.7-1z"/><circle cx="12" cy="12" r="2.6"/></svg>',
  users:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 20c.8-3.2 2.9-4.8 5.5-4.8s4.7 1.6 5.5 4.8"/><circle cx="17.5" cy="9.5" r="2.4"/><path d="M15.8 15.6c2.7-.4 4.4 1 5 4.4"/></svg>',
  dollar:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2v20M17 6.5c0-2-2.2-3-5-3s-5 1-5 3 2 2.8 5 3.4 5 1.6 5 3.6-2.2 3-5 3-5-1-5-3"/></svg>',
  alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 2 20h20z"/><path d="M12 9.5V14M12 17h.01"/></svg>',
  trend:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',
  arrow:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:15px;height:15px"><path d="M9 6l6 6-6 6"/></svg>',
  copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  mail:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="4.5" width="19" height="15" rx="2"/><path d="m3 6 9 6.5L21 6"/></svg>',
  pulse:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12h4l2.5-6 4 12L16 12h5"/></svg>',
};

const NAV=[
  {v:'overview',label:'Overview',ico:'grid'},
  {v:'leads',label:'Leads',ico:'users'},
  {v:'accounts',label:'Accounts',ico:'layers'},
  {v:'activity',label:'Activity',ico:'pulse'},
  {v:'payouts',label:'Payouts',ico:'wallet'},
  {v:'kyc',label:'KYC',ico:'shield'},
  {v:'tickets',label:'Tickets',ico:'chat'},
  {v:'orders',label:'Orders',ico:'file'},
  {v:'pool',label:'MT5 Pool',ico:'bank'},
  {v:'mail',label:'Mail',ico:'mail'},
  {v:'telemetry',label:'Telemetry',ico:'trend'},
  {v:'settings',label:'Settings',ico:'gear'},
];
$('side-nav').innerHTML=NAV.map(n=>
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}')" title="${n.label}">${ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`).join('');

/* Mobile bottom bar: the 4 most-used sections; "More" opens the drawer with
   the full list. Same go()/NAV as the sidebar — one source of truth.
   Leads i Orders to codzienna robota (Telegram -> zamowienie -> mark paid),
   a siedzialy w szufladzie; Payouts/KYC sa od swieta i tam wracaja. */
const MORE_ICO='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>';
$('botnav').innerHTML=['overview','leads','orders','accounts'].map(v=>{
  const n=NAV.find(x=>x.v===v);
  return `<button class="botnav-btn" data-v="${n.v}" onclick="go('${n.v}')">${ICO[n.ico]}<span>${n.label}</span></button>`;
}).join('')+`<button class="botnav-btn" onclick="toggleSide(true)" aria-label="All sections">${MORE_ICO}<span>More</span></button>`;

const TITLES={
  overview:['Overview','Platform health and items waiting for you'],
  leads:['Leads','Applications from the landing page, and who they turned into'],
  accounts:['Accounts','All challenge accounts and their live risk metrics'],
  activity:['Activity','Who claimed their account, who signs in, and what each client did'],
  payouts:['Payouts','Every payout booked so far, plus requests waiting for review'],
  kyc:['KYC','Identity verifications awaiting review'],
  tickets:['Tickets','Support conversations with traders'],
  orders:['Orders','Purchases and revenue'],
  pool:['MT5 Pool','Pre-provisioned accounts ready to assign'],
  mail:['Mail','Every e-mail the platform tried to send — and which ones failed'],
  telemetry:['Telemetry','Product events from the last 14 days'],
  settings:['Settings','Admin access and runtime configuration'],
};
/* ---------- theme (shared pf_theme2 key with the trader portal) ---------- */
const THEME_SUN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.4M12 19.6V22M2 12h2.4M19.6 12H22M4.9 4.9l1.7 1.7M17.4 17.4l1.7 1.7M19.1 4.9l-1.7 1.7M6.6 17.4l-1.7 1.7"/></svg>';
const THEME_MOON='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.4 14.2A8.5 8.5 0 0 1 9.8 3.6 8.5 8.5 0 1 0 20.4 14.2z"/></svg>';
function themeNow(){return document.documentElement.dataset.theme==='dark'?'dark':'light'}
function paintTheme(){
  const t=themeNow();
  document.querySelectorAll('.theme-toggle').forEach(b=>{
    b.innerHTML=(t==='dark'?THEME_SUN:THEME_MOON)
      +'<span class="sb-txt">'+(t==='dark'?'Light mode':'Dark mode')+'</span>';
    b.title=t==='dark'?'Switch to light mode':'Switch to dark mode';
  });
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',t==='dark'?'#0b0d12':'#f6f7fb');
}
function toggleTheme(){
  const next=themeNow()==='dark'?'light':'dark';
  document.documentElement.dataset.theme=next;
  try{localStorage.setItem('pf_theme2',next)}catch(e){}
  paintTheme();
  /* Charts snapshot token colors at render time — repaint the active view. */
  if(typeof VIEW!=='undefined')go(VIEW);
  /* The account slide-over lives outside #view — redraw its chart too. */
  if(window._oAcc&&document.getElementById('o-chart'))drawAdminChart();
}
paintTheme();

/* Sticky toolbar w Leads przykleja sie POD topbarem, a topbar zmienia wysokosc
   (safe-area w PWA, wesze okna) — mierzymy go zamiast zgadywac. */
const measureTopbar=()=>document.documentElement.style
  .setProperty('--tb-h',(document.querySelector('.topbar')?.offsetHeight||58)+'px');
measureTopbar();
addEventListener('resize',measureTopbar);

let VIEW='overview';

/* ---------- wiersze z importu ewidencji wyplat ----------
   Za adresem `…@imported.local` nie stoi klient, ktory sie zapisal, tylko wiersz
   z CSV-ki. Sa potrzebne (prawdziwe wyplaty, prawdziwe certyfikaty), ale na
   listach, po ktorych szuka sie LUDZI, topia prawdziwe konta. Domyslnie ukryte,
   z jednym przelacznikiem na caly panel -- pytanie brzmi "czy dzis patrze takze
   na archiwum", a nie "czy patrze na nie w tej jednej tabeli". */
let IMPORTED=localStorage.getItem('pf_admin_imported')==='1';
const impQ=(sep='?')=>IMPORTED?sep+'imported=1':'';
const impPill=()=>` · <span class="statlink" onclick="toggleImported()"
  title="Rows imported from the payout records: real payouts, but nobody signed up for them"
  >imported ${IMPORTED?'shown':'hidden'}</span>`;
function toggleImported(){
  IMPORTED=!IMPORTED;
  localStorage.setItem('pf_admin_imported',IMPORTED?'1':'0');
  go(VIEW);   // filtr siedzi na serwerze, wiec samo przerysowanie nic nie zmieni
}

/* Ekran przejscia: paski trzymaja wysokosc widoku, kolko w kolorze akcentu mowi,
   ze cos sie dzieje. Same paski na ciemnym motywie czytaly sie jako pusty ekran. */
const LOADING_HTML=(h=260)=>`<div class="view-load">
  <div class="skel" style="height:110px;margin-bottom:16px"></div>
  <div class="skel" style="height:${h}px"></div>
  <div class="vl-mid"><span class="vl-ring"></span><span class="vl-txt">Loading…</span></div>
</div>`;
/* Szkielet w ksztalcie listy leadow: wysokosci ≈ docelowy wiersz/karta,
   zeby wstawienie danych nie podnosilo strony. */
const LEADS_SKEL=()=>`<div class="view-load">
  <div class="skel" style="height:44px;margin-bottom:10px"></div>
  <div class="skel" style="height:14px;width:40%;margin-bottom:10px"></div>
  ${'<div class="skel lead-skel-row"></div>'.repeat(6)}
  <div class="vl-mid"><span class="vl-ring"></span><span class="vl-txt">Loading…</span></div>
</div>`;

/* Numer przelaczenia. Widoki pisza do #view dopiero po powrocie z API, wiec
   wolniejszy POPRZEDNI widok potrafil skonczyc sie PO zmianie zakladki i nadpisac
   nowy — na ekranie zostawal naglowek "Payouts" nad tabela kont. Zlapane przy
   sprawdzaniu wskaznika ladowania. Gdy przebieg okaze sie nieaktualny, przerysowujemy
   biezacy widok; to rzadka sciezka, wiec dodatkowe zapytanie nic nie kosztuje. */
let PRZEJSCIE = 0;

/* ---------- adres pamieta, gdzie jestes ----------
   F5 wyrzucalo na Overview i kazalo szukac swojego miejsca od nowa. Adres
   `#widok:filtr?q=fraza` (np. `#leads:free?q=kowalski`) niesie caly stan listy:
   `:filtr` znika przy `all`, `?q=` przy pustej frazie, wiec typowy adres zostaje
   krotki i czytelny. Kodujemy tylko `q` — wartosci filtrow to `[a-z_]+`. */
const STAN_POL={
  accounts:['_accQ','_accFilter'], activity:['_jrnQ','_jrnFilter'],
  kyc:['_kycQ','_kycFilter'],      leads:['_leadQ','_leadFilter'],
  mail:['_mailQ','_mailFilter'],   orders:['_ordQ','_ordFilter'],
  payouts:['_payQ','_payFilter'],  pool:['_poolQ','_poolFilter'],
  tickets:['_tickQ','_tickFilter'],
};
let OSTATNI_HASZ='', ZAPIS_T=0, PIERWSZY_ZAPIS=true;

const haszZe=st=>st.view+(st.filter&&st.filter!=='all'?':'+st.filter:'')
                        +(st.q?'?q='+encodeURIComponent(st.q):'');

function budujHasz(){
  const [kluczQ,kluczF]=STAN_POL[VIEW]||[];
  return haszZe({view:VIEW, filter:kluczF?(window[kluczF]||'all'):'all',
                 q:kluczQ?(window[kluczQ]||''):''});
}
function czytajHasz(){
  const h=location.hash.slice(1);
  if(!h)return null;
  const i=h.indexOf('?');
  const lewa=i<0?h:h.slice(0,i);
  const q=i<0?'':(new URLSearchParams(h.slice(i+1)).get('q')||'');
  const j=lewa.indexOf(':');
  const view=j<0?lewa:lewa.slice(0,j);
  /* Biala lista na TITLES, nie na VIEWS: w VIEWS siedzi `_kycRender`, ktore NIE
     jest async — `go('_kycRender')` rzucilby na `undefined.then(...)` i zostawil
     bialy ekran. Nieznany hasz oddaje null i konczy sie cichym Overview. */
  return TITLES[view]?{view, filter:j<0?'all':lewa.slice(j+1), q}:null;
}
function ustawStan(st){
  const [kluczQ,kluczF]=STAN_POL[st.view]||[];
  if(kluczF)window[kluczF]=st.filter;
  if(kluczQ)window[kluczQ]=st.q;
}
function zapiszStan(push){
  const nowy=budujHasz();
  /* Pierwsze przejscie w sesji nie zostawia za soba wpisu: Wstecz ma wyjsc
     z panelu, a nie wracac na adres bez hasza, ktory nic nie znaczy. Flaga
     gasnie PRZED straznikiem — po F5 adres juz niesie wlasciwy hasz, wiec
     startowe `go()` konczy sie na strazniku i flaga zostawalaby zapalona,
     a nastepne przelaczenie zakladki nadpisywaloby wpis zamiast go dolozyc. */
  const pierwszy=PIERWSZY_ZAPIS; PIERWSZY_ZAPIS=false;
  /* Straznik robi z powtorek koszt zerowy — inaczej auto-odswiezanie co 12 s
     waliloby w limit `history` w Safari, a pisanie w wyszukiwarce zrobiloby
     jeden wpis w historii na znak. */
  if(nowy===OSTATNI_HASZ)return;
  OSTATNI_HASZ=nowy;
  try{history[push&&!pierwszy?'pushState':'replaceState'](null,'','/admin?pwa=1#'+nowy)}catch(_){}
}
const zapiszPozniej=()=>{clearTimeout(ZAPIS_T);ZAPIS_T=setTimeout(()=>zapiszStan(false),250)};
/* Filtry to kilkanascie wpisanych w HTML `onclick="window._xFilter=…;renderX()"`
   i nie wszystkie siedza w `.seg` (banner terminow, statlink, arkusz akcji na
   telefonie). Jeden listener na dokumencie lapie kazdy z nich bez dotykania
   kilkunastu miejsc; `setTimeout(…,0)` czeka, az inline onclick ustawi globala. */
document.addEventListener('click',()=>setTimeout(()=>zapiszStan(false),0));
addEventListener('hashchange',()=>{
  const h=location.hash.slice(1);
  if(h===OSTATNI_HASZ)return;   // wlasny zapis, nie ruch Wstecz/Dalej
  OSTATNI_HASZ=h;               // bzdurny hasz tez: go() nizej go nadpisze
  const st=czytajHasz();
  if(st)ustawStan(st);
  go(st?st.view:'overview');
});

function go(v){
  /* Re-render TEGO SAMEGO widoku (po akcji, undo, odswiezeniu) nie moze rzucac
     admina na gore listy: stara tresc zostaje do przyjscia danych (bez szkieletu,
     ktory skraca strone i ucina scroll), a po przerysowaniu wracamy w to samo
     miejsce. Nowa zakladka dostaje szkielet jak dotad. */
  const samWidok=v===VIEW&&!$('view').querySelector('.view-load');
  const wrocDo=samWidok?scrollY:0;
  const innyWidok=v!==VIEW;
  VIEW=v;
  zapiszStan(innyWidok);   // wpis w historii tylko na zmiane zakladki
  document.querySelectorAll('.sb-link[data-v],.botnav-btn[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  const t=TITLES[v]||['',''];
  $('pg-title').textContent=t[0]; $('pg-crumb').textContent=t[1];
  toggleSide(false);
  const moj=++PRZEJSCIE;
  if(!samWidok)$('view').innerHTML=v==='leads'?LEADS_SKEL():LOADING_HTML(260);
  VIEWS[v]()
    .then(()=>{
      if(moj!==PRZEJSCIE){if(VIEWS[VIEW])VIEWS[VIEW]();return}
      if(wrocDo)scrollTo(0,wrocDo);
    })
    .catch(e=>{
      /* Blad zostaje W widoku z przyciskiem ponowienia. Sam toast znikal po
         paru sekundach i na ekranie zostawal wieczny szkielet ladowania. */
      if(moj!==PRZEJSCIE||String(e.message).includes('token'))return;
      $('view').innerHTML=`<div class="empty"><h3>Couldn't load this view</h3>
        <p>${esc(e.message)}</p>
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
  /* .botnav: "More" opens the drawer with this same click — without the
     exception the event bubbling to the document would close it immediately */
  if(e.target.closest('#side')||e.target.closest('.burger')||e.target.closest('.botnav'))return;
  toggleSide(false);
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&document.body.classList.contains('nav-open'))toggleSide(false);
});
function toggleCollapse(){const c=$('side').classList.toggle('collapsed');
  localStorage.setItem('pf_admin_collapsed',c?'1':'0')}
function toast(msg,kind='ok',ms=6000){
  const t=document.createElement('div');t.className='toast '+kind;t.textContent=msg;
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),350)},ms);
}
/* ---------- trwale usuwanie z oknem na cofniecie ----------
   Klikniete usuwanie NIE leci od razu: przez 5 s czeka w kolejce, a admin widzi
   toast z przyciskiem cofniecia i odliczaniem. Dopiero po tym czasie idzie
   zadanie na serwer. Dzieki temu "cofnij" nie musi NICZEGO odtwarzac -- po
   prostu nic sie nie stalo. Odtwarzanie skasowanego wiersza bylo alternatywa
   gorsza: certyfikat ma numer wdrukowany w kod QR, ktory klient ma juz u siebie,
   wiec wiersz odtworzony "taki sam" mialby inny numer i stare odwolania i tak
   zostalyby martwe. Wiersz zostaje widoczny na liscie przez cale odliczanie,
   wiec nie ma falszywego wrazenia, ze juz zniknal. */
const UNDO_MS=5000;
const UNDO_ICO='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
  +' stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/>'
  +'<path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg>';
const _czekajace=new Set();

/* Ostatnio klikniety przycisk — stad bierzemy WIERSZ do schowania. Faza
   przechwytywania, zeby zadzialalo tez dla przyciskow z inline onclick. */
let _ostatniPrzycisk=null;
addEventListener('click',e=>{
  const b=e.target&&e.target.closest&&e.target.closest('button');
  if(b)_ostatniPrzycisk=b;
},true);

function withUndo(opis,wykonaj,wiersz,onUndo){
  /* Wiersz znika OD RAZU, tak jak przy zwyklym usuwaniu — zadanie na serwer
     idzie dopiero po 5 s. Cofniecie w tym oknie przywraca wiersz i nic nie
     wysyla, wiec przywrocenie jest zawsze wierne: nie odtwarzamy rekordu,
     tylko go nie kasujemy. Odtwarzanie bylo alternatywa gorsza — certyfikat ma
     numer wdrukowany w kod QR, ktory klient ma juz u siebie, wiec wiersz
     odtworzony "taki sam" mialby inny numer. */
  const schowaj=wiersz&&wiersz.style?wiersz.style.display:null;
  if(wiersz&&wiersz.style)wiersz.style.display='none';

  const t=document.createElement('div');
  t.className='toast undo';
  t.innerHTML='<span class="undo-txt"></span>'
    +`<button class="undo-btn" type="button" aria-label="Restore">${UNDO_ICO}Undo</button>`;
  const txt=t.querySelector('.undo-txt');
  let zostalo=Math.round(UNDO_MS/1000);
  const rysuj=()=>{txt.textContent=`${opis} — undo within ${zostalo}s`};
  rysuj();
  $('toasts').appendChild(t);

  const zadanie={wykonaj};
  const sprzatnij=()=>{clearTimeout(zadanie.zegar);clearInterval(zadanie.tik);
    _czekajace.delete(zadanie);t.remove()};
  zadanie.anuluj=sprzatnij;
  zadanie.domknij=()=>{sprzatnij();wykonaj()};
  zadanie.tik=setInterval(()=>{zostalo--;if(zostalo>0)rysuj()},1000);
  zadanie.zegar=setTimeout(zadanie.domknij,UNDO_MS);
  t.querySelector('.undo-btn').onclick=()=>{
    sprzatnij();
    if(wiersz&&wiersz.style)wiersz.style.display=schowaj||'';
    /* Widoki bez zywego elementu wiersza (leady renderuja z window._leads)
       przywracaja swoj stan same. */
    if(onUndo)onUndo();
    toast('Restored — nothing was deleted.','ok',3500);
  };
  _czekajace.add(zadanie);
}

/* Wyjscie ze strony DOMYKA to, co czeka: admin widzial, ze wiersz zniknal, wiec
   zamkniecie karty nie moze go po cichu przywrocic. `keepalive` na zadaniu
   pozwala mu doleciec juz po zamknieciu strony. */
addEventListener('beforeunload',()=>{[..._czekajace].forEach(z=>z.domknij())});

/* Toast "zrobione + Cofnij" dla akcji, ktore JUZ poszly na serwer, ale maja
   endpoint odwrotny (approve KYC -> reset). Inaczej niz withUndo: tam zadanie
   czeka 5 s w kolejce, tutaj cofniecie to drugi, jawny request. */
function undoToast(msg,onUndo,ms=8000){
  const t=document.createElement('div');
  t.className='toast undo';
  t.innerHTML='<span class="undo-txt"></span>'
    +`<button class="undo-btn" type="button" aria-label="Undo">${UNDO_ICO}Undo</button>`;
  t.querySelector('.undo-txt').textContent=msg;
  t.querySelector('.undo-btn').onclick=()=>{t.remove();onUndo()};
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),350)},ms);
}

function closeOver(){$('over').classList.remove('open')}
function openOver(title,html){$('o-title').textContent=title;$('o-body').innerHTML=html;$('over').classList.add('open')}

const STATUS_LBL={active:'evaluation',funded:'funded',failed:'failed',passed:'passed',provisioning:'provisioning'};
const PHASE_LBL={eval_1:'Phase 1',eval_2:'Phase 2',funded:'Funded'};
function mini(pct){const p=Math.min(100,Math.max(0,pct||0));
  const c=p>=100?'bad':p>=70?'warn':'ok';
  return `<div class="mini"><i class="${c}" style="width:${p}%"></i></div>`}

/* Kanał do leada, który nie ma czym wysłać, CHOWA swój przycisk — i wtedy brak
   konfiguracji wygląda dokładnie tak samo jak zepsuta funkcja. Ten pasek jest
   jedynym miejscem, gdzie widać różnicę, więc mówi wprost, której zmiennej
   brakuje, zamiast „off". Serwer zwraca `undefined` na starszym deployu:
   wtedy nie ma o czym meldować i chip się nie pojawia. */
function leadChannel(nazwa,brakuje){
  if(!Array.isArray(brakuje))return'';
  return brakuje.length
    ?`<span class="sys warn" title="Set it in the hosting environment, then redeploy">
        <span class="dot"></span>${nazwa}: <b>needs ${brakuje.map(esc).join(', ')}</b></span>`
    :`<span class="sys"><span class="dot"></span>${nazwa}: <b>on</b></span>`;
}

/* ============================ VIEWS ============================ */
const VIEWS={
 async overview(){
  const [s,pay,kyc,tick,orders]=await Promise.all([
    api('/api/stats'),api('/api/admin/payout-requests'),api('/api/admin/kyc'),
    api('/api/admin/tickets'),api('/api/admin/orders')]);
  const revenue=orders.filter(o=>o.status==='paid').reduce((x,o)=>x+o.amount_usd,0);
  const pendingPay=pay.filter(r=>r.status==='pending').length;
  const openTick=tick.filter(t=>t.status==='open').length;
  const tile=(cls,ico,lbl,val,sub)=>`<div class="stat-tile"><div class="tile-ic ${cls}">${ICO[ico]}</div>
    <div><div class="lbl">${lbl}</div><div class="val">${val}</div>${sub?`<div class="sub">${sub}</div>`:''}</div></div>`;
  const todo=(n,label,view,ico)=>`<div class="todo ${n?'has':''}" onclick="go('${view}')">
    <div class="tile-ic ${n?'orange':'blue'}">${ICO[ico]}</div>
    <div><div class="n">${n}</div><div class="l">${label}</div></div><span class="go">${ICO.arrow}</span></div>`;
  /* Konto z ZAKUPU czeka na rachunek z puli po cichu: mail z poświadczeniami
     wychodzi dopiero przy uzbrojeniu, więc póki pula jest pusta, nikt nawet nie
     próbuje wysyłać — i dziennik maili też milczy. Wiersz prowadzi do MT5 Pool,
     bo tam widać, komu i jakiego rozmiaru rachunku brakuje. */
  $('view').innerHTML=`
    <div class="sysbar">
      <span class="sys ${s.stripe==='mock'?'warn':''}"><span class="dot"></span>Payments: <b>${esc(s.stripe)}</b></span>
      <span class="sys"><span class="dot"></span>Provisioning queue: <b>${s.provisioning??0}</b></span>
      <span class="sys"><span class="dot"></span>Pool free: <b>${s.pool_free??0}</b></span>
      ${leadChannel('Client e-mail',s.notify_mail_missing)}
      ${leadChannel('Lead e-mail',s.lead_mail_missing)}
      ${leadChannel('Lead SMS',s.lead_sms_missing)}
    </div>
    <div class="todo-grid">
      ${todo(pendingPay,'payout requests to review','payouts','wallet')}
      ${todo((kyc.pending||[]).length,'KYC submissions pending','kyc','shield')}
      ${todo(openTick,'support tickets open','tickets','chat')}
      ${s.mail_failed_7d?todo(s.mail_failed_7d,'e-mails failed to send (7 days)','mail','alert'):''}
      ${s.provisioning?todo(s.provisioning,'accounts waiting for an MT5 account from the pool','pool','bank'):''}
    </div>
    <div class="stats-row">
      ${tile('purple','layers','Accounts',s.total,`${s.active} active · ${s.provisioning??0} provisioning`)}
      ${tile('green','trend','Funded',s.funded,`${s.failed} failed`)}
      ${tile('blue','users','Traders',s.traders,`${s.orders_paid} paid orders`
        +(s.traders_internal?` · ${s.traders_internal} internal hidden`:''))}
      ${tile('orange','dollar','Revenue','$'+fmt0(revenue),'all paid orders')}
    </div>
    <div class="sec-card card-sm">
      <h3>Recent orders</h3>
      ${orders.length?`<div class="tbl-wrap tw-sm rtbl-wrap" style="border:0;box-shadow:none;border-radius:0"><table class="tbl rtbl"><thead><tr>
        <th>#</th><th>Trader</th><th>Product</th><th>Amount</th><th>Status</th><th>Account</th></tr></thead>
        <tbody>${orders.slice(0,8).map(o=>`<tr>
          <td class="num rt-hide" data-l="#">${o.id}</td><td class="rt-main" data-l="Trader">${esc(o.trader_email||'—')}</td><td data-l="Product">${esc(o.product_key)}</td>
          <td class="num" data-l="Amount">$${fmt(o.amount_usd)}</td>
          <td data-l="Status"><span class="status ${o.status==='paid'?'paid':o.status==='failed'?'failed':'pending'}"><span class="dot"></span>${esc(o.status)}</span></td>
          <td class="num rt-hide" data-l="Account">${accLink(o.account_id)}</td></tr>`).join('')}</tbody></table></div>`
        :'<p class="muted" style="font-size:13px">No orders yet.</p>'}
    </div>`;
 },

 async accounts(){
  const list=await api('/api/accounts'+impQ());
  window._accs=list;
  window._accFilter=window._accFilter||'all';
  renderAccounts();
 },

 async activity(){
  window._jrn=(await api('/api/admin/journal'+impQ())).items||[];
  window._jrnFilter=window._jrnFilter||'all';
  renderActivity();
 },

 async payouts(){
  /* Full list: every booked payout (including ones issued by hand from the
     account card) plus requests that have not become a payout yet. */
  window._payReqs=await api('/api/admin/payouts');
  renderPayoutsView();
 },

 async kyc(){
  /* Lista kanalu FREE leci osobnym zapytaniem, ale rownolegle — to podglad
     przed wysylka hurtowa, a nie czesc kolejki weryfikacji. */
  const [d,free]=await Promise.all([api('/api/admin/kyc'+impQ()),
                                    api('/api/admin/kyc/free-channel')]);
  window._kycData=d; window._kycFree=free;
  renderKyc();
 },

 _kycRender(){
  const d=window._kycData||{};
  const q=(window._kycQ||'').toLowerCase();
  const pasuje=t=>!q||[t.full_name,t.email,t.country,t.id_type,t.id_number,t.doc_ref]
    .some(x=>String(x||'').toLowerCase().includes(q));
  const pending=(d.pending||[]).filter(pasuje), histAll=d.history||[];
  const kf=window._kycFilter||'all';
  const hist=histAll.filter(t=>(kf==='all'||t.status===kf)&&pasuje(t));
  const cards=pending.length?`<div class="badge-grid" style="grid-template-columns:repeat(auto-fill,minmax(min(320px,100%),1fr))">`+
    pending.map(t=>`<div class="panel">
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
        <div><h3 style="font-size:15.5px">${esc(t.full_name||t.email)}</h3>
          <div class="muted" style="font-size:12px">${esc(t.email)}</div></div>
        <span class="status pending"><span class="dot"></span>pending</span>
      </div>
      <div style="margin:12px 0">
        <div class="kv"><span>Country</span><b>${esc(t.country||'—')}</b></div>
        <div class="kv"><span>Date of birth</span><b>${esc(t.dob||'—')}</b></div>
        <div class="kv"><span>Document</span><b>${esc(t.id_type||'—')} ${esc(t.id_number||t.doc_ref||'')}</b></div>
        <div class="kv"><span>Address</span><b style="font-family:var(--body);font-weight:500;text-align:right">${esc(t.address||'—')}</b></div>
        <div class="kv"><span>Submitted</span><b>${t.submitted_at?dstr(t.submitted_at):'—'}</b></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        ${(t.docs||[]).length?t.docs.map(k=>`<button class="btn-o sm" onclick="viewDoc(${t.trader_id},'${k}')">${esc(k.replace('_',' '))}</button>`).join('')
          :'<span class="muted" style="font-size:12px">No documents uploaded</span>'}
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn-p" style="flex:1" onclick="approveKyc(${t.trader_id})">Approve</button>
        <button class="btn-o" onclick="rejectKyc(${t.trader_id})">Reject</button>
      </div>
    </div>`).join('')+`</div>`
    :`<div class="empty"><h3>${q?'No pending submissions match':'Nothing to verify'}</h3><p>${
      q?'Check the history below or clear the search.':'KYC submissions from traders show up here.'}</p></div>`;
  const histTbl=histAll.length?`<div class="sec-card card-md" style="margin-top:18px">
    <h3>History</h3>
    <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Past verification decisions.</p>
    <div class="toolbar" style="margin-bottom:10px">
      <div class="seg">${[['all','All'],['approved','Approved'],['rejected','Rejected']]
        .map(([k,l])=>`<button class="${kf===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._kycFilter='${kf===k?'all':k}';renderKyc()">${l}</button>`).join('')}</div>
      <span class="count-pill">${hist.length} of ${histAll.length}${impPill()}</span>
    </div>
    ${hist.length?`<div class="tbl-wrap rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.kyc">
      <thead><tr><th>Reviewed</th><th>Trader</th><th>Country</th><th>Document</th><th>Status</th><th class="no-sort">Documents</th><th class="no-sort"></th></tr></thead>
      <tbody>${hist.map(t=>`<tr>
        <td class="muted" data-l="Reviewed" data-sort="${esc(t.reviewed_at||'')}">${t.reviewed_at?dstr(t.reviewed_at):'—'}</td>
        <td class="rt-main" data-l="Trader">${esc(t.full_name||'—')}<div class="muted" style="font-size:11.5px">${esc(t.email)}</div></td>
        <td class="muted" data-l="Country">${esc(t.country||'—')}</td>
        <td class="muted" data-l="Document">${esc(t.id_type||'—')} ${esc(t.id_number||t.doc_ref||'')}</td>
        <td data-l="Status"><span class="status ${t.status==='approved'?'funded':'failed'}"><span class="dot"></span>${esc(t.status)}</span></td>
        <td class="rt-acts">${(t.docs||[]).map(k=>`<button class="btn-o sm" onclick="viewDoc(${t.trader_id},'${k}')">${esc(k.replace('_',' '))}</button>`).join(' ')||'<span class="muted">—</span>'}</td>
        <td class="rt-acts" style="white-space:nowrap"><button class="btn-o sm" onclick="revertKyc(${t.trader_id})"
          title="Undo this decision, back to the pending queue">Revert</button>
          ${XBTN(`deleteKycRow(${t.trader_id},'${jsq(t.email)}')`,'Delete KYC record and uploaded documents')}</td></tr>`).join('')}
      </tbody></table></div>`:`<p class="muted" style="font-size:13px">No ${esc(kf)} decisions${q?' match':''}.</p>`}</div>`:'';
  const pasek=((d.pending||[]).length||histAll.length)
    ?`<div class="toolbar">${searchBox('kyc-q','_kycQ','renderKyc','Search name, email, country or document…')}</div>`:'';
  $('view').innerHTML=pasek+freeChannelCard()+cards+histTbl;
 },

 async mail(){
  window._mailLog=await api('/api/admin/mail-log');
  renderMailLog();
 },

 async tickets(){
  window._tickets=await api('/api/admin/tickets');
  renderTickets();
 },

 async orders(){
  window._orders=await api('/api/admin/orders');
  renderOrders();
 },

 async leads(){
  window._leads=await api('/api/admin/leads');
  renderLeads();
 },

 async pool(){
  const poolData=await api('/api/admin/pool');
  const rows=poolData.pool||[], waiting=poolData.waiting||[];
  const free=rows.filter(p=>!p.claimed).length;
  // how many accounts are missing per size — the admin should know WHAT to add
  const missingBySize={};
  waiting.forEach(w=>{missingBySize[w.account_size]=(missingBySize[w.account_size]||0)+1});
  // sizes from the catalog — the pool only makes sense for sizes someone can buy
  const sizeList=poolData.sizes||[];
  window._pool=rows; window._poolSizes=sizeList;
  const sizeOptions=(chosenSize)=>sizeList.map(r=>
    `<option value="${r}"${Number(chosenSize)===r?' selected':''}>$${fmt0(r)}</option>`).join('');
  $('view').innerHTML=`
    ${waiting.length?`<div class="sec-card card-sm" style="border-color:var(--gold-line);background:var(--gold-bg)">
      <h3>${waiting.length} paid ${waiting.length===1?'order is':'orders are'} waiting for an MT5 account</h3>
      <p class="muted" style="font-size:12.5px;margin:6px 0 12px">These challenges are paid for but not tradable yet: the pool has no free account of that size. Add one below or open a real MT5 demo for that trader.</p>
      <div class="tbl-wrap tw-sm rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.pool-waiting">
        <thead><tr><th>Account</th><th>Trader</th><th>Needs size</th><th>Waiting since</th><th class="no-sort"></th></tr></thead>
        <tbody>${waiting.map(w=>`<tr>
          <td class="num" data-l="Account">${accLink(w.account_id)}</td>
          <td class="rt-main" data-l="Trader">${esc(w.trader_email||'—')}</td>
          <td class="num" data-l="Size"><b>$${fmt0(w.account_size)}</b></td>
          <td class="muted" data-l="Since" data-sort="${esc(w.created_at||'')}">${w.created_at?dstr(w.created_at):'—'}</td>
          <td class="rt-acts"><button class="btn-p sm" onclick="provisionReal(${w.account_id})"
            ${poolData.can_generate?'':'disabled title="'+esc(poolData.generate_hint||'Browser channel unavailable')+'"'}>Open real MT5 now</button></td></tr>`).join('')}
        </tbody></table></div>
      <p class="muted" style="font-size:12.5px;margin-top:10px">Missing: ${Object.entries(missingBySize).map(([sizeKey,cnt])=>`<b>${cnt}×</b> $${fmt0(Number(sizeKey))}`).join(', ')}</p>
    </div>`:''}

    <div class="sec-card card-md">
      <h3>Simulated accounts</h3>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">Generates MT5-style credentials locally, with no real server behind them. Accounts provisioned from these entries are driven by the Trade BOT, not a live feed.</p>
      <div class="pool-form">
        <div><label class="muted" style="font-size:12px">Account size</label>
          <select id="sim-size" class="inp">${sizeOptions(50000)}</select></div>
        <div><label class="muted" style="font-size:12px">How many</label>
          <input id="sim-count" class="inp" type="number" min="1" max="50" value="5"></div>
      </div>
      <button class="btn-p" onclick="genSim()">Generate simulated accounts</button>
      <label style="display:flex;align-items:center;gap:9px;margin-top:14px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="sim-fb" ${poolData.sim_fallback?'checked':''} onchange="setSimFallback(this.checked)" style="width:16px;height:16px;accent-color:var(--acc)">
        Auto-provision simulated credentials when the pool has no matching account
      </label>
      <p class="muted" style="font-size:12px;margin-top:6px">With this on, a paid challenge never waits: if no free account of the right size is in the pool, the platform generates simulated credentials and activates the account right away.</p>
    </div>

    <div class="sec-card card-md">
      <h3>Real MT5 accounts</h3>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">Opens real demo accounts on MetaQuotes-Demo via <span class="mono">web.metatrader.app</span> and adds them to the pool. Needs a browser: local Chromium, or <span class="mono">BROWSER_CDP_URL</span> (Browserless).</p>
      ${poolData.can_generate?'':`<div class="warn-box" style="margin:0 0 12px"><div>${esc(poolData.generate_hint||'Real MT5 generator unavailable on this host.')}</div></div>`}
      <div class="pool-form">
        <div><label class="muted" style="font-size:12px">Account size</label>
          <select id="real-size" class="inp">${sizeOptions(50000)}</select></div>
        <div><label class="muted" style="font-size:12px">How many</label>
          <input id="real-count" class="inp" type="number" min="1" max="10" value="1"></div>
      </div>
      <button class="btn-p" onclick="genReal()" ${poolData.can_generate?'':'disabled'}>Generate real MT5 accounts</button>
      <label style="display:flex;align-items:center;gap:9px;margin-top:14px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="real-fb" ${poolData.real_fallback?'checked':''} onchange="setRealFallback(this.checked)" style="width:16px;height:16px;accent-color:var(--acc)">
        Auto-provision a real web MT5 demo when the pool has no matching account
      </label>
      <p class="muted" style="font-size:12px;margin-top:6px">Runs before the simulated fallback. Each open takes ~20–30 s and needs the browser channel above.</p>
    </div>

    <div class="sec-card card-md">
      <h3>Add account manually</h3>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">Accounts you created at your broker. Paste the credentials here. Provisioning assigns the first free account of a matching size when a challenge is purchased.</p>
      <div class="pool-form">
        <input id="pl-login" class="inp" inputmode="numeric" autocomplete="off" placeholder="MT5 login">
        <input id="pl-pass" class="inp" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="Password">
        <input id="pl-server" class="inp" spellcheck="false" autocapitalize="off" placeholder="Server">
        <select id="pl-size" class="inp">${sizeOptions(null)}</select>
      </div>
      <button class="btn-p" onclick="addPool()">+ Add to pool</button>
    </div>
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic green">${ICO.bank}</div>
        <div><div class="lbl">Free accounts</div><div class="val">${free}</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.users}</div>
        <div><div class="lbl">Assigned</div><div class="val">${rows.length-free}</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.layers}</div>
        <div><div class="lbl">Total in pool</div><div class="val">${rows.length}</div></div></div>
    </div>
    <div id="pool-list">${poolListHtml()}</div>`;
 },

 async settings(){
  const [s,pb,bg,rc]=await Promise.all([api('/api/stats'),api('/api/admin/payout-engine'),
    api('/api/admin/bogo-promo').catch(()=>({enabled:false})),
    api('/api/admin/reach').catch(()=>null)]);
  const brakuje=[!pb.telegram_ready?'Telegram channel':null,!pb.renderer_ready?'certificate renderer':null].filter(Boolean);
  $('view').innerHTML=`
    <div class="card-cols">
    <div class="sec-card" style="max-width:560px"><h3>Payout BOT</h3>
      <div class="chip-row" style="margin-bottom:12px">
        <span class="status ${pb.enabled?'funded':'pending'}"><span class="dot"></span>${pb.enabled?'running':'off'}</span>
        <span class="chip">window <b>${String(pb.win_from).padStart(2,'0')}:00&ndash;${String(pb.win_to).padStart(2,'0')}:00 ET</b></span>
        <span class="chip">today's slot <b>${esc(pb.today_slot_et||'--:--')} ET</b></span>
        <span class="chip">on landing <b>${pb.lp_pct}%</b></span>
        <span class="chip">last run <b>${esc(pb.last_day||'never')}</b></span>
        ${pb.last_result?`<span class="chip" ${/FAILED/.test(pb.last_result)?'style="border-color:var(--red-line);color:var(--red)"':''}>last post <b>${esc(pb.last_result)}</b></span>`:''}
      </div>
      ${brakuje.length?`<div class="warn-box" style="margin:0 0 12px">
        <div><b>Not configured yet: ${brakuje.join(' and ')}</b>
        The payout and its certificate are still created, but nothing is published.
        Set <span class="mono">TELEGRAM_BOT_TOKEN</span>, <span class="mono">TELEGRAM_CHAT_ID</span>
        and <span class="mono">SHOT_API_URL</span> in the environment.</div></div>`:''}
      <div class="pool-form">
        <div><label class="muted" style="font-size:12px">Window from (ET hour)</label>
          <input id="pb-from" class="inp" type="number" min="0" max="23" step="1" value="${pb.win_from}"></div>
        <div><label class="muted" style="font-size:12px">Window to (ET hour)</label>
          <input id="pb-to" class="inp" type="number" min="0" max="23" step="1" value="${pb.win_to}"></div>
        <div><label class="muted" style="font-size:12px">Chance of landing page %</label>
          <input id="pb-lp" class="inp" type="number" min="0" max="100" step="1" value="${pb.lp_pct}"></div>
        <div><label class="muted" style="font-size:12px">Profit min %</label>
          <input id="pb-min" class="inp" type="number" min="0.5" max="40" step="0.1" value="${pb.gross_min_pct}"></div>
        <div><label class="muted" style="font-size:12px">Profit max %</label>
          <input id="pb-max" class="inp" type="number" min="0.5" max="40" step="0.1" value="${pb.gross_max_pct}"></div>
      </div>
      <div style="margin-bottom:12px"><label class="muted" style="font-size:12px">Account sizes</label>
        <input id="pb-sizes" class="inp" value="${pb.sizes.map(n=>n.toFixed(0)).join(',')}" placeholder="50000,100000,200000"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn-p" onclick="savePayoutBot()">Save settings</button>
        <button class="btn-o" onclick="togglePayoutBot(${pb.enabled?'false':'true'})">${pb.enabled?'Turn off':'Turn on'}</button>
        <button class="btn-o" onclick="runPayoutBot()">Run once now</button>
      </div>
      <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
        Creates <b>one payout a day</b> with today's date and a funded archive account behind it.
        The posting minute is <b>drawn fresh every day</b> inside your window (US Eastern,
        DST-aware), so posts never land at the same time twice. Site traffic releases the post at
        that exact minute; with zero traffic it falls back to the daily tick, which fires from the
        start of the window. <b>Every payout gets a public certificate</b> and is posted to
        the channel; only the share above lands on the certificate strip on the landing page, so it
        does not fill up with the same entries.
        <b>Run once now</b> replaces today's automatic run rather than adding to it.</p></div>

    ${reachCardHtml(rc)}

    <div class="sec-card" style="max-width:560px"><h3>Buy 1 Get 1 Free</h3>
      <div class="chip-row" style="margin-bottom:12px">
        <span class="status ${bg.enabled?'funded':'pending'}"><span class="dot"></span>${bg.enabled?'running':'off'}</span>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn-p" onclick="setBogoPromo(${bg.enabled?'false':'true'})">${bg.enabled?'Turn off':'Turn on'}</button>
      </div>
      <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
        While it's on, a bar on the public site announces the promo and <b>every paid order
        automatically gets a second account of the same size</b> — including checkout purchases,
        not just manual orders. Orders are stamped when they are <b>created</b>: turning the promo
        off later does not take BOGO away from a customer who already holds a payment link, and
        turning it on does not add it to orders issued before. For a single lead you can always
        override this with the checkbox in the New order window or on the order itself.</p></div>

    <div class="sec-card" style="max-width:560px"><h3>Notifications</h3>
      <div class="mod-row">
        <div><div class="lbl">Push to this device</div>
          <div class="muted" style="font-size:11.5px" id="push-state">New leads, claims and follow-ups — straight to this device.</div></div>
        <button class="btn-p" id="push-btn" onclick="toggleAdminPush()">Enable</button>
      </div>
      <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)">
        <div class="lbl" style="font-size:12px;color:var(--muted)">What buzzes your phone <span style="font-weight:400">(this account, every device — the full list of admin notifications)</span></div>
        <div id="push-cats">${pushCatsHtml()}</div>
        <p class="muted" style="font-size:11.5px;margin-top:8px">Muted categories still land in the bell — they just stop buzzing.</p>
      </div>
      <div id="tg-identity">${tgIdentityHtml()}</div></div>

    <div class="sec-card" style="max-width:560px"><h3>Admin access</h3>
      <p class="muted" style="font-size:13px;margin:6px 0 12px">You are signed in with an administrator account. Access is granted by the <span class="mono">is_admin</span> flag on the account, not by a shared token.</p>
      <div class="kv"><span>Signed in as</span><b>${esc(ME?.email||'—')}</b></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">
        <button class="btn-o" onclick="signOut()">Sign out</button>
      </div></div>

    <div class="sec-card" style="max-width:560px"><h3>Runtime</h3>
      <div style="margin-top:8px">
        <div class="kv"><span>Payments</span><b>${esc(s.stripe)}</b></div>
        <div class="kv"><span>Accounts provisioning</span><b>${s.provisioning??0}</b></div>
        <div class="kv"><span>Free pool accounts</span><b>${s.pool_free??0}</b></div>
      </div>
      <p class="muted" style="font-size:12px;margin-top:12px">Runtime values come from the server's environment. Change them in <span class="mono">.env</span> and restart.</p></div>

    <div class="sec-card" style="max-width:560px"><h3>Links</h3>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
        <a class="btn-o sm" href="/" target="_blank">Public website</a>
        <a class="btn-o sm" href="/docs" target="_blank">API docs</a>
      </div></div>
    </div>`;
  paintPushCard();
  paintTgIdentity();
 },

 async telemetry(){
  const d=await api('/api/admin/telemetry');
  const items=d.items||[];
  $('view').innerHTML=items.length?`
    <p class="muted" style="font-size:12.5px;margin-bottom:10px">Click a row to see the individual events; click a trader email inside to see everything that user did.</p>
    <div class="tbl-wrap tw-sm rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.telemetry">
    <thead><tr><th>Day</th><th>Event</th><th style="text-align:right">Count</th>
      <th style="text-align:right">Unique traders</th></tr></thead>
    <tbody>${items.map(i=>`<tr class="clickable" onclick="openTelemetryDetail('${jsq(i.day)}','${jsq(i.name)}')">
      <td class="num" data-l="Day">${esc(i.day)}</td><td class="rt-main" data-l="Event">${esc(i.name)}</td>
      <td class="num" style="text-align:right" data-l="Count">${i.count}</td>
      <td class="num" style="text-align:right" data-l="Traders">${i.traders}</td></tr>`).join('')}
    </tbody></table></div>`
    :`<div class="empty"><h3>No events yet</h3>
      <p>Product events (signups, logins, orders, check-ins) land here as traders use the platform.</p></div>`;
 },
};


/* ---------- accounts: filters + table ---------- */
/* Search boxes re-render the whole view on every keystroke, so the input is
   torn down and rebuilt and a bare focus() lands with the caret at position 0
   — typing came out reversed ("ale" -> "ela"). Focus AND restore the caret. */
function qFocus(id,pos){
  const el=document.getElementById(id); if(!el)return;
  el.focus();
  const p=(pos==null?el.value.length:Math.min(pos,el.value.length));
  try{el.setSelectionRange(p,p)}catch(e){}
}
/* One search box, one behaviour: caret survives the re-render and a clear "x"
   sits inside the field on the right whenever there is anything to clear. */
function searchBox(id,stateKey,render,ph){
  const val=window[stateKey]||'';
  /* Jedyny generator pol wyszukiwania w panelu — dopisanie tu `zapiszPozniej()`
     sprawia, ze wpisana fraza przezywa F5 we WSZYSTKICH widokach naraz. */
  return `<span class="q-wrap"><input class="inp" id="${id}" placeholder="${ph}" value="${esc(val)}"
      oninput="window.${stateKey}=this.value;const p=this.selectionStart;${render}();qFocus('${id}',p);zapiszPozniej()">
    ${val?`<button class="q-x" type="button" aria-label="Clear search" title="Clear"
      onclick="window.${stateKey}='';${render}();qFocus('${id}');zapiszStan(false)">&times;</button>`:''}</span>`;
}

/* ---------- mail: dziennik doręczeń ----------
   SMTP pada po cichu (notify łapie wyjątek, żeby nie wywrócić requestu) —
   ta zakładka to jedyne miejsce, gdzie „mail nie wyszedł" widać, zanim
   zgłosi to klient. Fallback jest już pod ręką: Copy link przy zaproszeniu
   do portalu, Pay link przy zamówieniu, poświadczenia MT5 w karcie konta. */
function renderMailLog(){
  const d=window._mailLog||{};
  const list=d.entries||[];
  const q=(window._mailQ||'').toLowerCase(), f=window._mailFilter||'all';
  const rows=list.filter(m=>(f==='all'||(f==='failed'?!m.ok:m.ok))&&
    (!q||(m.to||'').toLowerCase().includes(q)||(m.event||'').toLowerCase().includes(q)
      ||(m.subject||'').toLowerCase().includes(q)));
  $('view').innerHTML=`
    <div class="toolbar">
      ${searchBox('mail-q','_mailQ','renderMailLog','Search recipient, subject or template…')}
      <div class="seg">${[['all','All'],['sent','Sent'],['failed','Failed']]
        .map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._mailFilter='${f===k?'all':k}';renderMailLog()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
    </div>
    ${d.failed_7d?`<p class="lead-statline" style="color:var(--gold)">⚠ ${d.failed_7d} e-mail${d.failed_7d>1?'s':''} failed in the last 7 days — deliver the content another way (copy the portal-invite link, the pay link or the MT5 credentials from the account card), then check the SMTP settings.</p>`:''}
    ${rows.length?`<div class="tbl-wrap tw-wide rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.maillog">
      <thead><tr><th>Date</th><th>To</th><th>Subject</th><th>Template</th><th>Status</th></tr></thead>
      <tbody>${rows.map(m=>`<tr>
        <td class="muted" data-l="Date" data-sort="${esc(m.ts||'')}">${dstr(m.ts)}</td>
        <td class="rt-main" data-l="To">${esc(m.to||'—')}</td>
        <td data-l="Subject">${esc(m.subject||'—')}</td>
        <td class="muted" data-l="Template">${esc((m.event||'—').replace(/_/g,' '))}</td>
        <td data-l="Status"><span class="status ${m.ok?'paid':'failed'}"><span class="dot"></span>${m.ok?'sent':'failed'}</span>
          ${m.error?`<div class="muted" style="font-size:11px;max-width:260px;word-break:break-word">${esc(m.error)}</div>`:''}</td></tr>`).join('')}
      </tbody></table></div>`
    :list.length?`<div class="empty"><h3>No e-mails match</h3><p>Try a different search or filter.</p></div>`
    :`<div class="empty"><h3>Nothing sent yet</h3><p>Every e-mail the platform sends will be listed here, including the ones that fail.</p></div>`}`;
}

/* ---------- tickets: search + filters + list ---------- */
function renderTickets(){
  const list=window._tickets||[];
  window._tickFilter=window._tickFilter||'all';
  const q=(window._tickQ||'').toLowerCase(), f=window._tickFilter;
  const rows=list.filter(t=>(f==='all'||t.status===f)&&
    (!q||String(t.id).includes(q)||(t.subject||'').toLowerCase().includes(q)
      ||(t.trader_email||'').toLowerCase().includes(q)));
  /* X siedzi W wierszu, ktory sam otwiera rozmowe, wiec musi zatrzymac klikniecie —
     inaczej kazde usuniecie otwieraloby przy okazji watek. Do `delTicket` idzie
     samo id: temat bywa z apostrofem ("Can't log in"), a wstrzykniety w inline
     onclick rozwalilby ten atrybut. */
  const row=t=>`
    <div class="ticket-row" onclick="openTicket(${t.id})">
      <div class="tile-ic ${t.status==='open'?'orange':t.status==='answered'?'green':'gray'}" style="width:36px;height:36px;flex:0 0 36px">${ICO.chat}</div>
      <div class="sub"><b>${esc(t.subject)}</b>
        <span>#${t.id} · ${esc(t.trader_email||'—')} · ${t.messages} message${t.messages>1?'s':''} · ${dstr(t.last_ts)}</span></div>
      <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${esc(t.status)}</span>
      ${XBTN(`event.stopPropagation();delTicket(${t.id})`,'Delete this ticket and its conversation')}
    </div>`;
  const seg=[['all','All'],['open','Open'],['answered','Answered'],['closed','Closed']];
  const active=rows.filter(t=>t.status!=='closed'), closed=rows.filter(t=>t.status==='closed');
  $('view').innerHTML=`
    <div class="toolbar">
      ${searchBox('tick-q','_tickQ','renderTickets','Search subject, e-mail or #…')}
      <div class="seg">${seg.map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._tickFilter='${f===k?'all':k}';renderTickets()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
    </div>`
    +(active.length?`<div class="tbl-wrap">`+active.map(row).join('')+`</div>`
      :closed.length?'' // samo History: pusta sekcja "open" nic nie wnosi
      :q||f!=='all'?`<div class="empty"><h3>No tickets match</h3><p>Try a different search or filter.</p></div>`
      :`<div class="empty"><h3>No open tickets</h3><p>Support conversations started by traders appear here.</p></div>`)
    +(closed.length?`<div class="sec-card" style="margin-top:18px">
      <h3>History</h3>
      <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Closed tickets. Click to review the conversation.</p>
      <div class="tbl-wrap">`+closed.map(row).join('')+`</div></div>`:'');
}

function renderAccounts(){
  const list=window._accs||[];
  const q=(window._accQ||'').toLowerCase();
  const f=window._accFilter;
  const rows=list.filter(a=>(f==='all'||a.status===f)&&
    (!q||String(a.login).includes(q)||(a.trader_name||'').toLowerCase().includes(q)
      ||(a.trader_email||'').toLowerCase().includes(q)||(a.product_key||'').includes(q)));
  const seg=[['all','All'],['active','Evaluation'],['funded','Funded'],['failed','Failed'],['provisioning','Provisioning']];
  $('view').innerHTML=`
    <div class="toolbar">
      ${searchBox('acc-q','_accQ','renderAccounts','Search login, trader, email or plan…')}
      <div class="seg">${seg.map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._accFilter='${f===k?'all':k}';renderAccounts()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}${impPill()}</span>
    </div>
    ${rows.length?`<div class="tbl-wrap tw-wide rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.accounts.v2">
      <thead><tr><th>Created</th><th>Paid</th><th>Login</th><th>Trader</th><th>Plan</th><th>Phase</th><th>Status</th>
        <th title="Trade BOT">Bot</th>
        <th style="text-align:right">Balance</th><th style="text-align:right">Equity</th><th style="text-align:right">P&amp;L</th>
        <th>Daily</th><th>Max DD</th><th class="no-sort"></th></tr></thead>
      <tbody>${rows.map(a=>{const m=a.metrics||{};
        return `<tr class="clickable" onclick="openAccount(${a.id})">
          <td class="muted rt-hide" style="white-space:nowrap" data-l="Created" data-sort="${esc(a.created_at||'')}">${a.created_at?dstr(a.created_at):'—'}</td>
          <td class="muted rt-hide" style="white-space:nowrap" data-l="Paid" data-sort="${esc(a.paid_at||'')}">${a.paid_at?dstr(a.paid_at):'—'}</td>
          <td class="num rt-main" style="font-weight:600" data-l="Login">${a.status==='provisioning'?'<span class="muted">pending…</span>':esc(a.login)}</td>
          <td data-l="Trader">${esc(a.trader_name||'—')}${a.trader_email?`<div class="muted" style="font-size:11px">${esc(a.trader_email)}</div>`:''}</td>
          <td class="muted" data-l="Plan">${esc(a.product_key)}</td>
          <td class="muted" data-l="Phase">${PHASE_LBL[a.phase]||esc(a.phase)}</td>
          <td data-l="Status"><span class="status ${esc(a.status)}"><span class="dot"></span>${STATUS_LBL[a.status]||esc(a.status)}</span></td>
          <td data-l="Bot" data-sort="${a.bot_enabled?(a.bot_paused?1:2):0}" title="${a.bot_enabled?(a.bot_paused?'Trade BOT paused':'Trade BOT running'):'Trade BOT off'}">${a.bot_enabled?(a.bot_paused?'🟡':'🟢'):'🔴'}</td>
          <td class="num" style="text-align:right" data-l="Balance">$${fmt(a.balance)}</td>
          <td class="num rt-hide" style="text-align:right" data-l="Equity">$${fmt(a.equity)}</td>
          <td class="num ${(m.profit_pct||0)>=0?'up':'down'}" style="text-align:right" data-l="P&amp;L">${(m.profit_pct||0)>=0?'+':''}${(m.profit_pct||0).toFixed(2)}%</td>
          <td data-l="Daily" data-sort="${(m.daily_loss_used_pct||0).toFixed(2)}">${mini(m.daily_loss_used_pct)}</td>
          <td data-l="Max DD" data-sort="${(m.overall_dd_used_pct||0).toFixed(2)}">${mini(m.overall_dd_used_pct)}</td>
          <td class="rt-acts" style="text-align:right" onclick="event.stopPropagation()">${
            XBTN(`deleteAccountRow(${a.id},'${jsq(a.login)}','${jsq(a.trader_name||'')}')`,'Delete account')}</td></tr>`}).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>No accounts match</h3><p>Try a different search or filter.</p></div>`}`;
}

/* ---------- kyc: seg buttons need a global to call ---------- */
function renderKyc(){VIEWS._kycRender()}

/* Kanal FREE: darmowy challenge dostaje ktos, kogo jeszcze nie znamy, a prezent
   sciaga dublerow — wiec przed dalszym korzystaniem z panelu ma sie zweryfikowac.
   Wysylka jest hurtowa i NIEODWRACALNA, dlatego karta pokazuje imienna liste
   przed klikiem, a nie sam licznik. */
function freeChannelCard(){
  const f=window._kycFree||{};
  const czeka=f.waiting||[], gotowe=f.done||[];
  if(!czeka.length&&!gotowe.length)return '';
  const lista=czeka.slice(0,12).map(t=>`<span class="chip">${esc(t.email)}</span>`).join(' ')
    +(czeka.length>12?`<span class="chip">+${czeka.length-12} more</span>`:'');
  const wstrzymani=gotowe.filter(t=>t.kyc_locked&&t.kyc_status!=='approved').length;
  return `<div class="sec-card card-md" style="margin-bottom:18px">
    <h3>Free challenge — identity checks</h3>
    <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Traders who were handed a free account.
      Asking pauses their dashboard until you approve the documents — the MT5 account keeps trading either way.</p>
    <div class="kv"><span>Waiting for a request</span><b>${czeka.length}</b></div>
    <div class="kv"><span>Already asked</span><b>${gotowe.length}</b></div>
    <div class="kv"><span>Dashboard paused now</span><b>${wstrzymani}</b></div>
    ${czeka.length?`<div class="chip-row" style="margin:12px 0">${lista}</div>
      <button class="btn-p" onclick="askFreeChannelKyc(this)">Ask all ${czeka.length} to verify</button>`
    :'<p class="muted" style="font-size:12.5px;margin-top:10px">Everyone from the free channel has already been asked.</p>'}
  </div>`;
}
/* Serwer wysyla paczkami (limit czasu funkcji), wiec petla chodzi do `left===0`.
   Przerwana w polowie nie szkodzi: ponowne klikniecie zaczyna od tych, ktorzy
   maila jeszcze nie dostali. */
async function askFreeChannelKyc(btn){
  const czeka=(window._kycFree||{}).waiting||[];
  if(!czeka.length)return;
  if(!await askConfirm({title:`Ask ${czeka.length} free-challenge trader${czeka.length===1?'':'s'} to verify?`,
    body:'Each of them gets an e-mail with a link to the Verification tab, and their dashboard stays paused '
      +'until you approve the documents. Their trading account keeps running. E-mails cannot be unsent.',
    ok:'Send the requests',requireText:'SEND'}))return;
  btn.disabled=true;
  let wyslane=0;
  try{
    for(;;){
      const r=await api('/api/admin/kyc/free-channel/request',{method:'POST'});
      wyslane+=r.count||0;
      btn.textContent=`Sending… ${wyslane}/${czeka.length}`;
      if(!r.count||!r.left)break;
    }
    toast(`Verification requested from ${wyslane} trader${wyslane===1?'':'s'}.`,'ok');
  }catch(e){toast(`Error after ${wyslane} sent: ${e.message}`,'err')}
  go('kyc');
}

/* ---------- pool: search + state filter (list only — the forms above keep
   whatever the admin typed, so only #pool-list re-renders) ---------- */
function poolSizeOptions(chosen){
  return (window._poolSizes||[]).map(r=>`<option value="${r}"${Number(chosen)===r?' selected':''}>$${fmt0(r)}</option>`).join('');
}
function poolState(p){return p.retired_reason?'retired':p.claimed?'assigned':'free'}
function poolListHtml(){
  const rows=window._pool||[];
  if(!rows.length)return `<div class="empty"><h3>Pool is empty</h3><p>Add the MT5 accounts you created at your broker. Provisioning takes credentials only from here.</p></div>`;
  const q=(window._poolQ||'').toLowerCase();
  const f=window._poolFilter||'all';
  const list=rows.filter(p=>(f==='all'||poolState(p)===f)&&
    (!q||String(p.platform_login||'').toLowerCase().includes(q)
      ||(p.platform_server||'').toLowerCase().includes(q)
      ||(p.trader_email||'').toLowerCase().includes(q)));
  return `<div class="toolbar">
      ${searchBox('pool-q','_poolQ','renderPoolList','Search login, server or trader…')}
      <div class="seg">${[['all','All'],['free','Free'],['assigned','Assigned'],['retired','Retired']]
        .map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._poolFilter='${f===k?'all':k}';renderPoolList()">${l}</button>`).join('')}</div>
      <span class="count-pill">${list.length} of ${rows.length}</span>
    </div>`
    +(list.length?`<div class="tbl-wrap tw-wide rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.pool">
      <thead><tr><th>#</th><th>Login</th><th class="no-sort">Password</th><th>Server</th><th>Size</th><th>State</th><th>Assigned to</th><th>When</th><th class="no-sort"></th></tr></thead>
      <tbody>${list.map(p=>`<tr>
        <td class="num rt-hide" data-l="#">${p.id}</td><td class="num rt-main" data-l="Login">${esc(p.platform_login)}${p.simulated?'<div class="muted" style="font-size:10.5px;letter-spacing:.06em">SIMULATED</div>':'<div class="muted" style="font-size:10.5px;letter-spacing:.06em">REAL MT5</div>'}</td>
        <td data-l="Password">${p.platform_password?`<span class="mono" style="cursor:pointer" title="Click to reveal"
          onclick="this.textContent=this.textContent==='••••••••'?this.dataset.p:'••••••••'" data-p="${esc(p.platform_password)}">••••••••</span>`:'<span class="muted">—</span>'}</td>
        <td class="muted" data-l="Server">${esc(p.platform_server)}</td><td class="num" data-l="Size">$${fmt0(p.account_size)}</td>
        <td data-l="Status">${p.retired_reason?`<span class="status failed"><span class="dot"></span>retired</span>`
          :p.claimed?`<span class="status pending"><span class="dot"></span>assigned</span>`
          :'<span class="status funded"><span class="dot"></span>free</span>'}</td>
        <td data-l="Assigned">${p.claimed?`${esc(p.trader_email||'—')}<div class="muted" style="font-size:11.5px">${p.retired_reason?esc(p.retired_reason)+' — not reusable':`account ${accLink(p.claimed_by_account_id)}${p.account_status?' · '+esc(p.account_status):''}`}</div>`:'<span class="muted">—</span>'}</td>
        <td class="muted" data-l="When" data-sort="${esc(p.claimed_at||'')}">${p.claimed_at?dstr(p.claimed_at):'—'}</td>
        <td class="rt-acts" style="white-space:nowrap">
          <button class="btn-o sm" onclick="editPool(${p.id})">Edit</button>
          ${p.claimed&&!p.retired_reason?''
            :' '+XBTN(`delPool(${p.id},'${jsq(p.platform_login)}',${p.retired_reason?1:0})`,
                      p.retired_reason?'Delete this retired entry':'Remove from pool')}</td></tr>
        <tr id="pool-edit-${p.id}" class="tr-sub" style="display:none"><td colspan="9" style="background:var(--bg)">
          <div class="pool-form" style="margin:6px 0">
            <input id="ed-login-${p.id}" class="inp" inputmode="numeric" value="${esc(p.platform_login)}" placeholder="MT5 login">
            <input id="ed-pass-${p.id}" class="inp" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="New password (leave empty to keep)">
            <input id="ed-server-${p.id}" class="inp" spellcheck="false" autocapitalize="off" value="${esc(p.platform_server)}" placeholder="Server">
            ${p.claimed?'':`<select id="ed-size-${p.id}" class="inp">${poolSizeOptions(p.account_size)}</select>`}
          </div>
          <button class="btn-p sm" onclick="savePool(${p.id},${p.claimed})">Save</button>
          <button class="btn-o sm" onclick="editPool(${p.id})">Cancel</button>
          ${p.claimed?`<span class="muted" style="font-size:12px;margin-left:10px">Assigned — new credentials also go to the trader's account; size is locked.</span>`:''}
        </td></tr>`).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>No accounts match</h3><p>Try a different search or filter.</p></div>`);
}
function renderPoolList(){const el=document.getElementById('pool-list');if(el)el.innerHTML=poolListHtml()}

/* ---------- payouts: status filter + table ----------
   NB: renderPayouts(id) already exists (the slide-over payout card),
   hence the -View suffix — a second declaration would shadow it. */
function renderPayoutsView(){
  const list=window._payReqs||[];
  const f=window._payFilter||'all';
  const q=(window._payQ||'').toLowerCase();
  const rows=list.filter(r=>(f==='all'||r.status===f)&&
    (!q||String(r.account_login||'').toLowerCase().includes(q)
      ||(r.trader_email||'').toLowerCase().includes(q)
      ||(r.method||'').toLowerCase().includes(q)||(r.status||'').includes(q)));
  const seg=[['all','All'],['pending','Pending'],['approved','Approved'],['paid','Paid'],['rejected','Rejected']];
  $('view').innerHTML=`
    <div class="toolbar">
      ${list.length?`${searchBox('pay-q','_payQ','renderPayoutsView','Search account, trader or method…')}
      <div class="seg">${seg.map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._payFilter='${f===k?'all':k}';renderPayoutsView()">${l}</button>`).join('')}</div>`:''}
      <button class="btn-o sm" onclick="openPayoutImport()">Import history</button>
      ${list.length?`<span class="count-pill">${rows.length} of ${list.length}</span>`:''}
    </div>`+(rows.length?`<div class="tbl-wrap tw-wide rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.payouts">
    <thead><tr><th>Date</th><th>Account</th><th>Trader</th><th>Profit</th><th>Trader share</th><th>Method</th><th>Status</th><th class="no-sort">Certificate</th><th class="no-sort"></th></tr></thead>
    <tbody>${rows.map(r=>`<tr>
      <td class="muted" data-l="Date" data-sort="${esc(r.ts||'')}">${dstr(r.ts)}</td><td class="num rt-main" data-l="Account">${accLink(r.account_id,r.account_login)}${r.express?' <span class="express-pill" title="Express Payout add-on — this request jumps the review queue">EXPRESS</span>':''}</td>
      <td data-l="Trader">${esc(r.trader_email||'—')}</td>
      <td class="num" data-l="Profit">$${fmt(r.profit_amount)}</td><td class="num up" data-l="Share">$${fmt(r.trader_share)}</td>
      <td data-l="Method">${(()=>{const d=r.details||{};
        const label=r.method==='usdt'?'USDT':r.method==='wise'?'Wise':'Bank';
        const info=r.method==='usdt'?[d.network,d.address].filter(Boolean).join(' · ')
          :r.method==='wise'?(d.email||'')
          :[d.holder,d.iban,d.swift,d.bank_name].filter(Boolean).join(' · ');
        return `${esc(label)}${info?`<div class="muted mono" style="font-size:11px;max-width:260px;word-break:break-all">${esc(info)}</div>`:''}`})()}</td>
      <td data-l="Status"><span class="status ${r.status==='paid'?'paid':r.status==='pending'?'pending'
        :r.status==='approved'?'active':'failed'}"><span class="dot"></span>${esc(r.status)}</span>
        ${r.status==='rejected'&&r.reject_reason?`<div class="muted" style="font-size:11px;max-width:200px">${esc(r.reject_reason)}</div>`:''}</td>
      <td class="rt-acts" style="white-space:nowrap">${r.kind!=='payout'?'<span class="muted">—</span>'
        :r.cert_url
          ?`<a class="btn-o sm" href="${r.cert_url}" target="_blank">Open</a>
            <button class="btn-o sm" onclick="copyCert('${location.origin}${r.cert_url}')">Copy link</button>
            <button class="btn-o sm" onclick="setCertLp(${r.id},${r.show_on_lp?'false':'true'})"
              title="${r.show_on_lp?'Currently shown in the landing-page payout strip':'Currently not on the landing page'}">
              ${r.show_on_lp?'Take off the LP':'Put on the LP'}</button>
            <button class="btn-o sm" onclick="revokeCert(${r.id})">Revoke</button>`
          :`<button class="btn-o sm" onclick="askCertLp(${r.id})">Generate</button>`}</td>
      <td class="rt-acts" style="text-align:right;white-space:nowrap">${r.kind==='request'&&r.status==='pending'
        ?`<button class="btn-p sm" onclick="approvePayout(${r.id},this)">Approve &amp; pay</button>
          <button class="btn-o sm" onclick="rejectPayout(${r.id},this)">Reject</button>`
        :XBTN(`deletePayoutRow('${r.kind}',${r.id},${r.trader_share},'${jsq(r.account_login||'')}')`,
              r.kind==='payout'?'Delete payout':'Delete request')}</td></tr>`).join('')}
    </tbody></table></div>
    <p class="muted" style="font-size:11.5px;margin-top:10px">Approving pays the trader share and refunds the challenge fee on the first payout for that account.</p>`
    :list.length?`<div class="empty"><h3>No payouts match</h3><p>Try a different search or filter.</p></div>`
    :`<div class="empty"><h3>No payouts yet</h3><p>Payouts you issue and requests from funded traders both land here.</p></div>`);
}

/* ---------- orders: search + payment flags ---------- */
function renderOrders(){
  const list=window._orders||[];
  const q=(window._ordQ||'').toLowerCase();
  const f=window._ordFilter||'all';
  const rows=list.filter(o=>
    (f==='all'||(f==='awaiting'?(o.flag==='awaiting_crypto'&&o.status==='pending'):o.status===f))&&
    (!q||(o.trader_email||'').toLowerCase().includes(q)
    ||(o.product_key||'').includes(q)||(o.status||'').includes(q)
    ||(o.flag||'').includes(q)||String(o.id)===q));
  /* Kafelki opisuja TO, CO WIDAC pod nimi — czyli zbior po filtrze i szukajce.
     Wczesniej liczyly sie z calej listy, wiec przelaczenie na "Failed" zostawialo
     nad pusta tabela pelny przychod. Ten sam blad byl w portalu na Challenges. */
  const paid=rows.filter(o=>o.status==='paid');
  /* Granty BOGO ($0, provider "grant") to nie sprzedaz: w liczniku i sredniej
     zanizalyby srednia i zawyzaly liczbe oplaconych zamowien. */
  const sold=paid.filter(o=>o.provider!=='grant');
  const revenue=sold.reduce((s,o)=>s+o.amount_usd,0);
  const avg=sold.length?revenue/sold.length:0;
  const zawezone=rows.length!==list.length;
  const podpis=zawezone?`<div class="sub">of ${list.length} total</div>`:'';
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic green">${ICO.dollar}</div>
        <div><div class="lbl">Revenue</div><div class="val">$${fmt0(revenue)}</div><div class="sub">${sold.length} paid orders</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.file}</div>
        <div><div class="lbl">Orders ${zawezone?'shown':'total'}</div><div class="val">${rows.length}</div>
          <div class="sub">${rows.length-paid.length} unpaid</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.trend}</div>
        <div><div class="lbl">Average order</div><div class="val">$${fmt0(avg)}</div>${podpis}</div></div>
    </div>
    <div class="toolbar">
      ${searchBox('ord-q','_ordQ','renderOrders','Search email, product, status…')}
      <div class="seg">${[['all','All'],['paid','Paid'],['pending','Pending'],['awaiting','Awaiting crypto'],['failed','Failed']]
        .map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._ordFilter='${f===k?'all':k}';renderOrders()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
      <button class="btn-p sm" onclick="openManualOrder()"
        title="Record an order the customer pays outside Stripe (crypto, transfer)">+ New order</button>
    </div>
    ${rows.length?`<div class="tbl-wrap tw-wide rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.orders">
      <thead><tr><th>#</th><th>Date</th><th>Trader</th><th>Product</th><th>Amount</th><th>Provider</th><th>Status</th><th>Account</th><th class="no-sort"></th></tr></thead>
      <tbody>${rows.map(o=>`<tr>
        <td class="num rt-hide" data-l="#">${o.id}</td><td class="muted" data-l="Date" data-sort="${esc(o.created_at||'')}">${dstr(o.created_at)}</td>
        <td class="rt-main" data-l="Trader">${esc(o.trader_email||'—')}</td>
        <td data-l="Product">${esc(o.product_key)}${o.bogo?` <span class="up" style="font-size:11px" title="Buy 1 Get 1 Free — paying this order also creates a free second account of the same size">+1 free</span>`:''}</td>
        <td class="num" data-l="Amount">$${fmt(o.amount_usd)}${o.coupon?` <span class="up" style="font-size:11px">(${esc(o.coupon)})</span>`:''}</td>
        <td class="muted rt-hide" data-l="Provider">${esc(o.provider)}</td>
        <td data-l="Status"><span class="status ${o.status==='paid'?'paid':o.status==='failed'?'failed':'pending'}"><span class="dot"></span>${esc(o.status)}</span>
          ${o.status==='pending'&&o.flag==='awaiting_crypto'?'<div class="muted" style="font-size:11px;white-space:nowrap">⏳ awaiting crypto</div>':''}
          ${o.flag==='bogo_grant_failed'?'<div style="font-size:11px;white-space:nowrap;color:var(--red)" title="The paid order promised a free second account, but creating it failed. Use Grant challenge to add it by hand.">⚠ BOGO grant failed — grant manually</div>':''}
          ${o.flag==='credits_shortfall'?'<div style="font-size:11px;white-space:nowrap;color:var(--red)" title="The order was discounted with store credits, but a parallel checkout had already spent them — the company covered the difference.">⚠ credits shortfall — discount without coverage</div>':''}
          ${o.status!=='paid'&&o.payment_address?`<div class="muted" title="${esc((o.payment_network?o.payment_network+' · ':'')+o.payment_address)}"
            style="font-size:11px;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.payment_network?o.payment_network+' · ':'')}${esc(o.payment_address)}</div>`:''}
          ${o.status==='failed'&&o.fail_reason?`<div class="muted" style="font-size:11px;max-width:200px">${esc(o.fail_reason)}</div>`:''}</td>
        <td class="num" data-l="Account">${accLink(o.account_id)}</td>
        <td class="rt-acts" style="white-space:nowrap">${o.status==='paid'?'':`
          ${o.status==='pending'?`<button class="btn-o sm" onclick="payLink(${o.id})"
            title="Copy a card-payment link for this order — send it to the customer">Pay link</button>
          <button class="btn-o sm" onclick="flagOrder(${o.id},'${o.flag==='awaiting_crypto'?'':'awaiting_crypto'}')"
            title="${o.flag==='awaiting_crypto'?'Clear the awaiting-crypto flag':'Mark as awaiting crypto payment'}">${o.flag==='awaiting_crypto'?'Clear flag':'Crypto?'}</button>
          <button class="btn-o sm" onclick="toggleOrderBogo(${o.id},${o.bogo?'false':'true'})"
            title="${o.bogo?'Remove the free second account from this order':'Buy 1 Get 1 Free — add a free second account of the same size when this order is paid'}">${o.bogo?'BOGO ✓':'BOGO'}</button>
          <button class="btn-o sm" onclick="markOrderFailed(${o.id})" title="Payment is not coming, close the order with a reason">Mark failed</button>`:''}
          <button class="btn-p sm" onclick="markOrderPaid(${o.id})" title="Confirm the payment arrived, creates the account">Mark paid</button>`}
          ${XBTN(`deleteOrderRow(${o.id},'${jsq(o.trader_email||'')}',${o.amount_usd},${o.account_id||0})`,'Delete order')}</td></tr>`).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>${list.length?'No orders match':'No orders yet'}</h3>${list.length?'<p>Try a different search or filter.</p>':''}</div>`}`;
}

/* ---------- Leads ----------
   Applications from the landing page. Two kinds of column live side by side and
   the difference matters: `status` is set by hand as the conversation moves,
   while "Bought" is derived from orders at read time and cannot be edited here.
   Nobody marks a lead as a customer — paying is what makes them one.

   The statuses read as one line: we wrote → they answered → it went quiet, or
   they are out. Contact goes through Telegram, so "we wrote" is a state that
   lasts, not a call that either connected or did not. */
/* 'burned' is the trash can: the lead vanishes from the working list into the
   Trash chip, but the row survives — permanent delete stays a separate call. */
const LEAD_STATUSES=[['new','New'],['messaged','Messaged'],['replied','Replied'],
  ['no_reply','No reply'],['rejected','Rejected'],['burned','Burned']];
const leadLabel=s=>(LEAD_STATUSES.find(([k])=>k===s)||[,s])[1];
/* The questionnaire answers are what the grade is made of, and they are the first
   thing you want in front of you when writing. Hover rather than a column: four
   free-text answers would push the rest of the row off the screen. */
const leadAnswers=a=>Object.entries(a||{}).map(([q,v])=>`${q} → ${v}`).join('\n');

/* ---------- click-to-message ----------
   The desk writes the same first message every time, so it lives here once.
   t.me by phone number cannot prefill a draft, which is why every phone action
   ALSO drops the opener on the clipboard; t.me by handle and WhatsApp prefill
   it themselves. Edit the text below to change what the team opens with. */
const leadOpener=l=>{const first=(l.name||'').trim().split(/\s+/)[0]||'there';
  return `Hey ${first}, this is the Forex Passing desk — your application just landed with me. `
    +`Ready to walk you through the next step when you are.`};
function copyOpener(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l)return;
  const txt=leadOpener(l);
  (navigator.clipboard?navigator.clipboard.writeText(txt):Promise.reject())
    .then(()=>toast('Opener copied — paste it in the chat'))
    .catch(()=>{prompt('Copy the opener:',txt)});
}
/* Klik w telegramowy przycisk = pierwszy kontakt: status „new" sam przechodzi
   w „messaged", a niczyj lead staje się TWÓJ — ten sam gest, który otwiera
   czat, robi całą księgowość (przyciski na kanale TG robią to samo). Undo
   cofa jedno i drugie, gdy chat otworzył się przez pomyłkę; każdy inny
   status zostaje w spokoju — drugi klik niczego nie psuje. */
async function markMessaged(id){
  const row=(window._leads||[]).find(x=>x.id===id);
  const l=row||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l||l.status!=='new')return;
  const przejmuje=!l.owner&&!!meMail();
  const odswiez=()=>{
    if(VIEW==='leads')renderLeads();
    if(window._leadOpen&&window._leadOpen.id===id)openLead(id);
  };
  const ustaw=(status,owner)=>{
    if(row){row.status=status;if(owner!==undefined)row.owner=owner}
    if(window._leadOpen&&window._leadOpen.id===id){
      window._leadOpen.status=status;
      if(owner!==undefined)window._leadOpen.owner=owner;
    }
  };
  try{
    const d=await api('/api/admin/leads/'+id,{method:'POST',
      body:JSON.stringify({status:'messaged',...(przejmuje?{owner:meMail()}:{})})});
    ustaw(d.status,przejmuje?meMail():undefined);
    if(row)row.contacted_at=d.contacted_at;
    odswiez();
    /* 30 s, nie 8: klik przełącza na Telegrama — undo musi doczekać powrotu */
    undoToast(przejmuje?'Messaged — and it’s yours.':'Marked as messaged.',async()=>{
      await api('/api/admin/leads/'+id,{method:'POST',
        body:JSON.stringify({status:'new',...(przejmuje?{owner:''}:{})})});
      ustaw('new',przejmuje?'':undefined);
      odswiez();
    },30000);
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Ostatnia droga do kogoś, komu Telegram nie dochodzi: SMS z linkiem z
   powrotem na Telegram. Ta wysyłka KOSZTUJE i nie da się jej cofnąć, więc
   inaczej niż reszta ikon nie leci od razu — pytanie pokazuje dokładną treść
   (składa ją serwer, panel tylko wyświetla) i numer, na który pójdzie.

   Odmowa „już poszedł" nie jest tu błędem, tylko drugim pytaniem: admin, który
   klika ponownie, zwykle wie, że pierwszy nie doszedł. Dopiero jego świadome
   „tak" wysyła powtórkę.

   Statusu tu NIE ruszamy: robi to serwer, tą samą transakcją co wysyłkę.
   Osobny strzał z przeglądarki mógłby nie dojść i zostawiłby leada z SMS-em
   w świecie, a na liście „do napisania". */
async function sendLeadSms(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l)return;
  const pyt=(tytul,tresc,ok)=>askConfirm({title:tytul,body:tresc,ok:ok,cancel:'Not now'});
  const podglad=`Goes to <b>${esc(l.phone||'')}</b> as a paid text message:<br><br>`
    +`<span style="color:var(--txt)">${esc(l.sms_text||'')}</span>`;
  if(!await pyt('Send this text?',podglad,'Send'))return;
  const strzal=async force=>api('/api/admin/leads/'+id+'/sms'+(force?'?force=true':''),
    {method:'POST'});
  try{
    await strzal(false);
  }catch(e){
    if(!/already went out/i.test(e.message)){toast('Not sent: '+e.message,'err');return}
    if(!await pyt('Send it a second time?',
      'This lead already had one text from us. Send another only if you know the '
      +'first one did not arrive — from their side a repeat reads as pestering.',
      'Send again'))return;
    try{await strzal(true)}catch(e2){toast('Not sent: '+e2.message,'err');return}
  }
  toast('Text sent — it points them back to Telegram');
  if(VIEW==='leads')await VIEWS.leads();
  if(window._leadOpen&&window._leadOpen.id===id)openLead(id);
}
/* Dno drabiny kontaktu: adres jest jedynym polem, którego formularz nie puszcza
   pustego, więc ten kanał zawsze ma dokąd pójść. Podgląd pokazuje CAŁY tekst,
   nie zapowiedź — to jest mail podpisany marką landingu i wychodzi w czyimś
   imieniu, więc klikający ma prawo zobaczyć go, zanim to się stanie.

   Powtórka pyta drugi raz z innego powodu niż przy SMS-ie: mail nic nie
   kosztuje, ale ten sam tekst drugi raz w tej samej skrzynce czyta się jak
   automat i unieważnia jedyne zdanie, które ten mail ma do sprzedania — że
   aplikację czytał człowiek. */
async function sendLeadEmail(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l)return;
  const pyt=(tytul,tresc,ok)=>askConfirm({title:tytul,body:tresc,ok:ok,cancel:'Not now'});
  const podglad=`Goes to <b>${esc(l.email||'')}</b>, subject `
    +`<b>${esc(l.mail_subject||'')}</b>:<br><br>`
    +`<span style="color:var(--txt);white-space:pre-wrap">${esc(l.mail_text||'')}</span>`;
  if(!await pyt('Send this e-mail?',podglad,'Send'))return;
  const strzal=async force=>api('/api/admin/leads/'+id+'/email'+(force?'?force=true':''),
    {method:'POST'});
  try{
    await strzal(false);
  }catch(e){
    if(!/already went out/i.test(e.message)){toast('Not sent: '+e.message,'err');return}
    if(!await pyt('Send it a second time?',
      'This lead already had this exact e-mail from us. The same text twice reads '
      +'as an autoresponder — send again only if you know the first one never arrived.',
      'Send again'))return;
    try{await strzal(true)}catch(e2){toast('Not sent: '+e2.message,'err');return}
  }
  toast('E-mail sent — it points them back to Telegram');
  if(VIEW==='leads')await VIEWS.leads();
  if(window._leadOpen&&window._leadOpen.id===id)openLead(id);
}
/* Mail pisany z ręki: temat i treść wpisuje admin, serwer ubiera je w papier
   firmowy marki z landingu (akapit będący samym linkiem -> zielony przycisk,
   blok po „--" -> szara stopka). Szablony mieszkają na SERWERZE, nie w
   localStorage: z panelu korzysta więcej niż jedno urządzenie i szablon
   zapisany na laptopie musi istnieć na telefonie. {name} podmienia się na
   imię PRZED podglądem — admin zatwierdza dokładnie to, co wyjdzie. */
const mailFill=(t,l)=>String(t||'')
  .replaceAll('{name}',String(l.name||'').trim().split(/\s+/)[0]||'there');
async function openLeadMail(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l)return;
  try{window._mailTpls=await api('/api/admin/email-templates')}
  catch(e){window._mailTpls=[];toast('Templates: '+e.message,'err')}
  document.getElementById('lead-mail-modal')?.remove();
  const box=document.createElement('div');
  box.id='lead-mail-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>E-mail to ${esc(l.name||l.email)}</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('lead-mail-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Goes to <b>${esc(l.email||'')}</b> on the brand letterhead.
      A paragraph that is just a link becomes the green button, everything after a <b>--</b> line becomes the grey footer,
      and <b>{name}</b> becomes their first name.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px">Template</label>
        <select id="lm-tpl" class="inp" onchange="fillMailTpl()"></select></div>
      <div><label class="muted" style="font-size:12px">Subject</label>
        <input id="lm-subject" class="inp" placeholder="Subject"></div>
      <div><label class="muted" style="font-size:12px">Message</label>
        <textarea id="lm-body" class="inp" rows="12" spellcheck="false"
          placeholder="Hi {name},"></textarea></div>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="lm-name" class="inp" style="flex:1;min-width:0" placeholder="Template name">
        <button class="btn-o sm" type="button" onclick="saveMailTpl()">Save template</button>
        <button class="btn-o sm" type="button" id="lm-del" onclick="delMailTpl()"
          style="display:none">Delete</button>
      </div>
      <button class="btn-p lg" style="width:100%" id="lm-send" onclick="sendCustomLeadMail(${l.id})">Send</button>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  paintMailTpls('');
  $('lm-subject').focus();
}
function paintMailTpls(sel){
  const s=$('lm-tpl');if(!s)return;
  s.innerHTML='<option value="">— start from scratch —</option>'
    +(window._mailTpls||[]).map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');
  s.value=String(sel||'');
  /* Nie atrybut `hidden`: reguła klasy .btn-o ustawia display i wygrywa z
     arkuszem przeglądarki, więc przycisk zostawał widoczny mimo atrybutu. */
  $('lm-del').style.display=s.value?'':'none';
}
function fillMailTpl(){
  const t=(window._mailTpls||[]).find(x=>String(x.id)===$('lm-tpl').value);
  $('lm-del').style.display=t?'':'none';
  if(!t)return;
  $('lm-subject').value=t.subject;$('lm-body').value=t.body;$('lm-name').value=t.name;
}
async function saveMailTpl(){
  const name=$('lm-name').value.trim(),subject=$('lm-subject').value.trim(),
    body=$('lm-body').value.trim();
  if(!name){toast('Give the template a name.','err');$('lm-name').focus();return}
  if(!subject||!body){toast('Subject and message are both required.','err');return}
  try{
    const t=await api('/api/admin/email-templates',{method:'POST',
      body:JSON.stringify({name,subject,body})});
    window._mailTpls=await api('/api/admin/email-templates');
    paintMailTpls(t.id);
    toast('Template saved.');
  }catch(e){toast('Not saved: '+e.message,'err')}
}
async function delMailTpl(){
  const t=(window._mailTpls||[]).find(x=>String(x.id)===$('lm-tpl').value);
  if(!t)return;
  if(!await askConfirm({title:'Delete this template?',
    body:`<b>${esc(t.name)}</b> disappears for everyone using the panel. E-mails already sent stay in each lead's history.`,
    ok:'Delete',danger:true}))return;
  try{
    await api('/api/admin/email-templates/'+t.id,{method:'DELETE'});
    window._mailTpls=(window._mailTpls||[]).filter(x=>x.id!==t.id);
    paintMailTpls('');
    toast('Template deleted.');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function sendCustomLeadMail(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:null);
  if(!l)return;
  const subject=mailFill($('lm-subject').value.trim(),l);
  const body=mailFill($('lm-body').value.trim(),l);
  if(!subject||!body){toast('Subject and message are both required.','err');return}
  /* Ten sam podgląd co przy automacie: wysyłka jest nieodwracalna i wychodzi
     pod cudzą marką, więc klikający widzi CAŁY tekst po podmianie {name}. */
  const podglad=`Goes to <b>${esc(l.email||'')}</b>, subject <b>${esc(subject)}</b>:<br><br>`
    +`<span style="color:var(--txt);white-space:pre-wrap">${esc(body)}</span>`;
  if(!await askConfirm({title:'Send this e-mail?',body:podglad,ok:'Send',cancel:'Not yet'}))return;
  await busy($('lm-send'),'Sending…',async()=>{
    try{
      await api('/api/admin/leads/'+id+'/email-custom',{method:'POST',
        body:JSON.stringify({subject,body})});
      document.getElementById('lead-mail-modal')?.remove();
      toast('E-mail sent.');
      if(VIEW==='leads')await VIEWS.leads();
      if(window._leadOpen&&window._leadOpen.id===id)openLead(id);
    }catch(e){toast('Not sent: '+e.message,'err')}
  });
}
/* Same shape the landing validates against. A handle that fails it ("gubi
   please") renders as plain text: a dead t.me link looks like contact and
   is not, and the desk should see the typo, not chase it. */
const TG_HANDLE_RE=/^@?[A-Za-z][A-Za-z0-9_]{4,31}$/;
function leadTgLink(l){
  const h=String(l.telegram||'').replace(/^@/,'');
  if(!h)return'';
  /* Sam podgląd profilu, bez księgowości: „napisz i oznacz jako messaged"
     mieszka na ikonie samolotu niżej. Klik w dane kontaktowe nie może po
     cichu zmieniać statusu leada ani przypisywać właściciela. */
  return TG_HANDLE_RE.test(h)
    ?`<a href="https://t.me/${esc(h)}" target="_blank" rel="noopener"
        title="Open the Telegram profile — the paper plane below writes to them">@${esc(h)}</a>`
    :`<span class="muted" title="Not a valid Telegram handle — ask for the right one">${esc(l.telegram)}</span>`;
}
/* Three icon actions (owner's spec): paper plane = write on Telegram by the
   HANDLE the lead left (prefilled opener); phone = Telegram chat looked up by
   the PHONE NUMBER (t.me cannot prefill there, so the click also drops the
   opener on the clipboard); copy = just the opener. Each icon shows only when
   its target actually exists — a dead t.me link looks like contact and is not. */
const ICO_TG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/></svg>';
const ICO_PHONE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.6a2 2 0 0 1-.5 2.1L8 9.6a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.5 2.6.6a2 2 0 0 1 1.7 2z"/></svg>';
const ICO_COPY='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICO_SMS='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 9.5 9.5 0 0 1-2.8-.4L3 21l1.4-4.2A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/></svg>';
const ICO_MAIL='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2.5" y="4.5" width="19" height="15" rx="2"/><path d="m3 6 9 6.5L21 6"/></svg>';
const ICO_PEN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';
function leadPhoneActs(l){
  const h=String(l.telegram||'').replace(/^@/,'');
  const handleOk=TG_HANDLE_RE.test(h);
  const digits=String(l.phone||'').replace(/\D/g,'');
  const intl=String(l.phone||'').trim().startsWith('+')&&digits.length>=8;
  if(!handleOk&&!intl&&!l.sms_ready&&!l.mail_ready)return'';
  const opener=encodeURIComponent(leadOpener(l));
  return `<span class="lead-acts">${handleOk
    ?`<a class="act-btn" title="Write on Telegram to @${esc(h)} — opener prefilled"
        aria-label="Telegram by handle" href="https://t.me/${esc(h)}?text=${opener}"
        target="_blank" rel="noopener" onclick="markMessaged(${l.id})">${ICO_TG}</a>`:''}${intl
    ?`<a class="act-btn" title="Telegram chat found by the phone number (opener goes to the clipboard)"
        aria-label="Telegram by phone" href="https://t.me/+${digits}"
        target="_blank" rel="noopener" onclick="copyOpener(${l.id});markMessaged(${l.id})">${ICO_PHONE}</a>`:''}${l.sms_ready
    ?`<button class="act-btn" type="button" aria-label="Send a text message"
        title="Text them the Telegram link — for people the Telegram side never reaches"
        onclick="sendLeadSms(${l.id})">${ICO_SMS}</button>`:''}${l.mail_ready
    ?`<button class="act-btn" type="button" aria-label="Send the e-mail"
        title="E-mail them the Telegram link — the only channel that always has somewhere to go"
        onclick="sendLeadEmail(${l.id})">${ICO_MAIL}</button>`:''}${l.mail_ready
    ?`<button class="act-btn" type="button" aria-label="Write an e-mail"
        title="Write your own e-mail — subject and text are yours, templates included"
        onclick="openLeadMail(${l.id})">${ICO_PEN}</button>`:''}
    <button class="act-btn" type="button" title="Copy the opener"
      aria-label="Copy the opener" onclick="copyOpener(${l.id})">${ICO_COPY}</button></span>`;
}

/* Ten sam prefiks, po którym backend wybiera grupę na Telegramie
   (`telegram.lead_chat_id`) — free-* to inny koszt i inni ludzie do obsługi,
   więc jedno źródło prawdy decyduje i o czacie, i o zakładce. */
const isFreeLead=l=>String(l.source||'').toLowerCase().startsWith('free');
/* Konto rozdaje się z listy dopiero w zakładce Free — kliknięcie zakłada
   PRAWDZIWE konto MT5 i wysyła maila, więc nie ma prawa wisieć obok każdego
   płatnego leada. Gdy konto już jest, przycisk ustępuje miejsca stanowi:
   drugi grant i tak dostałby 409, ale klikać nie ma po co. */
function leadFreeBtn(l){
  return l.accounts>0
    ?'<span class="muted" style="font-size:11px" title="A challenge account has already been opened for this e-mail">🎁 account opened</span>'
    :`<button class="btn-o sm" onclick="grantFreeAccount(${l.id})"
        title="Open the free $25K challenge for them — real account, credentials by e-mail">🎁 Free account</button>`;
}

function renderLeads(){
  const list=window._leads||[];
  const q=(window._leadQ||'').toLowerCase();
  const f=window._leadFilter||'all';
  /* Burned leads live ONLY behind the Trash chip — every other filter works on
     the living list, so a burned lead really is out of the way. */
  const kosz=list.filter(l=>l.status==='burned');
  const zywe=list.filter(l=>l.status!=='burned');
  const baza=f==='burned'?kosz:zywe;
  const rows=baza.filter(l=>
    (f==='all'||f==='burned'||(f==='bought'?(l.paid_usd>0||l.bought)
      :f==='due'?(l.next_due&&dueDays(l.next_due)<=0)
      :f==='mine'?l.owner===meMail()
      :f==='free'?isFreeLead(l)
      :f==='unowned'?!l.owner
      :l.status===f))&&
    (!q||(l.email||'').toLowerCase().includes(q)||(l.name||'').toLowerCase().includes(q)
     ||(l.phone||'').includes(q)||(l.ref||'').toLowerCase().includes(q)
     ||('@'+String(l.telegram||'').replace(/^@/,'')).toLowerCase().includes(q)
     ||(l.note||'').toLowerCase().includes(q)));
  /* Kolejność pracy, nie kolejność wpłynięcia: najpierw zaległe follow-upy
     (najstarszy dług na górze), potem nietknięci nowi, reszta od najnowszych.
     Na telefonie nagłówki tabeli są schowane, więc to jedyny porządek, jaki
     tam istnieje; na desktopie klik w nagłówek dalej sortuje po swojemu. */
  const rank=l=>l.next_due&&dueDays(l.next_due)<=0?0:l.status==='new'?1:2;
  rows.sort((a,b)=>rank(a)-rank(b)
    ||(rank(a)===0?dutc(a.next_due)-dutc(b.next_due)
      :String(b.created_at||'').localeCompare(String(a.created_at||''))));
  /* Same rule as Orders: the numbers describe WHAT IS VISIBLE below them, so
     switching to "Rejected" cannot leave the full revenue sitting on top. */
  /* „Bought" = zapłacone zamówienie ALBO ręczny przełącznik (deal poza sklepem);
     przychód sumuje tylko realne zamówienia — ręczne oznaczenie nie niesie kwoty. */
  const bought=rows.filter(l=>l.paid_usd>0||l.bought);
  const revenue=bought.reduce((s,l)=>s+(l.paid_usd||0),0);
  const waiting=rows.filter(l=>l.status==='new').length;
  /* Counted over the WHOLE list, not the filtered rows: a follow-up that came
     due is the one thing that must not hide behind the filter you left on. */
  const due=zywe.filter(l=>l.next_due&&dueDays(l.next_due)<=0).length;
  /* Statusy i „Unclaimed" filtruje się rzadziej niż Due/Mine/Bought/Free —
     mieszkają w dolnym arkuszu pod jednym chipem, zamiast rozpychać toolbar do
     12 chipów. Kosz ma własny chip, więc w arkuszu statusów go nie ma. */
  const sheetActive=[...LEAD_STATUSES.filter(([k])=>k!=='burned'),['unowned','Unclaimed']]
    .find(([k])=>k===f);
  $('view').innerHTML=`
    ${due&&f!=='due'?`<button class="due-banner" onclick="window._leadFilter='due';renderLeads()">⏰ ${due} follow-up${due>1?'s':''} due — someone is waiting to hear back</button>`:''}
    <div class="toolbar lead-toolbar">
      ${searchBox('lead-q','_leadQ','renderLeads','Search name, email, phone or partner…')}
      <div class="seg">${[['all','All'],['due','Due'],['mine','Mine'],['free','Free'],['bought','Bought']]
        .map(([k,l])=>`<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''} onclick="window._leadFilter='${f===k?'all':k}';renderLeads()">${l}</button>`).join('')}
        <button class="${sheetActive?'on':''}" onclick="${sheetActive?`window._leadFilter='all';renderLeads()`:'openLeadStatusSheet()'}">${sheetActive?sheetActive[1]:'Status ▾'}</button>
        ${kosz.length?`<button class="${f==='burned'?'on':''}" title="Burned leads"
          onclick="window._leadFilter='${f==='burned'?'all':'burned'}';renderLeads()">🗑 ${kosz.length}</button>`:''}</div>
      <button class="btn-p sm" onclick="openNewLead()"
        title="Somebody who wrote to us without filling the form">+ Add lead</button>
    </div>
    ${rows.length?`<p class="lead-statline">${rows.length}${rows.length!==baza.length?` of ${baza.length}`:''} lead${rows.length===1&&rows.length===baza.length?'':'s'}${f==='burned'?' in the trash':''}${
        waiting?` · <span class="statlink" onclick="window._leadFilter='new';renderLeads()">${waiting} untouched</span>`:''} · ${bought.length} bought · $${fmt0(revenue)}</p>
    <div class="tbl-wrap tw-wide lead-wrap rtbl-wrap"><table class="tbl sortable lead-tbl rtbl" data-tkey="admin.leads.v3">
      <thead><tr><th>Date</th><th>Lead</th><th>Contact</th><th>Source</th>
        <th>Status</th><th>Bought</th><th>Note</th></tr></thead>
      <tbody>${rows.map(l=>`<tr class="clickable" onclick="if(event.target.closest('a,button,input,textarea,select'))return;openLead(${l.id})">
        <td class="muted rt-hide" data-l="Date" data-sort="${esc(l.created_at||'')}">${dstr(l.created_at)}</td>
        <td data-l="Lead" class="rt-main"><b>${esc(l.name||'—')}</b>${
          l.tier?`<span class="g-dot" title="${esc(l.tier)} ${l.score}&#10;${esc(leadAnswers(l.answers))}">${l.tier==='high'?'🔥':l.tier==='warm'?'🟡':'⚪️'}</span>`:''}
          <span class="lead-when">${dstr(l.created_at)}</span>
          ${l.owner?`<div style="font-size:11px" title="Taken by">👤 ${esc(l.owner)}</div>`:''}
          ${l.applications>1?`<div class="muted" style="font-size:11px" title="Filled the form more than once">↻ applied ${l.applications}×</div>`:''}
          ${l.outcome==='not_qualified'?`<div class="muted" style="font-size:11px">${
            l.source==='safe'?'safe page lead — warm up':'failed the questionnaire'}</div>`:''}</td>
        <td data-l="Contact">${l.telegram?`<div>${leadTgLink(l)}</div>`:''}
          ${l.phone?`<div class="muted lead-ph"><a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>${
            l.phone_iso?` ${esc(l.phone_iso)}`:''}</div>`:''}
          ${l.phone||l.telegram||l.mail_ready?`<div class="lead-act-row">${leadPhoneActs(l)}</div>`:''}</td>
        <td class="muted" data-l="Source">${esc(l.source||'—')}${
          isFreeLead(l)?' <span title="Free challenge funnel — no money changes hands">🆓</span>':''}${
          l.ref?`<div style="font-size:11px">via ${esc(l.ref)}</div>`:''}${
          f==='free'?`<div class="lead-act-row">${leadFreeBtn(l)}</div>`:''}</td>
        <td data-l="Status"><button type="button" class="status status-tap ${LEAD_STATUS_CLS[l.status]||'pending'}"
            aria-label="Change status" title="Change status"
            onclick="openLeadStatusFor(${l.id})"><span class="dot"></span>${esc(leadLabel(l.status))}</button>
          ${l.next_due?`<div class="due ${dueDays(l.next_due)<=0?'now':''}">⏰ ${dueLabel(l.next_due)}</div>`
            :l.owner&&(l.status==='messaged'||l.status==='replied')?'<div class="due now">no next step</div>'
            :l.contacted_at?`<div class="muted" style="font-size:11px">${dstr(l.contacted_at)}</div>`:''}</td>
        <td class="num" data-l="Bought" data-sort="${l.paid_usd>0?l.paid_usd:l.bought?0.5:0}">${
          l.paid_usd>0?`<span class="status paid"><span class="dot"></span>$${fmt0(l.paid_usd)}</span>`
          :l.bought?'<span class="status paid"><span class="dot"></span>bought</span>'
          :'<span class="muted no-buy">—</span>'}</td>
        <td data-l="Note" class="muted lead-note" title="${esc(l.note||'')}">${esc((l.note||'').split('\n')[0])}</td></tr>`).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>${list.length?'No leads match':'No leads yet'}</h3>
        <p>${list.length?'Try a different search or filter.':'Applications from the landing page land here.'}</p></div>`}`;
}

/* Filtr po statusie w dolnym arkuszu (ten sam #act-sheet co long-press):
   pięć statusów + „Unclaimed" rozpychało toolbar do jedenastu chipów, a
   filtruje się nimi rzadziej niż Due/Mine/Bought/Free. Wybrany wraca do
   toolbara jako chip z ✕ w miejscu „Status ▾". */
function openLeadStatusSheet(){
  if(document.getElementById('act-sheet'))return;
  const veil=document.createElement('div');veil.id='act-veil';veil.className='sheet-veil';
  veil.onclick=closeActSheet;
  const s=document.createElement('div');s.id='act-sheet';s.className='sheet';
  s.innerHTML=`<div class="sheet-grab"></div><div class="act-sheet-title">Filter by status</div>
    <div class="act-sheet-list">${[...LEAD_STATUSES.filter(([k])=>k!=='burned'),['unowned','Unclaimed — nobody took it']]
      .map(([k,lab])=>`<button class="btn-o" onclick="window._leadFilter='${k}';renderLeads();closeActSheet()">${lab}</button>`).join('')}</div>`;
  document.body.append(veil,s);
  requestAnimationFrame(()=>s.classList.add('open'));
}

/* Tap w pigułkę statusu na wierszu: arkusz zmiany statusu TEGO leada.
   Zmiana z listy była możliwa tylko przez szufladę albo long-press, którego
   nie widać — pigułka jest na wierzchu i sama zapowiada, co się stanie. */
function openLeadStatusFor(id){
  if(document.getElementById('act-sheet'))return;
  const l=(window._leads||[]).find(x=>x.id===id);
  if(!l)return;
  const veil=document.createElement('div');veil.id='act-veil';veil.className='sheet-veil';
  veil.onclick=closeActSheet;
  const s=document.createElement('div');s.id='act-sheet';s.className='sheet';
  s.innerHTML=`<div class="sheet-grab"></div>
    <div class="act-sheet-title">${esc(l.name||l.email||'Lead')} — status</div>
    <div class="act-sheet-list">${LEAD_STATUSES.map(([k,lab])=>
      `<button class="${l.status===k?'btn-p':k==='burned'?'btn-danger':'btn-o'}"
        onclick="setLeadStatus(${id},'${k}');closeActSheet()">${k==='burned'?'🔥 Burned — to trash':lab}${l.status===k?' ✓':''}</button>`).join('')}</div>`;
  document.body.append(veil,s);
  requestAnimationFrame(()=>s.classList.add('open'));
}
/* Jak patchLead, ale bez otwierania szuflady — akcja z listy ma zostawić
   admina na liście. Szuflada odświeża się tylko, gdy akurat wisi na tym leadzie. */
async function setLeadStatus(id,status){
  const row=(window._leads||[]).find(x=>x.id===id);
  if(row&&row.status===status)return;
  try{
    await api('/api/admin/leads/'+id,{method:'POST',body:JSON.stringify({status})});
    if(row){row.status=status;
      /* Backend gasi przypomnienia przy spaleniu — bez tego lokalna lista
         pokazywalaby "⏰ in 7d" na leadzie lezacym w koszu az do refetchu. */
      if(status==='burned')row.next_due=null}
    if(VIEW==='leads')renderLeads();
    if(window._leadOpen&&window._leadOpen.id===id)await openLead(id);
    toast(status==='burned'?'Moved to trash':'Marked as '+leadLabel(status));
  }catch(e){toast('Error: '+e.message,'err')}
}

/* Who is sitting at the panel = the signed-in admin account. Leads are owned
   by the ADMIN EMAIL ("bartek@s"), the same identity the Telegram buttons sign
   with once the account is paired (Settings → Notifications) — one name for
   one person everywhere, no prompt() and no per-browser nickname. */
const meMail=()=>(ME&&ME.email)||'';

/* One writer for the small per-lead fields. The drawer re-renders from the
   server afterwards on purpose: owner, grade and reminders read off each other
   (taking a lead changes which buttons make sense), and patching four spots by
   hand is how they drift apart. */
async function patchLead(id,patch,msg){
  try{
    await api('/api/admin/leads/'+id,{method:'POST',body:JSON.stringify(patch)});
    toast(msg);
    await openLead(id);      // re-reads the lead and syncs the row behind it
    renderLeads();
  }catch(e){toast('Error: '+e.message,'err')}
}

function claimLead(id){
  const who=meMail();
  if(!who){toast('Session expired — sign in again','err');return}
  patchLead(id,{owner:who},'Yours — write to them');
}

/* Kasowanie leada — przez to samo 5-sekundowe okno undo co certyfikaty,
   zamiast modala z pytaniem: wiersz znika od razu, DELETE idzie na serwer
   dopiero po odliczaniu, a „Undo" po prostu niczego nie wysyła. Wiersze
   testowe i pomyłki muszą znikać naprawdę: `leads.email` jest unikalny,
   a ukryty wiersz blokowałby adres. */
function deleteLead(id){
  const lista=(window._leads||[]).slice();
  const l=lista.find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:{});
  const kto=l.name||l.email||('#'+id);
  closeOver();
  window._leads=lista.filter(x=>x.id!==id);
  if(VIEW==='leads')renderLeads();
  const przywroc=()=>{window._leads=lista;if(VIEW==='leads')renderLeads()};
  withUndo(`Deleting lead ${kto}`,async()=>{
    try{
      /* keepalive jak w xdel: DELETE idzie 5 s po kliknieciu — zamkniecie
         karty w tym oknie nie moze zgubic zadania. */
      await api('/api/admin/leads/'+id,{method:'DELETE',keepalive:true});
    }catch(e){
      toast('Error: '+e.message,'err');
      przywroc();
    }
  },null,przywroc);
}

/* ---------- one lead: the history behind the row ----------
   The table answers "where does this lead stand"; this answers "how did it get
   there". The list cannot show it: one person is one row on purpose, so a second
   application overwrites the first and only the counter survives. Every write
   also lands in lead_events, and that is what gets read back here. */
const LEAD_STATUS_CLS={new:'pending',messaged:'pending',replied:'paid',
  no_reply:'failed',rejected:'failed',burned:'failed'};
const LEAD_EVENT_LBL={applied:'Applied',status:'Status',note:'Note',reminder:'Reminder',
  claim:'Owner',tier:'Grade',bought:'Bought',sms:'Text sent',email:'E-mail sent',
  granted:'Free account'};
/* Reminders are sent to US, never to the lead — the landing they applied through
   is a separate brand. The wording says who is being nudged. */
const LEAD_REMINDER_LBL={no_contact:'Nobody wrote to them yet',bought:'Bought — stop treating as a lead',
  stalled:'Conversation led nowhere'};

function leadEventDetail(e){
  if(e.kind==='reminder')return LEAD_REMINDER_LBL[e.detail]||e.detail.replace(/^planned: /,'');
  if(e.kind==='status')return e.detail.split('→').map(s=>leadLabel(s.trim())).join(' → ');
  return e.detail;
}

/* Whole days, not hours: a reminder set for Friday is "in 2 days" all Wednesday
   long, and "3 hours overdue" is not a thing anyone acts on differently. */
const dueDays=iso=>{const a=dutc(iso),b=new Date();
  a.setHours(0,0,0,0);b.setHours(0,0,0,0);return Math.round((a-b)/86400000)};
const dueLabel=iso=>{const d=dueDays(iso);
  return d<0?`${-d}d overdue`:d===0?'due today':d===1?'tomorrow':`in ${d}d`};

/* Ready-made follow-ups. These are the messages that actually get sent after a
   first exchange — the point is that scheduling one costs a single click,
   because a reminder nobody sets is a lead nobody writes back to.
   [label, days from now, repeat every N days (0 = once), what to do] */
const REMINDER_PRESETS=[
  ['No reply',2,0,'Write again — a different time of day than last time.'],
  ['Asked to wait',1,0,'Asked us to write later — at the time you agreed on.'],
  ['Thinking about it',5,0,'Wanted to think it over. Ask what is still open.'],
  ['Too expensive',7,0,'Hesitated on price — bring up the smaller account or the current promo.'],
  ['No money yet',14,0,'Had no funds at the time. Ask whether anything changed.'],
  ['Promised to pay',1,0,'Said they would pay — check whether the order actually came through.'],
  ['Not right now',30,0,'Said not right now. Check whether they are still trading and on what.'],
  ['Bought — weekly',7,7,'Account update: how the challenge is going, whether the rules are clear, how far from the loss limit.'],
];

/* Where this lead stands, in the order the work happens: who has it, how far the
   conversation got, how good they turned out to be. All three are moderation,
   not data from the landing page — several people work the same Telegram
   channel, so these are the things that have to be fixable from here as well as
   from the channel buttons. */
function leadModCard(l){
  const grades=[['high','🔥 High'],['warm','🟡 Warm'],['cold','⚪️ Cold']];
  const ja=meMail();
  return `<div class="lead-card sec-card">
    <div class="mod-row">
      <div><div class="lbl">Handled by</div>
        <div class="mod-who">${l.owner?esc(l.owner):'<span class="muted">nobody took it yet</span>'}</div></div>
      <div class="mod-btns">${l.owner
        ?`${l.owner===ja?'':`<button class="btn-p" onclick="claimLead(${l.id})">Take over</button>`}
          <button class="btn-o" onclick="patchLead(${l.id},{owner:''},'Released — back in the pool')">Release</button>`
        :`<button class="btn-p" onclick="claimLead(${l.id})">Take it</button>`}</div>
    </div>
    <div class="mod-row">
      <div><div class="lbl">Where it stands</div>
        <div class="muted" style="font-size:11.5px">we wrote → they answered → it went quiet, or they are out</div></div>
      <div class="seg wrap">${LEAD_STATUSES.map(([k,lab])=>
        `<button class="${l.status===k?'on':''}" onclick="patchLead(${l.id},{status:'${k}'},'Marked as ${lab}')">${lab}</button>`).join('')}</div>
    </div>
    <div class="mod-row">
      <div><div class="lbl">Grade</div>
        <div class="muted" style="font-size:11.5px">scored from the form — correct it once you have talked</div></div>
      <div class="seg wrap">${grades.map(([k,lab])=>
        `<button class="${l.tier===k?'on':''}" onclick="patchLead(${l.id},{tier:'${k}'},'Grade set to ${k}')">${lab}</button>`).join('')}</div>
    </div>
    ${l.paid_usd>0?'':`<div class="mod-row">
      <div><div class="lbl">Bought</div>
        <div class="muted" style="font-size:11.5px">deal closed outside the store — a paid order counts itself</div></div>
      <div class="mod-btns"><button class="btn-o" onclick="patchLead(${l.id},{bought:${!l.bought}},'${
        l.bought?'Unmarked':'Marked as bought'}')">${l.bought?'Bought ✓ — unmark':'Mark as bought'}</button></div>
    </div>`}
  </div>`;
}

/* Closing the sale, from the same screen as the conversation. The Orders tab
   cannot do this one: it picks a customer from the list of accounts, and a lead
   has none until somebody pays. */
function leadSellCard(l){
  return `<div class="lead-card sec-card">
    <div class="mod-row">
      <div><div class="lbl">Sell a challenge</div>
        <div class="muted" style="font-size:11.5px">Opens an unpaid order and copies a card payment link to send them.</div></div>
      <button class="btn-p" onclick="sellToLead()">Payment link</button>
    </div>
  </div>`;
}
function sellToLead(){
  const l=window._leadOpen;
  if(!l)return;
  openManualOrder(null,{id:l.id,email:l.email,name:l.name});
}

/* Druga strona tej samej szuflady: leadowi z free nic się nie sprzedaje, tylko
   wydaje obiecane konto. Warunku nie sprawdza panel — obietnica z landingu bywa
   rozliczana ręcznie w rozmowie, więc decyzję podejmuje człowiek, a ekran tylko
   pilnuje, żeby wiedział, co klika. */
function leadFreeCard(l){
  if(!isFreeLead(l))return'';
  const jest=l.accounts>0;
  return `<div class="lead-card sec-card">
    <div class="mod-row">
      <div><div class="lbl">Free challenge</div>
        <div class="muted" style="font-size:11.5px">${jest
          ?'They already have a challenge account on this e-mail — nothing left to hand out here.'
          :'Opens a real 2-step $25K account and e-mails the login with a link to set the password.'}</div></div>
      ${jest?'<span class="status paid"><span class="dot"></span>account opened</span>'
        :`<button class="btn-p" onclick="grantFreeAccount(${l.id})">🎁 Free account</button>`}
    </div>
  </div>`;
}
/* Nie ma cofnięcia: za oknem potwierdzenia jedzie konto MT5 z puli i mail do
   klienta. Dlatego pytanie mówi wprost, na jaki adres to leci. */
async function grantFreeAccount(id){
  const l=(window._leads||[]).find(x=>x.id===id)
    ||(window._leadOpen&&window._leadOpen.id===id?window._leadOpen:{});
  if(!await askConfirm({title:'Open the free $25K account?',
    body:`<b>${esc(l.email||'')}</b> gets a real 2-step $25K challenge account and an `
      +`e-mail with the login and a “set your password” link. This is the same thing `
      +`a paid order does — there is no undo from here.`,ok:'Open the account'}))return;
  try{
    const r=await api('/api/admin/leads/'+id+'/free-account',{method:'POST'});
    toast(r.login?`Account ${r.login} is live — credentials e-mailed.`:'Account opened.');
    if(VIEW==='leads')await VIEWS.leads();
    openLead(id);
  }catch(e){toast('Not opened: '+e.message,'err')}
}

/* Konto założone ZA klienta nie ma hasła znanego komukolwiek — pierwszy link
   „ustaw hasło" jedzie w mailu z poświadczeniami, ale żyje 7 dni. Ta karta to
   druga szansa: świeży link bez przepychania klienta przez „forgot password".
   „Copy link" jest tu nie dla wygody, tylko na wypadek, gdy MAIL jest
   problemem (spam, literówka w adresie, cicha awaria SMTP) — wtedy link
   idzie klientowi na Telegramie z ręki. Karta znika, gdy klient hasło
   ustawi — wtedy panel nie ma tu nic do roboty. */
function inviteRow(tid,ctx){
  return `<div class="mod-row">
      <div><div class="lbl">Portal access</div>
        <div class="muted" style="font-size:11.5px">We opened this account for them and no password has been set yet. Send a fresh “set your password” link (valid 7 days) — or copy it and deliver it yourself when e-mail is the problem.</div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
        <button class="btn-o" onclick="copyPortalInvite(${tid})"
          title="Generates the link without sending anything — paste it to the client on Telegram or SMS">Copy link</button>
        <button class="btn-p" onclick="sendPortalInvite(${tid},'${ctx}')">Send invite</button>
      </div>
    </div>`;
}
/* Przy stanie innym niz „awaiting" zaproszenia nie wygenerujemy — serwer nie da
   wejsciowki na konto z zywym haslem (`portal-invite` odmawia) i slusznie. Ale pusta
   szuflada kazala dzialowi zgadywac, czemu przycisku nie ma, wiec kazdy stan
   mowi tu o sobie. Dla „password" zostaje droga bezpieczna: reset idzie
   WYLACZNIE na adres klienta, panel linku nie oglada. */
function leadInviteCard(l){
  if(!l.trader_id)return'';
  const st=l.portal_state||(l.must_set_password?'awaiting':'password');
  if(st==='awaiting')return`<div class="lead-card sec-card">${inviteRow(l.trader_id,'lead')}</div>`;
  const opis=st==='google'
    ?'Signs in with Google — no portal password needed.'
    :'Portal password already set. If they cannot get in, send them a reset link.';
  return `<div class="lead-card sec-card"><div class="mod-row">
      <div><div class="lbl">Portal access</div>
        <div class="muted" style="font-size:11.5px">${opis}</div></div>
      ${st==='google'?'':`<div style="display:flex;justify-content:flex-end">
        <button class="btn-o" onclick="sendLeadReset()">Send reset e-mail</button></div>`}
    </div></div>`;
}
async function sendLeadReset(){
  const l=window._leadOpen;if(!l)return;
  if(!await askConfirm({title:'Send a password reset?',
    body:`Goes to <b>${esc(l.email)}</b> — a link to choose a new portal password, `
      +`valid for 1 hour. The link never passes through this panel.`,
    ok:'Send',cancel:'Not now'}))return;
  try{await api('/api/auth/forgot',{method:'POST',body:JSON.stringify({email:l.email})})}
  catch(e){toast('Not sent: '+e.message,'err');return}
  toast('Reset e-mail sent — the link works for 1 hour');
}
async function sendPortalInvite(tid,ctx){
  const email=ctx==='lead'&&window._leadOpen?window._leadOpen.email
    :(window._oAcc&&window._oAcc.trader_email)||'';
  if(!await askConfirm({title:'Send the portal invite?',
    body:`Goes to <b>${esc(email)}</b> — a link to set their portal password, `
      +`valid for 7 days. Earlier links keep working until they expire or the `
      +`password is set.`,
    ok:'Send',cancel:'Not now'}))return;
  try{await api('/api/admin/traders/'+tid+'/portal-invite',{method:'POST'})}
  catch(e){toast('Not sent: '+e.message,'err');return}
  toast('Invite sent — the link works for 7 days');
  if(ctx==='lead'&&window._leadOpen)openLead(window._leadOpen.id);
}
async function copyPortalInvite(tid){
  let d;
  try{d=await api('/api/admin/traders/'+tid+'/portal-invite?send=false',{method:'POST'})}
  catch(e){toast('Error: '+e.message,'err');return}
  try{await navigator.clipboard.writeText(d.setup_url);
    toast('Link copied — valid 7 days, works once')}
  catch(e){
    /* iOS poza gestem albo http: schowek odmawia — link ma być widoczny,
       skoro już powstał i został odnotowany w historii leada. */
    await askConfirm({title:'Copy this link',body:`<span class="mono" style="word-break:break-all;font-size:12px">${esc(d.setup_url)}</span>`,ok:'Done'});
  }
}

/* Osobna karta na dole, nie przycisk obok „Release": to jedyna operacja w tej
   zakładce, której nie da się cofnąć, i ma być trudniej ją kliknąć przez pomyłkę
   niż zmienić status. */
function leadDangerCard(l){
  return `<div class="lead-card sec-card danger-zone">
    <div class="mod-row">
      <div><div class="lbl">Delete</div>
        <div class="muted" style="font-size:11.5px">For test entries and duplicates. History, follow-ups and the channel card go with it — you get 5 seconds to undo.</div></div>
      <button class="btn-danger" onclick="deleteLead(${l.id})">Delete lead</button>
    </div>
  </div>`;
}

/* Scheduling a call back. Everything here goes to OUR Telegram desk on the day
   it is due — the lead never hears from this panel, because the landing they
   applied through is a separate brand. */
function leadReminderCard(l){
  const rem=l.reminders||[];
  const open=rem.filter(r=>r.active),closed=rem.filter(r=>!r.active);
  return `<div class="lead-card sec-card">
    <h4>Follow-up</h4>
    ${open.length?`<div class="rem-list">${open.map(r=>`
      <div class="rem ${dueDays(r.due_at)<=0?'now':''}">
        <div class="rem-txt"><div>${esc(r.text)}</div>
          <div class="muted">${dueLabel(r.due_at)}${r.repeat_days?` · repeats every ${r.repeat_days}d`:''}${
            r.sent_count?` · sent ${r.sent_count}×`:''}${r.created_by==='cron'?' · automatic':''}</div></div>
        ${XBTN(`cancelLeadReminder(${l.id},${r.id})`,'Done — dismiss this follow-up')}
      </div>`).join('')}</div>`:'<p class="muted" style="font-size:12.5px">Nothing scheduled.</p>'}
    <div class="chip-row rem-presets">${REMINDER_PRESETS.map(([lab,days,,text],i)=>
      `<button class="chip preset" title="${esc(text)} — in ${days}d"
        onclick="schedulePreset(${l.id},${i})">${lab} · ${days}d</button>`).join('')}</div>
    <input id="rem-text" class="inp sm" placeholder="Or in your own words…">
    <div class="rem-when">
      <label>in <input id="rem-days" class="inp sm" type="number" min="0" max="365" value="3"> days</label>
      <label title="Keeps nudging on the same cycle until you cancel it">
        <input id="rem-rep" type="checkbox"> and keep repeating</label>
      <button class="btn-p" onclick="addLeadReminder(${l.id})">Schedule</button>
    </div>
    ${closed.length?`<details style="margin-top:10px"><summary class="muted" style="font-size:12px;cursor:pointer">${closed.length} closed</summary>
      ${closed.map(r=>`<div class="rem off"><div class="rem-txt"><div>${esc(r.text)}</div>
        <div class="muted">${r.sent_count?`sent ${r.sent_count}×`:'never sent'}${r.last_sent_at?` · last ${dstr(r.last_sent_at)}`:''}</div>
      </div></div>`).join('')}</details>`:''}
  </div>`;
}

/* Chip presetu od razu planuje: jeden dotyk zamiast „wypełnij → przewiń →
   Schedule". POST zwraca id, więc Undo kasuje przez istniejący endpoint
   cancel. Własny tekst i nietypowy termin dalej idą przez formularz niżej. */
async function schedulePreset(id,i){
  const [lab,days,rep,text]=REMINDER_PRESETS[i];
  try{
    const r=await api(`/api/admin/leads/${id}/reminders`,
      {method:'POST',body:JSON.stringify({text,due_in_days:days,repeat_days:rep>0?rep:null})});
    await openLead(id);renderLeads();
    undoToast(`${lab} — in ${days}d${rep>0?`, repeats every ${rep}d`:''}.`,async()=>{
      await api(`/api/admin/leads/${id}/reminders/${r.id}/cancel`,{method:'POST'});
      await openLead(id);renderLeads();
    });
  }catch(e){toast('Error: '+e.message,'err')}
}

async function addLeadReminder(id){
  const text=($('rem-text').value||'').trim();
  const days=parseInt($('rem-days').value,10);
  if(!text){toast('Write what needs doing','err');return}
  if(!(days>=0)){toast('Set how many days from now','err');return}
  /* One number does both jobs: "in 7 days" that repeats means every 7 days.
     A second field for the cycle length was one more thing to get wrong. */
  const repeat=$('rem-rep').checked?days||1:null;
  try{
    await api(`/api/admin/leads/${id}/reminders`,
      {method:'POST',body:JSON.stringify({text,due_in_days:days,repeat_days:repeat})});
    toast(repeat?`Scheduled — repeats every ${repeat}d`:'Reminder scheduled');
    await openLead(id);renderLeads();
  }catch(e){toast('Error: '+e.message,'err')}
}

async function cancelLeadReminder(id,rid){
  try{
    await api(`/api/admin/leads/${id}/reminders/${rid}/cancel`,{method:'POST'});
    await openLead(id);renderLeads();
    /* Ten sam wzorzec co przy planowaniu presetem: cancel nie kasuje wiersza,
       wiec Undo po prostu zapala go z powrotem — z licznikiem i terminem. */
    undoToast('Reminder dismissed.',async()=>{
      await api(`/api/admin/leads/${id}/reminders/${rid}/reactivate`,{method:'POST'});
      await openLead(id);renderLeads();
    });
  }catch(e){toast('Error: '+e.message,'err')}
}

/* Leada wpisanego z ręki nie da się dziś dodać nigdzie indziej: kto napisał na
   Telegramie prosto z reklamy, nie ma ani konta, ani wiersza w tabeli leadów. */
function openNewLead(){
  document.getElementById('lead-modal')?.remove();
  const box=document.createElement('div');
  box.id='lead-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Add lead</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('lead-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">For somebody who reached us without the form — a reply to an ad, a message on Telegram.
      They land in the same list as everyone else, with history, reminders and the card on the channel.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px">E-mail</label>
        <input id="nl-email" class="inp" type="email" inputmode="email" autocapitalize="off"
          spellcheck="false" placeholder="name@example.com"></div>
      <div><label class="muted" style="font-size:12px">Full name</label>
        <input id="nl-name" class="inp" placeholder="Optional"></div>
      <div><label class="muted" style="font-size:12px">Phone</label>
        <input id="nl-phone" class="inp" type="tel" inputmode="tel" placeholder="+48 601 234 567"></div>
      <div><label class="muted" style="font-size:12px">Telegram</label>
        <input id="nl-telegram" class="inp" autocapitalize="off" spellcheck="false" placeholder="@handle"></div>
      <div><label class="muted" style="font-size:12px">Country</label>
        <input id="nl-country" class="inp" placeholder="Optional"></div>
      <div><label class="muted" style="font-size:12px">First note</label>
        <textarea id="nl-note" class="inp" rows="3" placeholder="What they wrote, what they want"></textarea></div>
      <button class="btn-p lg" style="width:100%" id="nl-go" onclick="submitNewLead()">Add lead</button>
      <p class="hint">Marked as <b>manual</b>, so the landing page conversion keeps counting only the people it actually brought in.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  $('nl-email').focus();
}

async function submitNewLead(){
  const email=$('nl-email').value.trim();
  if(!email){toast('E-mail is required.','err');$('nl-email').focus();return}
  const btn=$('nl-go');btn.disabled=true;
  try{
    const r=await api('/api/admin/leads',{method:'POST',body:JSON.stringify({
      email,name:$('nl-name').value.trim(),phone:$('nl-phone').value.trim(),
      telegram:$('nl-telegram').value.trim(),country:$('nl-country').value.trim(),
      note:$('nl-note').value.trim()})});
    document.getElementById('lead-modal')?.remove();
    /* Ten mail już jest — otwieramy tamtą kartę zamiast udawać, że dodaliśmy
       nowego. Kto go prowadzi, jest tu najważniejsze: bez tego dwie osoby piszą
       do tego samego człowieka. */
    if(r.existing)toast(r.owner?`Already in the list — ${r.owner} is on it.`
      :'Already in the list, nobody took it yet.','warn',7000);
    else toast('Lead added.');
    await VIEWS.leads();
    openLead(r.id);
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false}
}

async function openLead(id){
  let l;
  try{l=await api('/api/admin/leads/'+id)}
  catch(e){toast('Error: '+e.message,'err');return}
  /* The detail call is the freshest thing we have, so the row behind the drawer
     is patched from it. Otherwise scheduling a reminder here leaves the table
     still saying the lead has nothing due. */
  const row=(window._leads||[]).find(x=>x.id===id);
  if(row)Object.assign(row,{owner:l.owner,tier:l.tier,status:l.status,note:l.note,
    next_due:l.next_due,contacted_at:l.contacted_at});
  /* copyOpener sięga tu, gdy wiersza nie ma w liście (deep-link z pusha,
     zanim tabela się dociągnie). Przyciski w szufladzie czytają leada stąd
     z drugiego powodu: mail i nazwisko podane przez inline onclick trzeba by
     wcisnąć w atrybut HTML, a nazwiska bywają z apostrofem. */
  window._leadOpen=l;
  const ev=l.events||[],ords=l.orders||[];
  /* Wejście z bannera „follow-ups due" ma od razu pokazywać CO jest do
     zrobienia — bez przewijania do karty Follow-up w połowie szuflady. */
  const zalegle=(l.reminders||[]).filter(r=>r.active&&dueDays(r.due_at)<=0);
  openOver(l.name||l.email,`
    ${zalegle.length?`<div class="due-banner" style="cursor:default">⏰ ${dueLabel(zalegle[0].due_at)} — ${esc(zalegle[0].text)}</div>`:''}
    <div class="chip-row">
      <span class="status ${LEAD_STATUS_CLS[l.status]||'pending'}"><span class="dot"></span>${esc(leadLabel(l.status))}</span>
      ${l.tier?`<span class="chip">${esc(l.tier)} ${l.score}</span>`:''}
      ${l.paid_usd>0?`<span class="status paid"><span class="dot"></span>paid $${fmt0(l.paid_usd)}</span>`
        :l.bought?'<span class="status paid"><span class="dot"></span>bought</span>':''}
      ${l.applications>1?`<span class="chip">applied ${l.applications}×</span>`:''}
      ${l.outcome==='not_qualified'?`<span class="chip">${l.source==='safe'?'safe page lead':'failed the questionnaire'}</span>`:''}
    </div>
    <div class="chip-row">
      ${l.telegram?`<span class="chip">${leadTgLink(l)}</span>`:''}
      <span class="chip"><a href="mailto:${esc(l.email)}">${esc(l.email)}</a></span>
      ${l.phone?`<span class="chip"><a href="tel:${esc(l.phone)}">${esc(l.phone)}</a></span>`:''}
      ${l.country?`<span class="chip">${esc(l.country)}</span>`:''}
      ${l.source?`<span class="chip">${esc(l.source)}${l.ref?' via '+esc(l.ref):''}</span>`:''}
    </div>
    ${l.phone||l.telegram||l.mail_ready?`<div class="chip-row" style="margin-top:2px">${leadPhoneActs(l)}</div>`:''}
    ${Object.keys(l.answers||{}).length?`<div class="lead-card sec-card"><h4>Answers</h4>
      ${Object.entries(l.answers).map(([q,v])=>`<div class="note-line"><span class="muted">${esc(q)}</span><br><b>${esc(v)}</b></div>`).join('')}
      <p class="muted" style="font-size:11.5px;margin-top:8px">From the latest application — earlier ones sit in the history below.</p></div>`:''}
    ${leadModCard(l)}
    ${leadReminderCard(l)}
    ${leadFreeCard(l)}
    ${leadSellCard(l)}
    ${leadInviteCard(l)}
    <div class="lead-card sec-card"><h4>Notes</h4>
      <textarea class="inp" rows="${Math.min(6,(l.note||'').split('\n').length+1)}"
        placeholder="What they wrote, what they want — one line per note"
        onchange="patchLead(${l.id},{note:this.value.trim()},'Note saved')">${esc(l.note||'')}</textarea>
      <p class="muted" style="font-size:11.5px;margin-top:8px">Reply to the lead's message on Telegram and it lands here too.</p></div>
    ${ords.length?`<h4 style="margin:16px 0 6px">Orders</h4>
      <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Date</th><th>Product</th><th>Amount</th><th>Status</th>
        <th title="Buy 1 Get 1 Free — paying the order also creates a free second account of the same size">BOGO</th></tr></thead>
      <tbody>${ords.map(o=>`<tr><td class="muted" style="white-space:nowrap">${dstr(o.created_at)}</td>
        <td>${esc(o.product_key)}</td><td class="num">$${fmt0(o.amount_usd)}</td>
        <td><span class="status ${o.status==='paid'?'paid':o.status==='failed'?'failed':'pending'}"><span class="dot"></span>${esc(o.status)}</span></td>
        <td>${o.status==='paid'
          ?(o.bogo?'<span class="up" style="font-size:11px">2 accounts</span>':'<span class="muted">—</span>')
          :`<button class="btn-o sm" onclick="toggleOrderBogo(${o.id},${o.bogo?'false':'true'},${l.id})"
             title="${o.bogo?'Remove the free second account from this order':'Add a free second account of the same size when this order is paid'}">${o.bogo?'On ✓':'Off'}</button>`}</td></tr>`).join('')}
      </tbody></table></div>`
      :'<p class="muted" style="font-size:12.5px;margin-top:14px">No orders on this e-mail address.</p>'}
    ${ev.length?`<details class="lead-his"><summary>History (${ev.length})</summary>
      <div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">
      <thead><tr><th style="width:104px">When</th><th style="width:96px">What</th><th>Details</th></tr></thead>
      <tbody>${ev.map(e=>`<tr>
        <td class="muted" style="white-space:nowrap">${dstr(e.created_at)}</td>
        <td>${esc(LEAD_EVENT_LBL[e.kind]||e.kind)}
          <div class="muted" style="font-size:11px">${esc(e.actor||'—')}</div></td>
        <td><div style="font-size:12px">${esc(leadEventDetail(e))}</div>
          ${e.body?`<div class="lead-sent">${esc(e.body)}</div>`:''}
          ${Object.keys(e.answers||{}).length?`<div class="muted" style="font-size:11.5px;margin-top:4px">${
            Object.entries(e.answers).map(([q,v])=>`${esc(q)} → <b>${esc(v)}</b>`).join('<br>')}</div>`:''}</td></tr>`).join('')}
      </tbody></table></div></details>`
      :'<p class="muted" style="font-size:12.5px;margin-top:14px">No history yet — it starts with the first change.</p>'}
    ${leadDangerCard(l)}`);
}
/* Jedno zdanie o mailu z instrukcją wpłaty. Brak adresu portfela MUSI krzyczeć:
   klient dostaje wtedy prośbę o zapłatę bez informacji, gdzie zapłacić, a admin
   nie ma jak tego zauważyć po swojej stronie. */
function mailInfo(d){
  if(!d.emailed)return['No e-mail sent.','ok'];
  return d.payment_details?['Payment instructions e-mailed.','ok']
    :['E-mail sent WITHOUT payment details — send the customer the wallet address yourself.','err'];
}
/* Ostatnio użyty adres. Adresy są rotowane i wpisywane ręcznie, więc panel go
   PODPOWIADA, ale nigdy nie wysyła bez pokazania adminowi — jedna literówka
   znaczy, że pieniądze klienta trafiają do kogoś obcego. */
function lastWallet(){try{return JSON.parse(localStorage.getItem('pf_wallet')||'{}')}catch(e){return{}}}
function saveWallet(address,network){
  try{localStorage.setItem('pf_wallet',JSON.stringify({address,network}))}catch(e){}}

function askWallet(opts={}){
  return new Promise(resolve=>{
    const ost=lastWallet();
    const box=document.createElement('div');
    box.className='modal-wrap';
    const done=v=>{box.remove();resolve(v)};
    box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head"><h3>${opts.title||'Payment address'}</h3>
        <button class="icon-btn" id="wa-x" aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">This goes straight into the customer's e-mail.
        Paste it, don't retype it — a wrong address means the money is gone for good.</p>
      <div class="stack">
        <div><label class="muted" style="font-size:12px">Network</label>
          <input id="wa-net" class="inp" placeholder="e.g. USDT · TRC20" value="${esc(opts.network||ost.network||'')}"></div>
        <div><label class="muted" style="font-size:12px">Wallet address</label>
          <input id="wa-addr" class="inp" spellcheck="false" placeholder="Paste the wallet address" value="${esc(opts.address||ost.address||'')}"></div>
        <button class="btn-p lg" style="width:100%" id="wa-go">${opts.confirmLabel||'Send instructions'}</button>
        <p class="hint">Leave both empty to send the e-mail without payment details — it then tells the customer the address will follow.</p>
      </div></div>`;
    box.onclick=()=>done(null);
    box.querySelector('#wa-x').onclick=()=>done(null);
    box.querySelector('#wa-go').onclick=()=>{
      const address=(box.querySelector('#wa-addr').value||'').trim();
      const network=(box.querySelector('#wa-net').value||'').trim();
      if(address)saveWallet(address,network);
      done({address,network})};
    document.body.appendChild(box);
    box.querySelector('#wa-addr').focus();
  });
}

async function flagOrder(id,flag){
  const o=(window._orders||[]).find(x=>x.id===id);
  let dane={};
  if(flag){
    dane=await askWallet({title:`Awaiting crypto payment — order #${id}`,
      address:o&&o.payment_address,network:o&&o.payment_network});
    if(dane===null)return;
  }
  try{const d=await api(`/api/admin/orders/${id}/flag`,{method:'POST',body:JSON.stringify({
      flag,payment_address:dane.address||null,payment_network:dane.network||null})});
    if(o){o.flag=flag||null;
      if(dane.address){o.payment_address=dane.address;o.payment_network=dane.network||null}}
    const [txt,kind]=mailInfo(d);
    toast(flag?`Marked as awaiting crypto payment. ${txt}`:'Flag cleared.',flag?kind:'ok',7000);
    renderOrders()}
  catch(e){toast('Error: '+e.message,'err')}
}
/* Decyzja BOGO per zamówienie. `leadId` przychodzi z szuflady leada — wtedy
   odświeżamy szufladę; bez niego jesteśmy w zakładce Orders i wystarczy
   podmienić wpis w lokalnej liście. */
async function toggleOrderBogo(id,on,leadId){
  try{await api(`/api/admin/orders/${id}/bogo`,{method:'POST',body:JSON.stringify({bogo:on})});
    toast(on?'BOGO on — paying this order will also create a free second account of the same size.'
            :'BOGO off — this order creates one account.','ok',7000);
    const o=(window._orders||[]).find(x=>x.id===id); if(o)o.bogo=on;
    if(leadId)await openLead(leadId); else renderOrders();
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Link do zapłaty kartą za KONKRETNE zamówienie — do wklejenia klientowi na
   Telegramie. Token jest stały, więc drugie kliknięcie daje ten sam adres i nie
   unieważnia linku, którego klient już używa. */
async function payLink(id,prefix){
  let d;
  try{d=await api(`/api/admin/orders/${id}/pay-link`,{method:'POST'})}
  catch(e){toast('Error: '+e.message,'err');return}
  /* Ten sam token wisi pod dwiema markami. Który wysłać, wie admin, który przed
     chwilą z tym człowiekiem rozmawiał — zamówienie nie wie, skąd on przyszedł,
     więc zgadywanie za niego kończyłoby się linkiem z obcą marką. Bez
     PARTNER_PAY_BASE_URL nie ma z czego wybierać i nic się nie zmienia. */
  const url=d.partner_url?await askPayBrand(d.url,d.partner_url):d.url;
  if(!url)return;
  try{await navigator.clipboard.writeText(url);
    toast(`🔗 ${prefix?prefix+' ':''}Payment link copied — paste it to the customer.`,'ok',9000)}
  catch(e){showPayLink(url)}   /* Safari bez gestu / http: kopiowanie odpada, a link musi być widoczny */
}
/* Nazwy marek biorą się z samych adresów, a nie z kodu: domena partnera żyje
   w zmiennej środowiskowej serwera i nie ma prawa trafić do repozytorium. */
function askPayBrand(ourUrl,partnerUrl){
  const host=u=>{try{return new URL(u).hostname.replace(/^www\./,'')}catch(e){return u}};
  return new Promise(resolve=>{
    const box=document.createElement('div');box.className='modal-wrap';
    const done=v=>{box.remove();resolve(v)};
    box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head"><h3>Which page should they land on?</h3>
        <button class="icon-btn" id="pb-x" aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">Same order, same amount — only the page differs. Send the brand this customer came from.</p>
      <div class="stack">
        <button class="btn-p lg" style="width:100%" data-u="${esc(ourUrl)}">${esc(host(ourUrl))}</button>
        <button class="btn-o lg" style="width:100%" data-u="${esc(partnerUrl)}">${esc(host(partnerUrl))}</button>
        <p class="hint">Either way the card is charged by us — that is the name on the statement.</p>
      </div></div>`;
    box.onclick=()=>done(null);
    box.querySelector('#pb-x').onclick=()=>done(null);
    box.querySelectorAll('button[data-u]').forEach(b=>b.onclick=()=>done(b.dataset.u));
    document.body.appendChild(box);
  });
}
function showPayLink(url){
  const box=document.createElement('div');box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Payment link</h3></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:12px">This browser blocked the copy — select it and copy by hand.</p>
    <input class="inp" readonly value="${esc(url)}"></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  const i=box.querySelector('input');i.focus();i.select();
}
async function markOrderFailed(id){
  const reason=await askReason({
    title:`Mark order #${id} as failed`,
    hint:'The reason stays on the order in this panel. The trader is not e-mailed.',
    label:'Reason (kept in this panel)',
    presets:['Payment never arrived','Payment declined by the provider','Duplicate order',
             'Cancelled by the customer','Suspected fraud'],
    confirmLabel:'Mark failed',danger:true});
  if(reason===null)return;
  try{await api(`/api/admin/orders/${id}/mark-failed`,{method:'POST',body:JSON.stringify({reason})});
    toast('Order marked as failed.','ok');go('orders')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function markOrderPaid(id){
  if(!await askConfirm({title:'Mark this order as paid?',
    body:'This creates the challenge account and sends the trader their credentials, exactly like a completed card payment.',
    ok:'Mark as paid'}))return;
  try{const d=await api(`/api/admin/orders/${id}/mark-paid`,{method:'POST'});
    /* Zamowienie BOGO tworzy DWA konta — toast musi to powiedziec, a gdy grant
       nie wyszedl, admin ma sie dowiedziec od razu, nie z dzwonka po fakcie. */
    toast(d.already?'This order was already paid.'
      :d.bogo&&d.bogo_grant_ok?`✅ Paid. Account #${d.account_id} + free BOGO account created.`
      :d.bogo?`⚠️ Paid, account #${d.account_id} created — but the BOGO grant FAILED. Grant the second account manually.`
      :`✅ Paid. Account #${d.account_id} created.`,d.bogo&&!d.bogo_grant_ok?'err':'ok',d.bogo?9000:undefined);
    go('orders')}
  catch(e){toast('Error: '+e.message,'err')}
}

/* Bot pace is stored as a short key; the chip shows what it actually means. */
const PACE_TXT={light:'1–2 trades/day',steady:'4–8 trades/day',busy:'~20 trades/day'};

/* Numer konta w tabeli ma być drzwiami, nie ślepą liczbą — łańcuch
   zamówienie→konto kończył się na ręcznym szukaniu w zakładce Accounts. */
function accLink(id,label){
  const txt=label!=null&&label!==''?esc(String(label)):id?'#'+id:'';
  if(!id)return txt||'—';
  return `<a href="#" onclick="openAccount(${id});return false" title="Open the account card">${txt}</a>`;
}
async function openAccount(id){
  const a=await api('/api/accounts/'+id);
  const m=a.metrics||{};
  const cred=(l,v)=>v?`<div class="kv"><span>${l}</span><b>${esc(v)}</b></div>`:'';
  openOver(`${a.login} · ${a.trader_name||'—'}`,`
    <div class="chip-row">
      <span class="status ${esc(a.status)}"><span class="dot"></span>${STATUS_LBL[a.status]||esc(a.status)}</span>
      ${a.trader_email?`<span class="chip">${esc(a.trader_email)}</span>`:''}
      <span class="chip">${PHASE_LBL[a.phase]||esc(a.phase)}</span>
      <span class="chip">${esc(a.product_key)}</span>
      <span class="chip">DD <b>${esc(a.drawdown_type)}</b></span>
      <span class="chip">split <b>${a.profit_split_pct}%</b></span>
      ${a.source==='grant'?`<span class="status passed" style="text-transform:none">🎁 granted${a.grant_note?' · '+esc(a.grant_note):''}</span>`:''}
      ${a.bot_enabled?`<span class="status ${a.bot_paused?'pending':'funded'}" style="text-transform:none">${a.bot_paused?'⏸ Trade BOT paused':'🤖 Trade BOT'}</span>`:''}
    </div>
    ${a.breach_reason?`<div class="breach-item">Breached: ${esc(a.breach_reason)}</div>`:''}
    <div class="panel">
      <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
        <div class="seg-mini"><span>Objective lines</span>
          <span class="sw">
            <button data-on="1" class="${objLinesOn()?'on':''}" onclick="setObjLinesAdmin(true)">On</button>
            <button data-on="0" class="${objLinesOn()?'':'on'}" onclick="setObjLinesAdmin(false)">Off</button>
          </span></div>
      </div>
      <div style="height:220px;position:relative"><canvas id="o-chart"></canvas></div>
    </div>
    <div class="sec-card" style="margin:0">
      <h3 style="font-size:15px;margin-bottom:10px">Objectives</h3>
      <div class="progress-list">
        ${m.profit_target_pct?bar('Profit target',(m.profit_pct/m.profit_target_pct*100),false,`${(m.profit_pct||0).toFixed(2)}% / ${m.profit_target_pct}%`):''}
        ${bar('Daily loss used',m.daily_loss_used_pct,true,`floor $${fmt(m.daily_floor)}`)}
        ${bar('Max drawdown used',m.overall_dd_used_pct,true,`floor $${fmt(m.overall_floor)}`)}
      </div>
      <div class="kv" style="margin-top:12px"><span>Trading days</span><b>${m.trading_days}/${m.min_trading_days}</b></div>
      <div class="kv"><span>Initial balance</span><b>$${fmt(a.initial_balance)}</b></div>
      <div class="kv"><span>Created</span><b>${a.created_at?dstr(a.created_at):'—'}</b></div>
    </div>
    <div class="sec-card" style="margin:0">
      <h3 style="font-size:15px;margin-bottom:10px">MT5 credentials</h3>
      ${cred('Server',a.platform_server)}${cred('Login',a.platform_login)}
      ${cred('Password',a.platform_password)}
      ${a.mt5_backed===false?'<div class="kv"><span>Backed by MT5</span><b>no, generated locally</b></div>':''}
      ${!a.platform_password?'<p class="muted" style="font-size:12.5px">Not provisioned yet.</p>':''}
    </div>
    ${(a.trader_must_set_password&&a.trader_id)?`<div class="sec-card" style="margin:0">${inviteRow(a.trader_id,'acc')}</div>`:''}
    ${a.trader_id?`<div class="sec-card" style="margin:0" id="client-card">
      <h3 style="font-size:15px;margin-bottom:10px">Client</h3>
      <div class="muted" style="font-size:12.5px">Loading…</div>
    </div>`:''}
    <div class="sec-card" style="margin:0">
      <h3 style="font-size:15px;margin-bottom:10px">Phase</h3>
      <div class="chip-row" style="margin-bottom:10px">
        <span class="chip">now <b>${PHASE_LBL[a.phase]||esc(a.phase)}</b></span>
        <span class="status ${esc(a.status)}"><span class="dot"></span>${STATUS_LBL[a.status]||esc(a.status)}</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${a.phase!=='eval_1'?`<button class="btn-o sm" onclick="setPhase(${a.id},'eval_1')">Back to Phase 1</button>`:''}
        ${(a.steps>=2&&a.phase!=='eval_2')?`<button class="btn-o sm" onclick="setPhase(${a.id},'eval_2')">Move to Phase 2</button>`:''}
        ${a.phase!=='funded'?`<button class="btn-p sm" onclick="setPhase(${a.id},'funded')">Make funded</button>`:''}
        ${a.status!=='failed'?`<button class="btn-o sm" style="border-color:var(--red-line);color:var(--red)" onclick="breachAccount(${a.id})">Breach account</button>`:''}
      </div>
      <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
        The risk engine promotes accounts automatically once the profit target and minimum
        trading days are met, but only while something feeds the account. With the live feed
        off, this is the way to move a trader forward. Changing the phase <b>resets balance,
        drawdown and the trading-day counter</b>, exactly like an automatic promotion.</p>
    </div>
    <div class="sec-card" style="margin:0" id="cert-card">
      <h3 style="font-size:15px;margin-bottom:10px">Certificates</h3>
      <div class="muted" style="font-size:12.5px">Loading…</div>
    </div>
    <div class="sec-card" style="margin:0" id="payout-card">
      <h3 style="font-size:15px;margin-bottom:10px">Payouts &amp; certificates</h3>
      <div class="muted" style="font-size:12.5px">Loading…</div>
    </div>
    <div class="sec-card" style="margin:0">
      <h3 style="font-size:15px;margin-bottom:10px">Rule breaches</h3>
      ${(a.breaches||[]).length?a.breaches.map(b=>`<div class="breach-item" style="margin-bottom:8px">${dstr(b.ts)} · [${esc(b.type)}] ${esc(b.detail)}</div>`).join('')
        :'<p class="muted" style="font-size:12.5px">None recorded.</p>'}
    </div>
    <div class="sec-card" style="margin:0" id="hist-card">
      <h3 style="font-size:15px;margin-bottom:10px">History</h3>
      <div class="muted" style="font-size:12.5px">Loading…</div>
    </div>
    <div class="sec-card" style="margin:0">
      <h3 style="font-size:15px;margin-bottom:10px">Trade BOT</h3>
      ${a.bot_enabled?`
        <div class="chip-row" style="margin-bottom:12px">
          <span class="status ${a.bot_paused?'pending':'funded'}"><span class="dot"></span>${a.bot_paused?'paused':'running'}</span>
          <span class="chip">style <b>${esc(a.bot_style||'balanced')}</b></span>
          <span class="chip">pace <b>${esc(PACE_TXT[a.bot_pace]||a.bot_pace||'steady')}</b></span>
          ${a.bot_target_pct?`<span class="chip">stops at <b>+${a.bot_target_pct}%</b></span>`:''}
        </div>
        ${(a.bot_target_pct && (m.profit_pct||0) >= a.bot_target_pct)?`
          <div class="warn-box" style="margin:0 0 12px;background:var(--gold-bg);border:1px solid var(--gold-line);color:var(--gold-ink)">
            <b style="display:block">Target reached — the bot stopped opening positions</b>
            It is still running at +${(m.profit_pct||0).toFixed(2)}%, just idle. Raise the target below to continue from here.
          </div>`:''}
        ${a.market_closed?`
          <div class="warn-box" style="margin:0 0 12px;background:var(--gold-bg);border:1px solid var(--gold-line);color:var(--gold-ink)">
            <b style="display:block">Market closed. The bot takes no positions until Monday</b>
            This challenge has no Weekend Trading add-on, so there is nothing it could trade right now.
            It stays on and picks up on its own when the week opens.
          </div>`:''}
        <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
          <div><label class="muted" style="font-size:12px">Target profit</label>
            <input id="bot-newtarget" class="inp" type="number" step="0.1" min="0"
                   style="max-width:130px" value="${(a.bot_target_pct||0).toFixed(1)}"></div>
          <button class="btn-o" onclick="setBotTarget(${a.id})">Update target</button>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn-p" onclick="pauseBot(${a.id},${a.bot_paused?'false':'true'})">${a.bot_paused?'Resume bot':'Pause bot'}</button>
          <button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="stopBot(${a.id})">Stop bot</button>
        </div>
        <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
          <b>Target</b> is the account profit at which the bot stops trading. Raise it in 0.1% steps
          to let it keep going, or set <b>0</b> for no limit. Changing it never resyncs the balance,
          so the equity curve continues without a jump.
          <b>Pause</b> only stops new entries — an open position runs to its close, the account
          stays on bot data and the balance is kept. <b>Stop</b> ends the bot for good and
          resyncs the balance to the live feed.</p>`
      :`
        ${a.market_closed?`
          <div class="warn-box" style="margin:0 0 12px;background:var(--gold-bg);border:1px solid var(--gold-line);color:var(--gold-ink)">
            <b style="display:block">Market closed. Nothing will happen until Monday</b>
            You can start the bot now, but with no Weekend Trading add-on on this challenge it
            stays flat over the weekend and opens its first position when the week opens.
          </div>`:''}
        <div class="pool-form">
          <div><label class="muted" style="font-size:12px">Style</label>
            <select id="bot-style" class="inp">
              <option value="scalper">Scalper — many small trades</option>
              <option value="balanced" selected>Balanced — day trader</option>
              <option value="swing">Swing — fewer, larger trades</option>
            </select></div>
          <div><label class="muted" style="font-size:12px">Pace</label>
            <select id="bot-pace" class="inp">
              <option value="light">Light — 1–2 trades a day</option>
              <option value="steady" selected>Steady — 4–8 trades a day</option>
              <option value="busy">Busy — around 20 trades a day</option>
            </select></div>
          <div><label class="muted" style="font-size:12px">Stop at profit %</label>
            <input id="bot-target" class="inp" type="number" step="0.5" min="0" value="0" placeholder="0 = no limit"></div>
        </div>
        <button class="btn-p" onclick="startBot(${a.id})">Start Trade BOT</button>`}
      <p class="muted" style="font-size:12px;margin-top:12px;line-height:1.55">
        Fills this account's dashboard with trading activity: varied instruments, a positive
        equity curve, always inside the account's own risk rules.
        <b>While the bot runs, this account is not read from MT5</b>, so the portal will
        diverge from the trader's MetaTrader terminal, and stopping the bot resyncs the
        balance back to the real account. <b>Pace is how many trades a day</b> the bot takes:
        it spreads them across the day and holds each position for a matching stretch, so
        Busy is around one trade an hour, not one a minute. Style shades that count: a
        scalper sits at the top of the range, a swing trader at the bottom.
        <b>No pace trades over the weekend</b> unless the challenge has the Weekend Trading
        add-on, and then the bot only touches crypto, the one market open on Saturday.</p>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="deleteAccount(${a.id},'${esc(a.login)}')">Delete account</button>
      ${a.trader_id?`<button class="btn-o" style="border-color:var(--red-line);color:var(--red)" onclick="deleteTrader(${a.trader_id},'${esc(a.trader_email||a.trader_name||'')}')"
        title="Removes the client and ALL their data, freeing the e-mail for a fresh signup">Delete client &amp; all data</button>`:''}
    </div>`);
  renderCerts(a.id);
  renderPayouts(a.id);
  renderHistory(a.id);
  if(a.trader_id)renderClientCard(a.trader_id,a.trader_email||a.trader_name||'');
  window._oAcc=a;
  drawAdminChart();
}

/* ---------- objective lines on the slide-over chart (same as the portal) ---------- */
function objLinesOn(){try{return localStorage.getItem('pf_obj_lines')!=='off'}catch(e){return true}}
function setObjLinesAdmin(on){
  try{localStorage.setItem('pf_obj_lines',on?'on':'off')}catch(e){}
  document.querySelectorAll('.seg-mini button').forEach(b=>b.classList.toggle('on',(b.dataset.on==='1')===on));
  drawAdminChart();
}
function adminDetailLines(){
  const a=window._oAcc; if(!a)return [];
  const m=a.metrics||{},th=chartTheme(),L=[];
  if(m.target_equity)L.push({y:m.target_equity,label:'Target',color:th.green});
  if(m.daily_floor)L.push({y:m.daily_floor,label:'Daily loss limit',color:th.gold});
  if(m.overall_floor)L.push({y:m.overall_floor,label:'Max loss',color:th.red});
  L.push({y:a.initial_balance,label:'Account size',color:th.dim});
  return L;
}
function drawAdminChart(){
  const a=window._oAcc,c=(a&&a.equity_curve)||[];
  if(window._oChart){window._oChart.destroy();window._oChart=null}
  if(!document.getElementById('o-chart')||c.length<2)return;
  window._oChart=new Chart($('o-chart'),equityChartConfig(c,{lines:objLinesOn()?adminDetailLines():[]}));
}
function bar(label,pct,danger,suffix){const p=Math.min(100,Math.max(0,pct||0));
  const cls=danger?(p>=100?'bad':p>=70?'warn':'mid'):'ok';
  return `<div><div class="prog-top"><span>${label}</span><b>${suffix||''} · ${(pct||0).toFixed(1)}%</b></div>
    <div class="prog-bar"><i class="${cls}" style="width:${p}%"></i></div></div>`}

async function startBot(id){
  const body={style:$('bot-style').value,pace:$('bot-pace').value,
    target_pct:parseFloat($('bot-target').value||'0')||0};
  try{await api(`/api/admin/accounts/${id}/bot`,{method:'POST',body:JSON.stringify(body)});
    toast('🤖 Trade BOT started. The dashboard starts filling on the next poller tick.','ok',8000);
    openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}
/* ---------- account timeline (orders, payments, breaches, payouts) ---------- */
async function renderHistory(id){
  const el=$('hist-card'); if(!el)return;
  let d; try{d=await api(`/api/accounts/${id}/history`)}catch(e){
    el.innerHTML='<h3 style="font-size:15px">History</h3>'
      +`<p class="muted" style="font-size:12.5px">Could not load: ${esc(e.message)}</p>`;return}
  const ic={order:'🧾',payment:'💳',breach:'⛔',payout:'💸',account:'🏁'};
  el.innerHTML=`<h3 style="font-size:15px;margin-bottom:10px">History</h3>`
    +((d.items||[]).length?d.items.map(i=>`
      <div class="kv" style="align-items:flex-start">
        <span style="white-space:nowrap" class="muted">${dstr(i.ts)}</span>
        <b style="text-align:right;font-weight:500">${ic[i.kind]||'·'} ${esc(i.label)}</b>
      </div>`).join('')
      :'<p class="muted" style="font-size:12.5px">Nothing recorded yet.</p>');
}

/* ---------- dziennik klienta (kto, kiedy, co robił) ---------- */
const JRN_ICO={login:'🔑',view:'👀',order:'🧾',payment:'💳',breach:'⛔',payout:'💸',
  account:'🏁',ticket:'🎫',telemetry:'⚡'};
function journalChips(t){
  const chips=[];
  if(t.awaiting_claim)chips.push('<span class="status pending"><span class="dot"></span>awaiting claim</span>');
  else if(t.claimed_at)chips.push(`<span class="status passed"><span class="dot"></span>claimed ${dstr(t.claimed_at)}</span>`);
  if(t.invited_at)chips.push(`<span class="chip">invite sent ${dstr(t.invited_at)}</span>`);
  if(t.logged_in_today)chips.push('<span class="status funded"><span class="dot"></span>signed in today</span>');
  else if(t.last_login_at)chips.push(`<span class="chip">last sign-in ${dstr(t.last_login_at)}</span>`);
  else chips.push('<span class="status failed"><span class="dot"></span>never signed in</span>');
  chips.push(`<span class="chip">${t.logins_7d||0} sign-in${t.logins_7d===1?'':'s'} in 7 days</span>`);
  if(t.last_seen_at&&t.last_seen_at!==t.last_login_at)chips.push(`<span class="chip">last seen ${dstr(t.last_seen_at)}</span>`);
  if(t.kyc_status)chips.push(`<span class="chip">KYC <b>${esc(t.kyc_status)}</b></span>`);
  if(t.kyc_requested_at)chips.push(`<span class="chip">KYC asked ${dstr(t.kyc_requested_at)}</span>`);
  if(t.kyc_locked&&t.kyc_status!=='approved')chips.push('<span class="status failed"><span class="dot"></span>portal paused</span>');
  return `<div class="chip-row" style="margin-bottom:12px">${chips.join('')}</div>`;
}
/* Prosba o weryfikacje wysylana z reki. Klient, ktory przeszedl ewaluacje i nigdy
   nie wszedl w zakladke KYC, nie dostaje od nas w tej sprawie ZADNEGO maila —
   wychodzi to dopiero przy wniosku o wyplate, ktory trzeba wtedy odrzucic.
   Dziala takze przed funded: prosba sama otwiera weryfikacje temu klientowi
   (`kyc_requested_at` po stronie serwera). Przycisku nie ma tylko przy
   pending/approved, bo dokumenty juz sa i nie ma o co prosic. */
function kycAskBtn(t,gdzie){
  if(!t||t.kyc_status==='approved'||t.kyc_status==='pending')return '';
  return `<button class="btn-o sm" style="margin-top:10px" onclick="requestKyc(${t.id},'${jsq(t.email||'')}','${gdzie}')">`
    +`${t.kyc_requested_at?'Ask for verification again':'Ask for verification'}</button>`;
}
async function requestKyc(tid,email,gdzie){
  if(!await askConfirm({title:'Ask this client to verify their identity?',
    body:'They get an e-mail with a link to the Verification tab, and it opens '
      +'verification for them — also before their first funded account.',
    ok:'Send the request'}))return;
  try{await api(`/api/admin/kyc/${tid}/request`,{method:'POST'});
    toast('Verification request sent.','ok');
    /* Odswiezamy TE szuflade, z ktorej padlo klikniecie: karta klienta i dziennik
       renderuja sie do tego samego overlaya, wiec zgadywanie zamienialoby jedna
       w druga. */
    if(gdzie==='journal')openTraderJournal(tid,email); else renderClientCard(tid,email);
  }catch(e){toast('Error: '+e.message,'err')}
}
function journalTimeline(items){
  if(!(items||[]).length)return '<p class="muted" style="font-size:12.5px">Nothing recorded yet.</p>';
  /* Data raz, nad grupą wpisów — sto wierszy z pełnym timestampem czyta się
     gorzej niż dzień jako nagłówek i sama godzina przy wpisie. */
  let out='',dzien='';
  for(const i of items){
    const w=wawIso(i.ts||'');
    const d=w.slice(0,10);
    if(d!==dzien){dzien=d;out+=`<div class="muted" style="font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;margin:14px 0 4px">${dstr(i.ts).split(',')[0]||d}</div>`}
    out+=`<div class="kv" style="align-items:flex-start">
      <span style="white-space:nowrap" class="muted">${w.slice(11,16)}</span>
      <b style="text-align:right;font-weight:500">${JRN_ICO[i.kind]||'·'} ${esc(i.label)}</b>
    </div>`;
  }
  return out;
}
async function renderClientCard(tid,email){
  const el=$('client-card'); if(!el)return;
  let d; try{d=await api(`/api/admin/traders/${tid}/journal`)}catch(e){
    el.innerHTML='<h3 style="font-size:15px">Client</h3>'
      +`<p class="muted" style="font-size:12.5px">Could not load: ${esc(e.message)}</p>`;return}
  const t=d.trader||{};
  el.innerHTML=`<h3 style="font-size:15px;margin-bottom:10px">Client</h3>
    ${journalChips(t)}
    <div class="kv"><span>E-mail</span><b>${esc(t.email||email||'—')}</b></div>
    <div class="kv"><span>Signed up</span><b>${t.created_at?dstr(t.created_at):'—'}</b></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn-o sm" style="margin-top:10px" onclick="openTraderJournal(${tid},'${esc(t.email||email||'')}')">Full activity journal</button>
      ${kycAskBtn(t,'card')}
    </div>`;
}
async function openTraderJournal(tid,email){
  let d; try{d=await api(`/api/admin/traders/${tid}/journal`)}catch(e){toast('Error: '+e.message,'err');return}
  const t=d.trader||{};
  openOver(`Journal · ${t.email||email||('trader #'+tid)}`,
    journalChips(t)
    +`<div style="display:flex">${kycAskBtn(t,'journal')}</div>`
    +`<p class="muted" style="font-size:12.5px;margin:2px 0">Everything this client did — sign-ins, portal visits, orders, payouts, tickets — newest first.</p>`
    +journalTimeline(d.items));
}

/* Zakladka Activity: te same dane co karta Client, ale dla WSZYSTKICH naraz —
   filtry odpowiadaja na pytania dzialu („kto nie odebral konta?", „kto zamilkl?")
   bez klikania po kolei w kazde konto. */
const JRN_FILTERS=[
  ['all','All',()=>true],
  ['today','Active today',t=>t.logged_in_today],
  ['awaiting','Awaiting claim',t=>t.awaiting_claim],
  ['never','Never signed in',t=>!t.last_login_at],
  ['quiet','Quiet 7+ days',t=>t.last_login_at&&!t.logins_7d],
];
function renderActivity(){
  const all=window._jrn||[];
  const f=window._jrnFilter||'all';
  const q=(window._jrnQ||'').toLowerCase();
  const test=(JRN_FILTERS.find(x=>x[0]===f)||JRN_FILTERS[0])[2];
  const rows=all.filter(t=>test(t)&&(!q||[t.email,t.full_name]
    .some(x=>String(x||'').toLowerCase().includes(q))));
  const chip=t=>t.awaiting_claim
    ?'<span class="status pending"><span class="dot"></span>awaiting claim</span>'
    :(t.claimed_at?'<span class="status passed"><span class="dot"></span>claimed</span>'
                  :'<span class="muted">—</span>');
  const login=t=>t.logged_in_today
    ?'<span class="status funded"><span class="dot"></span>today</span>'
    :(t.last_login_at?dstr(t.last_login_at)
      :'<span class="status failed"><span class="dot"></span>never</span>');
  $('view').innerHTML=`<div class="toolbar">
      ${searchBox('jrn-q','_jrnQ','renderActivity','Search name or email…')}
      <div class="seg">${JRN_FILTERS.map(([k,l])=>
        `<button class="${f===k?'on':''}"${k==='all'?' data-all="1"':''}
          onclick="window._jrnFilter='${f===k?'all':k}';renderActivity()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${all.length}${impPill()}</span>
    </div>`
    +(rows.length?`<div class="tbl-wrap rtbl-wrap"><table class="tbl sortable rtbl" data-tkey="admin.activity">
      <thead><tr><th>Client</th><th>Claim</th><th>Last sign-in</th><th>7 days</th><th>Last seen</th><th>Accounts</th><th>KYC</th></tr></thead>
      <tbody>${rows.map(t=>`<tr class="clickable" onclick="openTraderJournal(${t.id},'${jsq(t.email||'')}')">
        <td class="rt-main" data-l="Client">${esc(t.full_name||'—')}<div class="muted" style="font-size:11.5px">${esc(t.email)}</div></td>
        <td data-l="Claim" data-sort="${t.awaiting_claim?0:(t.claimed_at?2:1)}">${chip(t)}</td>
        <td data-l="Last sign-in" data-sort="${esc(t.last_login_at||'')}">${login(t)}</td>
        <td class="muted" data-l="7 days" data-sort="${t.logins_7d}">${t.logins_7d?t.logins_7d+'×':'—'}</td>
        <td class="muted" data-l="Last seen" data-sort="${esc(t.last_seen_at||'')}">${t.last_seen_at?dstr(t.last_seen_at):'—'}</td>
        <td class="muted" data-l="Accounts" data-sort="${t.accounts}">${t.accounts||'—'}</td>
        <td class="muted" data-l="KYC">${esc(t.kyc_status||'—')}</td></tr>`).join('')}
      </tbody></table></div>`
    :`<div class="empty"><h3>${q||f!=='all'?'No clients match':'No clients yet'}</h3><p>${
      q||f!=='all'?'Clear the search or pick another filter.':'Client accounts show up here as they appear.'}</p></div>`);
}

/* ---------- achievement certificates ---------- */
async function renderCerts(id){
  const el=$('cert-card'); if(!el)return;
  let list; try{list=await api(`/api/admin/accounts/${id}/certificates`)}catch(e){
    el.innerHTML='<h3 style="font-size:15px">Certificates</h3>'
      +`<p class="muted" style="font-size:12.5px">Could not load: ${esc(e.message)}</p>`;return}
  el.innerHTML=`<h3 style="font-size:15px;margin-bottom:10px">Certificates</h3>
    ${list.map(c=>`<div class="kv" style="align-items:center;flex-wrap:wrap;row-gap:6px">
      <span>${esc(c.label)}</span>
      <span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;row-gap:6px">
        ${c.url
          ? `<a class="btn-o sm" href="${c.url}" target="_blank">Open</a>
             <button class="btn-o sm" onclick="copyCert('${location.origin}${c.url}')">Copy link</button>`
          : c.available
            ? `<button class="btn-p sm" onclick="makeAchievementCert(${id},'${c.kind}')">Issue</button>`
            : `<span class="muted" style="font-size:12px">not reached yet</span>`}
      </span></div>`).join('')}
    <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
      Each stage gets its own document with its own verifiable number. A certificate can only be
      issued for a stage the account actually reached. Move the phase first if you need it earlier.</p>`;
}
async function makeAchievementCert(id,kind){
  try{const r=await api(`/api/admin/accounts/${id}/certificate`,{method:'POST',body:JSON.stringify({kind})});
    toast(`Certificate issued: ${location.origin}${r.url}`,'ok',9000); renderCerts(id);
  }catch(e){toast('Error: '+e.message,'err')}
}

/* ---------- shared confirmation ----------
   Pytanie w oknie PANELU, nie przegladarki. Natywny confirm() przedstawial sie
   jako "Komunikat ze strony protradersfunding.com", mial przyciski w jezyku
   systemu i wygladal identycznie dla skasowania klienta i dla zatrzymania bota
   — czyli nie niosl zadnej informacji o wadze decyzji.

   `requireText` zastepuje osobny prompt(): przy operacjach nieodwracalnych
   admin wpisuje slowo w TYM SAMYM oknie, zamiast odpowiadac na dwa systemowe
   monity pod rzad. Zwraca Promise<bool>. */
function askConfirm({title,body,ok='Confirm',cancel='Cancel',danger=false,requireText=null}){
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
      <p class="muted" style="font-size:12.5px;line-height:1.6;margin:2px 0 14px">${body}</p>
      ${requireText?`<input class="inp" id="ask-txt" autocomplete="off" spellcheck="false"
        placeholder="Type ${esc(requireText)} to confirm" style="margin-bottom:14px">`:''}
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="${danger?'btn-danger':'btn-p'}" id="ask-ok"${requireText?' disabled':''}>${esc(ok)}</button>
        <button class="btn-o" id="ask-no">${esc(cancel)}</button>
      </div></div>`;
    document.body.appendChild(w);
    const bOk=w.querySelector('#ask-ok');
    bOk.onclick=()=>koniec(true);
    w.querySelector('#ask-no').onclick=()=>koniec(false);
    w.querySelector('#ask-x').onclick=()=>koniec(false);
    if(requireText){
      const inp=w.querySelector('#ask-txt');
      inp.oninput=()=>{bOk.disabled=inp.value.trim().toUpperCase()!==requireText.toUpperCase()};
      inp.onkeydown=e=>{if(e.key==='Enter'&&!bOk.disabled)koniec(true)};
      inp.focus();
    }else{
      bOk.focus();
    }
  });
}

/* ---------- shared reason picker ----------
   Every admin action that needs a trader-facing reason goes through this modal:
   pick a preset from the list or choose Custom… and type your own.
   Resolves with the reason string, or null when the admin cancels. */
function askReason(opts){
  return new Promise(resolve=>{
    const box=document.createElement('div');
    box.id='reason-modal';box.className='modal-wrap';
    const done=v=>{box.remove();resolve(v)};
    box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head"><h3>${opts.title}</h3>
        <button class="icon-btn" id="rs-x" aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      ${opts.hint?`<p class="muted" style="font-size:12.5px;margin-bottom:14px">${opts.hint}</p>`:''}
      <div class="stack">
        <div><label class="muted" style="font-size:12px">${opts.label||'Reason (shown to the trader)'}</label>
          <select id="rs-sel" class="inp">
            ${opts.presets.map(p=>`<option>${p}</option>`).join('')}
            <option value="__custom__">Custom…</option></select></div>
        <textarea id="rs-txt" class="inp hidden" rows="3" maxlength="200"
          placeholder="Write your own reason"></textarea>
        <button class="btn-p lg" style="width:100%${opts.danger?';background:var(--red)':''}"
          id="rs-go">${opts.confirmLabel||'Confirm'}</button>
      </div></div>`;
    box.onclick=()=>done(null);
    box.querySelector('#rs-x').onclick=()=>done(null);
    const sel=box.querySelector('#rs-sel'),txt=box.querySelector('#rs-txt');
    sel.onchange=()=>{const c=sel.value==='__custom__';
      txt.classList.toggle('hidden',!c); if(c)txt.focus()};
    box.querySelector('#rs-go').onclick=()=>{
      const custom=sel.value==='__custom__';
      const v=custom?(txt.value||'').trim():sel.value;
      if(custom&&!v){txt.focus();return}
      done(v)};
    document.body.appendChild(box);
  });
}

/* ---------- payouts + certificates ---------- */
async function breachAccount(id){
  const breachReason=await askReason({
    title:'Breach this account',danger:true,confirmLabel:'Breach account',
    hint:'The account is closed as <b>failed</b> and the reason below is shown to the trader in the portal and by e-mail.',
    presets:['Daily loss limit exceeded','Maximum drawdown exceeded',
      'Prohibited trading strategy','Copy trading between accounts',
      'News trading violation','Account sharing','Closed by the risk desk']});
  if(breachReason===null)return;   // Cancel
  try{const r=await api(`/api/admin/accounts/${id}/breach`,{method:'POST',
      body:JSON.stringify({reason:breachReason})});
    toast(`Account closed: ${r.reason}`,'ok',8000); openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}

async function setPhase(id,phase){
  const phaseNames={eval_1:'Phase 1',eval_2:'Phase 2',funded:'Funded'};
  if(!await askConfirm({title:`Move this account to ${phaseNames[phase]}?`,
    body:'Balance, drawdown and the trading-day counter reset, same as an automatic promotion.',
    ok:`Move to ${phaseNames[phase]}`}))return;
  try{await api(`/api/admin/accounts/${id}/phase`,{method:'POST',body:JSON.stringify({phase})});
    toast(`Account moved to ${phaseNames[phase]}.`,'ok'); openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}

async function renderPayouts(id){
  const el=$('payout-card'); if(!el)return;
  let d; try{d=await api(`/api/admin/accounts/${id}/payouts`)}catch(e){
    el.innerHTML='<h3 style="font-size:15px">Payouts &amp; certificates</h3>'
      +`<p class="muted" style="font-size:12.5px">Could not load: ${esc(e.message)}</p>`;return}
  const lista=d.payouts.length?d.payouts.map(p=>`
    <div class="kv" style="align-items:center;flex-wrap:wrap;row-gap:6px">
      <span>${dstr(p.ts)} · profit $${fmt(p.profit_amount)}${p.note?' · '+esc(p.note):''}</span>
      <span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;row-gap:6px">
        <b class="up">$${fmt(p.trader_share)}</b>
        ${p.cert_url
          ? `<span class="status ${p.show_on_lp?'paid':'pending'}" title="Landing page strip">
               <span class="dot"></span>${p.show_on_lp?'On landing page':'Not published'}</span>
             <a class="btn-o sm" href="${p.cert_url}" target="_blank">Certificate</a>
             <button class="btn-o sm" onclick="copyCert('${location.origin}${p.cert_url}')">Copy link</button>
             <button class="btn-o sm" onclick="setCertLp(${p.id},${p.show_on_lp?'false':'true'},${id})">
               ${p.show_on_lp?'Take off the LP':'Put on the LP'}</button>
             <button class="btn-o sm" onclick="revokeCert(${p.id},${id})">Revoke</button>`
          : `<button class="btn-o sm" onclick="askCertLp(${p.id},${id})">Generate</button>`}
      </span>
    </div>`).join('')
    : '<p class="muted" style="font-size:12.5px">No payouts on this account yet.</p>';
  el.innerHTML=`<h3 style="font-size:15px;margin-bottom:10px">Payouts &amp; certificates</h3>
    ${lista}
    ${d.status!=='funded'?`<p class="muted" style="font-size:12.5px;margin-top:12px">
        Payouts can only be issued on a <b>funded</b> account. This one is
        <b>${esc(d.status)}</b>. Move the phase to Funded first.</p>`:`
    <div class="pool-form" style="margin-top:14px">
      <div><label class="muted" style="font-size:12px">Trader payout ($)</label>
        <input id="po-amount" class="inp" type="number" step="0.01" min="0.01" value="${d.suggested_share||''}"
          placeholder="${d.suggested_share?'':'enter an amount'}"></div>
      <div><label class="muted" style="font-size:12px">Method</label>
        <select id="po-method" class="inp">
          <option value="bank">Bank transfer</option><option value="crypto">Crypto (USDT)</option>
          <option value="wise">Wise</option><option value="rise">Rise</option></select></div>
      <div><label class="muted" style="font-size:12px">Note (optional)</label>
        <input id="po-note" class="inp" placeholder="e.g. 1st payout"></div>
    </div>
    <label style="display:flex;align-items:center;gap:8px;margin:12px 0;font-size:12.5px">
      <input type="checkbox" id="po-lp" checked>
      <span>Show this payout on the landing page strip
        <span class="muted">(the certificate, its QR and the verification link are created either
        way — this only decides whether the trader's payout is shown publicly)</span></span>
    </label>
    <button class="btn-p" onclick="issuePayout(${id})">Issue payout + certificate</button>`}
    <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
      Current profit <b>$${fmt(d.profit)}</b> · split <b>${d.split_pct}%</b> → suggested
      <b>$${fmt(d.suggested_share)}</b>. Issuing a payout books it and
      <b>resets the account balance to its starting capital</b>, exactly like approving
      a trader's request. The paid-out profit stops counting toward the next one.</p>`;
}
async function issuePayout(id){
  const amount=parseFloat($('po-amount').value||'0');
  if(!(amount>0)){toast('Enter a payout amount greater than 0.','err');return}
  const naLp=$('po-lp')?$('po-lp').checked:true;
  if(!await askConfirm({title:`Issue a payout of $${amount.toFixed(2)}?`,
    body:'It is booked as paid and the account balance resets to its starting capital. '
      +(naLp?'The certificate will also show on the landing page.'
            :'The certificate stays off the landing page.'),
    ok:'Issue payout',danger:true}))return;
  try{const p=await api(`/api/admin/accounts/${id}/payout`,{method:'POST',
      body:JSON.stringify({amount,method:$('po-method').value,note:($('po-note').value||'').trim()||null,
        show_on_lp:naLp})});
    toast(`✅ Payout $${fmt(p.trader_share)} issued${naLp?' and published on the landing page':''}. `
      +`Certificate: ${location.origin}${p.cert_url}`,'ok',10000);
    openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Publishing on the landing page is a separate decision from issuing the
   document, so it gets a real dialog instead of confirm(): in a native confirm
   "Cancel" cannot tell "do not publish" apart from "do not issue at all". */
function askCertLp(pid,accId){
  const w=document.createElement('div'); w.id='lp-modal'; w.className='modal-wrap';
  w.onclick=e=>{if(e.target===w)w.remove()};
  w.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Publish this payout on the landing page?</h3></div>
    <p class="muted" style="font-size:12.5px;margin:2px 0 14px">
      The certificate is created either way, with its QR code and a working
      <b>/verify</b> link the trader can share. This only decides whether the payout also shows up
      on the public strip on the landing page, with the name masked.</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn-p" onclick="makeCert(${pid},${accId||'null'},true)">Yes, show it on the landing page</button>
      <button class="btn-o" onclick="makeCert(${pid},${accId||'null'},false)">No, keep it private</button>
      <button class="btn-o" onclick="$('lp-modal').remove()">Cancel</button>
    </div></div>`;
  document.body.appendChild(w);
}
/* accId comes from the account slide-over; the Payouts view calls these
   without it and then the whole list is reloaded instead. */
async function makeCert(pid,accId,onLp){
  const m=$('lp-modal'); if(m)m.remove();
  try{await api(`/api/admin/payouts/${pid}/certificate`,{method:'POST',
      body:JSON.stringify({show_on_lp:onLp!==false})});
    toast(onLp===false
      ? 'Certificate generated. It stays off the landing page.'
      : 'Certificate generated and published on the landing page.','ok');
    accId?renderPayouts(accId):VIEWS.payouts();
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Publikacja i sam dokument to dwie rozne rzeczy: zdjecie wpisu z pasa NIE
   zabiera traderowi certyfikatu ani linku do weryfikacji. */
async function setCertLp(pid,show,accId){
  try{await api(`/api/admin/payouts/${pid}/lp`,{method:'POST',body:JSON.stringify({show})});
    toast(show?'Published on the landing page.':'Taken off the landing page. The certificate still works.','ok');
    accId?renderPayouts(accId):VIEWS.payouts();
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Revoking kills the DOCUMENT — the public link stops working. To only take an
   entry off the strip use setCertLp(). The payout row stays either way. */
async function revokeCert(pid,accId){
  /* Bez okna potwierdzenia: withUndo i tak trzyma zadanie 5 s z przyciskiem
     cofniecia, a certyfikat da sie wygenerowac od nowa — podwojna zapora
     (confirm + undo) tylko spowalniala moderacje na telefonie. */
  withUndo('Revoking the certificate',async()=>{
    try{await api(`/api/admin/payouts/${pid}/certificate`,{method:'DELETE'});
      toast('Certificate revoked. Removed from the landing page.','ok');
      accId?renderPayouts(accId):VIEWS.payouts();
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
function copyCert(url){navigator.clipboard.writeText(url)
  .then(()=>toast('Certificate link copied.','ok'),()=>toast('Could not copy.','err'))}

async function pauseBot(id,paused){
  try{await api(`/api/admin/accounts/${id}/bot`,{method:'PATCH',body:JSON.stringify({paused})});
    toast(paused?'⏸ Bot paused. No new entries, the account keeps its balance.'
                :'▶️ Bot resumed.','ok');
    openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}
async function setBotTarget(id){
  const cel=parseFloat($('bot-newtarget').value);
  if(isNaN(cel)||cel<0){toast('Enter a target of 0% or more.','err');return}
  try{
    const r=await api(`/api/admin/accounts/${id}/bot`,{method:'PATCH',
      body:JSON.stringify({target_pct:cel})});
    toast(r.bot_target_pct?`🎯 Target set to +${r.bot_target_pct}%. The bot continues from here.`
                          :'🎯 Target removed. The bot trades with no profit limit.','ok');
    openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}
async function stopBot(id){
  // After stopping, the account returns to the real feed and resyncs to the
  // MT5 account state — generated profit vanishes from the dashboard. The admin
  // must know that BEFORE clicking: from outside it looks like data loss.
  if(!await askConfirm({title:'Stop the Trade BOT?',
    body:'The account goes back to the live MT5 feed, so its balance and equity curve resync to the '
      +'real account. <b>The generated profit disappears from the dashboard.</b> The trade history stays.',
    ok:'Stop the bot',danger:true}))return;
  try{const r=await api(`/api/admin/accounts/${id}/bot`,{method:'DELETE'});
    toast(`Bot stopped. Balance $${fmt(r.balance)}, and the account goes back to the live MT5 feed.`,'ok',8000);
    openAccount(id);
  }catch(e){toast('Error: '+e.message,'err')}
}

/* Ta sama sciezka co X w tabeli: jedno pytanie, 5 s na cofniecie. Wczesniej
   przycisk w slide-overze kasowal OD RAZU — jedyne usuwanie w panelu bez okna
   na „jednak nie". Slide-over zamyka sie dopiero po potwierdzeniu. */
async function deleteAccount(id,login){
  if(await deleteAccountRow(id,login))closeOver();
}
async function deleteTrader(tid,who){
  /* Jedno okno zamiast dwoch: wczesniej admin odpowiadal na confirm(), a zaraz
     potem na prompt() o wpisanie DELETE — dwa systemowe monity pod rzad pod
     najgrozniejsza operacja w panelu. Teraz pytanie i potwierdzenie sa razem. */
  if(!await askConfirm({title:`Delete ${esc(who)} and ALL their data?`,
    body:'This permanently removes their profile, every challenge account, orders, KYC documents, '
      +'payouts, tickets and notifications.<br><br>The e-mail address becomes free again, so the '
      +'client can sign up from scratch. <b>This cannot be undone.</b>',
    ok:'Delete everything',danger:true,requireText:'DELETE'}))return;
  try{
    const r=await api('/api/admin/traders/'+tid,{method:'DELETE'});
    closeOver();toast(`Client ${r.email} deleted (${r.accounts_removed} account${r.accounts_removed===1?'':'s'}). E-mail is free again.`,'ok',8000);
    go('accounts');
  }catch(e){toast('Error: '+e.message,'err')}
}

/* ---------- actions ---------- */
/* Obie akcje pod busy(): drugi klik w trakcie requestu to no-op. Serwer ma
   własną blokadę (warunkowy UPDATE), ale bez tej dubel kończył się mylącym
   „already handled" zamiast po prostu nie zadziałać. */
async function rejectPayout(id,btn){
  await busy(btn,null,async()=>{
    const reason=await askReason({
      title:'Reject this payout request',danger:true,confirmLabel:'Reject request',
      hint:'The trader sees the reason under the request status and gets it by e-mail.',
      presets:['Profit target not met','Minimum trading days not met',
        'Open positions at the time of the request','Trading activity under review',
        'KYC verification incomplete']});
    if(reason===null)return;   // Cancel
    try{await api(`/api/admin/payout-requests/${id}/reject`,{method:'POST',
        body:JSON.stringify({reason})});
      toast('Request rejected.','ok');go('payouts')}
    catch(e){toast('Error: '+e.message,'err')}
  });
}
async function approvePayout(id,btn){
  await busy(btn,null,async()=>{
    if(!await askConfirm({title:'Approve and pay this request?',
      body:'This pays the trader share, plus the fee refund on a first payout. <b>It cannot be undone.</b>',
      ok:'Approve & pay',danger:true}))return;
    try{const d=await api(`/api/admin/payout-requests/${id}/approve`,{method:'POST'});
      toast(`✅ Paid $${fmt(d.total_paid)}${d.fee_refund?` (incl. $${fmt(d.fee_refund)} fee refund)`:''}`,'ok');
      go('payouts');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function approveKyc(tid){
  /* Jeden tap, zero okien — pomylke cofa przycisk w toascie (istniejacy
     endpoint /reset, ten sam co Revert w historii). */
  try{
    await api(`/api/admin/kyc/${tid}/approve`,{method:'POST'});go('kyc');
    undoToast('KYC approved.',async()=>{
      try{await api(`/api/admin/kyc/${tid}/reset`,{method:'POST'});
        toast('Approval undone — back in the pending queue.','ok');go('kyc');
      }catch(e){toast('Error: '+e.message,'err')}
    });
  }catch(e){toast('Error: '+e.message,'err')}
}
async function rejectKyc(tid){
  const reason=await askReason({
    title:'Reject this verification',danger:true,confirmLabel:'Reject KYC',
    hint:'The trader is asked to submit their KYC again. The reason shows in their portal and in the e-mail.',
    presets:['Document unreadable or blurry','Document expired',
      'Name does not match the account','Incomplete documents']});
  if(reason===null)return;   // Cancel
  try{await api(`/api/admin/kyc/${tid}/reject`,{method:'POST',
      body:JSON.stringify({reason})});toast('KYC rejected.','ok');go('kyc')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function revertKyc(tid){
  if(!await askConfirm({title:'Revert this KYC decision?',
    body:'The application goes back to the pending queue. The trader is <b>not</b> notified.',
    ok:'Revert decision'}))return;
  try{await api(`/api/admin/kyc/${tid}/reset`,{method:'POST'});toast('Decision reverted. Back in the pending queue.','ok');go('kyc')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function viewDoc(tid,kind){
  const r=await fetch(`/api/admin/kyc/${tid}/doc/${kind}`,{headers:adminH()});
  if(!r.ok){toast('Could not load the document.','err');return}
  window.open(URL.createObjectURL(await r.blob()),'_blank');
}

/* ---------- telemetry drill-down ---------- */
function telemetryRows(items){
  return `<div class="tbl-wrap rtbl-wrap"><table class="tbl rtbl">
    <thead><tr><th>Time</th><th>Event</th><th>Trader</th><th>Details</th></tr></thead>
    <tbody>${items.map(e=>{
      let props='';
      try{props=Object.entries(JSON.parse(e.props||'{}')).map(([k,v])=>`${esc(k)}: ${esc(v)}`).join(' · ')}
      catch(_){props=esc(e.props||'')}
      return `<tr><td class="muted" style="white-space:nowrap" data-l="Time">${dstr(e.ts)}</td>
        <td class="rt-main" data-l="Event">${esc(e.name)}</td>
        <td data-l="Trader">${e.trader_id?`<a href="#" onclick="openTraderJournal(${e.trader_id},'${esc(e.email||'')}');return false">${esc(e.email||('#'+e.trader_id))}</a>`:'<span class="muted">—</span>'}</td>
        <td class="muted" style="font-size:11.5px" data-l="Details">${props||'—'}</td></tr>`}).join('')}
    </tbody></table></div>`;
}
async function openTelemetryDetail(day,name){
  try{const d=await api(`/api/admin/telemetry/events?day=${encodeURIComponent(day)}&name=${encodeURIComponent(name)}`);
    const items=d.items||[];
    openOver(`${name} · ${day}`,items.length?telemetryRows(items)
      :'<div class="empty"><h3>No events</h3></div>')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function openTicket(id){
  const t=await api('/api/admin/tickets/'+id);
  openOver(`#${t.id} · ${t.subject}`,`
    <div class="chip-row">
      <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${esc(t.status)}</span>
      <span class="chip">${esc(t.trader_email||'—')}</span>
      <span class="chip">opened ${dstr(t.created_at)}</span>
    </div>
    <div class="thread">${t.thread.map(m=>`
      <div class="msg ${m.author==='admin'?'trader':'admin'}">
        <div class="who">${m.author==='admin'?'You (support)':'Trader'} · ${dstr(m.ts)}</div>${esc(m.body)}</div>`).join('')}</div>
    ${t.status!=='closed'?`
      <textarea id="tk-reply" class="inp" rows="4" placeholder="Write a reply…"></textarea>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn-p" onclick="replyTicket(${t.id},false)">Send reply</button>
        <button class="btn-o" onclick="replyTicket(${t.id},true)">Reply &amp; close</button>
      </div>`:'<p class="muted" style="font-size:12.5px">This ticket is closed.</p>'}`);
}
async function replyTicket(id,close){
  const el=$('tk-reply');const msg=el?el.value.trim():'';
  if(!msg&&!close){toast('Write a reply first.','err');return}
  try{await api(`/api/admin/tickets/${id}/reply`,{method:'POST',body:JSON.stringify({message:msg,close:!!close})});
    closeOver();toast(close?'Ticket closed.':'Reply sent. Trader notified by e-mail.','ok');go('tickets');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function addPool(){
  const body={platform_login:$('pl-login').value.trim(),platform_password:$('pl-pass').value.trim(),
    platform_server:$('pl-server').value.trim(),
    account_size:parseFloat($('pl-size').value||'0')};
  if(!body.platform_login||!body.platform_password||!body.platform_server||!body.account_size){
    toast('Login, password, server and account size are all required.','err');return}
  try{await api('/api/admin/pool',{method:'POST',body:JSON.stringify(body)});toast('Added to pool.','ok');go('pool')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function genSim(){
  const size=parseFloat($('sim-size').value||'0'), cnt=parseInt($('sim-count').value||'1',10);
  try{const r=await api('/api/admin/pool/generate-simulated',{method:'POST',
      body:JSON.stringify({account_size:size,count:cnt})});
    toast(`${r.created.length} simulated account${r.created.length===1?'':'s'} added to the pool.`,'ok');
    go('pool');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function genReal(){
  const size=parseFloat($('real-size').value||'0'), cnt=parseInt($('real-count').value||'1',10);
  if(cnt>2){toast('Open at most 2 at a time — each takes ~40s and the server cuts off at 60s.','err',8000);return}
  toast(`Opening ${cnt} real MT5 demo${cnt===1?'':'s'} via Browserless — usually ~40s each…`,'ok',12000);
  try{const r=await api('/api/admin/pool/generate',{method:'POST',
      body:JSON.stringify({account_size:size,count:cnt}), timeoutMs:58000});
    const n=(r.created||[]).length;
    toast(`${n} real MT5 account${n===1?'':'s'} added to the pool.`
      +(r.errors&&r.errors.length?` (${r.errors.length} failed)`:''),'ok',8000);
    go('pool');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function provisionReal(accountId){
  if(!await askConfirm({title:'Open a real MT5 demo for this trader?',
    body:'Opens a MetaQuotes-Demo account via web.metatrader.app using the trader\'s name and email, then activates the challenge with those credentials. Takes ~30–45 seconds.',
    ok:'Open real MT5'}))return;
  toast('Opening real MT5 demo — usually ~40s…','ok',12000);
  try{const r=await api('/api/admin/accounts/'+accountId+'/provision-real',{method:'POST', timeoutMs:58000});
    toast(`Real MT5 ready: ${r.platform_login}@${r.platform_server}`,'ok',8000);
    go('pool');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function setRealFallback(on){
  try{await api('/api/admin/pool/real-fallback',{method:'POST',body:JSON.stringify({enabled:on})});
    toast(on?'Auto-provisioning of real web MT5 demos is ON.':'Real auto-provisioning turned off.','ok');
  }catch(e){toast('Error: '+e.message,'err');go('pool')}
}
/* ---------- Payout BOT ---------- */
/* ---------- Reach BOT: zasieg pod postami kanalu ----------
   Karta stoi obok Payout BOT-a, bo to jego przedluzenie: kazdy opublikowany
   certyfikat dostaje reakcje i wyswietlenia od razu po wyjsciu na kanal.
   Adres i klucz dostawcy siedza w env (REACH_API_URL / REACH_API_KEY) —
   panel steruje tylko tym, co i ile. */
function reachCardHtml(rc){
  if(!rc)return'';
  const b=rc.balance||{};
  const saldo=b.error?`<span class="chip" style="border-color:var(--red-line);color:var(--red)">balance <b>${esc(b.error)}</b></span>`
    :`<span class="chip">balance <b>$${fmt(b.value)}</b></span>
      <span class="chip"${b.low?' style="border-color:var(--red-line);color:var(--red)"':''}>about <b>${b.posts_left}</b> more posts</span>
      <span class="chip">${rc.cost_from==='provider'?'':'~'}<b>$${(rc.unit_cost||0).toFixed(3)}</b> per post</span>`;
  return `<div class="sec-card" style="max-width:560px"><h3>Reach BOT</h3>
    <div class="chip-row" style="margin-bottom:12px">
      <span class="status ${rc.enabled?'funded':'pending'}"><span class="dot"></span>${rc.enabled?'running':'off'}</span>
      ${rc.provider_ready?saldo:''}
      ${rc.last_result?`<span class="chip">last <b>${esc(rc.last_result)}</b></span>`:''}
    </div>
    ${!rc.provider_ready?`<div class="warn-box" style="margin:0 0 12px">
      <div><b>Provider not configured.</b> Set <span class="mono">REACH_API_URL</span> and
      <span class="mono">REACH_API_KEY</span> in the environment. Posts still go out, they just
      get no extra reach.</div></div>`:''}
    <div class="pool-form">
      <div><label class="muted" style="font-size:12px">Reactions per post</label>
        <input id="rc-qr" class="inp" type="number" min="0" step="1" value="${rc.qty_reactions}"></div>
      <div><label class="muted" style="font-size:12px">Views per post</label>
        <input id="rc-qv" class="inp" type="number" min="0" step="1" value="${rc.qty_views}"></div>
      <div><label class="muted" style="font-size:12px">Warn below ($)</label>
        <input id="rc-min" class="inp" type="number" min="0" step="0.5" value="${rc.min_balance}"></div>
    </div>
    <details style="margin:2px 0 12px">
      <summary class="muted" style="font-size:12px;cursor:pointer">Provider services</summary>
      <div class="pool-form" style="margin-top:8px">
        <div><label class="muted" style="font-size:12px">Reactions service id</label>
          <input id="rc-sr" class="inp" type="number" min="1" step="1" value="${rc.svc_reactions}"></div>
        <div><label class="muted" style="font-size:12px">Views service id</label>
          <input id="rc-sv" class="inp" type="number" min="1" step="1" value="${rc.svc_views}"></div>
      </div>
      <p class="muted" style="font-size:11.5px;margin:6px 0 0">Which product to order at the
        provider. Only worth touching if a service is retired or you want a different one —
        the price per post is read from the provider's own price list.</p>
    </details>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn-p" onclick="saveReach(this)">Save settings</button>
      <button class="btn-o" onclick="toggleReach(${rc.enabled?'false':'true'},this)">${rc.enabled?'Turn off':'Turn on'}</button>
    </div>
    <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)">
      <label class="muted" style="font-size:12px">Boost a single post</label>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px">
        <input id="rc-link" class="inp" style="flex:1;min-width:220px" placeholder="https://t.me/channel/123">
        <button class="btn-o" onclick="boostReach(this)">Boost</button>
      </div>
    </div>
    <p class="muted" style="font-size:12px;margin-top:10px;line-height:1.55">
      Every post the Payout BOT publishes gets its reactions and views <b>right after it goes
      out</b> — that is what the switch controls. Paste a link above to order for anything else
      on the channel; that one works even while the bot is off. The balance
      alert lands in the bell and on your phone once a day while the account is below the
      threshold, and orders stop automatically when there is not enough left for one post.</p></div>`;
}
async function saveReach(btn){
  const body={
    qty_reactions:parseInt($('rc-qr').value,10), qty_views:parseInt($('rc-qv').value,10),
    svc_reactions:parseInt($('rc-sr').value,10), svc_views:parseInt($('rc-sv').value,10),
    min_balance:parseFloat($('rc-min').value),
  };
  if(Object.values(body).some(v=>isNaN(v))){toast('Fill every field with a number.','err');return}
  return busy(btn,'Saving…',async()=>{
    try{await api('/api/admin/reach',{method:'POST',body:JSON.stringify(body)});
      toast('Reach BOT settings saved.','ok'); go('settings');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function toggleReach(on,btn){
  return busy(btn,'…',async()=>{
    try{await api('/api/admin/reach',{method:'POST',body:JSON.stringify({enabled:on})});
      toast(on?'Reach BOT is on. Every published post gets its reactions and views.'
              :'Reach BOT is off. Posts go out with no extra reach.','ok');
      go('settings');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}
async function boostReach(btn){
  const link=($('rc-link').value||'').trim();
  if(!link){toast('Paste a post link first.','err');return}
  return busy(btn,'Ordering…',async()=>{
    try{const r=await api('/api/admin/reach/boost',{method:'POST',body:JSON.stringify({link})});
      toast(`Ordered for ${r.ordered}/2 services.`+(r.balance!=null?` Balance $${fmt(r.balance)}.`:''),'ok',7000);
      go('settings');
    }catch(e){toast('Error: '+e.message,'err')}
  });
}

async function savePayoutBot(){
  const body={
    win_from:parseInt($('pb-from').value,10),
    win_to:parseInt($('pb-to').value,10),
    lp_pct:parseFloat($('pb-lp').value),
    gross_min_pct:parseFloat($('pb-min').value),
    gross_max_pct:parseFloat($('pb-max').value),
    sizes:($('pb-sizes').value||'').split(',').map(x=>parseFloat(x.trim())).filter(x=>x>0),
  };
  if(Object.values(body).some(v=>typeof v==='number'&&isNaN(v))){
    toast('Fill every field with a number.','err');return}
  try{await api('/api/admin/payout-engine',{method:'POST',body:JSON.stringify(body)});
    toast('Payout BOT settings saved.','ok'); go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function togglePayoutBot(on){
  try{await api('/api/admin/payout-engine',{method:'POST',body:JSON.stringify({enabled:on})});
    toast(on?'Payout BOT is on. It posts once a day at a random minute inside your window.'
            :'Payout BOT is off. No new payouts are generated.','ok');
    go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function runPayoutBot(){
  /* Ten przebieg ZASTEPUJE dzisiejszy automatyczny i od razu publikuje na kanale,
     wiec admin musi wiedziec, na co klika — stad dialog, a nie samo klikniecie. */
  if(!await askConfirm({title:'Run the Payout BOT now?',
    body:'It creates one payout dated today, issues its public certificate and posts it to the '
      +'Telegram channel straight away. <b>This replaces today\'s scheduled run.</b>',
    ok:'Run it now'}))return;
  try{const r=await api('/api/admin/payout-engine/run',{method:'POST'});
    if(!r.created){toast('Nothing was created: '+(r.skipped||r.error||'unknown reason'),'err',8000)}
    else toast(`Payout created: ${r.trader} · $${fmt(r.amount_usd)}`
      +(r.posted?(r.photo?' · posted with the certificate image.':' · posted as text only.')
                :' · not posted: '+(r.reason||'channel off')),'ok',9000);
    go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}

async function setBogoPromo(on){
  try{await api('/api/admin/bogo-promo',{method:'POST',body:JSON.stringify({enabled:on})});
    toast(on?'Buy 1 Get 1 Free is ON — the site shows the promo bar and every new paid order gets a free second account.'
            :'Buy 1 Get 1 Free is off. Orders created while it was on keep their free account.','ok',8000);
    go('settings');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function setSimFallback(on){
  try{await api('/api/admin/pool/sim-fallback',{method:'POST',body:JSON.stringify({enabled:on})});
    toast(on?'Auto-provisioning of simulated credentials is ON.':'Auto-provisioning turned off.','ok');
  }catch(e){toast('Error: '+e.message,'err');go('pool')}
}
function editPool(id){
  /* '' zamiast 'table-row': na telefonie wiersz edycji jest kartą (display:flex
     z .rtbl) i wpisany na sztywno table-row rozjechałby układ. */
  const w=$('pool-edit-'+id); w.style.display=(w.style.display==='none'?'':'none');
}
async function savePool(id,claimed){
  const body={platform_login:$('ed-login-'+id).value.trim(),
              platform_server:$('ed-server-'+id).value.trim()};
  const newPass=$('ed-pass-'+id).value.trim();
  if(newPass)body.platform_password=newPass;
  if(!claimed){const s=parseFloat($('ed-size-'+id).value||'0'); if(s>0)body.account_size=s}
  if(!body.platform_login||!body.platform_server){toast('Login and server cannot be empty.','err');return}
  try{
    const r=await api('/api/admin/pool/'+id,{method:'PATCH',body:JSON.stringify(body)});
    toast(r.propagated_to_account?`Saved. Credentials also updated on account #${r.propagated_to_account}.`
                                 :'Pool entry saved.','ok');
    go('pool');
  }catch(e){toast('Error: '+e.message,'err')}
}
function delPool(id,login,retired){
  /* Wycofany wpis to jedyny slad, ze ten login u brokera juz komus wyszedl —
     pytanie musi to powiedziec wprost, bo po skasowaniu nic nie powstrzyma
     wpisania go do puli po raz drugi. */
  xdel(`/api/admin/pool/${id}`,
    retired
      ? `Delete the retired entry ${login}?\n\nThe trader account behind it is gone, so nothing `
        +`breaks. You do lose the record that this login was already handed out — nothing will `
        +`stop it from being added to the pool again. This cannot be undone.`
      : `Remove ${login} from the pool?\n\nAn account currently assigned to a trader stays. `
        +`This cannot be undone.`,
    ()=>go('pool'),retired?'Retired entry deleted.':'Removed from the pool.');
}

/* ---------- modal: grant a challenge ---------- */
const GRANT_REASONS=['BOGO promotion','Free upgrade','Compensation','Partner deal','Contest prize','Marketing campaign'];

const grantOpt=(t,chosen)=>`<option value="${t.id}"${chosen===t.id?' selected':''}>`
  +`${esc(t.email)}${t.full_name?' — '+esc(t.full_name):''} (${t.accounts} acc.`
  +`${t.referred_count?` · ${t.referred_count} referred`:''})</option>`;

function grantFilter(){
  const all=window._grantTraders||[],q=($('g-search').value||'').trim().toLowerCase();
  const sel=$('g-trader'),chosen=parseInt(sel.value);
  const hit=q?all.filter(t=>(t.email+' '+(t.full_name||'')).toLowerCase().includes(q)):all;
  sel.innerHTML=hit.map(t=>grantOpt(t,chosen)).join('');
  // Filtering can drop the selected trader out of the list; without this the
  // select ends up with nothing chosen and the grant would post NaN as the id.
  if(sel.selectedIndex<0&&hit.length)sel.selectedIndex=0;
  $('g-count').textContent=`· ${hit.length} of ${all.length}`;
  grantPick();
}

/* Safari paints the selection in an unfocused listbox a barely visible grey,
   so tapping a trader looks like it did nothing. Spell out who gets the grant. */
function grantPick(){
  const t=$('g-trader').selectedOptions[0];
  $('g-picked').innerHTML=t?`Granting to <b>${esc(t.textContent.split(' (')[0])}</b>`
                          :'<b>No trader selected</b>';
}

async function openGrant(traderId){
  let products=[],traders=[];
  try{[products,traders]=await Promise.all([
    (await fetch('/api/products')).json(), api('/api/admin/traders')])}catch(e){toast('Error: '+e.message,'err');return}
  if(!traders.length){toast('No registered traders yet.','err');return}
  window._grantTraders=traders;
  /* Reopening stacks a second #grant-modal, and submitGrant() reads the FIRST
     match by id — the hidden one, still on its default trader. */
  document.getElementById('grant-modal')?.remove();
  const box=document.createElement('div');
  box.id='grant-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Grant a challenge</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('grant-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Creates a real MT5 account for the trader without payment and e-mails them the credentials. The account behaves exactly like a purchased one.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px">Trader <span class="muted" id="g-count" style="opacity:.65"></span></label>
        <input id="g-search" class="inp" style="margin-bottom:7px" placeholder="Search by e-mail or name" oninput="grantFilter()">
        <select id="g-trader" class="inp" size="6" onchange="grantPick()">${traders.map(t=>grantOpt(t,traderId)).join('')}</select>
        <div class="muted" id="g-picked" style="font-size:12.5px;margin-top:6px"></div></div>
      <div><label class="muted" style="font-size:12px">Challenge</label>
        <select id="g-product" class="inp">${products.map(p=>
          `<option value="${esc(p.key)}">${esc(p.label)} — $${fmt0(p.account_size)} · normally $${fmt0(p.price_usd)}</option>`).join('')}</select></div>
      <div><label class="muted" style="font-size:12px">Customer paid for <span style="opacity:.65">(optional, BOGO upgrade)</span></label>
        <select id="g-paid" class="inp">
          <option value="">— not a paid upgrade —</option>
          ${products.map(p=>`<option value="${esc(p.key)}">${esc(p.label)} — $${fmt0(p.account_size)}</option>`).join('')}</select></div>
      <div><label class="muted" style="font-size:12px">Reason (shown to the trader)</label>
        <select id="g-reason" class="inp" onchange="document.getElementById('g-note').value=this.value">
          ${GRANT_REASONS.map(r=>`<option>${r}</option>`).join('')}</select></div>
      <input id="g-note" class="inp" value="${GRANT_REASONS[0]}" placeholder="Label on the e-mail badge">
      <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="g-funded" style="width:16px;height:16px;accent-color:var(--acc)">
        Start as <b>funded</b>, skipping the evaluation entirely</label>
      <button class="btn-p lg" style="width:100%" id="g-go" onclick="submitGrant()">Grant &amp; create account</button>
      <p class="hint">Trader gets an e-mail with the allocation and MT5 credentials.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  grantFilter();
  // The list scrolls, so a preselected trader can sit off-screen and look as
  // if the modal never picked them up.
  $('g-trader').selectedOptions[0]?.scrollIntoView({block:'nearest'});
}
async function submitGrant(){
  const wybrany=parseInt($('g-trader').value);
  if(!wybrany){toast('Pick a trader first.','err');return}
  const body={trader_id:wybrany,product_key:$('g-product').value,
    note:($('g-note').value||'').trim()||null,
    bogo_paid_key:$('g-paid').value||null,
    funded:$('g-funded').checked};
  /* Drugi tap = drugie konto z mailem do tradera — blokada na czas requestu. */
  const btn=$('g-go');btn.disabled=true;
  try{
    const r=await api('/api/admin/grant',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('grant-modal')?.remove();
    toast(`🎁 Granted ${r.product_key} to ${r.trader_email}.\n${r.status==='provisioning'
      ?'MT5 account is being created. Credentials e-mailed within a minute.'
      :'Credentials e-mailed to the trader.'}`,'ok',9000);
    go('accounts');
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false}
}

/* ---------- one delete control for the whole panel ----------
   Every table deletes through the same small red X, so "Delete" vs "Remove"
   never means two different things again. Undo-style actions (Revert on a KYC
   decision, Revoke on a certificate) keep their own labelled buttons — they are
   reversible and must not read as destruction. */
const XBTN=(call,tip)=>`<button class="btn-x" title="${esc(tip)}" aria-label="${esc(tip)}" onclick="${call}">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
    stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>`;

/* Jedno okno dla wszystkich piatki przyciskow X. Wolajacy podaja tekst w tym
   samym ksztalcie co dawniej ("pytanie\n\nskutki"), wiec pierwszy akapit staje
   sie tytulem, reszta trescia — zaden call site nie wymagal zmiany.
   esc() jest tu OBOWIAZKOWE: te teksty niosa login, e-mail i nazwisko klienta,
   ktore w natywnym confirm() byly zwyklym tekstem, a tutaj trafiaja do HTML-a. */
async function xdel(url,question,after,okMsg){
  /* Wiersz ustalamy PRZED oknem potwierdzenia. Szukamy go po ostatnio klknietym
     przycisku (bo `xdel` wolaja inline'owe onclicki), a klikniecie "Delete"
     w oknie jest KOLEJNYM klknieciem w <button> — po nim `_ostatniPrzycisk`
     wskazuje juz przycisk modala i `closest` nie znajduje niczego. Wiersz nie
     znikal wiec wcale: zostawal na ekranie az do przeladowania widoku, a odliczanie
     do cofniecia sugerowalo, ze cos sie stalo. Dotyczylo to wszystkich piatki X-ow.
     `.ticket-row` obok `tr`, bo zgloszenia nie sa tabela, tylko kaflami.
     `_row` maja klony przyciskow w arkuszu long-press — klon zyje poza tabela,
     wiec closest() nie znalazlby wiersza do schowania. */
  const wiersz=_ostatniPrzycisk&&(_ostatniPrzycisk._row||_ostatniPrzycisk.closest('tr, .ticket-row'));
  const [tytul,...reszta]=String(question).split('\n\n');
  if(!await askConfirm({title:tytul.trim(),
    body:esc(reszta.join('\n').trim()).replace(/\n/g,'<br>'),
    ok:'Delete',danger:true}))return false;
  withUndo(tytul.trim().replace(/\?+$/,''),async()=>{
    try{const r=await api(url,{method:'DELETE',keepalive:true});
      toast(typeof okMsg==='function'?okMsg(r):(okMsg||'Deleted.'),'ok');
      if(typeof after==='function')after(r);
    }catch(e){
      toast('Error: '+e.message,'err');
      if(wiersz&&wiersz.style)wiersz.style.display='';   // nie udalo sie — wroc
    }
  },wiersz);
  /* Prawda/falsz mowi wolajacemu, czy usuwanie POSZLO do kolejki — slide-over
     konta zamyka sie tylko wtedy, nie przy „Cancel". */
  return true;
}

/* Removes the whole row from the ledger — a mistyped payout, a duplicate, an
   import to undo. The account balance is NOT rewound: a payout booked with a
   balance reset took the profit off the account days ago and replaying that on
   a live account would overwrite its current equity. */
function deletePayoutRow(kind,id,amount,login){
  const co=kind==='payout'?`the $${fmt(amount)} payout on account ${login}`:'this payout request';
  xdel(kind==='payout'?`/api/admin/payouts/${id}`:`/api/admin/payout-requests/${id}`,
    `Delete ${co}?\n\nThe record disappears from the ledger and from every total. `
    +`The account balance stays as it is now. This cannot be undone.`,
    ()=>VIEWS.payouts(),
    r=>r.had_certificate?'Deleted. Its certificate is gone from the landing page too.':'Deleted.');
}

function deleteAccountRow(id,login,trader){
  return xdel(`/api/accounts/${id}`,
    `Delete account ${login}${trader?` (${trader})`:''}?\n\nIts trades, snapshots, payouts and `
    +`certificates go with it. This cannot be undone.`,
    ()=>go('accounts'));
}

function deleteOrderRow(id,email,amount,accId){
  xdel(`/api/admin/orders/${id}`,
    `Delete order #${id} — $${fmt(amount)}${email?` from ${email}`:''}?\n\n`
    +(accId?`Account ${accId} created from it STAYS.\n`:'')
    +`A paid order also leaves the revenue figure in Overview. This cannot be undone.`,
    ()=>go('orders'));
}

/* Temat bierzemy z ostatnio pobranej listy, a nie z atrybutu HTML — patrz komentarz
   przy `row` w widoku Tickets. Gdy go nie ma, pytanie i tak jest jednoznaczne
   dzieki numerowi zgloszenia. */
function delTicket(id){
  const t=(window._tickets||[]).find(x=>x.id===id);
  xdel(`/api/admin/tickets/${id}`,
    `Delete ticket #${id}${t?` — ${t.subject}`:''}?\n\n`
    +`The whole conversation${t&&t.messages?` (${t.messages} message${t.messages>1?'s':''})`:''} `
    +`goes with it and the trader loses it from their portal too. Closing a ticket only ends it; `
    +`this erases it. This cannot be undone.`,
    ()=>go('tickets'),'Ticket deleted.');
}

function deleteKycRow(tid,email){
  xdel(`/api/admin/kyc/${tid}`,
    `Delete the KYC record for ${email}?\n\nThe submitted data and the uploaded ID scans are `
    +`erased from the server, and the trader can start verification from scratch. `
    +`To only undo your decision, use Revert instead.`,
    ()=>go('kyc'),
    r=>r.files_removed?`Deleted. ${r.files_removed} file(s) erased from the server.`:'Deleted.');
}

/* ---------- modal: import historical payouts ----------
   For payouts settled before this panel existed (bank transfer, spreadsheet,
   notebook). They land as internal records so the totals stop lying; the public
   certificate is a separate, deliberate click per payout. */
const PI_HEAD='full_name,amount_usd,date,account_size,program,email,note,kyc';
function openPayoutImport(){
  const box=document.createElement('div');
  box.id='payimp-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()" style="max-width:760px">
    <div class="modal-head"><h3>Import historical payouts</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('payimp-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Paste one payout per line, CSV,
      starting with the header row. Missing traders and funded accounts are created for you.
      <code>program</code> is <code>2step</code> or <code>instant</code>, <code>kyc</code> is
      <code>none</code> (default), <code>pending</code>, <code>approved</code> or
      <code>rejected</code>.<br>
      <b>No public certificates are issued</b> — the entries stay internal until you press
      Generate on a payout you can back with a transfer confirmation.</p>
    <div class="stack">
      <textarea id="pi-csv" class="inp" rows="9" spellcheck="false"
        style="font-family:var(--mono,ui-monospace,monospace);font-size:12px;line-height:1.6"
        placeholder="${PI_HEAD}&#10;Jane Example,2480,2026-07-03,50000,2step,jane@example.com,,approved">${PI_HEAD}
</textarea>
      <div id="pi-out"></div>
      <div style="display:flex;gap:8px">
        <button class="btn-o lg" style="flex:1" onclick="payoutImport(false)">Preview</button>
        <button class="btn-p lg" style="flex:1" onclick="payoutImport(true)">Import</button>
      </div>
      <p class="hint">Preview changes nothing. Re-importing the same file is safe, because the same
        person, amount and day is never added twice.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
}
async function payoutImport(commit){
  const csv=($('pi-csv').value||'').trim();
  if(!csv){toast('Paste the CSV first.','err');return}
  const out=$('pi-out');
  out.innerHTML='<p class="muted" style="font-size:12.5px">Working…</p>';
  let r;
  try{r=await api('/api/admin/payouts/import',{method:'POST',body:JSON.stringify({csv,commit})})}
  catch(e){out.innerHTML=`<p class="muted" style="font-size:12.5px;color:var(--red)">Error: ${esc(e.message)}</p>`;return}
  if(!r.ok){
    out.innerHTML=`<div class="sec-card" style="padding:12px"><b style="font-size:12.5px">Fix these lines first</b>
      ${r.errors.map(x=>`<div class="muted" style="font-size:12px;margin-top:4px">${esc(x)}</div>`).join('')}</div>`;
    return;
  }
  out.innerHTML=`<div class="tbl-wrap" style="max-height:260px;overflow:auto"><table class="tbl">
    <thead><tr><th>Name</th><th>Amount</th><th>Date</th><th>Account</th><th>Status</th></tr></thead>
    <tbody>${r.rows.map(w=>`<tr>
      <td>${esc(w.full_name)}</td><td class="num up">$${fmt(w.amount_usd)}</td>
      <td class="muted">${esc(w.date)}</td>
      <td class="muted">${esc(w.program)} $${fmt0(w.account_size)}</td>
      <td>${w.duplicate?'<span class="muted">already in the database</span>'
        :commit?'<span class="up">imported</span>':'<span class="muted">will be added</span>'}</td>
    </tr>`).join('')}</tbody></table></div>`;
  if(commit){
    toast(`✅ ${r.added} payout(s) imported${r.skipped?`, ${r.skipped} skipped as duplicates`:''}. `
      +'They are internal records. No certificates were issued.','ok',9000);
    VIEWS.payouts();
  }
}

/* ---------- modal: order paid outside Stripe (card link, crypto, transfer) ----------
   Opens two ways. From Orders you pick an existing customer. From a lead card you
   pass `lead` ({id,email,name}) and there is nobody to pick — the lead has no
   account and will not open one just to receive a payment link. The backend
   creates it from the e-mail, which is also the only way this ever shows up as
   "Bought": that column is derived from traders by e-mail, not stored. */
async function openManualOrder(traderId,lead){
  let products=[],traders=[],pct=0;
  /* Rabat partnerski jest miły, ale nie jest warunkiem sprzedaży: gdyby jego
     odczyt wywrócił okno, admin nie wystawiłby ŻADNEGO zamówienia przez zniżkę,
     której akurat nie ma. Brak odpowiedzi = zero = okno bez tej opcji. */
  let bogoOn=false;
  try{[products,traders,pct,bogoOn]=await Promise.all([
    (await fetch('/api/products')).json(),lead?[]:api('/api/admin/traders'),
    api('/api/admin/partner-terms').then(d=>d.discount_pct||0).catch(()=>0),
    /* Pre-fill checkboxa BOGO stanem globalnej promocji — admin widzi domyślną
       decyzję i może ją nadpisać dla tego jednego zamówienia. */
    api('/api/admin/bogo-promo').then(d=>!!d.enabled).catch(()=>false)])}
  catch(e){toast('Error: '+e.message,'err');return}
  if(!lead&&!traders.length){toast('No registered traders yet.','err');return}
  window._moTraders=traders;window._moLead=lead||null;window._moPartnerPct=pct;
  document.getElementById('order-modal')?.remove();
  const box=document.createElement('div');
  box.id='order-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>New order</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('order-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px" id="mo-lead">For a customer paying outside Stripe — crypto or a transfer.
      The order lands as <b>unpaid</b>; the account is created only when you hit <b>Mark paid</b>, exactly like a card payment.</p>
    <div class="stack">
      <div class="seg" id="mo-method" style="width:100%">
        <button type="button" class="on" onclick="moMethod('crypto')">Crypto / transfer</button>
        <button type="button" onclick="moMethod('link')">Card payment link</button>
      </div>
      ${lead?`<div><label class="muted" style="font-size:12px">Customer</label>
        <div class="inp" style="display:flex;flex-direction:column;gap:2px">
          <b>${esc(lead.name||lead.email)}</b>
          <span class="muted" style="font-size:12px">${esc(lead.email)}</span></div></div>`
      :`<div><label class="muted" style="font-size:12px">Customer</label>
        <input id="mo-search" class="inp" type="search" autocapitalize="off" style="margin-bottom:7px" placeholder="Search by e-mail or name" oninput="moFilter()">
        <select id="mo-trader" class="inp" size="5"></select></div>`}
      <div><label class="muted" style="font-size:12px">Challenge</label>
        <select id="mo-product" class="inp" onchange="moPrice()">${products.map(p=>
          `<option value="${esc(p.key)}" data-price="${p.price_usd}">${esc(p.label)} — $${fmt0(p.account_size)} · $${fmt(p.price_usd)}</option>`).join('')}</select></div>
      <div><label class="muted" style="font-size:12px">Amount to collect (USD)</label>
        <input id="mo-amount" class="inp" type="number" inputmode="decimal" step="0.01" min="0"></div>
      ${pct?`<label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="mo-partner"${lead?' checked':''} onchange="moPrice()" style="width:16px;height:16px;accent-color:var(--acc)">
        <span>Partner price <b>−${pct}%</b>
          <span class="muted">— the rate agreed for customers the partner brings in</span></span></label>`:''}
      <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="mo-bogo"${bogoOn?' checked':''} style="width:16px;height:16px;accent-color:var(--acc)">
        <span><b>Buy 1 Get 1 Free</b>
          <span class="muted">— a second account of the same size is created automatically once this order is paid</span></span></label>
      <div class="stack" id="mo-crypto">
        <div><label class="muted" style="font-size:12px">Network</label>
          <input id="mo-network" class="inp" placeholder="e.g. USDT · TRC20" value="${esc(lastWallet().network||'')}"></div>
        <div><label class="muted" style="font-size:12px">Wallet address <span style="opacity:.65">(goes into the e-mail)</span></label>
          <input id="mo-address" class="inp" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="Paste the wallet address" value="${esc(lastWallet().address||'')}"></div>
        <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="mo-awaiting" checked style="width:16px;height:16px;accent-color:var(--acc)">
          Mark as <b>awaiting crypto payment</b></label>
        <label style="display:flex;align-items:center;gap:9px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="mo-mail" checked style="width:16px;height:16px;accent-color:var(--acc)">
          E-mail the customer the payment instructions</label>
      </div>
      <button class="btn-p lg" style="width:100%" id="mo-go" onclick="submitManualOrder()">Create order</button>
      <p class="hint">The amount is exactly what you type — coupons and store credits are not applied,
        and nothing leaves the customer's balance until the payment is confirmed.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  /* A lead is always here to be sent a link, so start there. */
  moMethod(lead?'link':(window._moMethodPref||'crypto'));
  if(!lead)moFilter(traderId);
  moPrice();
}
/* Jak klient płaci. Link kartą nie ma nic wspólnego z portfelem, więc pola
   crypto znikają — inaczej admin wysyłałby maila z adresem USDT komuś, kogo
   właśnie kieruje do kasy Stripe'a.

   Dwie różne rzeczy trzymamy celowo osobno: `_moMethod` to stan TEGO okna i
   z niego czyta `submitManualOrder`, a `_moMethodPref` to zapamiętany domyślny
   wybór zakładki Orders. Lead zawsze startuje na linku i nie ma prawa tego
   wyboru nadpisać następnym otwarciom. */
function moMethod(m){
  window._moMethod=m;
  if(!window._moLead)window._moMethodPref=m;
  const link=m==='link';
  $('mo-method').querySelectorAll('button').forEach((b,i)=>b.classList.toggle('on',(i===1)===link));
  $('mo-crypto').style.display=link?'none':'';
  $('mo-go').textContent=link?'Create order & copy payment link':'Create order';
  $('mo-lead').innerHTML=(link
    ?`The order lands as <b>unpaid</b> and you get a link to our payment page — send it to the customer
      (Telegram, chat, e-mail). They pay by card, the account is created automatically. The link never expires.`
    :`For a customer paying outside Stripe — crypto or a transfer.
      The order lands as <b>unpaid</b>; the account is created only when you hit <b>Mark paid</b>, exactly like a card payment.`)
    +(window._moLead?`<br><br>This lead has no account yet, so one is opened on their e-mail.
      They have no password for it — they get in through <b>“Forgot password?”</b>. Once the
      payment goes through, the lead shows up as <b>Bought</b> on its own.`:'');
}
function moFilter(preselect){
  const q=($('mo-search').value||'').toLowerCase();
  const all=window._moTraders||[];
  const hit=q?all.filter(t=>(t.email||'').toLowerCase().includes(q)
                          ||(t.full_name||'').toLowerCase().includes(q)):all;
  const sel=$('mo-trader');
  sel.innerHTML=hit.map(t=>`<option value="${t.id}"${preselect===t.id?' selected':''}>${esc(t.email)}${t.full_name?' — '+esc(t.full_name):''}</option>`).join('');
  // Szukajka potrafi wyrzucić zaznaczonego z listy — bez tego POST poszedłby
  // z NaN zamiast id klienta.
  if(sel.selectedIndex<0&&hit.length)sel.selectedIndex=0;
  sel.selectedOptions[0]?.scrollIntoView({block:'nearest'});
}
/* Cenę partnerską liczy panel, a nie serwer, bo admin ma ją zobaczyć ZANIM
   kliknie „utwórz" — kwota, która zmienia się po zatwierdzeniu, to przy
   pieniądzach zła niespodzianka. Serwer odnotowuje tylko, że rabat był. */
function moPrice(){
  const o=$('mo-product').selectedOptions[0];
  if(!o)return;
  const pct=$('mo-partner')?.checked?(window._moPartnerPct||0):0;
  $('mo-amount').value=(o.dataset.price*(1-pct/100)).toFixed(2);
}
async function submitManualOrder(){
  const lead=window._moLead;
  const tid=lead?0:parseInt($('mo-trader').value);
  if(!lead&&!tid){toast('Pick a customer first.','err');return}
  const amount=parseFloat($('mo-amount').value);
  if(!(amount>=0)){toast('Enter the amount to collect.','err');return}
  const link=window._moMethod==='link';
  const addr=link?'':($('mo-address').value||'').trim();
  const net=link?'':($('mo-network').value||'').trim();
  if(addr)saveWallet(addr,net);
  /* Na wolnej sieci drugi tap przed odpowiedzia zalozylby DRUGIE zamowienie
     (i konto leada) — blokada jak w submitNewLead. */
  const btn=$('mo-go');btn.disabled=true;
  try{
    const r=await api('/api/admin/orders',{method:'POST',body:JSON.stringify({
      ...(lead?{email:lead.email}:{trader_id:tid}),
      product_key:$('mo-product').value,amount_usd:amount,
      partner_discount:!!$('mo-partner')?.checked,
      bogo:!!$('mo-bogo')?.checked,
      flag:(!link&&$('mo-awaiting').checked)?'awaiting_crypto':'',
      payment_address:addr,payment_network:net,
      notify_trader:!link&&$('mo-mail').checked})});
    document.getElementById('order-modal')?.remove();
    // Nowe konto trzeba powiedzieć wprost: klient go nie zakładał i nie zna
    // hasła, więc dział musi wiedzieć, co odpowiedzieć na "nie mogę się zalogować".
    const konto=r.trader_created?' New account opened — they log in via “Forgot password?”.':'';
    if(link){await payLink(r.id,`Order #${r.id} for ${r.trader_email} — $${fmt(r.amount_usd)}.${konto}`)}
    else{const [txt,kind]=mailInfo(r);
      toast(`🧾 Order #${r.id} for ${r.trader_email} — $${fmt(r.amount_usd)}. ${txt}${konto}`,kind,9000)}
    // Z karty leada zostajemy przy leadzie: nowe zamówienie dopisuje się do
    // jego tabeli Orders, a skok do zakładki Orders gubiłby miejsce w robocie.
    if(lead){await openLead(lead.id);renderLeads()}
    else go('orders');
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false}
}

/* ---------- modal: store credits ---------- */
async function openCredits(traderId){
  let traders=[];
  try{traders=await api('/api/admin/traders')}catch(e){toast('Error: '+e.message,'err');return}
  if(!traders.length){toast('No registered traders yet.','err');return}
  document.getElementById('credits-modal')?.remove();
  const box=document.createElement('div');
  box.id='credits-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>Add store credits</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('credits-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Credits are a USD store balance:
      they reduce the price of the trader's next challenge automatically at checkout.
      Use a negative amount to correct a mistake.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px">Trader</label>
        <select id="cr-trader" class="inp" onchange="creditsBalance()">${traders.map(t=>
          `<option value="${t.id}" data-credits="${t.credits_usd||0}"${traderId===t.id?' selected':''}>${esc(t.email)}${t.full_name?' — '+esc(t.full_name):''}</option>`).join('')}</select></div>
      <div class="muted" id="cr-balance" style="font-size:12.5px"></div>
      <input id="cr-amount" class="inp" type="number" inputmode="numeric" step="1" placeholder="Amount in USD, e.g. 100">
      <input id="cr-note" class="inp" placeholder="Note for the ledger, e.g. Contest prize">
      <button class="btn-p lg" style="width:100%" id="cr-go" onclick="submitCredits()">Add credits</button>
      <p class="hint">The balance is spent automatically on the trader's next purchase.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
  creditsBalance();
}
function creditsBalance(){
  const sel=$('cr-trader'),el=$('cr-balance');
  const v=sel&&sel.selectedOptions[0]?parseFloat(sel.selectedOptions[0].dataset.credits||'0'):0;
  if(el)el.textContent='Current balance: $'+fmt(v);
}
async function submitCredits(){
  const amount=parseFloat($('cr-amount').value);
  if(!amount){toast('Enter a non-zero amount.','err');return}
  /* Drugi tap = saldo doliczone dwa razy — blokada na czas requestu. */
  const btn=$('cr-go');btn.disabled=true;
  try{
    const r=await api(`/api/admin/traders/${$('cr-trader').value}/credits`,{method:'POST',
      body:JSON.stringify({amount,note:($('cr-note').value||'').trim()||null})});
    document.getElementById('credits-modal')?.remove();
    toast(`💳 ${r.email} now has $${fmt(r.credits_usd)} in store credits.`,'ok',7000);
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false}
}

/* ---------- modal: new account ---------- */
async function openCreate(){
  let products=[];
  try{products=await (await fetch('/api/products')).json()}catch(e){}
  document.getElementById('create-modal')?.remove();
  const box=document.createElement('div');
  box.id='create-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>New challenge account</h3>
      <button class="icon-btn" aria-label="Close" onclick="document.getElementById('create-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Everything typed by hand, from an MT5 account outside the pool — nothing is taken from MT5 Pool and its state stays untouched. Drawdown type comes from the plan you pick.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px" for="c-login">MT5 login</label>
        <input id="c-login" class="inp" inputmode="numeric" autocomplete="off" placeholder="e.g. 100099"></div>
      <div><label class="muted" style="font-size:12px" for="c-pass">MT5 password</label>
        <input id="c-pass" class="inp" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="As set at the broker"></div>
      <div><label class="muted" style="font-size:12px" for="c-server">MT5 server</label>
        <input id="c-server" class="inp" spellcheck="false" autocapitalize="off" placeholder="e.g. MetaQuotes-Demo"></div>
      <div><label class="muted" style="font-size:12px" for="c-name">Trader name</label>
        <input id="c-name" class="inp" autocapitalize="words" placeholder="Shown in the portal and e-mails"></div>
      <div><label class="muted" style="font-size:12px" for="c-email">Trader e-mail <span style="opacity:.65">(links the account to their portal)</span></label>
        <input id="c-email" class="inp" type="email" inputmode="email" autocapitalize="off" spellcheck="false" placeholder="trader@example.com"></div>
      <div><label class="muted" style="font-size:12px">Challenge</label>
        <select id="c-product" class="inp">${products.map(p=>
          `<option value="${esc(p.key)}">${esc(p.label)} — $${fmt0(p.account_size)} · normally $${fmt0(p.price_usd)}</option>`).join('')}</select></div>
      <div><label class="muted" style="font-size:12px">Promotion <span style="opacity:.65">(optional, shown to the trader)</span></label>
        <select id="c-reason" class="inp" onchange="document.getElementById('c-note').value=this.value">
          <option value="">— no promotion —</option>
          ${GRANT_REASONS.map(r=>`<option>${r}</option>`).join('')}</select></div>
      <input id="c-note" class="inp" placeholder="Label on the e-mail badge">
      <div><label class="muted" style="font-size:12px">Customer paid for <span style="opacity:.65">(optional, BOGO upgrade)</span></label>
        <select id="c-paid" class="inp">
          <option value="">— not a paid upgrade —</option>
          ${products.map(p=>`<option value="${esc(p.key)}">${esc(p.label)} — $${fmt0(p.account_size)}</option>`).join('')}</select></div>
      <button class="btn-p lg" style="width:100%" id="c-go" onclick="submitCreate()">Create account</button>
      <p class="hint">If the e-mail matches a registered trader, the account shows up in their portal and they get the credentials by e-mail.</p>
    </div></div>`;
  box.onclick=()=>box.remove();
  document.body.appendChild(box);
}
async function submitCreate(){
  const body={login:$('c-login').value.trim(),
    platform_password:$('c-pass').value.trim()||null,
    platform_server:$('c-server').value.trim()||null,
    trader_name:$('c-name').value.trim(),
    trader_email:$('c-email').value.trim()||null,
    product_key:$('c-product').value,
    note:($('c-note').value||'').trim()||null,
    bogo_paid_key:$('c-paid').value||null};
  if(!body.login){toast('MT5 login is required.','err');return}
  /* Drugi tap = drugie konto z tym samym loginem — blokada na czas requestu. */
  const btn=$('c-go');btn.disabled=true;
  try{const r=await api('/api/accounts',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('create-modal')?.remove();
    toast(r.email_unknown?'Account created, but no trader with that e-mail, so it has no owner yet.'
          :r.linked_trader?`Account created for ${r.linked_trader}.`:'Account created.',
          r.email_unknown?'err':'ok');
    go('accounts');
  }catch(e){toast('Error: '+e.message,'err');btn.disabled=false}
}

/* ---------- admin inbox (bell) ---------- */
let INBOX=[];
async function loadInbox(){
  try{
    const d=await api('/api/admin/inbox');INBOX=d.items||[];
    const seen=localStorage.getItem('pf_admin_inbox_seen')||'';
    const n=INBOX.filter(i=>i.ts>seen).length;
    const dot=$('bell-dot');
    if(n){dot.textContent=n>9?'9+':n;dot.style.display='block'}else dot.style.display='none';
  }catch(_){}
}
function openInbox(){
  const seen=localStorage.getItem('pf_admin_inbox_seen')||'';
  const TYPE_ICO={order:'file',kyc:'shield',payout:'wallet',ticket:'chat',lead:'alert'};
  const wiersz=i=>`
    <div class="ticket-row" onclick="closeOver();go('${esc(i.view)}')${i.lead_id?`;openLead(${i.lead_id})`:''}">
      <div class="tile-ic ${i.ts>seen?'orange':'blue'}" style="width:36px;height:36px;flex:0 0 36px">${ICO[TYPE_ICO[i.type]]||ICO.file}</div>
      <div class="sub"><b>${esc(i.title)}</b>
        <span>${esc(i.body||'')} · ${dstr(i.ts)}</span></div>
      ${i.ts>seen?'<span class="status pending"><span class="dot"></span>new</span>':''}
    </div>`;
  /* Ten sam podział co przełączniki w Settings, ale jako FILTR na górze
     (decyzja usera): dwa pola Leads/Prop, klik przełącza listę. Wybór
     zapamiętany per przeglądarka. */
  const tab=localStorage.getItem('pf_admin_inbox_tab')||'leads';
  const leady=INBOX.filter(i=>i.type==='lead'),prop=INBOX.filter(i=>i.type!=='lead');
  const items=tab==='prop'?prop:leady;
  const segBtn=(k,l,n)=>`<button class="${tab===k?'on':''}"
    onclick="localStorage.setItem('pf_admin_inbox_tab','${k}');openInbox()">${l}${n?` (${n})`:''}</button>`;
  openOver('Notifications',pushCardHtml()
    +`<div class="seg" style="margin-bottom:12px">${segBtn('leads','Leads',leady.length)}${segBtn('prop','Prop',prop.length)}</div>`
    +(items.length?`<div class="tbl-wrap">${items.map(wiersz).join('')}</div>`
      :`<div class="empty"><h3>Nothing here</h3><p>${tab==='prop'
        ?'Orders, KYC submissions, payout requests and ticket messages show up here.'
        :'New leads, claims, statuses and follow-ups show up here.'}</p></div>`));
  paintPushCard();
  localStorage.setItem('pf_admin_inbox_seen',new Date().toISOString());
  loadInbox();
}

/* ---------- web push na telefon działu ----------
   Ta sama infrastruktura co w portalu tradera (/api/push/*, wspólny /sw.js):
   konto admina to Trader z is_admin, więc subskrypcja idzie tym samym
   endpointem. Na iOS push działa wyłącznie w PWA z ekranu głównego —
   stąd podpowiedź o instalacji zamiast martwego przycisku. */
const b64ToU8=b64=>{const p='='.repeat((4-b64.length%4)%4);
  const raw=atob((b64+p).replace(/-/g,'+').replace(/_/g,'/'));
  return Uint8Array.from(raw,c=>c.charCodeAt(0))};
function pushCardHtml(){
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent);
  const ok='serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
  if(!ok)return `<div class="lead-card sec-card" style="margin-bottom:12px"><div class="mod-row">
    <div><div class="lbl">Push to this device</div><div class="muted" style="font-size:11.5px">${
      ios?'Install the panel first: open /admin in Safari → Share → Add to Home Screen, then come back here.'
         :'This browser does not support web push.'}</div></div></div></div>`;
  return `<div class="lead-card sec-card" style="margin-bottom:12px"><div class="mod-row">
    <div><div class="lbl">Push to this device</div>
      <div class="muted" style="font-size:11.5px" id="push-state">New leads, claims and follow-ups — straight to this device.</div></div>
    <button class="btn-p" id="push-btn" onclick="toggleAdminPush()">Enable</button>
  </div>
  <p class="muted" style="font-size:11.5px;margin-top:8px">Pick which categories buzz — and pair
    your Telegram — in <a href="#" onclick="closeOver();go('settings');return false">Settings</a>.</p></div>`;
}

/* KOMPLETNA lista tego, co może brzęczeć u admina, w dwóch grupach: Leads
   (rura z landingu) i Prop (platforma). Wyciszane per KONTO
   (ui_prefs.admin_push) — jedna decyzja gasi wszystkie urządzenia admina.
   Brak wpisu = kategoria brzęczy; nowa kategoria zdarzeń dzwoni u wszystkich,
   dopóki ktoś jej świadomie nie zgasi. */
const PUSH_GROUPS=[
  ['Leads',[['lead_new','New leads'],['lead_action','Lead activity (claims & statuses)'],
    ['lead_reminder','Follow-ups & nudges']]],
  ['Prop',[['admin_order','Orders & payments'],['admin_kyc','KYC submissions'],
    ['admin_payout','Payout requests'],['admin_ticket','Support tickets'],
    ['admin_reach','Channel reach & balance']]],
];
function pushCatsHtml(){
  const cats=(ME&&ME.ui_prefs&&ME.ui_prefs.admin_push)||{};
  return PUSH_GROUPS.map(([grupa,katy])=>`
    <div class="lbl" style="font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin:10px 0 6px">${grupa}</div>
    <div class="chip-row">${katy.map(([k,l])=>`<label class="chip" style="cursor:pointer;display:inline-flex;gap:6px;align-items:center">
      <input type="checkbox" ${cats[k]===false?'':'checked'} onchange="setPushCat('${k}',this.checked)">${l}</label>`).join('')}</div>`).join('');
}
async function setPushCat(k,on){
  const prefs=(ME&&ME.ui_prefs&&typeof ME.ui_prefs==='object')?{...ME.ui_prefs}:{};
  const cats={...(prefs.admin_push||{})};
  if(on)delete cats[k];else cats[k]=false;
  prefs.admin_push=cats;
  try{
    await api('/api/me',{method:'PATCH',body:JSON.stringify({ui_prefs:prefs})});
    ME.ui_prefs=prefs;
    toast(on?'Will buzz again':'Muted — stays in the bell');
  }catch(e){toast('Error: '+e.message,'err')}
}
/* Tożsamość Telegrama z ŻYWYM statusem: po wydaniu kodu panel odpytuje
   GET /api/me/telegram-link co 3 s i sam przełącza się na „Connected as @nick"
   w chwili, gdy /start dojdzie — bez przeładowania. „Send test message" to
   dowód działania połączenia w drugą stronę: bot pisze do admina DM. */
function tgIdentityHtml(){
  const linked=ME&&ME.telegram_linked;
  return `<div class="mod-row" style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)">
    <div><div class="lbl">Telegram identity</div>
      <div class="muted" style="font-size:11.5px" id="tg-link-state">${linked
        ?`<span style="color:var(--green)">●</span> Connected${ME.telegram_username?' as <b>'+esc(ME.telegram_username)+'</b>':''}
           — channel clicks sign as <b>${esc(meMail())}</b>`
        :'Not linked — channel clicks sign with your Telegram first name'}</div></div>
    <div class="mod-btns">
      ${linked?`<button class="btn-o" onclick="tgLinkTest(this)">Send test message</button>`:''}
      <button class="btn-o" onclick="tgLinkCode()">${linked?'Re-link':'Link Telegram'}</button>
    </div>
  </div>
  <p class="muted" id="tg-link-code" style="font-size:12px;margin-top:8px"></p>`;
}
/* Wejście w Settings dociąga świeży stan z serwera: ME z boota potrafi być
   starsze niż parowanie (PWA wybudzona z uśpienia nie przeładowuje appki,
   a /start dochodzi przecież z telefonu, obok panelu) — bez tego „Not linked"
   wisiało mimo poprawnie zapisanego parowania. */
async function paintTgIdentity(){
  const box=$('tg-identity');
  if(!box)return;
  try{
    const st=await api('/api/me/telegram-link');
    if(ME){ME.telegram_linked=st.linked;ME.telegram_username=st.username}
    box.innerHTML=tgIdentityHtml();
  }catch(_){/* zostaje render z ME */}
}
async function tgLinkCode(){
  try{
    const d=await api('/api/me/telegram-link',{method:'POST'});
    const bot=d.bot?`<a href="https://t.me/${esc(d.bot)}" target="_blank" rel="noopener"><b>@${esc(d.bot)}</b></a>`
                   :'the <b>desk bot</b>';
    $('tg-link-code').innerHTML=`Open a private chat with ${bot} on Telegram and send:
      <span class="mono" style="user-select:all">/start ${esc(d.code)}</span>
      <span id="tg-wait">— waiting for your message…</span>`;
    tgPollLink(40);
  }catch(e){toast('Error: '+e.message,'err')}
}
let _tgPoll=0;
async function tgPollLink(pozostalo){
  clearTimeout(_tgPoll);
  if(pozostalo<=0){
    const w=$('tg-wait');
    if(w)w.textContent='— nothing arrived in 2 minutes; send the code and reopen Settings.';
    return;
  }
  try{
    const st=await api('/api/me/telegram-link');
    if(st.linked){
      ME.telegram_linked=true;ME.telegram_username=st.username;
      const box=$('tg-identity');
      if(box)box.innerHTML=tgIdentityHtml();
      toast('✅ Telegram connected'+(st.username?' as '+st.username:''),'ok');
      return;
    }
  }catch(_){/* chwilowy błąd sieci — następna próba za 3 s */}
  _tgPoll=setTimeout(()=>tgPollLink(pozostalo-1),3000);
}
async function tgLinkTest(btn){
  await busy(btn,'Sending…',async()=>{
    try{
      await api('/api/me/telegram-link/test',{method:'POST'});
      toast('📨 Test sent — check your Telegram','ok');
    }catch(e){toast('Test failed: '+e.message,'err')}
  });
}
async function paintPushCard(){
  const btn=$('push-btn'),st=$('push-state');
  if(!btn)return;
  try{
    let cfg;try{cfg=await api('/api/push/public-key')}catch(_){cfg={enabled:false}}
    if(!cfg.enabled){st.textContent='Push is not configured on the server.';btn.style.display='none';return}
    window._pushKey=cfg.key;
    if(Notification.permission==='denied'){
      st.textContent='Notifications are blocked for this site in the browser settings.';
      btn.style.display='none';return}
    const reg=await navigator.serviceWorker.ready;
    const sub=await reg.pushManager.getSubscription();
    if(sub){st.textContent='Enabled on this device.';btn.textContent='Disable'}
    else btn.textContent='Enable';
  }catch(_){}
}
async function toggleAdminPush(){
  const btn=$('push-btn');if(btn)btn.disabled=true;
  try{
    const reg=await navigator.serviceWorker.ready;
    let sub=await reg.pushManager.getSubscription();
    if(sub){
      await api('/api/me/push/unsubscribe',{method:'POST',body:JSON.stringify({endpoint:sub.endpoint})});
      await sub.unsubscribe();
      toast('Push disabled on this device.');
    }else{
      const perm=await Notification.requestPermission();
      if(perm!=='granted'){toast('Notifications were not allowed.','err');return}
      sub=await reg.pushManager.subscribe({userVisibleOnly:true,
        applicationServerKey:b64ToU8(window._pushKey)});
      await api('/api/me/push/subscribe',{method:'POST',body:JSON.stringify(sub.toJSON())});
      toast('🔔 Push enabled on this device.','ok');
    }
  }catch(e){toast('Push setup failed: '+e.message,'err')}
  finally{if(btn)btn.disabled=false;paintPushCard()}
}

/* ---------- deep-link z powiadomienia ----------
   Push niesie url `/admin?lead=<id>`. Trzy drogi, którymi może przyjść:
   zimny start (parametr w adresie — boot niżej), klik przy otwartym panelu
   (postMessage z sw.js) i powrót uśpionej PWA na iOS, gdzie postMessage
   przepada — stąd wpis w Cache Storage czytany przy każdym powrocie.
   Osobny klucz od portalu, żeby na wspólnym profilu desktop żadna z aplikacji
   nie zjadała cudzych kliknięć. */
function openLeadFromUrl(url){
  let lead=null;
  try{lead=new URL(url,location.origin).searchParams.get('lead')}catch(_){}
  if(!lead||!ME)return;
  go('leads');openLead(+lead);
}
async function applyPendingLead(){
  try{
    const c=await caches.open('pf-nav');const r=await c.match('/__pending-nav-admin');
    if(!r)return;
    const d=await r.json();await c.delete('/__pending-nav-admin');
    if(Date.now()-d.ts<30000)openLeadFromUrl(d.url);
  }catch(_){}
}
if('serviceWorker' in navigator){
  navigator.serviceWorker.addEventListener('message',e=>{
    const d=e.data||{};
    if(d.type!=='navigate')return;
    try{caches.open('pf-nav').then(c=>c.delete('/__pending-nav-admin'))}catch(_){}
    openLeadFromUrl(d.url);
  });
  navigator.serviceWorker.startMessages?.();
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible')return;
  applyPendingLead();setTimeout(applyPendingLead,600); /* zapis SW bywa jeszcze w locie */
});

/* ---------- start + auto-refresh ---------- */
if(localStorage.getItem('pf_admin_collapsed')==='1')$('side').classList.add('collapsed');
(async()=>{
  if(!TOKEN)return signInForm();
  try{
    const m=await api('/api/auth/me');
    if(!m.is_admin)return signInForm();
    ME=m; $('tok-state').textContent=m.email;
    $('app-shell').style.visibility='visible';   // only now reveal the panel
    /* Deep-link z pusha (zimny start): /admin?lead=<id> otwiera kartę leada.
       Adres wraca na /admin?pwa=1 — NIE na gołe /admin: bez `pwa=1` odświeżenie
       strony po wygaśnięciu ciasteczka trafia w celowy 404 serwera i admin
       "wylatuje z aplikacji", zamiast przejść przez furtkę i ekran logowania. */
    const lead=new URLSearchParams(location.search).get('lead');
    /* Hasz trzeba odczytac PRZED przepisaniem adresu i dokleic z powrotem —
       inaczej `replaceState` nizej skasowalby wlasnie odzyskany stan. */
    const st=czytajHasz();
    const hasz=st?'#'+haszZe(st):'';
    if(location.search!=='?pwa=1'||location.hash!==hasz)
      history.replaceState(null,'','/admin?pwa=1'+hasz);
    OSTATNI_HASZ=hasz.slice(1);
    if(lead){go('leads');openLead(+lead)}   // deep-link z pusha wygrywa z haszem
    else if(st){ustawStan(st);go(st.view)}  // globale PRZED go(): widok czyta je od razu
    else go('overview');
    applyPendingLead();
    loadInbox();
  }catch(e){
    if(e&&e.message==='Access denied')return; /* api() already redirected to login */
    /* Blad sieci na zimnym starcie (PWA w windzie, słaby zasięg): bez tego
       szkielet zostawał niewidoczny na zawsze — martwy bialy ekran. */
    document.body.insertAdjacentHTML('beforeend',
      `<div style="position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:24px;text-align:center;background:var(--bg,#f6f7fb);z-index:99">
        <div style="font-weight:700">No connection</div>
        <div style="font-size:13px;color:#64748b">The panel could not load. Check your network and try again.</div>
        <button class="btn-p" onclick="location.reload()">Retry</button>
      </div>`);
  }
})();
setInterval(()=>{
  if(document.hidden)return;
  /* Nie przerysowuj pod rekami: re-render zabija fokus i kursor w wyszukiwarce
     albo w polu otwartego modala — tick poczeka na nastepny obrot. */
  const a=document.activeElement;
  if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.tagName==='SELECT'))return;
  if(VIEW==='overview'||VIEW==='accounts')VIEWS[VIEW]().catch(()=>{});
},12000);
setInterval(()=>{if(!document.hidden&&ME)loadInbox()},60000);

/* ---------------- offline ----------------
   PWA otwarta w windzie pokazuje ostatnie dane, ale kazdy zapis przepadnie.
   Pasek mowi to wprost, zamiast pozwalac klikac w proznie. */
function paintOffline(){
  const bar=document.querySelector('.offline-bar');
  if(navigator.onLine){bar&&bar.remove();return}
  if(bar)return;
  const b=document.createElement('div');b.className='offline-bar';
  b.textContent='Offline — showing the last loaded data, changes will not save.';
  /* Nad paskiem gornym, w normalnym ukladzie. Przyklejony na fixed zaslanialby
     hamburger i przyciski akcji — akurat wtedy, gdy trzeba przejsc do widoku,
     ktory sie zdazyl zaladowac. */
  const m=document.querySelector('.main');
  m?m.prepend(b):document.body.prepend(b);
}
addEventListener('online',()=>{paintOffline();toast('Back online.','ok',3000)});
addEventListener('offline',paintOffline);
paintOffline();

/* ---------------- walidacja przy wyjsciu z pola ----------------
   Jedna delegacja dla wszystkich formularzy panelu: pole .inp z trescia,
   ktore nie przechodzi checkValidity (type=email, required, min itd.),
   dostaje czerwona ramke i komunikat przegladarki pod spodem — style .bad
   i .field-err juz istnieja w portal.css. Puste pole nie krzyczy: wymagane
   braki wylapuje i tak przycisk zapisu. */
document.addEventListener('focusout',e=>{
  const el=e.target;
  if(!(el instanceof HTMLInputElement)||!el.classList.contains('inp'))return;
  const nast=el.nextElementSibling;
  const err=nast&&nast.classList&&nast.classList.contains('field-err')?nast:null;
  if(el.value&&!el.checkValidity()){
    el.classList.add('bad');
    const box=err||el.insertAdjacentElement('afterend',
      Object.assign(document.createElement('div'),{className:'field-err'}));
    box.textContent=el.validationMessage;
  }else{el.classList.remove('bad');err&&err.remove()}
});

/* ---------------- szybkie akcje: long-press na karcie (telefon) ----------------
   Przyciski wiersza mieszkaja na dole karty .rtbl — przytrzymanie karty pol
   sekundy otwiera dolny arkusz (portalowy .sheet) ze SKLONOWANYMI przyciskami.
   cloneNode zachowuje inline onclick, wiec klony robia dokladnie to samo;
   `_row` wskazuje zrodlowy wiersz, zeby undo po usunieciu schowalo wlasciwa
   karte (patrz xdel). */
function closeActSheet(){
  const s=document.getElementById('act-sheet');
  if(!s)return;
  s.classList.remove('open');
  setTimeout(()=>{s.remove();document.getElementById('act-veil')?.remove()},180);
}
function openActSheet(tr){
  if(document.getElementById('act-sheet'))return;
  /* `:scope>.btn-x` lapie X na kaflu ticketa — tam przycisk lezy prosto
     w wierszu, nie w komorce .rt-acts. */
  const btns=[...tr.querySelectorAll('.rt-acts button,.rt-acts a,.lead-acts button,.lead-acts a,:scope>.btn-x')];
  if(!btns.length)return;
  const veil=document.createElement('div');veil.id='act-veil';veil.className='sheet-veil';
  veil.onclick=closeActSheet;
  const s=document.createElement('div');s.id='act-sheet';s.className='sheet';
  s.innerHTML='<div class="sheet-grab"></div><div class="act-sheet-title"></div><div class="act-sheet-list"></div>';
  const tytul=(tr.querySelector('.rt-main, .sub b')?.innerText||'').trim().split('\n')[0];
  s.querySelector('.act-sheet-title').textContent=tytul||'Actions';
  const list=s.querySelector('.act-sheet-list');
  btns.forEach(b=>{
    const c=b.cloneNode(true);
    c._row=tr;
    /* przyciski-ikony (X, kopiowanie openera) dostaja w arkuszu podpis z title */
    if(!c.textContent.trim()&&(b.title||b.getAttribute('aria-label')))
      c.append(' '+(b.title||b.getAttribute('aria-label')));
    c.addEventListener('click',closeActSheet);
    list.appendChild(c);
  });
  document.body.append(veil,s);
  requestAnimationFrame(()=>s.classList.add('open'));
  try{navigator.vibrate&&navigator.vibrate(10)}catch(_){}
}
let _lpT=null,_lpXY=null,_lpFired=false;
addEventListener('touchstart',e=>{
  if(!matchMedia('(max-width:860px)').matches)return;
  const tr=e.target.closest&&e.target.closest('table.rtbl tr,.ticket-row');
  if(!tr||tr.classList.contains('tr-sub'))return;
  /* start na przycisku/polu = zwykla interakcja, nie long-press */
  if(e.target.closest('button,a,input,select,textarea'))return;
  _lpXY=[e.touches[0].clientX,e.touches[0].clientY];
  _lpT=setTimeout(()=>{
    _lpT=null;_lpFired=true;openActSheet(tr);
    setTimeout(()=>{_lpFired=false},700);   // gdy click w ogole nie przyjdzie
  },500);
},{passive:true});
addEventListener('touchmove',e=>{
  if(!_lpT)return;
  const dx=e.touches[0].clientX-_lpXY[0],dy=e.touches[0].clientY-_lpXY[1];
  if(dx*dx+dy*dy>100){clearTimeout(_lpT);_lpT=null}   // to przewijanie, nie przytrzymanie
},{passive:true});
addEventListener('touchend',()=>{if(_lpT){clearTimeout(_lpT);_lpT=null}});
addEventListener('touchcancel',()=>{if(_lpT){clearTimeout(_lpT);_lpT=null}});
/* po otwarciu arkusza klik konczacy przytrzymanie nie moze otworzyc wiersza */
addEventListener('click',e=>{
  if(_lpFired){_lpFired=false;e.stopPropagation();e.preventDefault()}
},true);

/* ---------------- pull-to-refresh (mobile / PWA) ----------------
   Zainstalowana PWA nie ma przycisku ani gestu odswiezania — jedyna droga do
   swiezych danych bylo przelaczenie zakladki. Pociagniecie w dol przy samej
   gorze strony przeladowuje DANE biezacego widoku (nie cala strone: pelny
   reload traci stan i na slabym zasiegu potrafi wywalic z panelu). */
(function(){
  if(!('ontouchstart' in window))return;
  let startY=0,pull=0,armed=false,busy=false,el=null;
  const THRESH=72;
  function ind(){
    if(el)return el;
    el=document.createElement('div');el.id='ptr';
    el.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>';
    document.body.appendChild(el);return el;
  }
  /* Gest rusza tylko, gdy przewija sie sam dokument: wewnetrzne scrolle
     (szuflada, arkusz, tabela) maja wlasna fizyke i nie moga odpalac odswiezania. */
  function wewnetrznyScroll(n){
    for(;n&&n!==document.body;n=n.parentElement){
      if(n.nodeType!==1)continue;
      const s=getComputedStyle(n);
      if((s.overflowY==='auto'||s.overflowY==='scroll')&&n.scrollHeight>n.clientHeight+1)return true;
    }
    return false;
  }
  addEventListener('touchstart',e=>{
    armed=false;pull=0;
    if(busy||!ME||window.scrollY>0)return;
    if($('over').classList.contains('open')||document.getElementById('act-sheet')
       ||document.body.classList.contains('nav-open'))return;
    const a=document.activeElement;
    if(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))return; /* klawiatura otwarta */
    if(wewnetrznyScroll(e.target))return;
    startY=e.touches[0].clientY;armed=true;
  },{passive:true});
  addEventListener('touchmove',e=>{
    if(!armed||busy)return;
    pull=e.touches[0].clientY-startY;
    const i=ind();
    if(pull<=8){i.classList.remove('show','ready');return}
    const p=Math.min(pull,120);
    i.classList.add('show');
    i.style.transform=`translateX(-50%) translateY(${Math.round(p*0.55)}px) rotate(${Math.round(p*2)}deg)`;
    i.classList.toggle('ready',pull>THRESH);
  },{passive:true});
  async function koniec(){
    if(!armed)return;armed=false;
    const i=ind(),odpal=pull>THRESH&&!busy;
    pull=0;
    if(!odpal){i.classList.remove('show','ready');i.style.transform='';return}
    busy=true;i.classList.remove('ready');i.classList.add('show','spin');
    i.style.transform='translateX(-50%) translateY(46px)';
    /* Minimalne pol sekundy krecenia — blyskajacy na ulamek klatki wskaznik
       wyglada jak usterka, nie jak potwierdzenie. */
    const chwila=new Promise(r=>setTimeout(r,500));
    try{await Promise.all([(VIEWS[VIEW]||(()=>Promise.resolve()))(),loadInbox(),chwila])}
    catch(_){}
    busy=false;i.classList.remove('show','spin');i.style.transform='';
  }
  addEventListener('touchend',koniec,{passive:true});
  addEventListener('touchcancel',koniec,{passive:true});
})();

/* ---------------- klawiatura ekranowa vs dolny pasek ----------------
   position:fixed na iOS nie wie nic o klawiaturze: pasek "przykleja sie" nad
   nia albo zawisa w polowie ekranu. Na czas pisania pasek znika
   (body.kb-open w portal.css), wraca po zamknieciu klawiatury. */
addEventListener('focusin',e=>{
  if(e.target.matches&&e.target.matches('input,textarea,select'))
    document.body.classList.add('kb-open');
});
addEventListener('focusout',()=>setTimeout(()=>{
  const a=document.activeElement;
  if(!(a&&a.matches&&a.matches('input,textarea,select')))
    document.body.classList.remove('kb-open');
},80));
