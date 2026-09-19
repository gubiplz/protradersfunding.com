"""Panel i statystyki patrzą na PŁACĄCYCH; free signupy mają własną zakładkę.

Rejestracja w portalu jest darmowa, więc listy kont i kafelki Overview rosły od
ludzi, którzy nigdy nic nie kupili — a pytanie admina brzmi „jak idzie biznes",
nie „ilu ciekawskich założyło konto". Podział: płacący = ma opłacone zamówienie
za >$0, które nie jest grantem. Grant (BOGO, prezent od admina, import
archiwalnej wypłaty) nie robi z nikogo klienta.

Czego te testy pilnują poza samym filtrem:

* że konta z puli (`trader_id IS NULL`) NIE wypadają z widoku domyślnego —
  `IN (…)` w SQL-u odrzuca NULL-e po cichu, więc naiwny filtr zabrałby
  cały magazyn i wyglądałby przy tym na działający,
* że `provisioning` w /api/stats zostaje GLOBALNY — to kolejka operacyjna:
  konto free signupa czekające na login MT5 też trzeba obsłużyć,
* że `?imported=1` dalej znaczy „pokaż WSZYSTKO" (stara semantyka).
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
from app.models import Account, Order, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

PLACACY = "klient.placacy@gmail.com"
DARMOWY = "ciekawski.bez.zakupu@gmail.com"
GRANTOWY = "obdarowany.grantem@gmail.com"


def _konto(s, login, trader_id, status="active"):
    s.add(Account(login=login, trader_id=trader_id, trader_name="",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  status=status, phase="eval_1", balance=50_000, equity=50_000,
                  peak_equity=50_000, day_start_equity=50_000, day_start_balance=50_000))
    s.commit()


@pytest.fixture(scope="module", autouse=True)
def dane():
    """Czterech sąsiadów: płacący, darmowy, grantowy i konto z puli."""
    s = SessionLocal()
    ids = {}
    for email in (PLACACY, DARMOWY, GRANTOWY):
        tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                    full_name=email.split("@")[0], referral_code=auth.secrets.token_hex(3))
        s.add(tr); s.commit()
        ids[email] = tr.id
    # Płacący: ręcznie zaksięgowane zamówienie (kanał płatności bez znaczenia).
    s.add(Order(trader_id=ids[PLACACY], product_key="2step-50k",
                amount_usd=439.2, status="paid", provider="manual"))
    # Grantowy: dostał konto za darmo — grant NIE robi z niego klienta.
    s.add(Order(trader_id=ids[GRANTOWY], product_key="2step-50k",
                amount_usd=0.0, status="paid", provider="grant"))
    s.commit()
    _konto(s, "PAY80001", ids[PLACACY])
    _konto(s, "FREE80001", ids[DARMOWY], status="provisioning")
    _konto(s, "GRANT80001", ids[GRANTOWY])
    _konto(s, "POOL80001", None, status="provisioning")
    s.close()


def _loginy(q=""):
    return [a["login"] for a in client.get("/api/accounts" + q, headers=ADMIN).json()]


def test_widok_domyslny_to_placacy_plus_pula():
    loginy = _loginy()
    assert "PAY80001" in loginy
    assert "POOL80001" in loginy          # magazyn, nie signup — patrz docstring
    assert "FREE80001" not in loginy
    assert "GRANT80001" not in loginy     # grant to prezent, nie sprzedaż


def test_zakladka_free_pokazuje_reszte_bez_placacych_i_puli():
    # Bez asercji o KOMPLETNEJ liście: moduły testów współdzielą bazę
    # (DATABASE_URL ustawia pierwszy zaimportowany), więc obok żyją konta
    # z innych plików. Pilnujemy członkostwa, nie kompletu.
    loginy = _loginy("?free=1")
    assert "FREE80001" in loginy
    assert "GRANT80001" in loginy
    assert "PAY80001" not in loginy
    assert "POOL80001" not in loginy


def test_imported_1_dalej_znaczy_pokaz_wszystko():
    """Przełącznik «Show imported» od zawsze obiecuje komplet wierszy —
    nowy filtr nie może po cichu zawęzić tej obietnicy."""
    loginy = _loginy("?imported=1")
    for login in ("PAY80001", "FREE80001", "GRANT80001", "POOL80001"):
        assert login in loginy


def test_platnosc_przenosi_czlowieka_miedzy_licznikami():
    """Delty zamiast liczb bezwzględnych — patrz komentarz o współdzielonej
    bazie wyżej. Płacimy za DARMOWEGO i patrzymy, co drgnęło."""
    def stats():
        return client.get("/api/stats", headers=ADMIN).json()

    przed = stats()
    s = SessionLocal()
    darmowy_id = s.query(Trader).filter(Trader.email == DARMOWY).one().id
    zam = Order(trader_id=darmowy_id, product_key="2step-50k",
                amount_usd=299.0, status="paid", provider="manual")
    s.add(zam); s.commit(); zam_id = zam.id; s.close()

    po = stats()
    assert po["traders"] == przed["traders"] + 1
    assert po["traders_free"] == przed["traders_free"] - 1
    assert po["total"] == przed["total"] + 1          # FREE80001 wszedł do statystyk
    # Kolejka operacyjna liczyła jego konto CAŁY CZAS — płatność nic nie zmienia.
    assert po["provisioning"] == przed["provisioning"]

    # Sprzątamy, żeby nie przestawiać danych innym modułom w tym samym procesie.
    s = SessionLocal()
    s.delete(s.get(Order, zam_id)); s.commit(); s.close()
    assert stats() == przed
