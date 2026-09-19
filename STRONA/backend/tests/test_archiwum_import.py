"""Odtwarzanie treści ze starego kanału: kolejka zamiast jednorazowego zrzutu.

Stary account management miał 18 postów w 18,5 dnia, czyli mniej więcej jeden
dziennie. Import ma ten rytm odtworzyć, a nie wysypać wszystkiego naraz — i ma
zatrzymać przed automatem to, czego automat nie potrafi ocenić: twierdzenia
związane z czasem.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import contentbot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ChannelPost  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

FOTO = "https://cdn5.telesco.pe/file/przyklad.jpg"


def _post(pid, tekst, *, foto=True, data="2026-08-18T09:00:00+00:00"):
    return {"id": pid, "date": data, "text": tekst,
            "photos": [FOTO] if foto else [], "videos": [], "media": []}


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_mgmt_chat_id", "@kanal", raising=False)
    s = SessionLocal()
    try:
        s.query(ChannelPost).delete()
        s.commit()
    finally:
        s.close()


def _import(posty, **kw):
    s = SessionLocal()
    try:
        return contentbot.importuj_archiwum(s, posty, **kw)
    finally:
        s.close()


def _kolejka():
    s = SessionLocal()
    try:
        return s.query(ChannelPost).order_by(ChannelPost.scheduled_for).all()
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Rytm                                                                        #
# --------------------------------------------------------------------------- #
def test_posty_rozkladaja_sie_co_zadany_odstep():
    """Jednorazowy zrzut osiemnastu postów wygląda jak awaria, nie jak kanał."""
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    _import([_post(1, "Pierwszy post o zarządzaniu kontem klienta."),
             _post(2, "Drugi post o zarządzaniu kontem klienta, dłuższy."),
             _post(3, "Trzeci post o zarządzaniu kontem klienta, też dłuższy.")],
            co_ile_godzin=24, start=start)
    terminy = [p.scheduled_for for p in _kolejka()]
    assert len(terminy) == 3
    odstepy = {(terminy[i + 1] - terminy[i]) for i in range(len(terminy) - 1)}
    assert odstepy == {timedelta(hours=24)}


def test_kolejnosc_od_najstarszego():
    _import([_post(9, "Nowszy wpis o prowadzeniu konta klienta, dostatecznie długi.", data="2026-09-01T09:00:00+00:00"),
             _post(4, "Starszy wpis o prowadzeniu konta klienta, dostatecznie długi.", data="2026-08-01T09:00:00+00:00")])
    assert [p.origin for p in _kolejka()] == ["archive:mgmt/4", "archive:mgmt/9"]


def test_odstep_da_sie_zageszczic():
    _import([_post(1, "Jeden wpis o prowadzeniu konta klienta, dostatecznie długi."),
             _post(2, "Drugi wpis o prowadzeniu konta klienta, dostatecznie długi.")], co_ile_godzin=6)
    terminy = [p.scheduled_for for p in _kolejka()]
    assert terminy[1] - terminy[0] == timedelta(hours=6)


# --------------------------------------------------------------------------- #
#  Granica automatu                                                            #
# --------------------------------------------------------------------------- #
def test_twierdzenie_czasowe_zostaje_szkicem():
    """„Last month" powtórzone za pół roku jest po prostu nieprawdą, a dowód
    `archive:` tego nie wyłapie — potwierdza pochodzenie, nie aktualność."""
    _import([_post(1, "We paid out a lot last month across every account."),
             _post(2, "We manage the account. You get the notifications.")])
    stany = {p.origin: p.status for p in _kolejka()}
    assert stany["archive:mgmt/1"] == "draft"
    assert stany["archive:mgmt/2"] == "scheduled"


def test_nazwa_miesiaca_tez_zatrzymuje():
    _import([_post(1, "Verified payout confirmed on July 24, and the account went on.")])
    assert _kolejka()[0].status == "draft"


def test_szkic_i_tak_ma_zarezerwowany_termin():
    """Widać go w kolejce na swoim miejscu — tylko nie wyjdzie bez zatwierdzenia."""
    _import([_post(1, "Something happened last month worth telling you about.")])
    assert _kolejka()[0].scheduled_for is not None


def test_podglad_liczy_to_samo_co_import():
    """Panel obiecuje dokładnie to, co wykona import — ta sama funkcja."""
    posty = [_post(1, "We paid out a lot last month across every account."),
             _post(2, "We manage the account. You get the notifications."),
             _post(3, "No promises here, just the way the desk works.")]
    podglad = contentbot.podglad_archiwum(posty)
    wynik = _import(posty)
    assert podglad == {"total": 3, "auto": 2, "manual": 1}
    assert wynik["added"] == 3 and wynik["needs_review"] == 1


# --------------------------------------------------------------------------- #
#  Zdjęcia i limity                                                            #
# --------------------------------------------------------------------------- #
def test_zdjecie_idzie_adresem_a_nie_plikiem():
    """Archiwum nie leży w tym repozytorium — Telegram pobiera obraz sam."""
    _import([_post(1, "Krótki wpis o prowadzeniu konta klienta, ze zdjęciem w tle.")])
    p = _kolejka()[0]
    assert p.kind == "photo" and p.media_url == FOTO


def test_za_dlugi_podpis_gubi_zdjecie_zamiast_ucinac_tresc():
    """Telegram tnie podpis na 1024 znakach, a obcięte zdanie potrafi znaczyć
    co innego niż całe — więc treść zostaje w całości, a zdjęcie odpada."""
    _import([_post(1, "a" * 1200)])
    p = _kolejka()[0]
    assert p.kind == "text" and p.media_url is None
    assert len(p.body) == 1200


def test_komunikaty_systemowe_sa_pomijane():
    _import([_post(1, "Channel created", foto=False),
             _post(2, "Channel photo updated", foto=False),
             _post(3, "Prawdziwy wpis o prowadzeniu konta, wystarczająco długi.")])
    assert [p.origin for p in _kolejka()] == ["archive:mgmt/3"]


# --------------------------------------------------------------------------- #
#  Powtórny import                                                             #
# --------------------------------------------------------------------------- #
def test_ponowny_import_nie_dubluje():
    posty = [_post(1, "Wpis o prowadzeniu konta, wystarczająco długi.")]
    assert _import(posty)["added"] == 1
    drugi = _import(posty)
    assert drugi["added"] == 0 and drugi["skipped"] == 1
    assert len(_kolejka()) == 1


# --------------------------------------------------------------------------- #
#  Dowód `archive:`                                                            #
# --------------------------------------------------------------------------- #
def test_zaimportowany_post_przechodzi_walidator_mimo_liczb():
    """Sam by nie przeszedł — niesie kwotę. Dowód mówi, skąd ta treść pochodzi."""
    _import([_post(1, "Payout confirmed: $6,180 went out to the trader.")])
    p = _kolejka()[0]
    s = SessionLocal()
    try:
        contentbot.waliduj(s, p)     # nie rzuca
    finally:
        s.close()


def test_dowod_archiwalny_nie_dziala_na_poscie_z_panelu():
    """Gdyby działał, każdy mógłby przykleić `archive:` do dowolnej liczby."""
    s = SessionLocal()
    try:
        podrobka = ChannelPost(channel="mgmt", kind="text",
                               body="We paid out $999,999 yesterday.",
                               proof="archive:mgmt/1", origin="panel")
        with pytest.raises(contentbot.NieprawdziwyPost) as e:
            contentbot.waliduj(s, podrobka)
        assert "nieedytowanego" in str(e.value)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Endpoint                                                                    #
# --------------------------------------------------------------------------- #
def test_endpoint_wymaga_admina():
    assert client.post("/api/admin/archive/import", json={"posts": []}).status_code == 403


def test_pusty_plik_odrzucony():
    r = client.post("/api/admin/archive/import", json={"posts": []}, headers=ADMIN)
    assert r.status_code == 400


def test_odstep_poza_zakresem_odrzucony():
    r = client.post("/api/admin/archive/import",
                    json={"posts": [_post(1, "x" * 60)], "every_hours": 500}, headers=ADMIN)
    assert r.status_code == 400


def test_dry_run_niczego_nie_zapisuje():
    r = client.post("/api/admin/archive/import",
                    json={"posts": [_post(1, "Wpis o prowadzeniu konta klienta, dostatecznie długi na próg.")],
                          "dry_run": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert _kolejka() == []
