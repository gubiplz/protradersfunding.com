/* Ruch na stronie publicznej (landing, FAQ, objectives…) — subtelnie, jak w iOS.
   Osobny plik obok site.js (tamten ma budżet rozmiaru pilnowany testem).
   Nie zna logiki strony: patrzy na DOM i dokłada animacje.
   - przełączniki konfiguratora i cennika: pigułka z gradientem jedzie do
     wybranej opcji, także gdy sekcja przerysowała się od zera po kliknięciu;
   - FAQ: <details> otwiera się i zamyka płynnie zamiast skoku;
   - menu na telefonie: wjeżdża z kaskadą pozycji;
   - poświata w hero: łagodna paralaksa przy przewijaniu.
   `prefers-reduced-motion` = bez ruchu. */
(()=>{
'use strict';
const RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
const SPRING='linear(0,.0048,.0185 1.6%,.0735 3.3%,.2837 7.4%,.5128 11.7%,.7052 16.4%,.8398 21.3%,.9291 26.6%,.9826 32.3%,1.0115 38.4%,1.0228 45.3%,1.0212 52.3%,1.0114 62.2%,1.0022 76.5%,1)';
const EASE='cubic-bezier(.22,1,.36,1)';

/* ---------- pigułka w przełącznikach ---------- */
const GRUPY=[['#cfg-tabs','.cfg-tab'],['#pcfg-toggle','.ptog'],['#pcfg-sizes','.psize']];
const pamiec=new Map();
function pigulka(box,przycisk){
  const on=box.querySelector(przycisk+'.on');
  let p=box.querySelector(':scope>.sm-pill');
  if(!on){if(p)p.style.opacity='0';return}
  if(!p){p=document.createElement('span');p.className='sm-pill';p.setAttribute('aria-hidden','true');box.prepend(p);box.classList.add('sm-has-pill')}
  const cel={x:on.offsetLeft,y:on.offsetTop,w:on.offsetWidth,h:on.offsetHeight};
  const byl=pamiec.get(box.id);
  p.style.borderRadius=getComputedStyle(on).borderRadius;
  p.style.opacity='1';
  if(byl&&!RM&&(byl.x!==cel.x||byl.y!==cel.y||byl.w!==cel.w)){
    p.animate([{transform:`translate(${byl.x}px,${byl.y}px)`,width:byl.w+'px',height:byl.h+'px'},
               {transform:`translate(${cel.x}px,${cel.y}px)`,width:cel.w+'px',height:cel.h+'px'}],{duration:540,easing:SPRING});
    on.animate([{transform:'scale(.96)'},{transform:'none'}],{duration:420,easing:SPRING});
  }
  p.style.transform=`translate(${cel.x}px,${cel.y}px)`;p.style.width=cel.w+'px';p.style.height=cel.h+'px';
  pamiec.set(box.id,cel);
}
function wszystkie(){GRUPY.forEach(([s,b])=>{const box=document.querySelector(s);if(box)pigulka(box,b)})}
new MutationObserver(()=>requestAnimationFrame(wszystkie))
  .observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
addEventListener('resize',()=>{pamiec.clear();wszystkie()});
addEventListener('load',wszystkie);

/* ---------- FAQ: płynne rozwijanie ---------- */
document.addEventListener('click',e=>{
  const s=e.target.closest&&e.target.closest('.faq summary');
  if(!s||RM)return;
  const d=s.parentElement;
  if(d.dataset.anim)return e.preventDefault();
  e.preventDefault();
  const start=d.offsetHeight;
  d.dataset.anim='1';
  if(d.open){
    const koniec=s.offsetHeight;
    const a=d.animate([{height:start+'px'},{height:koniec+'px'}],{duration:320,easing:EASE});
    a.onfinish=()=>{d.open=false;delete d.dataset.anim};
  }else{
    d.open=true;
    const koniec=d.offsetHeight;
    const a=d.animate([{height:start+'px'},{height:koniec+'px'}],{duration:420,easing:SPRING});
    const tresc=d.querySelector('.a');
    if(tresc)tresc.animate([{opacity:0,transform:'translateY(-4px)'},{opacity:1,transform:'none'}],{duration:360,delay:60,easing:EASE,fill:'backwards'});
    a.onfinish=()=>{delete d.dataset.anim};
  }
});

/* ---------- menu na telefonie: kaskada pozycji ---------- */
const menu=document.getElementById('mobileMenu');
if(menu&&!RM){
  new MutationObserver(()=>{
    if(!menu.classList.contains('open'))return;
    menu.animate([{opacity:0,transform:'translateY(-10px)'},{opacity:1,transform:'none'}],{duration:420,easing:SPRING});
    [...menu.children].forEach((el,i)=>el.animate([{opacity:0,transform:'translateY(-6px)'},{opacity:1,transform:'none'}],
      {duration:380,delay:40+i*30,easing:EASE,fill:'backwards'}));
  }).observe(menu,{attributes:true,attributeFilter:['class']});
}

/* ---------- poświata w hero: paralaksa ---------- */
const glow=document.querySelector('.hero-glow');
if(glow&&!RM){
  let tik=false;
  addEventListener('scroll',()=>{
    if(tik)return;tik=true;
    requestAnimationFrame(()=>{tik=false;const y=Math.min(scrollY,900);glow.style.transform=`translate3d(0,${y*.22}px,0)`});
  },{passive:true});
}
})();
