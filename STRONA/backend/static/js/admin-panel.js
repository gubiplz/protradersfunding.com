const $=id=>document.getElementById(id);
const fmt=n=>(n??0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmt0=n=>(n??0).toLocaleString('en-US',{maximumFractionDigits:0});
const dstr=iso=>new Date(iso).toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* The panel is opened by an administrator ACCOUNT, not a shared token. The
   session is the same as the trader portal (`pf_token`), so a signed-in admin
   moves between the panel and the portal without a second login. */
let TOKEN=localStorage.getItem('pf_token')||null, ME=null;
const adminH=()=>{const h={'Content-Type':'application/json'};
  if(TOKEN)h['Authorization']='Bearer '+TOKEN; return h};
async function api(path,opts={}){
  const r=await fetch(path,{headers:adminH(),...opts});
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
};

const NAV=[
  {v:'overview',label:'Overview',ico:'grid'},
  {v:'leads',label:'Leads',ico:'users'},
  {v:'accounts',label:'Accounts',ico:'layers'},
  {v:'payouts',label:'Payouts',ico:'wallet'},
  {v:'kyc',label:'KYC',ico:'shield'},
  {v:'tickets',label:'Tickets',ico:'chat'},
  {v:'orders',label:'Orders',ico:'file'},
  {v:'pool',label:'MT5 Pool',ico:'bank'},
  {v:'telemetry',label:'Telemetry',ico:'trend'},
  {v:'settings',label:'Settings',ico:'gear'},
];
$('side-nav').innerHTML=NAV.map(n=>
  `<button class="sb-link" data-v="${n.v}" onclick="go('${n.v}')" title="${n.label}">${ICO[n.ico]}<span class="sb-txt">${n.label}</span></button>`).join('');

/* Mobile bottom bar: the 4 most-used sections; "More" opens the drawer with
   the full list. Same go()/NAV as the sidebar — one source of truth. */
const MORE_ICO='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>';
$('botnav').innerHTML=['overview','accounts','payouts','kyc'].map(v=>{
  const n=NAV.find(x=>x.v===v);
  return `<button class="botnav-btn" data-v="${n.v}" onclick="go('${n.v}')">${ICO[n.ico]}<span>${n.label}</span></button>`;
}).join('')+`<button class="botnav-btn" onclick="toggleSide(true)" aria-label="All sections">${MORE_ICO}<span>More</span></button>`;

const TITLES={
  overview:['Overview','Platform health and items waiting for you'],
  leads:['Leads','Applications from the landing page, and who they turned into'],
  accounts:['Accounts','All challenge accounts and their live risk metrics'],
  payouts:['Payouts','Every payout booked so far, plus requests waiting for review'],
  kyc:['KYC','Identity verifications awaiting review'],
  tickets:['Tickets','Support conversations with traders'],
  orders:['Orders','Purchases and revenue'],
  pool:['MT5 Pool','Pre-provisioned accounts ready to assign'],
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

let VIEW='overview';
/* Ekran przejscia: paski trzymaja wysokosc widoku, kolko w kolorze akcentu mowi,
   ze cos sie dzieje. Same paski na ciemnym motywie czytaly sie jako pusty ekran. */
const LOADING_HTML=(h=260)=>`<div class="view-load">
  <div class="skel" style="height:110px;margin-bottom:16px"></div>
  <div class="skel" style="height:${h}px"></div>
  <div class="vl-mid"><span class="vl-ring"></span><span class="vl-txt">Loading…</span></div>
</div>`;

/* Numer przelaczenia. Widoki pisza do #view dopiero po powrocie z API, wiec
   wolniejszy POPRZEDNI widok potrafil skonczyc sie PO zmianie zakladki i nadpisac
   nowy — na ekranie zostawal naglowek "Payouts" nad tabela kont. Zlapane przy
   sprawdzaniu wskaznika ladowania. Gdy przebieg okaze sie nieaktualny, przerysowujemy
   biezacy widok; to rzadka sciezka, wiec dodatkowe zapytanie nic nie kosztuje. */
let PRZEJSCIE = 0;

function go(v){
  VIEW=v;
  document.querySelectorAll('.sb-link[data-v],.botnav-btn[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  const t=TITLES[v]||['',''];
  $('pg-title').textContent=t[0]; $('pg-crumb').textContent=t[1];
  toggleSide(false);
  const moj=++PRZEJSCIE;
  $('view').innerHTML=LOADING_HTML(260);
  VIEWS[v]()
    .then(()=>{if(moj!==PRZEJSCIE&&VIEWS[VIEW])VIEWS[VIEW]()})
    .catch(e=>{if(!String(e.message).includes('token'))toast('Error: '+e.message,'err')});
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

function withUndo(opis,wykonaj,wiersz){
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
    toast('Restored — nothing was deleted.','ok',3500);
  };
  _czekajace.add(zadanie);
}

/* Wyjscie ze strony DOMYKA to, co czeka: admin widzial, ze wiersz zniknal, wiec
   zamkniecie karty nie moze go po cichu przywrocic. `keepalive` na zadaniu
   pozwala mu doleciec juz po zamknieciu strony. */
addEventListener('beforeunload',()=>{[..._czekajace].forEach(z=>z.domknij())});

function closeOver(){$('over').classList.remove('open')}
function openOver(title,html){$('o-title').textContent=title;$('o-body').innerHTML=html;$('over').classList.add('open')}

const STATUS_LBL={active:'evaluation',funded:'funded',failed:'failed',passed:'passed',provisioning:'provisioning'};
const PHASE_LBL={eval_1:'Phase 1',eval_2:'Phase 2',funded:'Funded'};
function mini(pct){const p=Math.min(100,Math.max(0,pct||0));
  const c=p>=100?'bad':p>=70?'warn':'ok';
  return `<div class="mini"><i class="${c}" style="width:${p}%"></i></div>`}

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
  $('view').innerHTML=`
    <div class="sysbar">
      <span class="sys ${s.stripe==='mock'?'warn':''}"><span class="dot"></span>Payments: <b>${esc(s.stripe)}</b></span>
      <span class="sys"><span class="dot"></span>Provisioning queue: <b>${s.provisioning??0}</b></span>
      <span class="sys"><span class="dot"></span>Pool free: <b>${s.pool_free??0}</b></span>
    </div>
    <div class="todo-grid">
      ${todo(pendingPay,'payout requests to review','payouts','wallet')}
      ${todo((kyc.pending||[]).length,'KYC submissions pending','kyc','shield')}
      ${todo(openTick,'support tickets open','tickets','chat')}
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
      ${orders.length?`<div class="tbl-wrap tw-sm" style="border:0;box-shadow:none;border-radius:0"><table class="tbl"><thead><tr>
        <th>#</th><th>Trader</th><th>Product</th><th>Amount</th><th>Status</th><th>Account</th></tr></thead>
        <tbody>${orders.slice(0,8).map(o=>`<tr>
          <td class="num">${o.id}</td><td>${esc(o.trader_email||'—')}</td><td>${esc(o.product_key)}</td>
          <td class="num">$${fmt(o.amount_usd)}</td>
          <td><span class="status ${o.status==='paid'?'paid':'pending'}"><span class="dot"></span>${esc(o.status)}</span></td>
          <td class="num">${o.account_id||'—'}</td></tr>`).join('')}</tbody></table></div>`
        :'<p class="muted" style="font-size:13px">No orders yet.</p>'}
    </div>`;
 },

 async accounts(){
  const list=await api('/api/accounts');
  window._accs=list;
  window._accFilter=window._accFilter||'all';
  renderAccounts();
 },

 async payouts(){
  /* Full list: every booked payout (including ones issued by hand from the
     account card) plus requests that have not become a payout yet. */
  window._payReqs=await api('/api/admin/payouts');
  renderPayoutsView();
 },

 async kyc(){
  window._kycData=await api('/api/admin/kyc');
  renderKyc();
 },

 _kycRender(){
  const d=window._kycData||{};
  const pending=d.pending||[], histAll=d.history||[];
  const kf=window._kycFilter||'all';
  const hist=histAll.filter(t=>kf==='all'||t.status===kf);
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
    :`<div class="empty"><h3>Nothing to verify</h3><p>KYC submissions from traders show up here.</p></div>`;
  const histTbl=histAll.length?`<div class="sec-card card-md" style="margin-top:18px">
    <h3>History</h3>
    <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Past verification decisions.</p>
    <div class="toolbar" style="margin-bottom:10px">
      <div class="seg">${[['all','All'],['approved','Approved'],['rejected','Rejected']]
        .map(([k,l])=>`<button class="${kf===k?'on':''}" onclick="window._kycFilter='${k}';renderKyc()">${l}</button>`).join('')}</div>
      <span class="count-pill">${hist.length} of ${histAll.length}</span>
    </div>
    ${hist.length?`<div class="tbl-wrap"><table class="tbl sortable" data-tkey="admin.kyc">
      <thead><tr><th>Reviewed</th><th>Trader</th><th>Country</th><th>Document</th><th>Status</th><th class="no-sort">Documents</th><th class="no-sort"></th></tr></thead>
      <tbody>${hist.map(t=>`<tr>
        <td class="muted" data-sort="${esc(t.reviewed_at||'')}">${t.reviewed_at?dstr(t.reviewed_at):'—'}</td>
        <td>${esc(t.full_name||'—')}<div class="muted" style="font-size:11.5px">${esc(t.email)}</div></td>
        <td class="muted">${esc(t.country||'—')}</td>
        <td class="muted">${esc(t.id_type||'—')} ${esc(t.id_number||t.doc_ref||'')}</td>
        <td><span class="status ${t.status==='approved'?'funded':'failed'}"><span class="dot"></span>${esc(t.status)}</span></td>
        <td>${(t.docs||[]).map(k=>`<button class="btn-o sm" onclick="viewDoc(${t.trader_id},'${k}')">${esc(k.replace('_',' '))}</button>`).join(' ')||'<span class="muted">—</span>'}</td>
        <td style="white-space:nowrap"><button class="btn-o sm" onclick="revertKyc(${t.trader_id})"
          title="Undo this decision, back to the pending queue">Revert</button>
          ${XBTN(`deleteKycRow(${t.trader_id},'${esc(t.email)}')`,'Delete KYC record and uploaded documents')}</td></tr>`).join('')}
      </tbody></table></div>`:`<p class="muted" style="font-size:13px">No ${esc(kf)} decisions.</p>`}</div>`:'';
  $('view').innerHTML=cards+histTbl;
 },

 async tickets(){
  const rows=await api('/api/admin/tickets');
  window._tickets=rows;
  /* X siedzi W wierszu, ktory sam otwiera rozmowe, wiec musi zatrzymac klikniecie —
     inaczej kazde usuniecie otwieraloby przy okazji watek. Do `delTicket` idzie
     samo id: temat bywa z apostrofem ("Can't log in"), a wstrzykniety w inline
     onclick rozwalilby ten atrybut. */
  const row=t=>`
    <div class="ticket-row" onclick="openTicket(${t.id})">
      <div class="tile-ic ${t.status==='open'?'orange':t.status==='answered'?'green':'blue'}" style="width:36px;height:36px;flex:0 0 36px">${ICO.chat}</div>
      <div class="sub"><b>${esc(t.subject)}</b>
        <span>#${t.id} · ${esc(t.trader_email||'—')} · ${t.messages} message${t.messages>1?'s':''} · ${dstr(t.last_ts)}</span></div>
      <span class="status ${t.status==='closed'?'failed':t.status==='answered'?'paid':'pending'}"><span class="dot"></span>${esc(t.status)}</span>
      ${XBTN(`event.stopPropagation();delTicket(${t.id})`,'Delete this ticket and its conversation')}
    </div>`;
  const active=rows.filter(t=>t.status!=='closed'), closed=rows.filter(t=>t.status==='closed');
  $('view').innerHTML=(active.length?`<div class="tbl-wrap">`+active.map(row).join('')+`</div>`
      :`<div class="empty"><h3>No open tickets</h3><p>Support conversations started by traders appear here.</p></div>`)
    +(closed.length?`<div class="sec-card" style="margin-top:18px">
      <h3>History</h3>
      <p class="muted" style="font-size:12.5px;margin:4px 0 12px">Closed tickets. Click to review the conversation.</p>
      <div class="tbl-wrap">`+closed.map(row).join('')+`</div></div>`:'');
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
      <p class="muted" style="font-size:12.5px;margin:6px 0 12px">These challenges are paid for but not tradable yet: the pool has no free account of that size. Add one below and it is assigned automatically.</p>
      <div class="tbl-wrap tw-sm"><table class="tbl sortable" data-tkey="admin.pool-waiting">
        <thead><tr><th>Account</th><th>Trader</th><th>Needs size</th><th>Waiting since</th></tr></thead>
        <tbody>${waiting.map(w=>`<tr>
          <td class="num">#${w.account_id}</td>
          <td>${esc(w.trader_email||'—')}</td>
          <td class="num"><b>$${fmt0(w.account_size)}</b></td>
          <td class="muted" data-sort="${esc(w.created_at||'')}">${w.created_at?dstr(w.created_at):'—'}</td></tr>`).join('')}
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
      <h3>Add account manually</h3>
      <p class="muted" style="font-size:12.5px;margin-bottom:14px">Accounts you created at your broker. Paste the credentials here. Provisioning assigns the first free account of a matching size when a challenge is purchased.</p>
      <div class="pool-form">
        <input id="pl-login" class="inp" placeholder="MT5 login">
        <input id="pl-pass" class="inp" placeholder="Password">
        <input id="pl-server" class="inp" placeholder="Server">
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
  const [s,pb]=await Promise.all([api('/api/stats'),api('/api/admin/payout-engine')]);
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
 },

 async telemetry(){
  const d=await api('/api/admin/telemetry');
  const items=d.items||[];
  $('view').innerHTML=items.length?`
    <p class="muted" style="font-size:12.5px;margin-bottom:10px">Click a row to see the individual events; click a trader email inside to see everything that user did.</p>
    <div class="tbl-wrap tw-sm"><table class="tbl sortable" data-tkey="admin.telemetry">
    <thead><tr><th>Day</th><th>Event</th><th style="text-align:right">Count</th>
      <th style="text-align:right">Unique traders</th></tr></thead>
    <tbody>${items.map(i=>`<tr class="clickable" onclick="openTelemetryDetail('${esc(i.day)}','${esc(i.name)}')">
      <td class="num">${esc(i.day)}</td><td>${esc(i.name)}</td>
      <td class="num" style="text-align:right">${i.count}</td>
      <td class="num" style="text-align:right">${i.traders}</td></tr>`).join('')}
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
  return `<span class="q-wrap"><input class="inp" id="${id}" placeholder="${ph}" value="${esc(val)}"
      oninput="window.${stateKey}=this.value;const p=this.selectionStart;${render}();qFocus('${id}',p)">
    ${val?`<button class="q-x" type="button" aria-label="Clear search" title="Clear"
      onclick="window.${stateKey}='';${render}();qFocus('${id}')">&times;</button>`:''}</span>`;
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
      <div class="seg">${seg.map(([k,l])=>`<button class="${f===k?'on':''}" onclick="window._accFilter='${k}';renderAccounts()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
    </div>
    ${rows.length?`<div class="tbl-wrap tw-wide"><table class="tbl sortable" data-tkey="admin.accounts.v2">
      <thead><tr><th>Created</th><th>Paid</th><th>Login</th><th>Trader</th><th>Plan</th><th>Phase</th><th>Status</th>
        <th title="Trade BOT">Bot</th>
        <th style="text-align:right">Balance</th><th style="text-align:right">Equity</th><th style="text-align:right">P&amp;L</th>
        <th>Daily</th><th>Max DD</th><th class="no-sort"></th></tr></thead>
      <tbody>${rows.map(a=>{const m=a.metrics||{};
        return `<tr class="clickable" onclick="openAccount(${a.id})">
          <td class="muted" style="white-space:nowrap" data-sort="${esc(a.created_at||'')}">${a.created_at?dstr(a.created_at):'—'}</td>
          <td class="muted" style="white-space:nowrap" data-sort="${esc(a.paid_at||'')}">${a.paid_at?dstr(a.paid_at):'—'}</td>
          <td class="num" style="font-weight:600">${a.status==='provisioning'?'<span class="muted">pending…</span>':esc(a.login)}</td>
          <td>${esc(a.trader_name||'—')}${a.trader_email?`<div class="muted" style="font-size:11px">${esc(a.trader_email)}</div>`:''}</td>
          <td class="muted">${esc(a.product_key)}</td>
          <td class="muted">${PHASE_LBL[a.phase]||esc(a.phase)}</td>
          <td><span class="status ${esc(a.status)}"><span class="dot"></span>${STATUS_LBL[a.status]||esc(a.status)}</span></td>
          <td data-sort="${a.bot_enabled?(a.bot_paused?1:2):0}" title="${a.bot_enabled?(a.bot_paused?'Trade BOT paused':'Trade BOT running'):'Trade BOT off'}">${a.bot_enabled?(a.bot_paused?'🟡':'🟢'):'🔴'}</td>
          <td class="num" style="text-align:right">$${fmt(a.balance)}</td>
          <td class="num" style="text-align:right">$${fmt(a.equity)}</td>
          <td class="num ${(m.profit_pct||0)>=0?'up':'down'}" style="text-align:right">${(m.profit_pct||0)>=0?'+':''}${(m.profit_pct||0).toFixed(2)}%</td>
          <td data-sort="${(m.daily_loss_used_pct||0).toFixed(2)}">${mini(m.daily_loss_used_pct)}</td>
          <td data-sort="${(m.overall_dd_used_pct||0).toFixed(2)}">${mini(m.overall_dd_used_pct)}</td>
          <td style="text-align:right" onclick="event.stopPropagation()">${
            XBTN(`deleteAccountRow(${a.id},'${esc(a.login)}','${esc(a.trader_name||'')}')`,'Delete account')}</td></tr>`}).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>No accounts match</h3><p>Try a different search or filter.</p></div>`}`;
}

/* ---------- kyc: seg buttons need a global to call ---------- */
function renderKyc(){VIEWS._kycRender()}

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
        .map(([k,l])=>`<button class="${f===k?'on':''}" onclick="window._poolFilter='${k}';renderPoolList()">${l}</button>`).join('')}</div>
      <span class="count-pill">${list.length} of ${rows.length}</span>
    </div>`
    +(list.length?`<div class="tbl-wrap tw-wide"><table class="tbl sortable" data-tkey="admin.pool">
      <thead><tr><th>#</th><th>Login</th><th class="no-sort">Password</th><th>Server</th><th>Size</th><th>State</th><th>Assigned to</th><th>When</th><th class="no-sort"></th></tr></thead>
      <tbody>${list.map(p=>`<tr>
        <td class="num">${p.id}</td><td class="num">${esc(p.platform_login)}${p.simulated?'<div class="muted" style="font-size:10.5px;letter-spacing:.06em">SIMULATED</div>':''}</td>
        <td>${p.platform_password?`<span class="mono" style="cursor:pointer" title="Click to reveal"
          onclick="this.textContent=this.textContent==='••••••••'?this.dataset.p:'••••••••'" data-p="${esc(p.platform_password)}">••••••••</span>`:'<span class="muted">—</span>'}</td>
        <td class="muted">${esc(p.platform_server)}</td><td class="num">$${fmt0(p.account_size)}</td>
        <td>${p.retired_reason?`<span class="status failed"><span class="dot"></span>retired</span>`
          :p.claimed?`<span class="status pending"><span class="dot"></span>assigned</span>`
          :'<span class="status funded"><span class="dot"></span>free</span>'}</td>
        <td>${p.claimed?`${esc(p.trader_email||'—')}<div class="muted" style="font-size:11.5px">${p.retired_reason?esc(p.retired_reason)+' — not reusable':`account #${p.claimed_by_account_id}${p.account_status?' · '+esc(p.account_status):''}`}</div>`:'<span class="muted">—</span>'}</td>
        <td class="muted" data-sort="${esc(p.claimed_at||'')}">${p.claimed_at?dstr(p.claimed_at):'—'}</td>
        <td style="white-space:nowrap">
          <button class="btn-o sm" onclick="editPool(${p.id})">Edit</button>
          ${p.claimed&&!p.retired_reason?''
            :' '+XBTN(`delPool(${p.id},'${esc(p.platform_login)}',${p.retired_reason?1:0})`,
                      p.retired_reason?'Delete this retired entry':'Remove from pool')}</td></tr>
        <tr id="pool-edit-${p.id}" class="tr-sub" style="display:none"><td colspan="9" style="background:var(--bg)">
          <div class="pool-form" style="margin:6px 0">
            <input id="ed-login-${p.id}" class="inp" value="${esc(p.platform_login)}" placeholder="MT5 login">
            <input id="ed-pass-${p.id}" class="inp" placeholder="New password (leave empty to keep)">
            <input id="ed-server-${p.id}" class="inp" value="${esc(p.platform_server)}" placeholder="Server">
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
  const rows=list.filter(r=>f==='all'||r.status===f);
  const seg=[['all','All'],['pending','Pending'],['approved','Approved'],['paid','Paid'],['rejected','Rejected']];
  $('view').innerHTML=`
    <div class="toolbar">
      ${list.length?`<div class="seg">${seg.map(([k,l])=>`<button class="${f===k?'on':''}" onclick="window._payFilter='${k}';renderPayoutsView()">${l}</button>`).join('')}</div>`:''}
      <button class="btn-o sm" onclick="openPayoutImport()">Import history</button>
      ${list.length?`<span class="count-pill">${rows.length} of ${list.length}</span>`:''}
    </div>`+(rows.length?`<div class="tbl-wrap tw-wide"><table class="tbl sortable" data-tkey="admin.payouts">
    <thead><tr><th>Date</th><th>Account</th><th>Trader</th><th>Profit</th><th>Trader share</th><th>Method</th><th>Status</th><th class="no-sort">Certificate</th><th class="no-sort"></th></tr></thead>
    <tbody>${rows.map(r=>`<tr>
      <td class="muted" data-sort="${esc(r.ts||'')}">${dstr(r.ts)}</td><td class="num">${esc(r.account_login||'—')}</td>
      <td>${esc(r.trader_email||'—')}</td>
      <td class="num">$${fmt(r.profit_amount)}</td><td class="num up">$${fmt(r.trader_share)}</td>
      <td>${(()=>{const d=r.details||{};
        const label=r.method==='usdt'?'USDT':r.method==='wise'?'Wise':'Bank';
        const info=r.method==='usdt'?[d.network,d.address].filter(Boolean).join(' · ')
          :r.method==='wise'?(d.email||'')
          :[d.holder,d.iban,d.swift,d.bank_name].filter(Boolean).join(' · ');
        return `${esc(label)}${info?`<div class="muted mono" style="font-size:11px;max-width:260px;word-break:break-all">${esc(info)}</div>`:''}`})()}</td>
      <td><span class="status ${r.status==='paid'?'paid':r.status==='pending'?'pending'
        :r.status==='approved'?'active':'failed'}"><span class="dot"></span>${esc(r.status)}</span>
        ${r.status==='rejected'&&r.reject_reason?`<div class="muted" style="font-size:11px;max-width:200px">${esc(r.reject_reason)}</div>`:''}</td>
      <td style="white-space:nowrap">${r.kind!=='payout'?'<span class="muted">—</span>'
        :r.cert_url
          ?`<a class="btn-o sm" href="${r.cert_url}" target="_blank">Open</a>
            <button class="btn-o sm" onclick="copyCert('${location.origin}${r.cert_url}')">Copy link</button>
            <button class="btn-o sm" onclick="setCertLp(${r.id},${r.show_on_lp?'false':'true'})">
              ${r.show_on_lp?'On LP ✓':'Not on LP'}</button>
            <button class="btn-o sm" onclick="revokeCert(${r.id})">Revoke</button>`
          :`<button class="btn-o sm" onclick="askCertLp(${r.id})">Generate</button>`}</td>
      <td style="text-align:right;white-space:nowrap">${r.kind==='request'&&r.status==='pending'
        ?`<button class="btn-p sm" onclick="approvePayout(${r.id})">Approve &amp; pay</button>
          <button class="btn-o sm" onclick="rejectPayout(${r.id})">Reject</button>`
        :XBTN(`deletePayoutRow('${r.kind}',${r.id},${r.trader_share},'${esc(r.account_login||'')}')`,
              r.kind==='payout'?'Delete payout':'Delete request')}</td></tr>`).join('')}
    </tbody></table></div>
    <p class="muted" style="font-size:11.5px;margin-top:10px">Approving pays the trader share and refunds the challenge fee on the first payout for that account.</p>`
    :list.length?`<div class="empty"><h3>No ${esc(f)} payouts</h3><p>Try a different filter.</p></div>`
    :`<div class="empty"><h3>No payouts yet</h3><p>Payouts you issue and requests from funded traders both land here.</p></div>`);
}

/* ---------- orders: search + payment flags ---------- */
function renderOrders(){
  const list=window._orders||[];
  const q=(window._ordQ||'').toLowerCase();
  const f=window._ordFilter||'all';
  const rows=list.filter(o=>
    (f==='all'||(f==='awaiting'?o.flag==='awaiting_crypto':o.status===f))&&
    (!q||(o.trader_email||'').toLowerCase().includes(q)
    ||(o.product_key||'').includes(q)||(o.status||'').includes(q)
    ||(o.flag||'').includes(q)||String(o.id)===q));
  /* Kafelki opisuja TO, CO WIDAC pod nimi — czyli zbior po filtrze i szukajce.
     Wczesniej liczyly sie z calej listy, wiec przelaczenie na "Failed" zostawialo
     nad pusta tabela pelny przychod. Ten sam blad byl w portalu na Challenges. */
  const paid=rows.filter(o=>o.status==='paid');
  const revenue=paid.reduce((s,o)=>s+o.amount_usd,0);
  const avg=paid.length?revenue/paid.length:0;
  const zawezone=rows.length!==list.length;
  const podpis=zawezone?`<div class="sub">of ${list.length} total</div>`:'';
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile"><div class="tile-ic green">${ICO.dollar}</div>
        <div><div class="lbl">Revenue</div><div class="val">$${fmt0(revenue)}</div><div class="sub">${paid.length} paid orders</div></div></div>
      <div class="stat-tile"><div class="tile-ic blue">${ICO.file}</div>
        <div><div class="lbl">Orders ${zawezone?'shown':'total'}</div><div class="val">${rows.length}</div>
          <div class="sub">${rows.length-paid.length} unpaid</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.trend}</div>
        <div><div class="lbl">Average order</div><div class="val">$${fmt0(avg)}</div>${podpis}</div></div>
    </div>
    <div class="toolbar">
      ${searchBox('ord-q','_ordQ','renderOrders','Search email, product, status…')}
      <div class="seg">${[['all','All'],['paid','Paid'],['pending','Pending'],['awaiting','Awaiting crypto'],['failed','Failed']]
        .map(([k,l])=>`<button class="${f===k?'on':''}" onclick="window._ordFilter='${k}';renderOrders()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
      <button class="btn-p sm" onclick="openManualOrder()"
        title="Record an order the customer pays outside Stripe (crypto, transfer)">+ New order</button>
    </div>
    ${rows.length?`<div class="tbl-wrap tw-wide"><table class="tbl sortable" data-tkey="admin.orders">
      <thead><tr><th>#</th><th>Date</th><th>Trader</th><th>Product</th><th>Amount</th><th>Provider</th><th>Status</th><th>Account</th><th class="no-sort"></th></tr></thead>
      <tbody>${rows.map(o=>`<tr>
        <td class="num">${o.id}</td><td class="muted" data-sort="${esc(o.created_at||'')}">${dstr(o.created_at)}</td>
        <td>${esc(o.trader_email||'—')}</td><td>${esc(o.product_key)}</td>
        <td class="num">$${fmt(o.amount_usd)}${o.coupon?` <span class="up" style="font-size:11px">(${esc(o.coupon)})</span>`:''}</td>
        <td class="muted">${esc(o.provider)}</td>
        <td><span class="status ${o.status==='paid'?'paid':o.status==='failed'?'failed':'pending'}"><span class="dot"></span>${esc(o.status)}</span>
          ${o.status==='pending'&&o.flag==='awaiting_crypto'?'<div class="muted" style="font-size:11px;white-space:nowrap">⏳ awaiting crypto</div>':''}
          ${o.status!=='paid'&&o.payment_address?`<div class="muted" title="${esc((o.payment_network?o.payment_network+' · ':'')+o.payment_address)}"
            style="font-size:11px;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.payment_network?o.payment_network+' · ':'')}${esc(o.payment_address)}</div>`:''}
          ${o.status==='failed'&&o.fail_reason?`<div class="muted" style="font-size:11px;max-width:200px">${esc(o.fail_reason)}</div>`:''}</td>
        <td class="num">${o.account_id||'—'}</td>
        <td style="white-space:nowrap">${o.status==='paid'?'':`
          ${o.status==='pending'?`<button class="btn-o sm" onclick="payLink(${o.id})"
            title="Copy a card-payment link for this order — send it to the customer">Pay link</button>
          <button class="btn-o sm" onclick="flagOrder(${o.id},'${o.flag==='awaiting_crypto'?'':'awaiting_crypto'}')"
            title="${o.flag==='awaiting_crypto'?'Clear the awaiting-crypto flag':'Mark as awaiting crypto payment'}">${o.flag==='awaiting_crypto'?'Clear flag':'Crypto?'}</button>
          <button class="btn-o sm" onclick="markOrderFailed(${o.id})" title="Payment is not coming, close the order with a reason">Mark failed</button>`:''}
          <button class="btn-p sm" onclick="markOrderPaid(${o.id})" title="Confirm the payment arrived, creates the account">Mark paid</button>`}
          ${XBTN(`deleteOrderRow(${o.id},'${esc(o.trader_email||'')}',${o.amount_usd},${o.account_id||0})`,'Delete order')}</td></tr>`).join('')}
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
const LEAD_STATUSES=[['new','New'],['messaged','Messaged'],['replied','Replied'],
  ['no_reply','No reply'],['rejected','Rejected']];
const leadLabel=s=>(LEAD_STATUSES.find(([k])=>k===s)||[,s])[1];
/* The questionnaire answers are what the grade is made of, and they are the first
   thing you want in front of you when writing. Hover rather than a column: four
   free-text answers would push the rest of the row off the screen. */
const leadAnswers=a=>Object.entries(a||{}).map(([q,v])=>`${q} → ${v}`).join('\n');

function renderLeads(){
  const list=window._leads||[];
  const q=(window._leadQ||'').toLowerCase();
  const f=window._leadFilter||'all';
  const rows=list.filter(l=>
    (f==='all'||(f==='bought'?l.paid_usd>0
      :f==='due'?(l.next_due&&dueDays(l.next_due)<=0)
      :f==='mine'?l.owner===deskWho(false)
      :f==='free'?!l.owner
      :l.status===f))&&
    (!q||(l.email||'').toLowerCase().includes(q)||(l.name||'').toLowerCase().includes(q)
     ||(l.phone||'').includes(q)||(l.ref||'').toLowerCase().includes(q)));
  /* Same rule as Orders: the tiles describe WHAT IS VISIBLE below them, so
     switching to "Rejected" cannot leave the full revenue sitting on top. */
  const bought=rows.filter(l=>l.paid_usd>0);
  const revenue=bought.reduce((s,l)=>s+l.paid_usd,0);
  const waiting=rows.filter(l=>l.status==='new').length;
  const conv=rows.length?Math.round(bought.length/rows.length*100):0;
  /* Counted over the WHOLE list, not the filtered rows: a follow-up that came
     due is the one thing that must not hide behind the filter you left on. */
  const due=list.filter(l=>l.next_due&&dueDays(l.next_due)<=0).length;
  $('view').innerHTML=`
    <div class="stats-row">
      <div class="stat-tile ${due?'clickable':''}" ${due?`onclick="window._leadFilter='due';renderLeads()"`:''}>
        <div class="tile-ic ${due?'orange':'blue'}">${ICO.alert}</div>
        <div><div class="lbl">Follow-ups due</div><div class="val">${due}</div>
          <div class="sub">${due?'someone is waiting to hear back':'nothing scheduled for today'}</div></div></div>
      <div class="stat-tile"><div class="tile-ic ${waiting?'orange':'blue'}">${ICO.alert}</div>
        <div><div class="lbl">Nobody wrote yet</div><div class="val">${waiting}</div>
          <div class="sub">of ${rows.length} shown</div></div></div>
      <div class="stat-tile"><div class="tile-ic green">${ICO.dollar}</div>
        <div><div class="lbl">Revenue from leads</div><div class="val">$${fmt0(revenue)}</div>
          <div class="sub">${bought.length} bought</div></div></div>
      <div class="stat-tile"><div class="tile-ic purple">${ICO.trend}</div>
        <div><div class="lbl">Conversion</div><div class="val">${conv}%</div>
          <div class="sub">lead to paid order</div></div></div>
    </div>
    <div class="toolbar">
      ${searchBox('lead-q','_leadQ','renderLeads','Search name, email, phone or partner…')}
      <div class="seg">${[['all','All'],['due','Due'],['mine','Mine'],['free','Free'],
        ...LEAD_STATUSES,['bought','Bought']]
        .map(([k,l])=>`<button class="${f===k?'on':''}" onclick="window._leadFilter='${k}';renderLeads()">${l}</button>`).join('')}</div>
      <span class="count-pill">${rows.length} of ${list.length}</span>
    </div>
    ${rows.length?`<p class="muted" style="font-size:12.5px;margin-bottom:10px">Tap a row for the full history: every application with the answers given at the time, status changes and who made them — and the button that deletes it.</p>
    <div class="tbl-wrap tw-wide lead-wrap"><table class="tbl sortable lead-tbl" data-tkey="admin.leads">
      <thead><tr><th>Date</th><th>Lead</th><th>Contact</th><th>Grade</th><th>Source</th>
        <th>Status</th><th>Bought</th><th>Note</th></tr></thead>
      <tbody>${rows.map(l=>`<tr class="clickable" onclick="openLead(${l.id})">
        <td class="muted" data-l="Date" data-sort="${esc(l.created_at||'')}">${dstr(l.created_at)}</td>
        <td data-l="Lead"><b>${esc(l.name||'—')}</b>
          ${l.owner?`<div style="font-size:11px" title="Taken by">👤 ${esc(l.owner)}</div>`
            :'<div class="muted" style="font-size:11px">nobody took it</div>'}
          ${l.applications>1?`<div class="muted" style="font-size:11px" title="Filled the form more than once">↻ applied ${l.applications}×</div>`:''}
          ${l.outcome==='not_qualified'?'<div class="muted" style="font-size:11px">failed the questionnaire</div>':''}</td>
        <td data-l="Contact"><a href="mailto:${esc(l.email)}">${esc(l.email)}</a>
          ${l.telegram?`<div><a href="https://t.me/${esc((l.telegram||'').replace(/^@/,''))}" target="_blank" rel="noopener">${esc(l.telegram)}</a></div>`:''}
          ${l.phone?`<div class="muted" style="font-size:11px"><a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>${l.phone_iso?` ${esc(l.phone_iso)}`:''}</div>`:''}</td>
        <td data-l="Grade" data-sort="${l.score||0}" title="${esc(leadAnswers(l.answers))}">${l.tier?`<span class="status ${l.tier==='high'?'paid':l.tier==='warm'?'pending':'failed'}"><span class="dot"></span>${esc(l.tier)} ${l.score}</span>`:'<span class="muted">—</span>'}</td>
        <td class="muted" data-l="Source">${esc(l.source||'—')}${l.ref?`<div style="font-size:11px">via ${esc(l.ref)}</div>`:''}</td>
        <td data-l="Status" onclick="event.stopPropagation()"><select class="inp sm st-${esc(l.status)}" style="min-width:116px" onchange="setLeadStatus(${l.id},this.value)">${
          LEAD_STATUSES.map(([k,lab])=>`<option value="${k}"${l.status===k?' selected':''}>${lab}</option>`).join('')}</select>
          ${l.next_due?`<div class="due ${dueDays(l.next_due)<=0?'now':''}">⏰ ${dueLabel(l.next_due)}</div>`
            :l.contacted_at?`<div class="muted" style="font-size:11px">${dstr(l.contacted_at)}</div>`:''}</td>
        <td class="num" data-l="Bought">${l.paid_usd>0?`<span class="status paid"><span class="dot"></span>$${fmt0(l.paid_usd)}</span>`:'<span class="muted">—</span>'}</td>
        <td data-l="Note" onclick="event.stopPropagation()"><input class="inp sm" style="min-width:170px" value="${esc(l.note||'')}"
          placeholder="Add a note…" onchange="setLeadNote(${l.id},this.value)"></td></tr>`).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>${list.length?'No leads match':'No leads yet'}</h3>
        <p>${list.length?'Try a different search or filter.':'Applications from the landing page land here.'}</p></div>`}`;
}

/* Both writers patch the row in memory instead of refetching the list: the admin
   is usually working down a long table and a full re-render would throw away
   their scroll position and whatever they were typing in the next note. */
async function setLeadStatus(id,status){
  try{
    const d=await api('/api/admin/leads/'+id,{method:'POST',body:JSON.stringify({status})});
    const row=(window._leads||[]).find(l=>l.id===id);
    if(row){row.status=d.status;row.contacted_at=d.contacted_at}
    toast('Marked as '+leadLabel(status));
    if(window._leadFilter&&window._leadFilter!=='all')renderLeads();
  }catch(e){toast('Error: '+e.message,'err');VIEWS.leads()}
}

async function setLeadNote(id,note){
  try{
    await api('/api/admin/leads/'+id,{method:'POST',body:JSON.stringify({note})});
    const row=(window._leads||[]).find(l=>l.id===id);
    if(row)row.note=note;
    toast('Note saved');
  }catch(e){toast('Error: '+e.message,'err')}
}

/* Who is sitting at the panel. The admin login is one shared token, so the app
   has no idea — but "who took this lead" is the whole point of the owner field,
   so we ask once and keep it in this browser. `ask=false` for code that only
   wants to compare (the "Mine" filter must not pop a dialog on every render). */
function deskWho(ask=true){
  let who=localStorage.getItem('pf_desk_who')||'';
  if(!who&&ask){
    who=(prompt('Your name — shown on leads you take')||'').trim().slice(0,60);
    if(who)localStorage.setItem('pf_desk_who',who);
  }
  return who;
}

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
  const who=deskWho();
  if(!who){toast('Enter your name first','err');return}
  patchLead(id,{owner:who},'Yours — write to them');
}

/* Kasowanie leada. Jedyne miejsce w tej zakładce, które nie da się cofnąć,
   więc siedzi na dole karty i pyta. Wiersze testowe i pomyłki muszą znikać
   naprawdę: `leads.email` jest unikalny, a ukryty wiersz blokowałby adres. */
async function deleteLead(id){
  const l=(window._leads||[]).find(x=>x.id===id)||{};
  const kto=l.name||l.email||('#'+id);
  if(!await askConfirm({title:`Delete ${esc(kto)}?`,
    body:'This removes the lead, its history and its follow-ups for good. '
      +'Orders and the trader account on the same e-mail stay untouched.<br><br>'
      +'The address becomes free again, so the same person can apply from scratch.',
    ok:'Delete lead',danger:true}))return;
  try{
    await api('/api/admin/leads/'+id,{method:'DELETE'});
    window._leads=(window._leads||[]).filter(x=>x.id!==id);
    closeOver();toast('Lead deleted','ok');
    renderLeads();
  }catch(e){toast('Error: '+e.message,'err')}
}

/* ---------- one lead: the history behind the row ----------
   The table answers "where does this lead stand"; this answers "how did it get
   there". The list cannot show it: one person is one row on purpose, so a second
   application overwrites the first and only the counter survives. Every write
   also lands in lead_events, and that is what gets read back here. */
const LEAD_STATUS_CLS={new:'pending',messaged:'pending',replied:'paid',
  no_reply:'failed',rejected:'failed'};
const LEAD_EVENT_LBL={applied:'Applied',status:'Status',note:'Note',reminder:'Reminder',
  claim:'Owner',tier:'Grade'};
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
const dueDays=iso=>{const a=new Date(iso),b=new Date();
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
  const ja=deskWho(false);
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
  </div>`;
}

/* Osobna karta na dole, nie przycisk obok „Release": to jedyna operacja w tej
   zakładce, której nie da się cofnąć, i ma być trudniej ją kliknąć przez pomyłkę
   niż zmienić status. */
function leadDangerCard(l){
  return `<div class="lead-card sec-card danger-zone">
    <div class="mod-row">
      <div><div class="lbl">Delete</div>
        <div class="muted" style="font-size:11.5px">For test entries and duplicates. History and follow-ups go with it.</div></div>
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
        ${XBTN(`cancelLeadReminder(${l.id},${r.id})`,'Cancel this reminder')}
      </div>`).join('')}</div>`:'<p class="muted" style="font-size:12.5px">Nothing scheduled.</p>'}
    <div class="chip-row rem-presets">${REMINDER_PRESETS.map(([lab],i)=>
      `<button class="chip preset" onclick="pickPreset(${i})">${lab}</button>`).join('')}</div>
    <input id="rem-text" class="inp sm" placeholder="What needs doing, in your own words…">
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

function pickPreset(i){
  const [,days,rep,text]=REMINDER_PRESETS[i];
  $('rem-text').value=text;$('rem-days').value=days;$('rem-rep').checked=rep>0;
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
    toast('Reminder cancelled');
    await openLead(id);renderLeads();
  }catch(e){toast('Error: '+e.message,'err')}
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
  const ev=l.events||[],ords=l.orders||[];
  openOver(l.name||l.email,`
    <div class="chip-row">
      <span class="status ${LEAD_STATUS_CLS[l.status]||'pending'}"><span class="dot"></span>${esc(leadLabel(l.status))}</span>
      ${l.tier?`<span class="chip">${esc(l.tier)} ${l.score}</span>`:''}
      ${l.paid_usd>0?`<span class="status paid"><span class="dot"></span>paid $${fmt0(l.paid_usd)}</span>`:''}
      ${l.applications>1?`<span class="chip">applied ${l.applications}×</span>`:''}
      ${l.outcome==='not_qualified'?'<span class="chip">failed the questionnaire</span>':''}
    </div>
    <div class="chip-row">
      ${l.telegram?`<span class="chip"><a href="https://t.me/${esc((l.telegram||'').replace(/^@/,''))}" target="_blank" rel="noopener">${esc(l.telegram)}</a></span>`:''}
      <span class="chip"><a href="mailto:${esc(l.email)}">${esc(l.email)}</a></span>
      ${l.phone?`<span class="chip"><a href="tel:${esc(l.phone)}">${esc(l.phone)}</a></span>`:''}
      ${l.country?`<span class="chip">${esc(l.country)}</span>`:''}
      ${l.source?`<span class="chip">${esc(l.source)}${l.ref?' via '+esc(l.ref):''}</span>`:''}
    </div>
    ${leadModCard(l)}
    ${leadReminderCard(l)}
    ${l.note?`<div class="lead-card sec-card"><h4>Notes</h4>
      ${l.note.split('\n').filter(Boolean).map(n=>`<div class="note-line">${esc(n)}</div>`).join('')}
      <p class="muted" style="font-size:11.5px;margin-top:8px">Reply to the lead's message on Telegram and it lands here.</p></div>`:''}
    ${ords.length?`<h4 style="margin:16px 0 6px">Orders</h4>
      <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Date</th><th>Product</th><th>Amount</th><th>Status</th></tr></thead>
      <tbody>${ords.map(o=>`<tr><td class="muted" style="white-space:nowrap">${dstr(o.created_at)}</td>
        <td>${esc(o.product_key)}</td><td class="num">$${fmt0(o.amount_usd)}</td>
        <td><span class="status ${o.status==='paid'?'paid':o.status==='failed'?'failed':'pending'}"><span class="dot"></span>${esc(o.status)}</span></td></tr>`).join('')}
      </tbody></table></div>`
      :'<p class="muted" style="font-size:12.5px;margin-top:14px">No orders on this e-mail address.</p>'}
    <h4 style="margin:16px 0 6px">History</h4>
    ${ev.length?`<div class="tbl-wrap"><table class="tbl" style="table-layout:fixed">
      <thead><tr><th style="width:104px">When</th><th style="width:96px">What</th><th>Details</th></tr></thead>
      <tbody>${ev.map(e=>`<tr>
        <td class="muted" style="white-space:nowrap">${dstr(e.created_at)}</td>
        <td>${esc(LEAD_EVENT_LBL[e.kind]||e.kind)}
          <div class="muted" style="font-size:11px">${esc(e.actor||'—')}</div></td>
        <td><div style="font-size:12px">${esc(leadEventDetail(e))}</div>
          ${Object.keys(e.answers||{}).length?`<div class="muted" style="font-size:11.5px;margin-top:4px">${
            Object.entries(e.answers).map(([q,v])=>`${esc(q)} → <b>${esc(v)}</b>`).join('<br>')}</div>`:''}</td></tr>`).join('')}
      </tbody></table></div>`
      :`<div class="empty"><h3>Nothing recorded yet</h3>
        <p>History starts at the first change made after this feature went live.</p></div>`}
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
/* Link do zapłaty kartą za KONKRETNE zamówienie — do wklejenia klientowi na
   Telegramie. Token jest stały, więc drugie kliknięcie daje ten sam adres i nie
   unieważnia linku, którego klient już używa. */
async function payLink(id,prefix){
  let url;
  try{url=(await api(`/api/admin/orders/${id}/pay-link`,{method:'POST'})).url}
  catch(e){toast('Error: '+e.message,'err');return}
  try{await navigator.clipboard.writeText(url);
    toast(`🔗 ${prefix?prefix+' ':''}Payment link copied — paste it to the customer.`,'ok',9000)}
  catch(e){showPayLink(url)}   /* Safari bez gestu / http: kopiowanie odpada, a link musi być widoczny */
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
    toast(d.already?'This order was already paid.':`✅ Paid. Account #${d.account_id} created.`,'ok');go('orders')}
  catch(e){toast('Error: '+e.message,'err')}
}

/* Bot pace is stored as a short key; the chip shows what it actually means. */
const PACE_TXT={light:'1–2 trades/day',steady:'4–8 trades/day',busy:'~20 trades/day'};

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
  if(!await askConfirm({title:'Revoke this certificate?',
    body:'The public link stops working and the entry disappears from the landing page. The payout '
      +'itself stays on the account.<br><br>To only hide it from the landing page, use '
      +'<b>Take off the LP</b> instead.',
    ok:'Revoke certificate',danger:true}))return;
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

async function deleteAccount(id,login){
  if(!await askConfirm({title:`Delete account ${esc(login)}?`,
    body:'This removes it from the platform permanently, together with its snapshots, breaches and '
      +'certificates.',
    ok:'Delete account',danger:true}))return;
  try{await api('/api/accounts/'+id,{method:'DELETE'});closeOver();toast('Account deleted.','ok');go('accounts')}
  catch(e){toast('Error: '+e.message,'err')}
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
async function rejectPayout(id){
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
}
async function approvePayout(id){
  if(!await askConfirm({title:'Approve and pay this request?',
    body:'This pays the trader share, plus the fee refund on a first payout. <b>It cannot be undone.</b>',
    ok:'Approve & pay',danger:true}))return;
  try{const d=await api(`/api/admin/payout-requests/${id}/approve`,{method:'POST'});
    toast(`✅ Paid $${fmt(d.total_paid)}${d.fee_refund?` (incl. $${fmt(d.fee_refund)} fee refund)`:''}`,'ok');
    go('payouts');
  }catch(e){toast('Error: '+e.message,'err')}
}
async function approveKyc(tid){
  try{await api(`/api/admin/kyc/${tid}/approve`,{method:'POST'});toast('KYC approved.','ok');go('kyc')}
  catch(e){toast('Error: '+e.message,'err')}
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
  return `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th>Time</th><th>Event</th><th>Trader</th><th>Details</th></tr></thead>
    <tbody>${items.map(e=>{
      let props='';
      try{props=Object.entries(JSON.parse(e.props||'{}')).map(([k,v])=>`${esc(k)}: ${esc(v)}`).join(' · ')}
      catch(_){props=esc(e.props||'')}
      return `<tr><td class="muted" style="white-space:nowrap">${dstr(e.ts)}</td><td>${esc(e.name)}</td>
        <td>${e.trader_id?`<a href="#" onclick="openTraderActivity(${e.trader_id},'${esc(e.email||'')}');return false">${esc(e.email||('#'+e.trader_id))}</a>`:'<span class="muted">—</span>'}</td>
        <td class="muted" style="font-size:11.5px">${props||'—'}</td></tr>`}).join('')}
    </tbody></table></div>`;
}
async function openTelemetryDetail(day,name){
  try{const d=await api(`/api/admin/telemetry/events?day=${encodeURIComponent(day)}&name=${encodeURIComponent(name)}`);
    const items=d.items||[];
    openOver(`${name} · ${day}`,items.length?telemetryRows(items)
      :'<div class="empty"><h3>No events</h3></div>')}
  catch(e){toast('Error: '+e.message,'err')}
}
async function openTraderActivity(tid,email){
  try{const d=await api(`/api/admin/telemetry/events?trader_id=${tid}`);
    const items=d.items||[];
    openOver(`Activity · ${email||('trader #'+tid)}`,
      `<p class="muted" style="font-size:12.5px;margin-bottom:10px">Everything this user did, newest first (last ${items.length} events).</p>`
      +(items.length?telemetryRows(items):'<div class="empty"><h3>No events</h3></div>'))}
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
/* ---------- Payout BOT ---------- */
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

async function setSimFallback(on){
  try{await api('/api/admin/pool/sim-fallback',{method:'POST',body:JSON.stringify({enabled:on})});
    toast(on?'Auto-provisioning of simulated credentials is ON.':'Auto-provisioning turned off.','ok');
  }catch(e){toast('Error: '+e.message,'err');go('pool')}
}
function editPool(id){
  const w=$('pool-edit-'+id); w.style.display=(w.style.display==='none'?'table-row':'none');
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
  +`${esc(t.email)}${t.full_name?' — '+esc(t.full_name):''} (${t.accounts} acc.)</option>`;

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
      <button class="icon-btn" onclick="document.getElementById('grant-modal').remove()">
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
      <button class="btn-p lg" style="width:100%" onclick="submitGrant()">Grant &amp; create account</button>
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
  try{
    const r=await api('/api/admin/grant',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('grant-modal')?.remove();
    toast(`🎁 Granted ${r.product_key} to ${r.trader_email}.\n${r.status==='provisioning'
      ?'MT5 account is being created. Credentials e-mailed within a minute.'
      :'Credentials e-mailed to the trader.'}`,'ok',9000);
    go('accounts');
  }catch(e){toast('Error: '+e.message,'err')}
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
     `.ticket-row` obok `tr`, bo zgloszenia nie sa tabela, tylko kaflami. */
  const wiersz=_ostatniPrzycisk&&_ostatniPrzycisk.closest('tr, .ticket-row');
  const [tytul,...reszta]=String(question).split('\n\n');
  if(!await askConfirm({title:tytul.trim(),
    body:esc(reszta.join('\n').trim()).replace(/\n/g,'<br>'),
    ok:'Delete',danger:true}))return;
  withUndo(tytul.trim().replace(/\?+$/,''),async()=>{
    try{const r=await api(url,{method:'DELETE',keepalive:true});
      toast(typeof okMsg==='function'?okMsg(r):(okMsg||'Deleted.'),'ok');
      if(typeof after==='function')after(r);
    }catch(e){
      toast('Error: '+e.message,'err');
      if(wiersz&&wiersz.style)wiersz.style.display='';   // nie udalo sie — wroc
    }
  },wiersz);
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
  xdel(`/api/accounts/${id}`,
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
      <button class="icon-btn" onclick="document.getElementById('payimp-modal').remove()">
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

/* ---------- modal: order paid outside Stripe (card link, crypto, transfer) ---------- */
async function openManualOrder(traderId){
  let products=[],traders=[];
  try{[products,traders]=await Promise.all([
    (await fetch('/api/products')).json(),api('/api/admin/traders')])}
  catch(e){toast('Error: '+e.message,'err');return}
  if(!traders.length){toast('No registered traders yet.','err');return}
  window._moTraders=traders;
  document.getElementById('order-modal')?.remove();
  const box=document.createElement('div');
  box.id='order-modal';box.className='modal-wrap';
  box.innerHTML=`<div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head"><h3>New order</h3>
      <button class="icon-btn" onclick="document.getElementById('order-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px" id="mo-lead">For a customer paying outside Stripe — crypto or a transfer.
      The order lands as <b>unpaid</b>; the account is created only when you hit <b>Mark paid</b>, exactly like a card payment.</p>
    <div class="stack">
      <div class="seg" id="mo-method" style="width:100%">
        <button type="button" class="on" onclick="moMethod('crypto')">Crypto / transfer</button>
        <button type="button" onclick="moMethod('link')">Card payment link</button>
      </div>
      <div><label class="muted" style="font-size:12px">Customer</label>
        <input id="mo-search" class="inp" style="margin-bottom:7px" placeholder="Search by e-mail or name" oninput="moFilter()">
        <select id="mo-trader" class="inp" size="5"></select></div>
      <div><label class="muted" style="font-size:12px">Challenge</label>
        <select id="mo-product" class="inp" onchange="moPrice()">${products.map(p=>
          `<option value="${esc(p.key)}" data-price="${p.price_usd}">${esc(p.label)} — $${fmt0(p.account_size)} · $${fmt(p.price_usd)}</option>`).join('')}</select></div>
      <div><label class="muted" style="font-size:12px">Amount to collect (USD)</label>
        <input id="mo-amount" class="inp" type="number" step="0.01" min="0"></div>
      <div class="stack" id="mo-crypto">
        <div><label class="muted" style="font-size:12px">Network</label>
          <input id="mo-network" class="inp" placeholder="e.g. USDT · TRC20" value="${esc(lastWallet().network||'')}"></div>
        <div><label class="muted" style="font-size:12px">Wallet address <span style="opacity:.65">(goes into the e-mail)</span></label>
          <input id="mo-address" class="inp" spellcheck="false" placeholder="Paste the wallet address" value="${esc(lastWallet().address||'')}"></div>
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
  moMethod(window._moMethod||'crypto');
  moFilter(traderId);
  moPrice();
}
/* Jak klient płaci. Link kartą nie ma nic wspólnego z portfelem, więc pola
   crypto znikają — inaczej admin wysyłałby maila z adresem USDT komuś, kogo
   właśnie kieruje do kasy Stripe'a. */
function moMethod(m){
  window._moMethod=m;
  const link=m==='link';
  $('mo-method').querySelectorAll('button').forEach((b,i)=>b.classList.toggle('on',(i===1)===link));
  $('mo-crypto').style.display=link?'none':'';
  $('mo-go').textContent=link?'Create order & copy payment link':'Create order';
  $('mo-lead').innerHTML=link
    ?`The order lands as <b>unpaid</b> and you get a link to our payment page — send it to the customer
      (Telegram, chat, e-mail). They pay by card, the account is created automatically. The link never expires.`
    :`For a customer paying outside Stripe — crypto or a transfer.
      The order lands as <b>unpaid</b>; the account is created only when you hit <b>Mark paid</b>, exactly like a card payment.`;
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
function moPrice(){
  const o=$('mo-product').selectedOptions[0];
  if(o)$('mo-amount').value=o.dataset.price;
}
async function submitManualOrder(){
  const tid=parseInt($('mo-trader').value);
  if(!tid){toast('Pick a customer first.','err');return}
  const amount=parseFloat($('mo-amount').value);
  if(!(amount>=0)){toast('Enter the amount to collect.','err');return}
  const link=window._moMethod==='link';
  const addr=link?'':($('mo-address').value||'').trim();
  const net=link?'':($('mo-network').value||'').trim();
  if(addr)saveWallet(addr,net);
  try{
    const r=await api('/api/admin/orders',{method:'POST',body:JSON.stringify({
      trader_id:tid,product_key:$('mo-product').value,amount_usd:amount,
      flag:(!link&&$('mo-awaiting').checked)?'awaiting_crypto':'',
      payment_address:addr,payment_network:net,
      notify_trader:!link&&$('mo-mail').checked})});
    document.getElementById('order-modal')?.remove();
    if(link){await payLink(r.id,`Order #${r.id} for ${r.trader_email} — $${fmt(r.amount_usd)}.`)}
    else{const [txt,kind]=mailInfo(r);
      toast(`🧾 Order #${r.id} for ${r.trader_email} — $${fmt(r.amount_usd)}. ${txt}`,kind,9000)}
    go('orders');
  }catch(e){toast('Error: '+e.message,'err')}
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
      <button class="icon-btn" onclick="document.getElementById('credits-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Credits are a USD store balance:
      they reduce the price of the trader's next challenge automatically at checkout.
      Use a negative amount to correct a mistake.</p>
    <div class="stack">
      <div><label class="muted" style="font-size:12px">Trader</label>
        <select id="cr-trader" class="inp" onchange="creditsBalance()">${traders.map(t=>
          `<option value="${t.id}" data-credits="${t.credits_usd||0}"${traderId===t.id?' selected':''}>${esc(t.email)}${t.full_name?' — '+esc(t.full_name):''}</option>`).join('')}</select></div>
      <div class="muted" id="cr-balance" style="font-size:12.5px"></div>
      <input id="cr-amount" class="inp" type="number" step="1" placeholder="Amount in USD, e.g. 100">
      <input id="cr-note" class="inp" placeholder="Note for the ledger, e.g. Contest prize">
      <button class="btn-p lg" style="width:100%" onclick="submitCredits()">Add credits</button>
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
  try{
    const r=await api(`/api/admin/traders/${$('cr-trader').value}/credits`,{method:'POST',
      body:JSON.stringify({amount,note:($('cr-note').value||'').trim()||null})});
    document.getElementById('credits-modal')?.remove();
    toast(`💳 ${r.email} now has $${fmt(r.credits_usd)} in store credits.`,'ok',7000);
  }catch(e){toast('Error: '+e.message,'err')}
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
      <button class="icon-btn" onclick="document.getElementById('create-modal').remove()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
    <p class="muted" style="font-size:12.5px;margin-bottom:14px">Everything typed by hand, from an MT5 account outside the pool — nothing is taken from MT5 Pool and its state stays untouched. Drawdown type comes from the plan you pick.</p>
    <div class="stack">
      <input id="c-login" class="inp" placeholder="MT5 login (e.g. 100099)">
      <input id="c-pass" class="inp" placeholder="MT5 password">
      <input id="c-server" class="inp" placeholder="MT5 server (e.g. MetaQuotes-Demo)">
      <input id="c-name" class="inp" placeholder="Trader name">
      <input id="c-email" class="inp" placeholder="Trader e-mail (links the account to their portal)">
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
      <button class="btn-p lg" style="width:100%" onclick="submitCreate()">Create account</button>
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
  try{const r=await api('/api/accounts',{method:'POST',body:JSON.stringify(body)});
    document.getElementById('create-modal')?.remove();
    toast(r.email_unknown?'Account created, but no trader with that e-mail, so it has no owner yet.'
          :r.linked_trader?`Account created for ${r.linked_trader}.`:'Account created.',
          r.email_unknown?'err':'ok');
    go('accounts');
  }catch(e){toast('Error: '+e.message,'err')}
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
  const TYPE_ICO={order:'file',kyc:'shield',payout:'wallet',ticket:'chat'};
  openOver('Notifications',INBOX.length?`<div class="tbl-wrap">`+INBOX.map(i=>`
    <div class="ticket-row" onclick="closeOver();go('${esc(i.view)}')">
      <div class="tile-ic ${i.ts>seen?'orange':'blue'}" style="width:36px;height:36px;flex:0 0 36px">${ICO[TYPE_ICO[i.type]]||ICO.file}</div>
      <div class="sub"><b>${esc(i.title)}</b>
        <span>${esc(i.body||'')} · ${dstr(i.ts)}</span></div>
      ${i.ts>seen?'<span class="status pending"><span class="dot"></span>new</span>':''}
    </div>`).join('')+`</div>`
    :'<div class="empty"><h3>Nothing new</h3><p>New orders, KYC submissions, payout requests and ticket messages show up here.</p></div>');
  localStorage.setItem('pf_admin_inbox_seen',new Date().toISOString());
  loadInbox();
}

/* ---------- start + auto-refresh ---------- */
if(localStorage.getItem('pf_admin_collapsed')==='1')$('side').classList.add('collapsed');
(async()=>{
  if(!TOKEN)return signInForm();
  try{
    const m=await api('/api/auth/me');
    if(!m.is_admin)return signInForm();
    ME=m; $('tok-state').textContent=m.email;
    $('app-shell').style.visibility='visible';   // only now reveal the panel
    go('overview');
    loadInbox();
  }catch(e){/* api() already redirected to login */}
})();
setInterval(()=>{
  if(document.hidden)return;
  if(VIEW==='overview'||VIEW==='accounts')VIEWS[VIEW]().catch(()=>{});
},12000);
setInterval(()=>{if(!document.hidden&&ME)loadInbox()},60000);
