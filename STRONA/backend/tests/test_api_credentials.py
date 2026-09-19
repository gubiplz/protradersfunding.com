"""Kto może zobaczyć hasło do konta MT5.

Regresja: `/api/accounts` był PUBLICZNY i zwracał `platform_password` wszystkich
traderów. Hasło główne daje pełną kontrolę nad rachunkiem, więc wychodzi tylko
do właściciela konta i do admina.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ["FEED"] = "sim"
os.environ["AUTO_SEED"] = "false"
os.environ["ADMIN_TOKEN"] = "tajny-token"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

HASLO_MT5 = "TajneHaslo123"


def _trader_z_kontem(email: str):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Jan Testowy", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    acc = Account(login="777001", trader_id=tr.id, trader_name="Jan Testowy",
                  platform_login="777001", platform_password=HASLO_MT5,
                  platform_server="MetaQuotes-Demo", platform_investor_password="Inv123",
                  product_key="2step-10k", initial_balance=10_000, balance=10_000, equity=10_000,
                  peak_equity=10_000, day_start_equity=10_000, day_start_balance=10_000,
                  status="active", phase="eval_1")
    s.add(acc); s.commit()
    # Płacący klient — domyślna lista /api/accounts pokazuje tylko takich
    # (test_podzial_placacy_free), a te testy pytają o widoczność haseł, nie
    # o segmentację.
    s.add(Order(trader_id=tr.id, product_key="2step-10k", amount_usd=99.0,
                status="paid", provider="manual"))
    s.commit()
    tid, aid = tr.id, acc.id
    s.close()
    return tid, aid, auth.make_token(tid)


def test_lista_wszystkich_kont_nie_jest_publiczna():
    _trader_z_kontem("public@test.pl")
    with TestClient(app) as c:
        r = c.get("/api/accounts")
    assert r.status_code in (401, 403), "listing kont bez autoryzacji to wyciek poświadczeń"
    assert HASLO_MT5 not in r.text


def test_pojedyncze_konto_tez_wymaga_admina():
    _, aid, _ = _trader_z_kontem("single@test.pl")
    with TestClient(app) as c:
        r = c.get(f"/api/accounts/{aid}")
    assert r.status_code in (401, 403)
    assert HASLO_MT5 not in r.text


def test_admin_widzi_poswiadczenia():
    _trader_z_kontem("adm@test.pl")
    with TestClient(app) as c:
        r = c.get("/api/accounts", headers={"X-Admin-Token": "tajny-token"})
    assert r.status_code == 200
    assert any(a.get("platform_password") == HASLO_MT5 for a in r.json())


def test_trader_widzi_komplet_danych_do_logowania_na_swoim_koncie():
    _, _, token = _trader_z_kontem("wlasciciel@test.pl")
    with TestClient(app) as c:
        r = c.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    acc = r.json()[0]
    assert acc["platform_server"] == "MetaQuotes-Demo"
    assert acc["platform_login"] == "777001"
    assert acc["platform_password"] == HASLO_MT5
    # hasła inwestora nie generujemy ani nie pokazujemy — nawet gdy stare konto je ma
    assert "platform_investor_password" not in acc


def test_trader_nie_widzi_kont_innego_tradera():
    _trader_z_kontem("ofiara@test.pl")
    _, _, token_obcy = _trader_z_kontem("obcy@test.pl")
    with TestClient(app) as c:
        r = c.get("/api/me/accounts", headers={"Authorization": f"Bearer {token_obcy}"})
    assert r.status_code == 200
    assert len(r.json()) == 1, "widać tylko własne konta"
