"""Pula MT5 jako JEDYNE źródło poświadczeń + ślad, komu rachunek przypadł.

Broker nie pozwala zakładać kont demo programowo, więc rachunki powstają poza
systemem i admin wkleja je do puli: login, hasło, serwer, rozmiar. Provisioning
tylko je przypisuje — i musi zostawić ślad, czyj jest dany rachunek MT5, bo
inaczej pula jest workiem loginów bez właścicieli.
"""
import asyncio
import os
import tempfile
from contextlib import contextmanager

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, provisioning  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, PoolAccount, Trader  # noqa: E402

init_db()
client = TestClient(app)
USTAWIENIA = get_settings()
ADMIN_H = {"X-Admin-Token": USTAWIENIA.admin_token}


@contextmanager
def realny_provisioning(zrodlo: str = "pool"):
    """MT5_PROVISIONING=true — konta czekaja na poswiadczenia zamiast dostac lokalne."""
    prov, src = USTAWIENIA.mt5_provisioning, USTAWIENIA.provisioning_source
    USTAWIENIA.mt5_provisioning = True
    USTAWIENIA.provisioning_source = zrodlo
    try:
        yield
    finally:
        USTAWIENIA.mt5_provisioning = prov
        USTAWIENIA.provisioning_source = src


def _trader(email: str) -> int:
    s = SessionLocal()
    tr = s.query(Trader).filter(Trader.email == email).first()
    if tr is None:
        tr = Trader(email=email, password_hash=auth.hash_password("tajne123"),
                    full_name="Pool Tester", referral_code=email[:8].upper())
        s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid


def _konto_czekajace(tid: int, rozmiar: float) -> int:
    s = SessionLocal()
    acc = Account(login="000000", trader_name="Pool Tester", trader_id=tid,
                  product_key="2step-100k", preset="2step-100k", initial_balance=rozmiar,
                  steps=2, phase="eval_1", status="provisioning", balance=rozmiar,
                  equity=rozmiar, peak_equity=rozmiar, day_start_equity=rozmiar,
                  day_start_balance=rozmiar)
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


def test_przydzial_zapisuje_komu_rachunek_przypadl():
    ROZMIAR = 71_000
    tid = _trader("wlasciciel@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)
    s = SessionLocal()
    s.add(PoolAccount(platform_login="7100001", platform_password="Haslo123",
                      platform_server="GOMarketsLtd-Demo", account_size=ROZMIAR))
    s.commit(); s.close()

    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, None))

    s = SessionLocal()
    acc = s.get(Account, aid)
    pool = s.query(PoolAccount).filter(PoolAccount.platform_login == "7100001").first()
    assert acc.status == "active"
    assert acc.platform_login == "7100001" and acc.platform_server == "GOMarketsLtd-Demo"
    assert acc.platform_password == "Haslo123"
    # slad przydzialu — to jest sedno monitorowania puli
    assert pool.claimed is True
    assert pool.claimed_by_account_id == aid
    assert pool.claimed_by_trader_id == tid
    assert pool.claimed_at is not None
    s.close()


def test_wpis_bez_metaapi_dziala_a_konto_nie_dostaje_zmyslonego_id():
    """Admin nie podaje metaapi_account_id — konto nie moze go sobie wymyslic."""
    ROZMIAR = 72_000
    tid = _trader("bezmeta@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)
    s = SessionLocal()
    s.add(PoolAccount(platform_login="7200001", platform_password="Haslo123",
                      platform_server="GOMarketsLtd-Demo", account_size=ROZMIAR))
    s.commit(); s.close()

    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, None))

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "active" and acc.metaapi_account_id is None
    s.close()


def test_dosypanie_puli_od_reki_uzbraja_czekajace_konto(monkeypatch):
    """Admin dodaje rachunek do puli DLA konta, które już czeka — poświadczenia
    i mail mają pójść od razu, nie przy najbliższym dziennym cronie."""
    from app import notify
    zebrane = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: zebrane.append((event, to)))
    ROZMIAR = 74_000
    tid = _trader("dosypka@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)

    with realny_provisioning():
        r = client.post("/api/admin/pool", headers=ADMIN_H,
                        json={"platform_login": "7400001", "platform_password": "Haslo123",
                              "platform_server": "GOMarketsLtd-Demo", "account_size": ROZMIAR})
        assert r.status_code == 200

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "active" and acc.platform_login == "7400001"
    s.close()
    assert ("credentials", "dosypka@pool.pl") in zebrane


def test_bez_pasujacego_rozmiaru_konto_czeka():
    ROZMIAR = 73_000
    tid = _trader("czeka@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)
    s = SessionLocal()
    s.add(PoolAccount(platform_login="7300099", platform_password="Haslo123",
                      platform_server="GOMarketsLtd-Demo", account_size=ROZMIAR + 5_000))
    s.commit(); s.close()

    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, None))

    s = SessionLocal()
    assert s.get(Account, aid).status == "provisioning"
    s.close()


def test_zrodlem_jest_wylacznie_pula_mimo_wlaczonych_kanalow_automatycznych():
    """METAQUOTES_WEB_ENABLED nie ma znaczenia przy PROVISIONING_SOURCE=pool.

    Gdyby kanal automatyczny mimo wszystko wystartowal, konto dostaloby
    poswiadczenia z web terminala zamiast z puli — a caly sens tej zmiany polega
    na tym, ze rachunki pochodza WYLACZNIE od admina.
    """
    ROZMIAR = 74_000
    tid = _trader("tylkopula@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)
    s = SessionLocal()
    s.add(PoolAccount(platform_login="7400001", platform_password="Haslo123",
                      platform_server="GOMarketsLtd-Demo", account_size=ROZMIAR))
    s.commit(); s.close()

    poprzednio = USTAWIENIA.metaquotes_web_enabled
    USTAWIENIA.metaquotes_web_enabled = True
    try:
        with realny_provisioning("pool"):
            asyncio.run(provisioning.provision_pending(SessionLocal, None))
    finally:
        USTAWIENIA.metaquotes_web_enabled = poprzednio

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.platform_login == "7400001"      # z puli, nie z web terminala
    assert acc.platform_server == "GOMarketsLtd-Demo"
    s.close()


def test_api_puli_przyjmuje_cztery_pola_i_pokazuje_przydzial():
    r = client.post("/api/admin/pool", headers=ADMIN_H, json={
        "platform_login": "7500001", "platform_password": "Haslo123",
        "platform_server": "GOMarketsLtd-Demo", "account_size": 75_000})
    assert r.status_code == 200

    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    assert "pool" in dane and "waiting" in dane
    wpis = [p for p in dane["pool"] if p["platform_login"] == "7500001"][0]
    assert wpis["claimed"] is False and wpis["trader_email"] is None


def test_api_puli_wystawia_zamowienia_czekajace_na_rachunek():
    ROZMIAR = 76_000
    tid = _trader("wkolejce@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)

    dane = client.get("/api/admin/pool", headers=ADMIN_H).json()
    czeka = [w for w in dane["waiting"] if w["account_id"] == aid]
    assert czeka, "konto w statusie provisioning musi byc widoczne jako oczekujace"
    assert czeka[0]["account_size"] == ROZMIAR
    assert czeka[0]["trader_email"] == "wkolejce@pool.pl"


def test_wolny_wpis_mozna_usunac_a_przydzielonego_nie():
    s = SessionLocal()
    wolny = PoolAccount(platform_login="7700001", platform_password="x",
                        platform_server="S", account_size=77_000)
    zajety = PoolAccount(platform_login="7700002", platform_password="x",
                         platform_server="S", account_size=77_000,
                         claimed=True, claimed_by_account_id=1)
    s.add_all([wolny, zajety]); s.commit()
    id_wolny, id_zajety = wolny.id, zajety.id
    s.close()

    assert client.delete(f"/api/admin/pool/{id_wolny}", headers=ADMIN_H).status_code == 200
    assert client.delete(f"/api/admin/pool/{id_zajety}", headers=ADMIN_H).status_code == 400


def test_generator_symulowanych_wpisow_i_przydzial_bez_mt5():
    """Wpisy simulated maja poswiadczenia w formacie MetaQuotes, a konto z nich
    dostaje mt5_backed=False — realny feed nie moze sie logowac zmyslonymi danymi."""
    ROZMIAR = 73_000
    r = client.post("/api/admin/pool/generate-simulated", headers=ADMIN_H,
                    json={"account_size": ROZMIAR, "count": 3})
    assert r.status_code == 200 and len(r.json()["created"]) == 3

    lista = client.get("/api/admin/pool", headers=ADMIN_H).json()
    nasze = [p for p in lista["pool"] if p["account_size"] == ROZMIAR]
    assert len(nasze) == 3 and all(p["simulated"] for p in nasze)
    assert all(len(p["platform_login"]) == 9 and p["platform_login"].isdigit() for p in nasze)

    tid = _trader("sim-claim@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)
    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, None))
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.status == "active" and acc.mt5_backed is False
    assert acc.platform_server == "MetaQuotes-Demo"
    s.close()

    # limity generatora
    assert client.post("/api/admin/pool/generate-simulated", headers=ADMIN_H,
                       json={"account_size": ROZMIAR, "count": 51}).status_code == 400
    assert client.post("/api/admin/pool/generate-simulated", headers=ADMIN_H,
                       json={"account_size": 0, "count": 1}).status_code == 400


def test_sim_fallback_aktywuje_konto_gdy_pula_pusta():
    """Checkbox w panelu: pusta pula nie trzyma oplaconego konta w kolejce —
    provisioning sam generuje symulowane poswiadczenia."""
    ROZMIAR = 74_000   # zaden wpis w puli nie ma tego rozmiaru
    tid = _trader("sim-fallback@pool.pl")
    aid = _konto_czekajace(tid, ROZMIAR)

    # wylaczony (domyslnie) -> konto czeka
    with realny_provisioning():
        asyncio.run(provisioning.provision_pending(SessionLocal, None))
    s = SessionLocal(); assert s.get(Account, aid).status == "provisioning"; s.close()

    r = client.post("/api/admin/pool/sim-fallback", headers=ADMIN_H, json={"enabled": True})
    assert r.status_code == 200
    assert client.get("/api/admin/pool", headers=ADMIN_H).json()["sim_fallback"] is True
    try:
        with realny_provisioning():
            asyncio.run(provisioning.provision_pending(SessionLocal, None))
        s = SessionLocal()
        acc = s.get(Account, aid)
        assert acc.status == "active" and acc.mt5_backed is False
        assert acc.platform_login and acc.platform_password
        assert acc.platform_server == "MetaQuotes-Demo"
        s.close()
    finally:
        client.post("/api/admin/pool/sim-fallback", headers=ADMIN_H, json={"enabled": False})
