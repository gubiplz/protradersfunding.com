"""Odtwarzanie treści ze starego kanału: kolejka zamiast jednorazowego zrzutu.

Stary account management miał 18 postów w 18,5 dnia, czyli mniej więcej jeden
dziennie. Import ma ten rytm odtworzyć, a nie wysypać wszystkiego naraz — i ma
zatrzymać przed automatem to, czego automat nie potrafi ocenić: twierdzenia
związane z czasem.
"""
import os
import struct
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
from app.models import ChannelPost, PostMedia  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

FOTO = "https://cdn5.telesco.pe/file/przyklad.jpg"


def _jpeg(szer=1080, wys=1080):
    """SOI + APP0 (JFIF) + SOF0 z wymiarami — tyle, ile czyta serwer."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, wys, szer, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _post(pid, tekst, *, foto=True, data="2026-08-18T09:00:00+00:00"):
    return {"id": pid, "date": data, "text": tekst,
            "photos": [FOTO] if foto else [], "videos": [], "media": []}


POBRANE = []
# Prawdziwe pobieranie — fixture niżej podstawia atrapę.
_POBIERZ_NAPRAWDE = contentbot._pobierz_zdjecie


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_mgmt_chat_id", "@kanal", raising=False)
    # Import pobiera zdjęcia z sieci; tu dostaje mały, prawdziwy JPEG.
    POBRANE.clear()
    monkeypatch.setattr(contentbot, "_pobierz_zdjecie",
                        lambda adres: (POBRANE.append(adres), (_jpeg(), ""))[1])
    s = SessionLocal()
    try:
        s.query(ChannelPost).delete()
        s.query(PostMedia).delete()
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
def test_zdjecie_kopiowane_do_bazy_a_nie_zostawiane_na_cdn_telegrama():
    """Adresy z podglądu `t.me` wygasają po kilku dniach — post zaplanowany na
    za dwa tygodnie trzymałby martwy link. Import trzyma własną kopię."""
    _import([_post(1, "Krótki wpis o prowadzeniu konta klienta, ze zdjęciem w tle.")])
    p = _kolejka()[0]
    assert POBRANE == [FOTO]
    assert p.kind == "photo" and "/media/posts/" in p.media_url
    assert p.media_url.endswith(".jpg") and p.status == "scheduled"
    plik = client.get(p.media_url[p.media_url.index("/media/posts/"):])
    assert plik.status_code == 200 and plik.headers["content-type"] == "image/jpeg"


def test_dlugi_tekst_zachowuje_zdjecie_i_cala_tresc():
    """Usterka z produkcji (archive:mgmt/16, 1132 znaki): import zdejmował
    zdjęcie z każdego posta dłuższego niż podpis. Taki post wychodzi teraz
    jako tekst z dużym podglądem zdjęcia, więc nie traci ani grafiki, ani słowa."""
    _import([_post(1, "a" * 1200)])
    p = _kolejka()[0]
    assert p.kind == "photo" and "/media/posts/" in p.media_url
    assert len(p.body) == 1200
    s = SessionLocal()
    try:
        contentbot.waliduj(s, p)     # nie rzuca: limit 4096, nie 1024
    finally:
        s.close()


def test_niepobrane_zdjecie_zostawia_szkic_z_powodem(monkeypatch):
    """Bez kopii post wyszedłby bez grafiki albo na martwy link — czeka na człowieka."""
    monkeypatch.setattr(contentbot, "_pobierz_zdjecie", lambda adres: (b"", "HTTP 404"))
    wynik = _import([_post(1, "Wpis o prowadzeniu konta klienta, ze zdjęciem w tle.")])
    p = _kolejka()[0]
    assert p.status == "draft" and p.kind == "text" and p.media_url is None
    assert "HTTP 404" in p.last_error and "upload" in p.last_error
    assert wynik["photos_missing"] == 1 and wynik["needs_review"] == 1


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
    assert POBRANE == [FOTO], "zdjęcie już skopiowane nie jest pobierane drugi raz"


def _stary_wpis(**pola):
    """Post tak, jak zostawił go import sprzed poprawki."""
    dane = dict(channel="mgmt", kind="text", body="a" * 1200, media_url=None,
                proof="archive:mgmt/16", status="scheduled", origin="archive:mgmt/16",
                scheduled_for=datetime(2026, 10, 12, 16, 53), created_by="import")
    dane.update(pola)
    s = SessionLocal()
    try:
        s.add(ChannelPost(**dane))
        s.commit()
    finally:
        s.close()


def test_ponowny_import_przypina_zdjecie_postowi_ktory_je_stracil():
    """Naprawa kolejki z produkcji: wgranie tego samego archiwum oddaje grafikę
    postom, które stary import zdegradował do tekstu — bez ruszania terminu."""
    _stary_wpis()
    wynik = _import([_post(16, "a" * 1200)])
    p = _kolejka()[0]
    assert wynik["photos_restored"] == 1 and wynik["added"] == 0
    assert p.kind == "photo" and "/media/posts/" in p.media_url
    assert p.status == "scheduled"
    assert p.scheduled_for == datetime(2026, 10, 12, 16, 53)


def test_ponowny_import_podmienia_wygasly_adres_telegrama():
    _stary_wpis(kind="photo", media_url=FOTO, body="Wpis ze zdjęciem o prowadzeniu konta, wystarczająco długi.")
    wynik = _import([_post(16, "Wpis ze zdjęciem o prowadzeniu konta, wystarczająco długi.")])
    assert wynik["photos_restored"] == 1
    assert "/media/posts/" in _kolejka()[0].media_url


def test_naprawa_grafiki_nie_rusza_poprawionej_tresci():
    """Posty w kolejce dostały po imporcie nowy uchwyt admina i linki, więc ich
    treść różni się od archiwum. Zdjęcie wraca, tekst zostaje ten z kolejki."""
    poprawiony = "a" * 1150 + "\nMessage @forex_passing_admin to get started!"
    _stary_wpis(body=poprawiony)
    wynik = _import([_post(16, "a" * 1150 + "\nMessage @fxpassingadmin to get started!")])
    p = _kolejka()[0]
    assert wynik["photos_restored"] == 1 and p.kind == "photo"
    assert p.body == poprawiony and p.proof == "archive:mgmt/16"


@pytest.mark.parametrize("pola", [
    {"status": "published"},
    {"kind": "photo", "media_url": "https://forexpassing.com/tg/arch/16.jpg"},
])
def test_ponowny_import_nie_rusza_decyzji_czlowieka(pola):
    """Post już na kanale i grafika podpięta z innej domeny zostają, jak są."""
    _stary_wpis(**pola)
    wynik = _import([_post(16, "a" * 1200)])
    p = _kolejka()[0]
    assert wynik["photos_restored"] == 0 and POBRANE == []
    assert p.kind == pola.get("kind", "text")
    assert p.media_url == pola.get("media_url")


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


# --------------------------------------------------------------------------- #
#  Co jeszcze nie jest postem, a co się starzeje                               #
# --------------------------------------------------------------------------- #
def test_komunikat_o_przypieciu_nie_jest_postem():
    """„<kanał> pinned a video" ma 49 znaków, więc przechodził próg długości
    i wyszedłby na kanał jako zdanie o samym sobie."""
    _import([_post(1, "FOREX CHANNEL | ACCOUNT MANAGEMENT pinned a video", foto=False),
             _post(2, "Prawdziwy wpis o prowadzeniu konta, wystarczająco długi.")])
    assert [p.origin for p in _kolejka()] == ["archive:mgmt/2"]


def test_pole_service_wystarczy_zeby_pominac():
    """Archiwizator zapisuje typ komunikatu systemowego — ufamy mu wprost."""
    wpis = _post(1, "Coś, co wygląda jak zwykły post o zarządzaniu kontem.")
    wpis["service"] = "pinned"
    _import([wpis])
    assert _kolejka() == []


def test_liczba_wolnych_miejsc_zostaje_szkicem():
    """Licznik miejsc zmienia się codziennie i stoi w opisie kanału. Post
    z „Only 2 Spots Left" wypuszczony w losowym dniu przeczy własnemu kanałowi."""
    _import([_post(1, "🚨 Only 2 Spots Left 🚨 Do Not Wait! We Are Closing Our Spots.")])
    assert _kolejka()[0].status == "draft"


def test_powod_wstrzymania_nazywa_rzecz_po_imieniu():
    assert "time-bound" in contentbot.wymaga_czlowieka("We paid out a lot last month.")
    assert "spots-left" in contentbot.wymaga_czlowieka("Only 3 spots remaining. Do not wait.")
    assert contentbot.wymaga_czlowieka("We manage the account. You get notified.") == ""


def test_podglad_liczy_takze_miejsca():
    posty = [_post(1, "🚨 Only 2 Spots Left 🚨 Do Not Wait! We Are Closing Our Spots."),
             _post(2, "No promises here, just the way the desk works every day.")]
    assert contentbot.podglad_archiwum(posty) == {"total": 2, "auto": 1, "manual": 1}


def test_miniatura_filmu_nie_udaje_zdjecia(monkeypatch):
    """Podgląd `t.me` rysuje miniaturę filmu tak samo jak zdjęcie, a archiwizator
    bierze ją do `photos`. Na starym kanale #10 i #23 były filmami 0:40 i 0:55 —
    miniatura 180×320 przypięta jako grafika byłaby gorsza niż żadna."""
    monkeypatch.setattr(contentbot, "_pobierz_zdjecie", lambda adres: (_jpeg(180, 320), ""))
    _stary_wpis()
    wynik = _import([_post(16, "a" * 1200),
                     _post(30, "Nowy wpis o prowadzeniu konta, wystarczająco długi.")])
    po_origin = {p.origin: p for p in _kolejka()}
    stary, nowy = po_origin["archive:mgmt/16"], po_origin["archive:mgmt/30"]
    assert wynik["photos_restored"] == 0 and stary.kind == "text"
    assert "180×320 thumbnail" in wynik["photos_not_restored"][0]
    assert nowy.status == "draft" and nowy.kind == "text"
    assert "video" in nowy.last_error


def test_nieudane_pobranie_przy_naprawie_jest_widoczne(monkeypatch):
    monkeypatch.setattr(contentbot, "_pobierz_zdjecie", lambda adres: (b"", "timed out"))
    _stary_wpis()
    wynik = _import([_post(16, "a" * 1200)])
    assert wynik["photos_restored"] == 0
    assert wynik["photos_not_restored"] == ["archive:mgmt/16: timed out"]
    assert _kolejka()[0].status == "scheduled", "nieudana naprawa nie rusza posta"


@pytest.mark.parametrize("adres", [
    "http://cdn5.telesco.pe/file/x.jpg",
    "https://169.254.169.254/latest/meta-data",
    "https://example.com/cdn5.telesco.pe/x.jpg",
    "https://telesco.pe.example.com/x.jpg",
])
def test_pobierane_sa_tylko_adresy_cdn_telegrama(adres):
    """Adres pochodzi z wgranego pliku — serwer nie sięga pod dowolny."""
    dane, powod = _POBIERZ_NAPRAWDE(adres)
    assert dane == b"" and "Telegram's CDN" in powod
