"""Admin -> Accounts: daty created/paid na liście + timeline historii konta
składany z istniejących wierszy (Order, Account, Breach, PayoutRequest).
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)

ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))


def _kupione_konto():
    email = f"hist{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="History Tester", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    res = billing.create_checkout(s, tr, "2step-25k", None)
    done = billing.mock_complete(s, res["order_id"], tid)
    s.close()
    return done["account_id"], res["order_id"]


def test_lista_kont_ma_daty_created_i_paid():
    aid, _ = _kupione_konto()
    lista = client.get("/api/accounts", headers=ADMIN).json()
    konto = next(a for a in lista if a["id"] == aid)
    assert konto["created_at"] and konto["paid_at"]


def test_history_zwraca_zamowienie_platnosc_i_start():
    aid, oid = _kupione_konto()
    r = client.get(f"/api/accounts/{aid}/history", headers=ADMIN)
    assert r.status_code == 200
    items = r.json()["items"]
    labels = [i["label"] for i in items]
    assert any(l.startswith(f"Order #{oid} created") for l in labels)
    assert any(l.startswith(f"Order #{oid} paid") for l in labels)
    assert "Account created" in labels
    # sortowanie malejąco po czasie
    tsy = [i["ts"] for i in items]
    assert tsy == sorted(tsy, reverse=True)
    # kinds z ustalonego słownika
    assert {i["kind"] for i in items} <= {"account", "order", "payment", "breach", "payout"}


def test_history_wymaga_admina_i_404_dla_obcych():
    aid, _ = _kupione_konto()
    assert client.get(f"/api/accounts/{aid}/history").status_code in (401, 403)
    assert client.get("/api/accounts/999999/history",
                      headers=ADMIN).status_code == 404


def test_lista_kont_idzie_od_najnowszego():
    """Panel czyta Accounts jak oś czasu — świeżo otwarte konto ma być na górze,
    a nie na końcu listy rosnącej po id."""
    aid1, _ = _kupione_konto()
    aid2, _ = _kupione_konto()
    lista = client.get("/api/accounts", headers=ADMIN).json()
    kolejnosc = [a["id"] for a in lista]
    assert kolejnosc.index(aid2) < kolejnosc.index(aid1)
    daty = [a["created_at"] for a in lista if a["created_at"]]
    assert daty == sorted(daty, reverse=True)
