/* Rozmowa w bilecie jak w komunikatorze — wspólna dla panelu admina i portalu.
 *
 * Dawniej każda wiadomość była prostokątną kartką z nagłówkiem wersalikami
 * („YOU · SEP 24, 12:25"), a odpowiedź pisało się w zwykłym polu formularza.
 * Teraz jak iMessage / WhatsApp: moje dymki po prawej w kolorze akcentu,
 * rozmówcy po lewej na szarym, kolejne wiadomości jednej osoby zgrupowane
 * (ogonek tylko przy ostatniej), godzina w rogu dymka, separator dnia, linki
 * klikalne; pasek pisania to rosnące pole i okrągły przycisk wysyłania.
 *
 * chatHtml(thread, {me, them, tz})
 *   thread — [{author, body, ts}], me — autor „moich" dymków ('admin'|'trader'),
 *   them — podpis rozmówcy nad pierwszym dymkiem grupy, tz — strefa (IANA).
 * chatCompose({id, send, placeholder}) — pasek pisania; `send` to kod onclick.
 * Enter wysyła na komputerze (Shift+Enter = nowa linia), na telefonie Enter
 * to nowa linia — jak w komunikatorach.
 */
(function () {
  'use strict';
  var GRUPA_MS = 5 * 60 * 1000;
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  // Baza zapisuje nagie UTC (bez strefy) — bez „Z" przeglądarka wzięłaby je za czas lokalny.
  var utc = function (iso) {
    var s = String(iso || '').replace(' ', 'T');
    return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
  };
  // Na tekście już escapowanym: & w adresie jest &amp;, co w atrybucie href jest poprawne.
  var linki = function (t) {
    return t.replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>');
  };
  var klucz = function (d, tz) { return d.toLocaleDateString('en-CA', tz); };

  function dzien(d, tz) {
    var k = klucz(d, tz), teraz = new Date();
    if (k === klucz(teraz, tz)) return 'Today';
    if (k === klucz(new Date(teraz.getTime() - 864e5), tz)) return 'Yesterday';
    var o = { month: 'short', day: 'numeric' };
    if (d.getFullYear() !== teraz.getFullYear()) o.year = 'numeric';
    return d.toLocaleDateString('en-US', Object.assign(o, tz));
  }

  window.chatHtml = function (thread, o) {
    o = o || {};
    var tz = o.tz ? { timeZone: o.tz } : {};
    var out = [], dzienPoprz = null;
    (thread || []).forEach(function (m, i) {
      var d = utc(m.ts), k = klucz(d, tz);
      if (k !== dzienPoprz) {
        out.push('<div class="chat-day"><span>' + dzien(d, tz) + '</span></div>');
        dzienPoprz = k;
      }
      var prev = thread[i - 1], next = thread[i + 1];
      var ciagDalej = function (a, b) {
        return a && b && a.author === b.author && klucz(utc(a.ts), tz) === klucz(utc(b.ts), tz)
          && Math.abs(utc(b.ts) - utc(a.ts)) < GRUPA_MS;
      };
      var pierwsza = !ciagDalej(prev, m), ostatnia = !ciagDalej(m, next);
      var moja = m.author === o.me;
      var godz = d.toLocaleTimeString('en-GB', Object.assign({ hour: '2-digit', minute: '2-digit' }, tz));
      out.push('<div class="chat-row ' + (moja ? 'me' : 'them') + (pierwsza ? ' first' : '')
        + (ostatnia ? ' last' : '') + '">'
        + (!moja && pierwsza && o.them ? '<div class="chat-who">' + esc(o.them) + '</div>' : '')
        // .bub-sp rezerwuje w ostatniej linii miejsce na godzinę (sztuczka WhatsAppa),
        // żeby tekst nigdy nie wszedł pod nią
        + '<div class="bub">' + linki(esc(m.body)) + '<span class="bub-sp"></span>'
        + '<span class="bub-t">' + godz + '</span></div></div>');
    });
    return '<div class="chat">' + out.join('') + '</div>';
  };

  window.chatCompose = function (o) {
    return '<div class="chat-compose">'
      + '<textarea id="' + esc(o.id) + '" class="chat-inp" rows="1" enterkeyhint="send" placeholder="'
      + esc(o.placeholder || 'Message') + '"></textarea>'
      + '<button type="button" class="chat-send" aria-label="Send" disabled onclick="' + esc(o.send) + '">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"'
      + ' stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button></div>';
  };

  function rosnij(t) {
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 140) + 'px';
    var b = t.parentElement && t.parentElement.querySelector('.chat-send');
    if (b) b.disabled = !t.value.trim();
  }
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('chat-inp')) rosnij(t);
  });
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains('chat-inp')) return;
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;   // telefon: nowa linia
    e.preventDefault();
    var b = t.parentElement.querySelector('.chat-send');
    if (b && !b.disabled) b.click();
  });
})();
