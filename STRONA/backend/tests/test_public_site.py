"""Publiczne API strony sprzedażowej: stats bez internali, ranking bez nazwisk,
szczegóły konta tylko dla właściciela, certyfikat po nieodgadywalnym tokenie.

UWAGA: pytest współdzieli moduły między plikami testów — env ustawia pierwszy
zaimportowany plik, a baza jest wspólna. Dlatego: unikalne e-maile/nazwiska,
asercje odporne na cudze wiersze i reset cache przed każdym odczytem statystyk.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Payout, Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

ADMIN_H = {"X-Admin-Token": get_settings().admin_token}


def _fresh_stats_cache():
    main_mod._PUBLIC_STATS_CACHE.update(ts=0.0, data=None)


def _trader(email: str, full_name: str):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name=full_name, referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, auth.make_token(tid)


def _konto(trader_id: int, trader_name: str, login: str, *, status="active", phase="eval_1",
           balance=None, initial=10_000.0):
    s = SessionLocal()
    acc = Account(login=login, trader_id=trader_id, trader_name=trader_name,
                  platform_login=login, platform_password="Sekret123",
                  platform_server="MetaQuotes-Demo",
                  product_key="2step-10k", initial_balance=initial,
                  balance=balance if balance is not None else initial,
                  equity=balance if balance is not None else initial,
                  peak_equity=initial, day_start_equity=initial, day_start_balance=initial,
                  status=status, phase=phase)
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_public_stats_bez_internali():
    _trader("stats@test.pl", "Stat Owy")
    _fresh_stats_cache()
    with TestClient(app) as c:
        r = c.get("/api/public/stats")
    assert r.status_code == 200
    data = r.json()
    for zakazane in ("feed", "stripe", "pool_free", "orders_paid", "provisioning"):
        assert zakazane not in data, f"public stats ujawnia internal: {zakazane}"
    assert data["traders_total"] >= 1
    # Kwoty na kaflach LP w pelnych dolarach — ".96" przy szesciocyfrowej
    # liczbie to szum, ktory poszerzal kafel az do obciecia tekstu.
    assert isinstance(data["payouts_total_usd"], int)
    assert isinstance(data["largest_payout_usd"], int)


def test_operacyjne_stats_tylko_dla_admina():
    with TestClient(app) as c:
        bez = c.get("/api/stats")
        z = c.get("/api/stats", headers=ADMIN_H)
    assert bez.status_code in (401, 403)
    assert z.status_code == 200 and "feed" in z.json()


def test_leaderboard_maskuje_nazwiska():
    tid, _ = _trader("rank@test.pl", "Ranking Maskowany")
    # Ranking pokazuje wylacznie konta FUNDED. Zysk na tyle duzy, zeby miejsce
    # w TOP 20 bylo pewne: ranking zasilaja konta ze WSZYSTKICH plikow testow.
    _konto(tid, "Ranking Maskowany", "770001", status="funded", balance=95_000)
    with TestClient(app) as c:
        r = c.get("/api/leaderboard")
    assert r.status_code == 200
    assert "Maskowany" not in r.text, "pełne nazwisko wyciekło do publicznego rankingu"
    assert "login" not in (r.json()[0] if r.json() else {}), "ranking nie może ujawniać loginów MT5"
    assert any(row["trader"] == "Ranking M." for row in r.json())


def test_leaderboard_liczy_z_biezacego_equity_bez_doliczania_wyplat():
    tid, _ = _trader("payout-rank@test.pl", "Wyplacony Zysk")
    # Ranking pokazuje STAN kont funded (bieżące equity/balance) — wypłacone
    # zyski NIE są doliczane, po wypłacie trader świadomie spada w rankingu.
    aid = _konto(tid, "Wyplacony Zysk", "770002", status="funded", phase="funded",
                 balance=10_400, initial=10_000)
    s = SessionLocal()
    s.add(Payout(account_id=aid, profit_amount=800.0, trader_share=640.0, paid=True))
    s.commit(); s.close()
    with TestClient(app) as c:
        r = c.get("/api/leaderboard")
    row = next(row for row in r.json() if row["trader"] == "Wyplacony Z.")
    assert row["profit_pct"] == 4.0, "wypłacony zysk nie może wracać do rankingu"
    assert row["equity"] == 10_400.0


def test_leaderboard_pokazuje_tylko_konta_funded():
    """Ranking to lista tych, ktorzy PRZESZLI — konta w ewaluacji tam nie naleza."""
    tid, _ = _trader("rank-eval@test.pl", "Wciaz Ewaluacja")
    _konto(tid, "Wciaz Ewaluacja", "770010", status="active", phase="eval_1", balance=99_000)
    tid2, _ = _trader("rank-passed@test.pl", "Tylko Passed")
    _konto(tid2, "Tylko Passed", "770011", status="passed", phase="eval_2", balance=99_000)
    with TestClient(app) as c:
        rows = c.get("/api/leaderboard").json()
    imiona = {r["trader"] for r in rows}
    assert "Wciaz E." not in imiona and "Tylko P." not in imiona
    assert all(r["status"] == "funded" for r in rows)


def test_wlasciciel_widzi_szczegoly_konta_a_obcy_nie():
    tid, token = _trader("detal@test.pl", "Detal Owner")
    aid = _konto(tid, "Detal Owner", "770003")
    _, token_obcy = _trader("obcy-detal@test.pl", "Obcy Detal")
    with TestClient(app) as c:
        moje = c.get(f"/api/me/accounts/{aid}", headers={"Authorization": f"Bearer {token}"})
        cudze = c.get(f"/api/me/accounts/{aid}", headers={"Authorization": f"Bearer {token_obcy}"})
        anonim = c.get(f"/api/me/accounts/{aid}")
    assert moje.status_code == 200
    d = moje.json()
    assert "equity_curve" in d and "breaches" in d and "payout_requests" in d
    assert d["platform_password"] == "Sekret123"
    assert cudze.status_code == 404
    assert anonim.status_code == 401


def test_certyfikat_po_tokenie_a_nie_po_id():
    tid, token = _trader("cert@test.pl", "Cert Owski")
    aid = _konto(tid, "Cert Owski", "770004", status="funded", phase="funded")
    with TestClient(app) as c:
        lista = c.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"}).json()
        cert_token = next(a["cert_token"] for a in lista if a["id"] == aid)
        assert cert_token, "konto funded powinno dostać token certyfikatu"
        cert = c.get(f"/certificate/{cert_token}")
        zly = c.get("/certificate/nie-ma-takiego-tokenu")
        stary = c.get(f"/api/accounts/{aid}/certificate")
    assert cert.status_code == 200 and "Cert Owski" in cert.text
    assert zly.status_code == 404
    assert stary.status_code in (404, 405), "stary enumerowalny endpoint ma nie istnieć"


def test_wszystkie_strony_publiczne_odpowiadaja():
    # /admin celowo NIE ma tu byc — panel jest zamkniety i dla goscia nie istnieje.
    strony = ["/", "/faq", "/affiliate", "/terms", "/privacy", "/risk-disclosure",
              "/refund-policy", "/verify", "/portal", "/robots.txt"]
    with TestClient(app) as c:
        for path in strony:
            r = c.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_link_afiliacyjny_bierze_host_z_zadania():
    """Instrukcja na /affiliate pokazywala „http://localhost:8000/?ref=KOD" na
    produkcji, bo APP_BASE_URL nie bylo ustawione. Adres bierzemy z ZADANIA."""
    with TestClient(app, base_url="https://mypropfunds.example") as c:
        html = c.get("/affiliate").text
    assert "https://mypropfunds.example/?ref=YOURCODE" in html
    assert "localhost" not in html and "127.0.0.1" not in html


def test_portal_ma_domyslny_jasny_motyw_i_przelacznik():
    """Dashboard renderuje sie w LIGHT (bez atrybutu data-theme na <html>);
    zapamietany dark wlacza pre-paintowy skrypt z pf_theme, a przelacznik
    mieszka na dole sidebara (klasa theme-toggle)."""
    with TestClient(app) as c:
        html = c.get("/portal").text
    assert 'data-theme="dark"' not in html
    assert "theme-toggle" in html and 'id="theme-btn"' in html
    assert "pf_theme" in html


def test_portal_laduje_sortowanie_tabel():
    """Kolumny tabel sortuja sie po kliknieciu w naglowek (sortable.js);
    skrypt jest podpiety w portalu i serwowany ze statykow."""
    with TestClient(app) as c:
        html = c.get("/portal").text
        assert "/static/js/sortable.js" in html
        js = c.get("/static/js/sortable.js")
        assert js.status_code == 200 and "data-tkey" in js.text


def test_publiczny_pas_certyfikatow_maskuje_i_nie_ujawnia_tokenow():
    """Landing pokazuje pas OSTATNIO wystawionych certyfikatów WYPŁAT: nazwisko
    zamaskowane jak w rankingu, zero tokenów i ID — link do certyfikatu
    publikuje jego właściciel, nie my.

    Certyfikaty za zaliczony etap i za funded na pas NIE idą — dowodem jest
    przelew, a nie zaliczona ewaluacja."""
    from app.models import Certificate

    tid, _ = _trader("certstrip@test.pl", "Certowy Pasek")
    aid = _konto(tid, "Certowy Pasek", "770100", status="funded", phase="funded")
    s = SessionLocal()
    s.add(Certificate(account_id=aid, kind="funded", cert_token="pas-sekret-token-1"))
    s.add(Payout(account_id=aid, profit_amount=1000.0, trader_share=900.0, paid=True,
                 cert_token="pas-sekret-token-2"))
    s.commit(); s.close()
    main_mod._PUBLIC_CERTS_CACHE.update(ts=0.0, data=None)
    with TestClient(app) as c:
        r = c.get("/api/public/certificates/recent")
    assert r.status_code == 200
    dane = r.json()
    assert any(x["kind"] == "payout" and x["amount_usd"] == 900 for x in dane)
    assert all(x["kind"] == "payout" for x in dane), \
        "na pas trafil certyfikat inny niz wyplata"
    assert "pas-sekret-token" not in r.text, "token certyfikatu wyciekł do publicznej listy"
    assert "Pasek" not in r.text, "pełne nazwisko wyciekło do publicznej listy"
    for x in dane:
        for zakazane in ("cert_token", "id", "account_id", "trader_id"):
            assert zakazane not in x


def test_landing_ma_katalog_wstrzykniety_w_html():
    """Konfigurator w hero nie moze mrugac pustymi "—" przez pol sekundy —
    serwer wstrzykuje caly katalog w HTML (#pf-products), a liczby ida z BAZY.

    Bazę trzyma w ryzach kod: przy starcie `sync_catalog()` wgrywa cennik z
    `catalog._CATALOG`, wiec recznie podmieniona cena w bazie wraca do wartosci
    z katalogu. Inaczej podwyzka wdrozona w kodzie nigdy nie dotarlaby na
    produkcje, gdzie AUTO_SEED jest wylaczony."""
    import json as _json

    s = SessionLocal()
    prod = s.query(main_mod.Product).filter(main_mod.Product.key == "2step-100k").first()
    prod.price_usd = 777.0          # recznie rozjechana cena...
    s.commit()
    s.close()

    with TestClient(app) as c:      # ...start aplikacji przywraca ja z katalogu
        html = c.get("/").text
    assert 'id="pf-products"' in html
    surowy = html.split('id="pf-products" type="application/json">', 1)[1].split("</script>", 1)[0]
    dane = _json.loads(surowy)
    wpis = next(p for p in dane if p["key"] == "2step-100k")
    assert wpis["price_usd"] == 549.0
    assert any(p["steps"] == 0 for p in dane)   # oba modele obecne
    # wejsciem do oferty jest 25k za 299 — 10k wypadlo calkowicie
    assert next(p for p in dane if p["key"] == "2step-25k")["price_usd"] == 299.0
    assert not any(p["key"].endswith("-10k") for p in dane)
    # konfigurator hero jest WYRENDEROWANY serwerowo (nie czeka na JS):
    # domyślny wybór to 2step-100k, więc cena z bazy stoi w pierwszym HTML
    assert 'data-v="549"' in html and "$549" in html
    assert 'data-key="2step-100k"' in html
    assert "2-Step Evaluation" in html and "Start with $100K" in html


def test_panel_admina_nie_jest_strona_publiczna():
    """Gosc nie moze nawet stwierdzic, ze panel istnieje — stad 404."""
    with TestClient(app) as c:
        r = c.get("/admin")
    assert r.status_code == 404
    assert "MT5 Pool" not in r.text


def test_weryfikacja_certyfikatu_na_stronie():
    tid, token = _trader("verify@test.pl", "Vera Fikacja")
    _konto(tid, "Vera Fikacja", "770006", status="funded", phase="funded")
    with TestClient(app) as c:
        lista = c.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"}).json()
        ct = next(a["cert_token"] for a in lista if a["trader_name"] == "Vera Fikacja")
        ok = c.get(f"/verify/{ct}")
        zly = c.get("/verify/xxx-nie-istnieje")
    assert ok.status_code == 200 and "Certificate is valid" in ok.text
    assert "Vera F." in ok.text and "Fikacja" not in ok.text.replace("Vera F.", "")
    assert zly.status_code == 200 and "Certificate not found" in zly.text


def test_konto_w_trakcie_eval1_nie_ma_certyfikatu():
    tid, token = _trader("nocert@test.pl", "Bez Certa")
    _konto(tid, "Bez Certa", "770005", status="active", phase="eval_1")
    with TestClient(app) as c:
        lista = c.get("/api/me/accounts", headers={"Authorization": f"Bearer {token}"}).json()
    assert all(not a["cert_token"] for a in lista if a["trader_name"] == "Bez Certa")


def test_assety_maja_dlugi_cache_a_html_nie():
    """Bez tego przeglądarka odpytuje o KAŻDY plik przy każdej nawigacji.

    Linki z szablonów mają ?v=<sha deployu>, więc treść pod danym adresem nigdy
    się nie zmienia — stąd `immutable`. HTML musi zostać świeży, inaczej po
    deployu zostałby ze starymi linkami ?v=.
    """
    with TestClient(app) as c:
        wersjonowany = c.get("/static/css/site.css?v=deadbeef")
        goly = c.get("/static/css/site.css")          # bez ?v= treść może się zmienić
        font = c.get("/static/fonts/inter.woff2")
        strona = c.get("/")
        sw = c.get("/sw.js")

    assert wersjonowany.status_code == 200
    assert "immutable" in wersjonowany.headers["cache-control"]
    assert "max-age=31536000" in wersjonowany.headers["cache-control"]

    assert "immutable" not in goly.headers["cache-control"]
    assert "max-age=86400" in goly.headers["cache-control"]

    assert "max-age=2592000" in font.headers["cache-control"]

    assert strona.headers["cache-control"] == "no-cache"
    assert sw.headers["cache-control"] == "no-cache"


def test_assety_z_pierwszego_wejscia_miesza_sie_w_budzecie():
    """Budżet wagowy na pliki, które ciągnie KAŻDE pierwsze wejście na stronę.

    Logo trafiło tu z 90 kB — było zapisane jako pełne RGBA, choć to płaski
    dwukolorowy znak; sam szum ±2 na kanale ważył dziesięć razy więcej niż
    kształt. Limity są luźne (ok. 1,5× obecnego stanu), więc łapią wrzucenie
    nieprzetworzonego eksportu, a nie normalną edycję.
    """
    budzet = {
        "static/img/logo.png": 20_000,
        "static/img/favicon.png": 10_000,
        "static/css/site.css": 70_000,
        "static/js/site.js": 70_000,
    }
    korzen = Path(__file__).resolve().parent.parent
    za_ciezkie = {p: (korzen / p).stat().st_size for p, limit in budzet.items()
                  if (korzen / p).stat().st_size > limit}
    assert not za_ciezkie, f"assety poza budżetem (limity: {budzet}): {za_ciezkie}"
