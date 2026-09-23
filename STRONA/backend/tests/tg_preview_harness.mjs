/* Uruchamia tg-preview.js POZA przegladarka i zwraca wyniki jego funkcji.
 *
 * Plik jest czysty (bez DOM poza tgDopasujZrzuty, ktorej tu nie wolamy),
 * wiec kontekst nie musi niczego udawac. Wejscie: sciezka do pliku i JSON
 * z lista zadan {fn, arg}. Wyjscie: JSON z wynikami w tej samej kolejnosci.
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const [, , sciezka, zadaniaJson] = process.argv;
const kontekst = vm.createContext({ console });
try {
  // Deklaracje z klasycznego skryptu laduja w kontekscie tylko jako funkcje;
  // stale `const` trzeba wystawic jawnie, dlatego dopisujemy eksport.
  vm.runInContext(readFileSync(sciezka, 'utf8')
    + '\n;globalThis.__tg={tgHtml,tgGrafika,tgLimit,tgMakieta};', kontekst,
  { filename: 'tg-preview.js' });
} catch (e) {
  console.error('MODUL_NIE_WSTAL: ' + e.message);
  process.exit(3);
}
const wyniki = JSON.parse(zadaniaJson).map(z => kontekst.__tg[z.fn](z.arg));
process.stdout.write(JSON.stringify(wyniki));
