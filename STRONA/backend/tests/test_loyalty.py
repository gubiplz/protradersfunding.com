"""Program lojalnościowy: wymiana punktów na własny kod jednorazowy.

Punkty były do tej pory licznikiem bez wyjścia — rosły i nic z nich nie
wynikało, a „nagrodą" w portalu było pokazanie kodu GLOBALNEGO. Tutaj stają się
walutą, więc testy pilnują trzech rzeczy, na których taka mechanika zwykle
pada: żeby nie dało się wydać tych samych punktów dwa razy, żeby kod działał
WYŁĄCZNIE u swojego właściciela i żeby zadziałał dokładnie raz.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, catalog, loyalty  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Order, RewardCode, Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

client = TestClient(app)
LICZNIK = iter(range(1000))


def _trader(punkty=0):
    s = SessionLocal()
    tr = Trader(email=f"loj{next(LICZNIK)}@test.pl", password_hash=auth.hash_password("haslo1234"),
                full_name="Loyal Ty", referral_code=auth.secrets.token_hex(3),
                bonus_points=punkty)
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _zamowienie(tid, kwota, status="paid", provider="mock"):
    s = SessionLocal()
    s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=kwota,
                status=status, provider=provider))
    s.commit(); s.close()


# ---------------- punkty ----------------
def test_punkty_licza_sie_z_oplaconych_zamowien_i_bonusow():
    """1 punkt za $1 — ale tylko z zamówień FAKTYCZNIE opłaconych."""
    tid, h = _trader(punkty=40)
    _zamowienie(tid, 299)
    _zamowienie(tid, 500, status="pending")          # nieopłacone nie liczą się
    _zamowienie(tid, 999, provider="grant")          # grant to prezent, nie zakup
    d = client.get("/api/me/loyalty", headers=h).json()
    assert d["points_lifetime"] == 339, "299 z zakupu + 40 bonusu"
    assert d["points_available"] == 339 and d["points_spent"] == 0


def test_wymiana_zdejmuje_punkty_i_wydaje_kod():
    tid, h = _trader(punkty=1200)
    r = client.post("/api/me/loyalty/redeem", headers=h, json={"reward": "off15"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["code"]["pct"] == 15.0 and d["code"]["status"] == "active"
    assert d["code"]["code"].startswith("PF-")
    assert d["points_spent"] == 500
    assert d["points_available"] == 700, "saldo maleje o koszt nagrody"
    assert d["points_lifetime"] == 1200, "dozywotnie punkty zostaja nietkniete"
    # kod widac na liscie tradera
    lista = client.get("/api/me/loyalty", headers=h).json()["codes"]
    assert [c["code"] for c in lista] == [d["code"]["code"]]


def test_tier_nie_spada_po_wymianie_punktow():
    """Nikt nie traci statusu za to, ze skorzystal z nagrody.

    Wymiana 2000 punktow zbija saldo praktycznie do zera, ale tier liczy sie z
    sumy DOZYWOTNIEJ — gdyby szedl z salda, trader spadlby z Golda na Bronze.
    """
    tid, h = _trader(punkty=2100)
    assert client.get("/api/me/loyalty", headers=h).json()["tier"] == "Gold"
    assert client.post("/api/me/loyalty/redeem", headers=h,
                       json={"reward": "off35"}).status_code == 200
    d = client.get("/api/me/loyalty", headers=h).json()
    assert d["points_available"] == 100 and d["points_spent"] == 2000
    assert d["tier"] == "Gold" and d["points_lifetime"] == 2100


def test_za_malo_punktow_to_odmowa_a_nie_ujemne_saldo():
    tid, h = _trader(punkty=100)
    r = client.post("/api/me/loyalty/redeem", headers=h, json={"reward": "off15"})
    assert r.status_code == 400 and "400 more points" in r.json()["detail"]
    d = client.get("/api/me/loyalty", headers=h).json()
    assert d["points_spent"] == 0 and d["points_available"] == 100
    assert d["codes"] == []


def test_nie_da_sie_wydac_tych_samych_punktow_dwa_razy():
    """Za 600 punktow jest JEDEN kod za 500, nie dwa."""
    tid, h = _trader(punkty=600)
    assert client.post("/api/me/loyalty/redeem", headers=h,
                       json={"reward": "off15"}).status_code == 200
    druga = client.post("/api/me/loyalty/redeem", headers=h, json={"reward": "off15"})
    assert druga.status_code == 400, "drugi kod poszedlby za punkty, ktorych juz nie ma"
    s = SessionLocal()
    assert s.query(RewardCode).filter(RewardCode.trader_id == tid).count() == 1
    s.close()


def test_nieznana_nagroda_odrzucona():
    tid, h = _trader(punkty=5000)
    assert client.post("/api/me/loyalty/redeem", headers=h,
                       json={"reward": "off99"}).status_code == 404


def test_loyalty_wymaga_logowania():
    assert client.get("/api/me/loyalty").status_code in (401, 403)
    assert client.post("/api/me/loyalty/redeem", json={"reward": "off15"}).status_code in (401, 403)


# ---------------- kod w checkoucie ----------------
def _kod(tid, **pola):
    """Kod nagrody wstawiony wprost, zeby nie mielic punktow w kazdym tescie."""
    s = SessionLocal()
    k = RewardCode(trader_id=tid, code=loyalty.generate_code(s), pct=10.0, points_spent=500,
                   expires_at=datetime.now(timezone.utc) + timedelta(days=90))
    for p, v in pola.items():
        setattr(k, p, v)
    s.add(k); s.commit()
    kod = k.code
    s.close()
    return kod


def test_kod_za_punkty_realnie_obniza_cene_w_checkoucie():
    """Bez rozwiazania kodu w billing dostalby 0% po cichu — trader oddalby
    punkty i zaplacil pelna cene."""
    tid, _ = _trader()
    kod = _kod(tid)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    res = billing.create_checkout(s, tr, "2step-25k", kod)
    assert res["discount_pct"] == 10.0
    assert res["amount"] == 269.1, "299 minus 10%"
    s.close()


def test_kod_nie_dziala_u_obcego_tradera():
    wlasciciel, _ = _trader()
    kod = _kod(wlasciciel)
    obcy, _ = _trader()
    s = SessionLocal()
    tr = s.get(Trader, obcy)
    try:
        billing.create_checkout(s, tr, "2step-25k", kod)
        assert False, "cudzy kod nie ma prawa przejsc"
    except Exception as e:
        assert "another account" in str(e)
    s.close()


def test_kod_dziala_dokladnie_raz():
    tid, h = _trader()
    kod = _kod(tid)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    zam = billing.create_checkout(s, tr, "2step-25k", kod)
    billing.mock_complete(s, zam["order_id"], tid)
    s.close()

    s = SessionLocal()
    k = s.query(RewardCode).filter(RewardCode.code == kod).one()
    assert k.used_at is not None and k.order_id is not None, "kod schodzi przy oplaceniu"
    tr = s.get(Trader, tid)
    try:
        billing.create_checkout(s, tr, "2step-25k", kod)
        assert False, "zuzyty kod nie ma prawa przejsc drugi raz"
    except Exception as e:
        assert "already been used" in str(e)
    s.close()
    assert client.get("/api/me/loyalty", headers=h).json()["codes"][0]["status"] == "used"


def test_porzucony_checkout_nie_pali_kodu():
    """Kod schodzi przy DOMKNIECIU platnosci, nie przy wejsciu do kasy."""
    tid, _ = _trader()
    kod = _kod(tid)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    billing.create_checkout(s, tr, "2step-25k", kod)      # zamowienie zostaje pending
    s.close()
    s = SessionLocal()
    assert s.query(RewardCode).filter(RewardCode.code == kod).one().used_at is None
    s.close()


def test_wygasly_kod_odrzucony():
    tid, _ = _trader()
    kod = _kod(tid, expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    s = SessionLocal()
    tr = s.get(Trader, tid)
    try:
        billing.create_checkout(s, tr, "2step-25k", kod)
        assert False, "wygasly kod nie ma prawa przejsc"
    except Exception as e:
        assert "expired" in str(e)
    s.close()


def test_wymyslony_kod_z_prefiksem_nie_daje_cichego_zera():
    """Kod, ktorego nie ma, musi ODMOWIC, a nie policzyc pelna cene bez slowa."""
    tid, _ = _trader()
    s = SessionLocal()
    tr = s.get(Trader, tid)
    try:
        billing.create_checkout(s, tr, "2step-25k", "PF-XXXX-XXXX")
        assert False, "nieistniejacy kod nagrody nie moze przejsc po cichu"
    except Exception as e:
        assert "another account" in str(e) or "personal" in str(e)
    s.close()


def test_podglad_kuponu_zna_kod_za_punkty():
    """Bez tego wlasny, swiezo wymieniony kod pokazywalby sie jako nieprawidlowy."""
    tid, _ = _trader()
    kod = _kod(tid)
    r = client.get(f"/api/coupon/{kod}")
    assert r.status_code == 200 and r.json()["pct"] == 10.0
    # ale nie zdradza, czyj jest ani jaki ma status
    assert set(r.json()) == {"code", "pct"}
    assert client.get("/api/coupon/PF-NIE-MA").status_code == 404


def test_generowany_kod_jest_czytelny_i_unikalny():
    s = SessionLocal()
    kody = {loyalty.generate_code(s) for _ in range(50)}
    s.close()
    assert len(kody) == 50
    for k in kody:
        assert k.startswith("PF-") and len(k) == 12
        # bez znakow, ktore myla sie przy przepisywaniu
        assert not (set("OIL01") & set(k[3:]))
