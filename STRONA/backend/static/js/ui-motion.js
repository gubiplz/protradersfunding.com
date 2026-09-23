/* Ruch w panelu admina i portalu tradera — subtelnie, jak w iOS.
   Samodzielny: nie zna widoków, patrzy na DOM (MutationObserver) i dokłada
   animacje przez Web Animations API, więc nie rusza stylów ani logiki reszty.
   - segmenty (.seg, .shop-tabs): pigułka przesuwa się pod aktywną opcję,
     także gdy widok przerysował filtr od zera (pamięć pozycji per miejsce);
   - pasek boczny: podświetlenie ślizga się do nowej pozycji;
   - zmiana zakładki: treść wjeżdża lekko z dołu, karty i wiersze kaskadą,
     liczby w kafelkach dobiegają do wartości;
   - górny pasek dostaje kreskę i rozmycie dopiero po przewinięciu;
   - zmiana motywu: przenikanie (View Transitions), jeśli przeglądarka umie.
   `prefers-reduced-motion` wyłącza wszystko poza natychmiastowym ustawieniem. */
(()=>{
'use strict';
const RM=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const EASE='cubic-bezier(.22,1,.36,1)';
const SPRING='linear(0,.0048,.0185 1.6%,.0735 3.3%,.2837 7.4%,.5128 11.7%,.7052 16.4%,.8398 21.3%,.9291 26.6%,.9826 32.3%,1.0115 38.4%,1.0228 45.3%,1.0212 52.3%,1.0114 62.2%,1.0022 76.5%,1)';
const widok=()=>{try{return typeof VIEW!=='undefined'?VIEW:(window._view||'')}catch(_){return window._view||''}};

/* ---------- segmenty: przesuwana pigułka ---------- */
const SEG='.seg,.shop-tabs';
const pamiec=new Map();
const kluczSeg=seg=>widok()+':'+[...document.querySelectorAll(SEG)].indexOf(seg);
function pigulka(seg,animuj=true){
  const on=seg.querySelector(':scope>button.on,:scope>.shop-tab.on');
  let p=seg.querySelector(':scope>.ui-pill');
  if(!on){if(p)p.style.opacity='0';return}
  if(!p){p=document.createElement('span');p.className='ui-pill';p.setAttribute('aria-hidden','true');seg.prepend(p);seg.classList.add('has-pill')}
  const cel={x:on.offsetLeft,y:on.offsetTop,w:on.offsetWidth,h:on.offsetHeight};
  const k=kluczSeg(seg),byl=p.dataset.x!=null?{x:+p.dataset.x,y:+p.dataset.y,w:+p.dataset.w,h:+p.dataset.h}:pamiec.get(k);
  const ustaw=r=>{p.style.transform=`translate(${r.x}px,${r.y}px)`;p.style.width=r.w+'px';p.style.height=r.h+'px'};
  p.style.opacity='1';
  if(animuj&&byl&&!RM()&&(byl.x!==cel.x||byl.y!==cel.y||byl.w!==cel.w)){
    p.getAnimations().forEach(a=>a.cancel());
    p.animate([{transform:`translate(${byl.x}px,${byl.y}px)`,width:byl.w+'px',height:byl.h+'px'},
               {transform:`translate(${cel.x}px,${cel.y}px)`,width:cel.w+'px',height:cel.h+'px'}],
              {duration:520,easing:SPRING});
  }
  ustaw(cel);
  Object.assign(p.dataset,{x:cel.x,y:cel.y,w:cel.w,h:cel.h});
  pamiec.set(k,cel);
}

/* ---------- pasek boczny: ślizgające się podświetlenie ---------- */
function slizg(nav,animuj=true){
  const on=nav.querySelector(':scope>.sb-link.on');
  let g=nav.querySelector(':scope>.ui-glider');
  if(!on||on.offsetParent===null){if(g)g.style.opacity='0';return}
  if(!g){g=document.createElement('span');g.className='ui-glider';g.setAttribute('aria-hidden','true');nav.prepend(g);nav.classList.add('has-glider')}
  const cel={y:on.offsetTop,h:on.offsetHeight,x:on.offsetLeft,w:on.offsetWidth};
  const byl=g.dataset.y!=null?{y:+g.dataset.y,h:+g.dataset.h}:null;
  g.style.opacity='1';
  if(animuj&&byl&&!RM()&&byl.y!==cel.y){
    g.getAnimations().forEach(a=>a.cancel());
    g.animate([{transform:`translate(${cel.x}px,${byl.y}px)`,height:byl.h+'px'},
               {transform:`translate(${cel.x}px,${cel.y}px)`,height:cel.h+'px'}],{duration:480,easing:SPRING});
  }
  g.style.transform=`translate(${cel.x}px,${cel.y}px)`;g.style.width=cel.w+'px';g.style.height=cel.h+'px';
  g.dataset.y=cel.y;g.dataset.h=cel.h;
}

/* ---------- wejście widoku ---------- */
let czekaNaWidok=false;
const _go=window.go;
if(typeof _go==='function'){
  window.go=function(v,...reszta){
    if(v!==widok())czekaNaWidok=true;
    return _go.call(this,v,...reszta);
  };
}
function liczba(el){
  const t=[...el.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());
  if(!t)return;
  const m=t.textContent.match(/^(\s*[+\-−]?\$?)([\d,]+(?:\.\d+)?)(.*)$/s);
  if(!m)return;
  const cel=parseFloat(m[2].replace(/,/g,''));
  if(!isFinite(cel)||cel===0)return;
  const dz=(m[2].split('.')[1]||'').length,start=performance.now(),czas=650;
  const fmt=v=>v.toLocaleString('en-US',{minimumFractionDigits:dz,maximumFractionDigits:dz,useGrouping:m[2].includes(',')});
  const krok=now=>{
    const f=Math.min(1,(now-start)/czas),e=1-Math.pow(1-f,3);
    t.textContent=m[1]+fmt(cel*e)+m[3];
    if(f<1)requestAnimationFrame(krok);
  };
  requestAnimationFrame(krok);
}
function wejscie(view){
  if(RM())return;
  view.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'none'}],{duration:420,easing:EASE});
  const kafle=[...view.querySelectorAll('.stat-tile,.todo,.sec-card,.tbl-wrap,.acc-card,.lead-card,.ticket-row,.kpi')]
    .filter(el=>el.getBoundingClientRect().top<innerHeight).slice(0,14);
  kafle.forEach((el,i)=>el.animate([{opacity:0,transform:'translateY(10px) scale(.985)'},{opacity:1,transform:'none'}],
    {duration:520,delay:40+i*35,easing:SPRING,fill:'backwards'}));
  const wiersze=[...view.querySelectorAll('tbody tr')].slice(0,16);
  wiersze.forEach((el,i)=>el.animate([{opacity:0},{opacity:1}],{duration:320,delay:80+i*22,easing:EASE,fill:'backwards'}));
  view.querySelectorAll('.stat-tile .val,.todo .n').forEach(liczba);
}

/* Komórki tabeli bez treści (same spacje albo „—"): na telefonie tabela staje
   się kartami i takie komórki robiły puste pasy. `:empty` nie łapie spacji. */
function puste(root){
  root.querySelectorAll('table.rtbl td:not(.rt-main):not(.rt-acts)').forEach(td=>{
    const tekst=td.textContent.replace(/\s+/g,'');
    const nic=(!tekst||/^[—–-]$/.test(tekst))&&!td.querySelector('img,svg,button,input,select,textarea,a[href]');
    td.classList.toggle('ui-empty',nic);
  });
}

/* ---------- obserwator ---------- */
let zaplanowane=false;const doSeg=new Set();let doNav=false,doWidok=false;
function planuj(){
  if(zaplanowane)return;zaplanowane=true;
  requestAnimationFrame(()=>{
    zaplanowane=false;
    doSeg.forEach(s=>{if(document.contains(s))pigulka(s)});doSeg.clear();
    if(doNav){doNav=false;document.querySelectorAll('.side-nav').forEach(n=>slizg(n))}
    if(doWidok){
      doWidok=false;
      const v=document.getElementById('view');
      if(v)puste(v);
      if(v&&czekaNaWidok&&!v.querySelector('.view-load')&&v.children.length){czekaNaWidok=false;wejscie(v)}
    }
  });
}
new MutationObserver(ms=>{
  for(const m of ms){
    const t=m.target;
    if(m.type==='attributes'){
      if(t.parentElement&&t.parentElement.matches&&t.parentElement.matches(SEG))doSeg.add(t.parentElement);
      if(t.classList&&t.classList.contains('sb-link'))doNav=true;
      continue;
    }
    if(t.id==='view'||(t.closest&&t.closest('#view')))doWidok=true;
    if(t.matches&&t.matches('.side-nav'))doNav=true;
    m.addedNodes.forEach(n=>{
      if(n.nodeType!==1)return;
      if(n.matches(SEG))doSeg.add(n);
      n.querySelectorAll&&n.querySelectorAll(SEG).forEach(s=>doSeg.add(s));
    });
  }
  planuj();
}).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
addEventListener('resize',()=>{
  document.querySelectorAll(SEG).forEach(s=>pigulka(s,false));
  document.querySelectorAll('.side-nav').forEach(n=>slizg(n,false));
});
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll(SEG).forEach(s=>pigulka(s,false));
  document.querySelectorAll('.side-nav').forEach(n=>slizg(n,false));
});

/* ---------- okna zsuwane palcem w dół (telefon) ----------
   To samo co w dzwonku, dla każdego okna od dołu: szuflada admina (bilet,
   karta konta), menu „More" w portalu, arkusz sortowania, okna modalne.
   Ciągnie się za dowolne miejsce, o ile treść pod palcem jest przewinięta do
   samej góry — inaczej palec przewija treść, jak w iOS. Zamyka się tą samą
   drogą co przycisk (closeOver / closeSheet / × w nagłówku okna), więc okno
   sprząta po sobie tak samo. Potwierdzenie (#ask-modal) zostaje: czeka na
   odpowiedź i zsunięcie bez niej zawiesiłoby decyzję. */
const OKNA=[
  {sel:'#over.open .over-panel',maxW:640,zamknij:()=>{if(typeof window.closeOver==='function')window.closeOver()}},
  {sel:'.sheet.open',zamknij:()=>{if(typeof window.closeSheet==='function')window.closeSheet()}},
  {sel:'.sk-sheet.open .sk-panel',zamknij:el=>{const v=el.parentNode.querySelector('.sk-veil');if(v)v.click()}},
  {sel:'.modal-wrap:not(#ask-modal) > .modal',zamknij:el=>{
    const x=el.querySelector('.modal-head [aria-label="Close"],.modal-head .icon-btn,.modal-x');
    if(x)x.click();else{const w=el.closest('.modal-wrap');if(w)w.remove()}}},
];
let zsuw=null;
function przewinieteWGore(od,panel){
  for(let n=od;n&&n!==panel.parentNode;n=n.parentElement){
    if(n.scrollTop>0&&/(auto|scroll)/.test(getComputedStyle(n).overflowY))return false;
  }
  return true;
}
document.addEventListener('touchstart',e=>{
  if(e.touches.length!==1||!e.target.closest||e.target.closest('#np'))return;
  if(e.target.closest('input,textarea,select,[contenteditable]'))return;
  for(const o of OKNA){
    if(o.maxW&&innerWidth>o.maxW)continue;
    const panel=e.target.closest(o.sel);
    if(!panel)continue;
    if(!przewinieteWGore(e.target,panel))return;
    const t=e.touches[0];
    zsuw={o,panel,x:t.clientX,y:t.clientY,t:performance.now(),dy:0,on:false};
    return;
  }
},{passive:true});
document.addEventListener('touchmove',e=>{
  const z=zsuw;if(!z)return;
  const t=e.touches[0],dx=t.clientX-z.x,dy=t.clientY-z.y;
  if(!z.on){
    if(Math.abs(dy)<6&&Math.abs(dx)<6)return;
    if(dy<=0||Math.abs(dx)>Math.abs(dy)){zsuw=null;return}
    z.on=true;z.panel.getAnimations().forEach(a=>a.cancel());
    z.panel.style.transition='none';
  }
  e.preventDefault();
  z.dy=dy;
  z.panel.style.transform=`translateY(${Math.max(0,dy)}px)`;
},{passive:false});
function zsuwKoniec(){
  const z=zsuw;zsuw=null;
  if(!z||!z.on)return;
  const v=z.dy/Math.max(1,performance.now()-z.t),p=z.panel;
  if(z.dy>110||(z.dy>35&&v>.5)){
    p.style.transition=RM()?'none':'transform .24s cubic-bezier(.4,0,1,1)';
    p.style.transform=`translateY(${innerHeight}px)`;
    setTimeout(()=>{z.o.zamknij(p);p.style.transition='';p.style.transform=''},RM()?0:230);
  }else{
    p.style.transition=RM()?'none':'transform .45s '+EASE;
    p.style.transform='';
    setTimeout(()=>{p.style.transition=''},460);
  }
}
document.addEventListener('touchend',zsuwKoniec);
document.addEventListener('touchcancel',zsuwKoniec);

/* ---------- górny pasek po przewinięciu ---------- */
const pasek=()=>document.querySelector('.topbar');
addEventListener('scroll',()=>{const p=pasek();if(p)p.classList.toggle('ui-scrolled',scrollY>4)},{passive:true});

/* ---------- motyw: przenikanie ---------- */
const _theme=window.toggleTheme;
if(typeof _theme==='function'&&document.startViewTransition){
  window.toggleTheme=function(...a){
    if(RM())return _theme.apply(this,a);
    document.startViewTransition(()=>_theme.apply(this,a));
  };
}
})();
