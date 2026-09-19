"""Kolejka treści na kanały: walidacja twierdzeń, publikacja, harmonogram.

Sedno jest jedno: post ma nie wyjść, jeśli jego liczby nie mają pokrycia
w danych — i ma to sprawdzić DWA razy, bo między zatwierdzeniem a publikacją
mija zaplanowany czas.
"""
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import contentbot  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, ChannelPost, Payout  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}


@pytest.fixture(autouse=True)
def _kanal(monkeypatch):
    """Kanał account management skonfigurowany — inaczej walidator odmawia na wstępie."""
    monkeypatch.setattr(get_settings(), "telegram_mgmt_chat_id", "@fx_passing",
                        raising=False)


def _post(**nadpisz) -> ChannelPost:
    dane = {"channel": "mgmt", "kind": "text", "body": "Treść.", "proof": ""}
    dane.update(nadpisz)
    return ChannelPost(**dane)


def _waliduj(post):
    s = SessionLocal()
    try:
        contentbot.waliduj(s, post)
    finally:
        s.close()


def _wyplata(*, share=6180.0, profit=7725.0, paid=True) -> str:
    token = secrets.token_urlsafe(12)[:16]
    s = SessionLocal()
    try:
        acc = Account(login=f"5{secrets.randbelow(10**6):06d}", initial_balance=100_000.0)
        s.add(acc)
        s.flush()
        s.add(Payout(account_id=acc.id, profit_amount=profit, trader_share=share,
                     paid=paid, cert_token=token))
        s.commit()
    finally:
        s.close()
    return token


# --------------------------------------------------------------------------- #
#  Treść bez dowodu                                                            #
# --------------------------------------------------------------------------- #
def test_liczba_bez_dowodu_jest_odrzucana():
    """Nie dlatego, że liczba jest zła — dlatego, że nikt nie wskazał źródła."""
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="We paid out $186,000 last month."))
    assert "źródła" in str(e.value)


def test_procent_bez_dowodu_tez():
    with pytest.raises(contentbot.NieprawdziwyPost):
        _waliduj(_post(body="Our traders keep 80% of the profit."))


def test_tresc_ponadczasowa_przechodzi():
    _waliduj(_post(body="We manage the account. You get the notifications."))


def test_pusta_tresc_odrzucona():
    with pytest.raises(contentbot.NieprawdziwyPost):
        _waliduj(_post(body="   "))


def test_slownictwo_niedoboru_przechodzi():
    """Świadoma decyzja właściciela: licznik miejsc jedzie w opisie kanału
    z `spots.js`, więc walidator nie może odrzucać tego samego w kolejce."""
    _waliduj(_post(body="Only two spots left. Message us to get started."))


# --------------------------------------------------------------------------- #
#  Dowód: wypłata                                                              #
# --------------------------------------------------------------------------- #
def test_kwota_zgodna_z_wyplata_przechodzi():
    token = _wyplata(share=6180.0)
    _waliduj(_post(body="$6,180 payout confirmed for Ryan F.", proof=f"payout:{token}"))


def test_kwota_niezgodna_jest_odrzucana():
    """To jest asercja, która czyni post o wypłacie niepodrabialnym."""
    token = _wyplata(share=6180.0, profit=7725.0)
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="$61,800 payout confirmed for Ryan F.", proof=f"payout:{token}"))
    assert "$6,180" in str(e.value)


def test_kwota_zysku_tez_jest_dozwolona():
    """Post może mówić o kwocie zdjętej z konta albo o udziale tradera."""
    token = _wyplata(share=6180.0, profit=7725.0)
    _waliduj(_post(body="$7,725 came off the account; $6,180 went to the trader.",
                   proof=f"payout:{token}"))


def test_nieistniejaca_wyplata_odrzucona():
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="$1,000 paid.", proof="payout:NIEMATAKIEGO"))
    assert "nie ma wypłaty" in str(e.value)


def test_niewyplacona_wyplata_odrzucona():
    token = _wyplata(share=500.0, paid=False)
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="$500 paid.", proof=f"payout:{token}"))
    assert "wypłacona" in str(e.value)


# --------------------------------------------------------------------------- #
#  Dowód: statystyka                                                           #
# --------------------------------------------------------------------------- #
def test_statystyka_spelniona_przechodzi():
    s = SessionLocal()
    try:
        ile = contentbot.statystyki_publiczne(s)["payouts_count"]
    finally:
        s.close()
    _waliduj(_post(body=f"More than {max(ile - 1, 0)} payouts so far.",
                   proof=f"stat:payouts_count:gte:{max(ile - 1, 0)}"))


def test_statystyka_niespelniona_mowi_ile_jest_dzis():
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="Over 10,000,000 paid out.",
                       proof="stat:payouts_total_usd:gte:10000000"))
    assert "wynosi dziś" in str(e.value)


def test_nieznana_statystyka_wymienia_dostepne():
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="123", proof="stat:zysk_kwartalny:gte:1"))
    assert "payouts_count" in str(e.value)


def test_zly_operator_odrzucony():
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(body="123", proof="stat:payouts_count:wiekszy:1"))
    assert "operator" in str(e.value)


def test_nieznany_rodzaj_dowodu():
    with pytest.raises(contentbot.NieprawdziwyPost):
        _waliduj(_post(body="123", proof="zaufajmi:tak"))


# --------------------------------------------------------------------------- #
#  Limity Telegrama                                                            #
# --------------------------------------------------------------------------- #
def test_za_dlugi_podpis_to_odmowa_a_nie_ciche_przyciecie():
    """Telegram utnie podpis do 1024 znaków w połowie zdania, a obcięte zdanie
    potrafi znaczyć coś innego niż całe."""
    with pytest.raises(contentbot.NieprawdziwyPost) as e:
        _waliduj(_post(kind="photo", media_url="https://x/y", body="a" * 1100))
    assert "1024" in str(e.value)


def test_dlugi_tekst_bez_zdjecia_przechodzi():
    _waliduj(_post(kind="text", body="a" * 1100))


def test_zdjecie_bez_adresu_odrzucone():
    with pytest.raises(contentbot.NieprawdziwyPost):
        _waliduj(_post(kind="photo", media_url=None, body="Podpis."))


# --------------------------------------------------------------------------- #
#  Przepływ w panelu                                                           #
# --------------------------------------------------------------------------- #
def _utworz(**dane):
    return client.post("/api/admin/channel-posts", json={
        "channel": "mgmt", "kind": "text", "body": "Treść bez liczb.",
        "proof": "", **dane}, headers=ADMIN)


def test_nowy_post_jest_zawsze_szkicem():
    assert _utworz().json()["status"] == "draft"


def test_zatwierdzenie_odrzuca_nieprawde_z_wyjasnieniem():
    pid = _utworz(body="Over 10,000,000 paid out.",
                  proof="stat:payouts_total_usd:gte:10000000").json()["id"]
    r = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)
    assert r.status_code == 400
    assert "wynosi dziś" in r.json()["detail"]


def test_zatwierdzenie_przechodzi_dla_prawdziwej_tresci():
    pid = _utworz().json()["id"]
    r = client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_nie_da_sie_zaplanowac_niezatwierdzonego():
    pid = _utworz().json()["id"]
    r = client.post(f"/api/admin/channel-posts/{pid}/schedule",
                    json={"scheduled_for": datetime.now(timezone.utc).isoformat()},
                    headers=ADMIN)
    assert r.status_code == 409


def test_edycja_cofa_do_szkicu():
    """Zmieniona treść nie jest już tą, którą ktoś zatwierdził."""
    pid = _utworz().json()["id"]
    client.post(f"/api/admin/channel-posts/{pid}/approve", headers=ADMIN)
    r = client.patch(f"/api/admin/channel-posts/{pid}",
                     json={"channel": "mgmt", "kind": "text",
                           "body": "Zupełnie inna treść.", "proof": ""}, headers=ADMIN)
    assert r.json()["status"] == "draft"


def test_publikacja_waliduje_ponownie(monkeypatch):
    """Post zatwierdzony wczoraj nie może wyjść dziś na wczorajszych liczbach."""
    s = SessionLocal()
    try:
        ile = contentbot.statystyki_publiczne(s)["payouts_count"]
    finally:
        s.close()
    pid = _utworz(body=f"At least {ile} payouts.",
                  proof=f"stat:payouts_count:gte:{ile}").json()["id"]
    assert client.post(f"/api/admin/channel-posts/{pid}/approve",
                       headers=ADMIN).status_code == 200

    # Świat się zmienił: próg nagle nieosiągalny.
    s = SessionLocal()
    try:
        p = s.get(ChannelPost, pid)
        p.proof = "stat:payouts_count:gte:999999"
        s.commit()
    finally:
        s.close()

    r = client.post(f"/api/admin/channel-posts/{pid}/publish", headers=ADMIN)
    assert r.status_code == 502
    s = SessionLocal()
    try:
        assert s.get(ChannelPost, pid).status == "failed"
    finally:
        s.close()


def test_harmonogram_wysyla_jeden_post_na_przebieg(monkeypatch):
    """Kolejka zostawiona na tydzień nie ma prawa wysypać wszystkiego naraz."""
    wyslane = []
    monkeypatch.setattr(contentbot, "opublikuj",
                        lambda s, post, **k: (wyslane.append(post.id),
                                              {"posted": True})[1])
    wczoraj = datetime.now(timezone.utc) - timedelta(days=1)
    s = SessionLocal()
    try:
        for _ in range(3):
            s.add(ChannelPost(channel="mgmt", kind="text", body="Gotowe.",
                              status="scheduled", scheduled_for=wczoraj))
        s.commit()
        wynik = contentbot.wyslij_zaplanowane(s)
    finally:
        s.close()
    assert wynik["sent"] == 1
    assert len(wyslane) == 1


def test_termin_w_przyszlosci_nie_wychodzi(monkeypatch):
    monkeypatch.setattr(contentbot, "opublikuj",
                        lambda s, post, **k: {"posted": True})
    jutro = datetime.now(timezone.utc) + timedelta(days=1)
    s = SessionLocal()
    try:
        s.query(ChannelPost).filter(ChannelPost.status == "scheduled").delete()
        s.add(ChannelPost(channel="mgmt", kind="text", body="Później.",
                          status="scheduled", scheduled_for=jutro))
        s.commit()
        assert contentbot.wyslij_zaplanowane(s)["sent"] == 0
    finally:
        s.close()


def test_kolejka_wymaga_admina():
    assert client.get("/api/admin/channel-posts").status_code == 403
