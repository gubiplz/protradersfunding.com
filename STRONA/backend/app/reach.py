"""Reach BOT — dokupowanie zasięgu pod postami kanału z wypłatami.

Payout BOT publikuje dobowy certyfikat na kanale, a ten moduł zaraz po
publikacji zamawia u zewnętrznego dostawcy reakcje i wyświetlenia pod TYM
konkretnym postem. Panel dostaje drugą drogę: wklejenie linku do dowolnego
posta i „Boost" ręcznie.

Cztery zasady, na których to stoi:

1. **Nigdy nie wywraca wypłaty.** Tak jak `telegram.py`: brak konfiguracji,
   padnięta sieć czy odmowa dostawcy kończą się wpisem w logu i `skipped`,
   nie wyjątkiem. Zamówienie jest dodatkiem do posta, a post do wypłaty.

2. **Dostawca siedzi w env, nie w kodzie.** `REACH_API_URL` i `REACH_API_KEY`
   — repozytorium deployowe jest publiczne, więc adres i klucz nie mają prawa
   być w plikach. ID usług i ilości są w `app_settings`, bo admin zmienia je
   z panelu, a nie deployem (ten sam wzorzec co `payoutbot`).

3. **Bramka salda przed zamówieniem.** Przy koncie poniżej kosztu pary
   zamówień nie strzelamy do dostawcy, tylko mówimy o tym adminowi —
   inaczej jedyną informacją o pustym koncie byłaby seria błędów w logu.

4. **Transport wstrzykiwany.** Testy podstawiają własny i nie ruszają sieci
   (kontrakt taki sam jak w `telegram.py`).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import notify
from .config import get_settings
from .models import AppSetting

settings = get_settings()

TIMEOUT_SEK = 15
PREFIKS = "reach_"
KLUCZ_WYNIK = PREFIKS + "last_result"
KLUCZ_ALERT = PREFIKS + "last_alert_day"

DOMYSLNE = {
    "enabled": "0",
    "svc_reactions": "8612",
    "qty_reactions": "30",
    "svc_views": "8407",
    "qty_views": "400",
    "min_balance": "1",
    # Ostatnia deska ratunku dla bramki salda: normalnie koszt liczy się
    # z cennika dostawcy (patrz `odswiez_cennik`), ale gdy cennik nie odpowie,
    # lepiej mieć przybliżenie niż wpuścić zamówienie na puste konto.
    "unit_cost": "0.055",
    # Stawki za 1000 sztuk, przepisane z cennika dostawcy przy dobowym ticku.
    "rate_reactions": "",
    "rate_views": "",
}

LIMITY = {
    "qty_reactions": (0, 100000),
    "qty_views": (0, 99999),
    "min_balance": (0, 1000),
    "unit_cost": (0.001, 100),
}


# --------------------------------------------------------------------------- #
#  Ustawienia (app_settings)                                                   #
# --------------------------------------------------------------------------- #
def _wiersz(session, klucz: str) -> AppSetting | None:
    return session.get(AppSetting, PREFIKS + klucz)


def _ustaw(session, klucz: str, wartosc: str) -> None:
    row = _wiersz(session, klucz)
    if row is None:
        row = AppSetting(key=PREFIKS + klucz)
        session.add(row)
    row.value = wartosc


def ustawienia(session) -> dict:
    out: dict = {}
    for klucz, domyslne in DOMYSLNE.items():
        row = _wiersz(session, klucz)
        out[klucz] = (row.value if row and row.value != "" else domyslne)
    wynik = session.get(AppSetting, KLUCZ_WYNIK)
    cfg = {
        "enabled": out["enabled"] == "1",
        "svc_reactions": int(float(out["svc_reactions"])),
        "qty_reactions": int(float(out["qty_reactions"])),
        "svc_views": int(float(out["svc_views"])),
        "qty_views": int(float(out["qty_views"])),
        "min_balance": float(out["min_balance"]),
        "fallback_cost": float(out["unit_cost"]),
        "last_result": wynik.value if wynik else None,
    }
    # Koszt posta liczymy z zapamiętanych stawek dostawcy — admin nie ma go po
    # co wpisywać ręcznie, a przy zmianie cennika sam się poprawia.
    stawki = {}
    for klucz, pole in (("rate_reactions", "qty_reactions"), ("rate_views", "qty_views")):
        try:
            stawki[klucz] = float(out[klucz])
        except (TypeError, ValueError):
            stawki[klucz] = None
    if stawki["rate_reactions"] is not None and stawki["rate_views"] is not None:
        cfg["unit_cost"] = round(cfg["qty_reactions"] * stawki["rate_reactions"] / 1000
                                 + cfg["qty_views"] * stawki["rate_views"] / 1000, 6)
        cfg["cost_from"] = "provider"
    else:
        cfg["unit_cost"] = cfg["fallback_cost"]
        cfg["cost_from"] = "estimate"
    return cfg


def zapisz_ustawienia(session, **pola) -> dict:
    """Zapis z panelu. Rzuca `ValueError` z komunikatem po angielsku."""
    if pola.get("enabled") is not None:
        _ustaw(session, "enabled", "1" if pola["enabled"] else "0")

    for klucz, stawka in (("svc_reactions", "rate_reactions"), ("svc_views", "rate_views")):
        wartosc = pola.get(klucz)
        if wartosc is None:
            continue
        if int(wartosc) <= 0:
            raise ValueError(f"'{klucz}' must be a positive service id")
        # Zmiana usługi unieważnia zapamiętaną stawkę — inaczej „koszt posta"
        # liczyłby się z ceny czegoś, czego już nie zamawiamy.
        if str(int(wartosc)) != str(ustawienia(session)[klucz]):
            _ustaw(session, stawka, "")
        _ustaw(session, klucz, str(int(wartosc)))

    for klucz in ("qty_reactions", "qty_views", "min_balance", "unit_cost"):
        wartosc = pola.get(klucz)
        if wartosc is None:
            continue
        dol, gora = LIMITY[klucz]
        if not (dol <= float(wartosc) <= gora):
            raise ValueError(f"'{klucz}' must be between {dol:g} and {gora:g}")
        _ustaw(session, klucz, str(float(wartosc)))

    session.commit()
    return ustawienia(session)


# --------------------------------------------------------------------------- #
#  Dostawca                                                                    #
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    """Czy dostawca jest w ogóle skonfigurowany (env, nie ustawienia panelu)."""
    return bool(settings.reach_api_url and settings.reach_api_key)


def _urllib_transport(url: str, body: bytes, content_type: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _api(action: str, pola: dict | None = None, *, transport=None,
         jako_lista: bool = False) -> tuple[bool, dict | list]:
    """`(czy poszło, odpowiedź)`. Nigdy nie rzuca — błąd wraca jako `{"error": …}`.

    `jako_lista` dla `services`: cennik przychodzi tablicą, a nie obiektem."""
    if not is_enabled():
        return False, {"error": "reach provider not configured"}
    dane = {"key": settings.reach_api_key, "action": action, **{k: str(v) for k, v in (pola or {}).items()}}
    body = urllib.parse.urlencode(dane).encode()
    try:
        status, tresc = (transport or _urllib_transport)(
            settings.reach_api_url, body, "application/x-www-form-urlencoded")
    except Exception as e:  # pragma: no cover - sieć
        print(f"[reach] {action} błąd sieci: {e}")
        return False, {"error": f"network error: {e}"}
    try:
        odp = json.loads(tresc or b"{}")
    except Exception:
        odp = {}
    if jako_lista:
        if status != 200 or not isinstance(odp, list):
            opis = str((odp or {}).get("error") if isinstance(odp, dict) else f"HTTP {status}")
            print(f"[reach] {action} odrzucone: {opis}")
            return False, {"error": opis or f"HTTP {status}"}
        return True, odp
    if not isinstance(odp, dict):
        odp = {}
    # Klucz jest w ciele żądania, więc do logu i panelu idzie sam opis błędu.
    if status != 200 or odp.get("error"):
        opis = str(odp.get("error") or f"HTTP {status}")
        print(f"[reach] {action} odrzucone: {opis}")
        return False, {"error": opis}
    return True, odp


def saldo(*, transport=None, unit_cost: float | None = None,
          min_balance: float | None = None) -> dict:
    """Stan konta u dostawcy w formie gotowej do decyzji."""
    ok, odp = _api("balance", transport=transport)
    if not ok or odp.get("balance") is None:
        return {"error": odp.get("error") or "no balance in response"}
    try:
        wartosc = float(odp["balance"])
    except (TypeError, ValueError):
        return {"error": "balance is not a number"}
    koszt = float(unit_cost or DOMYSLNE["unit_cost"])
    prog = float(min_balance if min_balance is not None else DOMYSLNE["min_balance"])
    return {
        "value": wartosc,
        "currency": odp.get("currency") or "USD",
        "posts_left": int(wartosc // koszt) if koszt > 0 else 0,
        "low": wartosc < prog,
    }


def saldo_z_ustawien(session, *, transport=None) -> dict:
    cfg = ustawienia(session)
    return saldo(transport=transport, unit_cost=cfg["unit_cost"],
                 min_balance=cfg["min_balance"])


def odswiez_cennik(session, *, transport=None) -> dict:
    """Przepisuje stawki wybranych usług z cennika dostawcy do ustawień.

    Wołane raz na dobę z ticka, a nie przy każdym wejściu w panel: lista usług
    dostawcy ma kilkaset pozycji i nie ma po co ciągnąć jej pod przycisk.
    Po tym „koszt posta" liczy się sam i nadąża za zmianą cennika.
    """
    cfg = ustawienia(session)
    ok, odp = _api("services", transport=transport, jako_lista=True)
    if not ok:
        return {"error": odp.get("error") if isinstance(odp, dict) else "bad response"}
    stawki = {}
    for poz in odp if isinstance(odp, list) else []:
        try:
            stawki[int(poz["service"])] = float(poz["rate"])
        except (KeyError, TypeError, ValueError):
            continue
    zapisane = {}
    for klucz, usluga in (("rate_reactions", cfg["svc_reactions"]),
                          ("rate_views", cfg["svc_views"])):
        if usluga in stawki:
            _ustaw(session, klucz, str(stawki[usluga]))
            zapisane[klucz] = stawki[usluga]
    session.commit()
    return {"rates": zapisane, "unit_cost": ustawienia(session)["unit_cost"]}


# --------------------------------------------------------------------------- #
#  Zamówienia                                                                  #
# --------------------------------------------------------------------------- #
def _alert(session, b: dict, *, tylko_raz_dziennie: bool = True) -> bool:
    """Powiadomienie o niskim saldzie — najwyżej raz na dobę.

    Dzwonek i push idą przez `notify_admins`, więc alert widzi każdy admin na
    swoim telefonie, a nie tylko ten, kto akurat siedzi w panelu."""
    if not b or b.get("error") or not b.get("low"):
        return False
    dzien = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = session.get(AppSetting, KLUCZ_ALERT)
    if tylko_raz_dziennie and row and row.value == dzien:
        return False
    if row is None:
        row = AppSetting(key=KLUCZ_ALERT)
        session.add(row)
    row.value = dzien
    session.commit()
    notify.notify_admins(
        "admin_reach", "Reach balance is low",
        f"${b['value']:.2f} left — about {b['posts_left']} more posts. Top up the provider account.",
        tag="reach")
    return True


def sprawdz_saldo(session, *, transport=None) -> dict:
    """Dobowy strażnik salda. Wołany z ticka — nie wymaga żadnej publikacji."""
    cfg = ustawienia(session)
    if not cfg["enabled"] or not is_enabled():
        return {"skipped": "off"}
    # Cennik odświeżamy przy tej samej okazji: raz na dobę wystarczy, a dzięki
    # temu „koszt posta" i licznik postów nadążają za zmianą cen u dostawcy.
    cennik = odswiez_cennik(session, transport=transport)
    b = saldo_z_ustawien(session, transport=transport)
    if b.get("error"):
        return {"error": b["error"]}
    return {"balance": b["value"], "posts_left": b["posts_left"],
            "low": b["low"], "alerted": _alert(session, b),
            "unit_cost": cennik.get("unit_cost", cfg["unit_cost"])}


def zamow(session, link: str, *, transport=None, powod: str = "manual",
          wymagaj_wlaczenia: bool = True) -> dict:
    """Reakcje i wyświetlenia pod jednym postem. Best-effort, nigdy nie rzuca.

    Przełącznik w panelu rządzi AUTOMATEM: wyłączony znaczy „nie dokupuj sam
    pod każdą publikacją". Ręczne zamówienie z panelu to świadoma decyzja
    admina i działa niezależnie — inaczej przycisk „Boost" byłby ślepy.
    """
    cfg = ustawienia(session)
    if wymagaj_wlaczenia and not cfg["enabled"]:
        return {"ordered": 0, "skipped": "reach bot off"}
    if not is_enabled():
        return {"ordered": 0, "skipped": "reach provider not configured"}
    if not (link or "").startswith("https://t.me/"):
        return {"ordered": 0, "skipped": "link must be a public t.me post url"}

    b = saldo_z_ustawien(session, transport=transport)
    if not b.get("error") and b["value"] < cfg["unit_cost"]:
        _zapisz_wynik(session, f"SKIPPED balance ${b['value']:.2f}")
        _alert(session, {**b, "low": True})
        return {"ordered": 0, "skipped": f"balance too low (${b['value']:.2f})",
                "balance": b["value"]}

    zlecenia = [
        ("reactions", cfg["svc_reactions"], cfg["qty_reactions"]),
        ("views", cfg["svc_views"], cfg["qty_views"]),
    ]
    wyniki = []
    for etykieta, usluga, ilosc in zlecenia:
        if ilosc <= 0:
            continue
        ok, odp = _api("add", {"service": usluga, "link": link, "quantity": ilosc},
                       transport=transport)
        wyniki.append({"label": etykieta, "order": odp.get("order") if ok else None,
                       "error": None if ok else odp.get("error")})

    udane = [w for w in wyniki if w["order"]]
    bledy = [w for w in wyniki if not w["order"]]
    print(f"[reach] {powod} {link} -> {json.dumps(wyniki)}")
    opis = (f"{link.rsplit('/', 1)[-1]}: {len(udane)}/{len(wyniki)} ok"
            + (f" — {bledy[0]['error']}" if bledy else ""))
    _zapisz_wynik(session, opis)

    if bledy:
        notify.notify_admins(
            "admin_reach", "Reach order failed",
            f"{bledy[0]['error'] or 'unknown error'} ({link})", tag="reach")

    # Saldo po zakupie: alert ma polecieć zanim konto zejdzie do zera, a nie
    # dopiero przy pierwszym odrzuconym zamówieniu.
    po = saldo_z_ustawien(session, transport=transport)
    _alert(session, po)
    return {"ordered": len(udane), "results": wyniki, "link": link,
            "balance": po.get("value")}


def _zapisz_wynik(session, opis: str) -> None:
    """Ostatni wynik w panelu obok Payout BOT-a. Osobna, best-effortowa transakcja."""
    try:
        dzien = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        _ustaw(session, "last_result", f"{dzien} {opis}"[:200])
        session.commit()
    except Exception:  # pragma: no cover
        session.rollback()


def po_publikacji(session, link: str | None, *, transport=None) -> dict:
    """Hak dla Payout BOT-a: zamówienie zaraz po udanej publikacji posta."""
    if not link:
        return {"ordered": 0, "skipped": "no post url"}
    try:
        return zamow(session, link, transport=transport, powod="payout")
    except Exception as e:  # pragma: no cover - zamówienie nie może cofnąć wypłaty
        print(f"[reach] zamówienie po publikacji nieudane: {e}")
        return {"ordered": 0, "error": str(e)}
