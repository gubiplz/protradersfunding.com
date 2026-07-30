/* ============================================================
   PHASE 2 — reward FX layer (window.RFX)
   Confetti, number roll-ups, pop, 3D flip and a queued full-screen
   celebration overlay. No dependencies beyond the optional GSAP
   global; every effect degrades to an instant end state under
   prefers-reduced-motion or when GSAP is missing.
   ============================================================ */
(function(){
'use strict';

const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
const hasGsap=()=>typeof gsap!=='undefined';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const PALETTE=['#6366f1','#8b5cf6','#a855f7','#fbbf24','#10b981'];
const GOLD=['#f59e0b','#fbbf24','#fde68a'];

/* ---------------- confetti (canvas, rAF) ---------------- */
let cv=null,ctx=null,parts=[],raf=0;

function canvasOn(){
  if(cv)return;
  cv=document.createElement('canvas');
  cv.id='fx-canvas';
  cv.style.cssText='position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:99';
  document.body.appendChild(cv);
  const d=Math.min(2,window.devicePixelRatio||1);
  cv.width=innerWidth*d; cv.height=innerHeight*d;
  ctx=cv.getContext('2d'); ctx.scale(d,d);
}
function canvasOff(){
  cancelAnimationFrame(raf); raf=0; parts=[];
  if(cv){cv.remove();cv=null;ctx=null}
}
function tick(){
  raf=requestAnimationFrame(tick);
  if(!ctx)return;
  ctx.clearRect(0,0,innerWidth,innerHeight);
  parts=parts.filter(p=>p.life<p.ttl);
  if(!parts.length){canvasOff();return}
  for(const p of parts){
    p.life++; p.vy+=p.g; p.vx*=.985; p.x+=p.vx; p.y+=p.vy; p.rot+=p.vr;
    ctx.save();
    ctx.globalAlpha=Math.max(0,1-p.life/p.ttl);
    ctx.translate(p.x,p.y); ctx.rotate(p.rot);
    ctx.fillStyle=p.color;
    ctx.fillRect(-2,-2,4,4);
    ctx.restore();
  }
}
/* burst at viewport coords; opts: {count<=80, palette} */
function confetti(x,y,opts){
  if(reduced)return;
  opts=opts||{};
  const pal=opts.palette||PALETTE;
  const n=Math.min(80,opts.count||60);
  canvasOn();
  if(parts.length>220)parts.splice(0,parts.length-140);   // overlapping bursts never pile up
  for(let i=0;i<n;i++){
    const a=Math.random()*Math.PI*2, sp=2+Math.random()*7;
    parts.push({x,y,
      vx:Math.cos(a)*sp, vy:Math.sin(a)*sp-4, g:.22,
      rot:Math.random()*Math.PI, vr:(Math.random()-.5)*.4,
      color:pal[(Math.random()*pal.length)|0],
      life:0, ttl:64+Math.random()*20});                  // ~1.1-1.4s at 60fps
  }
  if(!raf)tick();
}
/* burst from the center of an element */
function burstFrom(el,opts){
  if(!el||reduced)return;
  const r=el.getBoundingClientRect();
  confetti(r.left+r.width/2,r.top+r.height/2,opts);
}

/* ---------------- number roll-up ---------------- */
/* rollUp(el, 1234.5, {prefix:'$', formatter:fmt, duration:900})
   animates 92% -> 100% of the value, power2-out, plain rAF. */
function rollUp(el,value,opts){
  if(!el)return;
  opts=opts||{};
  const f=opts.formatter||(v=>v.toFixed(opts.decimals??0));
  const pre=opts.prefix||'', suf=opts.suffix||'';
  const done=()=>{el.textContent=pre+f(value)+suf};
  if(reduced||!isFinite(value)){done();return}
  const from=value*.92, dur=opts.duration||900, t0=performance.now();
  const step=t=>{
    const p=Math.min(1,(t-t0)/dur), e=1-Math.pow(1-p,3);
    el.textContent=pre+f(from+(value-from)*e)+suf;
    if(p<1)requestAnimationFrame(step); else done();
  };
  requestAnimationFrame(step);
}

/* ---------------- pop (scale bounce, 400ms) ---------------- */
function pop(el){
  if(!el||reduced||!hasGsap())return;
  gsap.timeline()
    .to(el,{scale:1.35,duration:.16,ease:'power2.out'})
    .to(el,{scale:1,duration:.24,ease:'back.out(1.7)',clearProps:'scale'});
}

/* ---------------- 3D flip (rotationY 90 -> swap -> 0) ---------------- */
/* flip(cardEl, swapFn, 600) -> Promise. Wrapper element provides perspective. */
function flip(el,swap,ms){
  return new Promise(res=>{
    if(!el||reduced||!hasGsap()){if(swap)swap();res();return}
    const d=(ms||600)/2000;
    gsap.timeline({onComplete:res})
      .to(el,{rotationY:90,duration:d,ease:'power2.in'})
      .add(()=>{if(swap)swap();gsap.set(el,{rotationY:-90})})
      .to(el,{rotationY:0,duration:d,ease:'power2.out',clearProps:'transform'});
  });
}

/* ---------------- celebration overlay (queued, never stacked) ---------------- */
let queue=Promise.resolve();

function celebrate(o){
  o=o||{};
  queue=queue.then(()=>showCelebrate(o)).catch(()=>{});
  return queue;
}
function showCelebrate(o){
  return new Promise(res=>{
    if(reduced){
      if(typeof o.toastFn==='function')o.toastFn((o.sub||'UNLOCKED')+': '+(o.title||''));
      if(o.onClose)o.onClose();
      res();return;
    }
    const v=document.createElement('div');
    v.className='celebrate-veil';
    v.innerHTML=`<div class="celebrate-box">
      <div class="celebrate-badge"><span class="celebrate-ring"></span><span class="celebrate-ic">${o.icon||''}</span></div>
      <div class="celebrate-sub">${esc(o.sub||'UNLOCKED')}</div>
      <h2 class="celebrate-title">${esc(o.title||'')}</h2>
      <button class="btn-p lg celebrate-btn" type="button">Continue</button>
    </div>`;
    document.body.appendChild(v);
    let closed=false;
    const close=()=>{
      if(closed)return; closed=true;
      if(hasGsap())gsap.to(v,{opacity:0,duration:.22,onComplete:()=>v.remove()});
      else v.remove();
      if(o.onClose)o.onClose();
      setTimeout(res,240);                                 // gap before the next queued one
    };
    v.addEventListener('click',close);                     // tap anywhere closes
    const badge=v.querySelector('.celebrate-badge');
    if(hasGsap()){
      gsap.from(v,{opacity:0,duration:.25});
      gsap.from(badge,{scale:0,duration:.7,ease:'back.out(1.7)'});
      gsap.from(v.querySelectorAll('.celebrate-sub,.celebrate-title,.celebrate-btn'),
        {opacity:0,y:12,duration:.4,delay:.28,stagger:.08});
    }
    setTimeout(()=>burstFrom(badge,{count:80,palette:GOLD}),350);
  });
}

window.RFX={reduced,confetti,burstFrom,rollUp,pop,flip,celebrate,PALETTE,GOLD};
})();
