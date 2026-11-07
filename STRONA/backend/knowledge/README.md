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

**Wciąż eskaluj (brak listy w kodzie):** pytania o konkretne kraje / sanctions — publicznie
jest tylko ogólny język AML/sanctions (Fix 9B), bez listy.

## Jak to podłączyć

Nie ma jeszcze żadnej integracji z LLM w repo (sprawdzone: zero wystąpień
anthropic/openai/claude w `app/`). Kiedy będziesz podłączać:

- `01-agent-brief.md` → prompt systemowy, w całości, na stałe
- `02-facts.md` + `03-answers.md` → kontekst do odpowiedzi

Pliki są małe (~25 KB razem), więc na start można je wkleić w całości do promptu i w ogóle
nie budować wyszukiwania. RAG ma sens dopiero, gdy baza urośnie kilkukrotnie.

Miejsce do wpięcia w istniejący system: zgłoszenia klientów siedzą już w modelach
`SupportTicket` / `TicketMessage` (`app/models.py`), a klient zakłada je przez
`POST /api/me/tickets`. Naturalny punkt: podpowiedź odpowiedzi dla admina przy zgłoszeniu,
zanim bot zacznie odpisywać samodzielnie.

## Czego bot nie ma prawa robić

Wypisane szczegółowo w `01-agent-brief.md`, w skrócie: nie doradza w handlu, nie obiecuje
wyniku ani daty, nie wymyśla rabatów, nie używa słów „invest/returns/guaranteed" (to
produkt symulowany — od tego zależy Twoja pozycja prawna) i eskaluje wszystko, co dotyczy
spornej wypłaty, odrzuconego KYC, reklamacji breacha, chargebacku i zgody na zarządzanie
cudzym kontem.
