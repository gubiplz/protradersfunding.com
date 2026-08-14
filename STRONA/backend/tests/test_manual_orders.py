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
from sqlalchemy import func  # noqa: E402

from app import auth, catalog, notify  # noqa: E402
from app import main as glowny  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Lead, Order, PoolAccount, Product, Trader  # noqa: E402

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


def _dodaj_po_mailu(email: str, **kw):
    """Zamówienie wystawione na sam adres — ścieżka z karty leada."""
    body = {"email": email, "product_key": "2step-25k", "flag": "",
            "notify_trader": False, **kw}
    return client.post("/api/admin/orders", json=body, headers=ADMIN)


def _mail_leada() -> str:
    """Adres nietknięty przez inne pliki testów.

    `DATABASE_URL` ustawia `setdefault`, więc cały pakiet dzieli JEDNĄ bazę,
    a licznik jest osobny w każdym module — `lead1@test.pl` zajmuje już
    `test_admin_leads.py` i kolizja wychodzi dopiero w pełnym przebiegu.
    """
    return f"lead{next(LICZNIK)}@manual-orders.test"


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


def test_mark_paid_od_reki_uzbraja_konto_i_wysyla_poswiadczenia(maile, monkeypatch):
    """Płatność crypto przy realnym MT5: mail z poświadczeniami ma wyjść OD RAZU
    po Mark paid (rachunek z puli), a nie przy najbliższym dziennym cronie —
    inaczej klient stoi z opłaconym zamówieniem i pustą skrzynką."""
    tid, email = _trader()
    oid = _dodaj(tid).json()["id"]

    s = SessionLocal()
    # Kickoff przetwarza WSZYSTKIE konta 'provisioning' po kolei, więc wpisów
    # w puli musi starczyć też dla kont osieroconych przez sąsiednie testy.
    czekajace = (s.query(Account).filter(Account.status == "provisioning",
                                         Account.initial_balance == 25_000).count())
    for i in range(czekajace + 1):
        s.add(PoolAccount(platform_login=f"255{i:04d}", platform_password="Haslo123",
                          platform_server="GOMarketsLtd-Demo", account_size=25_000))
    s.commit(); s.close()

    ust = get_settings()
    monkeypatch.setattr(ust, "mt5_provisioning", True)
    monkeypatch.setattr(ust, "provisioning_source", "pool")

    r = client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    assert r.status_code == 200
    aid = r.json()["account_id"]

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "active"
    assert acc.platform_login and acc.platform_login.startswith("2550")
    assert acc.platform_server == "GOMarketsLtd-Demo"
    # Sprzątamy wpisy puli po sobie — inne testy liczą pulę 25k globalnie.
    s.query(PoolAccount).filter(PoolAccount.platform_login.like("2550%")) \
        .delete(synchronize_session=False)
    s.commit(); s.close()

    assert any(e == "credentials" and to == email for (e, to, _c) in maile)


def test_zamowienie_po_mailu_zaklada_konto(maile):
    """Lead dogadany na Telegramie nie ma konta i nie założy go po to, żeby
    dostać link do zapłaty. Konto powstaje przy wystawianiu zamówienia."""
    email = _mail_leada()
    d = _dodaj_po_mailu(email).json()
    assert d["trader_created"] is True and d["trader_email"] == email
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == email).one()
    # Hasła nie znamy i nie wymyślamy — klient wchodzi przez „forgot password".
    # Gdyby dało się je zgadnąć, link do zapłaty rozdawałby cudze konta.
    assert client.post("/api/auth/login",
                       json={"email": email, "password": ""}).status_code >= 400
    assert s.get(Order, d["id"]).trader_id == tr.id
    s.close()


def test_lead_wchodzi_na_swoje_konto_przez_reset_hasla(maile):
    """Panel obiecuje leadowi wejście przez „Forgot password?" i to musi być prawda.

    Konto zakłada mu dział, więc hasła nie zna nikt. Gdyby ta droga nie działała,
    człowiek zapłaciłby za challenge i nie wszedłby po swoje konto MT5 — a jedyne,
    co dostał, to link do zapłaty.
    """
    email = _mail_leada()
    _dodaj_po_mailu(email)
    assert client.post("/api/auth/forgot", json={"email": email}).status_code == 200
    reset_url = [m for m in maile if m[1] == email][0][2]["reset_url"]
    token = reset_url.split("reset=")[1]
    assert client.post("/api/auth/reset",
                       json={"token": token, "password": "noweHaslo123"}).status_code == 200
    assert client.post("/api/auth/login",
                       json={"email": email, "password": "noweHaslo123"}).status_code == 200


def test_mail_po_zaplacie_daje_leadowi_haslo_do_portalu(maile):
    """Mail z poświadczeniami MT5 mówił „zaloguj się do portalu" komuś, kto nie
    ma czym — konto założył mu dział, a hasło jest losowe.

    To jedyny mail, jaki taki człowiek dostaje po zapłacie, i pierwszy powód,
    żeby wejść do portalu. Jeśli droga do środka nie jest właśnie w nim, każdy
    taki klient kończy w supporcie.
    """
    email = _mail_leada()
    oid = _dodaj_po_mailu(email).json()["id"]
    assert client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN).status_code == 200
    creds = [m for m in maile if m[1] == email and m[0] == "credentials"]
    assert creds, "po zapłacie nie poszedł mail z poświadczeniami"
    url = creds[0][2].get("setup_url") or ""
    assert "reset=" in url, "mail nie niesie drogi do ustawienia hasła"
    assert client.post("/api/auth/reset", json={"token": url.split("reset=")[1],
                                                "password": "zMailaHaslo1"}).status_code == 200
    assert client.post("/api/auth/login",
                       json={"email": email, "password": "zMailaHaslo1"}).status_code == 200


def test_klient_z_wlasnym_haslem_nie_dostaje_linku_do_ustawienia(maile):
    """Ten sam mail idzie do WSZYSTKICH kupujących. Kto zakładał konto sam, ma
    zobaczyć „View Dashboard", a nie sugestię, że jego hasło nie istnieje."""
    tid, email = _trader()
    oid = _dodaj(tid).json()["id"]
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    creds = [m for m in maile if m[1] == email and m[0] == "credentials"]
    assert creds and not creds[0][2].get("setup_url")


def test_po_ustawieniu_hasla_mail_przestaje_je_proponowac(maile):
    """Drugi challenge tego samego klienta to już zwykły zakup — hasło istnieje,
    więc konto nie jest dłużej „założone za kogoś"."""
    email = _mail_leada()
    _dodaj_po_mailu(email)
    client.post("/api/auth/forgot", json={"email": email})
    reset_url = [m for m in maile if m[1] == email and m[0] == "password_reset"][0][2]["reset_url"]
    client.post("/api/auth/reset", json={"token": reset_url.split("reset=")[1],
                                         "password": "wlasneHaslo1"})
    oid = _dodaj_po_mailu(email).json()["id"]
    client.post(f"/api/admin/orders/{oid}/mark-paid", headers=ADMIN)
    creds = [m for m in maile if m[1] == email and m[0] == "credentials"]
    assert creds and not creds[-1][2].get("setup_url")


def test_mail_html_prowadzi_pod_ustawienie_hasla_a_nie_pod_logowanie():
    """Klient czyta wersję HTML, nie tekstową — przycisk w niej ma prowadzić
    tam, gdzie da się wejść, a nie na ekran logowania bez klucza."""
    ctx = {"name": "Anna", "platform_login": "123456789", "platform_password": "abc",
           "platform_server": "MetaQuotes-Demo", "initial_balance": 25000, "steps": 2,
           "setup_url": "https://ptf.test/portal?reset=ATRAPA"}
    html = notify._render_html("credentials", ctx, "x")
    assert "reset=ATRAPA" in html and "Set Your Password" in html
    assert "?view=accounts" not in html
    bez = notify._render_html("credentials", {**ctx, "setup_url": None}, "x")
    assert "?view=accounts" in bez and "Set Your Password" not in bez


def test_lead_nie_wpada_w_slepa_uliczke_przy_rejestracji(maile):
    """Lead nie wie, że ma już konto — więc naturalnie spróbuje się zarejestrować.

    Signup odpowiada niepotwierdzonym adresom „zaloguj się, wyślemy nowy kod"
    (main.py:758). Dla kogoś, komu konto założył dział i kto hasła nie zna, to
    ślepa uliczka kończąca się supportem. Trzyma nas domyślne `email_verified=True`
    z modelu, którego nasz kod nigdzie nie ustawia jawnie — stąd ten test.
    """
    email = _mail_leada()
    _dodaj_po_mailu(email)
    r = client.post("/api/auth/signup", json={"email": email, "password": "haslo12345",
                                              "full_name": "Jan Testowy", "terms_accepted": True})
    assert r.status_code == 400
    assert "Forgot password" in r.json()["detail"]


def test_znany_mail_nie_zaklada_drugiego_konta(maile):
    """Maile w bazie bywają z wielkiej litery (import, Google). Dopasowanie 1:1
    zrobiłoby drugie konto na ten sam adres i rozbiło historię klienta na pół."""
    tid, email = _trader()
    d = _dodaj_po_mailu(email.upper()).json()
    assert d["trader_created"] is False
    s = SessionLocal()
    assert s.query(Trader).filter(func.lower(Trader.email) == email.lower()).count() == 1
    assert s.get(Order, d["id"]).trader_id == tid
    s.close()


def test_nowe_konto_bierze_nazwisko_z_leada(maile):
    """Nazwisko idzie stąd na konto MT5 i na certyfikaty — puste zostawiłoby
    rachunek bez właściciela, choć lead podał je w formularzu."""
    email = _mail_leada()
    s = SessionLocal()
    s.add(Lead(email=email, name="Anna Nowak")); s.commit(); s.close()
    _dodaj_po_mailu(email)
    s = SessionLocal()
    assert s.query(Trader).filter(Trader.email == email).one().full_name == "Anna Nowak"
    s.close()


def test_zaplacone_zamowienie_zapala_bought_na_karcie_leada(maile):
    """Sedno całej ścieżki: lead → link do zapłaty → wpłata → „Bought" w panelu.

    Kolumna nie jest zapisywana, tylko liczona z traderów po mailu, więc gdyby
    zamówienie poszło na konto z innym adresem, dział widziałby leada jako
    niekupującego mimo zaksięgowanej wpłaty.
    """
    email = _mail_leada()
    s = SessionLocal()
    s.add(Lead(email=email, name="Kupujacy Lead")); s.commit(); s.close()

    d = _dodaj_po_mailu(email, amount_usd=349).json()
    def karta():
        return [l for l in client.get("/api/admin/leads", headers=ADMIN).json()
                if l["email"] == email][0]

    # Samo wystawienie linku to jeszcze nie sprzedaż — dopóki nie zapłacił,
    # dział ma go dalej traktować jak leada.
    assert karta()["paid_usd"] == 0
    assert client.post(f"/api/admin/orders/{d['id']}/mark-paid",
                       headers=ADMIN).status_code == 200
    assert karta()["paid_usd"] == 349


def test_link_do_zaplaty_dziala_dla_konta_zalozonego_z_maila(maile):
    """Link jest jedyną rzeczą, którą lead dostaje. Strona /pay musi go przyjąć
    bez logowania — konto istnieje, ale on nie zna do niego hasła."""
    email = _mail_leada()
    oid = _dodaj_po_mailu(email).json()["id"]
    url = client.post(f"/api/admin/orders/{oid}/pay-link", headers=ADMIN).json()["url"]
    assert client.get(url.split("testserver")[-1]).status_code == 200


def test_zamowienie_bez_klienta_odrzucone(maile):
    """Puste żądanie nie może cicho założyć konta na adres z literówki."""
    for body in ({"product_key": "2step-25k"},
                 {"email": "to-nie-jest-mail", "product_key": "2step-25k"},
                 {"email": "", "product_key": "2step-25k"}):
        assert client.post("/api/admin/orders", json=body, headers=ADMIN).status_code == 400


def test_panel_pyta_serwer_o_stawke_z_umowy(maile, monkeypatch):
    """Procentu nie ma w kodzie panelu, bo repo jest publiczne, a to warunek
    handlowy. Panel musi go skądś wziąć, żeby pokazać cenę PRZED kliknięciem."""
    monkeypatch.setattr(glowny.settings, "partner_discount_pct", 20.0)
    r = client.get("/api/admin/partner-terms", headers=ADMIN)
    assert r.status_code == 200 and r.json()["discount_pct"] == 20.0
    # Stawka z umowy nie jest publiczna — partner ani klient nie mają jej czytać.
    assert client.get("/api/admin/partner-terms").status_code in (401, 403)


def test_cena_partnerska_zostawia_slad_na_zamowieniu(maile, monkeypatch):
    """Kwotę liczy panel, więc w bazie rabat rozpłynąłby się w liczbie i po
    miesiącu nikt nie policzyłby, ile kosztowała ta współpraca. Stempel na
    zamówieniu to jedyne, co odróżnia cenę partnerską od zwykłego upustu."""
    monkeypatch.setattr(glowny.settings, "partner_discount_pct", 20.0)
    tid, _ = _trader()
    d = _dodaj(tid, amount_usd=239.2, partner_discount=True).json()
    s = SessionLocal(); o = s.get(Order, d["id"]); s.close()
    assert o.coupon == "PARTNER20" and o.amount_usd == 239.2


def test_zwykle_zamowienie_nie_dostaje_stempla_partnera(maile, monkeypatch):
    monkeypatch.setattr(glowny.settings, "partner_discount_pct", 20.0)
    tid, _ = _trader()
    d = _dodaj(tid).json()
    s = SessionLocal(); assert s.get(Order, d["id"]).coupon is None; s.close()


def test_bez_umowy_nie_da_sie_ostemplowac_rabatu(maile, monkeypatch):
    """Zero znaczy „nie ma umowy". Gdyby przeszło, w raporcie pojawiłaby się
    zniżka, której nikomu nie obiecano — i to na cudzy rachunek."""
    monkeypatch.setattr(glowny.settings, "partner_discount_pct", 0.0)
    tid, _ = _trader()
    assert _dodaj(tid, partner_discount=True).status_code == 400
    assert client.get("/api/admin/partner-terms", headers=ADMIN).json()["discount_pct"] == 0


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
