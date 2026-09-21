"""Zgłoszenie ma własny znak, a nie klucz główny z bazy.

Temat maila do klienta brzmiał „Support replied to your ticket #9". To numer
kolejny z tabeli, więc mówił odbiorcy, ile zgłoszeń ma w sumie cała firma —
a dwa maile obok siebie dawały tempo ich przyrostu. Klient ma widzieć
oznaczenie SWOJEJ sprawy, nie licznik naszego biznesu.

Znak jest losowy (31^6), z alfabetu bez znaków, które się mylą przy
przepisywaniu z maila.
"""
import os
import re
import tempfile

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SupportTicket, Trader, nowy_znak_biletu  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


@pytest.fixture
def klient():
    """Trader z nagłówkiem autoryzacji, plus sprzątanie po teście."""
    numer = next(LICZNIK)
    s = SessionLocal()
    tr = Trader(email=f"znak{numer}@test.pl", password_hash=auth.hash_password("haslo1234"),
                full_name="Znak Tester", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid, email = tr.id, tr.email
    s.close()
    # Bez sprzatania: kazdy test dostaje WLASNEGO tradera i pyta wylacznie
    # o jego zgloszenia, a baza to plik jednorazowy. Kasowanie tradera ciagnie
    # za soba powiadomienia i telemetrie (klucze obce), czyli wiecej halasu
    # w tesciach niz pozytku.
    yield {"Authorization": f"Bearer {auth.make_token(tid)}"}, tid, email


def _zaloz(h, temat="Cannot log in"):
    r = client.post("/api/me/tickets", headers=h,
                    json={"subject": temat, "message": "Help please"})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
#  Sam znak
# --------------------------------------------------------------------------- #
def test_znak_nie_ma_znakow_ktore_sie_myla():
    """Klient przepisuje go z maila albo dyktuje przez telefon."""
    znaki = "".join(nowy_znak_biletu() for _ in range(400))

    assert re.fullmatch(r"[A-Z2-9]+", znaki), "tylko wersaliki i cyfry"
    for mylacy in "OIL01":
        assert mylacy not in znaki, f"{mylacy} myli sie z innym znakiem"


def test_znak_jest_losowy_a_nie_kolejny():
    """Sedno usterki: z dwóch znaków nie wolno policzyć, ile ich wydano."""
    partia = [nowy_znak_biletu() for _ in range(200)]

    assert len(set(partia)) == len(partia), "powtórka w 200 losowaniach"
    assert partia != sorted(partia), "kolejność losowania nie może być rosnąca"
    assert all(len(z) == 6 for z in partia)


# --------------------------------------------------------------------------- #
#  Co dostaje klient
# --------------------------------------------------------------------------- #
def test_nowe_zgloszenie_dostaje_znak(klient):
    h, _, _ = klient

    dane = _zaloz(h)

    assert re.fullmatch(r"[A-Z2-9]{6}", dane["ref"])
    assert dane["ref"] != str(dane["id"])


def test_lista_i_watek_niosa_znak(klient):
    h, _, _ = klient
    ref = _zaloz(h)["ref"]

    lista = client.get("/api/me/tickets", headers=h).json()
    watek = client.get(f"/api/me/tickets/{lista[0]['id']}", headers=h).json()

    assert lista[0]["ref"] == ref
    assert watek["ref"] == ref


def test_temat_maila_niesie_znak_a_nie_numer_z_bazy(klient, monkeypatch):
    """To jest dokładnie ten napis, który widać było w zakładce Mail.

    Podstawka siedzi pod `send`, bo samo `send` tylko kolejkuje; temat składamy
    potem prawdziwym rendererem, żeby test obejmował całą drogę od odpowiedzi
    admina do tego, co klient przeczyta w skrzynce.
    """
    h, _, email = klient
    zgloszenie = _zaloz(h)
    numer, ref = zgloszenie["id"], zgloszenie["ref"]
    wyslane = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: wyslane.append((event, to, ctx or {})))

    r = client.post(f"/api/admin/tickets/{numer}/reply", headers=ADMIN,
                    json={"message": "Odpisujemy", "close": False})
    assert r.status_code == 200

    do_klienta = [ctx for ev, to, ctx in wyslane if ev == "ticket_reply" and to == email]
    assert do_klienta, "mail do klienta w ogóle nie poszedł"
    ctx = do_klienta[0]
    assert ctx.get("ticket_ref") == ref
    assert "ticket_id" not in ctx, "klucz główny nie ma po co jechać do szablonu"

    temat, _tresc = notify._render("ticket_reply", ctx)
    assert f"#{ref}" in temat
    assert f"#{numer}" not in temat, "klucz główny nie ma prawa wrócić do tematu"


def test_brak_znaku_nie_daje_none_w_temacie():
    """Wiersz sprzed tej zmiany, gdyby uciekł uzupełnieniu: lepiej bez numeru
    niż „ticket #None"."""
    temat, _ = notify._render("ticket_reply",
                              {"name": "Ktos", "subject": "X", "ticket_ref": None})

    assert "None" not in temat
    assert "replied to your ticket" in temat


# --------------------------------------------------------------------------- #
#  Wiersze sprzed zmiany
# --------------------------------------------------------------------------- #
def test_stare_zgloszenia_dostaja_znak_przy_starcie(klient):
    """`_nadaj_znaki_biletom` ma nadać KAŻDEMU inny znak — jedno zapytanie ze
    stałą dałoby wszystkim ten sam."""
    h, tid, _ = klient
    _zaloz(h, "Stary jeden"); _zaloz(h, "Stary dwa"); _zaloz(h, "Stary trzy")
    s = SessionLocal()
    stare = s.query(SupportTicket).filter(SupportTicket.trader_id == tid).all()
    for t in stare:
        t.ref = None
    s.commit(); s.close()

    db._nadaj_znaki_biletom()

    s = SessionLocal()
    znaki = [t.ref for t in
             s.query(SupportTicket).filter(SupportTicket.trader_id == tid).all()]
    s.close()
    assert all(znaki), "został wiersz bez znaku"
    assert len(set(znaki)) == len(znaki), "dwa zgłoszenia z tym samym znakiem"


def test_uzupelnianie_nie_rusza_juz_nadanych(klient):
    h, tid, _ = klient
    ref = _zaloz(h)["ref"]

    db._nadaj_znaki_biletom()

    s = SessionLocal()
    t = s.query(SupportTicket).filter(SupportTicket.trader_id == tid).first()
    zapisany = t.ref
    s.close()
    assert zapisany == ref, "nadany znak jest w mailu u klienta — nie wolno go zmienić"
