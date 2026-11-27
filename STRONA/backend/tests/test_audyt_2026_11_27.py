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
