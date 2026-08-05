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

## Do Twojej decyzji — 4 rzeczy, które się nie zgadzają

Nie zgadywałem, tylko wypisałem. Pełna lista w `02-facts.md` §9.

**1. Błąd w FAQ przy limicie dziennym — to najpilniejsze.**
FAQ podaje przykład: konto 100k, limit 5%, dzień startuje z 102 000 → podłoga **96 900**.
Silnik liczy **97 000** (sprawdziłem, uruchamiając `rules.evaluate`). FAQ liczy 5% od
equity na starcie dnia, a kod liczy 5% od salda startowego.

Różnica to 100 USD **na niekorzyść klienta** — konto pada wcześniej, niż obiecuje FAQ.
Trader ścięty pomiędzy 97 000 a 96 900 ma uzasadnioną reklamację i zrzut ekranu z Twojej
własnej strony. Do poprawienia w `templates/faq.html:68`. Powiedz, czy mam to zrobić.

**2. FAQ: „minimum trading days typically 3–4".**
Sprzedawane plany egzekwują **5**. Liczby 3–4 pochodzą z nieużywanych presetów
w `rules.py` (`PRESETS`). `templates/faq.html:102`.

**3. Instant Funding: „30 dni handlowych" nikt nie pilnuje.**
Tabela objectives pokazuje 30 dni, ale konto Instant startuje od razu jako `funded`,
a wniosek o wypłatę sprawdza tylko: status funded, KYC zaakceptowane, zysk > 0. Klient
może poprosić o wypłatę pierwszego dnia. Albo dopisać warunek w kodzie, albo zdjąć
liczbę ze strony — teraz obiecujesz regułę, której nie masz.

**4. „Reward frequency: Bi-weekly / Every 7 days" to obietnica bez mechanizmu.**
Nigdzie w kodzie nie ma harmonogramu wypłat. Bot ma zakaz podawania konkretnej daty,
dopóki nie zdecydujesz, czy to zobowiązanie operacyjne zespołu, czy ma to robić system.

Poza tym: brak minimalnej kwoty wypłaty, brak progu i harmonogramu prowizji afiliacyjnej,
brak listy krajów wykluczonych. Bot ma to eskalować, a nie zmyślać.

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
