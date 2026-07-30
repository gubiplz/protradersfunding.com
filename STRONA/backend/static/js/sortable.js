/* Column sorting for data tables (trader portal + admin dashboard).
 *
 * Opt-in per table: <table class="tbl sortable" data-tkey="admin.accounts">.
 * Clicking a header cycles ascending -> descending -> original server order.
 * Headers with the no-sort class (action columns) are skipped.
 *
 * Sort keys come from td.dataset.sort when present (ISO dates, raw numbers
 * behind mini-bars), otherwise from the cell text; $ % , + and spaces are
 * stripped before a numeric compare, non-numeric values fall back to
 * localeCompare, empty cells always sink to the bottom.
 *
 * Hidden companion rows (credentials, inline edit forms) carry the tr-sub
 * class and travel with the data row directly above them.
 *
 * The chosen sort is kept per table key in ME.ui_prefs.sort and saved to the
 * account via PATCH /api/me (debounced), so it survives reloads and devices.
 * Views re-render their HTML freely (the admin auto-refreshes every 12 s) —
 * a MutationObserver re-applies the saved sort to every freshly rendered
 * sortable table.
 */
(function () {
  'use strict';

  function me() {
    /* ME is a top-level `let` in the page script — not on window; the
       try/catch also covers being called before that script ran. */
    try { return (typeof ME !== 'undefined' && ME) ? ME : null; } catch (e) { return null; }
  }
  function sortPrefs() {
    const m = me();
    if (!m) return {};
    m.ui_prefs = m.ui_prefs || {};
    m.ui_prefs.sort = m.ui_prefs.sort || {};
    return m.ui_prefs.sort;
  }

  let saveT = null;
  function savePrefs() {
    clearTimeout(saveT);
    saveT = setTimeout(function () {
      const m = me();
      try {
        if (!m || !m.ui_prefs || typeof api !== 'function') return;
        api('/api/me', { method: 'PATCH', body: JSON.stringify({ ui_prefs: m.ui_prefs }) }).catch(function () {});
      } catch (e) { /* saving is best-effort — the sort itself already happened */ }
    }, 800);
  }

  function cellKey(td) {
    if (!td) return '';
    if (td.dataset && td.dataset.sort !== undefined) return td.dataset.sort;
    return (td.textContent || '').trim();
  }
  function asNumber(v) {
    if (v === '') return null;
    const n = parseFloat(String(v).replace(/−/g, '-').replace(/[$,%+\s]/g, ''));
    return isNaN(n) ? null : n;
  }
  function isEmpty(v) { return v === '' || v === '—' || v === '-'; }

  /* Group tbody rows into [dataRow, ...its tr-sub companions] units. */
  function units(tbody) {
    const out = [];
    for (const tr of tbody.children) {
      if (tr.classList.contains('tr-sub') && out.length) out[out.length - 1].push(tr);
      else out.push([tr]);
    }
    return out;
  }

  function applySort(table, idx, dir) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    /* The original (server) order is captured once per render — the table
       element is recreated on re-render, so this resets naturally. */
    if (!table._skOrig) table._skOrig = units(tbody);
    let us;
    if (!dir) {
      us = table._skOrig;
    } else {
      us = units(tbody).sort(function (ua, ub) {
        const a = cellKey(ua[0].cells[idx]), b = cellKey(ub[0].cells[idx]);
        const ea = isEmpty(a), eb = isEmpty(b);
        if (ea && eb) return 0;
        if (ea) return 1;
        if (eb) return -1;
        const na = asNumber(a), nb = asNumber(b);
        const c = (na !== null && nb !== null)
          ? na - nb
          : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
        return c * dir;
      });
    }
    const frag = document.createDocumentFragment();
    us.forEach(function (u) { u.forEach(function (tr) { frag.appendChild(tr); }); });
    tbody.appendChild(frag);
    if (table.tHead && table.tHead.rows[0]) {
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {
        if (dir && i === idx) th.dataset.dir = dir > 0 ? 'asc' : 'desc';
        else delete th.dataset.dir;
      });
    }
  }

  document.addEventListener('click', function (e) {
    const th = e.target.closest && e.target.closest('table.sortable[data-tkey] th');
    if (!th || th.classList.contains('no-sort') || !th.closest('thead')) return;
    const table = th.closest('table');
    const idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    const key = table.dataset.tkey;
    const p = sortPrefs();
    const cur = p[key];
    let dir = 1;                                     // fresh column -> ascending
    if (cur && cur[0] === idx) dir = cur[1] === 1 ? -1 : cur[1] === -1 ? 0 : 1;
    if (dir) p[key] = [idx, dir]; else delete p[key];
    applySort(table, idx, dir);
    savePrefs();
  });

  /* Re-apply the saved sort whenever a sortable table (re)appears. */
  const obs = new MutationObserver(function (muts) {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        const tables = n.matches && n.matches('table.sortable[data-tkey]')
          ? [n]
          : (n.querySelectorAll ? n.querySelectorAll('table.sortable[data-tkey]') : []);
        for (const t of tables) {
          const st = sortPrefs()[t.dataset.tkey];
          if (st && t.tHead && t.tHead.rows[0] && st[0] < t.tHead.rows[0].cells.length) {
            applySort(t, st[0], st[1]);
          }
        }
      }
    }
  });
  /* The script is loaded from <head> — document.body does not exist yet and
     observe(null) would throw, killing re-apply while clicks kept working. */
  function watch() { obs.observe(document.body, { childList: true, subtree: true }); }
  if (document.body) watch();
  else document.addEventListener('DOMContentLoaded', watch);
})();
