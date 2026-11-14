# Baza wiedzy dla agenta AI obsługującego klientów

Materiał dla bota, który odpisuje na zapytania klientów. Trzy pliki, każdy o czym innym:

| Plik | Co zawiera | Kiedy agent tego używa |
|------|------------|------------------------|
| `01-agent-brief.md` | Persona, ton, granice prawne, kiedy oddać sprawę człowiekowi | Zawsze — to prompt systemowy |
| `02-facts.md` | Fakty: cennik, zasady, wypłaty, KYC, regulamin | Do sprawdzenia każdej liczby |
| `03-answers.md` | Gotowe odpowiedzi i obsługa obiekcji | Do napisania odpowiedzi |

Pliki są po angielsku, bo strona, regulamin i klienci są anglojęzyczni — agent musi
cytować dokładnie to, co widzi klient. Ten README jest po polsku, bo jest dla Ciebie.

## Skąd wzięte są liczby

Wszystkie przeczytane z działającego kodu, nie z tekstów marketingowych:

- cennik i parametry planów — `app/catalog.py` (`_CATALOG`, źródło prawdy dla sklepu)
- mechanika breachy i podłóg — `app/rules.py` (silnik ryzyka)
- wypłaty, KYC, zwrot opłaty — `app/main.py`
- regulamin, zwroty — `templates/legal/`
- tabela objectives — `static/js/site.js` (`renderObjectives`)

**Kiedy to zaktualizować:** po każdej zmianie cen w `_CATALOG`, zmianie limitów w
`rules.py`, zmianie regulaminu albo dodaniu kodu rabatowego. Baza wiedzy rozjechana
z kodem jest gorsza niż jej brak — bot obiecuje wtedy rzeczy, których system nie zrobi.

## Sync (Fix 7) — stan względem FAQ / kodu

Publiczne FAQ i Terms są source of truth dla metod i limitów. Knowledge ma je powtarzać.

**Zsynchronizowane:**
- wypłaty tylko **vUSDT na Polygon** (nie USDT TRC/BEP, bank, Wise)
- minimum wypłaty **$500 lub 2%** (wyższa wartość)
- funded trading days: **5 (2-Step) / 30 (Instant)** przed pierwszą wypłatą
- bi-weekly (14 dni) + On-demand add-on; bez Express Payout
- przykład daily loss: **$97,000**
- kredyt opłaty przy pierwszej wypłacie **tylko na 2-Step** (Instant sprzed zmiany z XI 2026 go zachowuje)
- pełna lista zakazów w Terms §6 (pozycje przeciwstawne, all-in, spam zleceń, VPN, kopiowanie bez dodatku)
- ceny referencyjne: Hyperliquid + feed brokera dla FX, srebra, platyny i JP225 (brokera nie nazywamy)

**Wciąż eskaluj (brak listy w kodzie):** pytania o konkretne kraje / sanctions — publicznie
jest tylko ogólny język AML/sanctions (Fix 9B), bez listy.

## Jak to podłączyć

Asystent AI w dymku supportu już działa (`app/helpdesk.py`, czat przez `POST /api/me/chat`),
ale **tych plików nie czyta**. Jego wiedza to:

- `templates/faq.html` po filtrze: bez pytań o wypłaty, zwroty, opłatę i skalowanie oraz
  bez zdań o nich w pozostałych odpowiedziach (te sprawy idą do człowieka),
- gotowe odpowiedzi o portalu (`PORTAL`) i nadpisania (`POPRAWKI`) w `helpdesk.py`,
- reguły planów z katalogu.

Wniosek praktyczny: żeby zmienić to, co mówi asystent, popraw FAQ (albo `helpdesk.py`),
a nie te pliki. Reguła w FAQ musi stać w osobnym zdaniu bez słów „payout / reward / refund”,
inaczej filtr wytnie ją razem ze zdaniem o pieniądzach (pilnuje tego
`tests/test_helpdesk.py::test_filtr_pieniedzy_nie_wycina_regul`).

Pliki tutaj są ściągą dla ludzi z supportu i dla narzędzi spoza repo. `01-agent-brief.md`
opisuje ton i granice; nie jest promptem asystenta.

## Czego bot nie ma prawa robić

Wypisane szczegółowo w `01-agent-brief.md`, w skrócie: nie doradza w handlu, nie obiecuje
wyniku ani daty, nie wymyśla rabatów, nie używa słów „invest/returns/guaranteed" (to
produkt symulowany — od tego zależy Twoja pozycja prawna) i eskaluje wszystko, co dotyczy
spornej wypłaty, odrzuconego KYC, reklamacji breacha, chargebacku i zgody na zarządzanie
cudzym kontem.
