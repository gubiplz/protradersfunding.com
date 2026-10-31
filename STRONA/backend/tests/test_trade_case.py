"""Darmowa „Trade Case": jedna skrzynka na tradera, nagroda z puli z wagami.

Testy pilnują tego, na czym taka mechanika zwykle pada: żeby skrzynka dała się
otworzyć dokładnie raz (drugie otwarcie oddaje TEN SAM wynik), żeby kod z niej
działał przy checkoucie wyłącznie u właściciela, żeby wygrane konto powstało tą
samą ścieżką co grant z panelu, i żeby panel nie przyjął puli, której nie da
się wydać (plan spoza sprzedaży, zerowa waga).
"""
import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "pf_test_trade_case.db")
if not os.environ.get("TEST_DATABASE_URL") and os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + _DB
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, loyalty, notify, trade_case  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, AppSetting, Order, Product, RewardCase, RewardCode, Trader  # noqa: E402

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

client = TestClient(app)
LICZNIK = iter(range(10_000))


def _trader(verified=True, admin=False, punkty=0):
    s = SessionLocal()
    tr = Trader(email=f"case{next(LICZNIK)}-{auth.secrets.token_hex(3)}@test.pl",
                password_hash=auth.hash_password("haslo1234"), full_name="Case Opener",
                referral_code=auth.secrets.token_hex(4), email_verified=verified,
                is_admin=admin, bonus_points=punkty)
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


_, ADMIN = _trader(admin=True)


def _ustaw(cfg: dict | None) -> None:
    """None = brak wpisu w bazie, czyli pula domyślna."""
    s = SessionLocal()
    try:
        row = s.get(AppSetting, trade_case.KLUCZ)
        if row is not None:
            s.delete(row)
            s.commit()
        if cfg is not None:
            trade_case.zapisz(s, trade_case.waliduj(s, cfg))
    finally:
        s.close()
    trade_case._CACHE.update(ts=0.0, val=None)


@pytest.fixture(autouse=True)
def czysta_konfiguracja(monkeypatch):
    """Każdy test zaczyna od domyślnej puli i bez maili/pushy."""
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(notify, "send_now", lambda *a, **k: None)
    admini = []
    monkeypatch.setattr(notify, "notify_admins", lambda *a, **k: admini.append(a))
    _ustaw(None)
    yield admini
    _ustaw(None)


def _wymus(monkeypatch, klucz):
    """Losowanie zawsze trafia w nagrodę o podanym kluczu."""
    monkeypatch.setattr(trade_case, "losuj", lambda pula: next(p for p in pula if p["key"] == klucz))


# ---------------- konfiguracja i szanse ----------------
def test_domyslna_pula_i_szanse_sumuja_sie_do_100():
    _, h = _trader()
    d = client.get("/api/me/case", headers=h).json()
    assert d["enabled"] is True and d["available"] is True and d["reason"] is None
    assert d["opened"] is None and d["cards"] == 3
    etykiety = [o["label"] for o in d["odds"]]
    assert etykiety[0] == "10% off any challenge" and "Free 2-Step $100K challenge" in etykiety
    assert len(d["odds"]) == 7
    assert d["odds"][0]["pct"] == 55.0 and d["odds"][-1]["pct"] == 0.2
    assert abs(sum(o["pct"] for o in d["odds"]) - 100) < 0.5
    # Każda nagroda coś daje — w domyślnej puli nie ma pustego pola.
    cfg = trade_case.konfig(odswiez=True)
    assert all(p["type"] in ("code", "account") for p in cfg["prizes"])
    assert {p["key"] for p in cfg["prizes"]} >= {"off10", "off25", "acc-2step-25k", "acc-2step-100k"}


def test_szanse_wlasnej_puli_sumuja_sie_do_100():
    _ustaw({"on": True, "prizes": [
        {"type": "code", "pct": 10, "label": "A", "weight": 1},
        {"type": "code", "pct": 20, "label": "B", "weight": 1},
        {"type": "points", "points": 300, "label": "C", "weight": 1}]})
    _, h = _trader()
    odds = client.get("/api/me/case", headers=h).json()["odds"]
    assert [o["pct"] for o in odds] == [33.3, 33.3, 33.3]
    assert abs(sum(o["pct"] for o in odds) - 100) < 0.5


def test_losowanie_trzyma_sie_wag():
    """Rozkład z prawdziwego generatora: nagroda z wagą 0 nie istnieje w puli,
    a ta z przytłaczającą wagą wypada prawie zawsze."""
    pula = [{"key": "a", "weight": 99.0}, {"key": "b", "weight": 1.0}]
    wyniki = [trade_case.losuj(pula)["key"] for _ in range(2000)]
    assert wyniki.count("a") > 1900


# ---------------- otwieranie ----------------
def test_otwarcie_raz_drugie_oddaje_ten_sam_wynik(monkeypatch):
    _wymus(monkeypatch, "off15")
    tid, h = _trader()
    r = client.post("/api/me/case/open", headers=h, json={"card": 1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["already"] is False and d["type"] == "code" and d["pct"] == 15
    assert d["label"] == "15% off any challenge" and d["card"] == 1
    assert d["code"].startswith(loyalty.CODE_PREFIX) and d["code_status"] == "active"
    assert len(d["others"]) == 2 and d["label"] not in d["others"]
    # Inny wynik losowania przy drugim kliknięciu nie ma znaczenia: skrzynka już otwarta.
    _wymus(monkeypatch, "off25")
    d2 = client.post("/api/me/case/open", headers=h, json={"card": 2}).json()
    assert d2["already"] is True and d2["code"] == d["code"] and d2["pct"] == 15 and d2["card"] == 1
    stan = client.get("/api/me/case", headers=h).json()
    assert stan["available"] is False and stan["reason"] == "opened"
    assert stan["opened"]["code"] == d["code"]
    s = SessionLocal()
    try:
        assert s.query(RewardCase).filter(RewardCase.trader_id == tid).count() == 1
        assert s.query(RewardCode).filter(RewardCode.trader_id == tid).count() == 1
    finally:
        s.close()


def test_kod_ze_skrzynki_dziala_przy_checkoucie_tylko_u_wlasciciela(monkeypatch):
    _wymus(monkeypatch, "off20")
    tid, h = _trader()
    kod = client.post("/api/me/case/open", headers=h, json={"card": 0}).json()["code"]
    obcy, _ = _trader()
    s = SessionLocal()
    try:
        tr = s.get(Trader, tid)
        k = loyalty.find_usable(s, tr, kod)
        assert k is not None and k.pct == 20 and k.points_spent == 0
        wygasa = k.expires_at if k.expires_at.tzinfo else k.expires_at.replace(tzinfo=timezone.utc)
        za = wygasa - datetime.now(timezone.utc)
        assert timedelta(days=29) < za <= timedelta(days=30)
        assert loyalty.find_usable(s, s.get(Trader, obcy), kod) is None
        # Punkty lojalnościowe nie schodzą — kod jest nagrodą, nie zakupem.
        assert int(tr.points_spent or 0) == 0
    finally:
        s.close()
    # Kod widać też na liście kodów w programie lojalnościowym.
    assert kod in [c["code"] for c in client.get("/api/me/loyalty", headers=h).json()["codes"]]


def test_wygrane_konto_idzie_sciezka_grantu(monkeypatch, czysta_konfiguracja):
    _wymus(monkeypatch, "acc-2step-25k")
    tid, h = _trader()
    r = client.post("/api/me/case/open", headers=h, json={"card": 2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["type"] == "account" and d["product_key"] == "2step-25k" and d["account_id"]
    s = SessionLocal()
    try:
        acc = s.get(Account, d["account_id"])
        assert acc.trader_id == tid and acc.product_key == "2step-25k"
        assert acc.source == "grant" and acc.grant_note == trade_case.NOTA_GRANTU
        order = s.query(Order).filter(Order.account_id == acc.id).one()
        assert order.provider == "grant" and order.amount_usd == 0 and order.status == "paid"
        case = s.query(RewardCase).filter(RewardCase.trader_id == tid).one()
        assert case.account_id == acc.id
    finally:
        s.close()
    assert d["account_login"]
    assert czysta_konfiguracja and "Trade Case" in czysta_konfiguracja[-1][1]
    # Drugie otwarcie nie zakłada drugiego konta.
    assert client.post("/api/me/case/open", headers=h, json={}).json()["already"] is True
    s = SessionLocal()
    try:
        assert s.query(Account).filter(Account.trader_id == tid).count() == 1
    finally:
        s.close()


def test_nagroda_w_punktach_trafia_do_bonusu(monkeypatch):
    _ustaw({"on": True, "prizes": [{"type": "points", "points": 250, "label": "250 bonus points", "weight": 1}]})
    tid, h = _trader(punkty=40)
    d = client.post("/api/me/case/open", headers=h, json={"card": 0}).json()
    assert d["type"] == "points" and d["points"] == 250
    assert client.get("/api/me/loyalty", headers=h).json()["points_available"] == 290


def test_plan_wycofany_ze_sprzedazy_wypada_z_puli():
    s = SessionLocal()
    try:
        p = s.query(Product).filter(Product.key == "2step-100k").one()
        p.active = False
        s.commit()
    finally:
        s.close()
    try:
        _, h = _trader()
        etykiety = [o["label"] for o in client.get("/api/me/case", headers=h).json()["odds"]]
        assert "Free 2-Step $100K challenge" not in etykiety and len(etykiety) == 6
    finally:
        s = SessionLocal()
        try:
            s.query(Product).filter(Product.key == "2step-100k").update({Product.active: True})
            s.commit()
        finally:
            s.close()


# ---------------- kto może otworzyć ----------------
def test_niezweryfikowany_mail_nie_otwiera():
    _, h = _trader(verified=False)
    d = client.get("/api/me/case", headers=h).json()
    assert d["available"] is False and d["reason"] == "verify_email"
    assert client.post("/api/me/case/open", headers=h, json={"card": 0}).status_code == 403


def test_wylaczona_skrzynka(monkeypatch):
    _wymus(monkeypatch, "off10")
    otwarty, h_otw = _trader()
    assert client.post("/api/me/case/open", headers=h_otw, json={"card": 0}).status_code == 200
    _ustaw({"on": False, "prizes": trade_case.DOMYSLNE_NAGRODY})
    _, h = _trader()
    d = client.get("/api/me/case", headers=h).json()
    assert d["enabled"] is False and d["available"] is False and d["reason"] == "disabled"
    assert client.post("/api/me/case/open", headers=h, json={"card": 0}).status_code == 409
    # Kto już otworzył, dalej widzi swój wynik — wyłącznik nie zabiera nagrody.
    d = client.get("/api/me/case", headers=h_otw).json()
    assert d["opened"] and d["opened"]["pct"] == 10


def test_podglad_admina_nie_pali_skrzynki():
    """Admin „oczami klienta" widzi skrzynkę, ale jej nie otworzy za klienta."""
    tid, h = _trader()
    imp = {"Authorization": f"Bearer {auth.make_impersonation_token(tid)}"}
    assert client.get("/api/me/case", headers=imp).json()["available"] is True
    assert client.post("/api/me/case/open", headers=imp, json={"card": 0}).status_code == 403
    assert client.get("/api/me/case", headers=h).json()["available"] is True


def test_zla_karta_i_brak_logowania():
    _, h = _trader()
    assert client.post("/api/me/case/open", headers=h, json={"card": 3}).status_code == 422
    assert client.get("/api/me/case").status_code == 401


# ---------------- panel ----------------
def test_panel_waliduje_pule():
    dobra = {"type": "code", "pct": 10, "label": "10% off", "weight": 5}
    zle = [
        [{"type": "account", "product_key": "nie-ma-takiego", "label": "Free X", "weight": 1}],
        [{**dobra, "weight": 0}],
        [{**dobra, "weight": -2}],
        [{**dobra, "pct": 95}],
        [{**dobra, "pct": 0}],
        [{**dobra, "label": ""}],
        [{**dobra, "label": "x" * 61}],
        [{**dobra, "type": "cash"}],
        [{"type": "points", "points": 0, "label": "Zero", "weight": 1}],
        [],
        [dobra] * 13,
    ]
    for prizes in zle:
        r = client.post("/api/admin/trade-case", headers=ADMIN, json={"on": True, "prizes": prizes})
        assert r.status_code == 400, (prizes[:1], r.text)
    # Plan wycofany ze sprzedaży też nie przejdzie.
    s = SessionLocal()
    try:
        s.query(Product).filter(Product.key == "instant-25k").update({Product.active: False})
        s.commit()
        r = client.post("/api/admin/trade-case", headers=ADMIN, json={"on": True, "prizes": [
            {"type": "account", "product_key": "instant-25k", "label": "Free Instant", "weight": 1}]})
        assert r.status_code == 400
        s.query(Product).filter(Product.key == "instant-25k").update({Product.active: True})
        s.commit()
    finally:
        s.close()
    assert client.post("/api/admin/trade-case", json={"on": True, "prizes": [dobra]}).status_code == 403


def test_panel_zapisuje_i_pokazuje_statystyki(monkeypatch):
    r = client.post("/api/admin/trade-case", headers=ADMIN, json={"on": True, "prizes": [
        {"type": "code", "pct": 12.5, "label": "  12.5% off   any challenge ", "weight": 3},
        {"type": "code", "pct": 12.5, "label": "Same again", "weight": 1},
        {"type": "account", "product_key": "2step-50k", "label": "Free 2-Step $50K challenge", "weight": 1},
        {"type": "points", "points": 500, "label": "500 points", "weight": 0.5}]})
    assert r.status_code == 200, r.text
    d = r.json()
    nagrody = d["config"]["prizes"]
    assert [p["key"] for p in nagrody] == ["off12-5", "off12-5-2", "acc-2step-50k", "pts500"]
    assert nagrody[0]["label"] == "12.5% off any challenge"
    assert abs(sum(o["pct"] for o in d["odds"]) - 100) < 0.5
    assert any(p["key"] == "2step-50k" for p in d["products"])
    # Trader od razu widzi nową pulę (zapis zeruje cache).
    _, h = _trader()
    assert len(client.get("/api/me/case", headers=h).json()["odds"]) == 4
    _wymus(monkeypatch, "pts500")
    przed = client.get("/api/admin/trade-case", headers=ADMIN).json()["stats"]["opened"]
    tid, h = _trader()
    assert client.post("/api/me/case/open", headers=h, json={"card": 1}).status_code == 200
    st = client.get("/api/admin/trade-case", headers=ADMIN).json()["stats"]
    assert st["opened"] == przed + 1
    assert any(x["key"] == "pts500" and x["count"] >= 1 for x in st["per_prize"])
    s = SessionLocal()
    try:
        email = s.get(Trader, tid).email
    finally:
        s.close()
    assert st["recent"][0]["email"] == email and st["recent"][0]["prize"] == "500 points"
    assert client.get("/api/admin/trade-case").status_code == 403


def test_usuniecie_klienta_z_otwarta_skrzynka(monkeypatch):
    """Hard delete z panelu nie może się wywrócić na kluczu obcym reward_cases."""
    _wymus(monkeypatch, "off10")
    tid, h = _trader()
    assert client.post("/api/me/case/open", headers=h, json={"card": 0}).status_code == 200
    r = client.delete(f"/api/admin/traders/{tid}", headers=ADMIN)
    assert r.status_code == 200, r.text
    s = SessionLocal()
    try:
        assert s.query(RewardCase).filter(RewardCase.trader_id == tid).count() == 0
    finally:
        s.close()
