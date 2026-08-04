"""Mail nie moze sie wysylac W TRAKCIE requestu, tylko po odeslaniu odpowiedzi.

SMTP do Brevo to kilkanascie round-tripow — zmierzone na atrapie z RTT 60 ms
sama rejestracja czekala 722 ms, z czego ~700 ms to poczta. Runtime Pythona na
Vercelu strumieniuje odpowiedz, wiec przeniesienie wysylki do BackgroundTask
oddaje klientowi odpowiedz od razu (zmierzone: 20 ms), a mail i tak wychodzi
w tym samym wywolaniu funkcji.

Test idzie po ASGI, a nie przez TestClient, bo TestClient czeka na
BackgroundTask i kolejnosc zdarzen bylaby przez to niewidoczna.
"""
import asyncio
import json
import os
import tempfile
import time

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import notify  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()


async def _post(sciezka: str, dane: dict) -> list[tuple[str, float]]:
    """Wolamy aplikacje wprost jako ASGI i notujemy, KIEDY poszla odpowiedz."""
    cialo = json.dumps(dane).encode()
    zdarzenia: list[tuple[str, float]] = []

    async def receive():
        return {"type": "http.request", "body": cialo, "more_body": False}

    async def send(msg):
        zdarzenia.append((msg["type"], time.perf_counter()))

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "POST", "path": sciezka,
             "raw_path": sciezka.encode(), "query_string": b"", "root_path": "",
             "scheme": "http", "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
             "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(cialo)).encode())]}
    await app(scope, receive, send)
    return zdarzenia


def test_mail_wychodzi_dopiero_po_odeslaniu_odpowiedzi(monkeypatch):
    wyslane: list[tuple[str, float]] = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: wyslane.append((event, time.perf_counter())))
    monkeypatch.setattr(notify, "_notify_admins_teraz", lambda *a, **k: None)

    zdarzenia = asyncio.run(_post("/api/auth/signup", {
        "email": "po-odpowiedzi@test.pl", "password": "haslo12345",
        "full_name": "Kto To", "terms_accepted": True}))

    status = next(m for m in zdarzenia if m[0] == "http.response.start")
    assert status, "brak odpowiedzi"
    ostatni_kawalek = max(t for typ, t in zdarzenia if typ == "http.response.body")

    assert wyslane, "mail przepadl — odlozenie wysylki nie moze jej gubic"
    for event, kiedy in wyslane:
        assert kiedy > ostatni_kawalek, \
            f"'{event}' poszedl W TRAKCIE requestu — klient czeka na SMTP"


def test_poza_requestem_wysylka_idzie_od_razu(monkeypatch):
    """Cron i poller nie maja requestu, w ktorym mozna by cokolwiek odlozyc."""
    wyslane = []
    monkeypatch.setattr(notify, "_send_teraz", lambda *a, **k: wyslane.append(a))
    notify.send("welcome", "cron@test.pl", {"name": "Cron"})
    assert wyslane, "bez requestu mail musi pojsc natychmiast, inaczej nigdy nie pojdzie"
