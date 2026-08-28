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
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app import auth, lead_mail, notify, provisioning, sms, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, AppSetting, Lead, LeadEvent,  # noqa: E402
                        LeadReminder, Order, Product, Trader)

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
TOKEN_LANDINGU = "sekret-landingu"
SEKRET_WEBHOOKA = "sekret-webhooka"
CZAT_PLATNY = "-100111"
CZAT_FREE = "-100222"
LICZNIK = iter(range(10_000))
# Numery wiadomości muszą być unikalne w CAŁYM przebiegu, nie w jednym teście:
# leady zostają w bazie między testami, a webhook szuka leada po `tg_message_id`
# i przy duplikacie nie wie, do którego przypiąć notatkę.
NUMERY_WIADOMOSCI = iter(range(900, 10_000))


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
    # Dwie grupy, bo leady free kosztują nas konto, a płatne nic — obsługują je
    # inni ludzie. Ustawione na sztywno, żeby test mógł powiedzieć DOKĄD poszła
    # karta, a nie tylko że poszła.
    monkeypatch.setattr(u, "telegram_leads_chat_id", CZAT_PLATNY, raising=False)
    monkeypatch.setattr(u, "telegram_free_leads_chat_id", CZAT_FREE, raising=False)
    # Token, choć żadna wysyłka nie idzie w sieć: kod odróżnia „Telegram odmówił"
    # od „Telegramu tu nie ma" po konfiguracji, a bez tokenu ten plik testowałby
    # tę drugą sytuację — czyli ścieżkę, w której kart się w ogóle nie zamawia.
    monkeypatch.setattr(u, "telegram_bot_token", "token-testowy", raising=False)
    wyslane: dict[str, list] = {"alert": [], "answer": [], "edit": [],
                                "przypomnienie": [], "dm": [], "delete": [],
                                # Czat OBOK treści, nie zamiast: krotki wyżej
                                # rozpakowuje kilkanaście asercji w tym pliku.
                                "alert_czat": [], "przypomnienie_czat": [], "delete_czat": []}
    # Alert oddaje `message_id` — po nim webhook dopasowuje odpowiedź na wiadomość
    # do leada, więc atrapa musi je zwracać, inaczej notatki z Telegrama nie mają
    # się o co zaczepić.
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda lead_id, tekst, **kw: (wyslane["alert"].append((lead_id, tekst)),
                                                      wyslane["alert_czat"].append(kw.get("chat_id")),
                                                      (True, "", next(NUMERY_WIADOMOSCI)))[2])
    monkeypatch.setattr(telegram, "send_lead_message",
                        lambda tekst, **kw: (wyslane["przypomnienie"].append(tekst),
                                             wyslane["przypomnienie_czat"].append(kw.get("chat_id")),
                                             (True, ""))[2])
    monkeypatch.setattr(telegram, "answer_callback",
                        lambda cb, tekst, **kw: (wyslane["answer"].append(tekst), (True, ""))[1])
    monkeypatch.setattr(telegram, "edit_lead_message",
                        lambda czat, mid, tekst, **kw: (wyslane["edit"].append((czat, mid, tekst)), (True, ""))[1])
    monkeypatch.setattr(telegram, "send_dm",
                        lambda czat, tekst, **kw: (wyslane["dm"].append((czat, tekst)), (True, ""))[1])
    monkeypatch.setattr(telegram, "delete_lead_card",
                        lambda mid, **kw: (wyslane["delete"].append(mid),
                                           wyslane["delete_czat"].append(kw.get("chat_id")),
                                           (True, ""))[2])
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
                json={"status": "replied", "note": "odpisze we wtorek"})

    _wyslij(dane)

    lead = _lead(lead_id)
    assert lead.status == "replied"
    assert lead.note == "odpisze we wtorek"
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
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "messaged"})
    pierwszy = _lead(lead_id).contacted_at
    assert pierwszy is not None

    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "replied"})
    lead = _lead(lead_id)
    assert lead.status == "replied" and lead.contacted_at == pierwszy


def test_nieznany_status_odrzucony():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "kupil"})
    assert odp.status_code == 400
    assert _lead(lead_id).status == "new"


def test_sama_notatka_nie_rusza_statusu():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "replied"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "prosi o telefon po 18"})
    lead = _lead(lead_id)
    assert lead.status == "replied" and lead.note == "prosi o telefon po 18"


def test_filtr_po_statusie():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "rejected"})
    odp = client.get("/api/admin/leads?status=rejected", headers=ADMIN)
    assert all(x["status"] == "rejected" for x in odp.json())
    assert lead_id in [x["id"] for x in odp.json()]


# --------------------------------------------------------------------------- #
#  Telegram                                                                    #
# --------------------------------------------------------------------------- #
def _callback(lead_id, status, sekret=SEKRET_WEBHOOKA, kto="Hubert", uid=None):
    naglowki = {"X-Telegram-Bot-Api-Secret-Token": sekret} if sekret else {}
    return client.post("/api/telegram/webhook", headers=naglowki, json={
        "callback_query": {
            "id": "cb1", "data": f"lead:{lead_id}:{status}",
            "from": {"first_name": kto, **({"id": uid} if uid else {})},
            "message": {"message_id": 55, "chat": {"id": -100123}},
        }})


def test_webhook_bez_sekretu_odmawia():
    """Adres webhooka nie jest tajny, więc to jedyna kontrola — bez niej
    status leada mógłby zmienić każdy, kto go zgadnie."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert _callback(lead_id, "replied", sekret=None).status_code == 401
    assert _callback(lead_id, "replied", sekret="nie-ten").status_code == 401
    assert _lead(lead_id).status == "new"


def test_przycisk_zmienia_status_i_przepisuje_wiadomosc(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = _callback(lead_id, "no_reply")

    assert odp.status_code == 200
    assert _lead(lead_id).status == "no_reply"
    assert "Nie odpisuje" in _srodowisko["answer"][-1]
    czat, mid, tekst = _srodowisko["edit"][-1]
    assert (czat, mid) == ("-100123", 55)
    assert "Nie odpisuje" in tekst and "Hubert" in tekst


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
    """Nagłówek bierze etykietę i emoji z landingu, żeby ta sama osoba wyglądała
    tak samo w obu kanałach — dopisujemy tylko wynik, bo landing wysyła go osobno."""
    _wyslij(_zgloszenie())
    _, tekst = _srodowisko["alert"][-1]
    assert "+48111222333 (PL)" in tekst and "@jasiu" in tekst
    assert "<b>HIGH QUALITY LEAD</b> · 42" in tekst
    assert "Qualified" in tekst


def test_alert_niesie_uzasadnienie_oceny(_srodowisko):
    """Ocena bez uzasadnienia to goła liczba, której nikt nie ma jak sprawdzić.
    Czerwone flagi mają własną linijkę, bo mówią coś innego niż braki: nie „mało
    punktów", tylko „te dane kontaktowe mogą być nic niewarte"."""
    _wyslij(_zgloszenie(quality={
        "tier": "high", "score": 42,
        "reasons": ["kupuje sam", "ma konto u brokera"],
        "gaps": ["nie podał kraju"],
        "penalties": ["numer bez kierunkowego"],
    }))
    _, tekst = _srodowisko["alert"][-1]
    assert "kupuje sam · ma konto u brokera" in tekst
    assert "nie podał kraju" in tekst
    assert "⚠️" in tekst and "numer bez kierunkowego" in tekst


def test_alert_bez_oceny_nie_dokleja_pustych_linijek(_srodowisko):
    """Odrzuconego nikt nie punktuje, więc landing nie przysyła `quality`.
    Brak oceny ma znaczyć brak wierszy, a nie wiersze z niczym."""
    _wyslij(_zgloszenie(outcome="not_qualified", quality=None))
    _, tekst = _srodowisko["alert"][-1]
    assert "Why:" not in tekst and "Gaps:" not in tekst and "⚠️" not in tekst
    assert "Not qualified" in tekst and "#lead_out" in tekst
    assert "\n\n\n" not in tekst


def test_alert_niesie_cala_ankiete(_srodowisko):
    """Dział czyta kanał zamiast panelu. Ankieta ucięta na czwartym pytaniu to
    ta, o którą dzwoni się do człowieka drugi raz."""
    ankieta = {f"Pytanie {i}": f"Odpowiedź {i}" for i in range(1, 9)}
    _wyslij(_zgloszenie(answers=ankieta))
    _, tekst = _srodowisko["alert"][-1]
    for i in range(1, 9):
        assert f"Pytanie {i}" in tekst and f"Odpowiedź {i}" in tekst


def test_klikniecie_przycisku_nie_gubi_ankiety_z_karty(_srodowisko):
    """Każde kliknięcie przepisuje wiadomość od zera z `payload_json`. Gdyby ta
    ścieżka nie odczytała payloadu, karta traciłaby ankietę i uzasadnienie oceny
    w momencie, w którym ktoś ją bierze — czyli dokładnie wtedy, gdy zaczyna być
    potrzebna."""
    odp = _wyslij(_zgloszenie(
        answers={"Kiedy chcesz kupić?": "W tym tygodniu"},
        quality={"tier": "high", "score": 42, "reasons": ["kupuje sam"],
                 "penalties": ["numer bez kierunkowego"]},
    ))
    lead_id = odp.json()["id"]
    _callback(lead_id, "replied", kto="Bartek")

    _, _, tekst = _srodowisko["edit"][-1]
    assert "Kiedy chcesz kupić?" in tekst and "W tym tygodniu" in tekst
    assert "kupuje sam" in tekst and "numer bez kierunkowego" in tekst
    assert "Bartek" in tekst


def test_alert_escapuje_po_ucieciu_a_nie_przed(_srodowisko):
    """Ucięcie PO ucieczce rozcina encję w pół: `&amp;` zostaje jako `&am`,
    czego Telegram nie parsuje i odrzuca CAŁY alert — czyli powiadomienie nie
    dochodzi w ogóle, a lead wygląda, jakby nigdy nie przyszedł."""
    pytanie = "x" * 79 + "&" + "y" * 20
    _wyslij(_zgloszenie(answers={pytanie: "tak"}))
    _, tekst = _srodowisko["alert"][-1]
    assert "x" * 79 + "&amp;" in tekst


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
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "messaged"})
    _callback(lead_id, "rejected")

    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "status")] == [
        ("new → messaged", "panel"), ("messaged → rejected", "telegram:Hubert")]


def test_ten_sam_status_drugi_raz_nie_zasmieca_historii():
    """Przycisk zostaje pod wiadomością w Telegramie i klika się go odruchowo.
    Historia ma pokazywać zmiany, nie kliknięcia."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "messaged"})
    _callback(lead_id, "messaged")
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
#  Kasowanie                                                                   #
# --------------------------------------------------------------------------- #
def test_kasowanie_wymaga_admina():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert client.delete(f"/api/admin/leads/{lead_id}").status_code in (401, 403)
    assert _lead(lead_id) is not None


def test_kasowanie_zabiera_historie_i_przypomnienia():
    """Zdarzenia i przypomnienia trzymają `lead_id` kluczem obcym, więc bez
    posprzątania dzieci Postgres odrzuciłby skasowanie wiersza."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}/reminders", headers=ADMIN,
                json={"text": "dopytać", "due_in_days": 3})

    assert client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN).status_code == 200

    assert _lead(lead_id) is None
    assert _zdarzenia(lead_id) == []
    s = SessionLocal()
    try:
        assert s.query(LeadReminder).filter(LeadReminder.lead_id == lead_id).count() == 0
    finally:
        s.close()


def test_po_skasowaniu_ten_sam_mail_wraca_jako_nowy():
    """Sedno tego, po co kasowanie w ogóle jest. `leads.email` jest unikalny,
    więc gdyby wiersz tylko chował się w panelu, człowiek, którego adresem ktoś
    testował formularz, do końca świata wracałby jako „zgłasza się 2. raz"."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN)

    odp = _wyslij(dane).json()
    assert odp["new"] is True
    lead = _lead(odp["id"])
    assert lead.applications == 1 and lead.status == "new"


def test_kasowanie_nie_rusza_zamowien():
    """Kasujemy notatkę działu o człowieku, nie jego historię płatności."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    s = SessionLocal()
    tr = Trader(email=dane["email"], password_hash=auth.hash_password("haslo1234"),
                full_name="Jan Kowalski", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    s.add(Order(trader_id=tr.id, product_key="eval-100k", amount_usd=549.0, status="paid"))
    s.commit(); trader_id = tr.id; s.close()

    client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN)

    s = SessionLocal()
    try:
        assert s.get(Trader, trader_id) is not None
        assert s.query(Order).filter(Order.trader_id == trader_id).count() == 1
    finally:
        s.close()


def test_kasowanie_nieznanego_leada_to_404():
    assert client.delete("/api/admin/leads/99999999", headers=ADMIN).status_code == 404


# --------------------------------------------------------------------------- #
#  Przypomnienia (cron)                                                        #
# --------------------------------------------------------------------------- #
def _cofnij(lead_id, *, od_zgloszenia=None, od_kontaktu=None, minut=None):
    """Postarza lead, żeby test nie musiał czekać trzech dni.

    `minut` jest osobno od `od_zgloszenia`, bo automatyczny mail i nudge „nikt
    nie wziął" liczą się w minutach, a powody z łańcucha reguł w dniach.
    """
    s = SessionLocal()
    try:
        lead = s.get(Lead, lead_id)
        teraz = datetime.now(timezone.utc)
        if od_zgloszenia is not None:
            lead.created_at = teraz - timedelta(days=od_zgloszenia)
        if minut is not None:
            lead.created_at = teraz - timedelta(minutes=minut)
        if od_kontaktu is not None:
            lead.contacted_at = teraz - timedelta(days=od_kontaktu)
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
    # Stary lead bez właściciela łapie DWA sygnały: nudge „nikt nie wziął"
    # (skala minut, sam push) i dzienny powód no_contact (czat + push).
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == [
        "unclaimed", "no_contact"]
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
    # Po jednym wpisie na powód (unclaimed + no_contact), nie po jednym na przebieg.
    assert len(_zdarzenia(lead_id, "reminder")) == 2


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


def test_przypomnienie_o_wiadomosci_bez_ciagu_dalszego():
    """Napisaliśmy i cisza. To najczęstszy stan przy kontakcie przez Telegram —
    i jedyny, w którym samo mijanie czasu jest powodem, żeby wrócić do tematu."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "messaged"})
    _cofnij(lead_id, od_zgloszenia=30, od_kontaktu=14)

    _cron()
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["stalled"]


def test_zamkniety_lead_nie_wraca_w_przypomnieniach():
    """`no_reply` to decyzja, że przestajemy pisać. Przypomnienie o takim leadzie
    kazałoby napisać jeszcze raz do kogoś, kto już dwa razy nie odpowiedział."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "no_reply"})
    _cofnij(lead_id, od_zgloszenia=30, od_kontaktu=14)

    _cron()
    assert _zdarzenia(lead_id, "reminder") == []


# --------------------------------------------------------------------------- #
#  Mail, którego nikt nie kliknął (backstop crona)                             #
# --------------------------------------------------------------------------- #
# Lead zgłoszony w nocy czekał do rana, bo mail wychodził wyłącznie z ręki.
# Automat jest ZABEZPIECZENIEM, nie kanałem: odzywa się dopiero, gdy przez
# godzinę nikt nie usiadł do tematu, i tylko do kogoś, kto nie zostawił
# handle'a — reszta ma dostać wiadomość tam, gdzie jej oczekuje.
@pytest.fixture
def poczta(monkeypatch):
    """Włącza kanał mailowy i podstawia transport. Bez tej fikstury `is_enabled()`
    jest fałszem, więc POZOSTAŁE testy w tym pliku automatu nie ruszają."""
    for pole, wartosc in (("smtp_host", "smtp.probe.test"),
                          ("lead_mail_from", "Forex Passing <desk@forexpassing.test>"),
                          ("lead_telegram_channel_url", "https://t.me/probe_channel"),
                          ("sms_telegram_url", "https://t.me/probe_desk")):
        monkeypatch.setattr(lead_mail.settings, pole, wartosc)
    poszly = []
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    return poszly


def _bez_handlea(**nadpisz):
    return _wyslij(_zgloszenie(telegram=None, **nadpisz)).json()["id"]


def test_lead_bez_handlea_dostaje_mail_sam(poczta):
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=90)

    assert _cron().json()["mailed"] == 1
    assert len(poczta) == 1
    # Autor w historii mówi działowi, że to nie był człowiek — inaczej „E-mail
    # sent" w karcie wygląda na czyjąś pracę i nikt do tego leada nie wraca.
    assert [z.actor for z in _zdarzenia(lead_id, "email")] == ["cron:unclaimed"]


def test_czlowiek_ma_pierwszy_strzal(poczta):
    """Nudge na dział idzie po 30 minutach, mail dopiero po godzinie. Gdyby oba
    padały naraz, automat odbierałby działowi szansę odezwania się pierwszym."""
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=35)

    _cron()
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["unclaimed"]
    assert poczta == []


def test_lead_z_handlem_czeka_na_czlowieka(poczta):
    """Kto zostawił handle, ma dostać wiadomość na Telegramie. Mail z prośbą
    „napisz do nas" byłby odesłaniem go tam, gdzie sam już zostawił adres."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _cofnij(lead_id, minut=90)

    _cron()
    assert poczta == []


def test_wziety_lead_nie_dostaje_automatu(poczta):
    """Automat reaguje na CISZĘ, nie na czas. Ktoś, kto już wziął leada, może
    właśnie z nim rozmawiać."""
    lead_id = _bez_handlea()
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"owner": "bartek"})
    _cofnij(lead_id, minut=90)

    _cron()
    assert poczta == []


def test_odrzucony_nie_dostaje_automatu(poczta):
    """Wersję dla odrzutu wysyła się świadomie, z ręki. Automat, który sam
    zawiadamia o odmowie, to nie jest decyzja, którą chce się oddać cronowi."""
    lead_id = _bez_handlea(outcome="not_qualified")
    _cofnij(lead_id, minut=90)

    _cron()
    assert poczta == []


def test_automat_nie_wysyla_drugi_raz(poczta):
    """Cron chodzi co kilka minut, a ten sam tekst drugi raz w jednej skrzynce
    czyta się jak autoresponder."""
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=90)

    _cron(); _cron(); _cron()
    assert len(poczta) == 1


def test_automat_nie_udaje_ze_ktos_napisal(poczta):
    """Status zostaje na „new". Przestawienie go na „messaged" zdjęłoby leada
    z listy do obdzwonienia i uciszyło powód `no_contact` — a rozmowy z
    człowiekiem dalej nie było."""
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=90)

    _cron()
    assert _lead(lead_id).status == "new"


def test_reczny_mail_zdejmuje_leada_automatowi(poczta):
    """Najgorszy możliwy dublet: ktoś pisze z ręki, a godzinę później automat
    dokłada to samo do tej samej skrzynki. Wtedy mail przestaje wyglądać na
    czytany przez człowieka, czyli traci jedyne, co ma do powiedzenia."""
    lead_id = _bez_handlea()
    assert client.post(f"/api/admin/leads/{lead_id}/email",
                       headers=ADMIN).status_code == 200
    _cofnij(lead_id, minut=90)

    _cron()
    assert len(poczta) == 1
    assert [z.actor for z in _zdarzenia(lead_id, "email")] == ["panel"]


def test_cofniety_status_nie_odblokowuje_automatu(poczta):
    """Drugi zamek, na wypadek gdyby pierwszy puścił. Status chodzi tam i z
    powrotem („messaged" → „new", gdy ktoś uzna, że kontakt się nie liczy),
    a sam warunek `niczyj` uznałby takiego leada za nietkniętego. Trzyma go
    dopiero wpis w historii, którego cofnięcie statusu nie kasuje."""
    lead_id = _bez_handlea()
    client.post(f"/api/admin/leads/{lead_id}/email", headers=ADMIN)
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "new"})
    _cofnij(lead_id, minut=90)

    _cron()
    assert len(poczta) == 1


def test_automat_nie_siega_po_sms(poczta, monkeypatch):
    """Mail nic nie kosztuje, SMS kosztuje i jest zgodą, której nikt świadomie
    nie wydał — więc drabina `_kontakt_zastepczy` NIE obowiązuje w cronie,
    mimo że lead ma numer w bazie."""
    wyslane_sms = []
    monkeypatch.setattr(sms, "wyslij", lambda *a, **kw: (wyslane_sms.append(a), (True, ""))[1])
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=90)

    _cron()
    assert len(poczta) == 1
    assert wyslane_sms == []


def test_bez_konfiguracji_automat_milczy():
    """Bez fikstury `poczta` kanał jest wyłączony — cron ma wtedy przejść
    obojętnie, a nie wywrócić się na leadzie bez handle'a."""
    lead_id = _bez_handlea()
    _cofnij(lead_id, minut=90)

    assert _cron().status_code == 200
    assert _zdarzenia(lead_id, "email") == []


# --------------------------------------------------------------------------- #
#  Kto się zajął leadem                                                        #
# --------------------------------------------------------------------------- #
def test_przycisk_bierze_leada():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim")

    lead = _lead(lead_id)
    assert lead.owner == "Hubert" and lead.owner_at is not None
    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "claim")] == [
        ("taken by Hubert", "telegram:Hubert")]


def test_przejecie_leada_nie_wymaga_zgody_wlasciciela(_srodowisko):
    """Kanał czyta wyłącznie zespół, więc nie ma tu przed kim bronić leada.
    Blokada kosztowała dokładnie tyle, ile trwało czekanie, aż nieobecny
    właściciel kliknie „oddaję" — a historia i tak zapisuje, spod kogo poszedł."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Hubert")

    _callback(lead_id, "claim", kto="Bartek")

    assert _lead(lead_id).owner == "Bartek"
    assert _srodowisko["answer"][-1] == "Przejęte od Hubert"
    # Karta musi się przepisać, inaczej pod alertem dalej stoi poprzednie imię.
    assert "Bartek" in _srodowisko["edit"][-1][2]
    assert [z.detail for z in _zdarzenia(lead_id, "claim")] == [
        "taken by Hubert", "taken by Bartek from Hubert"]


def test_wlasciciel_klikajacy_biore_drugi_raz_nie_zasmieca_historii(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Hubert")
    ile_przepisan = len(_srodowisko["edit"])

    _callback(lead_id, "claim", kto="Hubert")

    assert _srodowisko["answer"][-1] == "Już to masz"
    # Nic się nie zmieniło — nie ma czego przepisywać.
    assert len(_srodowisko["edit"]) == ile_przepisan
    assert len(_zdarzenia(lead_id, "claim")) == 1


def test_oddaje_tylko_wlasciciel(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Hubert")

    _callback(lead_id, "release", kto="Bartek")
    assert _lead(lead_id).owner == "Hubert"
    assert "Hubert" in _srodowisko["answer"][-1]

    _callback(lead_id, "release", kto="Hubert")
    lead = _lead(lead_id)
    assert lead.owner is None and lead.owner_at is None
    assert [z.detail for z in _zdarzenia(lead_id, "claim")] == [
        "taken by Hubert", "released by Hubert"]


def test_klikniecie_statusu_bierze_niczyjego_leada():
    """Alerty sprzed tej zmiany mają pod sobą same statusy, bez „Biorę".
    Bez tego zostawałyby na zawsze bez właściciela."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "messaged", kto="Bartek")

    lead = _lead(lead_id)
    assert (lead.owner, lead.status) == ("Bartek", "messaged")


def test_status_nie_podbiera_cudzego_leada():
    """Przejęcie jest osobnym, świadomym kliknięciem. Odklikanie statusu pod
    cudzą kartą to co innego niż zabranie leada i nie ma go zabierać."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Hubert")
    _callback(lead_id, "no_reply", kto="Bartek")

    lead = _lead(lead_id)
    assert lead.owner == "Hubert" and lead.status == "no_reply"


def test_klawiatura_pokazuje_statusy_dopiero_po_wzieciu():
    """Dwa etapy: dopóki leada nikt nie ma, jedyne sensowne kliknięcie to
    „biorę". Statusy pod niczyim leadem nie mówią, kto pisał."""
    from app import telegram as tg

    przed = tg.lead_keyboard(1)
    assert len(przed["inline_keyboard"]) == 1
    assert przed["inline_keyboard"][0][0]["callback_data"] == "lead:1:claim"

    po = tg.lead_keyboard(1, owner="Hubert", status="messaged", tier="high")
    wiersze = po["inline_keyboard"]
    # Statusy po dwa w rzędzie, potem ocena, potem przejęcie i oddanie.
    assert len(wiersze) == 4
    assert [g["callback_data"] for g in wiersze[0]] == [
        "lead:1:messaged", "lead:1:replied"]
    assert [g["callback_data"] for g in wiersze[1]] == [
        "lead:1:no_reply", "lead:1:rejected"]
    assert [g["callback_data"] for g in wiersze[2]] == [
        "lead:1:tier_high", "lead:1:tier_warm", "lead:1:tier_cold"]
    assert [g["callback_data"] for g in wiersze[3]] == ["lead:1:claim", "lead:1:release"]
    # Kropka pokazuje, co jest ustawione teraz — inaczej po przepisaniu
    # wiadomości nie widać, czy status w ogóle się zapisał.
    assert [g["text"].startswith("• ") for g in wiersze[0]] == [True, False]
    assert wiersze[2][0]["text"].startswith("• ")


def test_alert_niesie_kto_sie_zajal(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim")
    _, _, tekst = _srodowisko["edit"][-1]
    assert "Zajmuje się" in tekst and "Hubert" in tekst


# --------------------------------------------------------------------------- #
#  Korekta oceny z formularza                                                  #
# --------------------------------------------------------------------------- #
def test_ocena_z_telefonu_nadpisuje_ocene_z_ankiety():
    """Ankieta punktuje deklaracje sprzed telefonu. Historia trzyma jedno
    i drugie, bo rozjazd jest informacją o formularzu, nie tylko o leadzie."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]     # ankieta dała „high"
    _callback(lead_id, "tier_cold")

    assert _lead(lead_id).tier == "cold"
    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "tier")] == [
        ("high → cold", "telegram:Hubert")]


def test_ta_sama_ocena_drugi_raz_nie_zasmieca_historii():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "tier_high")
    assert _zdarzenia(lead_id, "tier") == []


def test_nieznana_ocena_z_przycisku_nic_nie_rusza(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "tier_platinum")
    assert _lead(lead_id).tier == "high"
    assert _zdarzenia(lead_id, "tier") == []


def test_panel_zapisuje_wlasciciela_i_ocene():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                      json={"owner": "Bartek", "tier": "warm"})

    assert odp.json()["owner"] == "Bartek" and odp.json()["tier"] == "warm"
    assert [z.actor for z in _zdarzenia(lead_id, "claim")] == ["panel"]
    assert [z.detail for z in _zdarzenia(lead_id, "tier")] == ["high → warm"]


def test_panel_pustym_wlascicielem_zdejmuje_leada():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"owner": "Bartek"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"owner": ""})
    assert _lead(lead_id).owner is None


def test_zapis_notatki_nie_zdejmuje_wlasciciela():
    """`owner=None` znaczy „nie ruszaj". Gdyby znaczyło „skasuj", dopisanie
    notatki z panelu odbierałoby leada temu, kto go wziął."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"owner": "Bartek"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "oddzwonić"})
    assert _lead(lead_id).owner == "Bartek"


def test_panel_odrzuca_nieznana_ocene():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"tier": "platinum"})
    assert odp.status_code == 400 and _lead(lead_id).tier == "high"


# --------------------------------------------------------------------------- #
#  Powód przegranej                                                            #
# --------------------------------------------------------------------------- #
def test_kody_powodow_z_przyciskow_sa_te_same_co_w_modelu():
    """`telegram.py` jest transportem i celowo nie importuje modeli, więc obie
    listy są przepisane ręcznie. Rozjazd kończy się przyciskiem, po którym
    walidacja odmawia — i nikt się nie dowie, bo dymek mówi tylko „nieznany
    powód"."""
    from app import telegram as tg
    from app.models import LOST_REASONS

    assert tuple(k for _, k in tg.LOST_REASON_BUTTONS) == LOST_REASONS


def test_klawiatura_pyta_o_powod_dopiero_po_odrzuceniu():
    """Trzeci stopień: rząd wchodzi, gdy lead odpadł i powodu jeszcze nie ma,
    i znika po wybraniu — wybrany powód widać już w treści karty."""
    from app import telegram as tg

    zwykly = tg.lead_keyboard(1, owner="Hubert", status="messaged")
    assert not any("why_" in g["callback_data"] for w in zwykly["inline_keyboard"]
                   for g in w)

    pyta = tg.lead_keyboard(1, owner="Hubert", status="rejected")
    # Pytanie stoi NAD statusami — na telefonie widać kilka pierwszych guzików.
    assert [g["callback_data"] for g in pyta["inline_keyboard"][0]] == [
        "lead:1:why_price", "lead:1:why_competitor"]

    po_wyborze = tg.lead_keyboard(1, owner="Hubert", status="rejected",
                                  lost_reason="price")
    assert not any("why_" in g["callback_data"]
                   for w in po_wyborze["inline_keyboard"] for g in w)


def test_przycisk_zapisuje_powod_i_pokazuje_go_na_karcie(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "rejected")
    _callback(lead_id, "why_price")

    assert _lead(lead_id).lost_reason == "price"
    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "status")] == [
        ("new → rejected", "telegram:Hubert"), ("lost: price", "telegram:Hubert")]
    assert "Za drogo" in _srodowisko["edit"][-1][2]


def test_powod_bez_odrzucenia_nic_nie_rusza(_srodowisko):
    """Realny scenariusz: ktoś klika stary alert, na którym lead stał na
    „odpada", a ten w międzyczasie odpisał i wrócił do gry."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "why_price")

    assert _lead(lead_id).lost_reason is None
    assert "nie jest odrzucony" in _srodowisko["answer"][-1]


def test_nieznany_powod_z_przycisku_nic_nie_rusza():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "rejected")
    _callback(lead_id, "why_bo_tak")
    assert _lead(lead_id).lost_reason is None


def test_ten_sam_powod_drugi_raz_nie_zasmieca_historii():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "rejected")
    _callback(lead_id, "why_ghosted")
    _callback(lead_id, "why_ghosted")
    assert [z.detail for z in _zdarzenia(lead_id, "status")] == [
        "new → rejected", "lost: ghosted"]


def test_powrot_leada_do_gry_kasuje_powod():
    """Inaczej raport policzyłby jako straconego kogoś, do kogo dział właśnie
    znowu pisze."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "rejected")
    _callback(lead_id, "why_price")
    _callback(lead_id, "replied")
    assert _lead(lead_id).lost_reason is None


def test_przejscie_miedzy_odrzuceniem_a_koszem_zachowuje_powod():
    """`rejected` i `burned` to dwa odcienie tej samej decyzji, a nie nowa —
    powód nie ma się przy tym gubić."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "rejected")
    _callback(lead_id, "why_spam")
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "burned"})
    assert _lead(lead_id).lost_reason == "spam"


def test_panel_zapisuje_powod_razem_z_odrzuceniem():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                      json={"status": "rejected", "lost_reason": "competitor"})

    assert odp.status_code == 200 and odp.json()["lost_reason"] == "competitor"
    assert _lead(lead_id).lost_reason == "competitor"
    assert [z.detail for z in _zdarzenia(lead_id, "status")] == [
        "new → rejected", "lost: competitor"]


def test_panel_pustym_powodem_zdejmuje_powod():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                json={"status": "rejected", "lost_reason": "other"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"lost_reason": ""})
    assert _lead(lead_id).lost_reason is None


def test_panel_odrzuca_nieznany_powod():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                      json={"status": "rejected", "lost_reason": "za drogo"})
    assert odp.status_code == 400 and _lead(lead_id).lost_reason is None


def test_panel_nie_przyjmuje_powodu_dla_leada_w_grze():
    """Powód bez przegranej to wiersz, którego raport nie ma gdzie policzyć."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                      json={"status": "replied", "lost_reason": "price"})
    assert odp.status_code == 400 and _lead(lead_id).lost_reason is None


# --------------------------------------------------------------------------- #
#  Notatki z odpowiedzi na kanale                                              #
# --------------------------------------------------------------------------- #
def _odpowiedz(message_id, tekst, autor="Hubert", pole="author_signature",
               czat=CZAT_PLATNY):
    """Post na kanale będący odpowiedzią na alert o leadzie.

    `czat` to grupa, w której wisi karta — dopasowanie leada idzie po parze
    (message_id, czat), bo numer wiadomości jest unikalny w czacie, a nie u bota.
    """
    wiadomosc = {"message_id": 7001, "chat": {"id": int(czat)}, "text": tekst,
                 "reply_to_message": {"message_id": message_id}}
    if pole == "author_signature":
        wiadomosc["author_signature"] = autor
    elif pole == "from":
        wiadomosc["from"] = {"first_name": autor}
    return client.post("/api/telegram/webhook",
                       headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_WEBHOOKA},
                       json={"channel_post": wiadomosc})


def test_ingest_zapamietuje_message_id_alertu():
    """Bez tego nie ma czego dopasować: odpowiedź niesie wyłącznie id
    wiadomości, na którą odpowiedziano."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert _lead(lead_id).tg_message_id is not None


def test_odpowiedz_na_kanale_zapisuje_notatke(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    mid = _lead(lead_id).tg_message_id

    assert _odpowiedz(mid, "prosi o telefon po 18").status_code == 200

    assert _lead(lead_id).note == "Hubert: prosi o telefon po 18"
    assert [(z.detail, z.actor) for z in _zdarzenia(lead_id, "note")] == [
        ("prosi o telefon po 18", "telegram:Hubert")]
    # Notatka wchodzi do samego alertu — dział czyta kanał, nie panel.
    assert "prosi o telefon po 18" in _srodowisko["edit"][-1][2]


def test_druga_notatka_dopisuje_sie_pod_pierwsza():
    """Na kanale odpowiada kilka osób; druga uwaga nie ma prawa skasować
    pierwszej."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    mid = _lead(lead_id).tg_message_id
    _odpowiedz(mid, "nie odbiera", autor="Hubert")
    _odpowiedz(mid, "oddzwonił, chce 100k", autor="Bartek")

    assert _lead(lead_id).note.split("\n") == [
        "Hubert: nie odbiera", "Bartek: oddzwonił, chce 100k"]


def test_notatka_bez_podpisu_ma_autora_zastepczego():
    """Póki właściciel kanału nie włączy „Sign messages", posty są anonimowe."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _odpowiedz(_lead(lead_id).tg_message_id, "był kontakt", pole=None)
    assert _lead(lead_id).note == "kanał: był kontakt"


def test_odpowiedz_na_obca_wiadomosc_jest_pomijana(_srodowisko):
    _wyslij(_zgloszenie())
    ile = len(_srodowisko["edit"])
    assert _odpowiedz(999_999, "rozmowa o czymś innym").status_code == 200
    assert len(_srodowisko["edit"]) == ile


def test_pusta_odpowiedz_nie_tworzy_notatki():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _odpowiedz(_lead(lead_id).tg_message_id, "   ")
    assert _lead(lead_id).note is None


def test_notatka_nie_rosnie_bez_konca():
    """Wiadomość w Telegramie ma limit 4096 znaków. Przy przepełnieniu wypadają
    całe najstarsze linie, a nie połowa zdania."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    mid = _lead(lead_id).tg_message_id
    for i in range(12):
        _odpowiedz(mid, f"{i:02d} " + "x" * 480)

    notatka = _lead(lead_id).note
    assert len(notatka) <= 4000
    assert notatka.split("\n")[-1].startswith("Hubert: 11 ")
    assert not notatka.startswith("Hubert: 00 ")     # najstarsze wypadły w całości


# --------------------------------------------------------------------------- #
#  Zaplanowane przypomnienia                                                   #
# --------------------------------------------------------------------------- #
def _utc(d):
    """Kolumny dat są bez strefy, `datetime.now(timezone.utc)` ze strefą —
    odjęcie jednego od drugiego wywraca się na TypeError."""
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _zaplanuj(lead_id, **payload):
    return client.post(f"/api/admin/leads/{lead_id}/reminders", headers=ADMIN,
                       json={"text": "oddzwonić", **payload})


def _przypomnienia(lead_id):
    s = SessionLocal()
    try:
        return s.query(LeadReminder).filter(LeadReminder.lead_id == lead_id) \
                .order_by(LeadReminder.id).all()
    finally:
        s.close()


def _przesun_termin(reminder_id, dni):
    s = SessionLocal()
    try:
        r = s.get(LeadReminder, reminder_id)
        r.due_at = datetime.now(timezone.utc) - timedelta(days=dni)
        s.commit()
    finally:
        s.close()


def test_przypomnienie_wymaga_terminu_i_tresci():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert _zaplanuj(lead_id).status_code == 400                    # bez terminu
    assert _zaplanuj(lead_id, text="  ", due_in_days=3).status_code == 400
    assert _zaplanuj(lead_id, due_in_days=3, repeat_days=0).status_code == 400
    assert _zaplanuj(999_999, due_in_days=3).status_code == 404


def test_przypomnienie_wymaga_admina():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}/reminders",
                      json={"text": "x", "due_in_days": 1})
    assert odp.status_code in (401, 403)


def test_termin_w_dniach_od_teraz():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = _zaplanuj(lead_id, due_in_days=5).json()
    kiedy = datetime.fromisoformat(odp["due_at"])
    assert 4 <= (kiedy - datetime.now(timezone.utc)).days <= 5


def test_zaplanowane_idzie_dopiero_po_terminie(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="zapytać o decyzję", due_in_days=5).json()["id"]

    _cron()
    assert _srodowisko["przypomnienie"] == []

    _przesun_termin(rid, 1)
    _cron()
    assert "zapytać o decyzję" in _srodowisko["przypomnienie"][-1]
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == [
        "planned: zapytać o decyzję"]


def test_jednorazowe_gasnie_po_wyslaniu(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="jednorazowe", due_in_days=1).json()["id"]
    _przesun_termin(rid, 1)

    _cron(); _cron()

    r = _przypomnienia(lead_id)[0]
    assert (r.active, r.sent_count) == (False, 1)
    assert sum("jednorazowe" in t for t in _srodowisko["przypomnienie"]) == 1


def test_cykliczne_przesuwa_termin_zamiast_gasnac(_srodowisko):
    """Cykl ma chodzić, dopóki ktoś go nie wyłączy — inaczej „update co tydzień"
    byłby jednorazowym powiadomieniem."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="update konta", due_in_days=7, repeat_days=7).json()["id"]
    _przesun_termin(rid, 1)

    _cron()
    r = _przypomnienia(lead_id)[0]
    assert r.active is True and r.sent_count == 1
    assert (_utc(r.due_at) - datetime.now(timezone.utc)).days >= 6

    _cron()                                   # termin jest w przyszłości
    assert _przypomnienia(lead_id)[0].sent_count == 1

    _przesun_termin(rid, 1)
    _cron()
    assert _przypomnienia(lead_id)[0].sent_count == 2
    assert "2. raz" in _srodowisko["przypomnienie"][-1]


def test_wylaczone_przypomnienie_nie_wychodzi(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="nieaktualne", due_in_days=1).json()["id"]
    _przesun_termin(rid, 1)

    odp = client.post(f"/api/admin/leads/{lead_id}/reminders/{rid}/cancel", headers=ADMIN)
    assert odp.status_code == 200
    _cron()
    assert not any("nieaktualne" in t for t in _srodowisko["przypomnienie"])
    # Wiersz zostaje — przy cyklicznym widać potem, ile razy zdążył pójść.
    assert len(_przypomnienia(lead_id)) == 1


def test_spalony_lead_laduje_w_koszu_i_gasi_przypomnienia(_srodowisko):
    """Spalenie leada = kosz: status 'burned' przechodzi, a aktywne przypomnienia
    gasną — cron nie ma prawa szturchać działu o człowieka, z którym koniec."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="dopytać spalonego", due_in_days=1).json()["id"]
    _przesun_termin(rid, 1)

    odp = client.post(f"/api/admin/leads/{lead_id}",
                      json={"status": "burned"}, headers=ADMIN)
    assert odp.status_code == 200 and odp.json()["status"] == "burned"

    assert _przypomnienia(lead_id)[0].active is False
    _cron()
    assert not any("dopytać spalonego" in t for t in _srodowisko["przypomnienie"])


def test_undo_przywraca_wylaczone_przypomnienie(_srodowisko):
    """Undo w panelu woła reactivate: wraca TEN SAM wiersz, z terminem
    i licznikiem — cancel niczego nie kasował."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="jednak dzwonić", due_in_days=1).json()["id"]
    client.post(f"/api/admin/leads/{lead_id}/reminders/{rid}/cancel", headers=ADMIN)
    assert _przypomnienia(lead_id)[0].active is False

    odp = client.post(f"/api/admin/leads/{lead_id}/reminders/{rid}/reactivate",
                      headers=ADMIN)
    assert odp.status_code == 200 and odp.json() == {"id": rid, "active": True}
    assert _przypomnienia(lead_id)[0].active is True

    _przesun_termin(rid, 1)
    _cron()
    assert any("jednak dzwonić" in t for t in _srodowisko["przypomnienie"])

    # Cudzego nie da się włączyć, tak samo jak wyłączyć.
    obcy = _wyslij(_zgloszenie()).json()["id"]
    assert client.post(f"/api/admin/leads/{obcy}/reminders/{rid}/reactivate",
                       headers=ADMIN).status_code == 404


def test_cudze_przypomnienie_nie_da_sie_wylaczyc():
    a = _wyslij(_zgloszenie()).json()["id"]
    b = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(a, due_in_days=1).json()["id"]

    assert client.post(f"/api/admin/leads/{b}/reminders/{rid}/cancel",
                       headers=ADMIN).status_code == 404
    assert _przypomnienia(a)[0].active is True


def test_zakup_zaklada_cykl_updatow(_srodowisko):
    """Jednorazowe „kupił" załatwia moment, w którym trzeba przestać dzwonić
    jak do leada. Klient z opłaconym kontem potrzebuje kontaktu w kółko."""
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
    cykle = _przypomnienia(lead_id)
    assert len(cykle) == 1
    assert (cykle[0].kind, cykle[0].repeat_days, cykle[0].created_by) == ("bought", 7, "cron")

    _przesun_termin(cykle[0].id, 1)
    _cron()
    assert any("Update konta" in t for t in _srodowisko["przypomnienie"])
    assert _przypomnienia(lead_id)[0].active is True


def test_szczegoly_niosa_przypomnienia_a_lista_najblizszy_termin():
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _zaplanuj(lead_id, text="później", due_in_days=30)
    _zaplanuj(lead_id, text="wcześniej", due_in_days=2)

    d = client.get(f"/api/admin/leads/{lead_id}", headers=ADMIN).json()
    assert {r["text"] for r in d["reminders"]} == {"później", "wcześniej"}
    # Tabela pokazuje jeden termin, więc musi to być ten najbliższy.
    assert d["next_due"][:10] == _z_listy(lead_id)["next_due"][:10]
    kiedy = _utc(datetime.fromisoformat(d["next_due"]))
    assert 1 <= (kiedy - datetime.now(timezone.utc)).days <= 2


# --------------------------------------------------------------------------- #
#  Push o leadach na telefony działu                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def _pushy(monkeypatch):
    """Łapie notify.notify_admins zamiast słać web push. Pushe leadowe idą
    wyłącznie tą bramką (main._lead_push), więc lista niesie dokładnie to,
    co dostałby telefon — razem z deep-linkiem i tagiem."""
    zlapane: list[dict] = []
    monkeypatch.setattr(notify, "notify_admins",
                        lambda event, title, body="", url="/admin", tag=None:
                        zlapane.append({"event": event, "title": title, "body": body,
                                        "url": url, "tag": tag}))
    return zlapane


def test_nowy_lead_pushuje_do_adminow(_pushy):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    p = next(p for p in _pushy if p["url"] == f"/admin?lead={lead_id}")
    assert p["title"] == "New lead: Jan Kowalski"
    assert p["tag"] == f"lead-{lead_id}"
    assert "high" in p["body"] and "questionnaire" in p["body"]


def test_push_niekwalifikowanego_mowi_skad_i_dlaczego(_pushy):
    a = _wyslij(_zgloszenie(outcome="not_qualified", quality=None)).json()["id"]
    assert next(p for p in _pushy if p["url"] == f"/admin?lead={a}")["body"] \
        == "failed the questionnaire"
    _pushy.clear()
    b = _wyslij(_zgloszenie(outcome="not_qualified", source="safe",
                            quality=None)).json()["id"]
    assert next(p for p in _pushy if p["url"] == f"/admin?lead={b}")["body"] \
        == "safe page lead — warm up"


def test_przycisk_telegrama_pushuje_kto_co_zrobil(_pushy):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _pushy.clear()
    _callback(lead_id, "claim")
    assert any(p["title"] == "Hubert: took the lead" for p in _pushy)
    _pushy.clear()
    _callback(lead_id, "messaged")
    assert any(p["title"] == "Hubert: marked messaged" for p in _pushy)
    # Kliknięcie w to samo drugi raz nic nie zmienia — i nie brzęczy.
    _pushy.clear()
    _callback(lead_id, "messaged")
    assert _pushy == []


def test_panel_pushuje_status_ale_nie_notatke(_pushy):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _pushy.clear()
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"note": "notatka"})
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"tier": "cold"})
    assert _pushy == []          # notatka i ocena to szum, nie sygnał
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"status": "messaged"})
    assert any("marked messaged" in p["title"] for p in _pushy)


def _postarz_o_minuty(lead_id, minuty):
    s = SessionLocal()
    try:
        s.get(Lead, lead_id).created_at = \
            datetime.now(timezone.utc) - timedelta(minutes=minuty)
        s.commit()
    finally:
        s.close()


def test_nudge_o_niewzietym_leadzie_dokladnie_raz(_pushy, _srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _postarz_o_minuty(lead_id, 45)
    _pushy.clear()

    _cron(); _cron()

    nudge = [p for p in _pushy if p["title"].startswith("Unclaimed lead")]
    assert len(nudge) == 1 and nudge[0]["url"] == f"/admin?lead={lead_id}"
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["unclaimed"]
    # Sam push, bez wpisu na czacie — karta leada i tak wisi na kanale.
    assert _srodowisko["przypomnienie"] == []


def test_nudge_omija_wzietych_swiezych_i_niekwalifikowanych(_pushy):
    wziety = _wyslij(_zgloszenie()).json()["id"]
    _callback(wziety, "claim")
    _postarz_o_minuty(wziety, 45)
    swiezy = _wyslij(_zgloszenie()).json()["id"]
    odpad = _wyslij(_zgloszenie(outcome="not_qualified", quality=None)).json()["id"]
    _postarz_o_minuty(odpad, 45)
    _pushy.clear()

    _cron()

    assert not any(p["title"].startswith("Unclaimed") for p in _pushy)
    for lid in (wziety, swiezy, odpad):
        assert "unclaimed" not in [z.detail for z in _zdarzenia(lid, "reminder")]


def test_zaplanowane_przypomnienie_pushuje(_pushy):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    rid = _zaplanuj(lead_id, text="oddzwonić po weekendzie", due_in_days=1).json()["id"]
    _przesun_termin(rid, 1)
    _pushy.clear()

    _cron()

    p = next(p for p in _pushy if p["title"].startswith("Reminder:"))
    assert "oddzwonić po weekendzie" in p["title"]
    assert p["url"] == f"/admin?lead={lead_id}" and p["body"] == "Jan Kowalski"


def test_sweep_z_ruchu_chodzi_raz_na_okno(monkeypatch):
    """Middleware ruchu przebiega follow-upy najwyżej co LEADS_SWEEP_MIN minut:
    znacznik w app_settings commitowany PRZED robotą, więc drugi request w tym
    samym oknie wychodzi bez przebiegu."""
    from app import main as app_main
    from app.models import AppSetting

    u = get_settings()
    monkeypatch.setattr(u, "leads_on_traffic", True, raising=False)
    ile = {"n": 0}
    monkeypatch.setattr(app_main, "_lead_followups",
                        lambda *a, **k: ile.__setitem__("n", ile["n"] + 1))
    s = SessionLocal()
    try:
        row = s.get(AppSetting, "leadbot_last_sweep")
        if row:
            s.delete(row)
        s.commit()
    finally:
        s.close()

    client.get("/api/public/stats")
    client.get("/api/public/stats")
    assert ile["n"] == 1


# --------------------------------------------------------------------------- #
#  Normalizacja telefonu przy przyjęciu                                        #
# --------------------------------------------------------------------------- #
def test_ingest_sklada_e164_i_prostuje_iso_z_prefiksu():
    """Landing przysyła „+48 601 234 567" i osobno ISO zgadnięte ze strefy
    czasowej urządzenia. Gdy się kłócą, prawdę mówi prefiks."""
    lead = _lead(_wyslij(_zgloszenie(phone="+48 601 234 567",
                                     phoneIso="US")).json()["id"])
    assert lead.phone == "+48601234567" and lead.phone_iso == "PL"


def test_ingest_00_to_tez_prefiks_a_bez_prefiksu_nie_zgadujemy():
    a = _lead(_wyslij(_zgloszenie(phone="0048 601 234 567", phoneIso="")).json()["id"])
    assert a.phone == "+48601234567" and a.phone_iso == "PL"
    b = _lead(_wyslij(_zgloszenie(phone="601 234 567", phoneIso="PL")).json()["id"])
    assert b.phone == "601 234 567" and b.phone_iso == "PL"


def test_iso_from_e164_dzielone_kody_i_najdluzsze_dopasowanie():
    from app.countries import iso_from_e164
    assert iso_from_e164("+48 601 234 567") == "PL"
    assert iso_from_e164("+12025550123") == "US"      # +1 dzielony: wygrywa primary
    assert iso_from_e164("+79161234567") == "RU"      # +7 dzielony: wygrywa primary
    assert iso_from_e164("+35561234567") == "AL"      # +355, nie krótsze dopasowania
    assert iso_from_e164("0044 7911 123456") == "GB"
    assert iso_from_e164("601234567") is None         # brak prefiksu = nie zgadujemy


# --------------------------------------------------------------------------- #
#  Tożsamość działu: parowanie Telegrama + preferencje pushy                   #
# --------------------------------------------------------------------------- #
def _admin_z_tokenem(prefix="desk"):
    """Konto admina + Bearer — endpointy /api/me/* wymagają tożsamości,
    a nagłówek X-Admin-Token jej nie niesie."""
    s = SessionLocal()
    tr = Trader(email=f"{prefix}{next(LICZNIK)}@k",
                password_hash=auth.hash_password("haslo1234"),
                full_name="Desk", referral_code=auth.secrets.token_hex(3),
                is_admin=True)
    s.add(tr)
    s.commit()
    email, tid = tr.email, tr.id
    s.close()
    tok = client.post("/api/auth/login",
                      json={"email": email, "password": "haslo1234"}).json()["token"]
    return email, tid, {"Authorization": f"Bearer {tok}"}


def test_sparowany_admin_podpisuje_sie_mailem(_srodowisko):
    email, tid, naglowki = _admin_z_tokenem()
    # Przed sparowaniem status mówi wprost: nie połączono.
    assert client.get("/api/me/telegram-link", headers=naglowki).json() == {
        "linked": False, "username": None}
    kod = client.post("/api/me/telegram-link", headers=naglowki).json()["code"]

    odp = client.post("/api/telegram/webhook",
                      headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_WEBHOOKA},
                      json={"message": {"chat": {"id": 777, "type": "private"},
                                        "from": {"id": 424242, "username": "gubi_desk",
                                                 "first_name": "Hubert"},
                                        "text": f"/start {kod.lower()}"}})
    assert odp.status_code == 200
    assert any("Linked as" in t for _c, t in _srodowisko["dm"])
    s = SessionLocal()
    try:
        tr = s.get(Trader, tid)
        assert (tr.telegram_user_id, tr.telegram_link_code) == ("424242", None)
        assert tr.telegram_username == "@gubi_desk"
    finally:
        s.close()
    # Panel odpytuje ten status po wydaniu kodu — „Connected as @…" na żywo.
    assert client.get("/api/me/telegram-link", headers=naglowki).json() == {
        "linked": True, "username": "@gubi_desk"}

    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Bartek", uid=424242)
    lead = _lead(lead_id)
    assert lead.owner == email
    assert [z.actor for z in _zdarzenia(lead_id, "claim")] == [f"telegram:{email}"]

    # Testowy DM = dowód, że połączenie bot→admin działa.
    _srodowisko["dm"].clear()
    assert client.post("/api/me/telegram-link/test",
                       headers=naglowki).json() == {"ok": True}
    assert _srodowisko["dm"] and _srodowisko["dm"][0][0] == "424242"
    assert email in _srodowisko["dm"][0][1]


def test_test_dm_bez_sparowania_to_400():
    _email, _tid, naglowki = _admin_z_tokenem()
    assert client.post("/api/me/telegram-link/test",
                       headers=naglowki).status_code == 400


def test_test_dm_do_bota_bez_czatu_mowi_co_zrobic(_srodowisko, monkeypatch):
    """„chat not found" = to konto nie pisało do BIEŻĄCEGO bota (np. po zmianie
    tokenu panelu). Parowanie jest całe, więc panel ma kazać kliknąć Start,
    a nie oddawać surowy komunikat Telegrama, po którym nie wiadomo, co robić."""
    email, tid, naglowki = _admin_z_tokenem()
    s = SessionLocal()
    try:
        s.get(Trader, tid).telegram_user_id = "424242"
        s.commit()
    finally:
        s.close()
    monkeypatch.setattr(telegram, "send_dm",
                        lambda czat, tekst, **kw: (False, "Bad Request: chat not found"))
    monkeypatch.setattr(telegram, "bot_username", lambda **kw: "fxpassingadmin_bot")
    odp = client.post("/api/me/telegram-link/test", headers=naglowki)
    assert odp.status_code == 502
    tresc = odp.json()["detail"]
    assert "@fxpassingadmin_bot" in tresc and "Start" in tresc
    assert email  # parowanie zostaje nietknięte


def test_niesparowany_zostaje_przy_imieniu_a_zly_kod_nie_paruje(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim", kto="Hubert", uid=999999)
    assert _lead(lead_id).owner == "Hubert"

    client.post("/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": SEKRET_WEBHOOKA},
                json={"message": {"chat": {"id": 778, "type": "private"},
                                  "from": {"id": 999999}, "text": "/start ZLYKOD"}})
    assert any("Unknown" in t for _c, t in _srodowisko["dm"])


def test_kod_parowania_tylko_dla_admina():
    s = SessionLocal()
    tr = Trader(email=f"user{next(LICZNIK)}@t",
                password_hash=auth.hash_password("haslo1234"),
                referral_code=auth.secrets.token_hex(3))
    s.add(tr)
    s.commit()
    email = tr.email
    s.close()
    tok = client.post("/api/auth/login",
                      json={"email": email, "password": "haslo1234"}).json()["token"]
    assert client.post("/api/me/telegram-link",
                       headers={"Authorization": f"Bearer {tok}"}).status_code == 404


def test_kasowanie_leada_zdejmuje_karte_z_kanalu(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    mid = _lead(lead_id).tg_message_id
    assert mid

    client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN)

    assert _srodowisko["delete"] == [mid]
    assert _lead(lead_id) is None


def test_reczne_bought_checkbox(_srodowisko):
    """Deal zamknięty poza sklepem: checkbox w tabeli robi z leada klienta —
    koniec dzwonienia jak do leada, start cyklu pilnowania jak przy zakupie."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    odp = client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN,
                      json={"bought": True}).json()
    assert odp["bought"] is True
    assert _z_listy(lead_id)["bought"] is True
    assert [z.detail for z in _zdarzenia(lead_id, "bought")] == ["marked bought"]

    # Follow-upy traktują jak zakup (bez kwoty — sklep go nie widział),
    # a nudge „nikt nie wziął" odpuszcza.
    _cofnij(lead_id, od_zgloszenia=5)
    _cron()
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["bought"]
    tekst = next(t for t in _srodowisko["przypomnienie"] if "KUPIŁ" in t)
    assert "$" not in tekst and "Oznaczone ręcznie" in tekst

    # Odznaczenie wraca do zwykłego leada i zostaje w historii.
    client.post(f"/api/admin/leads/{lead_id}", headers=ADMIN, json={"bought": False})
    assert _z_listy(lead_id)["bought"] is False
    assert [z.detail for z in _zdarzenia(lead_id, "bought")] == [
        "marked bought", "unmarked bought"]


def test_dzwonek_admina_niesie_zdarzenia_leadow():
    """Lista pod dzwoneczkiem (admin/inbox) agreguje kolejki — leady wchodzą
    do niej z historii lead_events, z lead_id do otwarcia karty jednym klikiem."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    _callback(lead_id, "claim")

    d = client.get("/api/admin/inbox", headers=ADMIN).json()
    wpisy = [i for i in d["items"] if i.get("lead_id") == lead_id]
    assert wpisy and all(i["type"] == "lead" and i["view"] == "leads" for i in wpisy)
    assert any(i["title"].startswith("New lead:") for i in wpisy)
    assert any("taken by" in i["body"] for i in wpisy)


def test_wyciszona_kategoria_nie_brzeczy_ale_dzwonek_zostaje(monkeypatch):
    from app import push as push_mod
    from app.models import Notification

    s = SessionLocal()
    cichy = Trader(email=f"adm{next(LICZNIK)}@k",
                   password_hash=auth.hash_password("haslo1234"),
                   referral_code=auth.secrets.token_hex(3), is_admin=True,
                   ui_prefs='{"admin_push":{"lead_new":false}}')
    glosny = Trader(email=f"adm{next(LICZNIK)}@s",
                    password_hash=auth.hash_password("haslo1234"),
                    referral_code=auth.secrets.token_hex(3), is_admin=True)
    s.add_all([cichy, glosny])
    s.commit()
    cichy_id, glosny_id = cichy.id, glosny.id
    s.close()

    dostali: list[int] = []
    monkeypatch.setattr(push_mod, "send_to_trader",
                        lambda tid, *a, **k: (dostali.append(tid), 1)[1])

    notify.notify_admins("lead_new", "New lead: X")
    assert glosny_id in dostali and cichy_id not in dostali

    # Inna kategoria brzęczy u obu — wyciszenie jest punktowe, nie globalne.
    notify.notify_admins("lead_action", "Ktoś: took the lead")
    assert cichy_id in dostali

    # Dzwonek dostaje wpis niezależnie od wyciszenia.
    s = SessionLocal()
    try:
        assert s.query(Notification).filter(Notification.trader_id == cichy_id,
                                            Notification.event == "lead_new").count() == 1
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Leady free: własna grupa i konto jednym kliknięciem                         #
# --------------------------------------------------------------------------- #
# Free i płatny to inny koszt i inni ludzie do obsługi, więc karty rozjeżdżają
# się na dwie grupy. Numer wiadomości jest unikalny W CZACIE, nie u bota —
# `tg_chat_id` na leadzie istnieje po to, żeby odpowiedź i kasowanie karty
# trafiały tam, gdzie ta karta naprawdę wisi.
def _produkt_free():
    s = SessionLocal()
    try:
        if not s.query(Product).filter(Product.key == "2step-25k").first():
            s.add(Product(key="2step-25k", label="2-Step 25K", account_size=25_000,
                          steps=2, price_usd=249, profit_target_p1=8, profit_target_p2=5,
                          max_daily_loss_pct=5, max_overall_loss_pct=10,
                          drawdown_type="trailing", min_trading_days=3,
                          profit_split_pct=80, max_lots=6, active=True))
            s.commit()
    finally:
        s.close()


def test_karta_leada_free_idzie_do_grupy_free(_srodowisko):
    """Landing obiecuje darmowe konto — te zgłoszenia obsługuje kto inny niż
    płatne, więc nie mogą wpadać do tej samej grupy."""
    lead_id = _wyslij(_zgloszenie(source="free-challenge")).json()["id"]

    assert _srodowisko["alert_czat"][-1] == CZAT_FREE
    assert _lead(lead_id).tg_chat_id == CZAT_FREE


def test_karta_leada_platnego_zostaje_w_grupie_dzialu(_srodowisko):
    lead_id = _wyslij(_zgloszenie()).json()["id"]

    assert _srodowisko["alert_czat"][-1] == CZAT_PLATNY
    assert _lead(lead_id).tg_chat_id == CZAT_PLATNY


def test_notatka_trafia_do_leada_z_tej_grupy_co_odpowiedz():
    """Sedno kolumny `tg_chat_id`. Telegram numeruje wiadomości osobno w każdym
    czacie, więc karta free i karta działu potrafią mieć ten sam `message_id` —
    bez filtra po czacie notatka z jednej grupy siadałaby na cudzym leadzie."""
    platny = _wyslij(_zgloszenie()).json()["id"]
    wolny = _wyslij(_zgloszenie(source="free-challenge")).json()["id"]
    kolizja = 4242
    s = SessionLocal()
    try:
        s.get(Lead, platny).tg_message_id = kolizja
        s.get(Lead, wolny).tg_message_id = kolizja
        s.commit()
    finally:
        s.close()

    _odpowiedz(kolizja, "przyszedł z darmówki", czat=CZAT_FREE)

    assert _lead(wolny).note == "Hubert: przyszedł z darmówki"
    assert _lead(platny).note in (None, "")


def test_notatka_do_karty_sprzed_kolumny_czatu_dalej_dziala():
    """Karty wysłane, zanim `tg_chat_id` istniało, mają je puste i wiszą
    w grupie działu. Odpowiedź na nie nie może przestać się zapisywać."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    s = SessionLocal()
    try:
        s.get(Lead, lead_id).tg_chat_id = None
        s.commit()
        mid = s.get(Lead, lead_id).tg_message_id
    finally:
        s.close()

    _odpowiedz(mid, "stara karta, nowa notatka")

    assert _lead(lead_id).note == "Hubert: stara karta, nowa notatka"


def test_przycisk_dopisuje_czat_staremu_leadowi(_srodowisko):
    """Callback niesie id leada, więc czat z kliknięcia jest pewny — to jedyne
    miejsce, gdzie wolno uzupełnić brakującą kolumnę bez zgadywania."""
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    s = SessionLocal()
    try:
        s.get(Lead, lead_id).tg_chat_id = None
        s.commit()
    finally:
        s.close()

    assert _callback(lead_id, "replied").status_code == 200
    assert _lead(lead_id).tg_chat_id == "-100123"


def test_kasowanie_zdejmuje_karte_z_zapamietanej_grupy(_srodowisko):
    """Kasowanie po samym `message_id` trafiłoby w grupę działu — kartę leada
    free trzeba zdjąć tam, gdzie ją powieszono."""
    lead_id = _wyslij(_zgloszenie(source="free-challenge")).json()["id"]
    mid = _lead(lead_id).tg_message_id

    client.delete(f"/api/admin/leads/{lead_id}", headers=ADMIN)

    assert _srodowisko["delete"] == [mid]
    assert _srodowisko["delete_czat"] == [CZAT_FREE]


def test_przypomnienie_idzie_do_tej_grupy_co_karta(_srodowisko):
    """Follow-upy chodzą do ludzi, a nie do leada — muszą trafiać do tej samej
    grupy, w której ci ludzie oglądają kartę."""
    wolny, platny = _zgloszenie(source="free-challenge"), _zgloszenie()
    for dane in (wolny, platny):
        _cofnij(_wyslij(dane).json()["id"], od_zgloszenia=5)

    assert _cron().status_code == 200

    # Baza żyje między testami, więc cron obsłuży też cudze zaległości — liczą
    # się przypomnienia o TYCH dwóch, rozpoznawane po mailu w treści.
    poszlo = list(zip(_srodowisko["przypomnienie"], _srodowisko["przypomnienie_czat"]))
    assert {c for t, c in poszlo if wolny["email"] in t} == {CZAT_FREE}
    assert {c for t, c in poszlo if platny["email"] in t} == {CZAT_PLATNY}


def test_darmowe_konto_z_panelu_zaklada_prawdziwy_challenge():
    """Ten przycisk nie rysuje niczego na próbę: powstaje trader, zamówienie
    grantowe i realne konto — dokładnie ta sama ścieżka co zakup."""
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]

    odp = client.post(f"/api/admin/leads/{lead_id}/free-account", headers=ADMIN)
    assert odp.status_code == 200, odp.text
    d = odp.json()
    assert d["granted"] is True and d["trader_created"] is True and d["login"]

    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == dane["email"]).one()
        konto = s.query(Account).filter(Account.trader_id == tr.id).one()
        assert konto.id == d["account_id"] and konto.product_key == "2step-25k"
        # Hasła nikt nie zna, więc mail z poświadczeniami niesie link do
        # ustawienia go — inaczej klient dostaje konto, do którego nie wejdzie.
        assert tr.must_set_password is True
    finally:
        s.close()

    assert [z.detail for z in _zdarzenia(lead_id, "granted")] == [
        f"2step-25k — login {d['login']}"]
    assert _z_listy(lead_id)["accounts"] == 1


def test_drugie_kliniecie_nie_daje_drugiego_konta():
    """Podwójny klik to podwójny koszt. Panel chowa przycisk po `accounts`,
    ale to serwer musi odmówić — panel bywa nieodświeżony."""
    _produkt_free()
    lead_id = _wyslij(_zgloszenie(source="free-challenge")).json()["id"]
    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 200

    odp = client.post(f"/api/admin/leads/{lead_id}/free-account", headers=ADMIN)
    assert odp.status_code == 409
    assert "Already granted" in odp.json()["detail"]
    assert _z_listy(lead_id)["accounts"] == 1


def test_stara_wyplata_nie_odbiera_prawa_do_darmowego_konta():
    """Kto kiedyś dostał od nas wypłatę, jest najlepszym kandydatem — a był
    jedynym, który nie mógł dostać darmowego challenge'a.

    Import ewidencji zakłada pod archiwalną wypłatę konto `source="grant"`
    (`payout_import.zapisz`), a adres bierze z CSV-ki, więc bywa PRAWDZIWY —
    filtr po `@imported.local` takiego wiersza nie łapie. Guard 409 i licznik
    `accounts`, po którym panel gasi przycisk, widziały w tym wręczony
    challenge. Człowiek czytał „account opened", choć niczego nie dostał: to my
    zapłaciliśmy jemu.
    """
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    csv = ("full_name,amount_usd,date,account_size,program,email,note\n"
           f"Jan Kowalski,1200,2026-05-04,25000,2step,{dane['email']},\n")
    assert client.post("/api/admin/payouts/import", headers=ADMIN,
                       json={"csv": csv, "commit": True}).json()["added"] == 1

    lead_id = _wyslij(dane).json()["id"]
    assert _z_listy(lead_id)["accounts"] == 0, "wiersz z ewidencji gasił przycisk w panelu"

    odp = client.post(f"/api/admin/leads/{lead_id}/free-account", headers=ADMIN)
    assert odp.status_code == 200, odp.text
    # Konto archiwalne zostaje na miejscu: to zapis księgowy pod wypłatę, a nie
    # duplikat do posprzątania. Od teraz liczy się tylko to wręczone.
    assert _z_listy(lead_id)["accounts"] == 1
    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == dane["email"]).one()
        assert s.query(Account).filter(Account.trader_id == tr.id).count() == 2
    finally:
        s.close()

    # ...i drugi klik dalej odmawia — wyjątek dotyczy archiwum, nie guardu.
    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 409


@pytest.fixture
def maile(monkeypatch):
    """Podstawka pod `send`, nie na nim — `send` w requeście tylko kolejkuje."""
    zebrane = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: zebrane.append((event, to, ctx or {})))
    return zebrane


def test_darmowe_konto_dla_swiezego_leada_niesie_link_do_hasla(maile):
    """Główna ścieżka: leada nie ma w bazie klientów, więc konto powstaje razem
    z grantem i hasła nie zna nikt — mail musi je pozwolić ustawić."""
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]
    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 200

    ctx = [m for m in maile if m[0] == "challenge_granted" and m[1] == dane["email"]][-1][2]
    assert "reset=" in (ctx.get("setup_url") or "")


def test_darmowe_konto_dla_starego_klienta_tez_daje_wejscie_do_portalu(maile):
    """Ten sam przycisk trafia na maile, które SĄ już w bazie klientów. Takiemu
    wierszowi nie mintujemy 7-dniowej wejściówki (mógł mieć żywe hasło), ale
    „zaloguj się" bez niczego to ślepa uliczka: `must_set_password` nie istnieje
    na kontach starszych od siebie ani na przejętych przez Google, więc z samej
    flagi nie wynika, że ten człowiek ma czym wejść.
    """
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]
    s = SessionLocal()
    try:
        s.add(Trader(email=dane["email"], password_hash=auth.hash_password("stareHaslo1"),
                     must_set_password=False, full_name="Jan Kowalski"))
        s.commit()
    finally:
        s.close()

    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 200

    ctx = [m for m in maile if m[0] == "challenge_granted" and m[1] == dane["email"]][-1][2]
    assert not ctx.get("setup_url"), "konto z żywym hasłem nie dostaje wejściówki z panelu"
    assert "forgot=1" in (ctx.get("forgot_url") or ""), "mail nie daje żadnej drogi do portalu"
    # Link musi trafiać na wpisany adres, inaczej reset i tak wymaga przepisywania.
    assert dane["email"].replace("@", "%40") in ctx["forgot_url"]


def test_stan_portalu_widac_na_karcie_leada():
    """Bez tego szuflada milczy: przycisku „send invite" nie ma, a dział nie wie,
    czy klient ma hasło, czy wchodzi przez Google."""
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    assert client.get(f"/api/admin/leads/{lead_id}",
                      headers=ADMIN).json()["portal_state"] == "none"

    s = SessionLocal()
    try:
        s.add(Trader(email=dane["email"], password_hash=auth.hash_password("x" * 12),
                     must_set_password=True))
        s.commit()
    finally:
        s.close()
    assert client.get(f"/api/admin/leads/{lead_id}",
                      headers=ADMIN).json()["portal_state"] == "awaiting"

    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == dane["email"]).one()
        tr.must_set_password = False
        s.commit()
    finally:
        s.close()
    assert client.get(f"/api/admin/leads/{lead_id}",
                      headers=ADMIN).json()["portal_state"] == "password"


@pytest.fixture
def realny_provisioning(monkeypatch):
    """Produkcja stoi na `MT5_PROVISIONING=true` — tu to samo, bez zmiany env."""
    monkeypatch.setattr(provisioning, "real_provisioning_enabled", lambda s=None: True)


def test_darmowe_konto_nie_czeka_na_rachunek_z_puli(realny_provisioning, maile):
    """Przy realnym provisioningu konto z zakupu ląduje w `provisioning` i czeka,
    aż poller weźmie dla niego rachunek z puli MT5; mail wychodzi dopiero wtedy.

    Darmowy challenge nie ma prawa w tym utknąć i do 2026-08-19 utykał: pula na
    $25k była pusta, więc lead z /freeaccount nie dostawał NICZEGO, a nigdzie
    nie zapalało się światło, bo nikt nawet nie próbował wysłać maila. Za to
    konto nikt nie zapłacił — rachunek z puli mu się nie należy.
    """
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]

    odp = client.post(f"/api/admin/leads/{lead_id}/free-account", headers=ADMIN)
    assert odp.status_code == 200, odp.text

    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == dane["email"]).one()
        konto = s.query(Account).filter(Account.trader_id == tr.id).one()
        assert konto.status != "provisioning"
        assert konto.platform_login and konto.platform_password and konto.platform_server
        # Poświadczenia są zmyślone, więc feed nie ma się nimi nigdzie logować.
        assert konto.mt5_backed is False
        # Panel i dziennik dostają numer PO uzbrojeniu, nie ten sprzed niego.
        assert odp.json()["login"] == konto.login
    finally:
        s.close()

    ctx = [m for m in maile if m[0] == "challenge_granted" and m[1] == dane["email"]][-1][2]
    assert "reset=" in (ctx.get("setup_url") or "")
    assert ctx["platform_login"] and ctx["platform_password"]


def test_darmowe_konto_rusza_z_wlaczonym_botem(realny_provisioning):
    """Darmowe konto jest prezentem dla kogoś, kto nas nie zna — pusty wykres
    przy pierwszym wejściu do portalu to zmarnowany pierwszy raz."""
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]
    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 200

    s = SessionLocal()
    try:
        tr = s.query(Trader).filter(Trader.email == dane["email"]).one()
        konto = s.query(Account).filter(Account.trader_id == tr.id).one()
        assert konto.bot_enabled is True and konto.bot_paused is False
        assert konto.bot_started_at is not None and konto.bot_seed is not None
    finally:
        s.close()


def test_mail_darmowego_konta_nie_obiecuje_promocji(maile):
    """Ta sama treść obsługuje wszystkie granty, a domyślna mówi o „buy one,
    get one free". Lead z /freeaccount niczego nie kupił, więc to zdanie byłoby
    nieprawdą — i to nieprawdą o pieniądzach."""
    _produkt_free()
    dane = _zgloszenie(source="free-challenge")
    lead_id = _wyslij(dane).json()["id"]
    assert client.post(f"/api/admin/leads/{lead_id}/free-account",
                       headers=ADMIN).status_code == 200

    ctx = [m for m in maile if m[0] == "challenge_granted" and m[1] == dane["email"]][-1][2]
    temat, tresc = notify._render("challenge_granted", ctx)
    html = notify._render_html("challenge_granted", ctx, temat)
    assert "free" in temat.lower()
    for gdzie in (temat, tresc, html):
        assert "bogo" not in gdzie.lower()
        assert "buy one, get one free" not in gdzie.lower()


def test_darmowe_konto_wymaga_admina():
    lead_id = _wyslij(_zgloszenie(source="free-challenge")).json()["id"]
    assert client.post(f"/api/admin/leads/{lead_id}/free-account").status_code in (401, 403)


def test_nick_telegrama_na_karcie_jest_linkiem(_srodowisko):
    """Dział pracuje z karty, nie z panelu — nieklikalny nick znaczy
    przepisywanie go z ekranu do wyszukiwarki Telegrama."""
    _wyslij(_zgloszenie(telegram="@jasiu"))
    _, tekst = _srodowisko["alert"][-1]
    assert '<a href="https://t.me/jasiu">@jasiu</a>' in tekst


def test_smieciowy_nick_zostaje_tekstem(_srodowisko):
    """Martwy link t.me wygląda jak kontakt, a nim nie jest. Dział ma zobaczyć
    literówkę, a nie gonić za nią."""
    _wyslij(_zgloszenie(telegram="gubi please <b>"))
    _, tekst = _srodowisko["alert"][-1]
    assert "t.me" not in tekst
    assert "gubi please &lt;b&gt;" in tekst


# --------------------------------------------------------------------------- #
#  Karta, która nie wyszła                                                     #
# --------------------------------------------------------------------------- #
# Wysyłka karty jest po commicie i best-effort — i słusznie, bo padnięty
# Telegram nie ma prawa kazać człowiekowi wypełniać formularza drugi raz. Ale
# dopóki porażka nie zostawiała śladu, „karty nie wychodzą" wyglądało w bazie
# identycznie jak „leadów nie ma" i cisza na kanale trwała tydzień.


@pytest.fixture
def _telegram_padl(monkeypatch):
    """Telegram odrzuca każdą kartę. Powód dokładnie taki, jaki oddaje API przy
    bocie spoza grupy — to jest realny scenariusz po zmianie czatu."""
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda *a, **kw: (False, "chat not found", None))


def test_nieudana_karta_zostawia_powod_w_historii(_telegram_padl):
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    assert _lead(lead_id).tg_message_id is None
    assert [z.detail for z in _zdarzenia(lead_id, "delivery")] == [
        "telegram card failed: chat not found"]


def test_nieudana_karta_nie_gubi_leada(_telegram_padl):
    """Lead jest zapisany PRZED wysyłką, więc odmowa Telegrama nie ma prawa
    zwrócić landingowi błędu — inaczej człowiek klika drugi raz."""
    odp = _wyslij(_zgloszenie())
    assert odp.status_code == 200 and odp.json()["new"] is True


def _padnij(monkeypatch):
    """Podmienia wysyłkę na padającą i oddaje funkcję przywracającą tę z fixture.

    `monkeypatch.undo()` się tu nie nadaje: cofnąłby też sekrety i czaty
    ustawione w `_srodowisko`, a bez nich kod uznaje, że Telegramu tu w ogóle
    nie ma — czyli testowalibyśmy wyłączony kanał zamiast awarii.
    """
    dziala = telegram.send_lead_alert
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda *a, **kw: (False, "chat not found", None))
    return lambda: monkeypatch.setattr(telegram, "send_lead_alert", dziala)


def test_karta_dosylana_przy_nastepnym_przebiegu(_srodowisko, monkeypatch):
    napraw = _padnij(monkeypatch)
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    napraw()

    assert _cron().status_code == 200
    assert _lead(lead_id).tg_message_id is not None
    # Karta z przyciskami, nie goła wiadomość: bez nich leada nie da się wziąć
    # z czatu, a dział pracuje z czatu, nie z panelu.
    assert lead_id in [i for i, _ in _srodowisko["alert"]]


def test_dosylka_zdejmuje_leada_z_kolejki(_srodowisko, monkeypatch):
    napraw = _padnij(monkeypatch)
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    napraw()

    _cron()
    ile = len([i for i, _ in _srodowisko["alert"] if i == lead_id])
    _cron(); _cron()
    assert len([i for i, _ in _srodowisko["alert"] if i == lead_id]) == ile


def test_stara_karta_nie_wraca_na_czat(_srodowisko, monkeypatch):
    """Doba to granica ratunku po awarii. Karta sprzed tygodnia przeczytałaby
    się na kanale jak świeże zgłoszenie i dział zadzwoniłby drugi raz."""
    napraw = _padnij(monkeypatch)
    lead_id = _wyslij(_zgloszenie()).json()["id"]
    napraw()
    _cofnij(lead_id, od_zgloszenia=8)

    _cron()
    assert _lead(lead_id).tg_message_id is None
    assert lead_id not in [i for i, _ in _srodowisko["alert"]]


# --------------------------------------------------------------------------- #
#  Lead, którego kanał nigdy nie zobaczył                                      #
# --------------------------------------------------------------------------- #
# Dosyłka słusznie odpuszcza po dobie, ale wtedy taki człowiek robi się dla
# działu niewidzialny: kanał czyta się zamiast panelu, a nieudana wysyłka
# zostawia wpis w historii, którego nikt nie ogląda. Tak przepadł tydzień.


def _bez_karty(monkeypatch, *, minut):
    """Lead w wieku `minut`, którego karta nigdy nie wyszła. Oddaje `(id, mail)`."""
    napraw = _padnij(monkeypatch)
    dane = _zgloszenie()
    lead_id = _wyslij(dane).json()["id"]
    napraw()
    _cofnij(lead_id, minut=minut)
    return lead_id, dane["email"]


def test_martwa_karta_upomina_sie_na_czacie(_srodowisko, monkeypatch):
    lead_id, mail = _bez_karty(monkeypatch, minut=30 * 60)

    _cron()
    wiadomosci = [t for t in _srodowisko["przypomnienie"] if mail in t]
    assert len(wiadomosci) == 1
    assert "Karta nie wyszła" in wiadomosci[0]
    # Bez linku do panelu byłaby informacją, z którą na telefonie nie da się nic
    # zrobić — a to na telefonie się ją czyta.
    assert f"/admin?lead={lead_id}" in wiadomosci[0]
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["unclaimed", "card_dead"]
    # Alert zastępuje dosyłkę, nie towarzyszy jej: po dobie karta z przyciskami
    # przeczytałaby się na kanale jak świeże zgłoszenie.
    assert lead_id not in [i for i, _ in _srodowisko["alert"]]


def test_martwa_karta_odzywa_sie_raz(_srodowisko, monkeypatch):
    """Awaria trwa, przebiegów jest kilkanaście na dobę — jedno zdanie na lead,
    inaczej alert utopiłby kanał, który ma go pokazać."""
    _, mail = _bez_karty(monkeypatch, minut=30 * 60)

    _cron(); _cron(); _cron()
    assert len([t for t in _srodowisko["przypomnienie"] if mail in t]) == 1


def test_stary_lead_bez_karty_nie_zalewa_czatu(_srodowisko, monkeypatch):
    """Górna granica pasma jest warunkiem, nie ozdobą: wiersze sprzed kolumny
    `tg_message_id` mają w niej NULL, które nic nie znaczy, i bez niej pierwszy
    przebieg po wdrożeniu wywołałby na czat całe archiwum."""
    lead_id, mail = _bez_karty(monkeypatch, minut=60 * 60)

    _cron()
    assert not any(mail in t for t in _srodowisko["przypomnienie"])
    assert "card_dead" not in [z.detail for z in _zdarzenia(lead_id, "reminder")]


def test_wylaczone_alerty_nie_zglaszaja_martwej_karty(_srodowisko, monkeypatch):
    """Brak czatu to świadome ustawienie, nie awaria. Alert o niedoręczonej
    karcie trafiłby wtedy dokładnie tam, gdzie go nie ma."""
    lead_id, mail = _bez_karty(monkeypatch, minut=30 * 60)
    monkeypatch.setattr(telegram, "lead_alerts_on", lambda *a, **kw: False)

    _cron()
    assert not any(mail in t for t in _srodowisko["przypomnienie"])
    assert [z.detail for z in _zdarzenia(lead_id, "reminder")] == ["unclaimed"]
    # Baza jest wspólna dla całego przebiegu, a ten lead wyszedł z testu bez
    # zdarzenia — czyli odezwałby się w cudzym. Wypycha się go poza pasmo.
    _cofnij(lead_id, od_zgloszenia=8)


# --------------------------------------------------------------------------- #
#  Wyścig o ten sam mail                                                       #
# --------------------------------------------------------------------------- #


def test_wyscig_o_unikalny_mail_nie_gubi_zgloszenia(_srodowisko, monkeypatch):
    """Dwa równoległe POST-y z tym samym NOWYM mailem: oba odczyty widzą pustkę,
    oba robią INSERT. Przegrany dostawał 500, a landing forwarduje bez
    ponowienia — zgłoszenie przepadało. Ma się zachować jak drugie zgłoszenie.
    """
    from sqlalchemy.orm import Session as _Session

    dane = _zgloszenie()
    assert _wyslij(dane).status_code == 200          # wiersz „zwycięzcy"

    oryginalny = _Session.flush
    padlo = {"raz": False}

    def raz_integrity(self, *a, **kw):
        # Odtwarza dokładnie moment kolizji: SELECT już nie zobaczył wiersza,
        # UNIQUE(email) zobaczy go dopiero teraz.
        if not padlo["raz"]:
            padlo["raz"] = True
            raise IntegrityError("UNIQUE constraint failed: leads.email", {}, None)
        return oryginalny(self, *a, **kw)

    monkeypatch.setattr(_Session, "flush", raz_integrity)
    odp = _wyslij(dane)
    monkeypatch.setattr(_Session, "flush", oryginalny)

    assert odp.status_code == 200
    assert odp.json()["new"] is False
    assert _lead(odp.json()["id"]).applications == 2


# --------------------------------------------------------------------------- #
#  Alarm ciszy                                                                 #
# --------------------------------------------------------------------------- #
# Każda reguła przypomnień mówi o leadzie, który JEST w bazie, więc przy zerze
# zgłoszeń wszystkie milczą. To jest ta awaria, która raz kosztowała tydzień.


def _wycisz_baze(godzin=48):
    """Postarza WSZYSTKIE leady — alarm patrzy na najświeższy, nie na jeden."""
    s = SessionLocal()
    try:
        teraz = datetime.now(timezone.utc)
        for l in s.query(Lead).all():
            l.created_at = teraz - timedelta(hours=godzin)
        s.query(AppSetting).filter(AppSetting.key == "leadbot_cisza_alert").delete()
        s.commit()
    finally:
        s.close()


def test_cisza_dluzsza_niz_doba_alarmuje(_srodowisko):
    _wyslij(_zgloszenie())
    _wycisz_baze()
    _cron()
    assert any("Zero nowych leadów" in t for t in _srodowisko["przypomnienie"])


def test_swiezy_lead_wycisza_alarm(_srodowisko):
    _wycisz_baze()
    _wyslij(_zgloszenie())
    _cron()
    assert not any("Zero nowych leadów" in t for t in _srodowisko["przypomnienie"])


def test_alarm_ciszy_nie_wraca_przy_kazdym_przebiegu(_srodowisko):
    """Przebieg chodzi z ruchu strony co ~10 minut. Alarm co przebieg uczyłby
    dział ignorować kanał — a to jest jedyna wiadomość, której nie wolno."""
    _wyslij(_zgloszenie())
    _wycisz_baze()
    _cron(); _cron(); _cron()
    assert len([t for t in _srodowisko["przypomnienie"]
                if "Zero nowych leadów" in t]) == 1


# --------------------------------------------------------------------------- #
#  Kampania — z którego ogłoszenia przyszedł lead                              #
# --------------------------------------------------------------------------- #
KAMPANIA = {"utm_source": "facebook", "utm_medium": "paid",
            "utm_campaign": "sierpien", "utm_content": "wideo-3",
            "fbclid": "FB123", "gclid": "GG1", "ttclid": "TT1"}


def test_kampania_ze_zgloszenia_dochodzi_do_panelu():
    """Landing parkuje utm_* przy wejściu i dokłada je do zgłoszenia. Bez tego
    raport umie powiedzieć „ilu leadów", nigdy „z której reklamy"."""
    lead_id = _wyslij(_zgloszenie(attribution=KAMPANIA)).json()["id"]
    assert _z_listy(lead_id)["campaign"] == KAMPANIA


def test_lead_bez_kampanii_ma_pusty_slownik():
    """Ruch bez oznaczenia to normalny stan, nie brak danych — panel ma dostać
    pusty słownik, a nie null, żeby nie musiał tego rozróżniać."""
    assert _z_listy(_wyslij(_zgloszenie()).json()["id"])["campaign"] == {}


def test_kampania_wpuszcza_tylko_znane_pola():
    """`payload_json` to surowe ciało POST-a z sieci. Bez zamkniętej listy panel
    wyświetlałby dowolny klucz, który ktoś tam wstrzyknie."""
    lead_id = _wyslij(_zgloszenie(
        attribution={"utm_source": "tiktok", "onmouseover": "alert(1)",
                     "note": "cokolwiek"})).json()["id"]
    assert _z_listy(lead_id)["campaign"] == {"utm_source": "tiktok"}


def test_kampania_przycina_przydluga_wartosc():
    lead_id = _wyslij(_zgloszenie(
        attribution={"utm_campaign": "x" * 400})).json()["id"]
    assert len(_z_listy(lead_id)["campaign"]["utm_campaign"]) == 120


def test_kampania_pomija_puste_i_nietekstowe_wartosci():
    """Pusty parametr w adresie i liczba wstrzyknięta POST-em wyglądają w panelu
    tak samo źle: chip bez treści, po którym nie da się nic zdecydować."""
    lead_id = _wyslij(_zgloszenie(
        attribution={"utm_source": "  ", "utm_medium": 7,
                     "utm_campaign": "wiosna"})).json()["id"]
    assert _z_listy(lead_id)["campaign"] == {"utm_campaign": "wiosna"}


def test_kampania_zlym_ksztaltem_nie_wywraca_listy():
    """Jedno śmieciowe pole nie ma prawa zabrać całej listy leadów."""
    for zle in ("utm_source=x", ["a"], 7):
        lead_id = _wyslij(_zgloszenie(attribution=zle)).json()["id"]
        assert _z_listy(lead_id)["campaign"] == {}


def test_ponowne_zgloszenie_podmienia_kampanie():
    """`payload_json` to migawka OSTATNIEGO zgłoszenia. Ten sam człowiek wraca
    z drugiej reklamy — i to ta druga go ostatecznie przyprowadziła."""
    dane = _zgloszenie(attribution={"utm_source": "facebook"})
    lead_id = _wyslij(dane).json()["id"]
    _wyslij({**dane, "attribution": {"utm_source": "tiktok"}})
    assert _z_listy(lead_id)["campaign"] == {"utm_source": "tiktok"}
