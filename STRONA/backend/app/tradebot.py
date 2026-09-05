"""Trade BOT — generator aktywnosci handlowej dla konta challenge.

Admin wlacza bota na konkretnym koncie, a ten od tego momentu prowadzi na nim
transakcje tak, jakby handlowal pro trader: rozne instrumenty, przewaga wygranych,
rosnaca krzywa equity. Poller zamiast czytac MT5 bierze snapshot stad — reszta
lancucha (rules.evaluate, snapshoty, breache, przejscia faz) zostaje bez zmian,
wiec bot przechodzi fazy PRAWDZIWYM silnikiem regul, a nie obok niego.

Trzy zasady, ktore trzymaja to w ryzach:

1. **Kazdy bot jest inny.** Persona wyprowadzana jest deterministycznie z konta
   (`bot_seed`): wlasny koszyk instrumentow, win rate, wielkosc pozycji, dzienny
   cel, szansa na dzien stratny. Dwa konta nie moga wygenerowac tej samej serii.
   Admin wybiera tylko styl i TEMPO, czyli ile transakcji dziennie ma robic bot
   (`PACE_TRADES`) — cala reszta rytmu dnia wynika z tej jednej liczby.

2. **Liczby sie zgadzaja.** Wolumen wynika z ryzyka i dystansu stopa, a cena
   zamkniecia jest policzona WSTECZ z P&L:
   `close = open ± pnl / (lots * point_value)`. Kto sprawdzi kalkulatorem,
   dostanie dokladnie te kwote, ktora widzi w tabeli.

3. **Bot nie lamie regul konta.** Dzienny cel jest ulamkiem limitu straty, wolumen
   jest przycinany do `max_lots`, a wychylenie floatingu ma twardy sufit. To nie
   jest „mam nadzieje, ze sie zmiesci" — to ograniczenia przy planowaniu pozycji.

4. **Bot handluje tylko wtedy, gdy rynek jest otwarty.** Forex, indeksy i towary
   stoja przez caly weekend, wiec konto BEZ dodatku Weekend Trading nie dostaje
   w sobote i niedziele ani jednego wejscia. Konto Z dodatkiem handluje dalej,
   ale wylacznie krypto — bo tylko ono chodzi 24/7.

Pozycja otwarta zyje w tabeli `trades` (`status='open'`), nie w pamieci procesu,
wiec restart serwera nie gubi transakcji ani ciaglosci krzywej.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import rules
from .config import get_settings
from .feed import MarketSnapshot
from .models import Account, Trade

settings = get_settings()

MIN_LOT = 0.01
STYLES = ("scalper", "balanced", "swing")
PACES = ("light", "steady", "busy")

# Najmniejsza transakcja, jaka ma jeszcze sens w dolarach. Ponizej tego progu
# `_close_trade` zaokragla cene do ticka instrumentu i ruch wychodzi ponizej
# jednego ticka: cena zamkniecia rowna cenie otwarcia, `pnl = 0.00`, saldo stoi
# w miejscu i bot probuje w kolko. Przy suficie zysku to jest roznica miedzy
# „ostatnie wejscie ladu je na suficie" a nieskonczona seria transakcji-widm.
MIN_FILL = 1.0

# --- tryb zjazdu (`bot_mode='doom'`) ---
# Ile z DZIENNEGO limitu straty wolno oddac w jednym dniu, gdy celem jest
# limit CALKOWITY. Bez tego pierwszy dzien zjazdu zlamalby limit dzienny
# i breach padlby z niewlasciwego powodu.
DOOM_DAILY_SAFE = 0.85
# Win rate persony przemnozony przez to w trybie zjazdu. Nie zero: kilka
# wygranych w serii strat to roznica miedzy „trader sie posypal" a „admin
# kliknal ubij konto".
DOOM_WIN_SCALE = 0.45


# --------------------------------------------------------------------------- #
#  Instrumenty                                                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Instrument:
    """`point_value` = USD na 1.0 lota przy ruchu ceny o 1.0.

    `stop_move` to typowy dystans stop-lossa w jednostkach ceny — z niego wychodzi
    wolumen, a z wolumenu cena zamkniecia. Dzieki temu ruch ceny na kazdym
    instrumencie wyglada wiarygodnie niezaleznie od wielkosci konta.
    """
    symbol: str
    price: float
    tick: float
    point_value: float
    stop_move: float
    usd_base: bool = False      # USDJPY/USDCAD: wartosc punktu zalezy od kursu


INSTRUMENTS: dict[str, Instrument] = {i.symbol: i for i in [
    Instrument("XAUUSD", 2385.00, 0.01, 100, 6.5),
    Instrument("EURUSD", 1.08500, 0.00001, 100_000, 0.0028),
    Instrument("GBPUSD", 1.27200, 0.00001, 100_000, 0.0034),
    Instrument("USDJPY", 157.400, 0.001, 100_000, 0.420, usd_base=True),
    Instrument("AUDUSD", 0.66500, 0.00001, 100_000, 0.0022),
    Instrument("USDCAD", 1.36900, 0.00001, 100_000, 0.0030, usd_base=True),
    Instrument("NAS100", 19850.0, 0.1, 10, 62.0),
    Instrument("US30", 39420.0, 0.1, 10, 105.0),
    Instrument("GER40", 18320.0, 0.1, 10, 48.0),
    Instrument("USOIL", 78.500, 0.01, 1000, 0.55),
    Instrument("BTCUSD", 68500.0, 0.1, 1, 520.0),
    Instrument("ETHUSD", 3550.00, 0.01, 10, 34.0),
]}

# Jedyne instrumenty czynne 24/7. Cala reszta stoi od piatkowej polnocy czasu
# serwera do poniedzialku, wiec w weekend moze pojawic sie WYLACZNIE to.
CRYPTO_SYMBOLS: frozenset[str] = frozenset({"BTCUSD", "ETHUSD"})


def _point_value(inst: Instrument, price: float) -> float:
    return (inst.point_value / price) if inst.usd_base else inst.point_value


def _decimals(step: float) -> int:
    return max(0, -int(math.floor(math.log10(step) + 1e-9)))


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, _decimals(step))


# --------------------------------------------------------------------------- #
#  Persona — charakter konkretnego bota                                        #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Persona:
    style: str
    pace: str
    win_rate: float
    avg_r: float
    risk_pct: float          # % balansu ryzykowane na jedna pozycje
    daily_target_pct: float  # dzienny cel zysku w % konta
    red_day_odds: float
    trades_per_day: int
    symbols: tuple[str, ...]
    weights: tuple[float, ...]


# Tempo mowi, ILE TRANSAKCJI DZIENNIE robi bot — i nic wiecej. Reszta (czas
# trzymania pozycji, dlugosc przerwy) wynika z tej liczby, patrz `_cykl_sek`.
# Wczesniej tempo bylo ustawiane „co ile sekund": najszybszy tryb otwieral
# pozycje co 45 s, czyli grubo ponad tysiac dziennie, i historia konta wygladala
# jak spam bota, a nie dzien tradera.
PACE_TRADES: dict[str, tuple[int, int]] = {
    "light": (1, 2),
    "steady": (4, 8),
    # Widelki wyzsze niz etykieta „okolo 20" w panelu, bo dzien konczy sie takze
    # na osiagnietym dziennym celu: czesc zaplanowanych wejsc nie dochodzi do
    # skutku i realnie wychodzi ~85% planu. Zmierzone na 5 dobach: 17-22 dziennie.
    "busy": (20, 26),
}

# Nazwy sprzed przejscia na „transakcje dziennie". Konta z wlaczonym botem maja
# stara wartosc zapisana w kolumnie `bot_pace`, wiec musi sie dalej rozwiazywac —
# po kolejnosci: najwolniejsze na najwolniejsze.
PACE_ALIASY: dict[str, str] = {"realistic": "light", "active": "steady", "demo": "busy"}

DZIEN_SEK = 24 * 3600
# Ile z cyklu zajmuje trzymanie pozycji (reszta to przerwa miedzy wejsciami).
HOLD_UDZIAL = (0.30, 0.50)
# Sufit i podloga trzymania: przy 1 transakcji dziennie 40% doby to 10 godzin
# w rynku, a przy 20 — kilkanascie minut. Jedno i drugie wyglada nienaturalnie.
HOLD_MAX_SEK = 6 * 3600
HOLD_MIN_SEK = 12 * 60

# Mnozniki stylu: (win_rate, avg_r, risk_pct). Liczby transakcji tu NIE MA —
# ustala ja tempo, styl tylko przesuwa ja w obrebie widelek (`_STYLE_UDZIAL`).
_STYLE_TUNING: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "scalper": ((0.66, 0.78), (1.1, 1.7), (0.12, 0.28)),
    "balanced": ((0.60, 0.72), (1.5, 2.2), (0.18, 0.38)),
    "swing": ((0.52, 0.64), (2.1, 3.2), (0.25, 0.50)),
}

# Gdzie w widelkach tempa siada dany styl: scalper przy gornej granicy,
# swing przy dolnej. Zakresy zachodza na siebie, zeby dwa konta z tym samym
# tempem i stylem nie musialy miec identycznej liczby wejsc.
_STYLE_UDZIAL: dict[str, tuple[float, float]] = {
    "scalper": (0.55, 1.00),
    "balanced": (0.25, 0.75),
    "swing": (0.00, 0.45),
}


def normalize_pace(value: str | None) -> str:
    """Nazwa tempa sprowadzona do obowiazujacej — z obsluga starych wartosci."""
    v = (value or "").strip().lower()
    v = PACE_ALIASY.get(v, v)
    return v if v in PACES else "steady"


def _cykl_sek(p: Persona) -> float:
    """Ile sekund przypada na jedna transakcje: trzymanie + przerwa po niej.

    Doba podzielona przez zadana liczbe wejsc. Dzieki temu „4-8 transakcji
    dziennie" jest obietnica, ktora naprawde da sie policzyc w historii konta,
    a nie luznym opisem trybu.
    """
    return DZIEN_SEK / max(1, p.trades_per_day)


def seed_for(acc: Account) -> int:
    """Stabilny, unikalny seed konta — decyduje o calym charakterze bota.

    Maska 31 bitow jest OBOWIAZKOWA: kolumna `bot_seed` to INTEGER, czyli int4
    w PostgreSQL (max 2 147 483 647). SQLite typuje dynamicznie i przyjmowal
    szersza wartosc bez mrugniecia, wiec na Supabase wychodzilo dopiero
    `NumericValueOutOfRange` przy starcie bota. 2 mld wariantow w zupelnosci
    wystarcza, zeby persony sie nie powtarzaly.
    """
    raw = f"{acc.id}:{acc.login}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & 0x7FFFFFFF


def persona_for(acc: Account) -> Persona:
    """Deterministyczna persona konta. To ona gwarantuje, ze leaderboard nie
    zamieni sie w liste klonow z identycznymi transakcjami."""
    rng = random.Random(acc.bot_seed if acc.bot_seed is not None else seed_for(acc))
    style = acc.bot_style if acc.bot_style in STYLES else "balanced"
    pace = normalize_pace(acc.bot_pace)
    wr, ar, rp = _STYLE_TUNING[style]

    pool = list(INSTRUMENTS)
    rng.shuffle(pool)
    symbols = tuple(pool[:rng.randint(4, 6)])
    # Wagi: jeden-dwa instrumenty „ulubione", reszta rzadziej — jak u ludzi.
    weights = tuple(rng.uniform(0.5, 3.0) for _ in symbols)

    dolna, gorna = PACE_TRADES[pace]
    udzial = rng.uniform(*_STYLE_UDZIAL[style])
    return Persona(
        style=style,
        pace=pace,
        win_rate=rng.uniform(*wr),
        avg_r=rng.uniform(*ar),
        risk_pct=rng.uniform(*rp),
        # Dzienny cel NIE zalezy od tempa: ile trader zarabia dziennie to co
        # innego niz w ilu wejsciach to robi. Wczesniej szybsze tempo mnozylo
        # tez cel, wiec „wiecej transakcji" po cichu znaczylo „szybsza krzywa".
        daily_target_pct=rng.uniform(0.25, 1.10),
        red_day_odds=rng.uniform(0.08, 0.20),
        trades_per_day=max(1, round(dolna + (gorna - dolna) * udzial)),
        symbols=symbols,
        weights=weights,
    )


# --------------------------------------------------------------------------- #
#  Czas serwera (ta sama strefa co poller, bez importu poller -> brak cyklu)    #
# --------------------------------------------------------------------------- #
def _server_now(now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + timedelta(hours=settings.server_utc_offset_hours)


def _day_key(now: datetime | None = None) -> str:
    return _server_now(now).strftime("%Y-%m-%d")


def _naive(dt: datetime) -> datetime:
    """Baza trzyma daty bez strefy — porownania musza byc na wspolnym typie."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# --------------------------------------------------------------------------- #
#  Kalendarz rynku                                                             #
# --------------------------------------------------------------------------- #
def is_weekend(now: datetime | None = None) -> bool:
    """Czy rynek stoi (poza krypto). Liczone w czasie SERWERA MT5 (domyslnie
    UTC+2) — akurat w tej strefie tydzien handlowy zaczyna sie w poniedzialek
    o 00:00 i konczy w piatek o 23:59, wiec sama nazwa dnia wystarcza za
    kalendarz sesji i niedzielne otwarcie wychodzi samo z siebie."""
    return _server_now(now).weekday() >= 5


def market_closed_for(acc: Account, now: datetime | None = None) -> bool:
    """Czy dla TEGO konta rynek jest teraz zamkniety.

    Weekend Trading to platny dodatek do challenge'u (`Account.weekend_trading`).
    Bez niego bot w sobote i niedziele nie ma czego handlowac i musi stac.
    """
    return is_weekend(now) and not bool(getattr(acc, "weekend_trading", False))


def _week_close(now: datetime) -> datetime:
    """Najblizsze zamkniecie tygodnia (sobota 00:00 czasu serwera), zwrocone
    w tej samej strefie co `now` — do przyciecia pozycji z piatku."""
    srv = _server_now(now)
    sobota = (srv + timedelta(days=5 - srv.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return sobota - timedelta(hours=settings.server_utc_offset_hours)


def _tradable(p: Persona, weekend: bool) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """Koszyk instrumentow na teraz. W weekend zostaje samo krypto — a persona,
    ktora nie ma go w koszyku, dostaje je na ten czas z listy, bo inaczej konto
    z dodatkiem Weekend Trading sterczaloby bezczynnie mimo otwartego rynku."""
    if not weekend:
        return p.symbols, p.weights
    pary = [(s, w) for s, w in zip(p.symbols, p.weights) if s in CRYPTO_SYMBOLS]
    if not pary:
        krypto = tuple(sorted(CRYPTO_SYMBOLS))
        return krypto, tuple(1.0 for _ in krypto)
    return tuple(s for s, _ in pary), tuple(w for _, w in pary)


# --------------------------------------------------------------------------- #
#  Sterowanie botem                                                            #
# --------------------------------------------------------------------------- #
DOOM_DAYS_DEFAULT = 5.0


def start(session, acc: Account, *, style: str = "balanced", pace: str = "steady",
          target_pct: float = 0.0, mode: str = "profit",
          doom_days: float | None = None, doom_limit: str = "overall") -> None:
    acc.bot_enabled = True
    acc.bot_style = style if style in STYLES else "balanced"
    acc.bot_pace = normalize_pace(pace)
    acc.bot_target_pct = max(0.0, round(float(target_pct or 0.0), 2))
    acc.bot_paused = False
    if acc.bot_seed is None:
        acc.bot_seed = seed_for(acc)
    acc.bot_started_at = datetime.now(timezone.utc)
    _apply_mode(acc, mode, doom_days, doom_limit)
    session.commit()


def _apply_mode(acc: Account, mode: str, doom_days: float | None,
                doom_limit: str) -> None:
    """Ustawia kierunek jazdy. Bez commita — wołane pod cudzą transakcją."""
    if mode == "doom":
        acc.bot_mode = "doom"
        # Sufit zysku i zjazd wykluczają się: jedno mówi „stań na +9,97%",
        # drugie „zjedź na podłogę". Zostawienie obu dałoby konto, które
        # nie rusza się w żadną stronę.
        acc.bot_target_pct = 0.0
        acc.bot_doom_limit = doom_limit if doom_limit in ("overall", "daily") else "overall"
        dni = max(0.5, float(doom_days if doom_days is not None else DOOM_DAYS_DEFAULT))
        acc.bot_doom_deadline = datetime.now(timezone.utc) + timedelta(days=dni)
    else:
        acc.bot_mode = "profit"
        acc.bot_doom_deadline = None


def set_mode(session, acc: Account, mode: str, *, doom_days: float | None = None,
             doom_limit: str = "overall") -> dict:
    """Przełącza bota między jazdą po zysk a zjazdem na drawdown."""
    _apply_mode(acc, mode, doom_days, doom_limit)
    session.commit()
    return {"mode": acc.bot_mode, "target_pct": acc.bot_target_pct,
            "doom_deadline": (acc.bot_doom_deadline.isoformat()
                              if acc.bot_doom_deadline else None)}


def stop(session, acc: Account, now: datetime | None = None) -> None:
    """Wylacza bota. Otwarta pozycja jest domykana po BIEZACYM floatingu — trader
    widzial juz to equity, wiec balans nie moze sie cofnac."""
    now = now or datetime.now(timezone.utc)
    open_tr = _open_trade(session, acc)
    if open_tr is not None:
        _close_trade(open_tr, _floating_pnl(open_tr, now), now)
        acc.balance = round((acc.balance or 0.0) + open_tr.pnl, 2)
        acc.equity = acc.balance
        acc.open_pnl = 0.0
    acc.bot_enabled = False
    acc.bot_paused = False
    _apply_mode(acc, "profit", None, "overall")
    session.commit()


def set_paused(session, acc: Account, paused: bool) -> None:
    """Pauza vs Stop: pauza wstrzymuje tylko OTWIERANIE nowych pozycji. Konto
    zostaje pod kontrolą bota, więc saldo nie resynchronizuje się do feedu i
    krzywa equity nie dostaje uskoku — po wznowieniu bot płynnie wraca."""
    acc.bot_paused = bool(paused)
    session.commit()


def is_doom(acc: Account) -> bool:
    """Czy bot jedzie w dół, na złamanie limitu, zamiast w górę po zysk."""
    return (getattr(acc, "bot_mode", None) or "profit") == "doom"


def cap_equity(acc: Account) -> float | None:
    """Saldo, ponad które bot nie ma prawa wyjść. None = bez limitu.

    Sufit liczymy w DOLARACH, nie w procentach, bo tylko tak da się go porównać
    z progiem fazy: `rules.profit_target_equity` też jest kwotą, a `Float` nie
    trzyma 9,97 dokładnie i porównanie procentów rozjeżdżałoby się na ostatnim
    miejscu po przecinku.
    """
    if is_doom(acc) or not acc.bot_target_pct or not acc.initial_balance:
        return None
    return round(acc.initial_balance * (1 + acc.bot_target_pct / 100.0), 2)


def doom_floor(acc: Account) -> float:
    """Podłoga, w którą ma trafić zjazd — TĄ SAMĄ formułą co silnik reguł.

    `display_metrics` nie mutuje stanu konta i liczy obie podłogi dokładnie tak,
    jak zrobi to potem `rules.evaluate`, więc bot celuje w ten sam próg, na
    którym breach naprawdę padnie. Przepisana formuła rozjechałaby się przy
    pierwszej zmianie reguł.
    """
    m = rules.display_metrics(
        rules.config_from_account(acc),
        balance=float(acc.balance or 0.0), equity=float(acc.equity or 0.0),
        peak_equity=float(acc.peak_equity or 0.0),
        day_start_equity=float(acc.day_start_equity or acc.balance or 0.0),
        trading_days=int(acc.trading_days_count or 0))
    return m["daily_floor"] if _doom_daily(acc) else m["overall_floor"]


def _doom_daily(acc: Account) -> bool:
    return (getattr(acc, "bot_doom_limit", None) or "overall") == "daily"


def _frakcja_dnia_do_konca(now: datetime) -> float:
    """Ile doby serwera jeszcze zostało (0–1). Godzin sesji nie ma (patrz
    `_should_open` pkt 4), więc doba kalendarzowa jest właściwą miarą."""
    srv = _server_now(now)
    minelo = srv.hour * 3600 + srv.minute * 60 + srv.second
    return (DZIEN_SEK - minelo) / DZIEN_SEK


def _doom_days_left(acc: Account, now: datetime) -> float:
    """Ile dni zostało do końca zjazdu. Minimum doba: cała reszta dystansu
    wrzucona w kilka godzin dałaby jeden gigantyczny dzień zamiast osuwania."""
    if not getattr(acc, "bot_doom_deadline", None):
        return 1.0
    zostalo = (_naive(acc.bot_doom_deadline) - _naive(now)).total_seconds() / DZIEN_SEK
    return max(1.0, zostalo)


def _doom_day_target(acc: Account, balance: float, now: datetime) -> float:
    """Ujemny cel na dziś: porcja dystansu do podłogi, z rozrzutem.

    `bot_doom_deadline` rozkłada zjazd tylko przy limicie CAŁKOWITYM — przy
    dziennym jest bez znaczenia (patrz niżej).
    """
    # Dystans liczymy od SALDA NA START DNIA, nie od bieżącego. Cel dnia jest
    # wszędzie porównywany ze zrealizowanym P&L dnia (`_today_realized`), który
    # też liczy się od tego punktu — cel liczony od bieżącego salda kurczyłby
    # się z każdą stratą i bramka „dzień zrobiony" w `_should_open` zamykała
    # sesję daleko nad podłogą.
    #
    # +MIN_FILL, bo ostatni krok ma PRZEBIĆ podłogę, nie stanąć na niej —
    # `_close_trade` zaokrągla cenę do ticka i zjada centy, a `rules` porównuje
    # equity z podłogą co do grosza.
    # Ten sam warunek świeżości co w `_today_realized`: na pierwszym ticku
    # nowego dnia baseline jeszcze wisi na wczoraj.
    if acc.day_key == _day_key(now) and acc.day_start_balance:
        start_dnia = float(acc.day_start_balance)
    else:
        start_dnia = balance
    dystans = max(0.0, start_dnia - doom_floor(acc)) + MIN_FILL
    rng = random.Random(f"{acc.bot_seed}:doom:{_day_key(now)}")

    if _doom_daily(acc):
        # Limit DZIENNY zeruje się o północy, więc rozkładanie go na dni nie ma
        # sensu — a wręcz jest niewykonalne: podłoga dnia to `start dnia − 5%`,
        # podłoga całkowita stoi na −10%, więc gdy tylko konto zjedzie poniżej
        # −5%, w dzienną już nie da się trafić przed całkowitą. Zjazd na limit
        # dnia jest z definicji jedną złą sesją. Mnożnik ponad 1.0, bo stanięcie
        # tuż nad podłogą to konto, które nie poległo, a przestało handlować.
        return -dystans * rng.uniform(1.05, 1.25)

    porcja = dystans / _doom_days_left(acc, now) * rng.uniform(0.85, 1.20)
    limit_dnia = (acc.max_daily_loss_pct or 0.0) / 100.0 * (acc.initial_balance or 0.0)
    if limit_dnia > 0:
        porcja = min(porcja, limit_dnia * DOOM_DAILY_SAFE)
    return -porcja


def set_target(session, acc: Account, target_pct: float) -> dict:
    """Zmienia docelowy zysk DZIAŁAJĄCEGO bota. Zwraca nową wartość i sufit.

    Po osiągnięciu celu bot przestaje otwierać pozycje, ale zostaje włączony —
    bez tej funkcji jedynym wyjściem było zatrzymanie go (co resynchronizuje
    saldo do feedu i robi uskok na krzywej) albo pauza, która niczego nie zmienia.
    Podniesienie celu puszcza bota dalej z tego samego miejsca, bez uskoku.

    OBNIŻENIE celu przy otwartej pozycji, której plan przebija nowy sufit, domyka
    ją po BIEŻĄCYM floatingu — tą samą mechaniką co `stop()`: equity sprzed
    zamknięcia staje się saldem po nim, więc krzywa nie dostaje skoku, a
    w historii wygląda to jak ręczne zamknięcie. Samego `plan_pnl` żywej
    transakcji nie ruszamy: `_floating_pnl` skaluje nim szum, więc każda zmiana
    w locie dałaby mikro-skok na wykresie.

    0 znosi limit — bot handluje bez górnej granicy zysku.

    `cap_overshot` mówi, że saldo JUŻ jest ponad nowym sufitem. Cofnąć się nie
    da (saldo tylko rośnie albo spada wynikiem transakcji), więc panel musi to
    powiedzieć wprost zamiast obiecywać wynik, którego nie dowiezie.
    """
    acc.bot_target_pct = max(0.0, round(float(target_pct or 0.0), 2))
    cap = cap_equity(acc)
    now = datetime.now(timezone.utc)
    balance = float(acc.balance if acc.balance is not None else acc.initial_balance or 0.0)

    if cap is not None:
        open_tr = _open_trade(session, acc)
        if open_tr is not None and balance + (open_tr.plan_pnl or 0.0) > cap:
            _close_trade(open_tr, _floating_pnl(open_tr, now), now)
            balance = round(balance + open_tr.pnl, 2)
            acc.balance = balance
            acc.equity = balance
            acc.open_pnl = 0.0

    session.commit()
    return {"target_pct": acc.bot_target_pct, "cap_equity": cap,
            "cap_overshot": cap is not None and balance > cap}


def _open_trade(session, acc: Account) -> Trade | None:
    return (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "open")
            .order_by(Trade.id.desc()).first())


# --------------------------------------------------------------------------- #
#  Rdzen — jeden tick pollera                                                  #
# --------------------------------------------------------------------------- #
def tick(session, acc: Account, now: datetime | None = None) -> MarketSnapshot:
    """Prowadzi/otwiera/zamyka pozycje i oddaje snapshot w formacie feedu."""
    now = now or datetime.now(timezone.utc)
    p = persona_for(acc)
    balance = float(acc.balance or acc.initial_balance)
    open_tr = _open_trade(session, acc)

    if open_tr is not None:
        if open_tr.plan_close_at is not None and _naive(now) >= _naive(open_tr.plan_close_at):
            # Balans dopisujemy z `tr.pnl` PO zamknieciu, bo tam siedzi kwota
            # przeliczona z zaokraglonej ceny — inaczej tabela nie zgadzalaby sie
            # o kilka groszy z krzywa balansu.
            _close_trade(open_tr, open_tr.plan_pnl, now)
            balance = round(balance + open_tr.pnl, 2)
            # Sesja ma autoflush=False, a `_should_open` pyta BAZE o ostatnia
            # zamknieta transakcje. Bez tego flusha zamkniecie sprzed chwili jest
            # dla niej niewidoczne, odstep liczy sie od poprzedniej pozycji i co
            # druga otwiera sie natychmiast — dzien wychodzil o polowe gestszy,
            # niz mowi tempo.
            session.flush()
            open_tr = None
        else:
            floating = _floating_pnl(open_tr, now)
            open_tr.pnl = round(floating, 2)

    if open_tr is None and _should_open(session, acc, p, balance, now):
        open_tr = _open_new(session, acc, p, balance, now)

    floating = round(_floating_pnl(open_tr, now), 2) if open_tr is not None else 0.0
    lots = open_tr.lots if open_tr is not None else 0.0
    return MarketSnapshot(
        balance=round(balance, 2),
        equity=round(balance + floating, 2),
        open_pnl=floating,
        has_open_position=open_tr is not None,
        volume_lots=lots,
        volume_known=True,
    )


def _floating_pnl(tr: Trade, now: datetime) -> float:
    """P&L otwartej pozycji: gladkie dojscie do zaplanowanego wyniku plus szum,
    ktory wygasa do zera na zamknieciu (krzywa nie ma skoku przy realizacji)."""
    if tr.plan_close_at is None:
        return tr.pnl or 0.0
    span = (_naive(tr.plan_close_at) - _naive(tr.opened_at)).total_seconds()
    if span <= 0:
        return tr.plan_pnl
    t = min(1.0, max(0.0, (_naive(now) - _naive(tr.opened_at)).total_seconds() / span))
    smooth = t * t * (3 - 2 * t)
    ph = (tr.id or 1) * 0.618
    wobble = abs(tr.plan_pnl) * 0.55 * (1 - t) * (
        0.6 * math.sin(9.7 * t + ph) + 0.4 * math.sin(21.3 * t + 2 * ph))
    return tr.plan_pnl * smooth + wobble


def _close_trade(tr: Trade, pnl: float, now: datetime) -> None:
    inst = INSTRUMENTS.get(tr.symbol)
    pv = _point_value(inst, tr.open_price) if inst else 1.0
    sign = 1 if tr.side == "buy" else -1
    move = pnl / (tr.lots * pv) if tr.lots and pv else 0.0
    step = inst.tick if inst else 0.01
    # Ruch OBCINAMY do pelnych tickow, nigdy nie zaokraglajac w gore. Cena
    # zamkniecia jest zrodlem prawdy dla P&L, wiec zaokraglenie do NAJBLIZSZEGO
    # ticka potrafilo dolozyc kilka centow ponad plan — a pod sufitem zysku te
    # centy przepychaly saldo ponad prog fazy i konto zdawalo przez przypadek.
    ticki = math.floor(abs(move) / step + 1e-9)
    tr.close_price = _round_to(tr.open_price + sign * math.copysign(ticki * step, move), step)
    # P&L liczymy z ZAOKRAGLONEJ ceny, zeby tabela zgadzala sie co do centa.
    tr.pnl = round(sign * (tr.close_price - tr.open_price) * tr.lots * pv, 2)
    tr.closed_at = now
    tr.status = "closed"


def _today_realized(session, acc: Account, now: datetime) -> float:
    """Zysk zrealizowany dzisiaj. Liczony z balansu (darmowe), z zabezpieczeniem
    na pierwszy tick, gdy silnik regul nie ustawil jeszcze baseline'u dnia."""
    if not acc.day_start_balance or acc.day_key != _day_key(now):
        return 0.0
    return float(acc.balance or 0.0) - float(acc.day_start_balance)


def _day_target(acc: Account, p: Persona, balance: float, now: datetime) -> float:
    """Cel na dzisiaj. Czasem ujemny — pro trader tez ma stratne dni, a krzywa
    bez ani jednego minusa wyglada jak wyklejona."""
    if is_doom(acc):
        return _doom_day_target(acc, balance, now)
    rng = random.Random(f"{acc.bot_seed}:{_day_key(now)}")
    if rng.random() < p.red_day_odds:
        return -balance * rng.uniform(0.10, 0.45) / 100.0
    return balance * p.daily_target_pct / 100.0


def _should_open(session, acc: Account, p: Persona, balance: float, now: datetime) -> bool:
    # 0) pauza — bez nowych wejsc. Otwarta pozycja dochodzi do konca normalnie,
    #    tak jak przy wylaczeniu EA: nie porzuca sie transakcji w polowie.
    if getattr(acc, "bot_paused", False):
        return False

    # 1) osiagniety docelowy zysk konta -> bot przestaje handlowac. Test jest
    #    KWOTOWY i z marginesem `MIN_FILL`: resztka rzedu centow nie przezyje
    #    zaokraglenia ceny do ticka i wygenerowalaby transakcje z `pnl = 0.00`,
    #    ktora nie rusza salda, wiec bot otwieralby ja w nieskonczonosc.
    cap = cap_equity(acc)
    if cap is not None and balance >= cap - MIN_FILL:
        return False

    # 2) dzienny cel zrobiony (w obie strony) -> koniec sesji
    target = _day_target(acc, p, balance, now)
    realized = _today_realized(session, acc, now)
    if (target >= 0 and realized >= target) or (target < 0 and realized <= target):
        return False

    # 3) odstep po ostatniej transakcji
    last = (session.query(Trade)
            .filter(Trade.account_id == acc.id, Trade.status == "closed")
            .order_by(Trade.id.desc()).first())
    if last is not None and last.closed_at is not None:
        # Przerwa to reszta cyklu po odjeciu czasu, ktory pozycja FAKTYCZNIE
        # przesiedziala w rynku. Dzieki temu dluzsze trzymanie skraca postoj,
        # a dzienna liczba wejsc trzyma sie zadanej niezaleznie od tego, jak
        # potoczyla sie poprzednia transakcja.
        trwanie = max(0.0, (_naive(last.closed_at) - _naive(last.opened_at)).total_seconds())
        rng = random.Random((last.id or 0) * 31 + 7)
        przerwa = max(60.0, _cykl_sek(p) - trwanie) * rng.uniform(0.75, 1.25)
        if (_naive(now) - _naive(last.closed_at)).total_seconds() < przerwa:
            return False

    # 4) rynek zamkniety -> ZERO wejsc, niezaleznie od tempa. To kalendarz gieldy,
    #    a nie styl gry: w weekend broker takiego zlecenia by nie przyjal. Konto
    #    z dodatkiem Weekend Trading handluje dalej, ale samym krypto (_tradable).
    if market_closed_for(acc, now):
        return False

    # Godzin sesji NIE ma celowo: forex, zloto i indeksy chodza w dzien roboczy
    # praktycznie na okraglo, wiec wejscie o 04:00 jest tak samo prawdziwe jak
    # o 14:00. Jedyna przerwa, ktora naprawde istnieje, to weekend (punkt 4).
    return True


def _open_new(session, acc: Account, p: Persona, balance: float, now: datetime) -> Trade:
    """Wolumen wynika z ryzyka i dystansu stopa; wynik z win-rate persony."""
    n = session.query(Trade).filter(Trade.account_id == acc.id).count()
    rng = random.Random(f"{acc.bot_seed}:{n}")

    weekend = is_weekend(now)
    symbols, weights = _tradable(p, weekend)
    symbol = rng.choices(symbols, weights=weights, k=1)[0]
    # Bez tego wagi potrafia wygenerowac cztery wejscia z rzedu w ten sam symbol,
    # a mial byc widoczny KOSZYK instrumentow. Jedno przelosowanie wystarczy.
    if len(symbols) > 1:
        prev = (session.query(Trade.symbol)
                .filter(Trade.account_id == acc.id)
                .order_by(Trade.id.desc()).limit(1).scalar())
        if prev == symbol:
            symbol = rng.choices(symbols, weights=weights, k=1)[0]
    inst = INSTRUMENTS[symbol]
    side = "buy" if rng.random() < 0.5 else "sell"
    sign = 1 if side == "buy" else -1

    open_price = _round_to(inst.price * (1 + rng.gauss(0, 0.004)), inst.tick)
    pv = _point_value(inst, open_price)

    target = _day_target(acc, p, balance, now)
    doom = is_doom(acc)

    if doom:
        # Sizing idzie z DZIENNEJ PORCJI STRATY, nie z celu persony (ten jest o
        # rzad wielkosci mniejszy) i bez sufitu `risk_pct` — konto ma sie
        # posypac, a nie ostroznie zarzadzac ryzykiem. Limit lotow nizej i tak
        # zostaje, bo jego zlamanie to osobny, zdradzajacy breach.
        #
        # Dzielimy przez wejscia, ktore w tym dniu JESZCZE ZOSTALY, nie przez
        # caly dzienny przydzial: zjazd zlecony po poludniu ma mniej okazji,
        # a wygrane po drodze odrabiaja czesc dystansu — bez tej korekty dzien
        # konczyl sie w polowie drogi do podlogi. Rosnaca pod wieczor pozycja
        # to zreszta dokladnie obraz tradera, ktory goni strate.
        zrealizowane = _today_realized(session, acc, now)
        zostalo_wejsc = max(1.0, p.trades_per_day * _frakcja_dnia_do_konca(now))
        risk_usd = abs(min(0.0, target - zrealizowane)) / zostalo_wejsc
    else:
        # Wielkosc pozycji wynika z dziennego celu rozlozonego na planowana liczbe
        # wejsc — inaczej dwie wygrane realizowalyby caly dzien i historia bylaby
        # pusta. `risk_pct` zostaje juz tylko jako sufit bezpieczenstwa.
        day_goal = balance * p.daily_target_pct / 100.0
        edge = max(0.15, p.win_rate * p.avg_r - (1 - p.win_rate) * 0.8)
        risk_usd = min(day_goal / (p.trades_per_day * edge), balance * p.risk_pct / 100.0)
    lots = max(MIN_LOT, round(risk_usd / (inst.stop_move * pv), 2))
    if acc.max_lots and acc.max_lots > 0:
        lots = min(lots, round(acc.max_lots, 2))

    if doom:
        # Male zyski, duze straty — dokladnie tak wyglada konto tradera, ktory
        # sie posypal. Samo obnizenie win rate nie wystarcza: przy `avg_r`
        # persony jedna wygrana odrabiala trzy straty i krzywa stalaby w miejscu.
        win = rng.random() < p.win_rate * DOOM_WIN_SCALE
        r = rng.uniform(0.25, 0.60) if win else -rng.uniform(1.2, 2.4)
    else:
        win = rng.random() < p.win_rate
        r = p.avg_r * rng.uniform(0.7, 1.35) if win else -rng.uniform(0.55, 1.0)
    plan_pnl = lots * inst.stop_move * pv * r

    # Przyciecie do dziennego celu: bot nie „przestrzeliwuje" planu na dzien,
    # dzieki czemu krzywa rosnie rownomiernie zamiast skakac. Symetrycznie
    # w dol, bo w trybie zjazdu cel dnia jest ujemny i tak samo wiazacy.
    realized = _today_realized(session, acc, now)
    if target > 0 and plan_pnl > 0:
        plan_pnl = min(plan_pnl, max(target - realized, target * 0.15))
    elif target < 0 and plan_pnl < 0:
        plan_pnl = max(plan_pnl, min(target - realized, target * 0.15))

    # Twardy sufit konta — PO przycieciu do celu dnia, bo to on jest wiazacy.
    # Cel dnia pilnuje tylko rytmu krzywej; sufit decyduje o tym, czy konto
    # zda faze, wiec ostatnie wejscie musi zatrzymac sie DOKLADNIE na nim.
    cap = cap_equity(acc)
    if cap is not None and plan_pnl > 0:
        plan_pnl = min(plan_pnl, max(0.0, cap - balance))

    # Ostatnie wejscie pod sufitem bywa male, a `_close_trade` zaokragla cene
    # zamkniecia do ticka instrumentu. Przy duzym wolumenie jeden tick jest wart
    # wiecej niz cala planowana kwota: cena nie drgnie, `pnl` wychodzi 0.00,
    # saldo stoi i bot probuje w kolko. Schodzimy wiec z WOLUMENU, zeby ruch
    # mial co najmniej kilka tickow — mala pozycja na domkniecie planu jest
    # zreszta dokladnie tym, co zrobilby trader.
    maks_lot = abs(plan_pnl) / (3 * inst.tick * pv)
    if maks_lot < lots:
        lots = max(MIN_LOT, math.floor(maks_lot * 100) / 100)

    hold = min(HOLD_MAX_SEK, max(HOLD_MIN_SEK, _cykl_sek(p) * rng.uniform(*HOLD_UDZIAL)))
    close_at = now + timedelta(seconds=hold)
    if not weekend and symbol not in CRYPTO_SYMBOLS:
        # Wejscie z piatkowego wieczoru nie moze miec daty zamkniecia „w sobote" —
        # poza krypto rynku wtedy nie ma. Pozycja idzie flat na zamknieciu tygodnia.
        koniec = _week_close(now)
        if _naive(close_at) > _naive(koniec):
            close_at = koniec

    tr = Trade(
        account_id=acc.id, symbol=symbol, side=side, lots=lots,
        open_price=open_price, pnl=0.0, opened_at=now, status="open", source="bot",
        plan_pnl=round(plan_pnl, 2),
        plan_close_at=close_at,
    )
    session.add(tr)
    session.flush()   # nadaje tr.id — potrzebne fazie szumu floatingu
    return tr
