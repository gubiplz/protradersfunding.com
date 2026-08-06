"""Centrum powiadomień (dzwonek w portalu) + dzienny recap.

Wpis w centrum powstaje przy każdym notify.send (poza password_reset) TAKŻE
przy wyłączonym pushu — dzwonek działa bez zgody przeglądarki. Recap: raz na
dobę, cisza bez transakcji, kategoria notify_marketing.
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
from app.models import Account, Notification, Trade, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

LICZNIK = iter(range(1000))


def _trader(**pola):
    email = f"center{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Center Tester", referral_code=email[:10].upper(), **pola)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, email, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _wpisy(tid):
    s = SessionLocal()
    rows = (s.query(Notification).filter(Notification.trader_id == tid)
            .order_by(Notification.id).all())
    dane = [(n.event, n.title, n.url) for n in rows]
    s.close()
    return dane


def test_notify_send_zasila_centrum_takze_bez_pusha():
    """Push wyłączony (conftest zeruje VAPID) — wpis w centrum i tak powstaje."""
    tid, email, H = _trader()
    notify.send("phase_passed", email, {"name": "T", "login": "123",
                                        "from_phase": "Phase 1", "to_phase": "Phase 2"})
    r = client.get("/api/me/notifications", headers=H).json()
    assert r["unread"] == 1 and r["items"][0]["event"] == "phase_passed"
    assert r["items"][0]["url"] == "/portal?view=accounts"
    # tresc wpisu pochodzi z push._BODY, nie z maila (mail credentials ma haslo)
    assert "Password" not in (r["items"][0]["body"] or "")
    client.post("/api/me/notifications/read", headers=H)
    assert client.get("/api/me/notifications", headers=H).json()["unread"] == 0


def test_notifications_wymagaja_logowania():
    assert client.get("/api/me/notifications").status_code == 401


def test_password_reset_nie_trafia_do_centrum():
    tid, email, _ = _trader()
    notify.send("password_reset", email, {"name": "T", "reset_url": "https://x/y"})
    assert _wpisy(tid) == []


def test_preferencja_ucisza_takze_wpis_w_centrum():
    tid, email, _ = _trader(notify_trading=False)
    notify.send("phase_passed", email, {"name": "T", "login": "1",
                                        "from_phase": "P1", "to_phase": "P2"})
    assert _wpisy(tid) == []
    notify.send("payout_approved", email, {"name": "T", "login": "1", "trader_share": "$100"})
    assert [w[0] for w in _wpisy(tid)] == ["payout_approved"]


def _konto_z_trejdem(tid, *, pnl, login, closed_at=None):
    wczoraj_poludnie = closed_at or (
        datetime.now(timezone.utc).replace(tzinfo=None, hour=12, minute=0)
        - timedelta(days=1))
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Center Tester",
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
    tid_c, _, _ = _trader(notify_marketing=False)  # wylaczyl recap
    _konto_z_trejdem(tid_a, pnl=120.0, login="880001")
    _konto_z_trejdem(tid_c, pnl=50.0, login="880002")
    # Stale `now` w poludnie UTC: ta sama doba co realny zegar (guard i okno
    # "wczoraj" bez zmian), ale test nie zalezy od pory odpalenia suity.
    poludnie = datetime.now(timezone.utc).replace(hour=12, minute=0,
                                                  second=0, microsecond=0)
    wynik = push.daily_recap(now=poludnie)
    assert wynik["sent"] == 1
    wpisy = _wpisy(tid_a)
    assert len(wpisy) == 1 and wpisy[0][0] == "daily_recap"
    assert "+$120" in wpisy[0][1]
    assert wpisy[0][2] == "/portal?view=analytics"
    assert _wpisy(tid_b) == [] and _wpisy(tid_c) == []
    # guard: drugi przebieg tego samego dnia nic nie wysyla
    assert push.daily_recap(now=poludnie)["sent"] == 0


def test_recap_czeka_do_szostej_rano_polskiego_czasu():
    tid, _, _ = _trader()
    dzis_poludnie = datetime.now(timezone.utc).replace(tzinfo=None, hour=12,
                                                       minute=0, second=0,
                                                       microsecond=0)
    _konto_z_trejdem(tid, pnl=75.0, login="880003", closed_at=dzis_poludnie)
    # Jutro 02:30 UTC = 03:30 (CET) albo 04:30 (CEST) w Warszawie — za
    # wczesnie. Nocne sprawdzenie NIE moze zuzyc dziennego guardu.
    noc = (datetime.now(timezone.utc).replace(hour=2, minute=30, second=0,
                                              microsecond=0)
           + timedelta(days=1))
    wynik = push.daily_recap(now=noc)
    assert wynik == {"sent": 0, "skipped": "before 06:00 Europe/Warsaw"}
    assert _wpisy(tid) == []
    # Jutro w poludnie UTC (dawno po 06:00 w Warszawie) recap wychodzi, a jego
    # okno "wczoraj" obejmuje dzisiejszy trejd. Guard jutrzejszej doby nie
    # koliduje z testem wyzej, ktory zuzywa dzisiejsza.
    rano = noc.replace(hour=12)
    assert push.daily_recap(now=rano)["sent"] >= 1
    assert [w[0] for w in _wpisy(tid)] == ["daily_recap"]


def test_ruch_na_stronie_wyzwala_recap(monkeypatch):
    """Okablowanie middleware: request na sciezce lazy-ticku ma wolac
    push.daily_recap, gdy RECAP_ON_TRAFFIC wlaczone (w testach domyslnie nie
    jest). Godzine i guard testuja przypadki wyzej, wiec wystarczy stub —
    bez niego test zalezalby od pory odpalenia suity."""
    wywolania = []
    monkeypatch.setattr(push, "daily_recap",
                        lambda now=None: wywolania.append(1) or {"sent": 0})
    assert client.get("/api/public/stats").status_code == 200
    assert wywolania == []  # flaga zgaszona w conftest = zero wywolan
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "recap_on_traffic", True)
    assert client.get("/api/public/stats").status_code == 200
    assert wywolania == [1]
