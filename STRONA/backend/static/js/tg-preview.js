/* Podglad posta tak, jak zobaczy go kanal na Telegramie.
 *
 * Osobny plik z czystymi funkcjami (bez DOM), bo to jest logika, ktora ma sie
 * zgadzac z tym, co robi serwer, i musi dac sie sprawdzic w Node:
 *
 *  - tgHtml      — parser znacznikow w stylu Telegrama. Telegram przy
 *                  `parse_mode=HTML` NIE pomija nieznanego znacznika, tylko
 *                  odrzuca cala wiadomosc („can't parse entities"). Walidator
 *                  na serwerze tego nie sprawdza, wiec post z <div> przechodzi
 *                  zatwierdzenie i pada dopiero przy publikacji. Podglad mowi
 *                  o tym wczesniej.
 *  - tgGrafika   — ta sama decyzja co `contentbot.opublikuj`: gotowy obraz
 *                  leci adresem, strona idzie do zrzutu, film tylko jako .mp4.
 *  - tgMakieta   — dymek w ciemnym motywie Telegrama.
 *
 * Liczenie znakow jak `contentbot.dlugosc_widoczna`: po zdjeciu znacznikow
 * i rozwinieciu encji, w punktach kodowych — limit 1024 pod zdjeciem/filmem,
 * 4096 dla samego tekstu.
 */
const TG_LIMIT_PODPISU = 1024, TG_LIMIT_TEKSTU = 4096, TG_ZRZUT = 660;

const tgEsc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Znaczniki, ktore Telegram rozumie, i to, na co je zamieniamy w podgladzie.
const TG_TAGI = {b:'b', strong:'b', i:'i', em:'i', u:'u', ins:'u', s:'s', strike:'s',
  del:'s', a:'a', code:'code', pre:'pre', blockquote:'blockquote',
  'tg-spoiler':'spoiler', span:'spoiler', 'tg-emoji':'emoji'};

function tgDekoduj(t){
  return String(t).replace(/&(#x[0-9a-f]+|#\d+|lt|gt|amp|quot|apos|nbsp);/gi, (m, k) => {
    k = k.toLowerCase();
    const nazwane = {lt:'<', gt:'>', amp:'&', quot:'"', apos:"'", nbsp:' '};
    if (k in nazwane) return nazwane[k];
    const n = k[1] === 'x' ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10);
    try { return String.fromCodePoint(n); } catch (e) { return m; }
  });
}

function tgHref(atrybuty){
  const m = /href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(atrybuty || '');
  if (!m) return '';
  const adres = tgDekoduj(m[1] ?? m[2] ?? m[3] ?? '').trim();
  return /^(https?:|tg:|mailto:)/i.test(adres) ? adres : '';
}

/* -> {html, widoczne, problemy}. `html` jest bezpieczny do wstawienia:
   tekst przechodzi przez tgEsc, a znaczniki powstaja wylacznie z listy wyzej. */
function tgHtml(zrodlo){
  const kawalki = String(zrodlo || '').split(/(<\/?[a-zA-Z][^<>]*>)/);
  let html = '', widoczne = 0;
  const stos = [], problemy = [];
  // Znaczniki juz zgloszone jako nieobslugiwane: ich zamkniecie ma zostac
  // pokazane tak samo (jako tekst), a nie dac drugiego, mylacego ostrzezenia.
  const odrzucone = new Set();
  const otwierajacy = {b:'<b>', i:'<i>', u:'<u>', s:'<s>', code:'<code>', pre:'<pre>',
    blockquote:'<blockquote>', spoiler:'<span class="tgp-spoiler">', emoji:'<span>'};
  const zamykajacy = {b:'</b>', i:'</i>', u:'</u>', s:'</s>', code:'</code>', pre:'</pre>',
    blockquote:'</blockquote>', spoiler:'</span>', emoji:'</span>', a:'</a>'};
  for (const k of kawalki){
    if (!k) continue;
    const m = /^<(\/?)([a-zA-Z][\w-]*)([^>]*)>$/.exec(k);
    if (!m){
      const t = tgDekoduj(k);
      widoczne += [...t].length;
      html += tgEsc(t);
      continue;
    }
    const [, zamyka, surowa, atr] = m;
    const nazwa = surowa.toLowerCase();
    const pasuje = stos.length && stos[stos.length - 1].nazwa === nazwa;
    if (zamyka && !pasuje && (odrzucone.has(nazwa) || !TG_TAGI[nazwa])){
      problemy.push('Telegram will refuse this post: it does not support <' + nazwa + '>.');
      widoczne += [...k].length;
      html += tgEsc(k);
      continue;
    }
    if (zamyka){
      const gora = stos[stos.length - 1];
      if (gora && gora.nazwa === nazwa){
        stos.pop();
        html += zamykajacy[gora.rodzaj];
      } else {
        problemy.push(`Telegram will refuse this post: </${nazwa}> closes a tag that is not open.`);
      }
      continue;
    }
    let rodzaj = TG_TAGI[nazwa];
    if (nazwa === 'span' && !/class\s*=\s*["']?tg-spoiler/i.test(atr)) rodzaj = null;
    if (!rodzaj){
      odrzucone.add(nazwa);
      problemy.push(`Telegram will refuse this post: it does not support <${nazwa}>.`);
      widoczne += [...k].length;
      html += tgEsc(k);
      continue;
    }
    stos.push({nazwa, rodzaj});
    if (rodzaj === 'a'){
      const href = tgHref(atr);
      html += href ? `<a href="${tgEsc(href)}" target="_blank" rel="noopener">` : '<a>';
    } else {
      html += otwierajacy[rodzaj];
    }
  }
  if (stos.length){
    problemy.push(`Telegram will refuse this post: <${stos.map(s => s.nazwa).join('>, <')}> is never closed.`);
    while (stos.length) html += zamykajacy[stos.pop().rodzaj];
  }
  return {html, widoczne, problemy: [...new Set(problemy)]};
}

/* Linki wezwan — kopia `contentbot.dolinkuj`. Wysylka dokleja je do tresci
   (archiwum zgubilo linki starego kanalu), wiec podglad musi pokazac to samo,
   inaczej „Get started" wygladaloby w makiecie na zwykly tekst. */
const TG_PROSBA_DM = 'I am ready to get funded and start earning payouts! Please send me more information';
const TG_CTA_RX = /^(\s*(?:<[^>]+>)*[^\p{L}\p{N}_<\n]*)(click here\b[^<\n]*?|get started|send us a message|message us|contact us)([!.]?(?:<[^>]+>)*\s*)$/iu;
const TG_ADMIN_RX = /(?<![\w@/])@(\w*admin\w*)\b/gi;

function tgZProsba(adres){
  if (!/^https?:\/\/t\.me\/\w+\/?$/i.test(adres || '')) return adres || '';
  return adres.replace(/\/$/, '') + '?text=' + encodeURIComponent(TG_PROSBA_DM)
    .replace(/[!'()*]/g, c => '%' + c.charCodeAt(0).toString(16).toUpperCase());
}

function tgAdresCta(tekst, zapas){
  const w = new RegExp(TG_ADMIN_RX.source, 'i').exec(String(tekst || '').replace(/<[^>]+>/g, ' '));
  return w ? tgZProsba('https://t.me/' + w[1]) : tgZProsba(String(zapas || '').trim());
}

function tgLinkuj(tekst, zapas){
  tekst = String(tekst || '');
  const adres = tgAdresCta(tekst, zapas);
  if (!adres) return tekst;
  const href = tgEsc(adres);
  const linie = tekst.split('\n').map(l => {
    if (l.toLowerCase().includes('<a')) return l;
    const m = TG_CTA_RX.exec(l);
    return m ? `${m[1]}<a href="${href}">${m[2]}</a>${m[3]}` : l;
  });
  return linie.join('\n').split(/(<a\b[^>]*>[\s\S]*?<\/a>)/i)
    .map((k, i) => i % 2 ? k : k.replace(TG_ADMIN_RX, w => `<a href="${href}">${w}</a>`))
    .join('');
}

const tgSciezka = u => String(u || '').split('?')[0].split('#')[0].toLowerCase();

/* Ta sama decyzja co `contentbot.opublikuj`. -> {typ, url, problem}
   typ: 'none' | 'image' | 'page' | 'video' */
function tgGrafika(post){
  const url = (post && post.media_url) || '';
  const kind = (post && post.kind) || 'text';
  if (kind === 'video'){
    if (!url) return {typ:'none', url:'', problem:'Video post with no clip URL — it will not be sent.'};
    if (!tgSciezka(url).endsWith('.mp4'))
      return {typ:'none', url, problem:'The clip URL must point at an .mp4 file.'};
    return {typ:'video', url, problem:''};
  }
  if (kind !== 'photo') return {typ:'none', url:'', problem:''};
  if (!url) return {typ:'none', url:'', problem:'Photo post with no image — it will not be sent.'};
  const archiwum = String(post.origin || '').startsWith('archive:');
  if (archiwum || /\.(png|jpe?g|webp)$/.test(tgSciezka(url))) return {typ:'image', url, problem:''};
  return {typ:'page', url, problem:''};
}

const tgLimit = kind => (kind === 'photo' || kind === 'video') ? TG_LIMIT_PODPISU : TG_LIMIT_TEKSTU;

/* Dymek kanalu. `godzina` przychodzi gotowa, zeby ten plik nie zalezal od strefy. */
function tgMakieta({tytul, godzina, post}){
  const tekst = tgHtml(tgLinkuj(post.body, post.cta_fallback));
  const g = tgGrafika(post);
  const inicjaly = String(tytul || '?').split(/\s+/).filter(Boolean).slice(0, 2)
    .map(w => w[0].toUpperCase()).join('');
  let media = '';
  if (g.typ === 'image'){
    media = `<img class="tgp-media" src="${tgEsc(g.url)}" alt=""
      onerror="this.outerHTML='<div class=&quot;tgp-miss&quot;>The image did not load here — Telegram will not be able to fetch it either.</div>'">`;
  } else if (g.typ === 'page'){
    media = `<div class="tgp-shot"><iframe src="${tgEsc(g.url)}"
      tabindex="-1" aria-hidden="true" scrolling="no" loading="lazy"></iframe></div>`;
  } else if (g.typ === 'video'){
    media = `<video class="tgp-media" src="${tgEsc(g.url)}" controls muted playsinline preload="metadata"></video>`;
  }
  const podpis = (post.body || '').trim()
    ? `<div class="tgp-text">${tekst.html}</div>` : '';
  return `<div class="tgp">
    <div class="tgp-head"><span class="tgp-ava">${tgEsc(inicjaly)}</span>
      <span><b>${tgEsc(tytul || 'Channel')}</b><small>channel</small></span></div>
    <div class="tgp-chat">
      <div class="tgp-bubble${media ? ' has-media' : ''}">
        ${media}${podpis}
        <div class="tgp-meta">${tgEsc(godzina || '')}</div>
      </div>
    </div>
  </div>`;
}

/* Zrzut strony ma 660 px szerokosci; ramka ze strona dostaje ten sam kadr
   i skaluje sie do szerokosci dymka. Wolane po wstawieniu makiety do DOM. */
function tgDopasujZrzuty(korzen){
  (korzen || document).querySelectorAll('.tgp-shot').forEach(el => {
    const k = el.clientWidth / TG_ZRZUT;
    if (k > 0) el.style.setProperty('--k', k);
  });
}
