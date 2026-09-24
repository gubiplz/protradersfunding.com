"""Wygląd maili spod marki landingu — ręcznych z panelu i automatów.

Ten sam papier co maile landingu (`api/_lib/emails.js` w repo marki): biała
kartka na szarym, logo, temat jako nagłówek, szary tekst, jedna zielona
pigułka, drobna stopka i pod kartką marka z domeną nadawcy. Tekst z panelu
jest zwykłym tekstem, więc struktura bierze się z konwencji w tekście.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import lead_mail  # noqa: E402


@pytest.fixture(autouse=True)
def nadawca(monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", "Desk <contact@desk-probe.test>")
    monkeypatch.setattr(lead_mail.settings, "lead_telegram_channel_url", "https://t.me/kanal_probe")


def test_temat_jest_naglowkiem_a_odpowiedz_nie():
    kod = lead_mail._html_z_tekstu("Hi Ada,\n\nText.", "Your account is ready")
    assert "<h1" in kod and "Your account is ready</h1>" in kod
    assert "<h1" not in lead_mail._html_z_tekstu("Hi Ada,\n\nText.", "Re: Your account")


def test_pigulka_na_srodku_z_kolorem_trzy_razy():
    kod = lead_mail._html_z_tekstu("Hi\n\nhttps://t.me/desk_probe\n\nWe answer within a day.")
    assert 'bgcolor="#16a34a"' in kod and "border-radius:980px" in kod
    assert kod.count("background-color:#16a34a") == 2 and 'align="center"' in kod
    # Krótkie zdanie pod przyciskiem = wyśrodkowany dopisek.
    assert "text-align:center\">We answer within a day.</p>" in kod


def test_kroki_i_etykieta_sekcji():
    tekst = ("Hi Ada,\n\nWHAT HAPPENS NEXT\n\n1. Message us: Onboarding happens there.\n"
             "2. We check the rules: Before anything else.\n3. We trade")
    kod = lead_mail._html_z_tekstu(tekst)
    assert "WHAT HAPPENS NEXT</div>" in kod and "letter-spacing:.08em" in kod
    assert ">Message us</div>" in kod and ">Onboarding happens there.</div>" in kod
    assert ">We trade</div>" in kod and "1. Message us" not in kod


def test_stopka_pod_kartka_z_domena_nadawcy_bez_telegramu():
    kod = lead_mail._html_z_tekstu("Hi\n\nhttps://example.test/portal\n\n--\nForex Passing\nWhy you got this.")
    # jak landing: szare linki bez podkreślenia (inaczej klient pocztowy sam
    # robi z nich niebieskie), obok przycisku nic więcej klikalnego
    assert 'href="https://desk-probe.test"' in kod and 'href="mailto:contact@desk-probe.test"' in kod
    assert kod.count("<a ") == 3 and kod.count("text-decoration:none") >= 2
    assert "Trading carries risk" in kod and "Why you got this." in kod
    assert "Telegram" not in kod                    # link do portalu nie obiecuje Telegrama


def test_stopka_podaje_contact_a_nie_noreply(monkeypatch):
    """2026-09-24: nadawca „noreply@" lądował w stopce jako adres do pisania."""
    monkeypatch.setattr(lead_mail.settings, "lead_mail_from", "Forex Passing <noreply@marka-probe.test>")
    monkeypatch.setattr(lead_mail.settings, "lead_mail_contact", "")
    kod = lead_mail._html_z_tekstu("Hi\n\nText.")
    assert "contact@marka-probe.test" in kod and "noreply@" not in kod
    assert lead_mail._odpowiedz_do() == "Forex Passing <contact@marka-probe.test>"
    monkeypatch.setattr(lead_mail.settings, "lead_mail_contact", "hello@marka-probe.test")
    assert "hello@marka-probe.test" in lead_mail._html_z_tekstu("Hi\n\nText.")


def test_pojedynczy_enter_zostaje_lamaniem_i_html_jest_escapowany():
    kod = lead_mail._html_z_tekstu("Hi <b>Ada</b>,\n\nLine one\nLine two")
    assert "Line one<br>Line two" in kod and "&lt;b&gt;Ada" in kod and "<b>Ada" not in kod


def test_wysylka_niesie_temat_do_html(monkeypatch):
    monkeypatch.setattr(lead_mail.settings, "smtp_host", "smtp.probe.test")
    monkeypatch.setattr(lead_mail.settings, "resend_api_key", "")
    poszly = []
    monkeypatch.setattr(lead_mail, "_smtp_transport", poszly.append)
    ok, _ = lead_mail.wyslij("ada@probe.test", "Your account is ready", "Hi Ada,\n\nText.",
                             tylko_nadawca=True)
    assert ok
    html = poszly[0].get_body(preferencelist=("html",)).get_content()
    assert "Your account is ready</h1>" in html
