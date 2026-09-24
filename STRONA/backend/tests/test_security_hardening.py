"""Poprawki z audytu bezpieczeństwa (wrzesień 2026).

Każdy test pilnuje jednej granicy, którą audyt znalazł otwartą:
sesja admina po zmianie hasła, token podglądu jako klucz do panelu, podgląd
innego admina, NaN w kwocie wypłaty, push na adres wewnętrzny, plik KYC
z fałszywym typem, `javascript:` w adresie grafiki posta i e-mail z apostrofem
wklejany do inline-handlera w panelu.
"""
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, main as main_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10_000))
STATIC = Path(__file__).resolve().parents[1] / "static" / "js"


def _konto(is_admin=False, email=None) -> int:
    n = next(LICZNIK)
    s = SessionLocal()
    tr = Trader(email=email or f"sec{n}@test.pl", password_hash=auth.hash_password("haslo12345"),
                full_name="Sec Test", referral_code=f"SEC{n:05d}", is_admin=is_admin)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _bearer(tid: int) -> dict:
    s = SessionLocal()
    tr = s.get(Trader, tid); tok = auth.make_token(tid, tr.password_hash); s.close()
    return {"Authorization": f"Bearer {tok}"}


def test_sesja_admina_umiera_po_zmianie_hasla():
    aid = _konto(is_admin=True)
    h = _bearer(aid)
    assert client.get("/api/stats", headers=h).status_code == 200
    s = SessionLocal()
    s.get(Trader, aid).password_hash = auth.hash_password("nowe-haslo-999")
    s.commit(); s.close()
    assert client.get("/api/stats", headers=h).status_code == 403


def test_token_podgladu_nie_otwiera_panelu():
    aid = _konto(is_admin=True)
    s = SessionLocal()
    imp = auth.make_impersonation_token(aid, s.get(Trader, aid).password_hash); s.close()
    assert client.get("/api/stats", headers={"Authorization": f"Bearer {imp}"}).status_code == 403


def test_nie_da_sie_podejrzec_admina_jako_klienta():
    aid = _konto(is_admin=True)
    assert client.post(f"/api/admin/traders/{aid}/impersonate", headers=ADMIN).status_code == 400
    tid = _konto()
    assert client.post(f"/api/admin/traders/{tid}/impersonate", headers=ADMIN).status_code == 200


def test_nan_w_kwocie_wyplaty_to_422():
    tid = _konto()
    s = SessionLocal()
    acc = Account(login=f"88{next(LICZNIK):05d}", trader_id=tid, trader_name="Sec",
                  product_key="2step-50k", preset="2step-50k", initial_balance=50_000.0,
                  steps=2, phase="funded", status="funded", balance=55_000.0, equity=55_000.0,
                  peak_equity=55_000.0, day_start_equity=55_000.0, day_start_balance=55_000.0)
    s.add(acc); s.commit(); acc_id = acc.id; s.close()
    r = client.post(f"/api/accounts/{acc_id}/payout-request", headers=_bearer(tid),
                    content='{"method":"usdt","amount":NaN,"details":{"network":"TRC20","address":"T1"}}',
                    )
    assert r.status_code == 422


def test_push_na_adres_wewnetrzny_odrzucony(monkeypatch):
    monkeypatch.setattr(main_mod.push, "is_enabled", lambda: True)
    h = _bearer(_konto())
    klucze = {"p256dh": "x", "auth": "y"}
    for zly in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://10.0.0.5/x",
                "https://localhost/x", "https://metadata.internal/x", "javascript:alert(1)"):
        r = client.post("/api/me/push/subscribe", headers=h, json={"endpoint": zly, "keys": klucze})
        assert r.status_code == 400, zly
    ok = client.post("/api/me/push/subscribe", headers=h,
                     json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": klucze})
    assert ok.status_code == 200


def test_kyc_html_podpisany_jako_png_odrzucony():
    tid = _konto()
    s = SessionLocal()
    s.add(Account(login=f"89{next(LICZNIK):05d}", trader_id=tid, trader_name="Sec",
                  product_key="2step-50k", preset="2step-50k", initial_balance=50_000.0,
                  steps=2, phase="funded", status="funded", balance=50_000.0, equity=50_000.0,
                  peak_equity=50_000.0, day_start_equity=50_000.0, day_start_balance=50_000.0))
    s.commit(); s.close()
    h = _bearer(tid)
    r = client.post("/api/me/kyc/docs", headers=h,
                    files={"id_front": ("x.png", b"<html><script>alert(1)</script>", "image/png")})
    assert r.status_code == 400 and "real" in r.json()["detail"]


def test_media_url_posta_tylko_http():
    r = client.post("/api/admin/channel-posts", headers=ADMIN,
                    json={"kind": "photo", "body": "x", "media_url": "javascript:alert(1)"})
    assert r.status_code == 422
    r = client.post("/api/admin/channel-posts", headers=ADMIN,
                    json={"kind": "photo", "body": "x", "media_url": "data:text/html,<b>"})
    assert r.status_code == 422


def test_ostrzezenie_o_domyslnym_sekrecie(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "secret_key", "dev-secret-change-me")
    monkeypatch.setattr(main_mod.settings, "admin_token", "admin")
    w = client.get("/api/stats", headers={"X-Admin-Token": "admin"}).json()["security_warnings"]
    assert any("SECRET_KEY" in x for x in w) and any("ADMIN_TOKEN" in x for x in w)
    monkeypatch.setattr(main_mod.settings, "secret_key", "x" * 48)
    monkeypatch.setattr(main_mod.settings, "admin_token", "")
    assert main_mod.ostrzezenia_bezpieczenstwa() == []


def test_inline_handlery_nie_uzywaja_samego_esc():
    """esc() w onclick="fn('${esc(x)}')" nie chroni: parser HTML odwija &#39;
    do apostrofu PRZED JS-em. E-mail `x');fetch(...)('@a.co` przechodzi
    walidację rejestracji — w panelu kradł token admina. Tylko jsq()."""
    wzorzec = re.compile(r'''on[a-z]+="[^"]*'\$\{esc\(''')
    for plik in STATIC.glob("*.js"):
        znalezione = wzorzec.findall(plik.read_text(encoding="utf-8"))
        assert not znalezione, f"{plik.name}: {znalezione[:3]}"
