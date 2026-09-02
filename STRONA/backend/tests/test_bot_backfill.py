"""Backfill bota: historia dogrywana WSTECZ, ale tylko kontom firmowym.

Po ręcznym wyczyszczeniu bazy tablica wyników świeciła pustką, a bot w czasie
rzeczywistym potrzebuje tygodni, żeby krzywa wyglądała na dojrzałą. Backfill
odtwarza przeszłość tym samym silnikiem co żywy przebieg — więc pilnujemy tu
trzech rzeczy, które łatwo popsuć niezauważenie:

* historia MUSI leżeć w przeszłości (transakcje, snapshoty i metryka startu
  konta cofnięte), inaczej dogrywka zdradza się jedną kolumną „Created",
* dostają ją WYŁĄCZNIE konta firmowe (bez właściciela, bez MT5) — klientowi
  wykresu się nie dopisuje,
* na czas dogrywki zamek w bazie wyłącza konto z pollera — lazy-tick z ruchu
  publicznego wpychałby „dzisiejsze" ticki w środek odtwarzanej przeszłości.
"""
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, poller  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import _leaderboard_rows, app  # noqa: E402
from app.models import Account, AppSetting, EquitySnapshot, Trade, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

LICZNIK = iter(range(1000))


def _konto_firmowe(nazwisko="Marco Silva") -> int:
    login = f"9100{next(LICZNIK):04d}"
    r = client.post("/api/accounts", headers=ADMIN_H,
                    json={"login": login, "trader_name": nazwisko,
                          "product_key": "instant-50k"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    r = client.post(f"/api/admin/accounts/{aid}/bot", headers=ADMIN_H,
                    json={"style": "balanced", "pace": "steady", "target_pct": 0.0})
    assert r.status_code == 200, r.text
    # Seed z loginu, nie z auto-increment id — inaczej trajektoria bota zależy
    # od tego, ile kont założyły WCZEŚNIEJSZE pliki testów (patrz test_tradebot).
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.bot_seed = int.from_bytes(hashlib.sha256(login.encode()).digest()[:4], "big") & 0x7FFFFFFF
    s.commit(); s.close()
    return aid


def _dogryj(aid: int, days: int) -> dict:
    """Woła backfill aż do `done` — tak samo będzie robił skrypt zasilający."""
    for _ in range(40):
        r = client.post(f"/api/admin/accounts/{aid}/bot/backfill", headers=ADMIN_H,
                        json={"days": days})
        assert r.status_code == 200, r.text
        d = r.json()
        if d["done"]:
            return d
    raise AssertionError("backfill nie domknął się w 40 wywołaniach")


def test_historia_lezy_w_przeszlosci():
    aid = _konto_firmowe()
    wynik = _dogryj(aid, 10)
    teraz = datetime.now(timezone.utc).replace(tzinfo=None)

    s = SessionLocal()
    acc = s.get(Account, aid)
    trades = s.query(Trade).filter(Trade.account_id == aid).all()
    snapy = s.query(EquitySnapshot).filter(EquitySnapshot.account_id == aid).all()
    s.close()

    # Metryka startu cofnięta — historia starsza niż konto zdradzałaby dogrywkę.
    assert acc.created_at <= teraz - timedelta(days=9, hours=23)
    assert acc.bot_started_at <= teraz - timedelta(days=9, hours=23)

    assert len(trades) > 10, "10 dni handlu bez transakcji to nie historia"
    assert wynik["trades"] == len(trades)
    najstarsza = min(t.opened_at for t in trades)
    # Luz na weekend na starcie okna: rynek bywa zamknięty do poniedziałku.
    assert najstarsza <= teraz - timedelta(days=6)

    assert snapy, "wykres equity bez ani jednego punktu"
    assert max(sn.ts for sn in snapy) <= teraz - timedelta(minutes=50)
    assert min(sn.ts for sn in snapy) <= teraz - timedelta(days=9)

    # Silnik reguł naliczał dni po drodze, nie tylko przestawił saldo.
    assert acc.trading_days_count >= 5
    assert round(acc.balance, 2) != acc.initial_balance


def test_zywy_tick_przed_dogrywka_nie_zatruwa_historii():
    """Na prodzie każdy gość strony odpala lazy-tick: między startem bota a
    pierwszym backfillem potrafi wpaść pozycja z DZISIEJSZĄ datą i zamknięciem
    w realnej przyszłości. Replay nigdy by jej nie domknął — bot przesiedziałby
    całą dogrywkę na jednej wiecznie otwartej pozycji z płaską krzywą. Pierwsze
    dogranie ma więc zacząć od czystej karty."""
    from app import tradebot
    aid = _konto_firmowe()
    s = SessionLocal()
    acc = s.get(Account, aid)
    tradebot.tick(s, acc)          # żywy tick „z ruchu" tuż po starcie bota
    s.commit()
    zatruta = s.query(Trade).filter(Trade.account_id == aid).count()
    s.close()
    assert zatruta >= 1, "tick na świeżym bocie miał otworzyć pozycję"

    _dogryj(aid, 10)
    teraz = datetime.now(timezone.utc).replace(tzinfo=None)
    s = SessionLocal()
    trades = s.query(Trade).filter(Trade.account_id == aid).all()
    acc = s.get(Account, aid)
    s.close()
    assert len(trades) > 10, "replay utknął na pozycji sprzed dogrywki"
    assert min(t.opened_at for t in trades) <= teraz - timedelta(days=6)
    assert round(acc.balance, 2) != acc.initial_balance


def test_ranking_widzi_konto_firmowe():
    """Kontrakt z tablicą wyników: konto bez właściciela wchodzi do rankingu
    jak każde inne funded — po samym zysku, z nazwiskiem z konta."""
    aid = _konto_firmowe(nazwisko="Elena Vasquez")
    _dogryj(aid, 12)
    s = SessionLocal(); acc = s.get(Account, aid); s.close()
    zysk = round((acc.balance - acc.initial_balance) / acc.initial_balance * 100, 2)
    maski = [r["trader"] for r in _leaderboard_rows()]
    if zysk > 0:
        assert "Elena V." in maski
    else:
        assert "Elena V." not in maski


def test_drugie_dogranie_niczego_nie_dopisuje():
    """Kursor wznowień: po `done` kolejne wywołanie nie dokleja drugiej
    przeszłości ani nie cofa konta jeszcze raz."""
    aid = _konto_firmowe()
    _dogryj(aid, 7)
    s = SessionLocal()
    najstarsza = min(t.opened_at for t in
                     s.query(Trade).filter(Trade.account_id == aid).all())
    s.close()
    znow = client.post(f"/api/admin/accounts/{aid}/bot/backfill", headers=ADMIN_H,
                       json={"days": 30}).json()
    assert znow["done"]
    s = SessionLocal()
    po = min(t.opened_at for t in
             s.query(Trade).filter(Trade.account_id == aid).all())
    s.close()
    assert po == najstarsza, "powtórka backfillu dokleiła starszą historię"


def test_tylko_konto_firmowe_i_tylko_z_botem():
    # Konto klienta: właściciel istnieje — historia to fakty, nie scenografia.
    s = SessionLocal()
    tr = Trader(email="backfill@test.pl", password_hash=auth.hash_password("haslo12345"),
                full_name="Realny Klient", referral_code="BACKFILL1")
    s.add(tr); s.commit()
    acc = Account(login="91009901", trader_name="Realny Klient", trader_id=tr.id,
                  product_key="instant-50k", initial_balance=50_000.0,
                  balance=50_000.0, equity=50_000.0, peak_equity=50_000.0,
                  day_start_equity=50_000.0, day_start_balance=50_000.0,
                  phase="funded", status="funded", bot_enabled=True, mt5_backed=False)
    s.add(acc); s.commit(); klient_aid = acc.id; s.close()
    r = client.post(f"/api/admin/accounts/{klient_aid}/bot/backfill", headers=ADMIN_H,
                    json={"days": 5})
    assert r.status_code == 400

    # Konto firmowe, ale bot jeszcze nie wystartował — nie ma czego odtwarzać.
    login = f"9100{next(LICZNIK):04d}"
    aid = client.post("/api/accounts", headers=ADMIN_H,
                      json={"login": login, "trader_name": "Bez Bota",
                            "product_key": "instant-50k"}).json()["id"]
    r = client.post(f"/api/admin/accounts/{aid}/bot/backfill", headers=ADMIN_H,
                    json={"days": 5})
    assert r.status_code == 400

    r = client.post(f"/api/admin/accounts/{aid}/bot/backfill", headers=ADMIN_H,
                    json={"days": 0})
    assert r.status_code == 400


def test_zamek_znika_po_domknieciu():
    """W trakcie dogrywki poller ma konto omijać, po `done` — puścić z powrotem.
    Zamek bez TTL zostawiłby konto wyłączone z silnika na zawsze, gdyby proces
    padł w połowie."""
    aid = _konto_firmowe()
    czesc = client.post(f"/api/admin/accounts/{aid}/bot/backfill", headers=ADMIN_H,
                        json={"days": 30}).json()
    s = SessionLocal()
    try:
        if not czesc["done"]:
            assert poller._backfill_locked_id(s) == aid
        _dogryj(aid, 30)
        s.expire_all()
        assert poller._backfill_locked_id(s) is None

        # TTL: stary zamek (proces ubity kwadrans temu) przestaje obowiązywać.
        zamek = s.get(AppSetting, poller.BACKFILL_LOCK_KEY)
        zamek.value = f"{aid}:1000000000.0"
        s.commit()
        assert poller._backfill_locked_id(s) is None
    finally:
        s.close()


def test_panel_odroznia_konto_firmowe():
    """Kontrakt z zakładką „House bots": lista kont niesie `trader_id` i
    `mt5_backed`, z których panel składa rozróżnienie firmowe/klienckie."""
    aid = _konto_firmowe(nazwisko="Panel Housetest")
    lista = client.get("/api/accounts", headers=ADMIN_H).json()
    moje = next(a for a in lista if a["id"] == aid)
    assert moje["trader_id"] is None
    assert moje["mt5_backed"] is False
