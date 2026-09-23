"""Maile PRZYCHODZĄCE — do zakładki Mail w panelu, z podziałem na marki.

Odbiorem zajmuje się Resend Receiving: domena ma MX u Resenda, a Resend
trzyma odebrane maile i oddaje je przez API (`GET /emails/receiving`, treść
przez `GET /emails/receiving/{id}`). Panel czyta je na żądanie — bez webhooka
i bez własnej tabeli: lista i tak żyje u Resenda, a kopia w bazie byłaby
drugim miejscem do pilnowania.

Marka to domena ODBIORCY: platforma = domena `SUPPORT_EMAIL`/`MAIL_FROM`,
landing = domena nadawcy `lead_mail` (`RESEND_FROM`/`LEAD_MAIL_FROM`). Z
ustawień, nie z kodu — nazwa domeny partnera nie może stać w kodzie.

Kluczy może być dwa: `RESEND_API_KEY` (konto landingu) i opcjonalny
`RESEND_API_KEY_PTF`, gdy domena platformy siedzi na innym koncie Resenda.
Lista zbiera z obu, każdy wiersz pamięta, z którego klucza przyszedł.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.utils import parseaddr

from .config import get_settings
from .lead_mail import RESEND_UA

settings = get_settings()

RESEND_API = "https://api.resend.com"
TIMEOUT_SEK = 12
MARKI = ("ptf", "fx")


def _domena(adres: str | None) -> str:
    return parseaddr(adres or "")[1].rpartition("@")[2].strip().lower()


def domeny() -> dict[str, set[str]]:
    """Domeny każdej marki z ustawień; puste i lokalne odpadają."""
    def zbierz(*adresy):
        return {d for d in map(_domena, adresy) if d and "." in d and not d.endswith(".local")}
    return {"ptf": zbierz(settings.support_email, settings.mail_from),
            "fx": zbierz(settings.lead_mail_from)}


def klucze() -> list[str]:
    """Klucze Resenda do czytania, bez duplikatów, w stałej kolejności."""
    out = []
    for k in (settings.resend_api_key, getattr(settings, "resend_api_key_ptf", "")):
        if k and k not in out:
            out.append(k)
    return out


def _http_get(sciezka: str, klucz: str) -> dict:
    """GET do API Resenda. Ten sam `User-Agent` co przy wysyłce — bez niego
    Cloudflare przed api.resend.com odbija urllib jako bota (403/1010)."""
    req = urllib.request.Request(
        RESEND_API + sciezka,
        headers={"Authorization": f"Bearer {klucz}", "Accept": "application/json",
                 "User-Agent": RESEND_UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            return json.loads(odp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        cialo = e.read().decode("utf-8", "replace")[:300]
        try:
            powod = json.loads(cialo).get("message") or cialo
        except ValueError:
            powod = cialo
        raise RuntimeError(f"resend {e.code}: {powod}") from None


def marka_maila(do: list[str] | None, mapa: dict[str, set[str]] | None = None) -> str | None:
    """Do której marki przyszedł mail — po domenie pierwszego pasującego odbiorcy."""
    mapa = mapa or domeny()
    for adres in do or []:
        d = _domena(adres)
        for marka, zbior in mapa.items():
            if d in zbior:
                return marka
    return None


def lista(marka: str = "all", *, limit: int = 100) -> dict:
    """Odebrane maile (najnowsze pierwsze) z podziałem na marki.

    Nigdy nie rzuca: błąd któregoś klucza trafia do `errors`, a reszta listy
    zostaje — panel ma pokazać, co się da, i powiedzieć, czego nie.
    """
    mapa = domeny()
    wynik = {"items": [], "errors": [], "configured": bool(klucze()),
             "domains": {k: sorted(v) for k, v in mapa.items()}}
    widziane = set()
    for i, klucz in enumerate(klucze()):
        try:
            dane = _http_get(f"/emails/receiving?limit={max(1, min(limit, 100))}", klucz)
        except Exception as e:  # noqa: BLE001 — sieć, JSON, odmowa: to samo dla panelu
            wynik["errors"].append(str(e)[:300])
            continue
        for m in dane.get("data") or []:
            if not m.get("id") or m["id"] in widziane:
                continue
            widziane.add(m["id"])
            do = m.get("to") or []
            mk = marka_maila(do, mapa)
            if marka in MARKI and mk != marka:
                continue
            wynik["items"].append({
                "id": m["id"], "k": i, "brand": mk,
                "from": m.get("from") or "", "from_email": parseaddr(m.get("from") or "")[1].lower(),
                "to": do, "subject": m.get("subject") or "",
                "created_at": m.get("created_at"),
                "attachments": len(m.get("attachments") or []),
            })
    wynik["items"].sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return wynik


def jeden(email_id: str, k: int = 0) -> dict:
    """Pełna treść jednego odebranego maila (tekst i HTML). Rzuca RuntimeError."""
    ks = klucze()
    if not ks:
        raise RuntimeError("RESEND_API_KEY is not set")
    klucz = ks[k] if 0 <= k < len(ks) else ks[0]
    m = _http_get(f"/emails/receiving/{urllib.request.quote(email_id, safe='')}", klucz)
    return {"id": m.get("id") or email_id, "brand": marka_maila(m.get("to")),
            "from": m.get("from") or "", "from_email": parseaddr(m.get("from") or "")[1].lower(),
            "to": m.get("to") or [], "cc": m.get("cc") or [],
            "reply_to": m.get("reply_to") or [],
            "subject": m.get("subject") or "", "created_at": m.get("created_at"),
            "text": m.get("text") or "", "html": m.get("html") or "",
            "attachments": [{"filename": a.get("filename"), "size": a.get("size")}
                            for a in (m.get("attachments") or [])]}
