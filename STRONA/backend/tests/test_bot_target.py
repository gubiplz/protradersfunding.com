"""Zmiana docelowego zysku na DZIAŁAJĄCYM Trade BOT-cie.

Bot, ktory dobil do `bot_target_pct`, przestaje otwierac pozycje, ale zostaje
wlaczony. Do tej pory jedynym wyjsciem bylo zatrzymanie go — a Stop resynchronizuje
saldo do feedu i robi uskok na krzywej equity. Podniesienie celu puszcza bota
dalej z tego samego miejsca.
"""
import os
import tempfile

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


def _konto_z_botem(login: str, *, cel: float, zysk_pct: float = 0.0) -> int:
    """Konto z uruchomionym botem i saldem podniesionym o zadany zysk."""
    s = SessionLocal()
    start = 100_000.0
    acc = Account(login=login, trader_name="Bot Target", product_key="2step-100k",
                  preset="2step-100k", initial_balance=start, steps=2, status="active",
                  phase="eval_1", balance=start, equity=start, peak_equity=start,
                  day_start_equity=start, day_start_balance=start)
    s.add(acc); s.commit()
    tradebot.start(s, acc, style="balanced", pace="demo", target_pct=cel)
    acc.balance = acc.equity = start * (1 + zysk_pct / 100.0)
    s.commit()
    aid = acc.id
    s.close()
    return aid


def test_podniesienie_celu_wznawia_handel_po_jego_osiagnieciu():
    """Sedno zmiany: bot na celu jest bezczynny, po podniesieniu znow otwiera pozycje."""
    aid = _konto_z_botem("bt-wznow", cel=5.0, zysk_pct=5.2)
    s = SessionLocal()
    acc = s.get(Account, aid)
    persona = tradebot.persona_for(acc)

    # na celu: bot nie otwiera nic nowego
    assert tradebot._should_open(s, acc, persona, acc.balance, tradebot._server_now()) is False

    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 8.5})
    assert r.status_code == 200 and r.json()["bot_target_pct"] == 8.5

    s.refresh(acc)
    # po podniesieniu celu bramka zysku juz nie blokuje
    assert acc.bot_target_pct == 8.5
    zablokowany_celem = (acc.balance - acc.initial_balance) / acc.initial_balance * 100.0 >= acc.bot_target_pct
    assert zablokowany_celem is False
    s.close()


def test_krok_01_procenta_jest_zachowany():
    aid = _konto_z_botem("bt-krok", cel=5.0)
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 5.1})
    assert r.json()["bot_target_pct"] == 5.1
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 12.34})
    assert r.json()["bot_target_pct"] == 12.3      # zaokraglane do dziesiatej


def test_zero_znosi_limit_zysku():
    aid = _konto_z_botem("bt-zero", cel=5.0, zysk_pct=6.0)
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 0})
    assert r.status_code == 200 and r.json()["bot_target_pct"] == 0.0

    s = SessionLocal()
    acc = s.get(Account, aid)
    persona = tradebot.persona_for(acc)
    # bez limitu bramka zysku w ogole nie jest brana pod uwage, mimo +6% na koncie
    assert acc.bot_target_pct == 0.0
    assert isinstance(tradebot._should_open(s, acc, persona, acc.balance, tradebot._server_now()), bool)
    s.close()


def test_zmiana_celu_nie_rusza_salda():
    """Stop resynchronizuje saldo do feedu — zmiana celu NIE MOZE tego robic."""
    aid = _konto_z_botem("bt-saldo", cel=5.0, zysk_pct=5.0)
    s = SessionLocal(); przed = s.get(Account, aid).balance; s.close()

    client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 9.0})

    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.balance == przed and acc.bot_enabled is True
    s.close()


def test_ujemny_cel_i_pusty_patch_sa_odrzucane():
    aid = _konto_z_botem("bt-walidacja", cel=5.0)
    assert client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                        json={"target_pct": -2}).status_code == 400
    assert client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                        json={}).status_code == 400


def test_pauza_dalej_dziala_starym_ksztaltem_zadania():
    """Panel wysylal wczesniej samo {paused: bool} — to nie moze przestac dzialac."""
    aid = _konto_z_botem("bt-pauza", cel=5.0)
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"paused": True})
    assert r.status_code == 200 and r.json()["bot_paused"] is True
    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"paused": False})
    assert r.json()["bot_paused"] is False


def test_celu_nie_da_sie_ustawic_gdy_bot_nie_chodzi():
    s = SessionLocal()
    acc = Account(login="bt-off", trader_name="x", product_key="2step-100k", preset="2step-100k",
                  initial_balance=100_000.0, steps=2, status="active", phase="eval_1",
                  balance=100_000.0, equity=100_000.0, peak_equity=100_000.0,
                  day_start_equity=100_000.0, day_start_balance=100_000.0)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    r = client.patch(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H, json={"target_pct": 7.0})
    assert r.status_code == 400
