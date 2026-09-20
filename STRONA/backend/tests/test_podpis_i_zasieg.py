"""Dwie usterki widoczne na pierwszym opublikowanym poście z kolejki.

1. LIMIT PODPISU MIERZONY NA SUROWYM HTML-u. Telegram przy `parse_mode=HTML`
   liczy tekst PO sparsowaniu — znaczniki i encje się nie liczą. Post Claire
   miał 1066 znaków surowo i 1019 widocznych, więc mieścił się w podpisie
   z zapasem, a import i tak zdegradował go do samego tekstu i wyrzucił
   zdjęcie. Cztery posty z archiwum straciły tak grafikę.

2. KOLEJKA NIE ZAMAWIAŁA ZASIĘGU. `reach.po_publikacji` wołał wyłącznie payout
   bot. Reach BOT był włączony i poprawnie skonfigurowany — tylko nikt go nie
   pytał, więc posty z kolejki wychodziły bez reakcji i wyświetleń.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import contentbot  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import ChannelPost  # noqa: E402

init_db()

LINK = "https://t.me/forex_passing_admin"
STOPKA = f'<a href="{LINK}">Click here to send us a message</a>'


@pytest.fixture
def sesja():
    s = SessionLocal()
    stworzone = []
    try:
        yield s, stworzone
    finally:
        try:
            s.rollback()
            for p in stworzone:
                s.delete(p)
            s.commit()
        finally:
            s.close()


def _post(sesja, **pola):
    s, stworzone = sesja
    dane = dict(channel="mgmt", kind="text", body="Timeless copy.", proof="",
                status="approved", origin="panel")
    dane.update(pola)
    p = ChannelPost(**dane)
    s.add(p)
    s.commit()
    stworzone.append(p)
    return p


# --------------------------------------------------------------------------- #
#  Pomiar długości
# --------------------------------------------------------------------------- #
def test_znaczniki_nie_licza_sie_do_limitu():
    """`<a href="…">` to kilkadziesiąt znaków, których czytelnik nie zobaczy."""
    assert contentbot.dlugosc_widoczna(f"abc {STOPKA}") == len(
        "abc Click here to send us a message")


def test_encje_licza_sie_jak_jeden_znak():
    """`&#39;` to pięć znaków w źródle i jeden na ekranie."""
    assert contentbot.dlugosc_widoczna("it&#39;s") == len("it's")
    assert contentbot.dlugosc_widoczna("a &amp; b") == len("a & b")


def test_pusty_tekst_nie_wywraca_pomiaru():
    assert contentbot.dlugosc_widoczna("") == 0
    assert contentbot.dlugosc_widoczna(None) == 0


def test_post_ktory_miesci_sie_po_odjeciu_znacznikow_przechodzi(sesja):
    """Sedno usterki: surowo ponad limit, widocznie pod limitem."""
    s, _ = sesja
    tresc = "x" * 990 + " " + STOPKA
    assert len(tresc) > contentbot.LIMIT_PODPISU
    assert contentbot.dlugosc_widoczna(tresc) <= contentbot.LIMIT_PODPISU

    post = _post(sesja, kind="photo", body=tresc,
                 media_url="https://forexpassing.com/tg/arch/6.jpg")
    contentbot.waliduj(s, post)   # brak wyjatku = test zdany


def test_naprawde_za_dlugi_podpis_dalej_odpada(sesja):
    """Poprawka ma przestac liczyc ZNACZNIKI, a nie przestac pilnowac limitu."""
    s, _ = sesja
    post = _post(sesja, kind="photo", body="x" * 1100,
                 media_url="https://forexpassing.com/tg/arch/6.jpg")
    with pytest.raises(contentbot.NieprawdziwyPost, match="1100"):
        contentbot.waliduj(s, post)


# --------------------------------------------------------------------------- #
#  Zamówienie zasięgu
# --------------------------------------------------------------------------- #
def _wyslij(monkeypatch, s, post, *, ok=True):
    zamowienia = []
    monkeypatch.setattr(contentbot, "chat_id", lambda kanal: "@kanal")
    monkeypatch.setattr(
        contentbot.telegram, "send_content",
        lambda *a, **k: (ok, "" if ok else "Telegram odmowil",
                         {"message_id": 7} if ok else {}))
    monkeypatch.setattr(contentbot.telegram, "post_url",
                        lambda d: "https://t.me/forex_passing/7" if d else "")
    monkeypatch.setattr(
        contentbot.reach, "po_publikacji",
        lambda sess, link, **k: zamowienia.append((link, k.get("powod"))) or {"ordered": 2})
    return contentbot.opublikuj(s, post), zamowienia


def test_udana_publikacja_zamawia_zasieg(sesja, monkeypatch):
    s, _ = sesja
    post = _post(sesja)

    wynik, zamowienia = _wyslij(monkeypatch, s, post)

    assert wynik["posted"] is True
    assert zamowienia == [("https://t.me/forex_passing/7", "content")], \
        "powod musi mowic, KTO zamowil — inaczej nie wiadomo, na co poszly pieniadze"


def test_nieudana_publikacja_nie_zamawia_zasiegu(sesja, monkeypatch):
    """Nie ma posta, nie ma czego podbijac — a zamowienie kosztuje."""
    s, _ = sesja
    post = _post(sesja)

    wynik, zamowienia = _wyslij(monkeypatch, s, post, ok=False)

    assert wynik["posted"] is False
    assert zamowienia == []


def test_wywrocone_zamowienie_nie_cofa_publikacji(sesja, monkeypatch):
    """Post juz poszedl na kanal. Blad przy dokupieniu zasiegu nie moze
    zmienic jego statusu — tego sie nie da cofnac."""
    s, _ = sesja
    post = _post(sesja)

    def wybuch(*a, **k):
        raise RuntimeError("JAP padl")

    monkeypatch.setattr(contentbot, "chat_id", lambda kanal: "@kanal")
    monkeypatch.setattr(contentbot.telegram, "send_content",
                        lambda *a, **k: (True, "", {"message_id": 7}))
    monkeypatch.setattr(contentbot.telegram, "post_url",
                        lambda d: "https://t.me/forex_passing/7")
    monkeypatch.setattr(contentbot.reach, "po_publikacji", wybuch)

    wynik = contentbot.opublikuj(s, post)

    assert wynik["posted"] is True, "publikacja sie powiodla i ma tak zostac zgloszona"
    assert post.status == "published"
    assert wynik["reach"].get("error"), "blad ma byc widoczny w wyniku, nie rzucony"
