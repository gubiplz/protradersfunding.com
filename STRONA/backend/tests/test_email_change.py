"""Zmiana e-maila w ustawieniach portalu: kod na STARY adres, dopiero potem zmiana.

Stary adres dowodzi, że zmianę robi właściciel konta, a nie ktoś z przejętą
sesją. Po zmianie oba adresy dostają powiadomienie.
"""
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
client = TestClient(app)
LICZNIK = iter(range(10_000))


@pytest.fixture
def maile(monkeypatch):
    wyslane = []
    monkeypatch.setattr(main_mod.notify, "send", lambda ev, to, ctx=None: wyslane.append((ev, to, ctx or {})))
    return wyslane


def _konto(haslo="haslo12345", **kw):
    n = next(LICZNIK)
    s = SessionLocal()
    tr = Trader(email=f"zmiana{n}@test.pl", password_hash=auth.hash_password(haslo),
                full_name="Zmiana Maila", referral_code=f"EMC{n:05d}", **kw)
    s.add(tr); s.commit(); tid, email = tr.id, tr.email; s.close()
    return tid, email, {"Authorization": f"Bearer {_tok(tid)}"}


def _tok(tid):
    s = SessionLocal(); tr = s.get(Trader, tid); t = auth.make_token(tid, tr.password_hash); s.close()
    return t


def _kod(maile):
    ev, to, ctx = [m for m in maile if m[0] == "email_change_code"][-1]
    return ctx["code"], to


def test_pelna_zmiana_adresu(maile):
    tid, stary, h = _konto()
    nowy = f"nowy{next(LICZNIK)}@example.com"
    r = client.post("/api/me/email/start", headers=h, json={"new_email": nowy.upper(), "password": "haslo12345"})
    assert r.status_code == 200 and r.json()["sent_to"] == stary
    kod, do_kogo = _kod(maile)
    assert do_kogo == stary, "kod idzie na OBECNY adres"
    assert re.fullmatch(r"\d{6}", kod)
    s = SessionLocal(); tr = s.get(Trader, tid)
    assert tr.email == stary and tr.pending_email == nowy and kod not in (tr.email_change_hash or "")
    s.close()

    zly = client.post("/api/me/email/confirm", headers=h, json={"code": "000000" if kod != "000000" else "111111"})
    assert zly.status_code == 400 and "attempt" in zly.json()["detail"]

    ok = client.post("/api/me/email/confirm", headers=h, json={"code": kod})
    assert ok.status_code == 200 and ok.json()["email"] == nowy
    zdarzenia = {(ev, to) for ev, to, _ in maile}
    assert ("email_changed_old", stary) in zdarzenia and ("email_changed_new", nowy) in zdarzenia
    # logowanie: nowy adres działa, stary już nie
    assert client.post("/api/auth/login", json={"email": nowy, "password": "haslo12345"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": stary, "password": "haslo12345"}).status_code == 401
    # sesja dalej ważna
    assert client.get("/api/auth/me", headers=h).json()["email"] == nowy


def test_zle_haslo_i_zajety_adres(maile):
    _, _, h = _konto()
    _, zajety, _ = _konto()
    assert client.post("/api/me/email/start", headers=h,
                       json={"new_email": "wolny@example.com", "password": "zle"}).status_code == 400
    r = client.post("/api/me/email/start", headers=h, json={"new_email": zajety, "password": "haslo12345"})
    assert r.status_code == 400 and "already used" in r.json()["detail"]
    r = client.post("/api/me/email/start", headers=h, json={"new_email": "to-nie-mail", "password": "haslo12345"})
    assert r.status_code == 400
    assert not [m for m in maile if m[0] == "email_change_code"]


def test_piec_zlych_kodow_kasuje_prosbe(maile):
    tid, _, h = _konto()
    client.post("/api/me/email/start", headers=h, json={"new_email": f"x{next(LICZNIK)}@example.com",
                                                        "password": "haslo12345"})
    kod, _ = _kod(maile)
    zly = "123456" if kod != "123456" else "654321"
    for _ in range(main_mod.EMAIL_CHANGE_PROBY):
        r = client.post("/api/me/email/confirm", headers=h, json={"code": zly})
    assert "Too many" in r.json()["detail"]
    # nawet dobry kod już nie przejdzie
    r = client.post("/api/me/email/confirm", headers=h, json={"code": kod})
    assert r.status_code == 400
    s = SessionLocal(); assert s.get(Trader, tid).pending_email is None; s.close()


def test_kod_wygasa(maile):
    tid, stary, h = _konto()
    client.post("/api/me/email/start", headers=h, json={"new_email": f"w{next(LICZNIK)}@example.com",
                                                        "password": "haslo12345"})
    kod, _ = _kod(maile)
    s = SessionLocal()
    s.get(Trader, tid).email_change_expires = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    s.commit(); s.close()
    r = client.post("/api/me/email/confirm", headers=h, json={"code": kod})
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    s = SessionLocal(); assert s.get(Trader, tid).email == stary; s.close()


def test_konto_google_musi_najpierw_ustawic_haslo(maile):
    _, _, h = _konto(google_sub=f"g-{next(LICZNIK)}", password_set=False)
    nowy = f"g{next(LICZNIK)}@example.com"
    r = client.post("/api/me/email/start", headers=h, json={"new_email": nowy})
    assert r.status_code == 400 and "Set a password first" in r.json()["detail"]
    assert not [m for m in maile if m[0] == "email_change_code"]
    # ustawia hasło w portalu (bez „obecnego") → zmiana maila już przechodzi
    r = client.post("/api/me/password", headers=h, json={"new_password": "google-haslo-9"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/me/email/start", headers=h, json={"new_email": nowy, "password": "google-haslo-9"})
    assert r.status_code == 200


def test_podglad_admina_nie_zmienia_maila(maile):
    tid, _, _ = _konto()
    s = SessionLocal(); tr = s.get(Trader, tid)
    imp = auth.make_impersonation_token(tid, tr.password_hash); s.close()
    r = client.post("/api/me/email/start", headers={"Authorization": f"Bearer {imp}"},
                    json={"new_email": "imp@example.com", "password": "haslo12345"})
    assert r.status_code == 403
