"""Darmowy lejek jako OSOBNY desk: dzwonek i preferencje pushu.

Na Telegramie podział istnieje od dawna — `telegram.lead_chat_id` kieruje
`source` zaczynający się od „free" na czat LEADS NIGERIA. Panel o tym nie
wiedział: dzwonek miał jedno pole „Leads", a wyciszenie kategorii gasiło oba
deski naraz.

Drugi wątek to budżet dzwonka. Wspólne obcięcie połączonej listy sprawiało, że
seria zdarzeń o leadach wypychała z niej wszystkie zamówienia — i że liczba
przy polu mówiła o obcięciu, a nie o tym, ile naprawdę przyszło.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import main, notify  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Lead, LeadEvent  # noqa: E402

init_db()

PRZEDROSTEK = "desk-free"


@pytest.fixture
def swiat():
    s = SessionLocal()
    try:
        yield s
    finally:
        try:
            s.rollback()
            ids = [i for (i,) in s.query(Lead.id)
                   .filter(Lead.email.like(f"{PRZEDROSTEK}%")).all()]
            if ids:
                s.query(LeadEvent).filter(LeadEvent.lead_id.in_(ids)).delete(
                    synchronize_session=False)
                s.query(Lead).filter(Lead.id.in_(ids)).delete(
                    synchronize_session=False)
            s.commit()
        finally:
            s.close()


def _lead(s, nazwa, source):
    lead = Lead(email=f"{PRZEDROSTEK}-{nazwa}@example.com", name=nazwa,
                source=source, status="new")
    s.add(lead)
    s.commit()
    return lead


# --------------------------------------------------------------------------- #
#  Rozpoznanie desku
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source,oczekiwany", [
    ("free", "free"),
    ("freeaccount", "free"),
    ("FREE", "free"),
    ("  free  ", "free"),
    ("money", "leads"),
    ("safe", "leads"),
    ("manual", "leads"),
    ("", "leads"),
    (None, "leads"),
])
def test_desk_rozpoznany_tak_samo_jak_czat_na_telegramie(source, oczekiwany):
    """Ta sama reguła co `telegram.lead_chat_id` — gdyby się rozjechały, karta
    wisiałaby na jednym kanale, a powiadomienie o niej szło z drugiego."""
    assert main._desk_leada(source) == oczekiwany


def test_reguła_zgadza_sie_z_telegramem(monkeypatch):
    monkeypatch.setattr(main.telegram.settings, "telegram_free_leads_chat_id", "-100free")
    monkeypatch.setattr(main.telegram.settings, "telegram_leads_chat_id", "-100leads")
    for source in ("free", "freeaccount", "money", "safe", None):
        czat = main.telegram.lead_chat_id(source)
        assert (czat == "-100free") == (main._desk_leada(source) == "free")


# --------------------------------------------------------------------------- #
#  Dzwonek
# --------------------------------------------------------------------------- #
def test_dzwonek_niesie_desk_przy_zdarzeniu_o_leadzie(swiat):
    darmowy = _lead(swiat, "darmowy", "free")
    platny = _lead(swiat, "platny", "money")
    for l in (darmowy, platny):
        swiat.add(LeadEvent(lead_id=l.id, kind="applied", detail="x", actor="test"))
    swiat.commit()

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    d = TestClient(app).get("/api/admin/inbox",
                            headers={"X-Admin-Token": get_settings().admin_token}).json()
    wg_id = {i["lead_id"]: i for i in d["items"] if i.get("lead_id")}

    assert wg_id[darmowy.id]["desk"] == "free"
    assert wg_id[platny.id]["desk"] == "leads"


def test_budzet_dzwonka_jest_per_desk(swiat, monkeypatch):
    """Sedno pytania „czemu max 15": wspólne obcięcie znaczyło, że hałaśliwy
    desk wypychał pozostałe. Każdy ma teraz własny budżet."""
    darmowy = _lead(swiat, "halas", "free")
    for i in range(80):
        swiat.add(LeadEvent(lead_id=darmowy.id, kind="status",
                            detail=f"zmiana {i}", actor="test"))
    swiat.commit()

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    d = TestClient(app).get("/api/admin/inbox",
                            headers={"X-Admin-Token": get_settings().admin_token}).json()
    z_nigerii = [i for i in d["items"] if i.get("desk") == "free"]

    assert len(z_nigerii) <= 30, "budżet jednego desku ma być ograniczony"
    # I nie zjada budżetu pozostałych — pozostałe deski mają własny licznik,
    # więc obecność zdarzeń z darmowego lejka nie kasuje wpisów innego rodzaju.
    assert all(i.get("desk") != "free" or i["type"] == "lead" for i in d["items"])


# --------------------------------------------------------------------------- #
#  Preferencje pushu
# --------------------------------------------------------------------------- #
def test_lead_z_darmowego_lejka_uzywa_kategorii_ng(swiat, monkeypatch):
    darmowy = _lead(swiat, "push-ng", "free")
    uzyte: list[str] = []
    monkeypatch.setattr(notify, "notify_admins",
                        lambda event, *a, **k: uzyte.append(event))
    monkeypatch.setattr(main.notify, "notify_admins",
                        lambda event, *a, **k: uzyte.append(event))

    main._lead_push(darmowy.id, "New lead: X", event="lead_new")
    main._lead_push(darmowy.id, "Ktoś: took the lead")
    main._lead_push(darmowy.id, "Follow-up", event="lead_reminder")

    assert uzyte == ["free_new", "free_action", "free_reminder"]


def test_lead_platny_zostaje_przy_kategoriach_lead(swiat, monkeypatch):
    platny = _lead(swiat, "push-platny", "money")
    uzyte: list[str] = []
    monkeypatch.setattr(main.notify, "notify_admins",
                        lambda event, *a, **k: uzyte.append(event))

    main._lead_push(platny.id, "New lead: X", event="lead_new")
    main._lead_push(platny.id, "Ktoś: took the lead")

    assert uzyte == ["lead_new", "lead_action"]


def test_wyciszenie_jednego_desku_nie_gasi_drugiego(swiat, monkeypatch):
    """Po to są osobne klucze: dział może wyciszyć darmowy lejek i dalej
    dostawać płatny."""
    from app import push as push_mod
    from app import auth
    from app.models import Trader

    s = SessionLocal()
    admin = Trader(email=f"{PRZEDROSTEK}-adm@example.com",
                   password_hash=auth.hash_password("haslo1234"),
                   referral_code=auth.secrets.token_hex(3), is_admin=True,
                   ui_prefs='{"admin_push":{"free_new":false}}')
    s.add(admin)
    s.commit()
    admin_id = admin.id
    s.close()

    dostali: list[int] = []
    monkeypatch.setattr(push_mod, "send_to_trader",
                        lambda tid, *a, **k: (dostali.append(tid), 1)[1])
    try:
        notify.notify_admins("free_new", "Nowy lead z darmowego")
        assert admin_id not in dostali, "wyciszony desk nie brzęczy"

        notify.notify_admins("lead_new", "Nowy lead płatny")
        assert admin_id in dostali, "drugi desk ma brzęczeć dalej"
    finally:
        s = SessionLocal()
        try:
            # Dziecko najpierw: `notify_admins` zapisuje wpis do dzwonka,
            # a `notifications.trader_id` to klucz obcy.
            from app.models import Notification
            s.query(Notification).filter(Notification.trader_id == admin_id).delete(
                synchronize_session=False)
            s.query(Trader).filter(Trader.id == admin_id).delete(
                synchronize_session=False)
            s.commit()
        finally:
            s.close()
