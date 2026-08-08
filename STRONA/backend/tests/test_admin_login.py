"""Panel admina otwiera KONTO, nie wspólny token.

Wcześniej wejście do panelu dawał nagłówek `X-Admin-Token` wpisywany w UI —
jeden sekret dla wszystkich, bez śladu kto co zrobił. Teraz admin loguje się
normalnie i dostaje ten sam token sesji co trader, a uprawnienia daje flaga
`is_admin`. Te testy pilnują granicy: zwykły trader nie może wejść w admina.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
client = TestClient(app)


def _trader(email: str, haslo: str, admin: bool) -> None:
    s = SessionLocal()
    if not s.query(Trader).filter(Trader.email == email).first():
        s.add(Trader(email=email, password_hash=auth.hash_password(haslo),
                     full_name="Kto To", is_admin=admin, referral_code=email[:8].upper()))
        s.commit()
    s.close()


def _zaloguj(email: str, haslo: str):
    return client.post("/api/auth/login", json={"email": email, "password": haslo})


def test_admin_wchodzi_do_panelu_swoimi_danymi():
    _trader("szef@firma.pl", "tajne123", admin=True)
    r = _zaloguj("szef@firma.pl", "tajne123")
    assert r.status_code == 200
    dane = r.json()
    assert dane["trader"]["is_admin"] is True

    naglowek = {"Authorization": f"Bearer {dane['token']}"}
    assert client.get("/api/accounts", headers=naglowek).status_code == 200
    assert client.get("/api/auth/me", headers=naglowek).json()["is_admin"] is True


def test_zwykly_trader_nie_wejdzie_do_admina():
    _trader("zwykly@firma.pl", "tajne123", admin=False)
    token = _zaloguj("zwykly@firma.pl", "tajne123").json()["token"]
    r = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_bez_logowania_admin_jest_zamkniety():
    assert client.get("/api/accounts").status_code in (401, 403)


def test_zle_haslo_nie_wpuszcza():
    _trader("szef2@firma.pl", "tajne123", admin=True)
    assert _zaloguj("szef2@firma.pl", "nie-to-haslo").status_code == 401


def test_login_bez_malpy_dziala():
    """Pierwsze konto zakładane skryptem ma login 'admin', nie adres e-mail."""
    _trader("admin", "admin", admin=True)
    r = _zaloguj("admin", "admin")
    assert r.status_code == 200 and r.json()["trader"]["is_admin"] is True


# --------------------------------------------------------------------------- #
#  Strona /admin — dla nie-admina po prostu NIE ISTNIEJE                        #
# --------------------------------------------------------------------------- #
def test_strona_admina_nie_istnieje_bez_logowania():
    """404, a nie 401/403: „brak dostepu" potwierdzaloby, ze cos tam jest.

    Wczesniej /admin zwracalo pelny HTML panelu — kazdy widzial nawigacje
    (Accounts, Payouts, KYC, MT5 Pool...) i przyciski, zanim cokolwiek go
    zapytalo o haslo.
    """
    # WLASNY klient: wspoldzielony `client` nosi ciasteczko z logowan w innych
    # testach tego pliku i wpuscilby nas jako admin.
    r = TestClient(app).get("/admin")
    assert r.status_code == 404
    assert "MT5 Pool" not in r.text and "Grant challenge" not in r.text


def test_zwykly_trader_tez_nie_widzi_strony_admina():
    _trader("szary@firma.pl", "tajne123", admin=False)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "szary@firma.pl", "password": "tajne123"}).status_code == 200
    assert c.get("/admin").status_code == 404      # ciasteczko sesji jest, ale bez is_admin


def test_admin_dostaje_strone_po_zalogowaniu():
    _trader("szefowa@firma.pl", "tajne123", admin=True)
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "szefowa@firma.pl", "password": "tajne123"})
    assert r.status_code == 200
    assert c.cookies.get("pf_session"), "logowanie ma zalozyc ciasteczko sesji"

    strona = c.get("/admin")
    assert strona.status_code == 200
    # Sam panel to juz osobny plik JS (HTML to tylko szkielet), wiec „dostal
    # strone" znaczy: szkielet + dowiazany skrypt, ktory faktycznie sie serwuje.
    # Bez tej drugiej asercji zepsuty link do bundla przeszedlby niezauwazony.
    assert 'src="/static/js/admin-panel.js' in strona.text
    bundle = c.get("/static/js/admin-panel.js")
    assert bundle.status_code == 200 and "MT5 Pool" in bundle.text


def test_docs_zamkniete_jak_admin():
    """Swagger i schemat OpenAPI to mapa calego API — 404 dla kazdego poza
    zalogowanym adminem (ta sama bramka ciasteczkowa co /admin)."""
    anon = TestClient(app)
    assert anon.get("/docs").status_code == 404
    assert anon.get("/openapi.json").status_code == 404
    assert anon.get("/redoc").status_code == 404

    _trader("docsuser@firma.pl", "tajne123", admin=False)
    zwykly = TestClient(app)
    zwykly.post("/api/auth/login", json={"email": "docsuser@firma.pl", "password": "tajne123"})
    assert zwykly.get("/docs").status_code == 404
    assert zwykly.get("/openapi.json").status_code == 404

    _trader("docsadmin@firma.pl", "tajne123", admin=True)
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"email": "docsadmin@firma.pl", "password": "tajne123"})
    assert admin.get("/docs").status_code == 200
    assert admin.get("/openapi.json").status_code == 200
    assert "/api/checkout" in admin.get("/openapi.json").text


def test_wylogowanie_zamyka_strone_admina():
    _trader("wyloguj@firma.pl", "tajne123", admin=True)
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "wyloguj@firma.pl", "password": "tajne123"})
    assert c.get("/admin").status_code == 200

    c.post("/api/auth/logout")
    assert c.get("/admin").status_code == 404


# --------------------------------------------------------------------------- #
#  Konto administratora ma WYLACZNIE panel admina — portal go odsyla           #
# --------------------------------------------------------------------------- #
def test_portal_odsyla_admina_do_panelu():
    """Zalogowany admin nie dostaje portalu tradera, tylko przekierowanie."""
    _trader("tylkopanel@firma.pl", "tajne123", admin=True)
    c = TestClient(app)
    assert c.post("/api/auth/login",
                  json={"email": "tylkopanel@firma.pl", "password": "tajne123"}).status_code == 200
    r = c.get("/portal", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/admin"
    assert c.get("/admin").status_code == 200


def test_portal_normalny_dla_tradera_i_bez_sesji():
    """Redirect dotyczy tylko adminow: go/login-screen dla wszystkich innych.

    /portal to jedyny ekran logowania (rowniez dla administratorow), wiec bez
    sesji NIE wolno przekierowywac — admin nie mialby sie gdzie zalogowac.
    """
    c = TestClient(app)
    assert c.get("/portal", follow_redirects=False).status_code == 200

    _trader("portalowy@firma.pl", "tajne123", admin=False)
    assert c.post("/api/auth/login",
                  json={"email": "portalowy@firma.pl", "password": "tajne123"}).status_code == 200
    assert c.get("/portal", follow_redirects=False).status_code == 200


def test_portal_bez_linku_do_admina():
    """Panel admina i portal tradera to rozlaczne swiaty — w portalu nie ma
    juz linku "Admin panel" (byl widoczny dla kont z is_admin)."""
    html = TestClient(app).get("/portal").text
    assert "admin-link" not in html
    bundle = TestClient(app).get("/static/js/portal-app.js").text
    assert "admin-link" not in bundle and "Admin panel" not in bundle


def test_panel_bez_wejscia_do_portalu_i_z_sign_out():
    """Z panelu admina nie da sie przejsc do portalu tradera (przycisk
    'Trader portal' zniknal z sidebara i z Ustawien) — admin i tak zostalby
    odbity z powrotem. W zamian sidebar ma wprost przycisk wylogowania."""
    _trader("panelui@firma.pl", "tajne123", admin=True)
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "panelui@firma.pl", "password": "tajne123"})
    html = c.get("/admin").text
    assert "Trader portal" not in html
    # klasa .signout z portal.css = ten sam czerwony przycisk co w portalu
    assert '<button class="signout" onclick="signOut()"' in html
    bundle = c.get("/static/js/admin-panel.js").text
    assert "Trader portal" not in bundle


# --------------------------------------------------------------------------- #
#  ADMIN_BOOTSTRAP — konta adminow z env, raz na deploy                        #
# --------------------------------------------------------------------------- #
from app.config import get_settings  # noqa: E402
from app.main import _bootstrap_adminow  # noqa: E402


def _admin(email):
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == email).first()
    s.close()
    return tr


def test_bootstrap_zaklada_adminow_i_nie_rusza_hasha_bez_potrzeby(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "admin_bootstrap",
        "boot@k:przeworsk9:Bartek K;boot@s:przeworsk9;  ;zepsuty-wpis")
    _bootstrap_adminow()
    a, b = _admin("boot@k"), _admin("boot@s")
    assert a.is_admin and b.is_admin
    assert a.full_name == "Bartek K" and b.full_name == "Administrator"
    # prefiks kodu polecajacego brany z e-maila jest identyczny (boot/boot),
    # a kolumna ma UNIQUE — drugi admin musi dostac kod losowy, nie wyjatek
    assert a.referral_code != b.referral_code
    assert auth.verify_password("przeworsk9", a.password_hash)

    # drugi przebieg z TYM SAMYM haslem nie przepisuje hasha: nowy hash to nowa
    # sol, a odcisk hasla w tokenach wylogowalby admina przy kazdym deployu
    stary = a.password_hash
    _bootstrap_adminow()
    assert _admin("boot@k").password_hash == stary

    # logowanie dziala normalnie (a "zepsuty-wpis" nie zalozyl smiecia)
    assert _zaloguj("boot@k", "przeworsk9").json()["trader"]["is_admin"] is True
    assert _admin("zepsuty-wpis") is None


def test_bootstrap_zmiana_hasla_w_env_nadpisuje(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_bootstrap", "boot@n:stare-haslo")
    _bootstrap_adminow()
    monkeypatch.setattr(get_settings(), "admin_bootstrap", "boot@n:nowe-haslo")
    _bootstrap_adminow()
    assert _zaloguj("boot@n", "stare-haslo").status_code == 401
    assert _zaloguj("boot@n", "nowe-haslo").status_code == 200


def test_pusty_admin_token_wylacza_tor_tokenowy(monkeypatch):
    """Dawne domyslne ADMIN_TOKEN="admin" w publicznym repo to backdoor;
    puste ustawienie musi wylaczac wejscie naglowkiem, nie porownywac z ''."""
    monkeypatch.setattr(get_settings(), "admin_token", "")
    assert client.get("/api/accounts", headers={"X-Admin-Token": ""}).status_code == 403
    assert client.get("/api/accounts", headers={"X-Admin-Token": "admin"}).status_code == 403


def test_pwa_admina_dostaje_goly_shell_a_reszta_dalej_404():
    """Zainstalowana PWA startuje z /admin?pwa=1 w pustym slojiku ciasteczek
    (iOS) — bez tej furtki zimny start to martwy 404 bez paska adresu. Furtka
    oddaje wylacznie niewidoczny szkielet, ktory sam odbija na logowanie;
    /admin bez parametru zostaje niebytem jak dotad."""
    # Swiezy klient: wspolny `client` nosi ciasteczko sesji admina z testow
    # wyzej, a tu sprawdzamy dokladnie przypadek BEZ sesji.
    goly = TestClient(app)
    assert goly.get("/admin").status_code == 404
    odp = goly.get("/admin?pwa=1")
    assert odp.status_code == 200
    assert 'visibility:hidden' in odp.text          # shell renderuje sie slepy
    assert 'manifest-admin.json' in odp.text        # panel jest instalowalny
    # Wspolny service worker rozroznia aplikacje po URL-u payloadu i trzyma
    # osobny klucz deep-linkow admina.
    sw = goly.get("/sw.js").text
    assert "/__pending-nav-admin" in sw and "startsWith('/admin')" in sw
    manifest = goly.get("/static/manifest-admin.json").json()
    assert manifest["scope"] == "/admin" and manifest["start_url"] == "/admin?pwa=1"
