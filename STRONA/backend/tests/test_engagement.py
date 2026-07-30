"""Engagement: dzienny check-in (streak + freeze + milestone'y) i mystery reveal.

Jak w pozostałych plikach: pytest współdzieli moduły/bazę — unikalne e-maile,
asercje odporne na cudze wiersze.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()


def _trader(email):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Engage Ment", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _ustaw(tid, **pola):
    s = SessionLocal()
    tr = s.get(Trader, tid)
    for k, v in pola.items():
        setattr(tr, k, v)
    s.commit(); s.close()


def _pole(tid, nazwa):
    s = SessionLocal()
    v = getattr(s.get(Trader, tid), nazwa)
    s.close()
    return v


def _dni_temu(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _lucky_payload(code, pct, godzin=24):
    return json.dumps({"type": "coupon", "code": code, "pct": pct,
                       "expires_at": (datetime.now(timezone.utc)
                                      + timedelta(hours=godzin)).isoformat()})


# ---------------- check-in ----------------
def test_checkin_pierwszy_raz_i_powtorka_tego_samego_dnia():
    _, h = _trader("checkin@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
        assert r == {"streak": 1, "already": False, "reward": None,
                     "freeze_used": False, "freezes": 1}
        r2 = c.post("/api/me/checkin", headers=h).json()
    assert r2 == {"streak": 1, "already": True, "reward": None,
                  "freeze_used": False, "freezes": 1}


def test_checkin_kolejny_dzien_podbija_serie():
    tid, h = _trader("checkin-serie@test.pl")
    _ustaw(tid, checkin_streak=1, checkin_last=_dni_temu(1))
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
    assert r["streak"] == 2 and r["already"] is False and r["freeze_used"] is False
    assert _pole(tid, "checkin_last") == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_checkin_freeze_ratuje_serie_po_1_dniu_przerwy():
    tid, h = _trader("checkin-freeze@test.pl")
    _ustaw(tid, checkin_streak=5, checkin_last=_dni_temu(2), streak_freezes=1)
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
    assert r["streak"] == 6 and r["freeze_used"] is True and r["freezes"] == 0
    assert _pole(tid, "streak_freezes") == 0


def test_checkin_bez_freeza_1_dzien_przerwy_resetuje():
    tid, h = _trader("checkin-bez-freeza@test.pl")
    _ustaw(tid, checkin_streak=5, checkin_last=_dni_temu(2), streak_freezes=0)
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
    assert r["streak"] == 1 and r["freeze_used"] is False and r["freezes"] == 0


def test_checkin_dluzsza_przerwa_resetuje_mimo_freeza():
    tid, h = _trader("checkin-przerwa@test.pl")
    _ustaw(tid, checkin_streak=9, checkin_last=_dni_temu(3), streak_freezes=2)
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
    # freeze pokrywa dokladnie 1 opuszczony dzien — dluzsza przerwa = reset,
    # a zapas freezow zostaje nietkniety
    assert r["streak"] == 1 and r["reward"] is None and r["freeze_used"] is False
    assert _pole(tid, "streak_freezes") == 2


def test_checkin_milestone_3_daje_25_punktow():
    tid, h = _trader("checkin-bonus@test.pl")
    _ustaw(tid, checkin_streak=2, checkin_last=_dni_temu(1))
    with TestClient(app) as c:
        r = c.post("/api/me/checkin", headers=h).json()
    assert r["streak"] == 3 and r["reward"] == {"type": "points", "amount": 25}
    assert _pole(tid, "bonus_points") == 25


def test_checkin_wymaga_logowania():
    with TestClient(app) as c:
        assert c.post("/api/me/checkin").status_code == 401


# ---------------- daily reveal ----------------
def test_reveal_pierwsze_losowanie_i_cache_tego_samego_dnia():
    _, h = _trader("reveal@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/daily-reveal", headers=h).json()
        assert r.pop("already") is False
        if r["type"] == "tip":
            assert 0 <= r["index"] <= 23
        elif r["type"] == "points":
            assert 5 <= r["amount"] <= 25
        elif r["type"] == "freeze":
            pass
        else:
            assert r["type"] == "coupon" and (r["code"], r["pct"]) in {("LUCKY10", 10), ("LUCKY15", 15)}
            assert "expires_at" in r
        # drugi strzal tego samego dnia oddaje IDENTYCZNY wynik z bazy
        r2 = c.post("/api/me/daily-reveal", headers=h).json()
    assert r2.pop("already") is True
    assert r2 == r


def test_reveal_punkty_zwiekszaja_bonus_points(monkeypatch):
    tid, h = _trader("reveal-punkty@test.pl")
    _ustaw(tid, bonus_points=10)
    monkeypatch.setattr(main_mod.random, "random", lambda: 0.75)       # widelka 62-82% -> punkty
    monkeypatch.setattr(main_mod.random, "randint", lambda a, b: 17)
    with TestClient(app) as c:
        r = c.post("/api/me/daily-reveal", headers=h).json()
    assert r == {"type": "points", "amount": 17, "already": False}
    assert _pole(tid, "bonus_points") == 27


def test_reveal_freeze_dodaje_zapas(monkeypatch):
    tid, h = _trader("reveal-freeze@test.pl")
    monkeypatch.setattr(main_mod.random, "random", lambda: 0.85)       # widelka 82-90% -> freeze
    with TestClient(app) as c:
        r = c.post("/api/me/daily-reveal", headers=h).json()
    assert r == {"type": "freeze", "already": False}
    assert _pole(tid, "streak_freezes") == 2                           # 1 startowy + 1 z reveala


def test_reveal_freeze_przy_pelnym_zapasie_zamienia_sie_w_punkty(monkeypatch):
    tid, h = _trader("reveal-freeze-cap@test.pl")
    _ustaw(tid, streak_freezes=3)
    monkeypatch.setattr(main_mod.random, "random", lambda: 0.85)
    monkeypatch.setattr(main_mod.random, "randint", lambda a, b: 12)
    with TestClient(app) as c:
        r = c.post("/api/me/daily-reveal", headers=h).json()
    assert r == {"type": "points", "amount": 12, "already": False}
    assert _pole(tid, "streak_freezes") == 3
    assert _pole(tid, "bonus_points") == 12


def test_reveal_kupon_lucky15_przy_najrzadszym_losie(monkeypatch):
    tid, h = _trader("reveal-kupon@test.pl")
    monkeypatch.setattr(main_mod.random, "random", lambda: 0.99)
    with TestClient(app) as c:
        r = c.post("/api/me/daily-reveal", headers=h).json()
        expires = r.pop("expires_at")
        assert r == {"type": "coupon", "code": "LUCKY15", "pct": 15, "already": False}
        # waznosc ~48h od losowania
        delta = datetime.fromisoformat(expires) - datetime.now(timezone.utc)
        assert timedelta(hours=47) < delta <= timedelta(hours=48)
        # endpoint informacyjny dalej zna kod (egzekwowanie jest w checkoucie)
        assert c.get("/api/coupon/LUCKY15").json()["pct"] == 15.0
        assert c.get("/api/coupon/LUCKY10").json()["pct"] == 10.0
    assert _pole(tid, "bonus_points") in (0, None)   # kupon nie dosypuje punktow


# ---------------- kupony LUCKY sa osobiste ----------------
def test_checkout_lucky_bez_wygranego_losowania_odrzucony():
    _, h = _trader("lucky-obcy@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/checkout", headers=h,
                   json={"product_key": "2step-10k", "coupon": "LUCKY10"})
    assert r.status_code == 400
    assert "personal" in r.json()["detail"]


def test_checkout_lucky_dziala_u_zwyciezcy_w_48h():
    tid, h = _trader("lucky-zwyciezca@test.pl")
    _ustaw(tid, reveal_payload=_lucky_payload("LUCKY10", 10, godzin=24))
    with TestClient(app) as c:
        r = c.post("/api/checkout", headers=h,
                   json={"product_key": "2step-10k", "coupon": "LUCKY10"})
    assert r.status_code == 200
    d = r.json()
    assert d["discount_pct"] == 10.0 and d["amount"] == 89.1     # 99 - 10%


def test_checkout_lucky_wygasly_odrzucony():
    tid, h = _trader("lucky-wygasly@test.pl")
    _ustaw(tid, reveal_payload=_lucky_payload("LUCKY15", 15, godzin=-1))
    with TestClient(app) as c:
        r = c.post("/api/checkout", headers=h,
                   json={"product_key": "2step-10k", "coupon": "LUCKY15"})
    assert r.status_code == 400


def test_checkout_zwykly_kupon_nie_wymaga_losowania():
    _, h = _trader("kupon-zwykly@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/checkout", headers=h,
                   json={"product_key": "2step-10k", "coupon": "WELCOME10"})
    assert r.status_code == 200 and r.json()["discount_pct"] == 10.0


# ---------------- profil ----------------
def test_profil_oddaje_pola_engagement():
    tid, h = _trader("profil-engagement@test.pl")
    _ustaw(tid, bonus_points=40, checkin_streak=5, streak_freezes=2,
           checkin_last="2026-07-28", reveal_last="2026-07-27")
    with TestClient(app) as c:
        d = c.get("/api/auth/me", headers=h).json()
    assert d["bonus_points"] == 40 and d["checkin_streak"] == 5
    assert d["checkin_last"] == "2026-07-28" and d["reveal_last"] == "2026-07-27"
    assert d["streak_freezes"] == 2
