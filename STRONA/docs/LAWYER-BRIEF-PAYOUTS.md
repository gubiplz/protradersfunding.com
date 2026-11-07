# Brief dla prawnika — PTF wypłaty / vUSDT / oferta (7 listopada 2026)

Cel: rzetelne, wykonalne i spójne warunki produktu oraz plan naprawy wcześniejszych informacji. Nie chodzi o ukrycie tokena ani o gwarancję „zabezpieczenia PTF”.

## Model do oceny (deklaracja operatora)

1. Nagroda: transfer **vUSDT** (własny token PTF) na Polygon, na portfel klienta.
2. Challenge fee refund: **dodatkowe vUSDT** przy pierwszej wypłacie z danego challenge — **nie** zwrot na kartę.
3. Cadence: default **co 14 dni** między wnioskami; add-on **On-demand** ($299 2-Step / $399 Instant) usuwa ten limit.
4. Eligibility pierwszej wypłaty na funded: **5** dni (2-Step) / **30** (Instant).
5. Express Payout (kolejka $49): wycofany.
6. **Brak danych operatora** (pełna firma, KRS/NIP, siedziba, jurysdykcja) — blocker przed pełną zgodnością informacyjną.

## Co wdrożono w copy (do review)

Jedna matryca w FAQ, Terms §5, Refund, homepage, Objectives, academy, portal store/checkout. Copytrading: „available when offered at checkout”.

Świadomie **nie** dodano twierdzeń „no monetary value / no redemption” bez osobnej decyzji — kod wewnętrznie opisuje token testowy, ale to nie zastępuje kwalifikacji prawnej.

## Pytania wymagające ustalenia

1. Podmiot / merchant of record / rynki / właściwe prawo.
2. Charakter świadczenia za opłatą challenge (demo evaluation vs inne).
3. Token: chain ID, adres kontraktu, mainnet/testnet, mint/burn, transferowalność, wykup, płynność, prawa posiadacza.
4. Czy fee credit w vUSDT może być nazywany „refund” wobec konsumenta.
5. Odstąpienie po wydaniu loginu MT5; zgody; trwały nośnik; chargeback language.
6. Obowiązki wobec klientów, którzy kupili pod obietnicą „on demand / no minimum wait”.
7. Copytrading gdy oferta wyłączona w adminie vs treść FAQ.

## Materiały do przekazania

- Ten brief + `PAYOUT-AUDIT-2026-11.md`
- Dokumenty spółki (gdy będą)
- Specyfikacja tokena / explorer (bez kluczy)
- Zrzuty checkoutu i wersje Terms z dat zakupów
- Konfiguracja backendu: `PAYOUT_INTERVAL_DAYS`, flagi On-demand, testy first/kolejnej wypłaty

## Blockery publikacji „pełnego pakietu compliance”

- [ ] Dane operatora
- [ ] Decyzja o ekonomice/ujawnieniu vUSDT
- [ ] Ocena odstąpienia i checkout zgód
- [ ] Plan komunikacji do istniejących klientów
- [ ] Potwierdzenie, że system egzekwuje opublikowaną matrycę na wszystkich 18 produktach
