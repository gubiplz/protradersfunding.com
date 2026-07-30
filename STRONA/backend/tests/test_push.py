"""Web push + centrum powiadomień: subskrypcje, wpisy przy notify.send,
bramka preferencji (ta sama co dla maili), kasacja martwych subskrypcji
i dzienny recap (raz na dobę, cisza bez transakcji).
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, notify, push  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Notification, PushSubscription, Trade, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

LICZNIK = iter(range(1000))


def _trader(**pola):
    email = f"push{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Push Tester", referral_code=email[:8].upper(), **pola)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, email, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _wpisy(tid):
    s = SessionLocal()
    rows = (s.query(Notification).filter(Notification.trader_id == tid)
            .order_by(Notification.id).all())
    dane = [(n.event, n.title, n.url) for n in rows]
    s.close()
    return dane


def test_sw_i_manifest_sa_serwowane_z_korzenia():
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "showNotification" in r.text and "notificationclick" in r.text
    m = client.get("/manifest.json")
    assert m.status_code == 200 and m.json()["start_url"] == "/portal"
    assert m.json()["icons"], "manifest bez ikony = brak instalacji PWA na iOS"


def test_push_key_wylaczony_gdy_brak_kluczy():
    # conftest zeruje VAPID-y: portal ma wtedy w ogole nie proponowac pushy
    assert client.get("/api/push/key").json()["key"] is None


def test_subskrypcja_auth_idempotencja_i_kasowanie():
    tid, _, H = _trader()
    body = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "P", "auth": "A"}}
    assert client.post("/api/me/push-subscriptions", json=body).status_code in (401, 403)
    assert client.post("/api/me/push-subscriptions", headers=H, json=body).json()["ok"] is True
    assert client.post("/api/me/push-subscriptions", headers=H, json=body).json()["ok"] is True
    s = SessionLocal()
    assert s.query(PushSubscription).filter_by(endpoint="https://push.example/abc").count() == 1
    s.close()
    zle = client.post("/api/me/push-subscriptions", headers=H,
                      json={"endpoint": "http://niehttps", "keys": {"p256dh": "P", "auth": "A"}})
    assert zle.status_code == 400
    r = client.request("DELETE", "/api/me/push-subscriptions", headers=H,
                       json={"endpoint": "https://push.example/abc"})
    assert r.status_code == 200
    s = SessionLocal()
    assert s.query(PushSubscription).filter_by(endpoint="https://push.example/abc").count() == 0
    s.close()


def test_notify_send_zasila_centrum_powiadomien():
    tid, email, H = _trader()
    notify.send("phase_passed", email, {"name": "T", "login": "123",
                                        "from_phase": "Phase 1", "to_phase": "Phase 2"})
    r = client.get("/api/me/notifications", headers=H).json()
    assert r["unread"] == 1 and r["items"][0]["event"] == "phase_passed"
    assert r["items"][0]["url"] == "/portal?view=accounts"
    # tresc pusha NIE jest trescia maila (mail credentials ma haslo MT5)
    assert "Password" not in r["items"][0]["body"]
    client.post("/api/me/notifications/read", headers=H)
    assert client.get("/api/me/notifications", headers=H).json()["unread"] == 0


def test_password_reset_nie_trafia_do_centrum():
    tid, email, _ = _trader()
    notify.send("password_reset", email, {"name": "T", "reset_url": "https://x/y"})
    assert _wpisy(tid) == []


def test_preferencja_ucisza_wszystkie_kanaly_kategorii():
    tid, email, _ = _trader(notify_trading=False)
    notify.send("phase_passed", email, {"name": "T", "login": "1",
                                        "from_phase": "P1", "to_phase": "P2"})
    assert _wpisy(tid) == [], "wylaczone Trading Alerts maja uciszac takze push/centrum"
    notify.send("payout_approved", email, {"name": "T", "login": "1", "trader_share": "$100"})
    assert [w[0] for w in _wpisy(tid)] == ["payout_approved"]


def test_martwa_subskrypcja_leci_z_bazy_po_410(monkeypatch):
    tid, email, _ = _trader()
    s = SessionLocal()
    s.add(PushSubscription(trader_id=tid, endpoint="https://push.example/dead",
                           p256dh="P", auth="A"))
    s.commit(); s.close()
    monkeypatch.setattr(push.settings, "push_enabled", True)
    monkeypatch.setattr(push.settings, "vapid_public_key", "pub")
    monkeypatch.setattr(push.settings, "vapid_private_key", "priv")

    class Odpowiedz:
        status_code = 410

    class Zgon(Exception):
        response = Odpowiedz()

    def padnij(subscription_info, data):
        raise Zgon("gone")

    monkeypatch.setattr(push, "_webpush_send", padnij)
    push.deliver("phase_passed", email, "Phase passed")
    s = SessionLocal()
    assert s.query(PushSubscription).filter_by(endpoint="https://push.example/dead").count() == 0
    s.close()
    assert [w[0] for w in _wpisy(tid)] == ["phase_passed"], "wpis w centrum zostaje mimo padu pusha"


def _konto_z_trejdem(tid, *, pnl, login):
    wczoraj_poludnie = (datetime.now(timezone.utc).replace(tzinfo=None, hour=12, minute=0)
                        - timedelta(days=1))
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Push Tester",
                  platform_login=login, platform_password="x", platform_server="MQ-Demo",
                  product_key="2step-10k", initial_balance=10_000.0, balance=10_000.0 + pnl,
                  equity=10_000.0 + pnl, peak_equity=10_000.0, day_start_equity=10_000.0,
                  day_start_balance=10_000.0, status="funded", phase="funded")
    s.add(acc); s.flush()
    s.add(Trade(account_id=acc.id, symbol="XAUUSD", side="buy", lots=0.1,
                open_price=2400.0, close_price=2410.0, pnl=pnl, status="closed",
                opened_at=wczoraj_poludnie - timedelta(hours=1),
                closed_at=wczoraj_poludnie))
    s.commit(); s.close()


def test_daily_recap_raz_na_dobe_i_tylko_dla_handlujacych():
    tid_a, _, _ = _trader()
    tid_b, _, _ = _trader()                       # bez transakcji = cisza
    tid_c, _, _ = _trader(notify_marketing=False) # wylaczyl recap
    _konto_z_trejdem(tid_a, pnl=120.0, login="880001")
    _konto_z_trejdem(tid_c, pnl=50.0, login="880002")
    wynik = push.daily_recap()
    assert wynik["sent"] == 1
    wpisy = _wpisy(tid_a)
    assert len(wpisy) == 1 and wpisy[0][0] == "daily_recap"
    assert "+$120" in wpisy[0][1]
    assert wpisy[0][2] == "/portal?view=analytics"
    assert _wpisy(tid_b) == [] and _wpisy(tid_c) == []
    # guard: drugi przebieg tego samego dnia nic nie wysyla
    assert push.daily_recap()["sent"] == 0
