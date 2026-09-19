"""Zamówienie wystawione ręcznie z panelu — klient płaci poza Stripe'em.

Tu chodzi o pieniądze, które przychodzą przelewem, więc mail z instrukcją jest
jedynym, co klient dostaje: jeśli poda zły adres portfela albo poleci dwa razy
z inną kwotą, pieniądze przepadają u kogoś obcego. Dlatego testy pilnują trzech
rzeczy: że w mailu ląduje DOKŁADNIE ten adres, który admin podał przy tym
zamówieniu (nigdy zmyślony, nigdy inny), że instrukcja leci raz na wejście
w stan „czekamy na wpłatę", i że ręczne zamówienie nie rusza salda klienta
przed zaksięgowaniem wpłaty.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Order, Product, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))


@pytest.fixture
def maile(monkeypatch):
    """Podstawka pod `send`, nie na nim — `send` w trakcie requestu tylko
    kolejkuje, a realna wysyłka idzie dopiero w BackgroundTask."""
    zebrane = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: zebrane.append((event, to, ctx or {})))
    return zebrane


@pytest.fixture
def portfel(monkeypatch):
    """Skonfigurowany portfel — w testach ADRES JEST FIKCYJNY i nieużywalny."""
    monkeypatch.setattr(notify.settings, "crypto_wallet", "TTestWalletAddress000")
    monkeypatch.setattr(notify.settings, "crypto_network", "USDT · TRC20")
    monkeypatch.setattr(notify.settings, "crypto_memo", "")


def _trader(*, credits: float = 0.0, marketing: bool = True) -> tuple[int, str]:
    email = f"manual{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Manual Tester", first_name="Jan",
                referral_code=email.split("@")[0].upper(),
                credits_usd=credits, notify_marketing=marketing)
    s.add(tr); s.commit()
    tid = tr.id; s.close()
    return tid, email


def _dodaj(tid: int, **kw):
    body = {"trader_id": tid, "product_key": "2step-25k", **kw}
    return client.post("/api/admin/orders", json=body, headers=ADMIN)


def test_reczne_zamowienie_powstaje_jako_nieoplacone(maile):
    tid, _ = _trader()
    r = _dodaj(tid)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "pending" and d["flag"] == "awaiting_crypto"
    s = SessionLocal()
    o = s.get(Order, d["id"])
    # `provider` odróżnia to zamówienie od stripe'owego w panelu i w historii —
    # nikt nie ma szukać webhooka, którego nigdy nie było.
    assert o.provider == "manual" and o.status == "pending" and o.account_id is None
    s.close()


def test_kwota_domyslnie_z_cennika(maile):
    tid, _ = _trader()
    s = SessionLocal()
    cena = s.query(Product).filter(Product.key == "2step-25k").first().price_usd
    s.close()
    assert _dodaj(tid).json()["amount_usd"] == cena


def test_kwote_da_sie_nadpisac(maile):
    """Klient dogadany na inną kwotę (rabat, dopłata) — panel ma to przyjąć,
    bo inaczej admin obchodziłby to ręcznie w bazie."""
    tid, _ = _trader()
    assert _dodaj(tid, amount_usd=149.5).json()["amount_usd"] == 149.5


def test_ujemna_kwota_odrzucona(maile):
    tid, _ = _trader()
    assert _dodaj(tid, amount_usd=-10).status_code == 400


def test_nieznany_produkt_i_trader_odrzuceni(maile):
    tid, _ = _trader()
    assert client.post("/api/admin/orders",
                       json={"trader_id": tid, "product_key": "nie-ma-takiego"},
                       headers=ADMIN).status_code == 404
    assert client.post("/api/admin/orders",
                       json={"trader_id": 999999, "product_key": "2step-25k"},
                       headers=ADMIN).status_code == 404


def test_mail_niesie_produkt_kwote_i_referencje(maile):
    tid, email = _trader()
    d = _dodaj(tid, amount_usd=299).json()
    moje = [m for m in maile if m[1] == email]
    assert len(moje) == 1 and moje[0][0] == "order_awaiting_payment"
    ctx = moje[0][2]
    assert ctx["amount"] == 299 and ctx["product_label"]
    # Przelew krypto nie niesie tytułu — referencja to jedyne, czym klient
    # połączy swoją wpłatę z tym zamówieniem w odpowiedzi na maila.
    assert ctx["reference"] == f"PTF-{d['id']}"
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert f"PTF-{d['id']}" in tresc and "299" in tresc


def test_mail_podaje_kwote_z_groszami(maile):
    """Kwota w instrukcji przelewu MUSI mieć grosze. Wspólny formater maili
    zaokrągla do pełnych dolarów — klient dostawał polecenie wysłania $200
    zamiast $199,50 i wpłata nie zgadzałaby się z zamówieniem."""
    tid, email = _trader()
    _dodaj(tid, amount_usd=199.5)
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "199.50" in tresc and "$200" not in tresc
    assert "199.50" in notify._render_html("order_awaiting_payment", ctx, "x")


def test_admin_moze_wystawic_bez_maila(maile):
    """Zamówienie spisane po fakcie (klient już zapłacił) nie ma prosić o wpłatę."""
    tid, email = _trader()
    assert _dodaj(tid, notify_trader=False).json()["emailed"] is False
    assert [m for m in maile if m[1] == email] == []


def test_adres_z_panelu_ladzie_w_zamowieniu_i_w_mailu(maile):
    """Adresy są rotowane, więc admin wpisuje je ręcznie przy zamówieniu.
    Podany adres ma trafić i do bazy (po miesiącach widać, dokąd te pieniądze
    miały pójść), i wprost do maila — klient nie ma innego źródła."""
    tid, email = _trader()
    d = _dodaj(tid, payment_address="TPanelAddress111",
               payment_network="USDT · TRC20").json()
    assert d["payment_details"] is True
    s = SessionLocal(); o = s.get(Order, d["id"]); s.close()
    assert o.payment_address == "TPanelAddress111" and o.payment_network == "USDT · TRC20"
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "TPanelAddress111" in tresc and "USDT · TRC20" in tresc
    assert "TPanelAddress111" in notify._render_html("order_awaiting_payment", ctx, "x")


def test_dlugi_adres_nie_rozpycha_maila(maile):
    """Adres portfela to 34–44 znaki bez spacji. Bez łamania w środku słowa
    szablon puchł do 591 px przy oknie 360 px i klient musiał przewijać maila
    w bok, żeby zobaczyć KONIEC adresu — czyli to, co musi sprawdzić."""
    tid, email = _trader()
    _dodaj(tid, payment_address="DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy")
    ctx = [m for m in maile if m[1] == email][0][2]
    html = notify._render_html("order_awaiting_payment", ctx, "x")
    wiersz = [w for w in html.split("<tr>") if "DRpbCBM" in w][0]
    assert "word-break:break-all" in wiersz


def test_adres_z_zamowienia_wygrywa_z_konfiguracja(maile, portfel):
    """Konfiguracja to tylko zapasowy, stały adres. Gdy admin poda własny,
    tamten NIE MOŻE wejść do maila — klient zapłaciłby nie tam, gdzie trzeba."""
    tid, email = _trader()
    _dodaj(tid, payment_address="TPanelAddress222")
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "TPanelAddress222" in tresc and "TTestWalletAddress000" not in tresc
    assert "TTestWalletAddress000" not in notify._render_html("order_awaiting_payment", ctx, "x")


def test_bez_adresu_w_panelu_wchodzi_ten_z_konfiguracji(maile, portfel):
    tid, email = _trader()
    _dodaj(tid)
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "TTestWalletAddress000" in tresc and "USDT · TRC20" in tresc
    assert "TTestWalletAddress000" in notify._render_html("order_awaiting_payment", ctx, "x")


def test_flaga_zapisuje_adres_podany_przy_niej(maile):
    tid, email = _trader()
    d = _dodaj(tid, flag="", notify_trader=False).json()
    r = client.post(f"/api/admin/orders/{d['id']}/flag",
                    json={"flag": "awaiting_crypto", "payment_address": "TFromFlag444",
                          "payment_network": "TON"}, headers=ADMIN).json()
    assert r["emailed"] is True and r["payment_details"] is True
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "TFromFlag444" in tresc and "TON" in tresc


def test_pusty_adres_nie_kasuje_zapisanego(maile):
    """Klient dostał adres mailem. Kolejny zapis bez wpisanego adresu (admin
    tylko zmienił flagę) nie może wyczyścić śladu, dokąd wpłata miała trafić."""
    tid, _ = _trader()
    d = _dodaj(tid, payment_address="TKeepThis333", flag="", notify_trader=False).json()
    assert client.post(f"/api/admin/orders/{d['id']}/flag",
                       json={"flag": "awaiting_crypto"}, headers=ADMIN).status_code == 200
    s = SessionLocal(); o = s.get(Order, d["id"]); s.close()
    assert o.payment_address == "TKeepThis333"


def test_bez_konfiguracji_mail_nie_zmysla_adresu(maile, monkeypatch):
    monkeypatch.setattr(notify.settings, "crypto_wallet", "")
    tid, email = _trader()
    # Panel MUSI się o tym dowiedzieć: inaczej admin zostaje w przekonaniu, że
    # klient wie, gdzie zapłacić, a mail poszedł bez adresu.
    assert _dodaj(tid).json()["payment_details"] is False
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("order_awaiting_payment", ctx)
    assert "Wallet address" not in tresc
    assert "separate message" in tresc, "mail nie mówi klientowi, skąd weźmie dane"


def test_instrukcja_idzie_mimo_wypisania_sie_z_ofert(monkeypatch):
    """To mail do WŁASNEGO zamówienia klienta, nie oferta — bramka preferencji
    nie może go zatrzymać. Test idzie prawdziwą ścieżką wysyłki, bo bramka
    siedzi w środku `_send_teraz`."""
    wyslane = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): wyslane.append(msg["To"])

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.atrapa")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    tid, email = _trader(marketing=False)
    _dodaj(tid)
    assert email in wyslane


def test_wystawienie_nie_rusza_kredytow_klienta(maile):
    """Kredyty schodzą przy domknięciu płatności, nie przy wystawieniu — inaczej
    nieopłacone zamówienie zjadałoby saldo klienta."""
    tid, _ = _trader(credits=50.0)
    d = _dodaj(tid).json()
    s = SessionLocal()
    assert s.get(Trader, tid).credits_usd == 50.0
    assert (s.get(Order, d["id"]).credits_used or 0) == 0
    s.close()


def test_flaga_wysyla_instrukcje_tylko_przy_wejsciu(maile):
    tid, email = _trader()
    oid = _dodaj(tid, flag="", notify_trader=False).json()["id"]
    r = client.post(f"/api/admin/orders/{oid}/flag",
                    json={"flag": "awaiting_crypto"}, headers=ADMIN)
    assert r.json()["emailed"] is True
    assert len([m for m in maile if m[1] == email]) == 1
    # Drugi klik w ten sam przycisk (albo powrót do widoku) nie ma wysyłać
    # klientowi drugiej instrukcji o tej samej należności.
    r2 = client.post(f"/api/admin/orders/{oid}/flag",
                     json={"flag": "awaiting_crypto"}, headers=ADMIN)
    assert r2.json()["emailed"] is False
    assert len([m for m in maile if m[1] == email]) == 1


def test_zdjecie_i_ponowne_nadanie_flagi_wysyla_ponownie(maile):
    """Świadome zdjęcie i nadanie flagi to nowa decyzja admina — wtedy klient
    ma dostać instrukcję jeszcze raz (np. po zmianie kwoty)."""
    tid, email = _trader()
    oid = _dodaj(tid, notify_trader=False).json()["id"]
    client.post(f"/api/admin/orders/{oid}/flag", json={"flag": ""}, headers=ADMIN)
    client.post(f"/api/admin/orders/{oid}/flag",
                json={"flag": "awaiting_crypto"}, headers=ADMIN)
    assert len([m for m in maile if m[1] == email]) == 1


def test_oplacone_zamowienie_nie_dostaje_instrukcji_wplaty(maile):
    tid, email = _trader()
    oid = _dodaj(tid, notify_trader=False).json()["id"]
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    maile.clear()
    r = client.post(f"/api/admin/orders/{oid}/flag",
                    json={"flag": "awaiting_crypto"}, headers=ADMIN)
    assert r.json()["emailed"] is False
    assert [m for m in maile if m[1] == email] == []


def test_domkniecie_recznej_platnosci_tworzy_konto(maile):
    """Cała ścieżka: wystawiam ręcznie, wpłata przychodzi, klikam Mark paid."""
    tid, _ = _trader()
    oid = _dodaj(tid).json()["id"]
    r = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r.status_code == 200 and r.json()["account_id"]
    s = SessionLocal()
    o = s.get(Order, oid)
    assert o.status == "paid" and o.flag is None and o.account_id
    # Ręczne zamówienie to normalny przychód, nie grant — `provider` musi zostać.
    assert o.provider == "manual"
    s.close()


def test_recznemu_zamowieniu_nie_dosyla_maila_o_porzuconym_koszyku(maile):
    """Zamówieniem z panelu zarządza admin. Automat pisałby do klienta, że
    „karta mogła zostać odrzucona" — o płatności, której nigdy nie zaczynał."""
    from datetime import datetime, timedelta, timezone

    from app.main import _checkout_recovery
    tid, email = _trader()
    oid = _dodaj(tid, flag="", notify_trader=False).json()["id"]
    s = SessionLocal()
    s.get(Order, oid).created_at = (datetime.now(timezone.utc).replace(tzinfo=None)
                                    - timedelta(hours=5))
    s.commit(); s.close()
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []
