"""Payout BOT — silnik dobowych wypłat z certyfikatem i wpisem na kanale.

Admin włącza go raz w panelu (Settings -> Payout BOT), a od tego momentu raz na
dobę powstaje wypłata z aktualną datą: konto archiwalne, publiczny certyfikat
i post na kanale Telegrama z grafiką tego certyfikatu.

Cztery zasady, na których to stoi:

1. **Jedna ścieżka zapisu.** Trader, konto i wypłata powstają przez
   `payout_import.zapisz` — dokładnie tę samą funkcję, której używa import CSV
   z ewidencji. Gdyby silnik tworzył konto po swojemu, po pierwszej zmianie
   w imporcie obie ścieżki zaczęłyby się rozjeżdżać.

2. **Dzień determinuje wynik.** Losowość idzie z `random.Random("...:{dzien}")`,
   więc dwa przebiegi tego samego dnia dają ten sam wynik. Razem z guardem
   `payoutbot_last_day` znaczy to, że nawet zdublowany cron nie zrobi drugiej,
   innej wypłaty.

3. **Certyfikat zawsze, landing czasem.** `cert_token` dostaje KAŻDA wypłata —
   bez niego nie ma czego zrzucić na Telegrama. Na pas certyfikatów na landingu
   trafia tylko część, sterowana `payoutbot_lp_pct`: przy jednej wypłacie
   dziennie pas zapchałby się w dwa tygodnie identycznymi wpisami.

4. **Publikacja nie może cofnąć zapisu.** Render i wysyłka są best-effort. Gdy
   usługa zrzutu padnie, na kanał idzie sam podpis z linkiem; gdy padnie i to,
   wypłata i tak zostaje w bazie, a błąd ląduje w logu.

Ustawienia siedzą w `app_settings` (klucz/wartość), nie w env — zmiana z panelu
ma działać od kliknięcia, a nie od deployu. Ten sam wzorzec co
`provisioning.SIM_FALLBACK_KEY`.
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone

from . import certshot, payout_import, telegram
from .config import get_settings
from .models import AppSetting, Payout, Product, Trader

settings = get_settings()

# --------------------------------------------------------------------------- #
#  Ustawienia                                                                  #
# --------------------------------------------------------------------------- #
PREFIKS = "payoutbot_"
KLUCZ_DZIEN = PREFIKS + "last_day"

DOMYSLNE: dict[str, str] = {
    "enabled": "0",
    "hour": "15",                                    # godzina serwera
    "lp_pct": "30",                                  # szansa na pas na landingu
    "sizes": "50000,100000,200000,300000,400000",
    "gross_min_pct": "9",                            # zysk brutto jako % konta
    "gross_max_pct": "11.5",
}

# Zakresy dopuszczalne przy zapisie z panelu. Sufit zysku jest niski celowo:
# wypłata rzędu 40% rozmiaru konta w jeden cykl nie wygląda jak wynik tradera,
# tylko jak pomyłka w formularzu.
LIMITY: dict[str, tuple[float, float]] = {
    "hour": (0, 23),
    "lp_pct": (0, 100),
    "gross_min_pct": (0.5, 40),
    "gross_max_pct": (0.5, 40),
}

# Imiona i nazwiska amerykańskie oraz brytyjskie. Iloczyn daje ponad 2 tysiące
# kombinacji, więc powtórka przy jednej wypłacie dziennie jest mało prawdopodobna,
# a i tak sprawdzamy zajętość adresu przed zapisem.
IMIONA = (
    "James", "Michael", "Robert", "Daniel", "Ethan", "Ryan", "Tyler", "Jacob",
    "Nathan", "Connor", "Austin", "Brandon", "Marcus", "Trevor", "Garrett",
    "Oliver", "Harry", "Callum", "Rory", "Alfie", "Freddie", "Archie", "Louis",
    "Sophie", "Emily", "Chloe", "Hannah", "Megan", "Imogen", "Harriet", "Eleanor",
    "Amelia", "Charlotte", "Grace", "Lauren", "Natalie", "Danielle", "Chelsea",
)
NAZWISKA = (
    "Whitfield", "Hastings", "Blackwood", "Ellison", "Pemberton", "Kirkland",
    "Fitzgerald", "MacAllister", "Monroe", "Whitaker", "Ashford", "Bramley",
    "Caldwell", "Donovan", "Everett", "Fairbanks", "Grayson", "Hollister",
    "Ingram", "Jennings", "Kendrick", "Lockwood", "Marsden", "Northcott",
    "Osgood", "Prescott", "Quinley", "Radcliffe", "Sinclair", "Thornbury",
    "Underwood", "Vancroft", "Westbrook", "Yardley", "Ainsworth", "Beaumont",
)

# Ile razy próbować wylosować wolne nazwisko, zanim się poddamy. Przy 2 tysiącach
# kombinacji i garstce zajętych ten limit nigdy nie powinien się wyczerpać.
PROBY_NAZWISKA = 40

NOTATKA = "payout bot"


def _wiersz(session, klucz: str) -> AppSetting | None:
    return session.get(AppSetting, PREFIKS + klucz)


def ustawienia(session) -> dict:
    """Bieżąca konfiguracja silnika, z domyślnymi dla nieustawionych kluczy."""
    out: dict = {}
    for klucz, domyslne in DOMYSLNE.items():
        row = _wiersz(session, klucz)
        out[klucz] = (row.value if row and row.value != "" else domyslne)
    return {
        "enabled": out["enabled"] == "1",
        "hour": int(float(out["hour"])),
        "lp_pct": float(out["lp_pct"]),
        "sizes": [float(x) for x in out["sizes"].split(",") if x.strip()],
        "gross_min_pct": float(out["gross_min_pct"]),
        "gross_max_pct": float(out["gross_max_pct"]),
        "last_day": (lambda r: r.value if r else None)(session.get(AppSetting, KLUCZ_DZIEN)),
    }


def _ustaw(session, klucz: str, wartosc: str) -> None:
    row = _wiersz(session, klucz)
    if row is None:
        row = AppSetting(key=PREFIKS + klucz)
        session.add(row)
    row.value = wartosc


def zapisz_ustawienia(session, **pola) -> dict:
    """Zapis z panelu. Rzuca `ValueError` z konkretnym komunikatem po angielsku."""
    if "enabled" in pola and pola["enabled"] is not None:
        _ustaw(session, "enabled", "1" if pola["enabled"] else "0")

    for klucz in ("hour", "lp_pct", "gross_min_pct", "gross_max_pct"):
        wartosc = pola.get(klucz)
        if wartosc is None:
            continue
        dol, gora = LIMITY[klucz]
        if not (dol <= float(wartosc) <= gora):
            raise ValueError(f"'{klucz}' must be between {dol:g} and {gora:g}")
        _ustaw(session, klucz, str(float(wartosc)))

    if pola.get("sizes") is not None:
        rozmiary = [float(x) for x in pola["sizes"]]
        if not rozmiary:
            raise ValueError("Pick at least one account size")
        znane = {p.account_size for p in session.query(Product).all()}
        obce = [r for r in rozmiary if r not in znane]
        if obce:
            raise ValueError("No plan for account size: "
                             + ", ".join(f"${r:,.0f}" for r in obce))
        _ustaw(session, "sizes", ",".join(f"{r:g}" for r in rozmiary))

    session.flush()
    cfg = ustawienia(session)
    if cfg["gross_min_pct"] > cfg["gross_max_pct"]:
        raise ValueError("Minimum profit cannot be higher than the maximum")
    session.commit()
    return cfg


# --------------------------------------------------------------------------- #
#  Czas serwera (ta sama strefa co tradebot/poller, bez importu -> brak cyklu)  #
# --------------------------------------------------------------------------- #
def _czas_serwera(now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + timedelta(hours=settings.server_utc_offset_hours)


def _dzien(now: datetime | None = None) -> str:
    return _czas_serwera(now).strftime("%Y-%m-%d")


def nalezy_odpalic(session, now: datetime | None = None) -> tuple[bool, str]:
    """`(czy odpalać, powód odmowy)`. Powód wraca do panelu i do logu crona."""
    cfg = ustawienia(session)
    if not cfg["enabled"]:
        return False, "engine off"
    srv = _czas_serwera(now)
    if cfg["last_day"] == srv.strftime("%Y-%m-%d"):
        return False, "already ran today"
    if srv.hour < cfg["hour"]:
        return False, f"waiting for {cfg['hour']:02d}:00 server time"
    return True, ""


# --------------------------------------------------------------------------- #
#  Generowanie wypłaty                                                         #
# --------------------------------------------------------------------------- #
def kwota_txt(wartosc: float) -> str:
    """Kwota tak, jak drukuje ją certyfikat: bez groszy, gdy okrągła.

    Używane w DWÓCH miejscach — na dokumencie i w podpisie posta — więc siedzi
    w jednym. Rozjazd między grafiką a podpisem byłby od razu widoczny na kanale.
    """
    return (f"${wartosc:,.0f}" if float(wartosc).is_integer()
            else f"${wartosc:,.2f}")


def podpis(imie: str, wartosc: float) -> str:
    """Podpis pod grafiką, 1:1 z formatem używanym na kanale."""
    return (f"<b><u>PAYOUT ALERT: {imie} Has Just Received "
            f"{kwota_txt(wartosc)}</u></b> 🚨")


def _wolne_nazwisko(session, rng) -> tuple[str, str]:
    """Imię i nazwisko, którego nie ma jeszcze w bazie."""
    for _ in range(PROBY_NAZWISKA):
        imie, nazwisko = rng.choice(IMIONA), rng.choice(NAZWISKA)
        nazwa = f"{imie} {nazwisko}"
        email = payout_import._email_techniczny(nazwa)
        if session.query(Trader).filter(Trader.email == email).first() is None:
            return nazwa, email
    # Pula wyczerpana — dokładamy liczbę, żeby silnik nie stanął w miejscu.
    nazwa = f"{rng.choice(IMIONA)} {rng.choice(NAZWISKA)}"
    return nazwa, payout_import._email_techniczny(nazwa).replace(
        "@", f"{rng.randint(2, 99)}@")


def _plan(session, rng, rozmiar: float) -> Product | None:
    """Plan dla wylosowanego rozmiaru. 2-Step częściej niż Instant — to on jest
    głównym produktem, więc i wypłaty powinny wyglądać na jego przewagę."""
    dostepne = (session.query(Product)
                .filter(Product.account_size == rozmiar).all())
    if not dostepne:
        return None
    dwustopniowe = [p for p in dostepne if p.steps == 2]
    instant = [p for p in dostepne if p.steps == 0]
    if dwustopniowe and (not instant or rng.random() < 0.7):
        return dwustopniowe[0]
    return (instant or dwustopniowe)[0]


def wygeneruj(session, now: datetime | None = None) -> Payout:
    """Tworzy komplet trader + konto + wypłata z certyfikatem. Bez commita."""
    cfg = ustawienia(session)
    kiedy = now or datetime.now(timezone.utc)
    # Seed z dnia ORAZ liczby dotychczasowych wypłat silnika. Sam dzień nie
    # wystarcza: „Run once now" pomija guard, więc kilka przebiegów tej samej
    # doby losowało IDENTYCZNIE — ten sam rozmiar konta i tę samą kwotę, zmieniało
    # się tylko nazwisko (zajęte są pomijane). Licznik rośnie dopiero po UDANYM
    # zapisie, więc powtórka po nieudanym dalej odtwarza tę samą wypłatę.
    dotad = session.query(Payout).filter(Payout.note == NOTATKA).count()
    rng = random.Random(f"payoutbot:{_dzien(kiedy)}:{dotad}")

    rozmiar = rng.choice(cfg["sizes"])
    prod = _plan(session, rng, rozmiar)
    if prod is None:
        raise LookupError(f"No plan for account size ${rozmiar:,.0f}")

    split = prod.profit_split_pct or 90.0
    brutto = prod.account_size * rng.uniform(cfg["gross_min_pct"],
                                             cfg["gross_max_pct"]) / 100.0
    # PEŁNE DOLARY, bez groszy. Przy wypłatach rzędu kilku tysięcy końcówka „,24"
    # nic nie wnosi, a na certyfikacie zabiera miejsce największej liczbie na
    # dokumencie i rozbija ją wizualnie. `kwota_txt` i tak drukuje grosze, gdy
    # kwota nie jest okrągła — zostaje dla wierszy wgranych ręcznie z ewidencji.
    udzial = float(max(1, round(brutto * split / 100.0)))
    # Zysk brutto liczony WSTECZ z udziału — dokładnie jak w imporcie CSV, żeby
    # kwoty w panelu zgadzały się ze splitem planu.
    brutto = round(udzial * 100.0 / split, 2)

    nazwa, email = _wolne_nazwisko(session, rng)
    wiersz = {
        "full_name": nazwa, "email": email,
        "amount_usd": udzial, "date": kiedy.replace(tzinfo=None),
        "product_key": prod.key, "account_size": prod.account_size,
        "steps": prod.steps, "split_pct": split, "profit_amount": brutto,
        "note": NOTATKA,
        # Wypłata bez weryfikacji tożsamości nie ma sensu, a data przeglądu
        # ustawia się z daty wypłaty (patrz payout_import.zapisz).
        "kyc": "approved",
    }
    wyplata, _ = payout_import.zapisz(session, wiersz,
                                      payout_import._nastepny_login(session))
    # Certyfikat dostaje KAŻDA wypłata — bez tokenu nie ma czego zrzucić na kanał.
    wyplata.cert_token = secrets.token_urlsafe(16)[:32]
    wyplata.show_on_lp = rng.random() < (cfg["lp_pct"] / 100.0)
    session.flush()
    return wyplata


# --------------------------------------------------------------------------- #
#  Przebieg                                                                    #
# --------------------------------------------------------------------------- #
def opublikuj(wyplata: Payout, nazwa: str, *, base_url: str | None = None,
              transport_shot=None, transport_tg=None) -> dict:
    """Zrzut certyfikatu + wpis na kanale. Best-effort, nigdy nie rzuca."""
    baza = (base_url or settings.app_base_url or "").rstrip("/")
    link = f"{baza}/payout/{wyplata.cert_token}"
    tekst = podpis((nazwa or "").split(" ")[0] or "A trader", wyplata.trader_share)

    if not telegram.is_enabled():
        return {"posted": False, "photo": False, "reason": "telegram off"}

    png = certshot.render(f"{link}?bare=1", transport=transport_shot)
    if png:
        ok, powod = telegram.send_photo(png, tekst, transport=transport_tg)
        if ok:
            return {"posted": True, "photo": True}
        # Powód prosto od Telegrama („bot is not a member of the channel chat",
        # „not enough rights to send photos"). Bez niego admin widzi samo
        # „odrzucone" i musi szukać przyczyny w logach hostingu.
        return {"posted": False, "photo": True, "reason": powod}

    # Bez grafiki idzie sam podpis z linkiem — cisza na kanale byłaby myląca,
    # skoro wypłata i jej publiczny certyfikat już istnieją.
    ok, powod = telegram.send_message(f"{tekst}\n{link}", transport=transport_tg)
    return {"posted": ok, "photo": False, "reason": "" if ok else powod}


def uruchom(session, now: datetime | None = None, *, force: bool = False,
            base_url: str | None = None, transport_shot=None,
            transport_tg=None) -> dict:
    """Jeden przebieg silnika. Zwraca liczniki, jak pozostałe zadania crona."""
    czy, powod = nalezy_odpalic(session, now)
    if not czy and not force:
        return {"created": 0, "skipped": powod}

    kiedy = now or datetime.now(timezone.utc)
    try:
        wyplata = wygeneruj(session, kiedy)
    except Exception as e:
        print(f"[payoutbot] nie udało się wygenerować wypłaty: {e}")
        return {"created": 0, "error": str(e)}

    nazwa = wyplata.account.trader_name if wyplata.account else ""
    # Guard i wypłata w JEDNEJ transakcji: gdyby zapis dnia poszedł osobno i padł,
    # następny tick zrobiłby drugą wypłatę tego samego dnia.
    _ustaw(session, "last_day", _dzien(kiedy))
    session.commit()

    wynik = opublikuj(wyplata, nazwa, base_url=base_url,
                      transport_shot=transport_shot, transport_tg=transport_tg)
    return {"created": 1, "payout_id": wyplata.id, "trader": nazwa,
            "amount_usd": wyplata.trader_share, "on_lp": bool(wyplata.show_on_lp),
            "cert_token": wyplata.cert_token, **wynik}
