"""Listy w panelu nie moga dokladac zapytania do bazy na kazdy wiersz.

Kazda z tych list budowala wiersz po wierszu przez `session.get(...)`, wiec
liczba zapytan rosla razem z liczba danych: zmierzone na realnej skali
1998 zapytan na ranking, 602 na wnioski o wyplate, 402 na tickety. Lokalnie
tego nie widac (SQLite siedzi w procesie), ale na produkcji kazde z nich to
osobny round-trip do bazy za oceanem — czyli sekundy.

Test nie sprawdza „malo zapytan" (staly limit przeszedlby przy N+1 na malej
probce), tylko wlasnosc, ktora ma faktycznie obowiazywac: DOLOZENIE DANYCH
NIE ZMIENIA LICZBY ZAPYTAN.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, Order, Payout, PayoutRequest, SupportTicket,  # noqa: E402
                        Trader, TicketMessage)

init_db()

SCIEZKI = ["/api/admin/orders", "/api/admin/tickets", "/api/admin/payout-requests",
           "/api/admin/payouts", "/api/admin/inbox", "/api/leaderboard",
           "/api/orders", "/api/me/tickets"]


def _admin_naglowek() -> dict:
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == "np1@test").first()
    if not tr:
        tr = Trader(email="np1@test", password_hash=auth.hash_password("x"),
                    full_name="N Plus", is_admin=True, referral_code="NP1")
        s.add(tr)
        s.commit()
    naglowek = {"Authorization": f"Bearer {auth.make_token(tr.id, tr.password_hash)}"}
    s.close()
    return naglowek


def _dosyp(ile: int) -> None:
    """Dokłada `ile` kompletów danych do KAŻDEJ listy objętej testem."""
    s = SessionLocal()
    wlasciciel = s.query(Trader).filter(Trader.email == "np1@test").first()
    for i in range(ile):
        tr = Trader(email=f"np1-{i}-{s.query(Trader).count()}@test",
                    password_hash="x", full_name=f"Trader {i}",
                    referral_code=f"NP{i}{s.query(Trader).count()}")
        s.add(tr)
        s.flush()
        # Konta musza byc `funded` (tylko takie liczy ranking), ale POD KRESKA:
        # cala suita dzieli jedna baze, a ranking oddaje 20 najlepszych — konta na
        # plusie wypchnelyby z niego wiersze, na ktorych opieraja sie inne testy.
        acc = Account(trader_id=tr.id, login=f"9{tr.id}", product_key="eval-10k",
                      initial_balance=10000, balance=9000, equity=9000,
                      status="funded", phase="funded")
        s.add(acc)
        s.flush()
        # Zamówienia obu list: admina (wszystkie) i tradera (tylko swoje).
        s.add(Order(trader_id=tr.id, product_key="eval-10k", amount_usd=100,
                    status="paid", provider="stripe", account_id=acc.id))
        s.add(Order(trader_id=wlasciciel.id, product_key="eval-10k", amount_usd=100,
                    status="paid", provider="stripe", account_id=acc.id))
        for wlasc in (tr.id, wlasciciel.id):
            t = SupportTicket(trader_id=wlasc, subject=f"Temat {i}")
            s.add(t)
            s.flush()
            s.add(TicketMessage(ticket_id=t.id, author="trader", body="tresc"))
        # Wniosek celowo na INNYM koncie niz wyplata: gdyby siedzialy na tym samym,
        # `session.get` w petli trafialby w identity map SQLAlchemy i N+1 w widoku
        # Payouts byloby dla testu niewidoczne.
        drugie = Account(trader_id=tr.id, login=f"8{tr.id}", product_key="eval-10k",
                         initial_balance=10000, balance=9000, equity=9000,
                         status="funded", phase="funded")
        s.add(drugie)
        s.flush()
        s.add(PayoutRequest(account_id=drugie.id, trader_id=tr.id, profit_amount=1000,
                            trader_share=800, status="pending", details='{"iban":"PL"}'))
        s.add(Payout(account_id=acc.id, profit_amount=1000, trader_share=800, paid=True))
    s.commit()
    s.close()


def _zapytania(client: TestClient, naglowek: dict) -> dict[str, int]:
    licznik = {"n": 0}

    def zlicz(*_a, **_k):
        licznik["n"] += 1

    event.listen(engine, "before_cursor_execute", zlicz)
    try:
        wynik = {}
        for p in SCIEZKI:
            licznik["n"] = 0
            r = client.get(p, headers=naglowek)
            assert r.status_code == 200, f"{p} -> {r.status_code}"
            wynik[p] = licznik["n"]
        return wynik
    finally:
        event.remove(engine, "before_cursor_execute", zlicz)


def test_liczba_zapytan_nie_rosnie_z_liczba_wierszy():
    naglowek = _admin_naglowek()
    with TestClient(app) as c:
        _dosyp(3)
        malo = _zapytania(c, naglowek)
        _dosyp(12)          # pieciokrotnie wiecej danych na kazdej liscie
        duzo = _zapytania(c, naglowek)

    winne = {p: (malo[p], duzo[p]) for p in SCIEZKI if duzo[p] != malo[p]}
    assert not winne, (
        "zapytan przybylo razem z danymi — to N+1, na produkcji kazde z nich to "
        f"osobny round-trip do bazy: {winne}")
