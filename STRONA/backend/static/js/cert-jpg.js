/* Certificate -> JPG.

   The file is a real browser paint of the very card on screen: the node is
   cloned into <svg><foreignObject>, so layout, container queries, blend modes
   and the engraved pseudo-element artwork are done by the engine itself.

   It used to go through html2canvas, which re-implements CSS in JavaScript and
   got three things wrong at once: text sitting on a line-height below 1 was
   drawn too low (the amount ran into "presented to", the signature ran into its
   own line and the CEO caption), the ::before/::after artwork and mix-blend-mode
   were dropped, and aspect-ratio spacing came out different. The image no longer
   matched the document it was supposed to copy — which for a certificate is the
   whole point of it.

   Two rules follow from rendering inside an SVG image:
   1. it cannot fetch anything, so fonts and artwork travel with it as data: URIs;
   2. it is its own document, so media queries see the SVG viewport. We render at
      the card's full 620 px, which means a phone downloads the same desktop
      document as a laptop instead of the narrow mobile variant. */
(function () {
  const SHEETS = ['/static/css/site.css', '/static/css/cert.css'];
  const CARD_W = 620;                 // .cert-card is width:min(620px,100%)
  const SCALE = 2;                    // 620 -> 1240 px JPG
  const PLATE = '#050914';            // behind the card, matches the page

  const assets = new Map();
  function asData(url) {
    if (!assets.has(url)) {
      assets.set(url, fetch(url).then(r => r.blob()).then(b => new Promise((ok, no) => {
        const fr = new FileReader();
        fr.onload = () => ok(fr.result); fr.onerror = no;
        fr.readAsDataURL(b);
      })));
    }
    return assets.get(url);
  }

  async function sheet(href) {
    let css = await (await fetch(href)).text();
    const refs = [...new Set([...css.matchAll(/url\((['"]?)(\/static\/[^'")]+)\1\)/g)]
      .map(m => m[2]))];
    for (const [u, d] of await Promise.all(refs.map(async u => [u, await asData(u)]))) {
      css = css.split(u).join(d);
    }
    /* The palette is declared on :root, which inside the SVG is the <svg>
       element — the clone has to carry the variables itself. */
    return css.replace(/:root\s*\{/g, ':root,.cert-export{');
  }

  /* The card is a fragment: it inherits typography from <body> and its payout
     colours come from a class ON THE BODY (.cert-variant-payout .cert-amount).
     Both have to be recreated on the wrapper, or the export loses the green
     amount and the motif behind it. */
  function wrapper(w) {
    const b = getComputedStyle(document.body);
    /* line-height has to travel as the RATIO the page declares, never as the
       pixels it computes to: a fixed value would hand every child the same line
       box regardless of its font size, and the card would drift a few pixels
       lower with each element until the footer fell off the bottom. */
    const px = parseFloat(b.lineHeight), size = parseFloat(b.fontSize);
    const lh = px && size ? +(px / size).toFixed(4) : 'normal';
    const el = document.createElement('div');
    el.className = 'cert-export ' + document.body.className;
    el.style.cssText =
      `width:${w}px;display:block;min-height:0;margin:0;padding:0;background:none;` +
      `font-family:${b.fontFamily};font-size:${b.fontSize};font-weight:${b.fontWeight};` +
      `font-style:${b.fontStyle};line-height:${lh};color:${b.color};` +
      `letter-spacing:${b.letterSpacing};-webkit-font-smoothing:antialiased`;
    return el;
  }

  /* Height comes from a laid-out copy, not from the card on screen: on a phone
     the visible card follows the mobile stylesheet, while the export always
     renders the 620 px one. Same width, same media queries, same number. */
  async function heightOf(w, css, markup) {
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = `position:fixed;left:-10000px;top:0;border:0;visibility:hidden;` +
                          `width:${w}px;height:${w * 3}px`;
    document.body.appendChild(frame);
    try {
      const doc = frame.contentDocument;
      doc.open();
      doc.write(`<!doctype html><meta charset="utf-8"><style>${css}</style>` +
                `<body style="margin:0">${markup}</body>`);
      doc.close();
      /* Force layout BEFORE waiting on fonts: with nothing laid out yet nothing
         has asked for a font, so fonts.ready would resolve instantly and the
         card would be measured in fallback metrics. */
      void doc.body.offsetHeight;
      if (doc.fonts && doc.fonts.ready) await doc.fonts.ready;
      return Math.ceil(doc.querySelector('.cert-card').getBoundingClientRect().height);
    } finally {
      frame.remove();
    }
  }

  async function render(card) {
    const css = (await Promise.all(SHEETS.map(sheet))).join('\n');

    const clone = card.cloneNode(true);
    await Promise.all([...clone.querySelectorAll('img')].map(async img => {
      const src = img.getAttribute('src');
      if (src && src.startsWith('/')) img.setAttribute('src', await asData(src));
    }));

    const wrap = wrapper(CARD_W);
    const style = document.createElement('style');
    style.textContent = css;
    wrap.append(style, clone);

    /* XMLSerializer escapes the stylesheet's < and &; the XML parser on the
       other side turns them straight back into CSS. */
    const markup = new XMLSerializer().serializeToString(wrap);
    const h = await heightOf(CARD_W, css, markup);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${CARD_W}" height="${h}">` +
                `<foreignObject x="0" y="0" width="${CARD_W}" height="${h}">${markup}</foreignObject></svg>`;

    const img = new Image();
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    await img.decode();

    const canvas = document.createElement('canvas');
    canvas.width = CARD_W * SCALE;
    canvas.height = h * SCALE;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = PLATE;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  /* Safety net for engines that refuse to rasterise a foreign object: the old
     renderer, loaded only if we get there. Its output is imperfect, but an
     imperfect certificate beats no download at all. */
  let h2c;
  function legacy() {
    if (!h2c) {
      h2c = new Promise((ok, no) => {
        const s = document.createElement('script');
        s.src = '/static/lib/html2canvas.min.js';
        s.onload = () => ok(window.html2canvas); s.onerror = no;
        document.head.appendChild(s);
      });
    }
    return h2c;
  }

  window.certJpg = async function (btn) {
    if (btn.dataset.busy) return;
    btn.dataset.busy = '1';
    const was = btn.textContent;
    btn.textContent = 'Rendering…';
    const card = document.querySelector('.cert-card');
    try {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      let canvas;
      try {
        canvas = await render(card);
      } catch (e) {
        canvas = await (await legacy())(card, { scale: SCALE, useCORS: true, backgroundColor: PLATE });
      }
      const a = document.createElement('a');
      a.download = 'certificate-' + (document.body.dataset.certToken || 'download') + '.jpg';
      a.href = canvas.toDataURL('image/jpeg', 0.94);
      document.body.appendChild(a); a.click(); a.remove();
    } catch (e) {
      alert('Could not render the image. Please use Print / save as PDF.');
    }
    btn.textContent = was;
    delete btn.dataset.busy;
  };
})();
