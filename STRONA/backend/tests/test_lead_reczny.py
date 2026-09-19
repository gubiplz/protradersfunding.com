"""Lead wpisany z ręki: ktoś napisał na Telegramie prosto z reklamy i nie ma
ani konta, ani wiersza w tabeli leadów — czyli nie da się mu ani nic wystawić,
ani go przypisać. `POST /api/admin/leads` jest jedynym wejściem dla takiej osoby.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead, LeadEvent, Order, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

# Własna domena adresów: wszystkie moduły testowe dzielą jeden plik SQLite
# (`setdefault` na DATABASE_URL), a licznik jest per plik. Bez tego `lead1@...`
# z tego pliku zderzyłby się z `lead1@...` z sąsiedniego dopiero w pełnym
# przebiegu — czyli w miejscu, w którym najtrudniej to zrozumieć.
LICZNIK = iter(range(10_000))


def _mail() -> str:
    return f"reczny{next(LICZNIK)}@reczny.test"


@pytest.fixture(autouse=True)
def _telegram_odciety(monkeypatch):
    """Karta na kanale idzie tą samą drogą co przy zgłoszeniu z landingu, więc
    bez podmiany endpoint strzeliłby na api.telegram.org."""
    wyslane: list[tuple[int, str]] = []
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda lead_id, tekst, **kw: (wyslane.append((lead_id, tekst)),
                                                      (True, "", 0))[1])
    return wyslane


def _dodaj(**pola):
    dane = {"email": _mail()}
    dane.update(pola)
    return client.post("/api/admin/leads", headers=ADMIN, json=dane)


def _lead(lead_id) -> Lead:
    s = SessionLocal()
    try:
        return s.get(Lead, lead_id)
    finally:
        s.close()


def test_dodanie_wymaga_admina():
    assert client.post("/api/admin/leads", json={"email": _mail()}).status_code in (401, 403)


def test_zapisuje_pola_z_okna():
    mail = _mail()
    odp = client.post("/api/admin/leads", headers=ADMIN, json={
        "email": mail.upper(), "name": "Jan Kowalski", "phone": "+48 601 234 567",
        "telegram": "@jasiu", "country": "Poland", "note": "napisał z reklamy"})
    assert odp.status_code == 200 and odp.json()["existing"] is False

    lead = _lead(odp.json()["id"])
    # Mail małymi literami tak samo jak przy ingeście — inaczej ten sam człowiek
    # wpisany raz z panelu, a raz z landingu byłby dwoma wierszami.
    assert lead.email == mail
    assert lead.name == "Jan Kowalski"
    assert lead.phone == "+48601234567"    # E.164 bez spacji
    assert lead.phone_iso == "PL"          # kraj z prefiksu, bo okno o niego nie pyta
    assert lead.telegram == "@jasiu"
    assert lead.note == "napisał z reklamy"


def test_zrodlo_to_manual():
    """Konwersja landingu dzieli leady przez liczbę zgłoszeń z formularza.
    Gdyby wpisani ręcznie mieli źródło puste albo takie jak formularz, landing
    zaczęłby odpowiadać za ludzi, których nigdy nie widział."""
    assert _lead(_dodaj().json()["id"]).source == "manual"


def test_nie_zmysla_oceny_ankiety():
    """Ankiety nie było, więc oceny też nie ma. Panel rysuje ją tylko przy
    ustawionym `tier` (admin-panel.js:920) — pusty daje „—", czyli prawdę.
    Wpisana na sztywno „wysoka jakość" pchałaby takich leadów na górę list
    sortowanych oceną, przed ludzi, którzy tę ocenę naprawdę wypracowali."""
    lead = _lead(_dodaj(name="Bez ankiety").json()["id"])
    assert lead.tier is None
    assert lead.score == 0          # kolumna nie jest nullowalna, 0 = brak punktów


def test_zapisuje_historie_zdarzen():
    """Karta leada pokazuje historię. Bez wpisu na start wyglądałaby, jakby
    lead pojawił się znikąd — a to pierwsza rzecz, o którą pyta dział."""
    lead_id = _dodaj().json()["id"]
    s = SessionLocal()
    try:
        zdarzenia = s.query(LeadEvent).filter(LeadEvent.lead_id == lead_id).all()
    finally:
        s.close()
    assert [z.kind for z in zdarzenia] == ["applied"]
    assert zdarzenia[0].actor == "panel"


def test_karta_leci_na_kanal(_telegram_odciety):
    """Dział pracuje na Telegramie, nie w panelu. Lead bez karty byłby jedynym,
    którego nie da się tam obsłużyć."""
    lead_id = _dodaj(name="Na kanal").json()["id"]
    assert [x[0] for x in _telegram_odciety] == [lead_id]
    assert "Na kanal" in _telegram_odciety[0][1]


def test_zly_mail_odrzucony():
    odp = client.post("/api/admin/leads", headers=ADMIN, json={"email": "to nie mail"})
    assert odp.status_code == 400


def test_znany_mail_oddaje_istniejacego_zamiast_zakladac_drugiego():
    """`leads.email` jest UNIQUE, więc druga próba i tak skończyłaby się błędem
    bazy — i to takim, który wpisującemu nic nie mówi. Zamiast tego oddajemy
    tamten wiersz, a panel otwiera jego kartę."""
    mail = _mail()
    pierwszy = client.post("/api/admin/leads", headers=ADMIN,
                           json={"email": mail, "name": "Pierwszy"}).json()
    client.post(f"/api/admin/leads/{pierwszy['id']}", headers=ADMIN,
                json={"owner": "Bartek", "note": "ustalone przez dział"})

    drugi = client.post("/api/admin/leads", headers=ADMIN,
                        json={"email": mail, "name": "Drugi", "note": "nowa notatka"})
    assert drugi.status_code == 200
    tresc = drugi.json()
    assert tresc["existing"] is True and tresc["id"] == pierwszy["id"]
    # Kto go prowadzi, jest tu najważniejsze: bez tego dwie osoby piszą do tego
    # samego człowieka.
    assert tresc["owner"] == "Bartek"

    s = SessionLocal()
    try:
        assert s.query(Lead).filter(Lead.email == mail).count() == 1
    finally:
        s.close()
    # Trzy pola z okna nie mają prawa zetrzeć tego, co dział zdążył ustalić.
    lead = _lead(pierwszy["id"])
    assert lead.name == "Pierwszy" and lead.note == "ustalone przez dział"


def test_lead_z_reki_ktory_kupil_dostaje_kwote():
    """Po co on w ogóle jest w tabeli: ma się sam zapalić na kwotę, gdy zapłaci
    za link wystawiony z jego karty. Wiązanie idzie po mailu, więc lead wpisany
    ręcznie liczy się tak samo jak ten z formularza."""
    mail = _mail()
    lead_id = client.post("/api/admin/leads", headers=ADMIN,
                          json={"email": mail, "name": "Kupił"}).json()["id"]

    s = SessionLocal()
    tr = Trader(email=mail, password_hash=auth.hash_password("haslo1234"),
                full_name="Kupił", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="eval-100k", amount_usd=549.0, status="paid"))
    s.add(Order(trader_id=tr.id, product_key="eval-25k", amount_usd=199.0, status="pending"))
    s.commit()
    trader_id = tr.id
    s.close()

    odp = client.get("/api/admin/leads", headers=ADMIN)
    wiersz = next(x for x in odp.json() if x["id"] == lead_id)
    assert wiersz["trader_id"] == trader_id
    assert wiersz["paid_usd"] == 549.0
