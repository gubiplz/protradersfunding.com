"""Bot dowozi cel W ZADANYCH DNIACH — lustro doomu, tylko w górę.

Admin przy „Will pass" wybiera, ile dni ma zająć dojście do sufitu. Bot dzieli
dystans do capu przez dni kalendarzowe do terminu i codziennie przelicza porcję
od nowa — weekendy i czerwone dni same zagęszczają kolejne sesje, dokładnie jak
w zjeździe `bot_doom_deadline`.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import tradebot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}
START = 100_000.0


def _konto_z_botem(login: str, *, cel: float) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Paced", product_key="2step-100k",
                  preset="2step-100k", initial_balance=START, steps=2, status="active",
                  phase="eval_1", balance=START, equity=START, peak_equity=START,
                  day_start_equity=START, day_start_balance=START)
    s.add(acc); s.commit()
    tradebot.start(s, acc, style="balanced", pace="demo", target_pct=cel)
    aid = acc.id
    s.close()
    return aid


def test_dzienna_porcja_to_dystans_przez_dni_do_terminu():
    """Cel 4% w 2 dni => porcja dnia ~2000 (rozrzut 0.85–1.20). Przy days_left
    <= 2.0 czerwone dni odpadają, więc wynik jest deterministycznie dodatni."""
    aid = _konto_z_botem("pt-porcja", cel=4.0)
    s = SessionLocal()
    acc = s.get(Account, aid)
    now = datetime.now(timezone.utc)
    acc.bot_target_deadline = now + timedelta(days=2)
    s.commit()
    porcja = tradebot._day_target(acc, tradebot.persona_for(acc), acc.balance, now)
    s.close()
    # dystans 4000 / 2 dni = 2000, z rozrzutem [0.85, 1.20]
    assert 4000 / 2 * 0.85 <= porcja <= 4000 / 2 * 1.20


def test_termin_z_przeszlosci_nie_robi_dnia_z_kosmosu():
    """Zegar minął => days_left clampuje do 1.0, a porcję i tak ścina sufit
    wiarygodności TARGET_DAILY_MAX — żadnych +8% w jedną sesję."""
    aid = _konto_z_botem("pt-clamp", cel=8.0)
    s = SessionLocal()
    acc = s.get(Account, aid)
    now = datetime.now(timezone.utc)
    acc.bot_target_deadline = now - timedelta(days=3)
    s.commit()
    porcja = tradebot._day_target(acc, tradebot.persona_for(acc), acc.balance, now)
    s.close()
    assert porcja == acc.balance * tradebot.TARGET_DAILY_MAX / 100.0


def test_api_ustawia_zeruje_i_odrzuca_termin():
    aid = _konto_z_botem("pt-api", cel=5.0)
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 5.0, "target_days": 14})
    assert r.status_code == 200
    assert r.json()["bot_outcome"]["target_deadline"] is not None

    # 0 = jawne „bez terminu, wolne tempo persony"
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                     json={"target_pct": 5.0, "target_days": 0})
    assert r.status_code == 200
    assert r.json()["bot_outcome"]["target_deadline"] is None

    assert client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                        json={"target_pct": 5.0, "target_days": -1}).status_code == 400


def test_zdjecie_celu_zdejmuje_tez_zegar():
    aid = _konto_z_botem("pt-zdjecie", cel=5.0)
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                 json={"target_pct": 5.0, "target_days": 7})
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 0})
    s = SessionLocal()
    assert s.get(Account, aid).bot_target_deadline is None
    s.close()


def test_zmiana_celu_bez_dni_zostawia_stary_termin():
    """Panel śle sam nowy cel => zegar stoi, a dzienna porcja i tak przeliczy
    się od nowego dystansu."""
    aid = _konto_z_botem("pt-keep", cel=5.0)
    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                 json={"target_pct": 5.0, "target_days": 21})
    s = SessionLocal(); przed = s.get(Account, aid).bot_target_deadline; s.close()
    assert przed is not None

    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 7.0})
    s = SessionLocal()
    assert s.get(Account, aid).bot_target_deadline == przed
    s.close()
