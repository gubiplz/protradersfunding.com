"""Darmowa „Trade Case": jedna skrzynka na tradera, trzy karty, nagroda z wagami.

Trader wybiera jedną z trzech zakrytych kart i dostaje nagrodę z puli, którą
ustawia admin (Offers → Trade Case). Karta niczego nie zmienia: wynik losuje
SERWER w momencie otwarcia, a dwie pozostałe karty dostają po odsłonie inne
nagrody z puli tylko dla wyglądu. Każda skrzynka coś daje — pula nie ma
pustego pola.

NAGRODY
-------
`code`    — własny kod jednorazowy PF-XXXX-XXXX (RewardCode, points_spent=0),
            ważny `KOD_DNI` dni; checkout przyjmuje go przez loyalty.find_usable.
`account` — darmowy challenge przez billing.grant_challenge (ta sama ścieżka co
            grant z panelu: zamówienie $0, provisioning, mail z poświadczeniami).
`points`  — punkty lojalnościowe doliczane do `Trader.bonus_points`.

Konfiguracja siedzi w `app_settings` pod kluczem `trade_case` (JSON) i jest
czytana przez 60-sekundowy cache — ten sam wzorzec co popup newslettera.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from .models import Account, AppSetting, Product, RewardCase, RewardCode, Trader

KLUCZ = "trade_case"
TYPY = ("code", "account", "points")
KARTY = 3
KOD_DNI = 30
MAX_NAGROD = 12
MAX_ETYKIETA = 60
NOTA_GRANTU = "Free Trade Case"

# Domyślna pula: wagi sumują się do 100, więc waga = procent szansy.
DOMYSLNE_NAGRODY = [
    {"type": "code", "pct": 10, "label": "10% off any challenge", "weight": 55},
    {"type": "code", "pct": 15, "label": "15% off any challenge", "weight": 25},
    {"type": "code", "pct": 20, "label": "20% off any challenge", "weight": 11},
    {"type": "code", "pct": 25, "label": "25% off any challenge", "weight": 5.5},
    {"type": "account", "product_key": "2step-25k", "label": "Free 2-Step $25K challenge", "weight": 2.5},
    {"type": "account", "product_key": "2step-50k", "label": "Free 2-Step $50K challenge", "weight": 0.8},
    {"type": "account", "product_key": "2step-100k", "label": "Free 2-Step $100K challenge", "weight": 0.2},
]

# Losowanie nagrody i kart „obok": generator systemowy, nie `random` — wynik
# ma wartość pieniężną, więc nie może dać się przewidzieć z poprzednich losów.
_RNG = secrets.SystemRandom()
_CACHE: dict = {"ts": 0.0, "val": None}
_ZNAKI_KLUCZA = re.compile(r"[^a-z0-9]+")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _liczba(v, domyslna=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return domyslna
    return x if x == x and x not in (float("inf"), float("-inf")) else domyslna


def _klucz(p: dict) -> str:
    """Klucz nagrody wynika z jej treści (typ + wartość), nie z formularza —
    statystyki per klucz zawsze mówią, CO wypadło, nawet po edycji puli."""
    if p["type"] == "code":
        k = f"off{p['pct']:g}"
    elif p["type"] == "account":
        k = "acc-" + p["product_key"]
    else:
        k = f"pts{int(p['points'])}"
    return _ZNAKI_KLUCZA.sub("-", k.lower()).strip("-")[:32]


def _nagroda(raw: dict) -> dict:
    """Jedna nagroda z surowego JSON-a. Rzuca ValueError z komunikatem dla admina."""
    if not isinstance(raw, dict):
        raise ValueError("Each prize must be an object")
    typ = str(raw.get("type") or "").strip()
    if typ not in TYPY:
        raise ValueError("Prize type must be code, account or points")
    etykieta = " ".join(str(raw.get("label") or "").split())
    if not etykieta:
        raise ValueError("Every prize needs a label")
    if len(etykieta) > MAX_ETYKIETA:
        raise ValueError(f"Prize labels can be at most {MAX_ETYKIETA} characters")
    waga = _liczba(raw.get("weight"))
    if waga is None or waga <= 0:
        raise ValueError(f"“{etykieta}”: the weight must be greater than 0")
    if waga > 1_000_000:
        raise ValueError(f"“{etykieta}”: the weight is too large")
    p = {"type": typ, "label": etykieta, "weight": round(waga, 4)}
    if typ == "code":
        pct = _liczba(raw.get("pct"))
        if pct is None or not 1 <= pct <= 90:
            raise ValueError(f"“{etykieta}”: the discount must be between 1 and 90%")
        p["pct"] = round(pct, 2)
    elif typ == "account":
        p["product_key"] = str(raw.get("product_key") or "").strip()
        if not p["product_key"]:
            raise ValueError(f"“{etykieta}”: pick the challenge to give away")
    else:
        pkt = _liczba(raw.get("points"))
        if pkt is None or pkt != int(pkt) or not 1 <= pkt <= 100_000:
            raise ValueError(f"“{etykieta}”: points must be a whole number from 1 to 100,000")
        p["points"] = int(pkt)
    return p


def _z_kluczami(nagrody: list[dict]) -> list[dict]:
    """Nadaje klucze; dwie nagrody o tej samej treści dostają przyrostek -2, -3…"""
    zajete: set[str] = set()
    out = []
    for p in nagrody:
        baza = _klucz(p)
        k, n = baza, 2
        while k in zajete:
            k, n = f"{baza[:29]}-{n}", n + 1
        zajete.add(k)
        out.append({"key": k, **p})
    return out


def czysc(raw: dict | None) -> dict:
    """Konfiguracja z bazy, wyczyszczona łagodnie: złe wpisy wypadają, a pusta
    pula wraca do domyślnej — zepsuty JSON nie może zgasić skrzynki po cichu."""
    raw = raw if isinstance(raw, dict) else {}
    nagrody = []
    for r in (raw.get("prizes") if isinstance(raw.get("prizes"), list) else [])[:MAX_NAGROD]:
        try:
            nagrody.append(_nagroda(r))
        except ValueError:
            continue
    if not nagrody:
        nagrody = [_nagroda(r) for r in DOMYSLNE_NAGRODY]
    return {"on": bool(raw.get("on", True)), "prizes": _z_kluczami(nagrody)}


def waliduj(session, raw: dict) -> dict:
    """Zapis z panelu: ostro, z komunikatem. Plan nagrody musi istnieć i być
    w sprzedaży — grant nieaktywnego planu wywróciłby otwarcie skrzynki."""
    nagrody_raw = raw.get("prizes")
    if not isinstance(nagrody_raw, list) or not nagrody_raw:
        raise ValueError("Add at least one prize")
    if len(nagrody_raw) > MAX_NAGROD:
        raise ValueError(f"The case can hold at most {MAX_NAGROD} prizes")
    nagrody = [_nagroda(r) for r in nagrody_raw]
    aktywne = {k for (k,) in session.query(Product.key).filter(Product.active == True).all()}  # noqa: E712
    for p in nagrody:
        if p["type"] == "account" and p["product_key"] not in aktywne:
            raise ValueError(f"“{p['label']}”: the challenge {p['product_key']} is not on sale")
    return {"on": bool(raw.get("on", True)), "prizes": _z_kluczami(nagrody)}


def konfig(odswiez: bool = False) -> dict:
    teraz = time.time()
    if odswiez or _CACHE["val"] is None or teraz - _CACHE["ts"] > 60:
        from .db import SessionLocal

        s = SessionLocal()
        try:
            row = s.get(AppSetting, KLUCZ)
            try:
                raw = json.loads(row.value) if row and row.value else {}
            except ValueError:
                raw = {}
        finally:
            s.close()
        _CACHE.update(ts=teraz, val=czysc(raw))
    return json.loads(json.dumps(_CACHE["val"]))      # głęboka kopia: wołający nie psuje cache'u


def zapisz(session, cfg: dict) -> None:
    row = session.get(AppSetting, KLUCZ) or AppSetting(key=KLUCZ)
    row.value = json.dumps(cfg)
    session.add(row)
    session.commit()
    _CACHE.update(ts=0.0, val=None)


def pula(session, cfg: dict) -> list[dict]:
    """Nagrody, które da się DZIŚ wydać. Konto z planem wycofanym ze sprzedaży
    po zapisie puli wypada — razem z jego szansą, więc pokazane „Odds" zgadzają
    się z tym, co faktycznie może wypaść."""
    klucze = {p["product_key"] for p in cfg["prizes"] if p["type"] == "account"}
    aktywne = set()
    if klucze:
        aktywne = {k for (k,) in session.query(Product.key)
                   .filter(Product.key.in_(klucze), Product.active == True).all()}  # noqa: E712
    return [p for p in cfg["prizes"] if p["type"] != "account" or p["product_key"] in aktywne]


def szanse(nagrody: list[dict]) -> list[dict]:
    suma = sum(p["weight"] for p in nagrody) or 1.0
    return [{"key": p["key"], "label": p["label"], "type": p["type"],
             "pct": round(p["weight"] / suma * 100, 1)} for p in nagrody]


def losuj(nagrody: list[dict]) -> dict:
    """Nagroda proporcjonalnie do wag (testy podmieniają tę funkcję)."""
    return _wazony(nagrody)


def _wazony(nagrody: list[dict]) -> dict:
    suma = sum(p["weight"] for p in nagrody)
    x = _RNG.random() * suma
    for p in nagrody:
        x -= p["weight"]
        if x < 0:
            return p
    return nagrody[-1]


def _inne_karty(nagrody: list[dict], wygrana: dict) -> list[str]:
    """Etykiety dwóch pozostałych kart — czysta kosmetyka odsłony.

    Losowane Z WAGAMI (bez powtórzeń), nie po równo: po równo karty „obok"
    pokazywałyby darmowe konta w co drugiej odsłonie, czyli obraz puli
    odwrotny do prawdziwych szans — sztuczne „prawie wygrałeś"."""
    inne = [p for p in nagrody if p["key"] != wygrana["key"]] or nagrody
    out = []
    for _ in range(KARTY - 1):
        p = _wazony(inne)
        out.append(p["label"])
        if len(inne) > 1:
            inne = [x for x in inne if x is not p]
    return out


def powod_blokady(trader: Trader, cfg: dict, nagrody: list[dict]) -> str | None:
    """Dlaczego ten trader nie może otworzyć skrzynki (None = może)."""
    if trader.is_admin:
        return "admin"
    if not cfg["on"]:
        return "disabled"
    if not nagrody:
        return "empty"
    if trader.email_verified is False:
        return "verify_email"
    return None


def wynik(session, case: RewardCase) -> dict:
    """Otwarta skrzynka w kształcie dla portalu."""
    try:
        extra = json.loads(case.payload or "{}")
    except ValueError:
        extra = {}
    out = {"key": case.prize_key, "type": case.prize_type, "label": case.prize_label,
           "card": case.card_index, "others": extra.get("others") or [],
           "opened_at": case.opened_at.isoformat() if case.opened_at else None}
    if case.prize_type == "code":
        out["pct"] = case.prize_value
        kod = session.get(RewardCode, case.reward_code_id) if case.reward_code_id else None
        if kod is not None:
            from . import loyalty

            k = loyalty.code_dict(kod)
            out.update(code=k["code"], expires_at=k["expires_at"], code_status=k["status"])
    elif case.prize_type == "points":
        out["points"] = int(case.prize_value or 0)
    else:
        out["product_key"] = extra.get("product_key")
        acc = session.get(Account, case.account_id) if case.account_id else None
        out["account_id"] = acc.id if acc else None
        if acc is not None:
            out.update(account_login=acc.login, account_status=acc.status)
    return out


def stan(session, trader: Trader) -> dict:
    """GET /api/me/case."""
    cfg = konfig()
    nagrody = pula(session, cfg)
    case = session.query(RewardCase).filter(RewardCase.trader_id == trader.id).first()
    powod = "opened" if case else powod_blokady(trader, cfg, nagrody)
    return {"enabled": cfg["on"], "available": powod is None, "reason": powod,
            "opened": wynik(session, case) if case else None,
            "cards": KARTY, "code_days": KOD_DNI,
            "odds": [{"label": s["label"], "type": s["type"], "pct": s["pct"]} for s in szanse(nagrody)]}


class Niedostepna(Exception):
    """Skrzynki nie da się teraz otworzyć; `args[0]` to powód z powod_blokady."""


def otworz(session, trader_id: int, karta: int) -> dict:
    """Otwiera skrzynkę. Zwraca wynik z `already` (True = była już otwarta).

    Kolejność jest warunkiem: wiersz RewardCase idzie do bazy PRZED nadaniem
    nagrody. Grant konta commituje sesję w środku (provisioning), więc gdyby
    wiersz powstawał na końcu, drugie równoległe otwarcie zdążyłoby przyznać
    drugie konto. UNIQUE na trader_id zatrzymuje je na flushu.
    """
    from sqlalchemy.exc import IntegrityError

    from . import billing, loyalty, notify, telemetry

    cfg = konfig()           # przed blokadą: cache potrafi otworzyć własną sesję
    tr = session.query(Trader).filter(Trader.id == trader_id).with_for_update().one()
    juz = session.query(RewardCase).filter(RewardCase.trader_id == tr.id).first()
    if juz:
        return {**wynik(session, juz), "already": True}
    nagrody = pula(session, cfg)
    powod = powod_blokady(tr, cfg, nagrody)
    if powod:
        raise Niedostepna(powod)
    p = losuj(nagrody)
    case = RewardCase(trader_id=tr.id, card_index=karta, prize_key=p["key"], prize_type=p["type"],
                      prize_label=p["label"][:80],
                      prize_value=(p.get("pct") if p["type"] == "code" else p.get("points")),
                      payload=json.dumps({"others": _inne_karty(nagrody, p),
                                          "product_key": p.get("product_key")}))
    session.add(case)
    try:
        session.flush()
    except IntegrityError:
        # Równoległe otwarcie zdążyło pierwsze — oddajemy JEGO wynik.
        session.rollback()
        juz = session.query(RewardCase).filter(RewardCase.trader_id == trader_id).one()
        return {**wynik(session, juz), "already": True}

    if p["type"] == "code":
        kod = RewardCode(trader_id=tr.id, code=loyalty.generate_code(session), pct=p["pct"],
                         points_spent=0, expires_at=_utcnow() + timedelta(days=KOD_DNI))
        session.add(kod)
        session.flush()
        case.reward_code_id = kod.id
    elif p["type"] == "points":
        tr.bonus_points = int(tr.bonus_points or 0) + int(p["points"])
    else:
        # grant_challenge commituje w środku (provisioning) — wiersz skrzynki
        # trafia do bazy razem z kontem, account_id dopisujemy commitem niżej.
        res = billing.grant_challenge(session, tr, p["product_key"], NOTA_GRANTU)
        case.account_id = res["account_id"]
    session.commit()
    telemetry.track("case_open", tr.id, prize=p["key"], kind=p["type"], card=karta)
    if p["type"] == "account":
        # Konto to realny koszt — admin ma wiedzieć od razu, nie z raportu.
        notify.notify_admins("admin_order", f"Trade Case: {p['label']}", tr.email)
    return {**wynik(session, case), "already": False}


def statystyki(session, cfg: dict) -> dict:
    """Panel: ile otwarto, ile razy wypadła każda nagroda, ostatnie otwarcia."""
    from sqlalchemy import func

    per = dict(session.query(RewardCase.prize_key, func.count(RewardCase.id))
               .group_by(RewardCase.prize_key).all())
    ostatnie = (session.query(RewardCase, Trader.email)
                .join(Trader, Trader.id == RewardCase.trader_id)
                .order_by(RewardCase.id.desc()).limit(20).all())
    etykiety = {p["key"]: p["label"] for p in cfg["prizes"]}
    for c, _ in ostatnie:
        etykiety.setdefault(c.prize_key, c.prize_label)
    return {"opened": sum(per.values()),
            "per_prize": [{"key": k, "label": etykiety.get(k, k), "count": n}
                          for k, n in sorted(per.items(), key=lambda kv: -kv[1])],
            "recent": [{"email": email, "prize": c.prize_label, "type": c.prize_type,
                        "account_id": c.account_id,
                        "opened_at": c.opened_at.isoformat() if c.opened_at else None}
                       for c, email in ostatnie]}
