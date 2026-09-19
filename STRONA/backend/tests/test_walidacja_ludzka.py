"""Błąd walidacji ma być zdaniem dla człowieka, a przecinek w kwocie — kwotą.

Pydantic oddaje 422 jako LISTĘ słowników, a portal wyświetla `detail` wprost —
klient na ostatnim kroku wypłaty widział „[object Object]". Do tego europejska
klawiatura numeryczna na iPhonie podpowiada przecinek: „123,45" wpisane w kwotę
wypłaty wywalało się na float_parsing, choć intencja jest oczywista.

Te testy przybijają kontrakt: `detail` z 422 to zawsze JEDEN string (frontend
może go pokazać bez parsowania), a kwota z przecinkiem przechodzi walidację.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import PayoutReqIn, app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
client = TestClient(app)


def _token() -> str:
    s = SessionLocal()
    tr = Trader(email="walidacja422@test.pl", password_hash=auth.hash_password("haslo12345"),
                full_name="Walidacja Tester", referral_code="WALID422")
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return auth.make_token(tid)


def test_422_to_jedno_zdanie_a_nie_lista():
    r = client.post("/api/accounts/1/payout-request", json={"amount": "sto"},
                    headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str), "frontend pokaże listę słowników jako [object Object]"
    # Zdanie ma wskazywać pole po ludzku — bez technicznego «body» ze ścieżki loc.
    assert detail.startswith("Amount: ")
    assert "body" not in detail


def test_przecinek_w_kwocie_to_kropka():
    assert PayoutReqIn(amount="123,45").amount == 123.45
    assert PayoutReqIn(amount=" 99,9 ").amount == 99.9
    assert PayoutReqIn(amount=250).amount == 250.0
    assert PayoutReqIn().amount is None       # brak kwoty = cała dostępna działka
