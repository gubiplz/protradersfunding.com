"""Program lojalnościowy: punkty, progi statusu i wymiana punktów na kod rabatowy.

Do tej pory punkty były licznikiem bez wyjścia: rosły przy check-inach i
losowaniach, nigdzie nie schodziły, a „nagrodą" w portalu było pokazanie kodu
GLOBALNEGO (WELCOME10/VIP20/BLACKFRIDAY), który i tak działa każdemu, kto go
zna — więc próg nic nie bramkował. Tutaj punkty stają się walutą: trader
wymienia je na własny kod jednorazowy, a saldo maleje.

DWIE LICZBY, NIE JEDNA
----------------------
`lifetime` (wydane na challenge'e + bonusy z zaangażowania) wyznacza STATUS i
nigdy nie maleje — nikt nie traci Golda za to, że skorzystał z nagrody.
`available` = lifetime − points_spent to portfel, z którego płaci się za kody.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .models import Order, RewardCode, Trader

# Progi statusu liczone z punktów DOŻYWOTNICH. Status sam w sobie nie daje już
# kodu — daje go wymiana punktów niżej — ale zostaje jako widoczny postęp.
TIERS = [
    ("Bronze", 0),
    ("Silver", 500),
    ("Gold", 1500),
]

# Sklepik nagród: koszt w punktach -> procent zniżki. Punkt bierze się z
# jednego dolara wydanego na challenge, więc 500 punktów to realne $500 obrotu.
REWARDS = [
    {"key": "off15", "pct": 15.0, "cost": 500},
    {"key": "off25", "pct": 25.0, "cost": 1000},
    {"key": "off35", "pct": 35.0, "cost": 2000},
]

# Ile kod żyje od wymiany. Bez terminu firma nosi zobowiązanie bez końca, a
# trader i tak zwykle używa kodu przy następnym zakupie.
CODE_TTL_DAYS = 90

# Alfabet bez znaków, które mylą się przy przepisywaniu (0/O, 1/I/L).
_ALFABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# Po tym prefiksie checkout poznaje, że ma szukać kodu w bazie, a nie w
# słowniku kodów globalnych.
CODE_PREFIX = "PF-"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_code(session) -> str:
    """Kod w formacie PF-XXXX-XXXX, unikalny w bazie."""
    for _ in range(12):
        srodek = "".join(secrets.choice(_ALFABET) for _ in range(4))
        koniec = "".join(secrets.choice(_ALFABET) for _ in range(4))
        kod = f"{CODE_PREFIX}{srodek}-{koniec}"
        if not session.query(RewardCode).filter(RewardCode.code == kod).first():
            return kod
    raise RuntimeError("nie udalo sie wylosowac wolnego kodu")


def lifetime_points(session, trader: Trader) -> int:
    """Punkty DOŻYWOTNIE: 1 punkt za każdy $1 opłaconego challenge'a + bonusy.

    Liczone po stronie serwera, bo od tego zależy, czy trader stać na nagrodę.
    Wcześniej sumę robiła przeglądarka (portal.html), co przy wymianie punktów
    byłoby zaproszeniem do liczenia sobie salda samemu.

    Granty (`provider="grant"`) nie liczą się do punktów: klient za nie nie
    zapłacił, a `amount_usd` niosą tylko dla faktury przy BOGO.
    """
    wydane = (session.query(Order)
              .filter(Order.trader_id == trader.id, Order.status == "paid",
                      Order.provider != "grant").all())
    return int(round(sum(o.amount_usd or 0 for o in wydane))) + int(trader.bonus_points or 0)


def balance(session, trader: Trader) -> dict:
    """Pełny stan programu dla jednego tradera."""
    lifetime = lifetime_points(session, trader)
    wydane = int(trader.points_spent or 0)
    dostepne = max(0, lifetime - wydane)
    tier = TIERS[0][0]
    for nazwa, prog in TIERS:
        if lifetime >= prog:
            tier = nazwa
    nastepny = next(((n, p) for n, p in TIERS if p > lifetime), None)
    return {
        "points_lifetime": lifetime,
        "points_spent": wydane,
        "points_available": dostepne,
        "tier": tier,
        "next_tier": nastepny[0] if nastepny else None,
        "next_tier_at": nastepny[1] if nastepny else None,
    }


def reward_by_key(key: str) -> dict | None:
    return next((r for r in REWARDS if r["key"] == key), None)


def redeem(session, trader: Trader, key: str) -> RewardCode:
    """Zamienia punkty na własny kod jednorazowy. Woła się to POD blokadą wiersza.

    Punkty schodzą TERAZ, przy wymianie — tak jak prosił o to właściciel
    produktu. Kod schodzi osobno, dopiero przy domknięciu płatności.
    """
    nagroda = reward_by_key(key)
    if nagroda is None:
        raise ValueError("unknown reward")
    stan = balance(session, trader)
    if stan["points_available"] < nagroda["cost"]:
        raise LookupError(str(nagroda["cost"] - stan["points_available"]))

    kod = RewardCode(
        trader_id=trader.id, code=generate_code(session), pct=nagroda["pct"],
        points_spent=nagroda["cost"],
        expires_at=_utcnow() + timedelta(days=CODE_TTL_DAYS),
    )
    session.add(kod)
    trader.points_spent = int(trader.points_spent or 0) + nagroda["cost"]
    return kod


def _minelo(kiedy: datetime | None) -> bool:
    """Czy podana chwila już minęła. Baza oddaje daty naiwne (bez strefy),
    a my liczymy w UTC — bez tego porównanie wywala się na TypeError."""
    if kiedy is None:
        return False
    return _utcnow() > (kiedy if kiedy.tzinfo else kiedy.replace(tzinfo=timezone.utc))


def code_dict(k: RewardCode) -> dict:
    wygasl = _minelo(k.expires_at)
    return {
        "code": k.code, "pct": k.pct, "points_spent": k.points_spent,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "used_at": k.used_at.isoformat() if k.used_at else None,
        "status": "used" if k.used_at else ("expired" if wygasl else "active"),
    }


def find_usable(session, trader: Trader, code: str) -> RewardCode | None:
    """Kod nagrody TEGO tradera, nadający się jeszcze do użycia.

    None gdy: kodu nie ma, należy do kogoś innego, został już użyty albo wygasł.
    Rozróżnienie „czyj" zostaje w `billing`, żeby komunikat błędu mógł być
    konkretny.
    """
    k = session.query(RewardCode).filter(RewardCode.code == (code or "").strip().upper()).first()
    if k is None or k.trader_id != trader.id or k.used_at is not None or _minelo(k.expires_at):
        return None
    return k
