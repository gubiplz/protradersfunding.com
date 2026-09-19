"""Zdrowie kanałów Telegrama w panelu i ponowna publikacja istniejącej wypłaty.

Oba endpointy powstały po zamrożeniu konta we wrześniu 2026: bot stracił
uprawnienia we wszystkich kanałach naraz, a objawem była cisza — nic nie
zgłaszało błędu, posty po prostu nie wychodziły.
"""
import os
import secrets
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import payoutbot, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Payout  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}


# --------------------------------------------------------------------------- #
#  Status kanałów                                                              #
# --------------------------------------------------------------------------- #
def test_status_wymaga_admina():
    assert client.get("/api/admin/channels/status").status_code == 403


def test_status_wylicza_wszystkie_kanaly():
    r = client.get("/api/admin/channels/status", headers=ADMIN)
    assert r.status_code == 200
    klucze = [k["key"] for k in r.json()]
    assert klucze == ["payouts", "mgmt", "trackrecord", "leads", "leads_ng"]


def test_bez_tokenu_kanal_jest_nieskonfigurowany():
    """Testy mają puste tokeny (conftest), więc nic nie wychodzi do sieci —
    i właśnie to ma być widać wprost, zamiast udawanego „w porządku"."""
    for kanal in client.get("/api/admin/channels/status", headers=ADMIN).json():
        assert kanal["configured"] is False
        assert kanal["is_admin"] is False


def test_status_nie_wywraca_sie_gdy_telegram_pada(monkeypatch):
    """Sprawdzenie zdrowia, które samo się wywraca, jest gorsze niż jego brak:
    panel przestałby się otwierać razem z Telegramem."""
    def boom(*a, **k):
        raise RuntimeError("sieć padła")
    monkeypatch.setattr(telegram, "_get", boom)
    u = get_settings()
    monkeypatch.setattr(u, "telegram_bot_token", "TOKEN", raising=False)
    monkeypatch.setattr(u, "telegram_chat_id", "@kanal", raising=False)
    # get_me łapie wyjątki w środku, więc endpoint ma oddać 200 z pustym statusem.
    r = client.get("/api/admin/channels/status", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()[0]["member_status"] == ""


# --------------------------------------------------------------------------- #
#  Ponowna publikacja wypłaty                                                  #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def wyplata():
    s = SessionLocal()
    try:
        acc = Account(login=f"7{secrets.randbelow(10**6):06d}", trader_name="Anna Nowak",
                      initial_balance=100_000.0, balance=100_000.0,
                      equity=100_000.0, phase="funded", status="funded")
        s.add(acc)
        s.flush()
        p = Payout(account_id=acc.id, profit_amount=5000.0, trader_share=4000.0,
                   paid=True, cert_token=secrets.token_urlsafe(12)[:16])
        s.add(p)
        s.commit()
        yield p.id
    finally:
        s.close()


def test_publikacja_wymaga_admina(wyplata):
    assert client.post(f"/api/admin/payouts/{wyplata}/post").status_code == 403


def test_nieistniejaca_wyplata_to_404():
    assert client.post("/api/admin/payouts/999999/post", headers=ADMIN).status_code == 404


def test_bez_certyfikatu_nie_ma_czego_publikowac():
    """Post to zrzut strony certyfikatu — bez niego nie ma z czego zrobić grafiki."""
    s = SessionLocal()
    try:
        acc = Account(login=f"6{secrets.randbelow(10**6):06d}", initial_balance=100_000.0,
                      status="funded")
        s.add(acc)
        s.flush()
        p = Payout(account_id=acc.id, profit_amount=1000.0, trader_share=800.0,
                   paid=True, cert_token=None)
        s.add(p)
        s.commit()
        pid = p.id
    finally:
        s.close()
    r = client.post(f"/api/admin/payouts/{pid}/post", headers=ADMIN)
    assert r.status_code == 400
    assert "certificate" in r.json()["detail"]


def test_odmowa_telegrama_wraca_z_powodem(monkeypatch, wyplata):
    """Admin ma zobaczyć „bot is not a member", a nie samo „nie poszło"."""
    monkeypatch.setattr(payoutbot, "opublikuj",
                        lambda *a, **k: {"posted": False,
                                         "reason": "bot is not a member of the channel chat"})
    r = client.post(f"/api/admin/payouts/{wyplata}/post", headers=ADMIN)
    assert r.status_code == 502
    assert "not a member" in r.json()["detail"]


def test_udana_publikacja_oddaje_link(monkeypatch, wyplata):
    monkeypatch.setattr(payoutbot, "opublikuj",
                        lambda *a, **k: {"posted": True, "photo": True,
                                         "post_url": "https://t.me/fx_passingpayouts/99"})
    r = client.post(f"/api/admin/payouts/{wyplata}/post", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["post_url"].endswith("/99")


def test_publikuje_istniejaca_wyplate_a_nie_tworzy_nowej(monkeypatch, wyplata):
    """Cała różnica wobec `/payout-engine/run`, które ZAWSZE tworzy nową wypłatę."""
    widziane = {}
    monkeypatch.setattr(payoutbot, "opublikuj",
                        lambda p, nazwa, **k: (widziane.update(id=p.id, nazwa=nazwa),
                                               {"posted": True})[1])
    s = SessionLocal()
    try:
        przed = s.query(Payout).count()
    finally:
        s.close()

    assert client.post(f"/api/admin/payouts/{wyplata}/post", headers=ADMIN).status_code == 200
    assert widziane["id"] == wyplata
    assert widziane["nazwa"] == "Anna Nowak"

    s = SessionLocal()
    try:
        assert s.query(Payout).count() == przed, "publikacja nie może tworzyć wypłat"
    finally:
        s.close()
