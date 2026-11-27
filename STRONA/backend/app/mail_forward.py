"""Kopia maili ODEBRANYCH na skrzynkę zespołu (`MAIL_FORWARD_TO`).

Domena ma MX u Resenda, więc poczta ląduje tylko u niego (panel czyta ją
przez `app/inbox.py`). Kopia na zwykłą skrzynkę jest po to, żeby kod z
banku czy reset hasła na adres firmowy nie czekał, aż ktoś otworzy panel.

Webhook `email.received` niesie tylko ID — treść pobieramy WŁASNYM kluczem z
API Resenda. Dlatego nie trzeba sekretu podpisu: spreparowane wywołanie może
najwyżej wskazać prawdziwy mail jeszcze raz, a to blokuje dziennik
(`mail_log`, event `inbound_forward`, temat zaczyna się od ID maila).

Kopia wychodzi przez Resenda spod nadawcy landingu (jedyna domena
zweryfikowana tam do WYSYŁKI). `Reply-To` = oryginalny nadawca, więc
„Odpowiedz" w skrzynce trafia prosto do niego. Załączniki idą jako `path`
(Resend sam je pobiera); gdy to się nie uda, kopia idzie bez nich, z listą
nazw w treści — mail jest i tak w panelu.
"""
from __future__ import annotations

import html as html_mod
import json
import urllib.error
import urllib.request
from email.utils import formataddr, parseaddr

from .config import get_settings
from .db import SessionLocal
from .inbox import _http_get, klucze
from .lead_mail import RESEND_UA, RESEND_URL
from .models import MailLog

settings = get_settings()

EVENT = "inbound_forward"
TIMEOUT_SEK = 15


def cele() -> list[str]:
    """Adresy kopii z `MAIL_FORWARD_TO`, bez pustych i duplikatów."""
    out = []
    for a in (settings.mail_forward_to or "").split(","):
        a = parseaddr(a.strip())[1].lower()
        if "@" in a and a not in out:
            out.append(a)
    return out


def _nadawca() -> str:
    adr = parseaddr(settings.lead_mail_from or "")[1]
    return formataddr(("Inbox", adr)) if adr else ""


def _juz_byl(email_id: str) -> bool:
    s = SessionLocal()
    try:
        return s.query(MailLog.id).filter(
            MailLog.event == EVENT, MailLog.ok.is_(True),
            MailLog.subject.like(f"{email_id}%")).first() is not None
    finally:
        s.close()


def _zapisz(email_id: str, temat: str, do: str, blad: str | None) -> None:
    s = SessionLocal()
    try:
        s.add(MailLog(event=EVENT, to_email=do[:200],
                      subject=f"{email_id} {temat}"[:200],
                      ok=blad is None, error=(blad or None) and blad[:500]))
        s.commit()
    finally:
        s.close()


def _pobierz(email_id: str) -> tuple[dict, str]:
    """Mail z API Resenda i klucz, którym się dał pobrać. Rzuca RuntimeError."""
    ostatni = "RESEND_API_KEY is not set"
    sciezka = "/emails/receiving/" + urllib.request.quote(email_id, safe="")
    for k in klucze():
        try:
            return _http_get(sciezka, k), k
        except RuntimeError as e:
            ostatni = str(e)
    raise RuntimeError(ostatni)


def _zalaczniki(email_id: str, klucz: str) -> list[dict]:
    try:
        d = _http_get("/emails/receiving/" + urllib.request.quote(email_id, safe="")
                      + "/attachments", klucz)
    except RuntimeError:
        return []
    return [{"filename": a.get("filename") or "attachment", "path": a["download_url"]}
            for a in (d.get("data") or []) if a.get("download_url")]


def _post(dane: dict) -> None:
    req = urllib.request.Request(
        RESEND_URL, data=json.dumps(dane).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.resend_api_key}",
                 "Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": RESEND_UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            odp.read()
    except urllib.error.HTTPError as e:
        cialo = e.read().decode("utf-8", "replace")[:300]
        try:
            powod = json.loads(cialo).get("message") or cialo
        except ValueError:
            powod = cialo
        raise RuntimeError(f"resend {e.code}: {powod}") from None


def tresc(m: dict, zalaczniki_nazwy: list[str]) -> dict:
    """Temat, tekst i HTML kopii: krótki nagłówek „od / do", potem oryginał."""
    do = m.get("to") or []
    do = [do] if isinstance(do, str) else do
    od = m.get("from") or ""
    temat = m.get("subject") or "(no subject)"
    glowa = f"From: {od}\nTo: {', '.join(do)}\n"
    if zalaczniki_nazwy:
        glowa += "Attachments: " + ", ".join(zalaczniki_nazwy) + "\n"
    tekst = glowa + "\n" + (m.get("text") or "")
    oryginal_html = m.get("html") or (
        "<pre style='white-space:pre-wrap'>" + html_mod.escape(m.get("text") or "") + "</pre>")
    html = ("<div style='font:13px/1.5 sans-serif;color:#555;border-bottom:1px solid #ddd;"
            "padding-bottom:8px;margin-bottom:12px'>"
            + "<br>".join(html_mod.escape(l) for l in glowa.strip().split("\n"))
            + "</div>" + oryginal_html)
    return {"subject": f"[{do[0] if do else 'inbox'}] {temat}"[:250], "text": tekst, "html": html}


def przekaz(email_id: str) -> tuple[bool, str]:
    """`(ok, opis)`. ok=False tylko przy błędzie, który warto ponowić."""
    email_id = (email_id or "").strip()
    adresy = cele()
    if not email_id:
        return True, "no email id"
    if not adresy:
        return True, "forwarding off"
    if not (settings.resend_api_key and _nadawca()):
        return True, "no Resend sender"
    if _juz_byl(email_id):
        return True, "already forwarded"
    try:
        m, klucz = _pobierz(email_id)
    except RuntimeError as e:
        return False, str(e)
    zal = _zalaczniki(email_id, klucz)
    t = tresc(m, [a["filename"] for a in zal])
    od = m.get("from") or ""
    dane = {"from": _nadawca(), "to": adresy, **t}
    if parseaddr(od)[1]:
        dane["reply_to"] = od
    blad = None
    try:
        _post({**dane, "attachments": zal} if zal else dane)
    except RuntimeError as e:
        if not zal:
            blad = str(e)
        else:
            try:
                _post(dane)
            except RuntimeError as e2:
                blad = str(e2)
    _zapisz(email_id, m.get("subject") or "", ", ".join(adresy), blad)
    return (blad is None), (blad or "forwarded")
