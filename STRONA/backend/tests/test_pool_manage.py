"""Zarządzanie pulą MT5: korekta danych i generowanie kont demo.

Dwie rzeczy, które muszą działać poprawnie:

* Zmiana hasła rachunku, ktory jest JUZ PRZYDZIELONY, musi trafic takze na konto
  tradera — inaczej portal pokazuje stare dane i trader nie zaloguje sie do MT5.
* Przycisk „Auto-generate" nie moze udawac, ze dziala tam, gdzie nie ma
  przegladarki. Na hostingu bezserwerowym Chromium nie istnieje, wiec endpoint
  ma odmowic z wyjasnieniem, a nie wysypac sie w polowie.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import catalog  # noqa: E402
from app import main as app_main  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, PoolAccount  # noqa: E402

init_db()
client = TestClient(app)
ADMIN_H = {"X-Admin-Token": get_settings().admin_token}

# AUTO_SEED jest wylaczone, a reczne tworzenie challenge'u potrzebuje katalogu planow.
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()


def _wpis(login: str, rozmiar: float = 50_000.0, przydzielony_do: int | None = None) -> int:
    s = SessionLocal()
    p = PoolAccount(platform_login=login, platform_password="Stare123",
                    platform_server="GOMarketsLtd-Demo", account_size=rozmiar,
                    claimed=przydzielony_do is not None,
                    claimed_by_account_id=przydzielony_do)
    s.add(p); s.commit()
    pid = p.id
    s.close()
    return pid


def _konto(login: str, rozmiar: float = 50_000.0) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_name="Edit Tester", product_key="2step-50k",
                  preset="2step-50k", initial_balance=rozmiar, steps=2, status="active",
                  phase="eval_1", platform_login=login, platform_password="Stare123",
                  platform_server="GOMarketsLtd-Demo", balance=rozmiar, equity=rozmiar,
                  peak_equity=rozmiar, day_start_equity=rozmiar, day_start_balance=rozmiar)
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_zmiana_hasla_wolnego_wpisu():
    pid = _wpis("8100001")
    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                     json={"platform_password": "Nowe456", "platform_server": "InnyBroker-Demo"})
    assert r.status_code == 200 and r.json()["propagated_to_account"] is None

    s = SessionLocal(); p = s.get(PoolAccount, pid)
    assert p.platform_password == "Nowe456" and p.platform_server == "InnyBroker-Demo"
    s.close()


def test_zmiana_poswiadczen_przydzielonego_idzie_na_konto_tradera():
    """Bez tego trader zostaje ze starym haslem i nie wejdzie do terminala."""
    aid = _konto("8200001")
    pid = _wpis("8200001", przydzielony_do=aid)

    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                     json={"platform_password": "ZmienioneUBrokera9"})
    assert r.status_code == 200 and r.json()["propagated_to_account"] == aid

    s = SessionLocal(); acc = s.get(Account, aid)
    assert acc.platform_password == "ZmienioneUBrokera9"
    s.close()


def test_rozmiaru_przydzielonego_rachunku_nie_da_sie_zmienic():
    """Konto challenge ma juz od niego policzone limity ryzyka."""
    aid = _konto("8300001")
    pid = _wpis("8300001", przydzielony_do=aid)
    r = client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H, json={"account_size": 200_000})
    assert r.status_code == 400


def test_puste_pole_jest_odrzucane():
    pid = _wpis("8400001")
    assert client.patch(f"/api/admin/pool/{pid}", headers=ADMIN_H,
                        json={"platform_login": "   "}).status_code == 400


def test_edycja_nieistniejacego_wpisu_to_404():
    assert client.patch("/api/admin/pool/999999", headers=ADMIN_H,
                        json={"platform_server": "X"}).status_code == 404


def test_generowanie_odmawia_bez_przegladarki(monkeypatch):
    """Na hostingu bezserwerowym nie ma Chromium — endpoint ma to powiedziec wprost."""
    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: False)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)

    r = client.post("/api/admin/pool/generate", headers=ADMIN_H,
                    json={"account_size": 50_000, "count": 1})
    assert r.status_code == 400
    assert "Chromium" in r.json()["detail"]

    # panel dostaje te sama informacje, zeby nie pokazywac przycisku, ktory nie zadziala
    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    assert dane["can_generate"] is False and dane["generate_hint"]


def test_generowanie_pilnuje_limitow(monkeypatch):
    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: True)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)

    assert client.post("/api/admin/pool/generate", headers=ADMIN_H,
                       json={"account_size": 0, "count": 1}).status_code == 400
    assert client.post("/api/admin/pool/generate", headers=ADMIN_H,
                       json={"account_size": 50_000, "count": 99}).status_code == 400


def test_generowanie_zapisuje_konta_z_kanalu(monkeypatch):
    """Kanal jest zamockowany — sprawdzamy, ze zwrocone poswiadczenia ladują w puli."""
    class _FakeCreds:
        def __init__(self, login):
            self.login, self.password, self.server = login, "WygenerowaneX1", "MetaQuotes-Demo"

    class _FakeOpener:
        def __init__(self): self.n = 0
        async def open_demo_account(self, spec):
            self.n += 1
            return _FakeCreds(f"9900{self.n:03d}")

    monkeypatch.setattr(app_main.metaquotes_web, "chromium_available", lambda: True)
    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)
    monkeypatch.setattr(app_main.metaquotes_web, "make_opener", lambda s=None: _FakeOpener())

    r = client.post("/api/admin/pool/generate", headers=ADMIN_H,
                    json={"account_size": 25_000, "count": 2})
    assert r.status_code == 200
    assert len(r.json()["created"]) == 2

    s = SessionLocal()
    wpisy = s.query(PoolAccount).filter(PoolAccount.account_size == 25_000).all()
    assert len(wpisy) == 2
    assert all(w.platform_server == "MetaQuotes-Demo" and not w.claimed for w in wpisy)
    s.close()


def test_zdalna_przegladarka_odblokowuje_generator(monkeypatch):
    """BROWSER_CDP_URL = przegladarka stoi poza tym procesem, wiec lokalna zbedna.

    To jedyna droga, zeby kanal MetaQuotes dzialal na hostingu bezserwerowym —
    bez tego panel slusznie odmawia, ale z ustawionym adresem ma przepuscic.
    """
    from app import metaquotes_web

    monkeypatch.setattr(app_main.settings, "metaquotes_web_enabled", True)
    monkeypatch.setattr(get_settings(), "browser_cdp_url", "http://zdalna:9222")

    assert metaquotes_web.chromium_available() is True
    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    assert dane["can_generate"] is True


def test_opener_podlacza_sie_zamiast_uruchamiac(monkeypatch):
    """Z adresem CDP opener ma sie LACZYC, a nie startowac wlasnego Chromium."""
    from app import metaquotes_web

    ustawienia = get_settings()
    monkeypatch.setattr(ustawienia, "metaquotes_web_enabled", True)
    monkeypatch.setattr(ustawienia, "browser_cdp_url", "wss://browserless.example/?token=abc")

    opener = metaquotes_web.make_opener(ustawienia)
    assert opener is not None
    assert opener._cdp_url == "wss://browserless.example/?token=abc"

    monkeypatch.setattr(ustawienia, "browser_cdp_url", "")
    assert metaquotes_web.make_opener(ustawienia)._cdp_url == ""


def test_rachunek_usunietego_konta_nie_wraca_do_puli():
    """Rachunek byl juz w rekach tradera: ma historie i zna go poprzedni wlasciciel.

    Gdyby wrocil do puli, nowy klient zobaczylby cudze transakcje, a stary
    zachowalby dzialajace poswiadczenia do konta kogos innego.
    """
    aid = _konto("8600001")
    pid = _wpis("8600001", przydzielony_do=aid)

    assert client.delete(f"/api/accounts/{aid}", headers=ADMIN_H).status_code == 200

    s = SessionLocal(); p = s.get(PoolAccount, pid)
    assert p.claimed is True, "wpis nie moze wrocic do obiegu"
    assert p.retired_reason, "panel musi wiedziec, dlaczego rachunek jest wycofany"
    s.close()

    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    wpis = [x for x in dane["pool"] if x["id"] == pid][0]
    assert wpis["retired_reason"] and wpis["claimed"] is True


def test_wycofany_rachunek_nie_zostanie_przydzielony_ponownie():
    from app import provisioning

    ROZMIAR = 87_000.0                       # unikalny, zeby zaden inny wpis nie pasowal
    aid = _konto("8700001", ROZMIAR)
    _wpis("8700001", ROZMIAR, przydzielony_do=aid)
    client.delete(f"/api/accounts/{aid}", headers=ADMIN_H)

    s = SessionLocal()
    nowe = Account(login="8700002", trader_name="Nastepny", product_key="2step-50k",
                   preset="2step-50k", initial_balance=ROZMIAR, steps=2, status="provisioning",
                   phase="eval_1", balance=ROZMIAR, equity=ROZMIAR, peak_equity=ROZMIAR,
                   day_start_equity=ROZMIAR, day_start_balance=ROZMIAR)
    s.add(nowe); s.commit()

    assert provisioning.claim_pool_account(s, nowe) is False, "wycofany rachunek nie moze wrocic"
    s.close()


def test_konto_ze_statusem_failed_trzyma_swoj_rachunek():
    """Breach nie zwalnia rachunku — konto istnieje dalej, tylko przegralo."""
    from app import provisioning

    ROZMIAR = 88_000.0                       # jw. — wlasny rozmiar izoluje ten przypadek
    aid = _konto("8800001", ROZMIAR)
    pid = _wpis("8800001", ROZMIAR, przydzielony_do=aid)
    s = SessionLocal()
    s.get(Account, aid).status = "failed"
    s.commit()

    p = s.get(PoolAccount, pid)
    assert p.claimed is True and p.claimed_by_account_id == aid

    nowe = Account(login="8800002", trader_name="Nastepny", product_key="2step-50k",
                   preset="2step-50k", initial_balance=ROZMIAR, steps=2, status="provisioning",
                   phase="eval_1", balance=ROZMIAR, equity=ROZMIAR, peak_equity=ROZMIAR,
                   day_start_equity=ROZMIAR, day_start_balance=ROZMIAR)
    s.add(nowe); s.commit()
    assert provisioning.claim_pool_account(s, nowe) is False
    s.close()


# --------------------------------------------------------------------------- #
#  Reczne tworzenie challenge'u — bez ruszania puli                            #
# --------------------------------------------------------------------------- #
def test_reczny_challenge_z_pelnymi_poswiadczeniami_nie_rusza_puli():
    from app.models import Product
    s = SessionLocal()
    wolnych_przed = s.query(PoolAccount).filter(PoolAccount.claimed == False).count()  # noqa: E712
    plan = s.query(Product).filter(Product.active == True).first()  # noqa: E712
    s.close()

    r = client.post("/api/accounts", headers=ADMIN_H, json={
        "login": "9100001", "platform_password": "Recznie1", "platform_server": "MetaQuotes-Demo",
        "trader_name": "Reczny Klient", "product_key": plan.key})
    assert r.status_code == 200
    d = r.json()
    assert d["platform_login"] == "9100001" and d["platform_password"] == "Recznie1"
    assert d["platform_server"] == "MetaQuotes-Demo"

    s = SessionLocal()
    assert s.query(PoolAccount).filter(PoolAccount.claimed == False).count() == wolnych_przed  # noqa: E712
    s.close()


def test_drawdown_bierze_sie_z_planu_a_nie_z_osobnego_pola():
    """Pole drawdownu znikneło z panelu — plan jest jedynym zrodlem prawdy."""
    from app.models import Product
    s = SessionLocal()
    plan = s.query(Product).filter(Product.active == True).first()  # noqa: E712
    oczekiwany = plan.drawdown_type
    s.close()

    r = client.post("/api/accounts", headers=ADMIN_H,
                    json={"login": "9200001", "product_key": plan.key})
    assert r.status_code == 200

    s = SessionLocal()
    acc = s.query(Account).filter(Account.login == "9200001").first()
    assert acc.drawdown_type == oczekiwany
    s.close()


def test_email_wiaze_konto_z_zarejestrowanym_traderem():
    from app import auth as app_auth
    from app.models import Product, Trader
    s = SessionLocal()
    tr = Trader(email="wlasciciel@reczne.pl", password_hash=app_auth.hash_password("x"),
                full_name="Wlasciciel Reczny", referral_code="RECZ1")
    s.add(tr); s.commit()
    tid = tr.id
    plan = s.query(Product).filter(Product.active == True).first()  # noqa: E712
    s.close()

    r = client.post("/api/accounts", headers=ADMIN_H, json={
        "login": "9300001", "platform_password": "Haslo1", "platform_server": "MetaQuotes-Demo",
        "trader_email": "WLASCICIEL@Reczne.pl", "product_key": plan.key})
    assert r.status_code == 200 and r.json()["linked_trader"] == "wlasciciel@reczne.pl"

    s = SessionLocal()
    acc = s.query(Account).filter(Account.login == "9300001").first()
    assert acc.trader_id == tid
    s.close()


def test_nieznany_email_nie_gubi_konta_ale_jest_zglaszany():
    """Konto ma powstac, tylko panel musi powiedziec, ze nie ma wlasciciela."""
    from app.models import Product
    s = SessionLocal()
    plan = s.query(Product).filter(Product.active == True).first()  # noqa: E712
    s.close()

    r = client.post("/api/accounts", headers=ADMIN_H, json={
        "login": "9400001", "trader_email": "nikt@nieistnieje.pl", "product_key": plan.key})
    assert r.status_code == 200
    assert r.json()["email_unknown"] is True and r.json()["linked_trader"] is None


def test_promocja_zapisuje_sie_jak_przy_grancie():
    from app.models import Product
    s = SessionLocal()
    plany = s.query(Product).filter(Product.active == True).order_by(Product.account_size).all()  # noqa: E712
    maly, duzy = plany[0], plany[-1]
    s.close()

    r = client.post("/api/accounts", headers=ADMIN_H, json={
        "login": "9500001", "product_key": duzy.key,
        "note": "BOGO promotion", "bogo_paid_key": maly.key})
    assert r.status_code == 200

    s = SessionLocal()
    acc = s.query(Account).filter(Account.login == "9500001").first()
    assert acc.grant_note == "BOGO promotion"
    assert acc.bogo_paid_size == maly.account_size
    assert acc.source == "grant"
    s.close()


# --------------------------------------------------------------------------- #
#  Kasowanie wpisow z puli                                                     #
# --------------------------------------------------------------------------- #
def test_wolny_wpis_da_sie_skasowac():
    pid = _wpis("8800001")
    r = client.delete(f"/api/admin/pool/{pid}", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["was_retired"] is False

    s = SessionLocal()
    assert s.get(PoolAccount, pid) is None
    s.close()


def test_zywy_przydzielony_wpis_zostaje():
    """Za takim rachunkiem stoi konto tradera — skasowanie zabiera mu poswiadczenia."""
    aid = _konto("8800002")
    pid = _wpis("8800002", przydzielony_do=aid)

    r = client.delete(f"/api/admin/pool/{pid}", headers=ADMIN_H)
    assert r.status_code == 400, "przydzielonego w uzyciu nie wolno skasowac"

    s = SessionLocal()
    assert s.get(PoolAccount, pid) is not None
    s.close()


def test_wycofany_wpis_da_sie_skasowac():
    """Wpisy `retired` zostawaly w tabeli na zawsze: byly `claimed`, wiec blokada
    obejmowala tez je, mimo ze konto tradera juz nie istnieje."""
    aid = _konto("8800003")
    pid = _wpis("8800003", przydzielony_do=aid)
    client.delete(f"/api/accounts/{aid}", headers=ADMIN_H)      # ustawia retired_reason

    s = SessionLocal()
    assert s.get(PoolAccount, pid).retired_reason, "warunek testu: wpis ma byc wycofany"
    s.close()

    r = client.delete(f"/api/admin/pool/{pid}", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["was_retired"] is True

    s = SessionLocal()
    assert s.get(PoolAccount, pid) is None
    s.close()
