"""Web push: klucz publiczny, subskrypcje, bramka preferencji, cron serii.

Jak w pozostałych plikach: pytest współdzieli moduły/bazę — unikalne e-maile
i endpointy, asercje odporne na cudze wiersze. Push włączamy per-test przez
monkeypatch na singletonie settings (lru_cache => ten sam obiekt wszędzie),
a `push._deliver` to szew testowy zamiast prawdziwego push service'u.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, notify, push  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PushSubscription, Trader  # noqa: E402

init_db()

ADMIN = {"X-Admin-Token": get_settings().admin_token}


def _trader(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Push Test", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _ustaw(tid, **pola):
    s = SessionLocal()
    tr = s.get(Trader, tid)
    for k, v in pola.items():
        setattr(tr, k, v)
    s.commit(); s.close()


def _enable(monkeypatch):
    monkeypatch.setattr(push.settings, "vapid_private_key", "test-priv")
    monkeypatch.setattr(push.settings, "vapid_public_key", "test-pub")


def _dodaj_sub(tid, endpoint):
    s = SessionLocal()
    s.add(PushSubscription(trader_id=tid, endpoint=endpoint, p256dh="pdh", auth="au"))
    s.commit(); s.close()


def _liczba_subow(endpoint):
    s = SessionLocal()
    n = s.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).count()
    s.close()
    return n


def _sub_json(endpoint):
    return {"endpoint": endpoint, "keys": {"p256dh": "pdh", "auth": "au"}}


# ---------------- public key ----------------
def test_public_key_domyslnie_wylaczony():
    with TestClient(app) as c:
        r = c.get("/api/push/public-key").json()
    assert r == {"enabled": False, "key": None}


def test_public_key_wlaczony_po_konfiguracji(monkeypatch):
    _enable(monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/push/public-key").json()
    assert r == {"enabled": True, "key": "test-pub"}


# ---------------- subscribe / unsubscribe ----------------
def test_subscribe_wymaga_logowania():
    with TestClient(app) as c:
        assert c.post("/api/me/push/subscribe",
                      json=_sub_json("https://p.example/e1")).status_code == 401


def test_subscribe_503_gdy_wylaczony():
    _, h = _trader("push-503@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/push/subscribe", headers=h,
                   json=_sub_json("https://p.example/e2"))
    assert r.status_code == 503


def test_subscribe_400_bez_kluczy(monkeypatch):
    _enable(monkeypatch)
    _, h = _trader("push-400@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/push/subscribe", headers=h,
                   json={"endpoint": "https://p.example/e3", "keys": {}})
    assert r.status_code == 400


def test_subscribe_zapisuje_i_upsert_po_endpoincie(monkeypatch):
    _enable(monkeypatch)
    tid1, h1 = _trader("push-sub1@test.pl")
    tid2, h2 = _trader("push-sub2@test.pl")
    ep = "https://p.example/e4"
    with TestClient(app) as c:
        assert c.post("/api/me/push/subscribe", headers=h1,
                      json=_sub_json(ep)).json() == {"ok": True}
        # ten sam endpoint od innego tradera => przepisanie, nie duplikat
        assert c.post("/api/me/push/subscribe", headers=h2,
                      json=_sub_json(ep)).json() == {"ok": True}
    assert _liczba_subow(ep) == 1
    s = SessionLocal()
    sub = s.query(PushSubscription).filter(PushSubscription.endpoint == ep).one()
    assert sub.trader_id == tid2
    s.close()


def test_unsubscribe_kasuje_wiersz(monkeypatch):
    _enable(monkeypatch)
    _, h = _trader("push-unsub@test.pl")
    ep = "https://p.example/e5"
    with TestClient(app) as c:
        c.post("/api/me/push/subscribe", headers=h, json=_sub_json(ep))
        assert _liczba_subow(ep) == 1
        assert c.post("/api/me/push/unsubscribe", headers=h,
                      json={"endpoint": ep}).json() == {"ok": True}
    assert _liczba_subow(ep) == 0


# ---------------- wysyłka ----------------
def test_send_to_trader_wylaczony_zwraca_zero():
    tid, _ = _trader("push-off@test.pl")
    _dodaj_sub(tid, "https://p.example/e6")
    assert push.send_to_trader(tid, "Tytuł") == 0


def test_send_to_trader_kasuje_martwa_subskrypcje(monkeypatch):
    _enable(monkeypatch)
    tid, _ = _trader("push-dead@test.pl")
    _dodaj_sub(tid, "https://p.example/zywy")
    _dodaj_sub(tid, "https://p.example/martwy")

    class _Resp:
        status_code = 410

    class _Gone(Exception):
        response = _Resp()

    dostarczone = []

    def fake_deliver(info, payload):
        if "martwy" in info["endpoint"]:
            raise _Gone()
        dostarczone.append(info["endpoint"])

    monkeypatch.setattr(push, "_deliver", fake_deliver)
    assert push.send_to_trader(tid, "Tytuł", "treść") == 1
    assert dostarczone == ["https://p.example/zywy"]
    assert _liczba_subow("https://p.example/martwy") == 0
    assert _liczba_subow("https://p.example/zywy") == 1


def test_send_event_bramka_preferencji(monkeypatch):
    _enable(monkeypatch)
    tid, _ = _trader("push-pref@test.pl")
    _dodaj_sub(tid, "https://p.example/e7")
    dostarczone = []
    monkeypatch.setattr(push, "_deliver", lambda i, p: dostarczone.append(p))

    _ustaw(tid, notify_payouts=False)
    assert push.send_event("payout_approved", "push-pref@test.pl", "Payout!") == 0
    _ustaw(tid, notify_payouts=True)
    assert push.send_event("payout_approved", "push-pref@test.pl", "Payout!") == 1
    assert "Payout!" in dostarczone[0]


def test_notify_send_wysyla_push_z_tematem_maila(monkeypatch):
    _enable(monkeypatch)
    tid, _ = _trader("push-notify@test.pl")
    _dodaj_sub(tid, "https://p.example/e8")
    dostarczone = []
    monkeypatch.setattr(push, "_deliver", lambda i, p: dostarczone.append(p))

    notify.send("welcome", "push-notify@test.pl", {"name": "Push"})
    assert len(dostarczone) == 1
    assert "Welcome to" in dostarczone[0]

    # wyłączona kategoria blokuje mail i push jednym przełącznikiem
    _ustaw(tid, notify_updates=False)
    notify.send("credits_granted", "push-notify@test.pl", {"amount": 10, "balance": 10})
    assert len(dostarczone) == 1
    # ...ale zdarzenia TRANSAKCYJNE (welcome, credentials) nie podlegają
    # preferencjom i dochodzą zawsze
    notify.send("welcome", "push-notify@test.pl", {"name": "Push"})
    assert len(dostarczone) == 2


# ---------------- cron: przypomnienie o serii ----------------
def _wczoraj():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _dzis():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_cron_streak_wymaga_autoryzacji():
    with TestClient(app) as c:
        assert c.get("/api/cron/streak-reminder").status_code == 401


def test_cron_streak_wylaczony():
    with TestClient(app) as c:
        r = c.get("/api/cron/streak-reminder", headers=ADMIN).json()
    assert r == {"sent": 0, "push": "disabled"}


def test_cron_streak_wybiera_tylko_zagrozone_serie(monkeypatch):
    _enable(monkeypatch)
    # A: kwalifikuje się (seria 5, check-in wczoraj, ma subskrypcję)
    tid_a, _ = _trader("streak-a@test.pl")
    _ustaw(tid_a, checkin_streak=5, checkin_last=_wczoraj())
    _dodaj_sub(tid_a, "https://p.example/sa")
    # B: check-in już dziś — nie ma czego przypominać
    tid_b, _ = _trader("streak-b@test.pl")
    _ustaw(tid_b, checkin_streak=7, checkin_last=_dzis())
    _dodaj_sub(tid_b, "https://p.example/sb")
    # C: seria za krótka (< 3)
    tid_c, _ = _trader("streak-c@test.pl")
    _ustaw(tid_c, checkin_streak=2, checkin_last=_wczoraj())
    _dodaj_sub(tid_c, "https://p.example/sc")
    # D: brak subskrypcji push
    tid_d, _ = _trader("streak-d@test.pl")
    _ustaw(tid_d, checkin_streak=9, checkin_last=_wczoraj())
    # E: wyłączone powiadomienia "updates"
    tid_e, _ = _trader("streak-e@test.pl")
    _ustaw(tid_e, checkin_streak=4, checkin_last=_wczoraj(), notify_updates=False)
    _dodaj_sub(tid_e, "https://p.example/se")

    dostarczone = []
    monkeypatch.setattr(push, "_deliver", lambda i, p: dostarczone.append((i["endpoint"], p)))
    with TestClient(app) as c:
        r = c.get("/api/cron/streak-reminder", headers=ADMIN).json()
    assert r == {"sent": 1, "eligible": 1}
    assert dostarczone[0][0] == "https://p.example/sa"
    assert "5-day streak" in dostarczone[0][1]
