"""Wezwania w postach kanału wychodzą jako linki, jak na starym kanale.

Archiwizator zapisał posty starego kanału jako czysty tekst, więc „👉 Get
started", „Click here to send us a message" i wzmianka o adminie — na starym
kanale linki do DM-a admina z gotową wiadomością — po przenosinach przestały
być klikalne. Wysyłka dokłada je z powrotem, a podgląd w panelu pokazuje to
samo (`tgLinkuj` w tg-preview.js), bo makieta ma być tym, co zobaczy kanał.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import contentbot  # noqa: E402

KATALOG = Path(__file__).resolve().parent
MODUL = KATALOG.parent / "static" / "js" / "tg-preview.js"
HARNESS = KATALOG / "tg_preview_harness.mjs"

# Post #11 ze starego kanału, z uchwytem po zmianie — dokładnie ten z kolejki.
SPOTS = ("🚨 Only 2 Spots Left 🚨\n\n"
         "Do Not Wait! We Are Closing Our Spots Once Those 2 Are Taken! ⚠️\n\n"
         "📩 Message @forex_passing_admin if you’re ready to stop struggling and "
         "start working toward payouts — without lifting a finger\n\n"
         "👉 Get started")
STOPKA = ("👉 Click here to send us a message\n\n"
          "➡ Message @forex_passing_admin to get started!\n\n"
          "❕ This is the OFFICIAL channel. Our only admin is @forex_passing_admin "
          "if anyone contacts you from a different name, it's a scam.")
ADMIN = "https://t.me/forex_passing_admin?text="


def _linki(tekst):
    import re
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', tekst)


def test_get_started_z_archiwum_jest_linkiem_do_admina():
    wynik = contentbot.dolinkuj(SPOTS, zapas="")
    linki = _linki(wynik)
    assert ("Get started" in [t for _, t in linki])
    assert ("@forex_passing_admin" in [t for _, t in linki])
    for adres, _ in linki:
        assert adres.startswith(ADMIN)
        prosba = parse_qs(urlparse(adres.replace("&amp;", "&")).query)["text"][0]
        assert prosba == contentbot.PROSBA_DM


def test_stopka_linkuje_wezwanie_i_kazda_wzmianke_admina():
    linki = [t for _, t in _linki(contentbot.dolinkuj(STOPKA, zapas=""))]
    assert linki == ["Click here to send us a message",
                     "@forex_passing_admin", "@forex_passing_admin"]


def test_zwrot_w_srodku_zdania_zostaje_tekstem():
    """„to get started!" to część zdania — klikalna jest tam wzmianka."""
    wynik = contentbot.dolinkuj("➡ Message @forex_passing_admin to get started!", zapas="")
    assert ">get started" not in wynik
    assert "to get started!" in wynik


def test_widoczny_tekst_sie_nie_zmienia():
    for tekst in (SPOTS, STOPKA):
        assert contentbot.dlugosc_widoczna(contentbot.dolinkuj(tekst, zapas="")) \
            == contentbot.dlugosc_widoczna(tekst)


def test_idempotentne_i_nie_rusza_istniejacych_linkow():
    raz = contentbot.dolinkuj(SPOTS, zapas="")
    assert contentbot.dolinkuj(raz, zapas="") == raz
    reczny = '👉 <a href="https://example.com/x">Get started</a>\n@forex_passing_admin'
    wynik = contentbot.dolinkuj(reczny, zapas="")
    assert '<a href="https://example.com/x">Get started</a>' in wynik
    assert wynik.count("<a ") == 2


def test_bez_wzmianki_idzie_na_zapasowy_desk():
    wynik = contentbot.dolinkuj("👉 Get started", zapas="https://t.me/desk_x")
    assert _linki(wynik)[0][0].startswith("https://t.me/desk_x?text=")
    assert contentbot.dolinkuj("👉 Get started", zapas="") == "👉 Get started"


def test_inne_wzmianki_nie_sa_linkowane():
    tekst = "Join @forex_passing_payouts for proof.\n\n👉 Get started"
    wynik = contentbot.dolinkuj(tekst, zapas="https://t.me/desk_x")
    assert "@forex_passing_payouts for" in wynik
    assert ">@forex_passing_payouts<" not in wynik


def test_publikacja_wysyla_tresc_z_linkami_a_baza_trzyma_archiwum(monkeypatch):
    from app.db import SessionLocal, init_db
    from app.models import ChannelPost
    init_db()
    s = SessionLocal()
    wyslane = []
    monkeypatch.setattr(contentbot, "chat_id", lambda kanal: "@kanal")
    monkeypatch.setattr(contentbot.telegram, "send_content",
                        lambda czat, tekst, **k: wyslane.append(tekst)
                        or (True, "", {"message_id": 9}))
    monkeypatch.setattr(contentbot.telegram, "post_url", lambda d: "")
    monkeypatch.setattr(contentbot.reach, "po_publikacji", lambda *a, **k: {})
    p = ChannelPost(channel="mgmt", kind="text", body=SPOTS, proof="",
                    status="approved", origin="panel")
    s.add(p)
    s.commit()
    try:
        assert contentbot.opublikuj(s, p)["posted"]
        assert '">Get started</a>' in wyslane[0]
        assert p.body == SPOTS
    finally:
        s.delete(p)
        s.commit()
        s.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="brak node")
@pytest.mark.parametrize("tekst", [SPOTS, STOPKA, "👉 Get started",
                                   "Join @forex_passing_payouts\n\nContact us.",
                                   '<b>👉 Get started</b>\n\n<a href="https://x.io">here</a>'])
@pytest.mark.parametrize("zapas", ["", "https://t.me/desk_x"])
def test_podglad_linkuje_tak_samo_jak_wysylka(tekst, zapas):
    wynik = subprocess.run(
        ["node", str(HARNESS), str(MODUL),
         json.dumps([{"fn": "tgLinkuj", "args": [tekst, zapas]}])],
        capture_output=True, text=True, timeout=30)
    assert wynik.returncode == 0, wynik.stderr[:400]
    assert json.loads(wynik.stdout)[0] == contentbot.dolinkuj(tekst, zapas=zapas)
