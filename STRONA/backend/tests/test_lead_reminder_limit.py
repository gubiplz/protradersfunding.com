"""Cykl przypomnień o leadzie: długość serii i ponowne sprawdzenie warunku.

Usterka z produkcji, którą pilnuje ten plik: cykl typu „bought" bił ludziom,
którzy nie kupili. Warunek sprawdzany był wyłącznie przy zakładaniu wpisu, więc
cykl powstały na stanie, który przestał obowiązywać, żył dalej — jedenaście
leadów z `paid_usd = 0` miało licznik 5.

Sufit serii był drugą, nietrafioną odpowiedzią na ten sam szum i dlatego dziś
jest wyłączony (`POWTORZEN_MAX = None`): cykl o kliencie ma chodzić tak długo,
jak długo ten człowiek jest klientem. Mechanizm sufitu zostaje w kodzie, więc
testy sprawdzają oba ustawienia — brak limitu jako stan domyślny i skończoną
serię po wpisaniu liczby.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import main  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (Lead, LeadEvent, LeadReminder,  # noqa: E402
                        Order, Trader)

init_db()

TERAZ = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
PRZEDROSTEK = "limit-przypomnien"


@pytest.fixture
def swiat():
    """Sprząta wyłącznie to, co samo założyło — pakiet dzieli jedną bazę.

    Na starcie wycisza WSZYSTKIE aktywne przypomnienia. `_wyslij_zaplanowane`
    skanuje całą tabelę, więc wiersz zostawiony przez inny przypadek trafiłby
    do wyniku, a przy leadzie już skasowanym — do próby zapisu zdarzenia na
    nieistniejącym rodzicu. To czyszczenie stanu wejściowego, nie obejście:
    każdy test ma widzieć dokładnie to, co sam założył.
    """
    s = SessionLocal()
    s.query(LeadReminder).filter(LeadReminder.active.is_(True)).update(
        {"active": False}, synchronize_session=False)
    s.commit()
    try:
        yield s
    finally:
        try:
            # Sesja ma wyłączony autoflush, więc zdarzenia dopisane przez
            # `_zdarzenie` czekają w pamięci i poszłyby do bazy dopiero przy
            # commicie sprzątania — czyli JUŻ PO skasowaniu leada, na klucz
            # obcy wskazujący donikąd. Asercje testu już się wykonały na
            # obiektach w pamięci, więc nie ma tu czego zapisywać.
            s.rollback()
            leady = s.query(Lead).filter(Lead.email.like(f"{PRZEDROSTEK}%")).all()
            ids = [l.id for l in leady]
            if ids:
                s.query(LeadReminder).filter(LeadReminder.lead_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(LeadEvent).filter(LeadEvent.lead_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(Lead).filter(Lead.id.in_(ids)).delete(
                    synchronize_session=False)
            traderzy = s.query(Trader).filter(
                Trader.email.like(f"{PRZEDROSTEK}%")).all()
            tids = [t.id for t in traderzy]
            if tids:
                s.query(Order).filter(Order.trader_id.in_(tids)).delete(
                    synchronize_session=False)
                s.query(Trader).filter(Trader.id.in_(tids)).delete(
                    synchronize_session=False)
            s.commit()
        finally:
            s.close()


def _lead(s, nazwa, *, bought=False, z_zakupem=False):
    mail = f"{PRZEDROSTEK}-{nazwa}@example.com"
    lead = Lead(email=mail, name=nazwa, source="money", status="messaged",
                bought=bought, created_at=TERAZ - timedelta(days=30))
    s.add(lead)
    s.flush()
    if z_zakupem:
        t = Trader(email=mail, password_hash="x")
        s.add(t)
        s.flush()
        s.add(Order(trader_id=t.id, product_key="2step-100k", amount_usd=499.0,
                    status="paid", paid_at=TERAZ - timedelta(days=30)))
    s.commit()
    return lead


def _cykl(s, lead, *, kind="bought", sent=0, repeat=7):
    r = LeadReminder(lead_id=lead.id, kind=kind, repeat_days=repeat,
                     due_at=TERAZ - timedelta(minutes=1), sent_count=sent,
                     text="Update konta", created_by="cron")
    s.add(r)
    s.commit()
    return r


def _przebieg(s, monkeypatch, lead):
    """Uruchamia wysyłkę i zwraca TYLKO to, co dotyczy podanego leada.

    `_wyslij_zaplanowane` skanuje całą tabelę, więc bez zawężenia test mierzy
    także wpisy innych przypadków w pakiecie — dzieli z nimi jedną bazę.
    """
    monkeypatch.setattr(main.telegram, "lead_chat_id", lambda src: "-100999")
    teksty, pushy = main._wyslij_zaplanowane(s, TERAZ)
    znak = f"<b>{lead.name}</b>"
    return ([t for _, t in teksty if znak in t],
            [p for p in pushy if p[0] == lead.id])


# --------------------------------------------------------------------------- #
#  Długość serii
# --------------------------------------------------------------------------- #
def test_domyslnie_nie_ma_sufitu(swiat):
    """Stan produkcyjny: cykl o kliencie nie ma się kończyć z powodu kalendarza."""
    assert main.POWTORZEN_MAX is None


def test_cykl_leci_dalej_po_wielu_wysylkach(swiat, monkeypatch):
    """Dwudziesta wiadomość ma wyjść tak samo jak pierwsza, dopóki lead jest
    klientem. Serię kończy `_nadal_klient`, nie licznik."""
    lead = _lead(swiat, "bezsufitu", bought=True)
    r = _cykl(swiat, lead, sent=19)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1
    assert "Ostatnie z serii" not in teksty[0], "nic tu nie wygasa"
    assert r.sent_count == 20
    assert r.active is True
    assert r.due_at > TERAZ, "termin ma się przesunąć na kolejny tydzień"


def test_sufit_dziala_gdy_ktos_go_ustawi(swiat, monkeypatch):
    """Mechanizm zostaje w kodzie — wpisanie liczby ma przywrócić skończoną
    serię bez dotykania czegokolwiek poza tą stałą."""
    monkeypatch.setattr(main, "POWTORZEN_MAX", 3)
    lead = _lead(swiat, "sufit", bought=True)
    r = _cykl(swiat, lead, sent=2)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1, "ostatnia wiadomość ma jeszcze wyjść"
    assert r.sent_count == 3
    assert r.active is False, "po wyczerpaniu serii wpis ma zgasnąć"


def test_ostatnia_wiadomosc_mowi_ze_jest_ostatnia(swiat, monkeypatch):
    """Ciche urwanie byłoby gorsze niż brak limitu — dział myślałby, że automat
    dalej pilnuje tematu, i przestałby pilnować go sam."""
    monkeypatch.setattr(main, "POWTORZEN_MAX", 3)
    lead = _lead(swiat, "ostatnia", bought=True)
    _cykl(swiat, lead, sent=2)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert "Ostatnie z serii" in teksty[0]
    assert f"lead={lead.id}" in teksty[0], "ma być czym uzbroić ponownie"


def test_przed_koncem_serii_cykl_leci_dalej(swiat, monkeypatch):
    lead = _lead(swiat, "srodek", bought=True)
    r = _cykl(swiat, lead, sent=0)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1
    assert "Ostatnie z serii" not in teksty[0]
    assert r.active is True
    assert r.due_at > TERAZ, "termin ma się przesunąć"


def test_jednorazowe_zamyka_sie_po_pierwszej_wysylce(swiat, monkeypatch):
    lead = _lead(swiat, "jednorazowe", bought=True)
    r = _cykl(swiat, lead, kind="manual", repeat=None)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1
    assert "Ostatnie z serii" not in teksty[0], "to nie seria, tylko jeden wpis"
    assert r.active is False


# --------------------------------------------------------------------------- #
#  Ponowne sprawdzenie warunku
# --------------------------------------------------------------------------- #
def test_cykl_po_zakupie_gasnie_gdy_lead_nie_jest_juz_klientem(swiat, monkeypatch):
    """Sedno usterki z produkcji: `bought=False`, zero zapłaconych zamówień,
    a cykl bił piąty raz."""
    lead = _lead(swiat, "nieklient", bought=False, z_zakupem=False)
    r = _cykl(swiat, lead, sent=5)

    teksty, pushy = _przebieg(swiat, monkeypatch, lead)

    assert teksty == [], "nic nie powinno pójść na czat"
    assert pushy == []
    assert r.active is False


def test_cykl_zostaje_gdy_lead_ma_oplacone_zamowienie(swiat, monkeypatch):
    lead = _lead(swiat, "platnik", bought=False, z_zakupem=True)
    r = _cykl(swiat, lead, sent=0)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1
    assert r.active is True


def test_reczne_przypomnienie_nie_podlega_sprawdzeniu_warunku(swiat, monkeypatch):
    """Wpis ustawiony przez człowieka dotyczy jego sprawy, nie statusu zakupu —
    gaszenie go warunkiem o kliencie kasowałoby cudzą robotę."""
    lead = _lead(swiat, "reczne", bought=False, z_zakupem=False)
    r = _cykl(swiat, lead, kind="manual", sent=0)

    teksty, _ = _przebieg(swiat, monkeypatch, lead)

    assert len(teksty) == 1, "ręczne ma wyjść mimo braku zakupu"
    assert r.active is True


def test_warunek_dopasowuje_mail_bez_wzgledu_na_wielkosc_liter(swiat):
    lead = _lead(swiat, "wielkosc", bought=False, z_zakupem=True)
    lead.email = lead.email.upper()
    swiat.commit()

    assert main._nadal_klient(swiat, lead) is True
