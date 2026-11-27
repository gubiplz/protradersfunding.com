"""Audyt 27.11: wylogowanie unieważnia token, płatność testowa wyłączona na Vercelu, ręczna
zmiana fazy czyści terminal i progi, strona 404, limit maili resetu, dwustopniowy porzucony
koszyk z rabatem 15% i wypisaniem się. Unikalne e-maile, asercje odporne na cudze wiersze."""
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog, notify, offers  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import _checkout_recovery, app  # noqa: E402
from app.models import Account, FlashOffer, MailLog, Order, Product, Trader  # noqa: E402

init_db()
_s = SessionLocal(); catalog.seed_products(_s); _s.close()
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(100000))
TERAZ = lambda: datetime.now(timezone.utc).replace(tzinfo=None)  # noqa: E731


@pytest.fixture
def maile(monkeypatch):
    zebrane = []
    monkeypatch.setattr(notify, "_send_teraz", lambda event, to, ctx=None: zebrane.append((event, to, ctx or {})))
    return zebrane


def _signup(c, email):
    r = c.post("/api/auth/signup", json={"email": email, "password": "haslo12345", "terms_accepted": True})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_wylogowanie_uniewaznia_skopiowany_token():
    with TestClient(app) as c:
        email = f"wyloguj{next(LICZNIK)}@test.pl"
        tok = _signup(c, email)
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/me/accounts", headers=h).status_code == 200
        assert c.post("/api/auth/logout", headers=h).status_code == 200
        assert c.get("/api/me/accounts", headers=h).status_code == 401
        # Ponowne logowanie (od następnej sekundy — czas tokenu jest w sekundach) daje działający token.
        time.sleep(1.1)
        nowy = c.post("/api/auth/login", json={"email": email, "password": "haslo12345"}).json()["token"]
        assert c.get("/api/me/accounts", headers={"Authorization": f"Bearer {nowy}"}).status_code == 200


def test_platnosc_testowa_wylaczona_na_vercelu(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert billing.mock_zablokowany()
    s = SessionLocal()
    tr = Trader(email=f"mock{next(LICZNIK)}@test.pl", password_hash=auth.hash_password("haslo12345"),
                referral_code=f"MOCK{next(LICZNIK)}")
    s.add(tr); s.commit()
    zam = Order(trader_id=tr.id, product_key="2step-25k", amount_usd=299.0, status="pending", provider="mock")
    s.add(zam); s.commit()
    with pytest.raises(Exception) as e:
        billing.mock_complete(s, zam.id, tr.id)
    assert "disabled" in str(e.value.detail)
    s.close()
    monkeypatch.delenv("VERCEL_ENV")
    assert not billing.mock_zablokowany()


def test_reczna_zmiana_fazy_czysci_progi_i_blokuje_mt5():
    s = SessionLocal()
    acc = Account(login=f"FZ{next(LICZNIK)}", trader_name="Faza", product_key="2step-25k", initial_balance=25000.0,
                  balance=26000.0, equity=26000.0, steps=2, phase="eval_1", status="active",
                  target_50_at=TERAZ(), target_75_at=TERAZ(), min_days_at=TERAZ())
    mt5 = Account(login=f"FZ{next(LICZNIK)}", trader_name="Faza MT5", product_key="2step-25k",
                  initial_balance=25000.0, steps=2, phase="eval_1", status="active", metaapi_account_id="ma-1")
    s.add_all([acc, mt5]); s.commit()
    aid, mid = acc.id, mt5.id
    s.close()
    with TestClient(app) as c:
        r = c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "eval_2"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        r2 = c.post(f"/api/admin/accounts/{mid}/phase", json={"phase": "eval_2"}, headers=ADMIN)
        assert r2.status_code == 400 and "MT5" in r2.json()["detail"]
    s = SessionLocal()
    a = s.get(Account, aid)
    assert a.phase == "eval_2" and a.balance == 25000.0
    assert a.target_50_at is None and a.target_75_at is None and a.min_days_at is None
    assert s.get(Account, mid).phase == "eval_1"
    s.close()


def test_strona_404_w_html_a_api_dalej_json():
    with TestClient(app) as c:
        html = c.get("/nie-ma-takiej-strony", headers={"Accept": "text/html"})
        assert html.status_code == 404 and "This page doesn't exist" in html.text
        api = c.get("/api/nie-ma-takiego", headers={"Accept": "text/html"})
        assert api.status_code == 404 and api.json()["detail"] == "Not Found"


def test_strony_konta_bez_pamieci_podrecznej():
    with TestClient(app) as c:
        assert c.get("/portal").headers["cache-control"] == "no-store"
        assert c.get("/faq").headers["cache-control"] == "no-cache"


def test_za_dlugie_pole_to_422_nie_500():
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": "a" * 200 + "@test.pl", "password": "haslo12345",
                                             "terms_accepted": True})
        assert r.status_code == 422


def test_limit_maili_resetu_na_adres(maile):
    email = f"reset{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    s.add(Trader(email=email, password_hash=auth.hash_password("haslo12345"), referral_code=f"RS{next(LICZNIK)}"))
    for _ in range(main_mod.RESET_MAILE_NA_GODZINE):
        s.add(MailLog(event="password_reset", to_email=email, subject="x", ok=True))
    s.commit(); s.close()
    with TestClient(app) as c:
        r = c.post("/api/auth/forgot", json={"email": email})
        assert r.status_code == 200 and r.json()["ok"] is True      # ta sama odpowiedź
    assert not [m for m in maile if m[1] == email]


def _porzucone(*, status="pending", fail_reason=None, wiek_h=2.0, marketing=True):
    email = f"koszyk{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"), first_name="Jan",
                referral_code=f"KS{next(LICZNIK)}", notify_marketing=marketing)
    s.add(tr); s.commit()
    zam = Order(trader_id=tr.id, product_key="2step-25k", amount_usd=299.0, status=status, provider="stripe",
                fail_reason=fail_reason, created_at=TERAZ() - timedelta(hours=wiek_h))
    s.add(zam); s.commit()
    ids = (tr.id, zam.id, email)
    s.close()
    return ids


def test_wygasla_sesja_stripe_tez_dostaje_przypomnienie_z_wypisaniem(maile):
    tid, oid, email = _porzucone(status="failed", fail_reason="Stripe checkout expired (never paid)", wiek_h=26)
    _checkout_recovery()
    moje = [m for m in maile if m[1] == email]
    assert [m[0] for m in moje] == ["checkout_recovery"]
    url = moje[0][2]["unsubscribe_url"]
    assert "/email/unsubscribe/" in url


def test_drugi_krok_daje_15_procent_na_porzucony_plan(maile):
    tid, oid, email = _porzucone()
    _checkout_recovery()
    s = SessionLocal()
    s.get(Order, oid).recovery_sent_at = TERAZ() - timedelta(hours=25)
    s.commit(); s.close()
    _checkout_recovery()
    oferta_mail = [m for m in maile if m[1] == email and m[0] == "checkout_recovery_offer"]
    assert len(oferta_mail) == 1
    s = SessionLocal()
    oferta = s.query(FlashOffer).filter(FlashOffer.trader_id == tid).one()
    assert oferta.discount_pct == 15.0 and oferta.single_use and oferta.plan_keys == "2step-25k"
    assert timedelta(hours=47) < oferta.ends_at - TERAZ() <= timedelta(hours=48)
    produkt = s.query(Product).filter(Product.key == "2step-25k").one()
    assert offers.best_for(s, tid, produkt).id == oferta.id          # checkout nalicza sam
    assert oferta_mail[0][2]["price"] == offers.price_after(produkt, oferta)
    s.close()
    _checkout_recovery()                                                  # drugi raz nic nie wychodzi
    assert len([m for m in maile if m[1] == email and m[0] == "checkout_recovery_offer"]) == 1


def test_wypisanie_blokuje_oba_maile(maile):
    tid, oid, email = _porzucone()
    with TestClient(app) as c:
        r = c.get(main_mod._unsub_url(tid).replace(get_settings().app_base_url, ""))
        assert r.status_code == 200 and "unsubscribed" in r.text
    s = SessionLocal()
    assert s.get(Trader, tid).notify_marketing is False
    s.close()
    _checkout_recovery()
    s = SessionLocal()
    s.get(Order, oid).recovery_sent_at = TERAZ() - timedelta(hours=25)
    s.commit(); s.close()
    _checkout_recovery()
    # Bramka preferencji siedzi w `_send_teraz` (podstawione w teście): każdy taki mail musi być przez nią zablokowany.
    assert all(not notify._email_allowed(ev, to) for ev, to, _ in maile if to == email)
    assert not [m for m in maile if m[1] == email and m[0] == "checkout_recovery_offer"]
    s = SessionLocal()
    assert s.query(FlashOffer).filter(FlashOffer.trader_id == tid).count() == 0
    s.close()


def test_zgoda_marketingowa_przy_rejestracji_jest_osobna_i_domyslnie_nie():
    from app.models import ConsentRecord
    with TestClient(app) as c:
        bez = f"bezzgody{next(LICZNIK)}@test.pl"
        ze = f"zezgoda{next(LICZNIK)}@test.pl"
        assert c.post("/api/auth/signup", json={"email": bez, "password": "haslo12345",
                                                "terms_accepted": True}).status_code == 200
        assert c.post("/api/auth/signup", json={"email": ze, "password": "haslo12345", "terms_accepted": True,
                                                "marketing_opt_in": True}).status_code == 200
    s = SessionLocal()
    t_bez = s.query(Trader).filter(Trader.email == bez).one()
    t_ze = s.query(Trader).filter(Trader.email == ze).one()
    assert t_bez.notify_marketing is False and t_ze.notify_marketing is True
    rodzaje = lambda t: {r.kind for r in s.query(ConsentRecord).filter(ConsentRecord.trader_id == t.id)}  # noqa: E731
    assert "marketing" not in rodzaje(t_bez) and "marketing" in rodzaje(t_ze)
    s.close()


def test_zapis_do_newslettera_wlacza_oferty_na_koncie_i_odwrotnie():
    from app.models import ConsentRecord
    with TestClient(app) as c:
        # konto bez zgody → zapis do newslettera tym adresem włącza oferty i zostawia dowód
        konto = f"nlkonto{next(LICZNIK)}@test.pl"
        assert c.post("/api/auth/signup", json={"email": konto, "password": "haslo12345",
                                                "terms_accepted": True}).status_code == 200
        assert c.post("/api/newsletter/subscribe", json={"email": konto, "consent": True}).status_code == 200
        # najpierw newsletter, potem rejestracja bez checkboxa → oferty od razu włączone
        nl = f"nlnajpierw{next(LICZNIK)}@test.pl"
        assert c.post("/api/newsletter/subscribe", json={"email": nl, "consent": True}).status_code == 200
        assert c.post("/api/auth/signup", json={"email": nl, "password": "haslo12345",
                                                "terms_accepted": True}).status_code == 200
    s = SessionLocal()
    for adres in (konto, nl):
        t = s.query(Trader).filter(Trader.email == adres).one()
        assert t.notify_marketing is True
        assert s.query(ConsentRecord).filter(ConsentRecord.trader_id == t.id,
                                             ConsentRecord.kind == "marketing").count() == 1
    s.close()


def test_ten_sam_mail_marketingowy_nie_idzie_dwa_razy(monkeypatch):
    """Drugie „Send” newslettera i ta sama oferta drugi raz pomijają tych, którzy już ją dostali."""
    from app.models import NewsletterSubscriber
    wyslane = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): wyslane.append((msg["To"], msg["Subject"]))

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.atrapa")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    moje = [f"dubel{next(LICZNIK)}@test.pl" for _ in range(2)]
    s = SessionLocal()
    for a in moje:
        s.add(NewsletterSubscriber(email=a, consent=True, source="footer", token=f"t{next(LICZNIK)}x"))
    s.commit(); s.close()
    temat = f"Weekend promo {next(LICZNIK)}"
    with TestClient(app) as c:
        r1 = c.post("/api/admin/newsletter/send", json={"subject": temat, "body": "Hi"}, headers=ADMIN).json()
        r2 = c.post("/api/admin/newsletter/send", json={"subject": temat, "body": "Hi"}, headers=ADMIN).json()
    do_moich = [w for w in wyslane if w[0] in moje]
    assert sorted(w[0] for w in do_moich) == sorted(moje)          # każdy dokładnie raz
    assert r1["sent"] >= 2 and r2["already_sent"] >= 2
    # bezpośrednio przez notify: ten sam mail na ten sam adres drugi raz → pominięty
    ctx = {"name": "Jan", "title": "Only today", "pct": 20, "plans": "2-Step", "ends": "x", "url": "u"}
    assert notify.send_now("flash_offer", moje[0], ctx) is None
    assert notify.send_now("flash_offer", moje[0], ctx) == notify.DUPLIKAT
    assert len([w for w in wyslane if w == (moje[0], "Only today")]) == 1


def test_mail_marketingowy_ma_wypisanie_jednym_kliknieciem(monkeypatch):
    """Nagłówki List-Unsubscribe (+ One-Click) dla Gmaila/Yahoo, POST na link wypisuje."""
    naglowki = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): naglowki.append(dict(msg.items()))

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.atrapa")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    tid, _, email = _porzucone()
    url = "https://protradersfunding.com/email/unsubscribe/abc"
    notify.send_now("flash_offer", email, {"name": "Jan", "title": f"Click {next(LICZNIK)}", "pct": 10,
                                           "plans": "2-Step", "ends": "x", "url": "u", "unsubscribe_url": url})
    s = SessionLocal(); s.get(Trader, tid).notify_marketing = True; s.commit(); s.close()
    notify.send_now("flash_offer", email, {"name": "Jan", "title": f"Click {next(LICZNIK)}", "pct": 10,
                                           "plans": "2-Step", "ends": "x", "url": "u", "unsubscribe_url": url})
    h = naglowki[-1]
    assert h["List-Unsubscribe"].startswith(f"<{url}>") and h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    with TestClient(app) as c:
        r = c.post(main_mod._unsub_url(tid).replace(get_settings().app_base_url, ""),
                   data={"List-Unsubscribe": "One-Click"})
        assert r.status_code == 200
    s = SessionLocal()
    assert s.get(Trader, tid).notify_marketing is False
    s.close()


def test_probki_maili_z_panelu_ida_zawsze(maile):
    with TestClient(app) as c:
        for _ in range(2):          # drugi raz też — próbki omijają blokadę dubli
            r = c.post("/api/admin/mail-test", json={"to": "probka@test.pl"}, headers=ADMIN)
            assert r.status_code == 200
        assert c.post("/api/admin/mail-test", json={"to": "probka@test.pl"}).status_code in (401, 403)
    zdarzenia = [m[0] for m in maile if m[1] == "probka@test.pl"]
    assert zdarzenia.count("checkout_recovery_offer") == 2 and "newsletter_blast" in zdarzenia


def test_strona_preferencji_bez_logowania_i_stopka(monkeypatch):
    tid, _, email = _porzucone(marketing=True)
    sciezka = main_mod._unsub_url(tid).replace(get_settings().app_base_url, "").replace("/unsubscribe/", "/preferences/")
    with TestClient(app) as c:
        strona = c.get(sciezka)
        assert strona.status_code == 200 and "Email preferences" in strona.text and "Offers &amp; reminders" in strona.text
        # odznacz oferty, zostaw resztę
        r = c.post(sciezka, data={"notify_trading": "on", "notify_payouts": "on", "notify_updates": "on"})
        assert r.status_code == 200 and "Saved" in r.text
        s = SessionLocal(); t = s.get(Trader, tid)
        assert t.notify_marketing is False and t.notify_trading and t.notify_payouts and t.notify_updates
        s.close()
        c.post(sciezka, data={"all_off": "1"})
        s = SessionLocal(); t = s.get(Trader, tid)
        assert not (t.notify_marketing or t.notify_trading or t.notify_payouts or t.notify_updates)
        s.close()
        assert "This link has expired" in c.get("/email/preferences/zly").text
    # stopka maila do posiadacza konta: dokumenty + link do preferencji z tokenem
    html_maila = []
    monkeypatch.setattr(notify.settings, "smtp_host", "")
    monkeypatch.setattr(notify, "_render_html", (lambda orig: (lambda e, ctx, subj: html_maila.append(
        orig(e, ctx, subj)) or html_maila[-1]))(notify._render_html))
    notify.send_now("password_reset", email, {"name": "Jan", "reset_url": "https://x"})
    assert "/email/preferences/" in html_maila[-1] and "/privacy" in html_maila[-1] and "/risk-disclosure" in html_maila[-1]
    assert "All rights reserved" in html_maila[-1]


def test_kampania_z_odliczaniem_raz_na_adres(maile):
    from app.models import NewsletterSubscriber
    with TestClient(app) as c:
        gif = c.get(f"/email/countdown.gif?end={int(time.time()) + 3600}")
        assert gif.status_code == 200 and gif.headers["content-type"] == "image/gif" and gif.content[:3] == b"GIF"
        assert "no-store" in gif.headers["cache-control"]
    # ten sam adres w newsletterze i na koncie ze zgodą → jeden mail, z linkiem wypisania z konta
    wspolny = f"kampania{next(LICZNIK)}@test.pl"
    tylko_nl = f"kampanianl{next(LICZNIK)}@test.pl"
    bez_zgody = f"kampaniabez{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=wspolny, password_hash=auth.hash_password("haslo12345"), referral_code=f"KM{next(LICZNIK)}",
                notify_marketing=True)
    s.add(tr)
    s.add(Trader(email=bez_zgody, password_hash=auth.hash_password("haslo12345"), referral_code=f"KB{next(LICZNIK)}",
                 notify_marketing=False))
    for a in (wspolny, tylko_nl):
        s.add(NewsletterSubscriber(email=a, consent=True, source="footer", token=f"k{next(LICZNIK)}x"))
    s.commit(); tid = tr.id; s.close()
    dane = {"template": "black-friday", "subject": f"BF {next(LICZNIK)}", "offer": "Second challenge free",
            "body": "Hi,\n\nit's on.", "ends_at": "2030-01-01T00:00:00Z", "fine_print": "Terms apply."}
    with TestClient(app) as c:
        r = c.post("/api/admin/campaign/send", json=dane, headers=ADMIN)
        assert r.status_code == 200, r.text
        assert c.post("/api/admin/campaign/send", json={**dane, "template": "nope"}, headers=ADMIN).status_code == 400
    moje = {m[1]: m[2] for m in maile if m[0] == "campaign"}
    assert wspolny in moje and tylko_nl in moje and bez_zgody not in moje
    assert str(auth.make_unsub_token(tid)) in moje[wspolny]["unsubscribe_url"]        # link z konta
    assert "/newsletter/unsubscribe/" in moje[tylko_nl]["unsubscribe_url"]
    assert moje[wspolny]["ends_ts"] == 1893456000 and moje[wspolny]["template"] == "black-friday"
    tresc = notify._render_html("campaign", moje[wspolny], dane["subject"])
    assert "/email/countdown.gif?end=1893456000" in tresc and "/static/img/campaigns/black-friday.jpg" in tresc
    assert "Second challenge free" in tresc and "Unsubscribe" in tresc
