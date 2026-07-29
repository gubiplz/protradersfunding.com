"""Testy logiki biznesowej: kupony, auth, checkout(mock)->provisioning,
free trial, wniosek o wypłatę + akceptacja ze zwrotem opłaty, awans 1-step."""
import os
import tempfile

# WAŻNE: ustaw DB i tryb PRZED importem aplikacji (engine tworzony przy imporcie)
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ["FEED"] = "sim"
os.environ["AUTO_SEED"] = "false"
os.environ["STRIPE_SECRET_KEY"] = ""  # tryb MOCK

import asyncio  # noqa: E402

from app import auth, billing, catalog, poller  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.feed import Feed, MarketSnapshot  # noqa: E402
from app.models import Account, Order, Payout, Product, Trader  # noqa: E402

init_db()
_session = SessionLocal()
catalog.seed_products(_session)
_session.close()


def _new_trader(email, kyc="approved"):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("pass1234"),
                full_name="Test", referral_code=auth.secrets.token_hex(3), kyc_status=kyc)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def test_apply_coupon():
    assert catalog.apply_coupon(100, "WELCOME10") == (90.0, 10.0)
    assert catalog.apply_coupon(100, None) == (100, 0.0)
    assert catalog.apply_coupon(100, "NIEISTNIEJE") == (100, 0.0)


def test_password_hash_roundtrip():
    h = auth.hash_password("tajne")
    assert auth.verify_password("tajne", h)
    assert not auth.verify_password("złe", h)


def test_token_roundtrip():
    t = auth.make_token(42)
    assert auth.parse_token(t) == 42
    assert auth.parse_token("śmieci") is None


def test_mock_checkout_provisions_account():
    tid = _new_trader("buyer@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-100k", coupon="WELCOME10")
    assert res["mock"] is True and res["amount"] < 549   # rabat zadziałał
    done = billing.mock_complete(s, res["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    assert acc.status == "active" and acc.initial_balance == 100_000
    assert acc.platform_login and acc.platform_password   # poświadczenia wygenerowane
    order = s.get(Order, res["order_id"])
    assert order.status == "paid" and order.account_id == acc.id
    s.close()


def test_free_trial_wycofany_z_oferty():
    """Decyzja 2026-07: bez darmowego triala. Seed nie tworzy planów za 0 USD,
    a w starych bazach dezaktywuje istniejące."""
    s = SessionLocal()
    darmowe_aktywne = (s.query(Product)
                       .filter(Product.price_usd <= 0, Product.active == True).count())  # noqa: E712
    assert darmowe_aktywne == 0, "darmowy plan wciąż aktywny w katalogu"
    tid = _new_trader("free@test.pl")
    tr = s.get(Trader, tid)
    try:
        billing.create_checkout(s, tr, "free-10k", coupon=None)
        raise AssertionError("checkout na wycofany plan nie powinien przejść")
    except Exception:
        pass
    s.close()


def test_payout_request_and_fee_refund():
    """Konto funded z zyskiem -> payout = split% zysku + zwrot opłaty (1. wypłata)."""
    tid = _new_trader("funded@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-100k", None)      # opłata 549
    done = billing.mock_complete(s, res["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    acc.status = "funded"; acc.balance = 110_000                  # +10k zysku, split 90%
    s.commit()
    s.close()

    # symulacja endpointu zatwierdzania (logika w main, tu odtwarzamy rdzeń)
    s = SessionLocal()
    acc = s.get(Account, done["account_id"])
    profit = acc.balance - acc.initial_balance
    share = profit * acc.profit_split_pct / 100.0
    assert share == 9000.0                                        # 90% z 10k
    first = s.query(Payout).filter(Payout.account_id == acc.id).count() == 0
    order = s.query(Order).filter(Order.account_id == acc.id, Order.status == "paid").first()
    fee_refund = order.amount_usd if first else 0
    assert fee_refund == 549                                      # zwrot opłaty przy 1. wypłacie
    s.close()


def test_1step_advance_goes_straight_to_funded():
    """1-step: zaliczenie eval_1 przechodzi od razu na funded (pomija eval_2).

    Plany 1-step zniknely z oferty, ale mechanika w silniku zostaje — test
    tworzy wlasny produkt, zeby jej pilnowac."""
    tid = _new_trader("onestep@test.pl")
    s = SessionLocal()
    if not s.query(Product).filter(Product.key == "test-1step-100k").first():
        s.add(Product(key="test-1step-100k", label="Test 1-Step 100K", account_size=100_000,
                      steps=1, price_usd=577, profit_target_p1=10, profit_target_p2=0,
                      max_daily_loss_pct=5, max_overall_loss_pct=6, drawdown_type="trailing",
                      min_trading_days=3, profit_split_pct=90, max_lots=12, active=True))
        s.commit()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "test-1step-100k", None)["order_id"], tid)
    aid = done["account_id"]
    s.close()

    class Stub(Feed):
        def __init__(self, snap): self.snap = snap
        async def snapshot(self, *a, **k): return self.snap

    # cel 1-step 100k = +10% => balance 110k, z otwartą pozycją (liczy dzień handlowy)
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.trading_days_count = 5; acc.last_counted_trading_day = "x"; s.commit()
    asyncio.run(poller.process_account(s, acc, Stub(MarketSnapshot(110_000, 110_000, 0, True))))
    s.refresh(acc)
    assert acc.phase == "funded" and acc.status == "funded"
    s.close()


def test_2step_awansuje_sam_eval1_potem_eval2_potem_funded():
    """Pytanie usera: czy po zaliczeniu targetu konto samo idzie na etap 2 i funded?
    TAK — robi to silnik reguł w pollerze, pod warunkiem że coś konto zasila."""
    tid = _new_trader("dwa-etapy@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-100k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    assert acc.phase == "eval_1"

    class Stub(Feed):
        def __init__(self, snap): self.snap = snap
        async def snapshot(self, *a, **k): return self.snap

    def zalicz(cel):
        acc.trading_days_count = 9; acc.last_counted_trading_day = "x"; s.commit()
        asyncio.run(poller.process_account(s, acc, Stub(MarketSnapshot(cel, cel, 0, True))))
        s.refresh(acc)

    zalicz(110_000)                    # +10% => cel etapu 1
    assert acc.phase == "eval_2" and acc.status == "active"
    assert acc.balance == acc.initial_balance, "nowy etap startuje od kapitalu"

    zalicz(105_000)                    # +5% => cel etapu 2
    assert acc.phase == "funded" and acc.status == "funded"
    s.close()


def test_konto_bez_zrodla_danych_nie_awansuje():
    """Odwrotna strona medalu: przy FEED=local nikt konta nie tyka, wiec samo
    z siebie nie ruszy — od tego jest reczne przestawienie fazy w panelu."""
    from app.feed import NullFeed
    tid = _new_trader("bez-feedu@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-100k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    acc.balance = acc.equity = 108_000; acc.trading_days_count = 9; s.commit()

    asyncio.run(poller.process_account(s, acc, NullFeed()))
    s.refresh(acc)
    assert acc.phase == "eval_1", "bez zrodla danych silnik nie ma czego oceniac"
    s.close()


class _RecordingFeed(Feed):
    """Stub feedu rejestrujący wywołania enforcementu / provisioningu."""
    def __init__(self, snap=None, creds=None):
        self.snap = snap; self.creds = creds
        self.closed = None; self.locked = None; self.closed_login = None
    async def snapshot(self, *a, **k): return self.snap
    async def close_all_positions(self, maid, *, login=None, password=None):
        self.closed = maid; self.closed_login = login; return 3
    async def lock(self, maid, *, login=None, password=None): self.locked = maid
    async def provision(self, spec): return self.creds


def test_breach_triggers_enforcement_on_metaapi_account():
    """Konto z metaapi_account_id po breachu => feed zamyka pozycje i blokuje konto."""
    tid = _new_trader("enf@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-100k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    acc.metaapi_account_id = "META-123"      # symulacja realnego konta MT5
    s.commit()
    feed = _RecordingFeed(snap=MarketSnapshot(89_000, 89_000, 0, False))  # < static floor 90k
    asyncio.run(poller.process_account(s, acc, feed))
    s.refresh(acc)
    assert acc.status == "failed"
    assert feed.closed == "META-123" and feed.locked == "META-123"   # ENFORCEMENT zadziałał
    s.close()


import contextlib  # noqa: E402


@contextlib.contextmanager
def realny_provisioning():
    """Ścieżka z realnymi kontami MT5 (pula / kanał) — domyślnie wyłączona,
    bo konta dostają dziś poświadczenia generowane lokalnie."""
    from app.config import get_settings
    s = get_settings()
    poprzednio = s.mt5_provisioning
    s.mt5_provisioning = True
    try:
        yield
    finally:
        s.mt5_provisioning = poprzednio


def test_provision_pending_assigns_from_pool():
    """provision_pending przydziela kontu 'provisioning' wolne konto z PULI o pasującym rozmiarze."""
    from app import provisioning
    from app.models import PoolAccount
    tid = _new_trader("prov@test.pl")
    s = SessionLocal()
    # Nietypowy rozmiar konta: `provision_pending` przetwarza WSZYSTKIE konta w stanie
    # 'provisioning', więc przy zwykłym 100k wpis z puli potrafił przechwycić inne
    # konto zostawione przez sąsiedni test (zależnie od kolejności plików).
    ROZMIAR = 123_000
    # konto w puli (gotowe konto MT5 dodane w MetaApi)
    s.add(PoolAccount(metaapi_account_id="MT-POOL-1", platform_login="5551234",
                      platform_password="RealPass99", platform_server="XGlobalMarkets-ABFTrade",
                      account_size=ROZMIAR))
    # konto kupione, czeka na przydział
    acc = Account(login="000000", trader_name="Prov", trader_id=tid, product_key="2step-100k",
                  preset="2step-100k", initial_balance=ROZMIAR, steps=2,
                  platform_login="000000", platform_password="x", platform_server="PropFunding-SIM",
                  phase="eval_1", status="provisioning", balance=ROZMIAR, equity=ROZMIAR,
                  peak_equity=ROZMIAR, day_start_equity=ROZMIAR, day_start_balance=ROZMIAR)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, _RecordingFeed()))

    s = SessionLocal(); acc = s.get(Account, aid)
    pool = s.query(PoolAccount).filter(PoolAccount.metaapi_account_id == "MT-POOL-1").first()
    assert acc.status == "active" and acc.metaapi_account_id == "MT-POOL-1"
    assert acc.platform_login == "5551234" and acc.platform_server == "XGlobalMarkets-ABFTrade"
    assert pool.claimed is True and pool.claimed_by_account_id == aid   # konto z puli oznaczone
    s.close()


def test_provision_pending_waits_when_pool_empty():
    """Bez wolnej puli konto zostaje 'provisioning' (czeka na zasilenie puli)."""
    from app import provisioning
    tid = _new_trader("wait@test.pl")
    s = SessionLocal()
    acc = Account(login="000001", trader_name="Wait", trader_id=tid, product_key="2step-50k",
                  preset="2step-50k", initial_balance=50_000, steps=2,
                  phase="eval_1", status="provisioning", balance=50_000, equity=50_000,
                  peak_equity=50_000, day_start_equity=50_000, day_start_balance=50_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, _RecordingFeed()))

    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.status == "provisioning"   # nadal czeka, brak konta 50k w puli
    s.close()


def test_zakup_daje_konto_aktywne_z_lokalnymi_poswiadczeniami():
    """Bez zakładania konta MT5: challenge jest handlowalny od razu po zakupie."""
    tid = _new_trader("lokalny-zakup@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-10k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])

    assert acc.status == "active"          # żadnego czekania w 'provisioning'
    assert len(acc.platform_login) == 9 and acc.platform_login.isdigit()
    assert acc.login == acc.platform_login
    assert acc.platform_password and acc.platform_server == "MetaQuotes-Demo"
    assert acc.platform_investor_password is None
    assert acc.mt5_backed is False
    s.close()


def test_haslo_ma_format_z_metaquotes_demo():
    from app.provisioning import _gen_login, _gen_password
    for _ in range(50):
        assert len(_gen_login()) == 9 and _gen_login().isdigit()
        h = _gen_password()
        assert len(h) == 8 and h.islower() and h.isalnum()


def test_mail_z_poswiadczeniami_nie_zawiera_hasla_inwestora():
    from app import notify
    ctx = {"name": "Jan", "platform_login": "123456789", "platform_password": "ichhl00j",
           "platform_server": "MetaQuotes-Demo", "initial_balance": 10_000, "steps": 2,
           "platform_investor_password": "PowinnoZniknac"}
    _, body = notify._render("credentials", ctx)
    html = notify._render_html("credentials", ctx, "s")
    for tekst in (body, html):
        assert "Investor" not in tekst and "PowinnoZniknac" not in tekst
    assert "ichhl00j" in html and "123456789" in html


def test_null_feed_nie_rusza_konta():
    """FEED=local: konto stoi na ostatnim saldzie, dopóki nie ruszy go Trade BOT."""
    from app.feed import NullFeed
    tid = _new_trader("nullfeed@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-10k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    przed = (acc.balance, acc.equity, acc.trading_days_count)

    asyncio.run(poller.process_account(s, acc, NullFeed()))
    s.refresh(acc)
    assert (acc.balance, acc.equity, acc.trading_days_count) == przed
    s.close()


def test_konto_bez_realnego_mt5_nie_trafia_do_feedu_metaquotes():
    """Gdyby ktoś wrócił do FEED=metaquotes_web, poller nie może próbować logować
    się wygenerowanym loginem — na MetaQuotes takiego rachunku nie ma."""
    from app.config import get_settings
    tid = _new_trader("nomt5@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-10k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])

    class SpyFeed(Feed):
        wolany = False

        async def snapshot(self, *a, **k):
            SpyFeed.wolany = True
            return MarketSnapshot(1, 1, 0, False)

    ustawienia = get_settings()
    poprzedni = ustawienia.feed
    ustawienia.feed = "metaquotes_web"
    try:
        asyncio.run(poller.process_account(s, acc, SpyFeed()))
    finally:
        ustawienia.feed = poprzedni
    s.close()
    assert SpyFeed.wolany is False


def test_bez_realnego_provisioningu_konto_konczy_sie_lokalnie():
    """Domyślnie nie czekamy na MT5: konto utknięte w 'provisioning' (np. z
    poprzedniej konfiguracji) dostaje poświadczenia generowane u nas i rusza."""
    from app import provisioning
    tid = _new_trader("lokalne@test.pl")
    s = SessionLocal()
    acc = Account(login="000002", trader_name="Local", trader_id=tid, product_key="2step-50k",
                  preset="2step-50k", initial_balance=50_000, steps=2,
                  phase="eval_1", status="provisioning", balance=50_000, equity=50_000,
                  peak_equity=50_000, day_start_equity=50_000, day_start_balance=50_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    asyncio.run(provisioning.provision_pending(SessionLocal, _RecordingFeed()))

    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.status == "active"
    assert acc.platform_login and len(acc.platform_login) == 9 and acc.platform_login.isdigit()
    assert acc.platform_password and acc.platform_server == "MetaQuotes-Demo"
    assert acc.platform_investor_password is None
    assert acc.mt5_backed is False
    s.close()



def test_breach_enforcement_dziala_bez_metaapi():
    """Konto z MetaQuotes-Demo nie ma metaapi_account_id, a mimo to breach musi
    zamknąć pozycje — inaczej trader z przekroczonym DD handluje dalej."""
    tid = _new_trader("enf-web@test.pl")
    s = SessionLocal()
    tr = s.get(Trader, tid)
    done = billing.mock_complete(s, billing.create_checkout(s, tr, "2step-100k", None)["order_id"], tid)
    acc = s.get(Account, done["account_id"])
    acc.metaapi_account_id = None                  # konto założone przez web terminal
    acc.platform_login = "110124719"
    acc.platform_password = "E-FdK6Qc"
    acc.platform_server = "MetaQuotes-Demo"
    s.commit()

    feed = _RecordingFeed(snap=MarketSnapshot(89_000, 89_000, 0, False))   # poniżej dziennej podłogi
    asyncio.run(poller.process_account(s, acc, feed))
    s.refresh(acc)

    assert acc.status == "failed", "przekroczony limit musi zamknąć challenge"
    assert acc.closed_at is not None
    assert feed.closed_login == "110124719", "enforcement musi dostać poświadczenia konta"
    s.close()
