/* Sliding indicator for segmented switches: one element per group that glides from the old
   option to the new one, instead of the active state going out on one button and lighting up
   on the other. Same motion as the Challenges tabs (site.js slidePill), but generic:

     <div class="tabs" data-slide>…<button class="on">…</button></div>

   data-slide            "pill" (default): the indicator takes the active button's box, in both
                         axes, so groups that wrap onto several rows work too;
                         "bar": an underline — only left and width follow the button, the rest
                         (bottom, height, colour) comes from CSS.
   data-slide-sel        selector of the active item inside the group (default ".on").
   data-slide-inset      bar only: how far the bar stays from the button's edges, "10" (px) or "18%".
   data-slide-copy       class names copied from the active button to the indicator ("buy sell"),
                         for groups whose active colour depends on the option.
   data-slide-key        remembers the position under this key when the whole group is replaced
                         (views rendered from scratch); call pfSlide.scan(root) after the render.

   The group may toggle classes in place or rebuild its buttons with innerHTML: a MutationObserver
   follows both, and a ResizeObserver keeps the indicator in place when the layout changes.
   Looks live in each surface's CSS: `.group > .sl-ind{…}` and `.group.sl-box .on{background:none}`.
   Without this script nothing changes — the buttons keep their own active style. */
(function () {
  'use strict';
  var MEM = new Map();

  function measure(box, on, o) {
    var x = 0, y = 0, el = on;
    // offsetLeft/offsetTop ignore transforms, so a button caught mid-press (:active scale)
    // or inside an element that is animating still gives its resting box.
    while (el && el !== box) { x += el.offsetLeft; y += el.offsetTop; el = el.offsetParent; }
    if (el !== box) return null;                       // not laid out inside this group (hidden)
    var w = on.offsetWidth, h = on.offsetHeight;
    if (!w || !h) return null;
    if (o.mode === 'bar' && o.inset) {
      var ins = /%$/.test(o.inset) ? w * parseFloat(o.inset) / 100 : parseFloat(o.inset);
      x += ins; w -= 2 * ins;
    }
    return { x: x, y: y, w: w, h: h };
  }

  function put(ind, r, o) {
    ind.style.left = r.x + 'px'; ind.style.width = r.w + 'px';
    if (o.mode !== 'bar') { ind.style.top = r.y + 'px'; ind.style.height = r.h + 'px'; }
  }

  function setup(box) {
    if (!box || box._sl) return box && box._sl;
    var d = box.dataset;
    var o = { mode: d.slide === 'bar' ? 'bar' : 'pill', sel: d.slideSel || '.on', inset: d.slideInset || '',
              copy: (d.slideCopy || '').split(/\s+/).filter(Boolean), key: d.slideKey || '' };
    var st = box._sl = { o: o, ind: null, last: null, raf: 0, anim: false };
    box.classList.add('sl-box');

    function place(animate) {
      var on = box.querySelector(o.sel);
      var ind = st.ind && st.ind.parentNode === box ? st.ind : null;
      var r = on && measure(box, on, o);
      if (!r) { if (ind) ind.style.opacity = '0'; return; }
      var prev = st.last || (o.key && MEM.get(o.key)) || null;
      if (!ind) {
        // New (first paint) or wiped by a re-render: start where the indicator last stood.
        ind = st.ind = document.createElement('i');
        ind.className = 'sl-ind'; ind.setAttribute('aria-hidden', 'true');
        ind.classList.add('sl-now');
        put(ind, animate && prev ? prev : r, o);
        box.insertBefore(ind, box.firstChild);
        void ind.offsetWidth;
        ind.classList.remove('sl-now');
      }
      if (!animate) ind.classList.add('sl-now');
      put(ind, r, o);
      // A pill takes the shape of the button it sits under (breakpoints change the radius).
      if (o.mode !== 'bar') ind.style.borderRadius = getComputedStyle(on).borderRadius;
      ind.style.opacity = '';
      o.copy.forEach(function (c) { ind.classList.toggle(c, on.classList.contains(c)); });
      if (!animate) { void ind.offsetWidth; ind.classList.remove('sl-now'); }
      st.last = r;
      if (o.key) MEM.set(o.key, r);
    }

    function kick(animate) {
      st.anim = st.anim || animate;
      if (st.raf) return;
      st.raf = requestAnimationFrame(function () { st.raf = 0; var a = st.anim; st.anim = false; place(a); });
    }
    st.kick = kick;

    new MutationObserver(function (list) {
      for (var i = 0; i < list.length; i++) {
        var m = list[i];
        if (m.target === st.ind) continue;                              // our own class changes
        if (m.type === 'childList' && m.target === box && !m.removedNodes.length
            && m.addedNodes.length === 1 && m.addedNodes[0] === st.ind) continue;   // our own insert
        kick(true); return;
      }
    }).observe(box, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'hidden'] });
    if (window.ResizeObserver) {
      // A ResizeObserver reports once as soon as it starts observing. Acting on that report
      // would snap the indicator into place and cut short the glide of a freshly rendered
      // group, so only a real change of the group's size counts.
      var bw = box.offsetWidth, bh = box.offsetHeight;
      new ResizeObserver(function () {
        if (box.offsetWidth === bw && box.offsetHeight === bh) return;
        bw = box.offsetWidth; bh = box.offsetHeight;
        kick(false);
      }).observe(box);
    }

    // A group that replaces a remembered one (same key) glides from where that one stood.
    place(!!(o.key && MEM.get(o.key)));
    return st;
  }

  function pfSlide(box) { var st = setup(box); if (st && st.kick) st.kick(true); }
  pfSlide.scan = function (root) {
    var list = (root || document).querySelectorAll('[data-slide]');
    for (var i = 0; i < list.length; i++) setup(list[i]);
  };
  window.pfSlide = pfSlide;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { pfSlide.scan(); });
  else pfSlide.scan();
  // Web fonts and late layout move the buttons after the first placement.
  window.addEventListener('load', function () {
    var list = document.querySelectorAll('.sl-box');
    for (var i = 0; i < list.length; i++) if (list[i]._sl) list[i]._sl.kick(false);
  });
})();
