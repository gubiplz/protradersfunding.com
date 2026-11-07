# PTF — audyt spójności wypłat (7 listopada 2026)

Wewnętrzny dokument. Nie jest opinią prawną. Odpowiedzi operatora opisują zamierzony model biznesowy, nie przesądzają zgodności z prawem.

## Zamierzony model (zatwierdzony do wdrożenia)

| Element | Reguła |
| --- | --- |
| Asset nagrody | vUSDT na Polygon |
| Fee refund | dodatkowe vUSDT przy pierwszej wypłacie z danego challenge (nie zwrot na kartę) |
| Default cadence | bi-weekly: 14 dni kalendarzowych między wnioskami (2-Step i Instant) |
| On-demand add-on | $299 (2-Step) / $399 (Instant) — usuwa limit między wnioskami |
| First payout (funded) | 5 trading days (2-Step) / 30 (Instant) |
| Express Payout ($49) | wycofany |
| Dane operatora | brak — nie uzupełniać zmyślonymi danymi |
| Copytrading | przełącznik admin `copytrading_offered`; FAQ/Terms: tylko gdy w ofercie |

## Klasyfikacja (weryfikacja briefu vs kod / live)

### Potwierdzone fakty

1. Live miał sprzeczność: prose „on demand / no wait” vs `data-payout-days="14"` + egzekucja backendu.
2. FAQ mówił o vUSDT; Terms/Refund milczały (przed korektą).
3. Kod `vusdt.py` / `config.py` opisuje token testowy — to żywy komentarz w repo, nie tylko „historyczne założenie”.
4. Academy „3–4 days” vs katalog 5/30.
5. Brak pełnego podmiotu prawnego w stopce/Terms/Privacy.
6. Copytrading: admin gate już istnieje; „sprzeczność 18× false vs pasek LP” była zawyżona (UI chowa chip gdy offered=false).

### Założenia / deklaracje operatora

- Fee refund w ścieżce vUSDT = tokeny, nie kartą.
- Wartość/wykup vUSDT: forma świadczenia potwierdzona; ekonomika do prawnika — **nie** publikować „no monetary value” ani gwarancji 1:1 bez osobnej decyzji.

### Interpretacje prawne (kierunek, nie kwalifikacja)

- UOKiK: cechy świadczenia, dane przedsiębiorcy, warunki przed umową, trwały nośnik.
- UCPD: myląca może być ogólna prezentacja mimo prawdziwych elementów.
- ESMA: substance over form przy kwalifikacji kryptoaktywów.

## Wdrożone korektury treści i systemu (branch `feat/payout-ondemand-biweekly`)

- FAQ, Terms §5, Refund, homepage, academy, Objectives/`site.js`, portal store + checkout.
- Add-on On-demand w catalog/billing/models/portal; interwał per konto.
- Express usunięty z oferty UI/FAQ/testów (kolumny legacy zostają).
- Dymek support: filtr Free — rozmowy free tylko pod tym filtrem; plakietka/peek bez free.

## Poza zakresem tej iteracji

Pełne dane operatora, kwalifikacja odstąpienia po loginie MT5, Privacy KYC scope, MiCA/AML kategoryczna, dowód on-chain ekonomiki tokena, plan dla klientów kupionych pod starą obietnicą on-demand.
