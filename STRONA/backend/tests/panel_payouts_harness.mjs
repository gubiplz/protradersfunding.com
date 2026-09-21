/* Uruchamia renderPayoutsView() z admin-panel.js POZA przegladarka.
 *
 * Filtr wierszy archiwalnych to kilka warunkow splecionych z wyszukiwarka,
 * segmentem statusu i licznikiem. Asercja "w kodzie jest taki napis" przeszlaby
 * dla kodu, ktory sie nie wykonuje albo liczy zle -- dlatego tutaj naprawde
 * wolamy widok i ogladamy HTML, ktory z niego wychodzi.
 *
 * Shim DOM jest MINIMALNY z rozmyslem: ma wystarczyc do jednego renderu, a nie
 * udawac przegladarke. Gdy bundle siegnie po cos, czego tu nie ma, test ma
 * paisc glosno -- cicha zaslepka ukrylaby, ze widok przestal dzialac.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const [, , sciezka, daneJson, importedShown] = process.argv;
const zrodlo = readFileSync(sciezka, 'utf8');

const elementy = new Map();
function element(id) {
  if (!elementy.has(id)) {
    elementy.set(id, {
      id, innerHTML: '', value: '', dataset: {},
      style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => '' },
      classList: {
        add() {}, remove() {}, toggle() {}, contains: () => false,
      },
      getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0 }),
      addEventListener() {}, removeEventListener() {}, appendChild() {},
      prepend() {}, append() {}, insertBefore() {}, insertAdjacentHTML() {},
      setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
      querySelector: () => null, querySelectorAll: () => [], closest: () => null,
      focus() {}, blur() {}, remove() {},
    });
  }
  return elementy.get(id);
}

const magazyn = new Map([['pf_admin_imported', importedShown === '1' ? '1' : '0']]);
const document = {
  getElementById: (id) => element(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => element('__tmp__'),
  addEventListener() {}, removeEventListener() {},
  body: element('__body__'),
  documentElement: element('__html__'),
  head: element('__head__'),
  fonts: { ready: Promise.resolve() },
};

const okno = {
  document,
  localStorage: {
    getItem: (k) => (magazyn.has(k) ? magazyn.get(k) : null),
    setItem: (k, v) => magazyn.set(k, String(v)),
    removeItem: (k) => magazyn.delete(k),
  },
  location: {
    origin: 'https://panel.test', pathname: '/admin', search: '', hash: '',
    // Bundle po nieudanym zapytaniu wylogowuje i przeladowuje strone. Tutaj to
    // ma byc bezczynnosc, a nie wyjatek -- inaczej test padalby na scieszce,
    // ktora z renderem nie ma nic wspolnego.
    replace() {}, assign() {}, reload() {},
  },
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  requestAnimationFrame: (f) => { f(0); return 0; },
  /* Rozruch panelu sam pyta serwer o stan. Zadanie, ktore NIGDY sie nie konczy,
     jest tu lepsze od bledu: render jest synchroniczny, wiec niczego nie traci,
     a zaden handler bledu nie zacznie robic czegos w tle. */
  fetch: () => new Promise(() => {}),
  console, JSON, Math, Date, URL, URLSearchParams,
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 2,
  navigator: { userAgent: 'node', clipboard: { writeText: async () => {} } },
  history: { replaceState() {}, pushState() {} },
};
okno.window = okno;
okno.self = okno;
okno.globalThis = okno;

const kontekst = vm.createContext(okno);
try {
  vm.runInContext(zrodlo, kontekst, { filename: 'admin-panel.js' });
} catch (e) {
  console.error('BUNDLE_NIE_WSTAL: ' + e.message);
  process.exit(3);
}

okno._payReqs = JSON.parse(daneJson);
okno._payFilter = 'all';
okno._payQ = '';
try {
  kontekst.renderPayoutsView();
} catch (e) {
  console.error('RENDER_PADL: ' + e.message);
  process.exit(4);
}
process.stdout.write(element('view').innerHTML);
