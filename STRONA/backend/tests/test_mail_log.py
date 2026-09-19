"""Dziennik doręczeń maili: cicha awaria SMTP musi zostawić ślad.

`notify._send_teraz` łapie wyjątek SMTP, żeby nie wywrócić requestu — słusznie,
ale przez to „mail nie doszedł" wyglądał identycznie jak „mail nigdy nie
wyszedł". Dziennik rozstrzyga to jedno pytanie, a panel (zakładka Mail +
kafelek na Overview) pokazuje porażki, zanim zgłosi je klient.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}

CTX = {"name": "Jan Dziennik", "setup_url": "https://x.test/portal?reset=abc",
       "portal_url": "https://x.test/portal"}


def test_udana_wysylka_trafia_do_dziennika(monkeypatch):
    class _DzialajacySmtp:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): pass

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.test", raising=False)
    monkeypatch.setattr(notify.smtplib, "SMTP", _DzialajacySmtp)

    notify._send_teraz("portal_invite", "dziennik-ok@test.pl", CTX)

    d = client.get("/api/admin/mail-log", headers=ADMIN).json()
    wpis = next(m for m in d["entries"] if m["to"] == "dziennik-ok@test.pl")
    assert wpis["ok"] is True
    assert wpis["error"] is None
    assert wpis["event"] == "portal_invite"
    assert wpis["subject"]


def test_adres_importowy_nie_idzie_do_smtp(monkeypatch):
    """Konta z importu (`@imported.local`) nie mają skrzynek: każda wysyłka to
    twardy bounce w Brevo, a seria bounców psuje reputację nadawcy. Ręczne
    akcje admina na takich kontach (faza, wypłata) nie mogą strzelać mailami."""
    class _EksplodujacySmtp:
        def __init__(self, *a, **k):
            raise AssertionError("SMTP nie może być dotknięty dla @imported.local")

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.test", raising=False)
    monkeypatch.setattr(notify.smtplib, "SMTP", _EksplodujacySmtp)

    notify._send_teraz("account_funded", "rory.sinclair@imported.local",
                       {"name": "Rory Sinclair", "login": "10011911015", "split": 80})


def test_brak_smtp_to_tez_porazka_a_nie_ciche_wyslane():
    """Bez `SMTP_HOST` mail nie opuszcza serwera — dziennik ma o tym mówić.

    Do 2026-08-19 ta gałąź drukowała maila na konsolę i zapisywała wpis bez
    błędu, więc panel pokazywał zielone „wysłane" dla czegoś, czego nikt nigdy
    nie dostał, a kafelek porażek stał na zerze. Dokładnie ta cisza sprawiła, że
    „maile z platformy nie dochodzą" przeżyło na produkcji tygodnie.
    """
    notify._send_teraz("portal_invite", "dziennik-bez-smtp@test.pl", CTX)

    d = client.get("/api/admin/mail-log", headers=ADMIN).json()
    wpis = next(m for m in d["entries"] if m["to"] == "dziennik-bez-smtp@test.pl")
    assert wpis["ok"] is False
    assert "SMTP_HOST" in wpis["error"]


def test_awaria_smtp_zostawia_slad(monkeypatch):
    class _PadajacySmtp:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("brevo padło")

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.test", raising=False)
    monkeypatch.setattr(notify.smtplib, "SMTP", _PadajacySmtp)

    notify._send_teraz("portal_invite", "dziennik-pad@test.pl", CTX)

    d = client.get("/api/admin/mail-log", headers=ADMIN).json()
    wpis = next(m for m in d["entries"] if m["to"] == "dziennik-pad@test.pl")
    assert wpis["ok"] is False
    assert "brevo padło" in wpis["error"]
    assert d["failed_7d"] >= 1

    # Overview czerpie licznik ze /api/stats — kafelek ma się zapalić sam.
    assert client.get("/api/stats", headers=ADMIN).json()["mail_failed_7d"] >= 1


def test_dziennik_tylko_dla_admina():
    assert client.get("/api/admin/mail-log").status_code in (401, 403)
