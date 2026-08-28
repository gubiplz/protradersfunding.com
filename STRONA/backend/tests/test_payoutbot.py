"""Payout BOT — silnik dobowych wypłat, certyfikat i wpis na kanale.

pytest współdzieli bazę między modułami, więc asercje muszą być odporne na cudze
wiersze: sprawdzamy KONKRETNĄ wypłatę po jej id, nie „ostatnią w tabeli".
"""
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import catalog, certshot, payoutbot, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppSetting, Payout  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _sesja():
    s = SessionLocal()
    catalog.seed_products(s)
    s.commit()
    return s


def _wyczysc(s):
    """Zeruje stan silnika — testy nie mogą dziedziczyć guardu po sobie."""
    for row in s.query(AppSetting).filter(AppSetting.key.like("payoutbot_%")).all():
        s.delete(row)
    s.commit()


@contextmanager
def _kanal(shot=None, tg=None):
    """Włącza kanał i renderer na czas testu, podstawiając transporty.

    Ustawienia to jeden obiekt z `lru_cache`, a pola są atrybutami klasy —
    przypisanie na instancji je przesłania i po teście wraca stan wyjściowy.
    """
    u = get_settings()
    stare = (u.telegram_bot_token, u.telegram_chat_id, u.shot_api_url)
    u.telegram_bot_token, u.telegram_chat_id = "TESTOWY", "@kanal_testowy"
    u.shot_api_url = "https://zrzut.test/screenshot"
    try:
        yield
    finally:
        u.telegram_bot_token, u.telegram_chat_id, u.shot_api_url = stare


def _shot_ok(url, body, ct):
    return 200, PNG


def _tg_ok(url, body, ct):
    return 200, b'{"ok":true}'


# --------------------------------------------------------------------------- #
#  Guard i przełącznik                                                         #
# --------------------------------------------------------------------------- #
def test_wylaczony_silnik_nic_nie_tworzy():
    s = _sesja()
    _wyczysc(s)
    try:
        assert payoutbot.uruchom(s) == {"created": 0, "skipped": "engine off"}
    finally:
        s.close()


def test_jedna_wyplata_na_dobe():
    """Drugi przebieg tego samego dnia MUSI być bezczynny — cron i lazy tick
    potrafią uderzyć wielokrotnie, a kanał ma dostawać jeden wpis dziennie."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        kiedy = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
        with _kanal():
            pierwszy = payoutbot.uruchom(s, kiedy, transport_shot=_shot_ok, transport_tg=_tg_ok)
            drugi = payoutbot.uruchom(s, kiedy, transport_shot=_shot_ok, transport_tg=_tg_ok)
            trzeci = payoutbot.uruchom(s, kiedy + timedelta(days=1),
                                       transport_shot=_shot_ok, transport_tg=_tg_ok)
        assert pierwszy["created"] == 1
        assert drugi == {"created": 0, "skipped": "already ran today"}
        assert trzeci["created"] == 1 and trzeci["payout_id"] != pierwszy["payout_id"]
    finally:
        s.close()


def test_wynik_publikacji_widoczny_w_panelu():
    """Guard dnia commit-uje sie PRZED postem, wiec „last run" z dzisiejsza data
    nie znaczy, ze post wyszedl (wpadka 2026-08-08: kanal milczal, panel
    wygladal na „zadzialal"). Wynik ostatniej publikacji laduje w AppSetting
    i endpoint panelu go zwraca."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        kiedy = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
        with _kanal():
            payoutbot.uruchom(s, kiedy, transport_shot=_shot_ok, transport_tg=_tg_ok)
        wpis = s.get(AppSetting, payoutbot.KLUCZ_WYNIK)
        assert wpis is not None and wpis.value == "2026-08-04 OK (photo)"

        # Porazka Telegrama: wyplata i guard zostaja, a panel dostaje powod.
        def _tg_pad(url, body, ct):
            return 403, b'{"ok":false,"description":"bot is not a member"}'
        with _kanal():
            wynik = payoutbot.uruchom(s, kiedy + timedelta(days=1),
                                      transport_shot=_shot_ok, transport_tg=_tg_pad)
        assert wynik["created"] == 1 and wynik["posted"] is False
        wpis = s.get(AppSetting, payoutbot.KLUCZ_WYNIK)
        assert wpis.value.startswith("2026-08-05 FAILED:")
    finally:
        s.close()


def test_czeka_na_slot_w_oknie_et():
    """Okno liczy się w czasie wschodnim USA, a publikacja czeka na wylosowany
    slot — nie na początek okna."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=15, win_to=17)
        # 06:00 UTC to 02:00 ET — długo przed oknem.
        wczesnie = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
        czy, powod = payoutbot.nalezy_odpalic(s, wczesnie)
        assert czy is False and "ET" in powod
        # 22:30 UTC = 18:30 EDT — po końcu okna, więc KAŻDY możliwy slot minął.
        czy2, _ = payoutbot.nalezy_odpalic(s, datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc))
        assert czy2 is True
    finally:
        s.close()


def test_slot_jest_staly_w_ciagu_dnia_i_siedzi_w_oknie():
    """Seed z daty: slot nie może się przelosowywać między tickami tej samej
    doby, a wynik musi mieścić się w zadanym oknie."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=9, win_to=11)
        cfg = payoutbot.ustawienia(s)
    finally:
        s.close()
    rano = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    po_poludniu = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    a, b = payoutbot.slot_dnia(cfg, rano), payoutbot.slot_dnia(cfg, po_poludniu)
    assert a == b
    assert 9 * 60 <= a.hour * 60 + a.minute <= 11 * 60
    assert a.strftime("%Y-%m-%d") == "2026-08-04"
    # Między dniami slot MA się zmieniać — o to chodzi, żeby kanał nie dostawał
    # posta co dzień o tej samej minucie. 10 losowań z ~120 minut: kolizja
    # wszystkich naraz jest astronomicznie nieprawdopodobna.
    sloty = {payoutbot.slot_dnia(cfg, datetime(2026, 9, d, 15, 0, tzinfo=timezone.utc))
             .strftime("%H:%M") for d in range(1, 11)}
    assert len(sloty) >= 3, sloty


def test_okno_liczone_w_czasie_wschodnim_z_dst():
    """Ta sama chwila UTC jest w oknie latem (EDT, UTC-4), a zimą (EST, UTC-5)
    jeszcze przed nim — strefa musi być liczona z DST, nie stałym offsetem."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=9, win_to=11)
        lato = datetime(2026, 8, 4, 13, 30, tzinfo=timezone.utc)      # 09:30 EDT
        zima = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)     # 08:30 EST
        # Latem 13:30 UTC jest JUZ w oknie: backstop albo publikuje (slot minal),
        # albo czeka na slot — nigdy nie czeka na poczatek okna.
        czy_lato, powod_lato = payoutbot.nalezy_odpalic(s, lato, backstop=True)
        assert czy_lato or "slot" in powod_lato
        czy, powod = payoutbot.nalezy_odpalic(s, zima, backstop=True)
        assert czy is False and "09:00 ET" in powod
    finally:
        s.close()


def test_backstop_w_srodku_okna_czeka_na_slot_a_po_oknie_dosyla():
    """Backstop publikuje BEZWARUNKOWO dopiero po koncu okna; w srodku okna
    czeka na slot jak zwykly tick. Geometria okna jest w rekach panelu (okno
    11-15 ET + cron 15:00 UTC = poczatek okna) i stara zasada „od poczatku
    okna" oznaczala post codziennie o stalej godzinie crona — wpadka
    z 2026-08-08: cron z dryfem trafil 11:29 ET i wyprzedzil slot 11:48."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=9, win_to=11)
        cfg = payoutbot.ustawienia(s)
        # Dzien, ktorego slot wypada PO 09:00 (seed z daty = deterministyczne).
        dzien = next(d for d in range(10, 28)
                     if payoutbot.slot_dnia(
                         cfg, datetime(2026, 8, d, 15, 0, tzinfo=timezone.utc))
                     .strftime("%H:%M") != "09:00")
        start_okna = datetime(2026, 8, dzien, 13, 0, tzinfo=timezone.utc)  # 09:00 EDT
        # Przed slotem czekaja OBA tryby — takze cron.
        czy, powod = payoutbot.nalezy_odpalic(s, start_okna)
        assert czy is False and "slot" in powod
        czy_b, powod_b = payoutbot.nalezy_odpalic(s, start_okna, backstop=True)
        assert czy_b is False and "slot" in powod_b
        # Od wylosowanej minuty backstop publikuje...
        slot = payoutbot.slot_dnia(cfg, start_okna)
        w_slocie = slot.astimezone(timezone.utc)
        assert payoutbot.nalezy_odpalic(s, w_slocie, backstop=True)[0] is True
        # ...a po koncu okna dosyla bezwarunkowo (dzien bez ruchu strony).
        po_oknie = datetime(2026, 8, dzien, 15, 30, tzinfo=timezone.utc)  # 11:30 EDT
        assert payoutbot.nalezy_odpalic(s, po_oknie, backstop=True)[0] is True
    finally:
        s.close()


def test_kolejne_przebiegi_nie_powtarzaja_tej_samej_kwoty():
    """„Run once now" pomija guard, wiec admin odpala silnik kilka razy dziennie.
    Losowanie po samym dniu dawalo za kazdym razem TE SAMA kwote i ten sam
    rozmiar konta — na kanale wygladalo to jak zaciety generator."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        kiedy = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        kwoty, rozmiary = [], []
        for _ in range(5):
            r = payoutbot.uruchom(s, kiedy, force=True)
            kwoty.append(r["amount_usd"])
            rozmiary.append(s.get(Payout, r["payout_id"]).account.initial_balance)
        assert len(set(kwoty)) >= 4, kwoty
        assert len(set(rozmiary)) >= 2, rozmiary
    finally:
        s.close()


def test_ten_sam_dzien_daje_ten_sam_wynik():
    """Losowość jest zaseedowana dniem, więc powtórka po nieudanym zapisie
    odtwarza tę samą wypłatę, a nie wymyśla nowej osoby i kwoty."""
    s = _sesja()
    _wyczysc(s)
    try:
        kiedy = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
        a = payoutbot.wygeneruj(s, kiedy)
        nazwa_a, kwota_a = a.account.trader_name, a.trader_share
        s.rollback()
        s.expunge_all()
        b = payoutbot.wygeneruj(s, kiedy)
        assert (b.account.trader_name, b.trader_share) == (nazwa_a, kwota_a)
        s.rollback()
        s.expunge_all()
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Kwoty i certyfikat                                                          #
# --------------------------------------------------------------------------- #
def test_kwota_zgadza_sie_ze_splitem_i_widelkami():
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0, gross_min_pct=9,
                                    gross_max_pct=11.5,
                                    sizes=[50_000, 100_000, 200_000, 300_000, 400_000])
        for dzien in range(4, 14):
            _wyczysc_dzien(s)
            p = payoutbot.wygeneruj(s, datetime(2026, 8, dzien, 18, 0, tzinfo=timezone.utc))
            konto = p.account
            split = konto.profit_split_pct
            assert abs(p.profit_amount * split / 100.0 - p.trader_share) < 0.01
            procent = p.profit_amount / konto.initial_balance * 100.0
            assert 9 - 0.01 <= procent <= 11.5 + 0.01, procent
            assert 50_000 <= konto.initial_balance <= 400_000
            s.rollback()
        s.expunge_all()
    finally:
        s.close()


def _wyczysc_dzien(s):
    row = s.get(AppSetting, payoutbot.KLUCZ_DZIEN)
    if row:
        s.delete(row)
        s.commit()
    # Pętla generuje i cofa wielokrotnie w tej samej sesji — bez tego SQLAlchemy
    # ostrzega o duplikacie tożsamości przy kolejnym flushu.
    s.expunge_all()


def test_certyfikat_dostaje_kazda_wyplata():
    """Bez tokenu nie ma czego zrzucić na kanał, więc dostaje go KAŻDA wypłata —
    niezależnie od tego, czy trafi na pas na landingu."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0, lp_pct=0)
        r = payoutbot.uruchom(s, datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc))
        assert r["created"] == 1 and r["cert_token"]
        assert r["on_lp"] is False
    finally:
        s.close()
    assert client.get(f"/payout/{r['cert_token']}").status_code == 200


def test_lp_zero_i_sto_procent():
    s = _sesja()
    try:
        _wyczysc(s)
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0, lp_pct=0)
        bez = payoutbot.uruchom(s, datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc))
        _wyczysc(s)
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0, lp_pct=100)
        z_lp = payoutbot.uruchom(s, datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc))
        assert bez["on_lp"] is False and z_lp["on_lp"] is True
        assert s.get(Payout, bez["payout_id"]).show_on_lp is False
        assert s.get(Payout, z_lp["payout_id"]).show_on_lp is True
    finally:
        s.close()


def test_bare_zdejmuje_wszystko_poza_karta():
    """`?bare=1` to widok, który zrzuca renderer — musi dokładać klasę, na której
    wisi ukrycie przycisków, i nie ruszać samej karty."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        r = payoutbot.uruchom(s, datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc))
    finally:
        s.close()
    zwykly = client.get(f"/payout/{r['cert_token']}").text
    goly = client.get(f"/payout/{r['cert_token']}?bare=1").text
    assert "cert-bare" not in zwykly and "cert-bare" in goly
    assert "cert-card" in goly and r["cert_token"] in goly
    # Skalowanie pod zrzut nie ma prawa dzialac na stronie, ktora oglada klient.
    assert "style.zoom" in goly and "style.zoom" not in zwykly


def test_znacznik_pochodzenia_nie_drukuje_sie_na_certyfikacie():
    """Notatka „payout bot" jest dla panelu, nie dla klienta. Raz już wyciekła na
    dokument, bo trasa ukrywała wyłącznie znacznik importu z CSV."""
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        r = payoutbot.uruchom(s, datetime(2026, 8, 9, 18, 0, tzinfo=timezone.utc))
        assert s.get(Payout, r["payout_id"]).note == payoutbot.NOTATKA
    finally:
        s.close()
    strona = client.get(f"/payout/{r['cert_token']}").text
    assert payoutbot.NOTATKA not in strona
    assert "cert-note" not in strona


# --------------------------------------------------------------------------- #
#  Podpis, Telegram, renderer                                                  #
# --------------------------------------------------------------------------- #
def test_podpis_jest_1_do_1_z_kanalem():
    assert payoutbot.podpis("Jacob", 3840.0) == (
        "<b><u>PAYOUT ALERT: Jacob Has Just Received $3,840</u></b> \U0001f6a8")
    # Grosze nie mogą zniknąć — podpis i kwota na grafice muszą być identyczne.
    assert "$9,542.88" in payoutbot.podpis("Eleanor", 9542.88)
    assert payoutbot.kwota_txt(3840.0) == "$3,840"


def test_telegram_sklada_poprawny_multipart():
    zebrane = {}

    def transport(url, body, ct):
        zebrane["url"], zebrane["body"], zebrane["ct"] = url, body, ct
        return 200, b'{"ok":true}'

    with _kanal():
        assert telegram.send_photo(PNG, "Podpis", transport=transport) == (True, "")
    granica = zebrane["ct"].split("boundary=")[1]
    assert zebrane["url"].endswith("/sendPhoto")
    assert granica.encode() in zebrane["body"]
    assert b'name="chat_id"' in zebrane["body"] and b"@kanal_testowy" in zebrane["body"]
    assert b'name="photo"; filename="certificate.png"' in zebrane["body"]
    assert PNG in zebrane["body"]
    assert zebrane["body"].endswith(f"--{granica}--\r\n".encode())


def test_telegram_wylaczony_nie_strzela():
    def transport(url, body, ct):  # pragma: no cover - nie ma prawa się wykonać
        raise AssertionError("nie wolno wysyłać przy wyłączonym kanale")

    assert telegram.send_photo(PNG, "x", transport=transport)[0] is False


def test_telegram_zwraca_false_gdy_odrzucone():
    with _kanal():
        # Powod MUSI wrocic do wywolujacego, zeby panel mogl go pokazac.
        assert telegram.send_photo(
            PNG, "x",
            transport=lambda u, b, c: (400, b'{"description":"chat not found"}')
        ) == (False, "chat not found")


def _limit(retry_after, potem=None):
    """Transport odmawiający z limitu. Zbiera strzały, żeby test mógł powiedzieć,
    ile razy poszliśmy do Telegrama, a nie tylko jaki był wynik."""
    strzaly: list[str] = []
    odmowa = (429, b'{"description":"Too Many Requests: retry later",'
                   b'"parameters":{"retry_after":%d}}' % retry_after)

    def transport(url, body, ct):
        strzaly.append(url)
        return potem if potem is not None and len(strzaly) > 1 else odmowa

    return strzaly, transport


def test_limit_grupy_ponawia_po_czasie_z_odpowiedzi(monkeypatch):
    """Grupa przyjmuje ~20 wiadomości na minutę, a kampania wieczorem to
    przekracza. Telegram nie odmawia wtedy na stałe — podaje, ile odczekać.
    Bez ponowienia nadwyżka schodziła do kolejki dosyłek, a ta chodzi z ruchu
    strony: karta z 20:03 lądowała na kanale dopiero rano."""
    spane: list[float] = []
    monkeypatch.setattr(telegram.time, "sleep", spane.append)
    strzaly, transport = _limit(1, potem=(200, b'{"ok":true,"result":{"message_id":7}}'))

    with _kanal():
        assert telegram.send_photo(PNG, "x", transport=transport) == (True, "")
    assert len(strzaly) == 2
    assert spane == [1.0], "czekamy dokładnie tyle, ile każe Telegram"


def test_dlugi_limit_zostawia_karte_kolejce(monkeypatch):
    """Pół minuty to za długo, żeby trzymać request. Od czekania jest kolejka
    dosyłek, więc odmowa ma wrócić od razu i z powodem."""
    monkeypatch.setattr(telegram.time, "sleep",
                        lambda s: (_ for _ in ()).throw(
                            AssertionError("nie wolno tu stać pół minuty")))
    strzaly, transport = _limit(30)

    with _kanal():
        poszlo, powod = telegram.send_photo(PNG, "x", transport=transport)
    assert poszlo is False and "Too Many Requests" in powod
    assert len(strzaly) == 1


def test_limit_ponawia_najwyzej_raz(monkeypatch):
    """Drugie ponowienie znaczyłoby, że limit trzyma dłużej niż wolno tu stać —
    a wtedy rekurencja mnożyłaby strzały zamiast oddać sprawę kolejce."""
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    strzaly, transport = _limit(1)

    with _kanal():
        assert telegram.send_photo(PNG, "x", transport=transport)[0] is False
    assert len(strzaly) == 2


def test_renderer_odrzuca_odpowiedz_ktora_nie_jest_obrazkiem():
    """Usługi przy błędzie potrafią zwrócić JSON-a ze statusem 200 — bez tej
    kontroli poleciałby on na kanał jako „grafika"."""
    with _kanal():
        assert certshot.render("https://x/y", transport=lambda u, b, c: (200, b'{"error":1}')) is None
        assert certshot.render("https://x/y", transport=lambda u, b, c: (500, b"boom")) is None
        assert certshot.render("https://x/y", transport=lambda u, b, c: (200, PNG)) == PNG


def test_renderer_wylaczony_zwraca_none():
    assert certshot.render("https://x/y", transport=lambda u, b, c: (200, PNG)) is None


def test_renderer_tryb_get_podstawia_adres():
    """Usługi hostowane biorą adres w parametrze — `{url}` przełącza na GET."""
    zebrane = {}

    def transport(url, body, ct):
        zebrane["url"], zebrane["body"] = url, body
        return 200, PNG

    u = get_settings()
    stare = u.shot_api_url
    u.shot_api_url = "https://api.test/take?key=K&url={url}"
    try:
        certshot.render("https://app.test/payout/ABC?bare=1", transport=transport)
    finally:
        u.shot_api_url = stare
    assert zebrane["body"] is None                       # GET, nie POST
    assert "https%3A%2F%2Fapp.test%2Fpayout%2FABC%3Fbare%3D1" in zebrane["url"]


def test_bez_grafiki_idzie_sam_podpis_z_linkiem():
    """Awaria renderu nie może uciszyć kanału — wypłata i certyfikat już są."""
    s = _sesja()
    _wyczysc(s)
    wyslane = {}
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        with _kanal():
            r = payoutbot.uruchom(
                s, datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc),
                base_url="https://app.test",
                transport_shot=lambda u, b, c: (500, b"padlo"),
                transport_tg=lambda u, b, c: (wyslane.update(url=u, body=b), (200, b'{"ok":1}'))[1])
    finally:
        s.close()
    assert r["created"] == 1 and r["posted"] is True and r["photo"] is False
    assert wyslane["url"].endswith("/sendMessage")
    assert f"https://app.test/payout/{r['cert_token']}".encode() in wyslane["body"]


# --------------------------------------------------------------------------- #
#  Endpointy panelu                                                            #
# --------------------------------------------------------------------------- #
def test_endpointy_wymagaja_admina():
    assert client.get("/api/admin/payout-engine").status_code in (401, 403)
    for sciezka in ("/api/admin/payout-engine", "/api/admin/payout-engine/run"):
        r = client.post(sciezka, json={})
        assert r.status_code in (401, 403), (sciezka, r.status_code)


def test_zapis_ustawien_z_panelu():
    r = client.post("/api/admin/payout-engine", headers=ADMIN,
                    json={"enabled": True, "win_from": 9, "win_to": 11, "lp_pct": 25,
                          "gross_min_pct": 8, "gross_max_pct": 12,
                          "sizes": [50_000, 100_000]})
    assert r.status_code == 200
    d = client.get("/api/admin/payout-engine", headers=ADMIN).json()
    assert d["enabled"] is True and d["win_from"] == 9 and d["win_to"] == 11
    assert d["lp_pct"] == 25
    assert d["sizes"] == [50_000, 100_000]
    # Panel pokazuje wylosowaną na dziś minutę — format HH:MM, wewnątrz okna.
    godz, minuta = map(int, d["today_slot_et"].split(":"))
    assert 9 * 60 <= godz * 60 + minuta <= 11 * 60
    assert d["telegram_ready"] is False and d["renderer_ready"] is False
    client.post("/api/admin/payout-engine", headers=ADMIN, json={"enabled": False})


def test_zapis_odrzuca_bzdury():
    for zle in ({"win_from": 30}, {"win_to": -1}, {"lp_pct": 140},
                {"gross_min_pct": 90}, {"sizes": [12_345]},
                {"gross_min_pct": 20, "gross_max_pct": 5},
                {"win_from": 12, "win_to": 9}):
        r = client.post("/api/admin/payout-engine", headers=ADMIN, json=zle)
        assert r.status_code == 400, (zle, r.status_code)


def test_run_teraz_omija_guard_godziny_i_dnia():
    s = _sesja()
    _wyczysc(s)
    try:
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=23, win_to=23)
    finally:
        s.close()
    r = client.post("/api/admin/payout-engine/run", headers=ADMIN)
    assert r.status_code == 200 and r.json()["created"] == 1
    # Drugi klik już nie dubluje dnia — „Run once now" ZASTĘPUJE dzisiejszy przebieg.
    s = SessionLocal()
    try:
        assert payoutbot.nalezy_odpalic(s)[0] is False
    finally:
        s.close()
    client.post("/api/admin/payout-engine", headers=ADMIN, json={"enabled": False})


def test_ruch_na_stronie_wyzwala_payout(monkeypatch):
    """Landing pobiera /api/leaderboard, a middleware lazy-tick ma przy okazji
    odpalić Payout BOT-a — to ta ścieżka trafia wylosowany slot, nie cron.
    W conftest wyłącznik PAYOUTBOT_ON_TRAFFIC stoi na false; tu włączamy go
    monkeypatchem na singletonie ustawień."""
    monkeypatch.setattr(get_settings(), "payoutbot_on_traffic", True)
    s = _sesja()
    _wyczysc(s)
    try:
        # Okno 0–23 ze slotem gdziekolwiek — realny zegar testu zawsze jest
        # za JAKIMŚ slotem tylko przy oknie zaczynającym się o północy ET.
        payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
        przed = s.query(Payout).filter(Payout.note == payoutbot.NOTATKA).count()
    finally:
        s.close()
    assert client.get("/api/leaderboard").status_code == 200
    s = SessionLocal()
    try:
        po = s.query(Payout).filter(Payout.note == payoutbot.NOTATKA).count()
        assert po == przed + 1
        # Guard dnia ustawiony — drugi request już nie dubluje.
        assert payoutbot.nalezy_odpalic(s)[0] is False
        payoutbot.zapisz_ustawienia(s, enabled=False)
    finally:
        s.close()
    assert client.get("/api/leaderboard").status_code == 200


def _png(szer: int, wys: int) -> bytes:
    """Najprostszy poprawny PNG (RGBA, bez filtrow) o zadanych wymiarach."""
    import struct
    import zlib

    def chunk(typ, dane):
        return (struct.pack(">I", len(dane)) + typ + dane
                + struct.pack(">I", zlib.crc32(typ + dane)))

    ihdr = struct.pack(">IIBBBBB", szer, wys, 8, 6, 0, 0, 0)
    surowe = b"".join(b"\x00" + bytes([i % 256, 0, 0, 255]) * szer for i in range(wys))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(surowe)) + chunk(b"IEND", b""))


def _wymiary(png: bytes) -> tuple[int, int]:
    return (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big"))


def test_przycinanie_obrazka_do_kwadratu():
    """Uslugi zrzutu maja rozne okna: przy pionowym pod certyfikatem zostawalo
    czarne pole, bo zrzut calej strony ma wysokosc WIEKSZA z dwoch — tresci
    i okna. Parametrami sie tego nie zalatwi (kazdy dostawca nazywa je inaczej),
    wiec obcinamy u siebie."""
    wysoki = _png(40, 100)
    assert _wymiary(wysoki) == (40, 100)
    przyciety = certshot.przytnij_do_kwadratu(wysoki)
    assert _wymiary(przyciety) == (40, 40)
    # Obrazek MUSI zostac dekodowalny — obcinamy wiersze, nie psujemy strumienia.
    assert przyciety.startswith(b"\x89PNG\r\n\x1a\n") and przyciety.endswith(b"IEND\xae\x42\x60\x82")


def test_przycinanie_nie_rusza_tego_co_juz_dobre():
    kwadrat = _png(30, 30)
    assert certshot.przytnij_do_kwadratu(kwadrat) is kwadrat
    szeroki = _png(60, 20)                       # nadmiar moze byc tylko na dole
    assert certshot.przytnij_do_kwadratu(szeroki) is szeroki
    # Nie-PNG (np. JPEG od innej uslugi) przechodzi bez zmian, zamiast wybuchac.
    jpeg = b"\xff\xd8\xff" + b"cokolwiek"
    assert certshot.przytnij_do_kwadratu(jpeg) is jpeg
    assert certshot.przytnij_do_kwadratu(b"to nie jest obrazek") == b"to nie jest obrazek"


def test_kwoty_bez_groszy():
    """Wlasciciel: „nie chce juz nigdy zeby payouty mialy w sobie eurocenty".
    Koncowka „,24" nic nie wnosi przy tej skali, a na certyfikacie rozbija
    najwieksza liczbe na dokumencie."""
    s = SessionLocal()
    payoutbot.zapisz_ustawienia(s, enabled=True, win_from=0, win_to=0)
    kwoty = []
    for _ in range(12):
        w = payoutbot.wygeneruj(s)
        s.commit()
        kwoty.append(w.trader_share)
    s.close()

    z_groszami = [k for k in kwoty if float(k) != float(int(k))]
    assert not z_groszami, f"kwoty z groszami: {z_groszami}"
    assert all(k >= 1 for k in kwoty)
    # Podpis i certyfikat drukuja to samo — bez ".00" na koncu.
    assert all("." not in payoutbot.kwota_txt(k) for k in kwoty)
