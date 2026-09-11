"""Moment, w którym dzienny limit straty wraca do pełna — `day_reset_at`.

Portal odlicza z tego pola „Daily limit resets in 6h 12m". Liczy to serwer,
bo dzień handlowy zmienia się o północy czasu SERWERA MT5, a nie w strefie
przeglądarki: trader w Dubaju i trader w Bogocie mają ten sam moment resetu
i obu trzeba pokazać tę samą godzinę.

Tu pilnujemy dokładnie jednego: żeby ta godzina zgadzała się z `day_key`,
którym poller rozpoznaje zmianę dnia. Rozjazd o godzinę oznaczałby, że portal
obiecuje reset, którego jeszcze nie ma — i pozwala wejść w pozycję na limicie.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, poller  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import _next_day_reset, app  # noqa: E402
from app.models import Account, Trader  # noqa: E402

init_db()
client = TestClient(app)


def test_reset_wypada_o_polnocy_czasu_serwera():
    reset = datetime.fromisoformat(_next_day_reset())
    assert reset.tzinfo is not None, "bez strefy przeglądarka doklei swoją"
    assert reset > datetime.now(timezone.utc)
    assert reset - datetime.now(timezone.utc) <= timedelta(days=1)
    # Ta sama chwila w czasie serwera to równa północ, czyli pierwszy moment
    # z NOWYM `day_key` — inaczej odliczanie kłamałoby o różnicę stref.
    srv = reset + timedelta(hours=get_settings().server_utc_offset_hours)
    assert (srv.hour, srv.minute, srv.second) == (0, 0, 0)
    assert poller.server_day_key(srv) != poller.server_day_key()


def test_konto_niesie_te_date_do_portalu():
    s = SessionLocal()
    tr = Trader(email="reset@example.com", password_hash=auth.hash_password("haslo1234"),
                full_name="Reset Tester", referral_code="RESET")
    s.add(tr); s.commit(); tid = tr.id
    acc = Account(login="reset-1", trader_id=tid, trader_name="Reset Tester",
                  platform_login="reset-1", platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0,
                  day_start_balance=25_000.0, status="active", phase="eval_1",
                  steps=2, max_daily_loss_pct=5.0, max_overall_loss_pct=10.0)
    s.add(acc); s.commit(); s.close()

    r = client.get("/api/me/accounts", headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    assert r.status_code == 200
    a = r.json()[0]
    assert datetime.fromisoformat(a["day_reset_at"]) > datetime.now(timezone.utc)
    # Portal liczy z tych trzech pól i budżet dnia, i bufor do zamknięcia konta.
    m = a["metrics"]
    assert m["daily_floor"] == 25_000.0 - 0.05 * 25_000.0
    assert m["overall_floor"] == 25_000.0 - 0.10 * 25_000.0
    assert a["equity"] - m["daily_floor"] == 0.05 * 25_000.0
