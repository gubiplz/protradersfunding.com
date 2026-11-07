/* Privacy choices bar (templates/_consent.html): stores pf_consent, loads analytics after
   consent (window.pfLoadAnalytics from _analytics.html), keeps or drops the ?ref= partner code. */
(function(){
  var KEY='pf_consent', el=document.getElementById('pfCk');
  if(!el) return;
  var opts=document.getElementById('pfCkOpts'), act=document.getElementById('pfCkAct'), save=document.getElementById('pfCkSave');
  var an=document.getElementById('pfCkAn'), rf=document.getElementById('pfCkRef');
  function read(){
    try{
      var c=JSON.parse(localStorage.getItem(KEY)||'null');
      if(c&&typeof c==='object') return c;
      var old=localStorage.getItem('pf_cookie_ok');   // choice made under the old notice
      if(old!==null){ c={v:1,analytics:old==='1',referral:old==='1',at:Date.now()}; localStorage.setItem(KEY,JSON.stringify(c)); return c; }
    }catch(e){}
    return null;
  }
  function space(){
    var open=!el.hidden;
    document.documentElement.style.setProperty('--ck-h', open?Math.ceil(innerHeight-el.getBoundingClientRect().top)+'px':'0px');
  }
  function apply(c){
    if(!c) return;
    try{
      if(c.analytics && window.pfLoadAnalytics) window.pfLoadAnalytics();
      var pending=sessionStorage.getItem('pf_ref_pending');
      if(c.referral){
        if(pending){ localStorage.setItem('pf_ref',pending); localStorage.setItem('pf_ref_ts',String(Date.now())); sessionStorage.removeItem('pf_ref_pending'); }
      } else { localStorage.removeItem('pf_ref'); localStorage.removeItem('pf_ref_ts'); }
    }catch(e){}
  }
  function store(analytics,referral){
    var c={v:1,analytics:!!analytics,referral:!!referral,at:Date.now()};
    try{ localStorage.setItem(KEY,JSON.stringify(c)); localStorage.setItem('pf_cookie_ok',analytics||referral?'1':'0'); }catch(e){}
    apply(c); hide();
  }
  function show(manage){
    var c=read();
    an.checked=c?!!c.analytics:true; rf.checked=c?!!c.referral:true;
    opts.hidden=!manage; act.hidden=!!manage; save.hidden=!manage;
    el.hidden=false; space();
  }
  function hide(){ el.hidden=true; space(); }
  el.addEventListener('click',function(e){
    var b=e.target.closest('[data-ck]'); if(!b) return;
    var k=b.getAttribute('data-ck');
    if(k==='manage') show(true);
    else if(k==='collapse') show(false);   // back to the compact banner, keep it open
    else if(k==='accept') store(true,true);
    else if(k==='reject') store(false,false);
    else if(k==='save') store(an.checked,rf.checked);
    else if(k==='close') hide();
  });
  document.addEventListener('click',function(e){
    var a=e.target.closest('[data-ck-prefs]'); if(!a) return;
    e.preventDefault(); show(true);
  });
  window.pfConsent={read:read,open:function(){show(true);},apply:function(){apply(read());}};
  var c=read();
  if(c) apply(c); else show(false);
  addEventListener('resize',space); addEventListener('load',space);
})();
