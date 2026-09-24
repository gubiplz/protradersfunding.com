/* Wspólne klocki panelu powiadomień (dzwonek) — panel admina i portal tradera.
   Ikony, czas względny, gesty (przesunięcie wiersza, zsunięcie arkusza),
   animacje wejścia/wyjścia wiersza, dwuetapowe „usuń wszystko" i „przeczytane,
   gdy widziane". Klasy i wygląd: static/css/notifications.css.
   Ładowany PRZED admin-panel.js / portal-app.js; `dutc` pochodzi z nich, ale
   jest wołane dopiero w czasie działania, więc kolejność definicji nie gra roli. */
const npQ=(s,r=document)=>[...r.querySelectorAll(s)];
const npRM=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const npNarrow=()=>matchMedia('(max-width:640px)').matches;
const NP_P={
  bell:'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  gear:'<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  x:'<path d="M18 6 6 18M6 6l12 12"/>',
  chevL:'<path d="m15 18-6-6 6-6"/>',chevR:'<path d="m9 18 6-6-6-6"/>',chevD:'<path d="m6 9 6 6 6-6"/>',
  check:'<path d="M20 6 9 17l-5-5"/>',
  dot:'<circle cx="12" cy="12" r="4.5" fill="currentColor" stroke="none"/>',
  trash:'<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  userPlus:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>',
  userCheck:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m16 11 2 2 4-4"/>',
  undo:'<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  arrows:'<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
  send:'<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  alarm:'<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6M22 6l-3-3"/>',
  bag:'<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
  shield:'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  shieldX:'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m14.5 9.5-5 5M9.5 9.5l5 5"/>',
  wallet:'<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  chat:'<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  octagon:'<path d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86z"/><path d="M12 8v4M12 16h.01"/>',
  megaphone:'<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  note:'<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  trend:'<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  gift:'<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C10 3 12 8 12 8s2-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
  truck:'<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
  sms:'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  mail:'<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
  phone:'<rect width="14" height="20" x="5" y="2" rx="2"/><path d="M12 18h.01"/>',
  laptop:'<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>',
  share:'<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="m16 6-4-4-4 4"/><path d="M12 2v13"/>',
  plusSq:'<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M8 12h8M12 8v8"/>',
  layers:'<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  eye:'<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  checkCircle:'<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  xCircle:'<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  trophy:'<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
  badge:'<path d="M3.85 8.62a4 4 0 0 1 4.78-4.77 4 4 0 0 1 6.74 0 4 4 0 0 1 4.78 4.78 4 4 0 0 1 0 6.74 4 4 0 0 1-4.77 4.78 4 4 0 0 1-6.75 0 4 4 0 0 1-4.78-4.77 4 4 0 0 1 0-6.76Z"/><path d="m9 12 2 2 4-4"/>',
  gauge:'<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
  target:'<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  calCheck:'<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
  chart:'<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
  tag:'<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r="1" fill="currentColor"/>',
};
const npI=(n,sw=1.9)=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NP_P[n]||NP_P.bell}</svg>`;

/* Czas po polsku dla działu, ale na osi Warszawy: „dziś/wczoraj" liczymy tam,
   gdzie siedzi zespół, a nie w UTC serwera. */
const npDay=iso=>dutc(iso).toLocaleDateString('en-CA',{timeZone:'Europe/Warsaw'});
function npDaysAgo(iso){return Math.round((Date.parse(npDay(new Date().toISOString()))-Date.parse(npDay(iso)))/864e5)}
function npRel(iso){
  const m=(Date.now()-dutc(iso).getTime())/6e4,d=npDaysAgo(iso);
  if(m<1)return 'now';
  if(d<=0)return m<60?Math.floor(m)+'m':Math.floor(m/60)+'h';
  if(d===1)return dutc(iso).toLocaleTimeString('en-GB',{timeZone:'Europe/Warsaw',hour:'2-digit',minute:'2-digit'});
  return dutc(iso).toLocaleDateString('en-GB',{timeZone:'Europe/Warsaw',day:'numeric',month:'short'});
}
const npDayLabel=iso=>{const d=npDaysAgo(iso);return d<=0?'Today':d===1?'Yesterday':'Earlier'};
const npIds=row=>row?(row.dataset.ids?row.dataset.ids.split(','):[row.dataset.id]):[];

function npGrow(el){
  if(npRM())return;
  const h=el.offsetHeight;
  el.style.height='0px';el.style.opacity='0';void el.offsetHeight;
  el.style.transition='height .5s var(--np-spring),opacity .3s var(--np-ease)';
  el.style.height=h+'px';el.style.opacity='1';
  el.classList.add('pop');
  setTimeout(()=>{el.style.height=el.style.opacity=el.style.transition=''},650);
}
function npCollapse(el){
  if(npRM()){el.style.display='none';return}
  el.style.height=el.offsetHeight+'px';void el.offsetHeight;el.classList.add('gone');
}
/* Dwuetapowe „usuń wszystko": pierwszy klik uzbraja, drugi wykonuje. Bez
   systemowego confirm(), który na iOS w PWA potrafi zamrozić widok. */
function npArm(btn,ile,fn){
  const lab=btn.querySelector('b')||btn;
  if(btn.classList.contains('armed')){clearTimeout(btn._t);btn.classList.remove('armed');lab.textContent=btn._o;return fn()}
  if(!ile)return;
  btn._o=lab.textContent;btn.classList.add('armed');
  lab.textContent=`Delete ${ile} notification${ile===1?'':'s'}? Tap again`;
  btn._t=setTimeout(()=>{btn.classList.remove('armed');lab.textContent=btn._o},3500);
}
/* „Usunięto · Undo" dla stron bez własnego withUndo (portal): akcja idzie na
   serwer dopiero po 5 s, więc cofnięcie niczego nie odtwarza. */
function npUndoToast(msg,wykonaj,cofnij){
  document.querySelector('.np-undo')?.dispatchEvent(new Event('np-flush'));
  const t=document.createElement('div');
  t.className='np-undo';t.setAttribute('role','status');
  t.innerHTML=`<span></span><button type="button">Undo</button>`;
  t.querySelector('span').textContent=msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=>t.classList.add('show'));
  let zrobione=false;
  const zamknij=()=>{t.classList.remove('show');setTimeout(()=>t.remove(),400)};
  const domknij=()=>{if(zrobione)return;zrobione=true;clearTimeout(zegar);zamknij();wykonaj()};
  const zegar=setTimeout(domknij,5000);
  t.addEventListener('np-flush',domknij);
  t.querySelector('button').onclick=()=>{if(zrobione)return;zrobione=true;clearTimeout(zegar);zamknij();cofnij()};
  addEventListener('pagehide',domknij,{once:true});
}
/* Sprężynowe liczniki: podskok, gdy liczba ROŚNIE. */
function npBumpNum(el,n){
  const byl=+el.dataset.n||0;
  el.dataset.n=n;
  if(n>byl&&byl){el.classList.remove('np-bump');void el.offsetWidth;el.classList.add('np-bump')}
}

/* „Przeczytane, gdy widziane": wiersz 2 s na ekranie gaśnie sam; paczka
   leci jednym wywołaniem `onSeen(ids)`. */
const NP_SEEN={io:null,t:new Map(),q:[],flush:0};
function npObserveSeen(root,rows,onSeen,ok){
  if(NP_SEEN.io){NP_SEEN.io.disconnect();NP_SEEN.io=null}
  NP_SEEN.t.forEach(clearTimeout);NP_SEEN.t.clear();
  if(!root||!rows.length)return;
  NP_SEEN.io=new IntersectionObserver(es=>es.forEach(en=>{
    const r=en.target;
    const widac=en.isIntersecting&&en.intersectionRatio>.8&&r.offsetHeight>4&&!r.closest('.np-stk:not(.open) .np-kids');
    if(widac&&r.classList.contains('unread')&&ok()){
      if(!NP_SEEN.t.has(r))NP_SEEN.t.set(r,setTimeout(()=>{
        NP_SEEN.t.delete(r);
        if(!document.contains(r)||!ok())return;
        NP_SEEN.q.push(...npIds(r));
        clearTimeout(NP_SEEN.flush);
        NP_SEEN.flush=setTimeout(()=>{const ids=NP_SEEN.q;NP_SEEN.q=[];onSeen(ids)},250);
      },2000));
    }else if(NP_SEEN.t.has(r)){clearTimeout(NP_SEEN.t.get(r));NP_SEEN.t.delete(r)}
  }),{root,threshold:[0,.8,1]});
  rows.forEach(r=>NP_SEEN.io.observe(r));
}

/* Gesty. Przesunięcie wiersza w lewo = usuń (za 1/3 wiersza albo szybkim
   machnięciem — od razu, z Undo), w prawo =
   przeczytane/nieprzeczytane; myszą też działa. Na telefonie arkusz zsuwa się
   w dół za uchwyt. Przeciągnięcie kasuje stuknięcie, jak w iOS: wciśnięcie na
   przycisku i zjechanie z niego nie może skończyć się klikiem w WIERSZ (click
   trafia wtedy we wspólnego przodka). */
const NP_G={sw:null,drag:null,press:null,suppress:false,cfg:null};
const npSwiping=()=>!!(NP_G.sw&&NP_G.sw.on);
const npSlide=(rb,x)=>{rb.dataset.off=x;rb.style.transform=x?`translateX(${x}px)`:''};
/* Próg usunięcia: tyle co w iOS Mail — jedna trzecia wiersza, nie ponad
   połowa. Pomyłkę łapie Undo, a nie długość ruchu. */
const npDelAt=W=>Math.max(96,W*.32);
const npBuzz=()=>{try{navigator.vibrate&&navigator.vibrate(8)}catch(_){}};
/* Wiersz `.leaving` właśnie odjeżdża po usunięciu — zamykanie innych
   swipe'ów nie może go ściągnąć z powrotem (wracał i zwijał się naraz). */
function npCloseSwipes(opr){
  const c=NP_G.cfg;if(!c)return;
  npQ(c.items+' .np-rb[data-off]').forEach(rb=>{if(rb!==opr&&+rb.dataset.off&&!rb.closest('.leaving'))npSlide(rb,0)});
}
/* ---------- push na tym urządzeniu ----------
   Jeden stan (PUSH), malowany na KAŻDYM przełączniku, który go pokazuje
   (dzwonek, Settings). Dawniej każde otwarcie/przełączenie zakładki budowało
   kartę od zera z napisem „Enable" i dopiero po odpytaniu service workera
   wracała do „Disable" — przełącznik przeskakiwał. Ostatni znany stan leży w
   localStorage, więc od razu widać prawdę, a sprawdzenie tylko ją potwierdza.
   Tekst „włączone" dopowiada aplikacja przez `npPushOnText()`, jeśli ją ma. */
const PUSH={st:(()=>{try{const s=localStorage.getItem('pf_push_st');return s&&s!=='pending'?s:'unknown'}catch(_){return 'unknown'}})(),
  target:null,key:null};
const npB64=b64=>{const p='='.repeat((4-b64.length%4)%4);
  const raw=atob((b64+p).replace(/-/g,'+').replace(/_/g,'/'));
  return Uint8Array.from(raw,c=>c.charCodeAt(0))};
const NP_PUSH_TXT={
  on:()=>typeof npPushOnText==='function'?npPushOnText():'On · this device buzzes',
  off:()=>'Off · alerts only show up in this list',
  pending:()=>PUSH.target?'Turning on…':'Turning off…',
  unknown:()=>'Checking this device…',
  ios:()=>'Add the app to your Home Screen to get push',
  unsupported:()=>'This browser can\'t receive push',
  denied:()=>'Blocked for this site in browser settings',
  nokey:()=>'Push isn\'t configured on the server',
};
const npPushInner=()=>`<span class="np-pr-ic"><span class="np-ic-laptop">${npI('laptop')}</span><span class="np-ic-phone">${npI('phone')}</span></span>
  <div class="np-pr-t"><b>Push on this device</b><span data-np-push-txt></span></div>
  <button type="button" class="np-sw" role="switch" data-np="push" aria-label="Push notifications on this device"><i></i></button>
  <span class="np-pr-go">${npI('chevR')}</span>`;
function npPushHelp(){
  const st=PUSH.st;
  if(st==='ios')return `<ol class="np-guide">
    <li><span class="np-gi">${npI('share')}</span><span>Open this site in <b>Safari</b> and tap <b>Share</b></span></li>
    <li><span class="np-gi">${npI('plusSq')}</span><span>Choose <b>Add to Home Screen</b></span></li>
    <li><span class="np-gi">${npI('bell')}</span><span>Open the app from the Home Screen and switch push on here</span></li></ol>`;
  if(st==='denied')return '<p class="np-note">Notifications are blocked for this site. Open the site settings (the icon left of the address bar) → Notifications → Allow, then reload.</p>';
  if(st==='nokey')return '<p class="np-note">Push keys are missing on the server, so push can\'t reach any device yet.</p>';
  if(st==='unsupported')return '<p class="np-note">This browser doesn\'t support web push. Chrome, Edge, Firefox and Safari 16.4+ do.</p>';
  return '';
}
function npPaintPush(){
  const st=PUSH.st;
  npQ('[data-push]').forEach(el=>el.dataset.push=st);
  npQ('[data-np-push-txt]').forEach(el=>el.textContent=(NP_PUSH_TXT[st]||NP_PUSH_TXT.unknown)());
  npQ('[data-np="push"]').forEach(sw=>{
    sw.setAttribute('aria-checked',String(st==='pending'?!!PUSH.target:st==='on'));
    sw.classList.toggle('pending',st==='pending');
    sw.hidden=!['on','off','pending','unknown'].includes(st);
    sw.disabled=st==='unknown';
  });
  npQ('[data-np-push-help]').forEach(el=>el.innerHTML=npPushHelp());
}
function npPushSave(st){PUSH.st=st;try{localStorage.setItem('pf_push_st',st)}catch(_){}npPaintPush()}
async function refreshPush(){
  let st;
  const ok='serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
  if(!ok)st=/iPhone|iPad|iPod/.test(navigator.userAgent)?'ios':'unsupported';
  else{
    if(!PUSH.key){
      let cfg;
      try{cfg=await api('/api/push/public-key')}catch(_){npPaintPush();return}
      if(!cfg.enabled)st='nokey';else PUSH.key=cfg.key;
    }
    if(!st){
      if(Notification.permission==='denied')st='denied';
      else{
        try{
          const reg=await Promise.race([navigator.serviceWorker.ready,
            new Promise((_,z)=>setTimeout(()=>z(new Error('sw')),4000))]);
          st=(await reg.pushManager.getSubscription())?'on':'off';
        }catch(_){npPaintPush();return}
      }
    }
  }
  if(PUSH.st==='pending')return;
  npPushSave(st);
}
async function npTogglePush(){
  if(!['on','off'].includes(PUSH.st))return;
  const chce=PUSH.st==='off';
  PUSH.target=chce;PUSH.st='pending';npPaintPush();
  let st=chce?'off':'on';
  try{
    const reg=await navigator.serviceWorker.ready;
    let sub=await reg.pushManager.getSubscription();
    if(!chce){
      if(sub){
        await api('/api/me/push/unsubscribe',{method:'POST',body:JSON.stringify({endpoint:sub.endpoint})});
        await sub.unsubscribe();
      }
      st='off';
    }else{
      const perm=await Notification.requestPermission();
      if(perm==='denied')st='denied';
      else if(perm!=='granted'){st='off';toast('Notifications were not allowed.','err')}
      else{
        if(!PUSH.key){const cfg=await api('/api/push/public-key');PUSH.key=cfg.key}
        sub=sub||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:npB64(PUSH.key)});
        await api('/api/me/push/subscribe',{method:'POST',body:JSON.stringify(sub.toJSON())});
        st='on';
      }
    }
  }catch(e){toast('Push setup failed: '+e.message,'err')}
  PUSH.target=null;npPushSave(st);
}

/* Gesty panelu. Trzy różne ruchy, rozróżnione MIEJSCEM i KIERUNKIEM:
   - na powiadomieniu w bok: przeczytane (w prawo) / usuń (w lewo);
   - w panelu poza powiadomieniem w bok: poprzednia/następna zakładka filtra;
   - w dół od góry (uchwyt, pasek, albo lista przewinięta do samej góry):
     zsunięcie arkusza na telefonie.
   Zsuwanie idzie na zdarzeniach touch z preventDefault, bo na zdarzeniach
   pointer przeglądarka zabierała gest sobie: przewijała stronę pod spodem
   albo odświeżała ją pociągnięciem w dół. */
function npGestures(cfg){
  NP_G.cfg=cfg;
  const panel=()=>document.querySelector(cfg.panel);
  const kontrolka=el=>el.closest('button,a,input,select,textarea,label,[role=switch]');
  addEventListener('pointerdown',e=>{
    if(!e.target.closest)return;
    NP_G.press=e.target.closest(cfg.panel)?{x:e.clientX,y:e.clientY,pid:e.pointerId}:null;
    if(!cfg.open()||e.button!==0)return;
    const rb=e.target.closest(cfg.items+' .np-rb');
    if(rb&&!cfg.editing()&&!kontrolka(e.target)){
      NP_G.sw={rb,row:rb.closest('.np-row'),x:e.clientX,y:e.clientY,base:+(rb.dataset.off||0),dx:0,on:false,pid:e.pointerId};
      return;
    }
    if(!rb&&cfg.onTabSwipe&&cfg.list&&e.target.closest(cfg.list)&&!cfg.editing()&&!e.target.closest('input,select,textarea'))
      NP_G.tab={x:e.clientX,y:e.clientY,pid:e.pointerId,dx:0,on:false,el:null};
    // Myszą (np. wąskie okno na komputerze) zsuwa się za uchwyt; palcem — niżej, przez touch.
    const uchwyt=e.target.closest(cfg.panel+' [data-np-drag]');
    if(e.pointerType==='mouse'&&uchwyt&&npNarrow()&&!kontrolka(e.target))
      NP_G.drag={y:e.clientY,t:performance.now(),pid:e.pointerId,dy:0};
  });
  addEventListener('pointermove',e=>{
    const d=NP_G.drag;
    if(d&&e.pointerId===d.pid){
      d.dy=Math.max(0,e.clientY-d.y);
      const np=panel();
      if(d.dy>3&&np){np.classList.add('dragging');np.style.transform=`translateY(${d.dy}px)`}
      return;
    }
    const tb=NP_G.tab;
    if(tb&&e.pointerId===tb.pid){
      const dx=e.clientX-tb.x,dy=e.clientY-tb.y;
      if(!tb.on){
        if(Math.abs(dx)<10){if(Math.abs(dy)>10)NP_G.tab=null;return}
        if(Math.abs(dy)>Math.abs(dx)*.8){NP_G.tab=null;return}
        tb.on=true;tb.el=document.querySelector(cfg.items);
        if(!tb.el){NP_G.tab=null;return}
        tb.el.getAnimations().forEach(a=>a.cancel());
        npCloseSwipes();
      }
      tb.dx=dx;
      tb.el.style.transform=`translateX(${dx*.35}px)`;
      tb.el.style.opacity=String(1-Math.min(.35,Math.abs(dx)/700));
      return;
    }
    const s=NP_G.sw;
    if(!s||e.pointerId!==s.pid)return;
    const dx=e.clientX-s.x,dy=e.clientY-s.y;
    if(!s.on){
      if(Math.abs(dx)<8){if(Math.abs(dy)>8)NP_G.sw=null;return}
      if(Math.abs(dy)>Math.abs(dx)){NP_G.sw=null;return}
      s.on=true;npCloseSwipes(s.rb);s.rb.classList.add('drag');
      try{s.rb.setPointerCapture(e.pointerId)}catch(_){}
    }
    const W=s.rb.offsetWidth;let x=s.base+dx;
    if(x>0)x=110*(1-Math.exp(-x/110));
    if(x<-W)x=-W;
    const t=performance.now();
    if(s.pt!=null)s.v=.6*((x-s.dx)/Math.max(1,t-s.pt))+.4*(s.v||0);
    s.pt=t;s.dx=x;s.rb.style.transform=`translateX(${x}px)`;
    // Pod spodem tylko tło strony, w którą jedzie wiersz (dawniej połowa
    // niebieska, połowa czerwona); --sw prowadzi napis Delete za krawędzią.
    s.row.classList.toggle('sw-l',x<0);s.row.classList.toggle('sw-r',x>0);
    s.row.style.setProperty('--sw',Math.max(0,-x)+'px');
    const del=x<-npDelAt(W),read=x>56;
    if((del&&!s.row.classList.contains('arm-del'))||(read&&!s.row.classList.contains('arm-read')))npBuzz();
    s.row.classList.toggle('arm-del',del);
    s.row.classList.toggle('arm-read',read);
  });
  const koniec=e=>{
    const pr=NP_G.press;
    if(pr&&e.pointerId===pr.pid){
      NP_G.press=null;
      if(Math.hypot(e.clientX-pr.x,e.clientY-pr.y)>8){NP_G.suppress=true;setTimeout(()=>{NP_G.suppress=false},0)}
    }
    const d=NP_G.drag;
    if(d&&e.pointerId===d.pid){
      NP_G.drag=null;
      const np=panel();
      if(np){np.classList.remove('dragging');np.style.transform=''}
      const v=d.dy/Math.max(1,performance.now()-d.t);
      if(d.dy>120||(d.dy>30&&v>.6))cfg.onDismiss();
      return;
    }
    const tb=NP_G.tab;
    if(tb&&e.pointerId===tb.pid){
      NP_G.tab=null;
      if(!tb.on)return;
      const el=tb.el,dx=tb.dx;
      el.style.transform='';el.style.opacity='';
      const zmiana=Math.abs(dx)>60&&e.type==='pointerup'?cfg.onTabSwipe(dx<0?1:-1):false;
      if(!zmiana&&!npRM())
        el.animate([{transform:`translateX(${dx*.35}px)`},{transform:'none'}],{duration:420,easing:'cubic-bezier(.22,1,.36,1)'});
      return;
    }
    const s=NP_G.sw;
    if(!s||e.pointerId!==s.pid)return;
    NP_G.sw=null;
    if(!s.on)return;
    s.rb.classList.remove('drag');s.row.classList.remove('arm-del','arm-read');
    const W=s.rb.offsetWidth,x=s.dx,up=e.type==='pointerup';
    // Szybkie machnięcie w lewo usuwa jak pełny ruch; krótkie puszczenie
    // wraca sprężyną — bez pół-otwartego stanu, który trzeba było dostukać.
    const flick=x<-48&&(s.v||0)<-.5;
    if(up&&(x<-npDelAt(W)||flick)){s.row.classList.add('leaving');npSlide(s.rb,-W);cfg.onAct('del',s.row)}
    else if(up&&x>56){npSlide(s.rb,0);cfg.onAct('read',s.row)}
    else npSlide(s.rb,0);
    if(cfg.onEnd)cfg.onEnd();
  };
  addEventListener('pointerup',koniec);
  addEventListener('pointercancel',koniec);

  /* Zsuwanie arkusza palcem (telefon). */
  document.addEventListener('touchstart',e=>{
    const np=panel();
    if(!np||!cfg.open()||!npNarrow()||e.touches.length!==1||!np.contains(e.target))return;
    const uchwyt=e.target.closest('[data-np-drag]'),sc=e.target.closest('.np-scroll');
    if(!uchwyt&&!(sc&&sc.scrollTop<=0))return;
    const t=e.touches[0];
    NP_G.td={x:t.clientX,y:t.clientY,t:performance.now(),dy:0,on:false,sc};
  },{passive:true});
  document.addEventListener('touchmove',e=>{
    const d=NP_G.td;
    if(!d)return;
    const t=e.touches[0],dx=t.clientX-d.x,dy=t.clientY-d.y;
    if(!d.on){
      if(Math.abs(dy)<6&&Math.abs(dx)<6)return;
      if(dy<=0||Math.abs(dx)>Math.abs(dy)||(d.sc&&d.sc.scrollTop>0)){NP_G.td=null;return}
      d.on=true;NP_G.sw=null;NP_G.tab=null;npCloseSwipes();
      panel().classList.add('dragging');
    }
    e.preventDefault();
    d.dy=Math.max(0,dy);
    panel().style.transform=`translateY(${d.dy}px)`;
  },{passive:false});
  const zsunKoniec=()=>{
    const d=NP_G.td;NP_G.td=null;
    if(!d||!d.on)return;
    NP_G.suppress=true;setTimeout(()=>{NP_G.suppress=false},0);
    const np=panel();
    const v=d.dy/Math.max(1,performance.now()-d.t);
    np.classList.remove('dragging');np.style.transform='';
    if(d.dy>110||(d.dy>30&&v>.5))cfg.onDismiss();
  };
  document.addEventListener('touchend',zsunKoniec);
  document.addEventListener('touchcancel',zsunKoniec);

  addEventListener('click',e=>{
    if(NP_G.suppress){NP_G.suppress=false;e.preventDefault();e.stopPropagation()}
  },true);
  addEventListener('scroll',e=>{
    const sc=e.target;
    if(sc&&sc.classList&&sc.classList.contains('np-scroll')){
      sc.closest('.np-pg').classList.toggle('scrolled',sc.scrollTop>30);
      npCloseSwipes();
    }
  },true);
}
