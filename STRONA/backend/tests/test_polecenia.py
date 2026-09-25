"""Program poleceń partnera: lead z `ref` → „pending", zakup → „confirmed".

Dotąd partner dopisywał poleconych ręcznie, a „confirmed" ktoś przestawiał
w bazie z ręki. Teraz robi to backend, bo tylko on zna oba fakty: `ref` przychodzi
z landingu razem z leadem, wypłatę zatwierdza admin.
"""
import json
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, notify, polecenia, telegram  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Lead, Order, Trader  # noqa: E402

init_db()
_s = SessionLocal(); catalog.seed_products(_s); _s.close()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
TOKEN_LANDINGU = "sekret-landingu-polecenia"
ADRES = "https://baza-partnera.example/rest/v1/rpc/sync_referral"
KLUCZ = "klucz-testowy"
LICZNIK = iter(range(10_000))
NUMERY = iter(range(500_000, 600_000))
PARTNER = {"name": "Anna Partner", "email": "anna@partner.pl"}
KTO = "Anna Partner · anna@partner.pl (anna-partner)"


@pytest.fixture(autouse=True)
def wyslane(monkeypatch):
    """Transport podmieniony na listę; sieć nie jest dotykana."""
    u = get_settings()
    monkeypatch.setattr(u, "lead_ingest_token", TOKEN_LANDINGU, raising=False)
    monkeypatch.setattr(u, "referral_sync_url", ADRES, raising=False)
    monkeypatch.setattr(u, "referral_sync_key", KLUCZ, raising=False)
    lista: list[tuple[str, dict, str]] = []

    def transport(url, body, key, timeout=6):
        if url.endswith("/referral_partner"):
            # Do właściciela sluga — osobna funkcja pod tym samym /rpc/.
            assert url == ADRES.replace("sync_referral", "referral_partner")
            return 200, json.dumps(PARTNER if body["p_slug"] == "anna-partner" else None).encode()
        lista.append((url, body, key))
        return 200, json.dumps("confirmed" if body["p_paid"] else "pending").encode()

    monkeypatch.setattr(polecenia, "_post", transport)
    return lista


@pytest.fixture(autouse=True)
def kanal(monkeypatch):
    """Karta, wiadomości na czat leadów i pushe — zamiast Telegrama i web pusha."""
    zlapane: dict[str, list] = {"karta": [], "wiadomosc": [], "push": []}
    monkeypatch.setattr(telegram, "send_lead_alert",
                        lambda lead_id, tekst, **kw: (zlapane["karta"].append(tekst),
                                                      (True, "", next(NUMERY)))[1])
    monkeypatch.setattr(telegram, "send_lead_message",
                        lambda tekst, **kw: (zlapane["wiadomosc"].append(tekst), (True, ""))[1])
    monkeypatch.setattr(notify, "notify_admins",
                        lambda event, title, body="", url="/admin", tag=None:
                        zlapane["push"].append((title, body)))
    return zlapane


def _mail():
    return f"polecony{next(LICZNIK)}@test.pl"


def _zgloszenie(email, **nadpisz):
    dane = {"email": email, "name": "Anna Polecona", "source": "questionnaire",
            "ref": "anna-partner", "accountSize": "$50K", "outcome": "qualified",
            "quality": {"tier": "high", "score": 8}}
    dane.update(nadpisz)
    return client.post("/api/leads/ingest", json=dane, headers={"X-Lead-Token": TOKEN_LANDINGU})


def _ref(email):
    s = SessionLocal()
    try:
        return s.query(Lead).filter(Lead.email == email).one().ref
    finally:
        s.close()


def _funded(email, plan=None):
    """Funded trader o tym mailu; z `plan` także opłacone zamówienie na ten plan."""
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Polecony", referral_code=auth.secrets.token_hex(3),
                kyc_status="approved")
    s.add(tr); s.commit(); tid = tr.id
    if plan:
        s.add(Order(trader_id=tid, product_key=plan, amount_usd=549.0, status="paid"))
        s.commit()
    kapital = 100_000.0
    acc = Account(login=f"8{tid:08d}"[:9], trader_id=tid, trader_name="Polecony",
                  product_key="2step-50k", initial_balance=kapital, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  min_trading_days=5, trading_days_count=5,
                  balance=kapital + 1_000, equity=kapital + 1_000,
                  peak_equity=kapital + 1_000, day_start_equity=kapital + 1_000,
                  day_start_balance=kapital + 1_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def test_lead_z_linku_partnera_trafia_na_jego_liste_jako_pending(wyslane):
    email = _mail()
    assert _zgloszenie(email.upper()).status_code == 200
    assert wyslane == [(ADRES, {"p_slug": "anna-partner", "p_email": email,
                                "p_account_size": "$50K", "p_paid": False}, KLUCZ)]


def test_drugie_zgloszenie_bez_linku_nie_kasuje_partnera(wyslane):
    email = _mail()
    _zgloszenie(email)
    _zgloszenie(email, ref="")
    assert _ref(email) == "anna-partner"


def test_pierwszy_partner_zostaje(wyslane):
    email = _mail()
    _zgloszenie(email)
    _zgloszenie(email, ref="inny-partner")
    assert _ref(email) == "anna-partner"
    assert {b["p_slug"] for _, b, _ in wyslane} == {"anna-partner"}


def test_smieci_zamiast_sluga_nie_wchodza_ani_do_bazy_ani_dalej(wyslane):
    email = _mail()
    assert _zgloszenie(email, ref="<script>" + "x" * 80).status_code == 200
    assert _ref(email) is None
    assert wyslane == []


def test_lead_bez_ref_nic_nie_wysyla(wyslane):
    _zgloszenie(_mail(), ref=None)
    assert wyslane == []


def test_bez_konfiguracji_nic_nie_wychodzi(wyslane, monkeypatch):
    monkeypatch.setattr(get_settings(), "referral_sync_url", "", raising=False)
    assert _zgloszenie(_mail()).status_code == 200
    assert wyslane == []


def test_padnieta_baza_partnera_nie_psuje_przyjecia_leada(monkeypatch):
    def pada(url, body, key):
        raise OSError("connection refused")
    monkeypatch.setattr(polecenia, "_post", pada)
    r = _zgloszenie(_mail())
    assert r.status_code == 200 and r.json()["ok"] is True


def test_zatwierdzona_wyplata_potwierdza_polecenie(wyslane):
    email = _mail()
    _zgloszenie(email)
    wyslane.clear()
    aid, h = _funded(email)
    r = client.post(f"/api/accounts/{aid}/payout-request", headers=h,
                    json={"method": "wise", "amount": 200, "details": {"email": "x@test.pl"}})
    assert r.status_code == 200, r.text
    assert wyslane == []  # sam wniosek to jeszcze nie pieniądze
    assert client.post(f"/api/admin/payout-requests/{r.json()['id']}/approve",
                       headers=ADMIN).status_code == 200
    assert wyslane == [(ADRES, {"p_slug": "anna-partner", "p_email": email,
                                "p_account_size": None, "p_paid": True}, KLUCZ)]


def test_wyplata_wystawiona_przez_admina_tez_potwierdza(wyslane):
    email = _mail()
    _zgloszenie(email)
    wyslane.clear()
    aid, _ = _funded(email)
    r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                    json={"amount": 300, "method": "bank", "reset_balance": False})
    assert r.status_code == 200, r.text
    assert [b["p_paid"] for _, b, _ in wyslane] == [True]


def test_wyplata_tradera_bez_polecenia_nic_nie_wysyla(wyslane):
    aid, _ = _funded(_mail())  # trader bez leada — np. zapisał się sam
    r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                    json={"amount": 300, "method": "bank", "reset_balance": False})
    assert r.status_code == 200, r.text
    assert wyslane == []


def _cron():
    r = client.post("/api/cron/lead-followups", headers=ADMIN)
    assert r.status_code == 200, r.text


def _o(zlapane, fraza):
    return [t for t in zlapane if fraza in t]


def test_karta_i_push_mowia_czyj_to_ref(kanal):
    email = _mail()
    _zgloszenie(email)
    karta = kanal["karta"][-1]
    assert f"🤝 <b>Polecenie</b> — partner: {KTO}" in karta
    # Slug nie dubluje się już w „Source" — ma własną linijkę.
    assert "<b>Source:</b> questionnaire\n" in karta + "\n"
    tytul, tresc = kanal["push"][-1]
    assert tytul.startswith("New lead") and tresc.endswith("🤝 ref: Anna Partner")
    s = SessionLocal()
    try:
        assert s.query(Lead).filter(Lead.email == email).one().ref_partner == \
            "Anna Partner · anna@partner.pl"
    finally:
        s.close()


def test_lead_bez_ref_nie_ma_linijki_polecenia(kanal):
    _zgloszenie(_mail(), ref=None)
    assert "Polecenie" not in kanal["karta"][-1]
    assert "ref:" not in kanal["push"][-1][1]


def test_zakup_2step_potwierdza_i_konto_nalezy_sie_od_razu(wyslane, kanal):
    email = _mail()
    _zgloszenie(email)
    wyslane.clear()
    _funded(email, plan="2step-100k")
    _cron()
    assert wyslane == [(ADRES, {"p_slug": "anna-partner", "p_email": email,
                                "p_account_size": "2-Step 100K", "p_paid": True}, KLUCZ)]
    [tekst] = _o(kanal["wiadomosc"], email)
    assert "Polecony kupił konto" in tekst and KTO in tekst
    assert "należy się teraz <b>2-Step 100K</b>" in tekst
    assert any("Referral bought" in t and "2-Step 100K now" in b for t, b in kanal["push"])
    # Drugi przebieg nie powtarza ani alarmu, ani potwierdzenia.
    wyslane.clear()
    _cron()
    assert wyslane == [] and len(_o(kanal["wiadomosc"], email)) == 1


def test_instant_konto_dla_partnera_dopiero_po_pierwszej_wyplacie(wyslane, kanal):
    email = _mail()
    _zgloszenie(email)
    aid, _ = _funded(email, plan="instant-100k")
    _cron()
    [tekst] = _o(kanal["wiadomosc"], email)
    assert "<b>Instant 100K</b>, ale dopiero po PIERWSZEJ" in tekst
    for _ in range(2):
        r = client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                        json={"amount": 300, "method": "bank", "reset_balance": False})
        assert r.status_code == 200, r.text
    wyplata = [t for t in _o(kanal["wiadomosc"], email) if "Pierwsza wypłata" in t]
    assert len(wyplata) == 1  # druga wypłata nie wraca z tym samym
    assert "Teraz wydaj partnerowi <b>Instant 100K</b>" in wyplata[0] and KTO in wyplata[0]


def test_2step_wyplata_nie_wola_o_drugie_konto(kanal):
    email = _mail()
    _zgloszenie(email)
    aid, _ = _funded(email, plan="2step-100k")
    _cron()
    client.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN,
                json={"amount": 300, "method": "bank", "reset_balance": False})
    assert not [t for t in _o(kanal["wiadomosc"], email) if "Pierwsza wypłata" in t]


def test_slug_bez_wlasciciela_bez_alarmu_o_koncie(wyslane, kanal):
    email = _mail()
    _zgloszenie(email, ref="nikt-taki")
    assert "partner: nikt-taki" in kanal["karta"][-1]
    wyslane.clear()
    _funded(email, plan="2step-100k")
    _cron()
    assert not [t for t in _o(kanal["wiadomosc"], email) if "Polecony kupił" in t]
    assert wyslane == []
    # Zwykłe „KUPIŁ" idzie dalej i też mówi, z jakiego linku przyszedł.
    assert [t for t in _o(kanal["wiadomosc"], email) if "KUPIŁ" in t and "partner: nikt-taki" in t]


def test_baza_partnera_padla_przy_zgloszeniu_cron_dopyta(wyslane, kanal, monkeypatch):
    email = _mail()
    dziala = polecenia._post

    def bez_wlasciciela(url, body, key, timeout=6):
        if url.endswith("/referral_partner"):
            raise OSError("timeout")
        return dziala(url, body, key, timeout)
    monkeypatch.setattr(polecenia, "_post", bez_wlasciciela)
    _zgloszenie(email)
    assert "partner: anna-partner" in kanal["karta"][-1]  # sam slug, lead wszedł
    _funded(email, plan="2step-100k")
    _cron()
    kupil = lambda: [t for t in _o(kanal["wiadomosc"], email) if "Polecony kupił" in t]  # noqa: E731
    assert kupil() == []  # nie wiadomo czyj — bez wpisu, spróbuje znowu
    monkeypatch.setattr(polecenia, "_post", dziala)
    _cron()
    [tekst] = kupil()
    assert KTO in tekst
