"""Reach BOT — zamówienia zasięgu pod postami kanału i strażnik salda.

Żaden test nie rusza sieci: dostawca jest włączany na czas testu przez
podstawienie transportu, tym samym wzorcem co kanał Telegrama w
`test_payoutbot.py`.
"""
import json
import os
import tempfile
import urllib.parse
from contextlib import contextmanager

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import reach, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppSetting  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LINK = "https://t.me/kanal_testowy/321"


@contextmanager
def _dostawca():
    u = get_settings()
    stare = (u.reach_api_url, u.reach_api_key)
    u.reach_api_url, u.reach_api_key = "https://dostawca.test/api/v2", "KLUCZ"
    try:
        yield
    finally:
        u.reach_api_url, u.reach_api_key = stare


def _sesja():
    return SessionLocal()


def _wyczysc(s):
    for row in s.query(AppSetting).filter(AppSetting.key.like("reach_%")).all():
        s.delete(row)
    s.commit()


def _transport(saldo="7.95", zamowienia=None, log=None):
    """Udaje panel SMM: `balance` oddaje saldo, `add` kolejne numery zamówień."""
    licznik = {"n": 0}

    def transport(url, body, ct):
        pola = dict(urllib.parse.parse_qsl(body.decode()))
        if log is not None:
            log.append(pola)
        if pola["action"] == "balance":
            return 200, json.dumps({"balance": saldo, "currency": "USD"}).encode()
        if zamowienia == "error":
            return 200, b'{"error":"Not enough funds"}'
        licznik["n"] += 1
        return 200, json.dumps({"order": 1000 + licznik["n"]}).encode()

    return transport


# --------------------------------------------------------------------------- #
#  Zamówienia                                                                  #
# --------------------------------------------------------------------------- #
def test_zamowienie_sklada_dwie_uslugi_pod_jednym_linkiem():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        log = []
        with _dostawca():
            wynik = reach.zamow(s, LINK, transport=_transport(log=log))
        assert wynik["ordered"] == 2
        addy = [p for p in log if p["action"] == "add"]
        assert [p["service"] for p in addy] == ["8612", "8407"]
        assert [p["quantity"] for p in addy] == ["30", "400"]
        assert {p["link"] for p in addy} == {LINK}
        # Klucz idzie w ciele żądania, nigdy w URL-u — inaczej wyciekłby do logów.
        assert all(p["key"] == "KLUCZ" for p in log)
    finally:
        s.close()


def test_wylaczony_bot_i_brak_dostawcy_nic_nie_zamawiaja():
    s = _sesja()
    try:
        _wyczysc(s)

        def transport(url, body, ct):  # pragma: no cover - nie ma prawa się wykonać
            raise AssertionError("wyłączony Reach BOT nie może strzelać do dostawcy")

        with _dostawca():
            assert reach.zamow(s, LINK, transport=transport)["skipped"] == "reach bot off"
        reach.zapisz_ustawienia(s, enabled=True)
        # Włączony w panelu, ale bez env dostawcy — cisza, nie wyjątek.
        assert "not configured" in reach.zamow(s, LINK, transport=transport)["skipped"]
    finally:
        s.close()


def test_link_musi_byc_publicznym_postem_telegrama():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        with _dostawca():
            wynik = reach.zamow(s, "https://example.com/post/1", transport=_transport())
        assert "public t.me" in wynik["skipped"]
    finally:
        s.close()


def test_puste_saldo_wstrzymuje_zamowienie_i_alarmuje():
    """Bramka salda: przy pustym koncie nie strzelamy, tylko mówimy o tym adminowi."""
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        wyslane = []
        with _dostawca():
            wynik = reach.zamow(s, LINK, transport=_transport(saldo="0.01",
                                                              log=wyslane))
        assert wynik["ordered"] == 0 and "balance too low" in wynik["skipped"]
        assert [p["action"] for p in wyslane] == ["balance"]  # żadnego `add`
        wpis = s.get(AppSetting, reach.KLUCZ_WYNIK)
        assert wpis and "SKIPPED" in wpis.value
    finally:
        s.close()


def test_alert_o_niskim_saldzie_leci_raz_na_dobe(monkeypatch):
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True, min_balance=5)
        alerty = []
        monkeypatch.setattr(reach.notify, "notify_admins",
                            lambda *a, **k: alerty.append(a))
        with _dostawca():
            reach.zamow(s, LINK, transport=_transport(saldo="2.00"))
            reach.zamow(s, LINK, transport=_transport(saldo="2.00"))
        assert len(alerty) == 1 and alerty[0][0] == "admin_reach"
    finally:
        s.close()


def test_blad_dostawcy_nie_wywraca_wywolania_i_wpada_do_panelu(monkeypatch):
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        monkeypatch.setattr(reach.notify, "notify_admins", lambda *a, **k: None)
        with _dostawca():
            wynik = reach.zamow(s, LINK, transport=_transport(zamowienia="error"))
        assert wynik["ordered"] == 0
        assert all(w["error"] == "Not enough funds" for w in wynik["results"])
        wpis = s.get(AppSetting, reach.KLUCZ_WYNIK)
        assert wpis and "0/2" in wpis.value
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Saldo i link posta                                                          #
# --------------------------------------------------------------------------- #
def test_saldo_liczy_ile_zostalo_postow():
    with _dostawca():
        b = reach.saldo(transport=_transport(saldo="7.95"), unit_cost=0.055, min_balance=1)
    assert b["value"] == 7.95 and b["posts_left"] == 144 and b["low"] is False


# --------------------------------------------------------------------------- #
#  Obsługiwane kanały                                                          #
# --------------------------------------------------------------------------- #
def test_lista_kanalow_czysci_nazwy_i_odrzuca_smieci():
    s = _sesja()
    try:
        _wyczysc(s)
        lista = reach.zapisz_kanaly(s, [
            {"username": "https://t.me/fx_passing/12", "label": "Account management"},
            {"username": "@FX_PassingPayouts", "label": "Payouts", "on": False},
            {"username": "@fx_passing"},          # duplikat po normalizacji
        ])
        assert [k["username"] for k in lista] == ["fx_passing", "fx_passingpayouts"]
        assert lista[1]["on"] is False
        with pytest.raises(ValueError):
            reach.zapisz_kanaly(s, [{"username": "@zle!"}])
    finally:
        s.close()


def test_post_z_kanalu_zamawia_raz_i_pomija_albumy():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])
        post = {"message_id": 55, "text": "hej", "chat": {"id": -100, "username": "fx_passing"}}
        with _dostawca():
            pierwszy = reach.z_kanalu(s, post, transport=_transport())
            drugi = reach.z_kanalu(s, post, transport=_transport())
            album = reach.z_kanalu(s, {**post, "message_id": 56, "media_group_id": "abc"},
                                   transport=_transport())
            album2 = reach.z_kanalu(s, {**post, "message_id": 57, "media_group_id": "abc"},
                                    transport=_transport())
        assert pierwszy["ordered"] == 2 and pierwszy["link"] == "https://t.me/fx_passing/55"
        assert drugi["skipped"] == "duplicate"        # ponowienie webhooka
        assert album["ordered"] == 2
        assert album2["skipped"] == "duplicate"       # drugie zdjęcie tego albumu
    finally:
        s.close()


def test_post_z_kanalu_spoza_listy_nic_nie_kosztuje():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": False}])

        def transport(url, body, ct):  # pragma: no cover - nie ma prawa się wykonać
            raise AssertionError("kanał spoza listy nie może zamawiać")

        with _dostawca():
            obcy = reach.z_kanalu(s, {"message_id": 1, "text": "x",
                                      "chat": {"id": -1, "username": "cudzy_kanal"}},
                                  transport=transport)
            wylaczony = reach.z_kanalu(s, {"message_id": 2, "text": "x",
                                           "chat": {"id": -100, "username": "fx_passing"}},
                                       transport=transport)
        assert obcy["skipped"] == "channel not watched"
        assert wylaczony["skipped"] == "channel not watched"
    finally:
        s.close()


def test_publikacja_na_odznaczonym_kanale_nie_zamawia():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passingpayouts", "on": False}])

        def transport(url, body, ct):  # pragma: no cover - nie ma prawa się wykonać
            raise AssertionError("odznaczony kanał nie może zamawiać")

        with _dostawca():
            wynik = reach.po_publikacji(s, "https://t.me/fx_passingpayouts/9",
                                        transport=transport)
        assert "off the list" in wynik["skipped"]
    finally:
        s.close()


def test_koszt_posta_liczy_sie_z_cennika_dostawcy():
    """Admin nie wpisuje ceny ręcznie — bierze się z cennika i sama się poprawia."""
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        assert reach.ustawienia(s)["cost_from"] == "estimate"

        def cennik(url, body, ct):
            return 200, json.dumps([
                {"service": 8612, "rate": "0.0275", "name": "Telegram Positive Reactions"},
                {"service": 8407, "rate": "0.1305", "name": "Telegram Views [1 Post]"},
                {"service": 1, "rate": "9.99", "name": "Co innego"},
            ]).encode()

        with _dostawca():
            reach.odswiez_cennik(s, transport=cennik)
        cfg = reach.ustawienia(s)
        # Usługa negatywnych reakcji ma id o jeden większe niż pozytywna,
        # więc panel musi wiedzieć, CO zamawia, a nie tylko pod jakim numerem.
        assert cfg["name_reactions"] == "Telegram Positive Reactions"
        assert cfg["reactions_positive"] is True
        # 30 x 0.0275/1000 + 400 x 0.1305/1000
        assert cfg["unit_cost"] == 0.053025 and cfg["cost_from"] == "provider"

        # Zmiana usługi kasuje starą stawkę i nazwę: cena i opis czegoś, czego
        # już nie zamawiamy, wprowadzałyby w błąd.
        reach.zapisz_ustawienia(s, svc_views=8408)
        po = reach.ustawienia(s)
        assert po["cost_from"] == "estimate" and po["name_views"] == ""

        # Podmiana na wariant negatywny (id o jeden dalej) musi być widoczna.
        def cennik_neg(url, body, ct):
            return 200, json.dumps([
                {"service": 8613, "rate": "0.0275", "name": "Telegram Negative Reactions"},
            ]).encode()

        reach.zapisz_ustawienia(s, svc_reactions=8613)
        with _dostawca():
            reach.odswiez_cennik(s, transport=cennik_neg)
        assert reach.ustawienia(s)["reactions_positive"] is False
    finally:
        s.close()


def test_link_posta_powstaje_z_odpowiedzi_telegrama():
    """Bez publicznego `username` kanału nie ma czego podbijać."""
    assert telegram.post_url({"message_id": 12, "chat": {"username": "kanal"}}) \
        == "https://t.me/kanal/12"
    assert telegram.post_url({"message_id": 12, "chat": {"id": -100}}) == ""
    assert telegram.post_url({}) == ""


def test_publikacja_payout_bota_oddaje_link_posta():
    from app import payoutbot

    with _dostawca():
        pass
    u = get_settings()
    stare = (u.telegram_bot_token, u.telegram_chat_id, u.shot_api_url)
    u.telegram_bot_token, u.telegram_chat_id = "TESTOWY", "@kanal_testowy"
    u.shot_api_url = ""
    try:
        class Fake:
            cert_token = "abc"
            trader_share = 1234.0
        wynik = payoutbot.opublikuj(
            Fake(), "Ann Smith", base_url="https://ptf.test",
            transport_tg=lambda u_, b_, c_: (
                200, b'{"ok":true,"result":{"message_id":77,"chat":{"username":"kanal_testowy"}}}'))
        assert wynik["posted"] is True
        assert wynik["post_url"] == "https://t.me/kanal_testowy/77"
    finally:
        u.telegram_bot_token, u.telegram_chat_id, u.shot_api_url = stare


# --------------------------------------------------------------------------- #
#  Panel                                                                       #
# --------------------------------------------------------------------------- #
def test_panel_czyta_i_zapisuje_ustawienia():
    s = _sesja()
    try:
        _wyczysc(s)
    finally:
        s.close()
    r = client.get("/api/admin/reach", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["provider_ready"] is False

    zapis = client.post("/api/admin/reach", headers=ADMIN,
                        json={"enabled": True, "qty_reactions": 50, "min_balance": 2.5})
    assert zapis.status_code == 200
    d = zapis.json()
    assert d["enabled"] is True and d["qty_reactions"] == 50 and d["min_balance"] == 2.5

    zly = client.post("/api/admin/reach", headers=ADMIN, json={"qty_views": 999999})
    assert zly.status_code == 400 and "must be between" in zly.json()["detail"]


def test_reczny_boost_dziala_mimo_wylaczonego_automatu():
    """Przełącznik gasi automat pod publikacjami, nie przycisk w panelu."""
    s = _sesja()
    try:
        _wyczysc(s)
    finally:
        s.close()
    r = client.post("/api/admin/reach/boost", headers=ADMIN, json={"link": LINK})
    # Bez skonfigurowanego dostawcy kończy się na jego braku, a NIE na „off".
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"] and "off" not in r.json()["detail"]


def test_automat_nie_zamawia_przy_wylaczonym_botze():
    s = _sesja()
    try:
        _wyczysc(s)

        # Kanał NA liście — chodzi o sam wyłącznik, nie o bramkę kanałów.
        reach.zapisz_kanaly(s, [{"username": "kanal_testowy", "on": True}])

        def transport(url, body, ct):  # pragma: no cover - nie ma prawa się wykonać
            raise AssertionError("wyłączony automat nie może strzelać do dostawcy")

        with _dostawca():
            assert reach.po_publikacji(s, LINK, transport=transport)["skipped"] == "reach bot off"
    finally:
        s.close()


def test_getme_idzie_bez_multiparta_wiec_status_admina_jest_znany(monkeypatch):
    """Metoda bez pol nie moze isc multipartem — Telegram odbija taki request
    jako HTTP 400, przez co `bot_id` wychodzilo 0, a panel przy kazdym kanale
    pisal "nie wiadomo", mimo ze bot byl administratorem."""
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "TESTOWY:TOKEN",
                        raising=False)
    monkeypatch.setattr(telegram, "_BOT_ID", None, raising=False)
    widziane = []

    def transport(url, body, content_type):
        widziane.append((url.rsplit("/", 1)[-1], bytes(body or b""), content_type))
        if url.endswith("/getMe"):
            return 200, json.dumps({"ok": True, "result": {"id": 4242}}).encode()
        return 200, json.dumps({"ok": True,
                                "result": {"status": "administrator"}}).encode()

    assert telegram.bot_id(transport=transport) == 4242
    assert telegram.jest_adminem("@kanal", transport=transport) is True
    getme = [w for w in widziane if w[0] == "getMe"][0]
    # Puste cialo multiparta = 400 u Telegrama; JSON przechodzi.
    assert getme[1] == b"{}" and getme[2] == "application/json"
    # Wywolanie Z polami dalej idzie multipartem (zdjecia musza dzialac).
    zparam = [w for w in widziane if w[0] == "getChatMember"][0]
    assert zparam[2].startswith("multipart/form-data")


def test_lista_kanalow_miesci_sie_w_kolumnie_ustawien():
    """Trzy kanaly z etykietami to ponad 200 znakow JSON-a. Dopoki
    app_settings.value bylo VARCHAR(200), Postgres odrzucal taki zapis i panel
    oddawal 500 (SQLite w testach niczego nie egzekwuje, wiec pilnujemy tego
    tutaj: kolumna ma byc bezdlugosciowa, a zapis ma wracac w calosci)."""
    from app.models import AppSetting

    assert getattr(AppSetting.__table__.c.value.type, "length", None) is None

    s = _sesja()
    try:
        _wyczysc(s)
        wejscie = [
            {"username": "fx_passingpayouts", "label": "FOREX PASSING | PAYOUTS",
             "on": True},
            {"username": "fx_passing", "label": "FOREX PASSING | ACCOUNT MANAGEMENT",
             "on": False},
            {"username": "fx_passingtrackrecord", "label": "FOREX PASSING | TRACK RECORD",
             "on": True},
        ]
        zapisane = reach.zapisz_kanaly(s, wejscie)
        assert [k["username"] for k in zapisane] == [k["username"] for k in wejscie]
        assert [k["on"] for k in zapisane] == [True, False, True]
        # Sedno: serializacja NAPRAWDE przekracza dawny limit kolumny.
        wiersz = s.get(AppSetting, "reach_channels")
        assert wiersz is not None and len(wiersz.value) > 200
    finally:
        s.close()
