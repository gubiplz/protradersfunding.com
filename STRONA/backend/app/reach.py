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
from datetime import datetime, timedelta, timezone

import random
import re

from . import notify, telegram
from .config import get_settings
from .models import AppSetting

# Publiczna nazwa kanału wg reguł Telegrama (5–32 znaki, litera na starcie).
TELEGRAM_NAZWA = re.compile(r"^[a-z][a-z0-9_]{4,31}$")

settings = get_settings()

TIMEOUT_SEK = 15
PREFIKS = "reach_"
KLUCZ_WYNIK = PREFIKS + "last_result"
KLUCZ_ALERT = PREFIKS + "last_alert_day"

DOMYSLNE = {
    "enabled": "0",
    # Reakcje z najtańszej półki dostawcy (0.0275/1000, m.in. 8612) mimo nazwy
    # „Positive" idą ze wspólnej puli emoji i potrafią wsypać pod post 🍌 albo 🗿.
    "svc_reactions": "7256",
    "qty_reactions": "30",
    "svc_views": "8407",
    "qty_views": "400",
    # Stała ilość albo ZAKRES: „range" losuje pod każdym postem liczbę z
    # [qty_x, qty_x_max], żeby kolejne posty nie miały co do sztuki tyle samo
    # reakcji i wyświetleń — równe liczby pod każdym postem zdradzają zakup.
    "qty_mode": "fixed",
    "qty_reactions_max": "",
    "qty_views_max": "",
    "min_balance": "1",
    # Ostatnia deska ratunku dla bramki salda: normalnie koszt liczy się
    # z cennika dostawcy (patrz `odswiez_cennik`), ale gdy cennik nie odpowie,
    # lepiej mieć przybliżenie niż wpuścić zamówienie na puste konto.
    "unit_cost": "0.055",
    # Stawki za 1000 sztuk, przepisane z cennika dostawcy przy dobowym ticku.
    "rate_reactions": "",
    "rate_views": "",
    # Nazwy usług z cennika — panel ma pokazywać, CO zamawiamy. Usługa
    # „Negative Reactions" ma id o jeden większe niż „Positive", więc literówka
    # w ID kosztowałaby kanał 💩 zamiast 🔥.
    "name_reactions": "",
    "name_views": "",
    # Obsługiwane kanały: JSON [{"username","label","on","qty_reactions","qty_views"}].
    # Pusty = tylko kanał wypłat (dopisywany automatycznie z TELEGRAM_CHAT_ID
    # przy pierwszym odczycie), żeby lista nie startowała pusta i nie kłamała.
    # Ilości per kanał są opcjonalne (null = globalne): kanał z 170 subami i
    # kanał z 400 nie potrzebują tyle samo wyświetleń, a wspólna liczba robi
    # z jednego z nich post oglądany trzy razy częściej, niż ma subskrybentów.
    "channels": "",
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
        "qty_mode": out["qty_mode"] if out["qty_mode"] in ("fixed", "range") else "fixed",
        "min_balance": float(out["min_balance"]),
        "fallback_cost": float(out["unit_cost"]),
        "last_result": wynik.value if wynik else None,
        "name_reactions": out["name_reactions"],
        "name_views": out["name_views"],
        # Ostrzeżenie dla panelu: usługa negatywnych reakcji ma id o jeden
        # większe niż pozytywna, a pomyłka jest widoczna dopiero na kanale.
        "reactions_positive": ("positive" in out["name_reactions"].lower()
                               if out["name_reactions"] else None),
    }
    # Górny koniec zakresu; w trybie stałym (albo bez wpisanego końca) = dolny.
    for klucz in ("qty_reactions", "qty_views"):
        gora = _ilosc_lub_nic(out[klucz + "_max"])
        cfg[klucz + "_max"] = (max(cfg[klucz], gora) if cfg["qty_mode"] == "range"
                               and gora is not None else cfg[klucz])
    # Koszt posta liczymy z zapamiętanych stawek dostawcy — admin nie ma go po
    # co wpisywać ręcznie, a przy zmianie cennika sam się poprawia. W zakresie
    # z GÓRNEGO końca: bramka salda i ostrzeżenia mają liczyć najgorszy post.
    stawki = {}
    for klucz, pole in (("rate_reactions", "qty_reactions"), ("rate_views", "qty_views")):
        try:
            stawki[klucz] = float(out[klucz])
        except (TypeError, ValueError):
            stawki[klucz] = None
    if stawki["rate_reactions"] is not None and stawki["rate_views"] is not None:
        cfg["unit_cost"] = round(cfg["qty_reactions_max"] * stawki["rate_reactions"] / 1000
                                 + cfg["qty_views_max"] * stawki["rate_views"] / 1000, 6)
        cfg["cost_from"] = "provider"
    else:
        cfg["unit_cost"] = cfg["fallback_cost"]
        cfg["cost_from"] = "estimate"
    return cfg


def zapisz_ustawienia(session, **pola) -> dict:
    """Zapis z panelu. Rzuca `ValueError` z komunikatem po angielsku."""
    # Zakres sprawdzamy PRZED zapisem, z pól żądania i obecnych wartości: sesja
    # ma autoflush=False, więc świeżo dodanych wierszy odczyt by nie zobaczył,
    # a częściowy zapis „od 30 do 10" zostałby w bazie mimo błędu.
    teraz = ustawienia(session)
    tryb = pola.get("qty_mode") or teraz["qty_mode"]
    if tryb not in ("fixed", "range"):
        raise ValueError("'qty_mode' must be 'fixed' or 'range'")
    if tryb == "range":
        for klucz, nazwa in (("qty_reactions", "reactions"), ("qty_views", "views")):
            od = pola.get(klucz) if pola.get(klucz) is not None else teraz[klucz]
            do = pola.get(klucz + "_max")
            if do is not None and int(float(do)) < int(float(od)):
                raise ValueError(f"The {nazwa} range is upside down: {int(float(od))} to {int(float(do))}")
    if pola.get("enabled") is not None:
        _ustaw(session, "enabled", "1" if pola["enabled"] else "0")

    for klucz, stawka, nazwa in (("svc_reactions", "rate_reactions", "name_reactions"),
                                 ("svc_views", "rate_views", "name_views")):
        wartosc = pola.get(klucz)
        if wartosc is None:
            continue
        if int(wartosc) <= 0:
            raise ValueError(f"'{klucz}' must be a positive service id")
        # Zmiana usługi unieważnia zapamiętaną stawkę i nazwę — inaczej panel
        # pokazywałby cenę i opis czegoś, czego już nie zamawiamy.
        if str(int(wartosc)) != str(ustawienia(session)[klucz]):
            _ustaw(session, stawka, "")
            _ustaw(session, nazwa, "")
        _ustaw(session, klucz, str(int(wartosc)))

    for klucz in ("qty_reactions", "qty_views", "min_balance", "unit_cost"):
        wartosc = pola.get(klucz)
        if wartosc is None:
            continue
        dol, gora = LIMITY[klucz]
        if not (dol <= float(wartosc) <= gora):
            raise ValueError(f"'{klucz}' must be between {dol:g} and {gora:g}")
        _ustaw(session, klucz, str(float(wartosc)))

    if pola.get("qty_mode") is not None:
        _ustaw(session, "qty_mode", pola["qty_mode"])
    for klucz in ("qty_reactions", "qty_views"):
        gora = pola.get(klucz + "_max")
        if gora is None:
            continue
        dol_z, gora_z = LIMITY[klucz]
        if not (dol_z <= float(gora) <= gora_z):
            raise ValueError(f"'{klucz}_max' must be between {dol_z:g} and {gora_z:g}")
        _ustaw(session, klucz + "_max", str(int(float(gora))))

    session.commit()
    return ustawienia(session)


# --------------------------------------------------------------------------- #
#  Obsługiwane kanały                                                          #
# --------------------------------------------------------------------------- #
def _czysta_nazwa(s: str) -> str:
    """`@Kanal`, `https://t.me/Kanal`, `t.me/Kanal/12` → `kanal`."""
    tekst = str(s or "").strip()
    for przedrostek in ("https://", "http://"):
        if tekst.startswith(przedrostek):
            tekst = tekst[len(przedrostek):]
    for host in ("t.me/", "telegram.me/"):
        if tekst.lower().startswith(host):
            tekst = tekst[len(host):]
    tekst = tekst.split("/")[0].split("?")[0].lstrip("@").strip()
    return tekst.lower()


def _ilosc_lub_nic(wartosc) -> int | None:
    """`None`/`""` → `None` („jak globalnie"), reszta → liczba całkowita."""
    if wartosc is None or (isinstance(wartosc, str) and not wartosc.strip()):
        return None
    try:
        return int(float(wartosc))
    except (TypeError, ValueError):
        return None


def kanaly(session) -> list[dict]:
    """Lista obsługiwanych kanałów. Kanał wypłat dopisuje się sam.

    Payout BOT publikuje tam, gdzie wskazuje `TELEGRAM_CHAT_ID`, więc ten
    kanał jest obsługiwany niezależnie od tego, co admin doda ręcznie —
    i musi być widoczny na liście, żeby panel nie kłamał o zasięgu.
    """
    row = _wiersz(session, "channels")
    try:
        lista = json.loads(row.value) if row and row.value else []
    except Exception:
        lista = []
    out = []
    for poz in lista if isinstance(lista, list) else []:
        nazwa = _czysta_nazwa(poz.get("username"))
        if not nazwa or any(k["username"] == nazwa for k in out):
            continue
        out.append({"username": nazwa,
                    "label": str(poz.get("label") or "")[:40],
                    "on": bool(poz.get("on", True)),
                    "qty_reactions": _ilosc_lub_nic(poz.get("qty_reactions")),
                    "qty_views": _ilosc_lub_nic(poz.get("qty_views")),
                    "qty_reactions_max": _ilosc_lub_nic(poz.get("qty_reactions_max")),
                    "qty_views_max": _ilosc_lub_nic(poz.get("qty_views_max")),
                    # Tryb kanału jawnie (przełącznik przy kanale); stare wpisy
                    # bez trybu: zakres, jeśli mają górny koniec.
                    "qty_mode": (poz.get("qty_mode") if poz.get("qty_mode") in ("fixed", "range")
                                 else "range" if (poz.get("qty_reactions_max") is not None
                                                  or poz.get("qty_views_max") is not None)
                                 else "fixed"),
                    "payout": False})

    info = telegram.chat_info(settings.telegram_chat_id) if telegram.is_enabled() else {}
    nazwa = _czysta_nazwa(info.get("username"))
    if nazwa:
        istniejacy = next((k for k in out if k["username"] == nazwa), None)
        if istniejacy:
            istniejacy["payout"] = True
            istniejacy["label"] = istniejacy["label"] or (info.get("title") or "Payouts")
        else:
            out.insert(0, {"username": nazwa, "label": info.get("title") or "Payouts",
                           "on": True, "qty_reactions": None, "qty_views": None,
                           "qty_reactions_max": None, "qty_views_max": None,
                           "qty_mode": "fixed", "payout": True})
    return out


def zapisz_kanaly(session, lista: list[dict]) -> list[dict]:
    """Zapis listy z panelu. Rzuca `ValueError` z komunikatem po angielsku."""
    czyste = []
    for poz in lista or []:
        nazwa = _czysta_nazwa((poz or {}).get("username"))
        if not nazwa:
            continue
        if not TELEGRAM_NAZWA.match(nazwa):
            raise ValueError(f"'{nazwa}' is not a valid public channel name")
        if any(k["username"] == nazwa for k in czyste):
            continue
        zakres = (poz or {}).get("qty_mode") == "range"
        wpis = {"username": nazwa,
                "label": str((poz or {}).get("label") or "")[:40],
                "on": bool((poz or {}).get("on", True)),
                "qty_mode": "range" if zakres else "fixed"}
        # Puste pole w panelu = „jak globalnie", nie „zero". Zero jest legalną
        # wartością (kanał bez reakcji), więc te dwa stany muszą się różnić.
        for klucz in ("qty_reactions", "qty_views"):
            ile = _ilosc_lub_nic((poz or {}).get(klucz))
            if ile is None:
                continue
            dol, gora = LIMITY[klucz]
            if not (dol <= ile <= gora):
                raise ValueError(f"'{klucz}' for @{nazwa} must be between "
                                 f"{dol:g} and {gora:g}")
            wpis[klucz] = ile
            # Kanał w trybie Range ma własny ZAKRES: górny koniec osobno,
            # pusty = stała liczba. W trybie Fixed górny koniec się nie liczy.
            maks = _ilosc_lub_nic((poz or {}).get(klucz + "_max")) if zakres else None
            if maks is not None and maks != ile:
                if not (ile <= maks <= gora):
                    raise ValueError(f"'{klucz}' range for @{nazwa} must go up, "
                                     f"from {ile} to at most {gora:g}")
                wpis[klucz + "_max"] = maks
        czyste.append(wpis)
    _ustaw(session, "channels", json.dumps(czyste))
    session.commit()
    return kanaly(session)


def kanal_wlaczony(session, username: str) -> bool:
    nazwa = _czysta_nazwa(username)
    return any(k["username"] == nazwa and k["on"] for k in kanaly(session))


def ilosci(session, username: str | None = None, *,
           qty_reactions: int | None = None, qty_views: int | None = None) -> dict:
    """Ile zamówić pod postem: jawnie podane → ustawienie kanału → globalne.

    Trzy poziomy, bo trzy różne decyzje: „tym razem inaczej" (ręczny boost),
    „ten kanał zawsze inaczej" (mały kanał nie udźwignie 400 wyświetleń) oraz
    domyślne, którymi jedzie automat.
    """
    cfg = ustawienia(session)
    # Każda ilość to przedział [qty_x, qty_x_max]; stała = przedział jednopunktowy.
    out = {"qty_reactions": cfg["qty_reactions"], "qty_reactions_max": cfg["qty_reactions_max"],
           "qty_views": cfg["qty_views"], "qty_views_max": cfg["qty_views_max"],
           "from": "global"}
    nazwa = _czysta_nazwa(username) if username else ""
    if nazwa:
        kanal = next((k for k in kanaly(session) if k["username"] == nazwa), None)
        if kanal:
            for klucz in ("qty_reactions", "qty_views"):
                if kanal.get(klucz) is not None:
                    out[klucz] = kanal[klucz]
                    maks = kanal.get(klucz + "_max") if kanal.get("qty_mode") == "range" else None
                    out[klucz + "_max"] = max(kanal[klucz], maks or kanal[klucz])
                    out["from"] = "channel"
    for klucz, jawne in (("qty_reactions", qty_reactions), ("qty_views", qty_views)):
        if jawne is None:
            continue
        dol, gora = LIMITY[klucz]
        if not (dol <= int(jawne) <= gora):
            raise ValueError(f"'{klucz}' must be between {dol:g} and {gora:g}")
        out[klucz] = out[klucz + "_max"] = int(jawne)
        out["from"] = "explicit"
    return out


def losuj_ilosci(ile: dict, rng=None) -> dict:
    """Konkretne liczby na TEN post: z przedziału, gdy jest zakres, inaczej stałe."""
    rng = rng or random.SystemRandom()
    return {k: (rng.randint(ile[k], ile[k + "_max"]) if ile[k + "_max"] > ile[k] else ile[k])
            for k in ("qty_reactions", "qty_views")}


def koszt(session, qty_reactions: int, qty_views: int) -> float:
    """Koszt jednego zamówienia dla PODANYCH ilości (cennik dostawcy).

    Bramka salda liczyła dotąd koszt z ilości globalnych — po wprowadzeniu
    ustawień per kanał kłamałaby o każdym kanale, który ma własne."""
    cfg = ustawienia(session)
    stawki = {}
    for klucz in ("rate_reactions", "rate_views"):
        row = _wiersz(session, klucz)
        try:
            stawki[klucz] = float(row.value) if row and row.value else None
        except (TypeError, ValueError):
            stawki[klucz] = None
    if stawki["rate_reactions"] is None or stawki["rate_views"] is None:
        return cfg["fallback_cost"]
    return round(qty_reactions * stawki["rate_reactions"] / 1000
                 + qty_views * stawki["rate_views"] / 1000, 6)


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
    stawki, nazwy = {}, {}
    for poz in odp if isinstance(odp, list) else []:
        try:
            usluga = int(poz["service"])
            stawki[usluga] = float(poz["rate"])
            nazwy[usluga] = str(poz.get("name") or "")[:120]
        except (KeyError, TypeError, ValueError):
            continue
    zapisane = {}
    for klucz, nazwa_klucz, usluga in (("rate_reactions", "name_reactions", cfg["svc_reactions"]),
                                       ("rate_views", "name_views", cfg["svc_views"])):
        if usluga in stawki:
            _ustaw(session, klucz, str(stawki[usluga]))
            _ustaw(session, nazwa_klucz, nazwy.get(usluga, ""))
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
          wymagaj_wlaczenia: bool = True,
          qty_reactions: int | None = None, qty_views: int | None = None) -> dict:
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

    # Kanał z linku rządzi ilościami, chyba że wywołujący poda je wprost.
    nazwa_kanalu = _czysta_nazwa(link.rsplit("/", 2)[-2] if link.count("/") >= 4 else "")
    zakres = ilosci(session, nazwa_kanalu, qty_reactions=qty_reactions, qty_views=qty_views)
    ile = {**zakres, **losuj_ilosci(zakres)}
    cena = koszt(session, ile["qty_reactions"], ile["qty_views"])

    b = saldo_z_ustawien(session, transport=transport)
    if not b.get("error") and b["value"] < cena:
        _zapisz_wynik(session, f"SKIPPED balance ${b['value']:.2f}")
        _alert(session, {**b, "low": True})
        return {"ordered": 0, "skipped": f"balance too low (${b['value']:.2f})",
                "balance": b["value"]}

    zlecenia = [
        ("reactions", cfg["svc_reactions"], ile["qty_reactions"]),
        ("views", cfg["svc_views"], ile["qty_views"]),
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
    # Numery zamówień zostają w panelu: bez nich sprawdzenie u dostawcy, co
    # naprawdę poszło pod post, wymaga grzebania w logach hostingu.
    opis = (f"{link.rsplit('/', 1)[-1]}: {len(udane)}/{len(wyniki)} ok"
            + (f" #{','.join(str(w['order']) for w in udane)}" if udane else "")
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
            "balance": po.get("value"), "quantities": ile, "cost": cena}


def _zapisz_wynik(session, opis: str) -> None:
    """Ostatni wynik w panelu obok Payout BOT-a. Osobna, best-effortowa transakcja."""
    try:
        dzien = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        _ustaw(session, "last_result", f"{dzien} {opis}"[:200])
        session.commit()
    except Exception:  # pragma: no cover
        session.rollback()


# --------------------------------------------------------------------------- #
#  Subskrybenci kanału                                                         #
# --------------------------------------------------------------------------- #
# Reakcje i wyświetlenia kupuje się POD POSTEM, subskrybentów POD KANAŁEM —
# ten sam dostawca, ta sama akcja `add`, ale linkiem jest adres kanału, a nie
# posta. Dlatego to osobna ścieżka, a nie flaga w `zamow`.
#
# Usługi nie ma w ustawieniach na stałe: dostawca ma ich kilkadziesiąt (różne
# źródła, różne tempo, różny odpad), ceny i dostępność zmieniają się z tygodnia
# na tydzień, a pomyłka w id kosztuje realne pieniądze. Panel pokazuje listę
# z cennika i admin wybiera świadomie — kod niczego nie zgaduje.
SUB_LIMITY = (10, 100000)

# Po czym poznać usługę „członkowie kanału" w cenniku liczącym kilkaset pozycji.
_SUB_SZUKANE = ("member", "subscriber")
# Wyrazy, które znaczą COŚ INNEGO niż dołączenie do kanału. „Leave"/„remove"
# to usługi kasujące subskrybentów — nazwa też zawiera „members".
_SUB_ODPADA = ("leave", "remove", "unsub", "drop", "view", "reaction", "vote",
               "poll", "comment", "share", "report", "story")


def uslugi_subskrypcji(*, transport=None, limit: int = 40) -> list[dict]:
    """Usługi „Telegram members" z cennika dostawcy, od najtańszej.

    Panel wywołuje to na żądanie (cennik waży ~1,5 MB), żeby admin widział
    nazwę, cenę za 1000 i widełki ilości ZANIM cokolwiek zamówi.
    """
    ok, odp = _api("services", transport=transport, jako_lista=True)
    if not ok:
        raise ValueError(str(odp.get("error") if isinstance(odp, dict) else "bad response"))
    out = []
    for poz in odp if isinstance(odp, list) else []:
        opis = f"{poz.get('name') or ''} {poz.get('category') or ''}".lower()
        if "telegram" not in opis:
            continue
        if not any(s in opis for s in _SUB_SZUKANE):
            continue
        if any(s in opis for s in _SUB_ODPADA):
            continue
        try:
            out.append({"service": int(poz["service"]),
                        "name": str(poz.get("name") or "")[:120],
                        "category": str(poz.get("category") or "")[:80],
                        "rate": float(poz["rate"]),
                        "min": int(float(poz.get("min") or 0)),
                        "max": int(float(poz.get("max") or 0))})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda u: u["rate"])
    return out[:limit]


def zamow_subskrypcje(session, kanal: str, ilosc: int, usluga: int,
                      *, transport=None) -> dict:
    """Subskrybenci na kanał. Rzuca `ValueError` z komunikatem po angielsku.

    W odróżnieniu od `zamow` NIE jest best-effort: to ręczne, płatne kliknięcie
    admina, więc odmowa ma wrócić do panelu jako zdanie, a nie zniknąć w logu.
    Cena liczy się ZE ŚWIEŻEGO cennika dostawcy, nie z tego, co przyszło
    z przeglądarki — inaczej bramka salda pilnowałaby liczby podanej przez
    stronę, którą ma chronić.
    """
    if not is_enabled():
        raise ValueError("Reach provider is not configured")
    # Prywatne zaproszenie (`t.me/joinchat/AAA`, `t.me/+AAA`) NIE jest nazwą
    # kanału: `_czysta_nazwa` zwraca z niego „joinchat", co przechodzi walidację
    # nazwy i wysyła zamówienie pod adres, pod którym nie ma naszego kanału.
    # Pieniądze wydane, kanał bez zmian, dostawca bez błędu.
    surowy = str(kanal or "").strip().lower()
    if "joinchat" in surowy or "/+" in surowy or surowy.startswith("+"):
        raise ValueError("That is a private invite link — subscribers can only be "
                         "ordered for a channel with a public @name")
    nazwa = _czysta_nazwa(kanal)
    if not TELEGRAM_NAZWA.match(nazwa):
        raise ValueError(f"'{kanal}' is not a valid public channel name")
    dol, gora = SUB_LIMITY
    try:
        ilosc = int(ilosc)
        usluga = int(usluga)
    except (TypeError, ValueError):
        raise ValueError("Quantity and service must be numbers")
    if not (dol <= ilosc <= gora):
        raise ValueError(f"Quantity must be between {dol} and {gora}")

    wybrana = next((u for u in uslugi_subskrypcji(transport=transport)
                    if u["service"] == usluga), None)
    if wybrana is None:
        raise ValueError(f"Service {usluga} is not on the provider's member list")
    if wybrana["min"] and ilosc < wybrana["min"]:
        raise ValueError(f"This service takes at least {wybrana['min']}")
    if wybrana["max"] and ilosc > wybrana["max"]:
        raise ValueError(f"This service takes at most {wybrana['max']}")

    cena = round(ilosc * wybrana["rate"] / 1000, 4)
    b = saldo(transport=transport, unit_cost=cena or None)
    if not b.get("error") and b["value"] < cena:
        raise ValueError(f"Balance is ${b['value']:.2f}, this order costs ${cena:.2f}")

    link = f"https://t.me/{nazwa}"
    ok, odp = _api("add", {"service": usluga, "link": link, "quantity": ilosc},
                   transport=transport)
    if not ok:
        raise ValueError(str(odp.get("error") or "provider refused the order"))
    print(f"[reach] subskrypcje @{nazwa} x{ilosc} usluga {usluga} -> {odp.get('order')}")
    _zapisz_wynik(session, f"@{nazwa}: +{ilosc} members #{odp.get('order')} (${cena:.2f})")
    po = saldo_z_ustawien(session, transport=transport)
    _alert(session, po)
    return {"order": odp.get("order"), "channel": nazwa, "quantity": ilosc,
            "cost": cena, "service": wybrana, "balance": po.get("value")}


def po_publikacji(session, link: str | None, *, transport=None,
                  powod: str = "payout") -> dict:
    """Hak po udanej publikacji posta — Payout BOT i kolejka treści.

    `powod` trafia do dziennika zamówień, więc musi mówić, KTO zamówił:
    inaczej nie da się odpowiedzieć, na co poszły pieniądze.
    """
    if not link:
        return {"ordered": 0, "skipped": "no post url"}
    try:
        nazwa = _czysta_nazwa(link.rsplit("/", 2)[-2] if link.count("/") >= 4 else "")
        if nazwa and not kanal_wlaczony(session, nazwa):
            return {"ordered": 0, "skipped": f"channel @{nazwa} is off the list"}
        mid = link.rstrip("/").rsplit("/", 1)[-1]
        if nazwa and mid.isdigit() and not _zajmij_post(session, nazwa, int(mid)):
            return {"ordered": 0, "skipped": "duplicate"}
        return zamow(session, link, transport=transport, powod=powod)
    except Exception as e:  # pragma: no cover - zamówienie nie może cofnąć wypłaty
        print(f"[reach] zamówienie po publikacji nieudane: {e}")
        return {"ordered": 0, "error": str(e)}


def z_kanalu(session, post: dict, *, transport=None) -> dict:
    """Nowy post na obserwowanym kanale (webhook Telegrama).

    Telegram przysyła `channel_post` tylko z kanałów, w których bot jest
    administratorem — dlatego panel pokazuje ten status per kanał. Albumy
    lecą jako kilka wiadomości z jednym `media_group_id`, a webhooki bywają
    ponawiane, więc pilnujemy ostatnio obsłużonego posta per kanał.
    """
    czat = (post or {}).get("chat") or {}
    nazwa = _czysta_nazwa(czat.get("username"))
    mid = (post or {}).get("message_id")
    if not nazwa or not mid:
        return {"ordered": 0, "skipped": "channel has no public name"}
    if not kanal_wlaczony(session, nazwa):
        return {"ordered": 0, "skipped": "channel not watched"}
    if not ustawienia(session)["enabled"]:
        return {"ordered": 0, "skipped": "reach bot off"}
    tresc = any(post.get(k) for k in ("text", "caption", "photo", "video", "animation",
                                      "document", "audio", "voice", "poll"))
    if not tresc:
        return {"ordered": 0, "skipped": "no content"}

    grupa = str(post.get("media_group_id") or "")
    if not _zajmij_post(session, nazwa, int(mid), grupa, chat_id=czat.get("id")):
        return {"ordered": 0, "skipped": "duplicate"}

    return zamow(session, f"https://t.me/{nazwa}/{mid}", transport=transport,
                 powod=f"channel @{nazwa}")


# --------------------------------------------------------------------------- #
#  Znacznik „ostatni obsłużony post" per kanał                                 #
# --------------------------------------------------------------------------- #
# Trzy drogi zamawiają pod postem: webhook Telegrama (`z_kanalu`), nasza
# publikacja (`po_publikacji`) i skan kanału (`skanuj_kanaly`). Wszystkie
# przechodzą przez JEDEN znacznik na kanał, zajmowany atomowo — inaczej post
# złapany dwiema drogami (albo przez dwie równoległe funkcje Vercela) byłby
# opłacony dwa razy.
def _klucz_posta(nazwa: str) -> str:
    return f"seen_@{nazwa}"


def _ostatni_post(session, nazwa: str, chat_id=None) -> tuple[int, str]:
    """(numer posta, media_group_id) ostatnio obsłużonego posta kanału.

    Starsze wersje trzymały znacznik pod id czatu (`seen_-100…`) albo nazwą
    bez „@" — czytamy też te, żeby wdrożenie nie odświeżyło starych postów.
    """
    najlepszy = (0, "")
    klucze = [_klucz_posta(nazwa), f"seen_{nazwa}"] + ([f"seen_{chat_id}"] if chat_id else [])
    for klucz in klucze:
        row = _wiersz(session, klucz)
        if not row or not row.value:
            continue
        try:
            mid, grupa = row.value.split(":", 1)
            if int(mid) > najlepszy[0]:
                najlepszy = (int(mid), grupa)
        except (ValueError, TypeError):
            continue
    return najlepszy


def _zajmij_post(session, nazwa: str, mid: int, grupa: str = "", *, chat_id=None) -> bool:
    """Zajmuje post `mid` kanału. False = ten post (albo nowszy) już obsłużony.

    Compare-and-set na `app_settings`: UPDATE … WHERE value = <stara wartość>
    przechodzi tylko jednemu z równoległych wywołań.
    """
    stary_mid, stara_grupa = _ostatni_post(session, nazwa, chat_id)
    if stary_mid >= mid or (grupa and grupa == stara_grupa):
        return False
    klucz = PREFIKS + _klucz_posta(nazwa)
    nowa = f"{mid}:{grupa}"
    row = session.get(AppSetting, klucz)
    try:
        if row is None:
            session.add(AppSetting(key=klucz, value=nowa))
            session.commit()
            return True
        stara = row.value
        ile = (session.query(AppSetting)
               .filter(AppSetting.key == klucz, AppSetting.value == stara)
               .update({AppSetting.value: nowa}, synchronize_session=False))
        session.commit()
        session.expire_all()
        return ile == 1
    except Exception:  # pragma: no cover - wyścig na INSERT: wygrał ktoś inny
        session.rollback()
        return False


# --------------------------------------------------------------------------- #
#  Skan kanałów — zapas na wypadek, gdy webhook milczy                          #
# --------------------------------------------------------------------------- #
# Webhook zależy od rzeczy, których stąd nie widać: czy bot jest adminem, czy
# `setWebhook` wskazuje na nas i czy `allowed_updates` zawiera `channel_post`.
# 2026-09-23 post na @forex_passing nie dostał nic, bo Telegram po prostu nie
# zapukał. Publiczny podgląd `t.me/s/<kanał>` nie wymaga niczego — skan co
# kilka minut łapie posty, których webhook nie zgłosił.
SKAN_CO_MIN = 10
# Post świeższy niż to zostawiamy webhookowi i naszej publikacji — obie drogi
# zajmują znacznik w sekundach, skan nie ma się z nimi ścigać.
SKAN_KARENCJA = timedelta(minutes=2)
# Starsze posty nie dostają zamówienia: zasięg dokupiony po dwóch dniach nie
# wygląda naturalnie, a po długiej przerwie skan nie może wysypać budżetu.
SKAN_MAX_WIEK = timedelta(hours=48)
SKAN_MAX_NA_KANAL = 3
_POST = re.compile(r'data-post="([A-Za-z0-9_]+)/(\d+)"')
_CZAS = re.compile(r'<time datetime="([^"]+)"')


def _pobierz_strone(url: str) -> str:
    zadanie = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")})
    with urllib.request.urlopen(zadanie, timeout=TIMEOUT_SEK) as odp:
        return odp.read(2_000_000).decode("utf-8", "replace")


def posty_kanalu(nazwa: str, *, fetch=None) -> list[tuple[int, datetime]]:
    """(numer, czas UTC) postów z publicznego podglądu, rosnąco.

    Komunikaty serwisowe („kanał utworzony", „przypięto") odpadają — to nie
    są posty, pod którymi ktokolwiek zobaczy reakcje.
    """
    html = (fetch or _pobierz_strone)(f"https://t.me/s/{nazwa}")
    out = {}
    for blok in html.split("tgme_widget_message_wrap")[1:]:
        if "service_message" in blok:
            continue
        p, c = _POST.search(blok), _CZAS.search(blok)
        if not p or not c or p.group(1).lower() != nazwa:
            continue
        try:
            kiedy = datetime.fromisoformat(c.group(1))
        except ValueError:
            continue
        if kiedy.tzinfo is None:
            kiedy = kiedy.replace(tzinfo=timezone.utc)
        out[int(p.group(2))] = kiedy.astimezone(timezone.utc)
    return sorted(out.items())


def _pora_skanu(session, teraz: datetime) -> bool:
    """Throttle: jeden skan na SKAN_CO_MIN, zajmowany compare-and-set."""
    klucz = PREFIKS + "scan_at"
    row = session.get(AppSetting, klucz)
    nowa = teraz.isoformat()
    try:
        if row is None:
            session.add(AppSetting(key=klucz, value=nowa))
            session.commit()
            return True
        try:
            ostatni = datetime.fromisoformat(row.value)
            if teraz - ostatni < timedelta(minutes=SKAN_CO_MIN):
                return False
        except (TypeError, ValueError):
            pass
        ile = (session.query(AppSetting)
               .filter(AppSetting.key == klucz, AppSetting.value == row.value)
               .update({AppSetting.value: nowa}, synchronize_session=False))
        session.commit()
        session.expire_all()
        return ile == 1
    except Exception:  # pragma: no cover
        session.rollback()
        return False


def skanuj_kanaly(session, *, fetch=None, transport=None, teraz: datetime | None = None,
                  wymus: bool = False) -> dict:
    """Zamawia pod postami, których webhook nie zgłosił. Nigdy nie rzuca.

    Pierwszy skan kanału bez znacznika zamawia TYLKO pod najnowszym postem
    (o ile jest świeży) — inaczej wdrożenie opłaciłoby od nowa całą historię
    kanału, także posty już podbite ręcznie.
    """
    teraz = teraz or datetime.now(timezone.utc)
    if not ustawienia(session)["enabled"]:
        return {"ordered": 0, "skipped": "reach bot off"}
    if not is_enabled():
        return {"ordered": 0, "skipped": "reach provider not configured"}
    if not wymus and not _pora_skanu(session, teraz):
        return {"ordered": 0, "skipped": "not yet"}
    zamowione, bledy = [], []
    for kanal in kanaly(session):
        nazwa = kanal["username"]
        if not kanal["on"]:
            continue
        try:
            posty = posty_kanalu(nazwa, fetch=fetch)
        except Exception as e:
            bledy.append(f"@{nazwa}: {e}")
            continue
        if not posty:
            continue
        ostatni, _ = _ostatni_post(session, nazwa)
        kandydaci = [(mid, kiedy) for mid, kiedy in posty
                     if mid > ostatni and SKAN_KARENCJA <= teraz - kiedy <= SKAN_MAX_WIEK]
        if not ostatni:
            # Kanał wypłat: pod postami Payout BOT-a zamawia sam bot, a do dziś
            # nie zostawiał znacznika — pierwszy skan tylko ustawia punkt startu.
            kandydaci = [] if kanal["payout"] else [p for p in kandydaci if p[0] == posty[-1][0]]
            if not kandydaci:
                # Najnowszy post jest za stary albo za świeży — sam znacznik,
                # żeby następny skan liczył od tego miejsca.
                if teraz - posty[-1][1] >= SKAN_KARENCJA:
                    _zajmij_post(session, nazwa, posty[-1][0])
                continue
        for mid, _kiedy in kandydaci[-SKAN_MAX_NA_KANAL:]:
            if not _zajmij_post(session, nazwa, mid):
                continue
            wynik = zamow(session, f"https://t.me/{nazwa}/{mid}", transport=transport,
                          powod=f"scan @{nazwa}")
            if wynik.get("ordered"):
                zamowione.append(f"@{nazwa}/{mid}")
    if bledy:
        print(f"[reach] skan kanałów: {'; '.join(bledy)}")
    return {"ordered": len(zamowione), "posts": zamowione, "errors": bledy}
