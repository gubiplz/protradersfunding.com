/* libraries.dev — Thinking orb przed każdym „Loading…" w panelach terminala.
 * Nie dotyka terminal.js: obserwuje DOM i dokleja orb (ptf-fx.js) do .t-oi-msg / .t-empty / .t-hint,
 * gdy ich tekst zaczyna się od „Loading". Bez ptf-fx.js nic nie robi. */
(function () {
  'use strict';
  var SEL = '.t-oi-msg, .t-empty, .t-hint';
  function dotnij(n) {
    if (!window.PTFFx || n.dataset.fxOrb) return;
    if (!/^\s*Loading/i.test(n.textContent || '')) return;
    n.dataset.fxOrb = '1';
    var o = document.createElement('span');
    o.className = 'fx-orb-inline';
    o.style.cssText = 'display:inline-flex;width:20px;height:20px;vertical-align:middle;margin-right:8px';
    n.insertBefore(o, n.firstChild);
    o._fxOff = PTFFx.orb(o, { state: 'working', size: 20 });
  }
  // terminal.js nadpisuje innerHTML paneli — orb, który wypadł z DOM, wyłączamy.
  function sprzataj(n) {
    var lista = n.classList && n.classList.contains('fx-orb-inline') ? [n] : (n.querySelectorAll ? n.querySelectorAll('.fx-orb-inline') : []);
    Array.prototype.forEach.call(lista, function (o) { if (o._fxOff) { o._fxOff(); o._fxOff = null; } });
  }
  function skan(root) {
    if (root.matches && root.matches(SEL)) dotnij(root);
    if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(dotnij);
  }
  function start() {
    skan(document.body);
    new MutationObserver(function (lista) {
      lista.forEach(function (m) {
        m.removedNodes.forEach(function (n) { if (n.nodeType === 1 && !n.isConnected) sprzataj(n); });
        m.addedNodes.forEach(function (n) { if (n.nodeType === 1) skan(n); });
        if (m.type === 'characterData' && m.target.parentElement) skan(m.target.parentElement);
      });
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
