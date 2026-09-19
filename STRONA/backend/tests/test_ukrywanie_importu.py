"""Wiersze `…@imported.local` znikają z list, po których szuka się LUDZI.

Import ewidencji wypłat (`payout_import`) wymyśla adres technicznego tradera dla
każdego wiersza CSV bez maila. Za takim wierszem nie stoi klient, który się
zapisał — stoi przelew sprzed wdrożenia panelu. Na Accounts i Activity topiły
prawdziwe konta, więc domyślnie ich nie ma, a `?imported=1` je przywraca.

Czego te testy pilnują poza samym filtrem:

* że KSIĘGA PIENIĘDZY zostaje kompletna — wypłaty widać zawsze i da się do nich
  wystawić certyfikat, bo po to import w ogóle powstał,
* że konta z puli (bez właściciela, `trader_id IS NULL`) NIE wypadają razem
  z importem — w SQL-u `NOT IN (…)` daje dla NULL-a NULL, więc naiwny filtr
  zabrałby całą pulę i wyglądałby przy tym na działający.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Order, Payout, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

IMPORTOWY = "lauren.lockwood@imported.local"
PRAWDZIWY = "prawdziwa.klientka@gmail.com"


@pytest.fixture(scope="module", autouse=True)
def dane():
    """Trzy wiersze obok siebie: import, prawdziwy klient i konto z puli."""
    s = SessionLocal()
    for email, login in ((IMPORTOWY, "IMP70001"), (PRAWDZIWY, "REAL70001")):
        tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                    full_name="Lauren Lockwood" if email == IMPORTOWY else "Prawdziwa Klientka",
                    referral_code=auth.secrets.token_hex(3), kyc_status="approved")
        s.add(tr); s.commit()
        s.add(Account(login=login, trader_id=tr.id, trader_name=tr.full_name,
                      product_key="2step-50k", initial_balance=50_000, steps=2,
                      status="funded", phase="funded", balance=50_000, equity=50_000,
                      peak_equity=50_000, day_start_equity=50_000, day_start_balance=50_000))
        s.commit()
        if email == IMPORTOWY:
            acc = s.query(Account).filter(Account.login == login).one()
            s.add(Payout(account_id=acc.id, profit_amount=3100, trader_share=2480, paid=True))
            s.commit()
        else:
            # „Prawdziwa klientka" znaczy: ZAPŁACIŁA. Odkąd panel domyślnie
            # chowa darmowe signupy (test_podzial_placacy_free), sama
            # rejestracja nie wystarcza, żeby być na listach ludzi.
            s.add(Order(trader_id=tr.id, product_key="2step-50k",
                        amount_usd=299.0, status="paid", provider="manual"))
            s.commit()
    # Konto z puli: nikt go jeszcze nie odebrał, więc nie ma właściciela.
    s.add(Account(login="POOL70001", trader_id=None, trader_name="",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  status="provisioning", phase="eval_1", balance=50_000, equity=50_000,
                  peak_equity=50_000, day_start_equity=50_000, day_start_balance=50_000))
    s.commit(); s.close()


def _loginy(imported=""):
    return [a["login"] for a in client.get("/api/accounts" + imported, headers=ADMIN).json()]


def _maile(sciezka, klucz=None, imported=""):
    d = client.get(sciezka + imported, headers=ADMIN).json()
    if klucz:
        d = d[klucz]
    return [w["email"] for w in d]


def test_listy_ludzi_nie_pokazuja_wierszy_z_importu():
    assert IMPORTOWY not in _maile("/api/admin/traders")
    assert PRAWDZIWY in _maile("/api/admin/traders")

    assert IMPORTOWY not in _maile("/api/admin/journal", "items")
    assert PRAWDZIWY in _maile("/api/admin/journal", "items")

    assert IMPORTOWY not in _maile("/api/admin/kyc", "history")
    assert PRAWDZIWY in _maile("/api/admin/kyc", "history")

    konta = client.get("/api/accounts", headers=ADMIN).json()
    assert IMPORTOWY not in [a["trader_email"] for a in konta]
    assert "IMP70001" not in _loginy() and "REAL70001" in _loginy()


def test_przelacznik_przywraca_komplet():
    """Nic nie kasujemy — «Show imported» ma oddać dokładnie te same wiersze."""
    assert IMPORTOWY in _maile("/api/admin/traders", imported="?imported=1")
    assert IMPORTOWY in _maile("/api/admin/journal", "items", "?imported=1")
    assert IMPORTOWY in _maile("/api/admin/kyc", "history", "?imported=1")
    assert "IMP70001" in _loginy("?imported=1")


def test_konto_z_puli_przezywa_filtr():
    """`Account.trader_id` jest nullowalny, a `NOT IN (…)` daje dla NULL-a NULL.
    Bez jawnej gałęzi na NULL włączenie filtru zabierałoby z listy całą pulę —
    awaria, która na pierwszy rzut oka wygląda jak działający filtr."""
    assert "POOL70001" in _loginy()
    assert "POOL70001" in _loginy("?imported=1")


def test_ksiega_pieniedzy_zostaje_kompletna():
    """Payouts to jedyna droga do wystawienia certyfikatu importowanej wypłaty.
    Gdyby filtr sięgnął i tutaj, import straciłby sens."""
    lista = client.get("/api/admin/payouts", headers=ADMIN).json()
    moje = [x for x in lista if x["trader_email"] == IMPORTOWY]
    assert len(moje) == 1 and moje[0]["trader_share"] == 2480

    r = client.post(f"/api/admin/payouts/{moje[0]['id']}/certificate", headers=ADMIN)
    assert r.status_code == 200 and r.json()["cert_url"]


def test_importowane_konto_nie_wchodzi_na_leaderboard():
    """Import ustawia `balance == initial_balance`, więc zysk wychodzi zerowy
    i ranking odrzuca wiersz arytmetyką, bez osobnego filtru. Test przybija ten
    zbieg okoliczności, żeby zmiana importu nie wpuściła tam archiwum po cichu."""
    nazwiska = [w["trader"] for w in client.get("/api/leaderboard").json()]
    assert not any("Lockwood" in n for n in nazwiska)
