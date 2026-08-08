"""Leady z landingu: przyjęcie, deduplikacja po mailu, doliczenie zakupu
i zmiana statusu — z panelu oraz przyciskiem z Telegrama. Dalej historia
zdarzeń i przypomnienia z crona."""
import os
import tempfile
from datetime import datetime, timedelta, timezone

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
TOKEN_LANDINGU = "sekret-landingu"
SEKRET_WEBHOOKA = "sekret-webhooka"
LICZNIK = iter(range(10_000))


@pytest.fixture(autouse=True)
def _srodowisko(monkeypatch):
    """Sekrety na czas testu + Telegram odcięty od sieci.

    Wysyłki podmieniamy na liście, a nie na atrapy transportu, bo sprawdzamy
    tu zachowanie endpointów, nie składanie multipartu (od tego jest
    test_payoutbot). Bez tej podmiany `ingest` strzelałby na api.telegram.org.
    """
    u = get_settings()
    monkeypatch.setattr(u, "lead_ingest_token", TOKEN_LANDINGU, raising=False)
    monkeypatch.setattr(u, "telegram_webhook_secret", SEKRET_WEBHOOKA, raising=False)
    wyslane: dict[str, list] = {"alert": [], "answer": [], "edit": [], "przypomnienie": []}
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda lead_id, tekst, **kw: (wyslane["alert"].append((lead_id, tekst)), (True, ""))[1])
    monkeypatch.setattr(telegram, "send_lead_message",
                        lambda tekst, **kw: (wyslane["przypomnienie"].append(tekst), (True, ""))[1])
    monkeypatch.setattr(telegram, "answer_callback",
                        lambda cb, tekst, **kw: (wyslane["answer"].append(tekst), (True, ""))[1])
    monkeypatch.setattr(telegram, "edit_lead_message",
                        lambda czat, mid, tekst, **kw: (wyslane["edit"].append((czat, mid, tekst)), (True, ""))[1])
    return wyslane


def _zgloszenie(**nadpisz):
    """Payload w kształcie, w jakim przysyła go landing (camelCase, `quality`)."""
    dane = {
        "email": f"lead{next(LICZNIK)}@test.pl",
        "name": "Jan Kowalski",
        "phone": "+48111222333",
        "phoneIso": "PL",
        "telegram": "@jasiu",
        "source": "questionnaire",
        "ref": "partner7",
        "outcome": "qualified",
        "answers": {"Do you buy the evaluation yourself?": "Yes"},
        "quality": {"tier": "high", "score": 42, "label": "HIGH QUALITY LEAD"},
        "ua": "Mozilla/5.0",
    }
    dane.update(nadpisz)
    return dane


def _wyslij(dane, token=TOKEN_LANDINGU):
    naglowki = {"X-Lead-Token": token} if token else {}
    return client.post("/api/leads/ingest", json=dane, headers=naglowki)


def _lead(lead_id):
    s = SessionLocal()
    try:
        return s.get(Lead, lead_id)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Autoryzacja                                                                 #
# --------------------------------------------------------------------------- #
def test_ingest_bez_tokenu_odmawia():
    assert _wyslij(_zgloszenie(), token=None).status_code == 401


def test_ingest_z_cudzym_tokenem_odmawia():
    assert _wyslij(_zgloszenie(), token="nie-ten").status_code == 401


def test_ingest_nie_przyjmuje_tokenu_admina():
    """Token landingu jest osobnym sekretem — admin nie otwiera tej furtki
    i, co ważniejsze, wyciek tokenu landingu nie otwiera panelu."""
    assert _wyslij(_zgloszenie(), token=get_settings().admin_token).status_code == 401


def test_lista_leadow_wymaga_admina():
    assert client.get("/api/admin/leads").status_code in (401, 403)


# --------------------------------------------------------------------------- #
#  Zapis i deduplikacja                                                        #
# --------------------------------------------------------------------------- #
def test_ingest_zapisuje_pola_z_landingu():
    dane = _zgloszenie()
    odp = _wyslij(dane)
    assert odp.status_code == 200 and odp.json()["new"] is True

    lead = _lead(odp.json()["id"])
    assert lead.email == dane["email"]
    assert lead.name == "Jan Kowalski"
    assert lead.phone_iso == "PL"          # camelCase z landingu -> kolumna
    assert (lead.tier, lead.score) == ("high", 42)
    assert lead.status == "new" and lead.applications == 1
    assert "Do you buy the evaluation yourself?" in lead.payload_json


def test_mail_normalizowany_do_malych_liter():
    """Inaczej „Jan@x.pl" i „jan@x.pl" to dwa wiersze i dwa telefony do tej
    samej osoby — czyli dokładnie to, po co powstała ta tabela."""
    dane = _zgloszenie(email="WIELKIE@Test.PL")
    lead = _lead(_wyslij(dane).json()["id"])
    assert lead.email == "wielkie@test.pl"


def test_ponowne_zgloszenie_nie_tworzy_drugiego_wiersza():
    dane = _zgloszenie()
    pierwszy = _wyslij(dane).json()
    drugi = _wyslij({**dane, "name": "Jan Nowak"}).json()

    assert drugi["id"] == pierwszy["id"] and drugi["new"] is False
    lead = _lead(pierwszy["id"])
    assert lead.applications == 2
    assert lead.name == "Jan Nowak"        # dane kontaktowe się odświeżają


def test_ponowne_zgloszenie_nie_kasuje_pracy_dzialu():
    """Najważniejszy test tej tabeli: człowiek wypełnia formularz drugi raz
    i nie ma prawa tym skasować statusu ani notatki z rozmowy."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                json={"status": "called", "note": "oddzwonić we wtorek"})

    _wyslij(dane)

    lead = _lead(lead_id)
    assert lead.status == "called"
    assert lead.note == "oddzwonić we wtorek"
    assert lead.contacted_at is not None


def test_ingest_odrzuca_bledny_mail():
    assert _wyslij(_zgloszenie(email="bez-malpy")).status_code == 400


# --------------------------------------------------------------------------- #
#  „Czy kupił" — wyliczane, nie przechowywane                                  #
# --------------------------------------------------------------------------- #
def _z_listy(lead_id):
    odp = client.get("/api/admin/leads", headers=ADMIN)
    assert odp.status_code == 200
    return next(x for x in odp.json() if x["id"] == lead_id)


def test_lead_bez_konta_ma_zero():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    wiersz = _z_listy(lead_id)
    assert wiersz["trader_id"] is None and wiersz["paid_usd"] == 0


def test_lead_ktory_kupil_dostaje_kwote_po_mailu():
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]

    s = SessionLocal()
    tr = Trader(email=dane["email"], password_hash=auth.hash_password("haslo1234"),
                full_name="Jan Kowalski", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="eval-100k", amount_usd=549.0, status="paid"))
    # Nieopłacone nie ma prawa się doliczyć — inaczej porzucony koszyk
    # wyglądałby w panelu jak klient, który zapłacił.
    s.add(Order(trader_id=tr.id, product_key="eval-25k", amount_usd=199.0, status="pending"))
    s.commit()
    trader_id = tr.id
    s.close()

    wiersz = _z_listy(lead_id)
    assert wiersz["trader_id"] == trader_id
    assert wiersz["paid_usd"] == 549.0


# --------------------------------------------------------------------------- #
#  Statusy                                                                     #
# --------------------------------------------------------------------------- #
def test_contacted_at_zapisuje_pierwszy_kontakt_a_nie_ostatni():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "no_answer"})
    pierwszy = _lead(lead_id).contacted_at
    assert pierwszy is not None

    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "called"})
    lead = _lead(lead_id)
    assert lead.status == "called" and lead.contacted_at == pierwszy


def test_nieznany_status_odrzucony():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "kupil"})
    assert odp.status_code == 400
    assert _lead(lead_id).status == "new"


def test_sama_notatka_nie_rusza_statusu():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "called"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "prosi o telefon po 18"})
    lead = _lead(lead_id)
    assert lead.status == "called" and lead.note == "prosi o telefon po 18"


def test_filtr_po_statusie():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "rejected"})
    odp = client.get("/api/admin/leads?status=rejected", headers=ADMIN)
    assert all(x["status"] == "rejected" for x in odp.json())
    assert lead_id in [x["id"] for x in odp.json()]


# --------------------------------------------------------------------------- #
#  Telegram                                                                    #
# --------------------------------------------------------------------------- #
def _callback(lead_id, status, sekret=SEKRET_WEBHOOKA):
    naglowki = {"X-Telegram-Bot-Api-Secret-Token": sekret} if sekret else {}
    return client.post("/api/telegram/webhook", headers=naglowki, json={
        "callback_query": {
            "id": "cb1", "data": f"lead:{lead_id}:{status}",
            "from": {"first_name": "Hubert"},
            "message": {"message_id": 55, "chat": {"id": -100123}},
        }})


def test_webhook_bez_sekretu_odmawia():
    """Adres webhooka nie jest tajny, więc to jedyna kontrola — bez niej
    status leada mógłby zmienić każdy, kto go zgadnie."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert _callback(lead_id, "called", sekret=None).status_code == 401
    assert _callback(lead_id, "called", sekret="nie-ten").status_code == 401
    assert _lead(lead_id).status == "new"


def test_przycisk_zmienia_status_i_przepisuje_wiadomosc(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = _callback(lead_id, "no_answer")

    assert odp.status_code == 200
    assert _lead(lead_id).status == "no_answer"
    assert "Nie odbiera" in _srodowisko["answer"][-1]
    czat, mid, tekst = _srodowisko["edit"][-1]
    assert (czat, mid) == ("-100123", 55)
    assert "Nie odbiera" in tekst and "Hubert" in tekst


def test_webhook_przepuszcza_nieswoje_update(_srodowisko):
    """Bot dostaje też zwykłe wiadomości. Muszą kończyć się 200, bo Telegram
    ponawia każdy update, na który nie dostał potwierdzenia."""
    odp = client.post("/api/telegram/webhook",
                      headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_WEBHOOKA},
                      json={"message": {"text": "cześć"}})
    assert odp.status_code == 200 and _srodowisko["edit"] == []


def test_alert_escapuje_dane_z_formularza(_srodowisko):
    """Imię idzie do wiadomości z parse_mode=HTML. Bez ucieczki jeden nawias
    kątowy rozsypuje alert albo wstrzykuje do niego własne znaczniki."""
    _wyslij(_zgloszenie(name="<b>Jan</b> <script>"))
    _, tekst = _srodowisko["alert"][-1]
    assert "<script>" not in tekst
    assert "&lt;b&gt;Jan&lt;/b&gt;" in tekst


def test_alert_niesie_kontakt_i_ocene(_srodowisko):
    _wyslij(_zgloszenie())
    _, tekst = _srodowisko["alert"][-1]
    assert "+48111222333" in tekst and "HIGH 42" in tekst


# --------------------------------------------------------------------------- #
#  Historia                                                                    #
# --------------------------------------------------------------------------- #
def _zdarzenia(lead_id, kind=None):
    s = SessionLocal()
    try:
        q = s.query(LeadEvent).filter(LeadEvent.lead_id == lead_id)
        if kind:
            q = q.filter(LeadEvent.kind == kind)
        return q.order_by(LeadEvent.id).all()
    finally:
        s.close()


def test_historia_pamieta_odpowiedzi_z_kazdego_zgloszenia():
    """Sedno tej tabeli. `leads` trzyma ostatnią wersję zgłoszenia, więc po
    drugim wypełnieniu formularza zostawał sam licznik i nie dało się
    powiedzieć, czy człowiek zmienił zdanie, czy kliknął dwa razy."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    _wyslij({**dane, "answers": {"When do you want to buy?": "In three months"}})

    zgloszenia = _zdarzenia(lead_id, "applied")
    assert len(zgloszenia) == 2
    assert "Do you buy the evaluation yourself?" in zgloszenia[0].payload_json
    assert "In three months" in zgloszenia[1].payload_json
    assert zgloszenia[1].detail == "application #2 — qualified, high 42"
    assert [z.actor for z in zgloszenia] == ["landing", "landing"]

    # Wiersz leada zna już tylko ostatnią wersję — dlatego historia jest osobno.
    assert "Do you buy the evaluation yourself?" not in _lead(lead_id).payload_json


def test_historia_zapisuje_kto_zmienil_status(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "called"})
    _callback(lead_id, "rejected")

    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "status")] == [
        ("new → called", "panel"), ("called → rejected", "telegram:Hubert")]


def test_ten_sam_status_drugi_raz_nie_zasmieca_historii():
    """Przycisk zostaje pod wiadomością w Telegramie i klika się go odruchowo.
    Historia ma pokazywać zmiany, nie kliknięcia."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "called"})
    _callback(lead_id, "called")
    assert len(_zdarzenia(lead_id, "status")) == 1


def test_historia_zapisuje_notatke():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "prosi o telefon po 18"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "prosi o telefon po 18"})

    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "note")] == [
        ("prosi o telefon po 18", "panel")]


def test_szczegoly_wymagaja_admina():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert client.get(f"/api/admin/leads/{lead_id}").status_code in (401, 403)


def test_szczegoly_nieznanego_leada_to_404():
    assert client.get("/api/admin/leads/99999999", headers=ADMIN).status_code == 404


def test_szczegoly_niosa_zamowienia_i_historie():
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]

    s = SessionLocal()
    tr = Trader(email=dane["email"], password_hash=auth.hash_password("haslo1234"),
                full_name="Jan Kowalski", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="eval-100k", amount_usd=549.0, status="paid"))
    s.add(Order(trader_id=tr.id, product_key="eval-25k", amount_usd=199.0, status="pending"))
    s.commit(); s.close()

    d = client.get(f"/api/admin/leads/{lead_id}", headers=ADMIN).json()
    assert d["paid_usd"] == 549.0                      # nieopłacone się nie liczy
    assert {o["status"] for o in d["orders"]} == {"paid", "pending"}
    assert d["events"][0]["kind"] == "applied"
    assert d["events"][0]["answers"] == dane["answers"]


# --------------------------------------------------------------------------- #
#  Przypomnienia (cron)                                                        #
# --------------------------------------------------------------------------- #
def _cofnij(lead_id, *, od_zgloszenia=None, od_telefonu=None):
    """Postarza lead, żeby test nie musiał czekać trzech dni."""
    s = SessionLocal()
    try:
        lead = s.get(Lead, lead_id)
        teraz = datetime.now(timezone.utc)
        if od_zgloszenia is not None:
            lead.created_at = teraz - timedelta(days=od_zgloszenia)
        if od_telefonu is not None:
            lead.contacted_at = teraz - timedelta(days=od_telefonu)
        s.commit()
    finally:
        s.close()


def _cron():
    return client.post("/api/cron/lead-followups", headers=ADMIN)


def test_przypomnienie_gdy_nikt_nie_oddzwonil(_srodowisko):
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    _cofnij(lead_id, od_zgloszenia=5)

    assert _cron().status_code == 200
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["no_contact"]
    assert any(dane["email"] in t for t in _srodowisko["przypomnienie"])


def test_swiezy_lead_nie_wywoluje_przypomnienia():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _cron()
    assert _zdarzenia(lead_id, "reminder") == []


def test_przypomnienie_nie_wraca_przy_kazdym_przebiegu():
    """Dedup po historii, nie po nowej kolumnie-znaczniku: cron chodzi co kilka
    minut, a dział ma dostać jedną wiadomość o jednym człowieku."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _cofnij(lead_id, od_zgloszenia=5)
    _cron(); _cron(); _cron()
    assert len(_zdarzenia(lead_id, "reminder")) == 1


def test_kupujacy_ma_pierwszenstwo_przed_statusem(_srodowisko):
    """Status przy takim leadzie potrafi miesiącami stać na „new", bo nikt go
    nie odklikał. Zapłacone zamówienie jest ważniejsze niż to, co w panelu."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    _cofnij(lead_id, od_zgloszenia=5)

    s = SessionLocal()
    tr = Trader(email=dane["email"], password_hash=auth.hash_password("haslo1234"),
                full_name="Jan Kowalski", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="eval-100k", amount_usd=549.0, status="paid"))
    s.commit(); s.close()

    _cron()
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["bought"]
    tekst = next(t for t in _srodowisko["przypomnienie"] if dane["email"] in t)
    assert "549" in tekst


def test_przypomnienie_o_rozmowie_bez_ciagu_dalszego():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "called"})
    _cofnij(lead_id, od_zgloszenia=30, od_telefonu=14)

    _cron()
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["stalled"]
