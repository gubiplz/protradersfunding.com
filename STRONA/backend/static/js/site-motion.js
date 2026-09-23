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

/* ---------- wybór w konfiguratorze i cenniku ----------
   Sekcja przerysowuje się od zera po każdym kliknięciu, więc tło nie może
   „jechać" pod przyciskami (napis zmieniał kolor od razu, tło docierało
   później — wyglądało nienaturalnie). Zamiast tego wybrana opcja zapala się
   W MIEJSCU krótkim sprężystym wciśnięciem, a karta zmienia wysokość płynnie. */
const GRUPY=[['#cfg-tabs','.cfg-tab','step'],['#pcfg-toggle','.ptog','t'],
             ['#pcfg-sizes','.psize','s'],['#cfg-sizes','.cfg-size','key']];
const wybrane=new Map();
function sprawdz(){
  GRUPY.forEach(([sel,btn,klucz])=>{
    const box=document.querySelector(sel);if(!box)return;
    const on=box.querySelector(btn+'.on'),val=on?on.dataset[klucz]:null;
    const byl=wybrane.get(sel);
    if(byl!==undefined&&val!==byl&&on&&!RM)
      on.animate([{transform:'scale(.94)',filter:'brightness(1.12)'},{transform:'none',filter:'none'}],{duration:440,easing:SPRING});
    wybrane.set(sel,val);
  });
}
new MutationObserver(sprawdz).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
addEventListener('load',sprawdz);

let ostatniKlik=0;
addEventListener('pointerdown',()=>{ostatniKlik=performance.now()},true);
if(!RM&&'ResizeObserver' in window){
  const ro=new ResizeObserver(es=>es.forEach(en=>{
    const el=en.target,h=el.offsetHeight,byl=+el.dataset.smH||0;
    el.dataset.smH=h;
    if(!byl||Math.abs(h-byl)<2||performance.now()-ostatniKlik>700)return;
    const ov=el.style.overflow;el.style.overflow='hidden';
    const a=el.animate([{height:byl+'px'},{height:h+'px'}],{duration:380,easing:EASE});
    a.onfinish=a.oncancel=()=>{el.style.overflow=ov};
  }));
  const podlacz=()=>['#pcfg','.prules','#cfg'].forEach(s=>{const el=document.querySelector(s);if(el&&!el.dataset.smRo){el.dataset.smRo='1';ro.observe(el)}});
  podlacz();addEventListener('load',podlacz);
}

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
