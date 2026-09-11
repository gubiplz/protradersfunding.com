"""Powiadomienia o cichej zmianie stanu konta (`poller._state_change_due`).

Cztery zdarzenia raz na całe życie konta: połowa i trzy czwarte celu zysku,
komplet dni handlowych, gotowość do wypłaty. Kanał jak przy limit_warning:
push + centrum powiadomień, bez maila, pod preferencją `notify_trading`.

Testy pilnują trzech rzeczy, na których ta funkcja stoi: strażnik zapala się
raz, doba ciszy po założeniu konta zasłania importy z historią, a treść mówi
o tym, co się JUŻ stało (żadnego „zostało ci jeszcze X").
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "pf_test_state_notices.db")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from app import auth, notify, poller, push, rules  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Account, Notification, Trader  # noqa: E402
from app.poller import _state_change_due  # noqa: E402

init_db()

TERAZ = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
DAWNO = TERAZ - timedelta(days=30)


def _konto(**nad):
    dane = dict(login="971234567", trader_id=1, trader_name="State Tester",
                product_key="2step-100k", initial_balance=100_000, steps=2,
                profit_split_pct=90, status="active", phase="eval_1",
                balance=104_000, equity=104_000,
                profit_target_p1=8.0, profit_target_p2=5.0,
                max_daily_loss_pct=5.0, max_overall_loss_pct=10.0,
                min_trading_days=4, drawdown_type="static",
                trading_days_count=0, created_at=DAWNO)
    dane.update(nad)
    return Account(**dane)


def _wynik(acc, profit_pct=0.0, trading_days=0, now=TERAZ):
    cfg = rules.config_from_account(acc)
    metryki = {"profit_pct": profit_pct, "trading_days": trading_days}
    return _state_change_due(acc, cfg, metryki, now)


def test_polowa_celu_odzywa_sie_raz():
    acc = _konto()
    ev, tytul = _wynik(acc, profit_pct=4.0)
    assert ev == "target_50"
    assert tytul == "971234567: half of the 8% profit target reached"
    assert acc.target_50_at == TERAZ
    # drugi tick z tym samym stanem ma milczeć
    assert _wynik(acc, profit_pct=4.5) is None


def test_ponizej_polowy_cisza():
    acc = _konto()
    assert _wynik(acc, profit_pct=3.99) is None
    assert acc.target_50_at is None


def test_przeskok_obu_progow_daje_tylko_wyzszy():
    """Konto, które między tickami przeskoczyło 50% i 75%, dostaje jedną
    wiadomość — o progu, na którym naprawdę stoi."""
    acc = _konto()
    ev, tytul = _wynik(acc, profit_pct=6.5)
    assert ev == "target_75"
    assert "three quarters" in tytul
    # niższy próg jest ostemplowany, żeby nie odezwał się spóźniony
    assert acc.target_50_at == TERAZ and acc.target_75_at == TERAZ
    assert _wynik(acc, profit_pct=7.0) is None


def test_po_polowie_przychodzi_kolej_na_trzy_czwarte():
    acc = _konto()
    assert _wynik(acc, profit_pct=4.0)[0] == "target_50"
    assert _wynik(acc, profit_pct=6.0)[0] == "target_75"
    assert _wynik(acc, profit_pct=7.9) is None


def test_doba_ciszy_po_zalozeniu_konta():
    """Konto z zaimportowaną historią przechodzi progi na pierwszym ticku —
    to nie jest zmiana stanu, tylko stan początkowy."""
    swieze = _konto(created_at=TERAZ - timedelta(hours=5))
    assert _wynik(swieze, profit_pct=6.5, trading_days=9) is None
    assert swieze.target_75_at is None
    # po dobie te same metryki już się odzywają
    assert _wynik(swieze, profit_pct=6.5, now=TERAZ + timedelta(hours=20))[0] == "target_75"


def test_komplet_dni_handlowych():
    acc = _konto()
    ev, tytul = _wynik(acc, trading_days=4)
    assert ev == "min_days_met"
    assert tytul == "971234567: minimum of 4 trading days completed"
    assert _wynik(acc, trading_days=7) is None


def test_cel_zysku_ma_pierwszenstwo_przed_dniami():
    """Jeden tick to jedna wiadomość; dni poczekają na następny."""
    acc = _konto()
    assert _wynik(acc, profit_pct=4.0, trading_days=9)[0] == "target_50"
    assert _wynik(acc, profit_pct=4.0, trading_days=9)[0] == "min_days_met"


def test_funded_nie_dostaje_wiadomosci_o_dniach():
    """Na funded te same dni są bramką wypłaty — mówi o nich payout_ready,
    a treść min_days_met („faza się zamknie") nie miałaby tam sensu."""
    acc = _konto(status="funded", phase="funded", balance=100_000,
                 equity=100_000, trading_days_count=9)
    assert _wynik(acc, trading_days=9) is None
    assert acc.min_days_at is None


def test_wyplata_gotowa_wymaga_zysku():
    acc = _konto(status="funded", phase="funded", steps=0, min_trading_days=30,
                 balance=100_000, equity=100_000, trading_days_count=30)
    assert _wynik(acc) is None          # zero zysku = nie ma czego wypłacać
    acc.balance = 103_000
    ev, tytul = _wynik(acc)
    assert ev == "payout_ready"
    assert tytul == "971234567: eligible to request a payout"
    assert _wynik(acc) is None


def test_wyplata_czeka_na_komplet_dni():
    acc = _konto(status="funded", phase="funded", steps=0, min_trading_days=30,
                 balance=103_000, equity=103_000, trading_days_count=12)
    assert _wynik(acc) is None
    acc.trading_days_count = 30
    assert _wynik(acc)[0] == "payout_ready"


def test_ewaluacja_nie_dostaje_payout_ready():
    """Konto w ewaluacji ma zysk i komplet dni, ale wypłata jest dopiero
    po funded — z wyczerpanymi strażnikami progów ma milczeć."""
    acc = _konto(balance=103_000, equity=103_000, trading_days_count=30)
    acc.target_50_at = acc.target_75_at = acc.min_days_at = TERAZ
    assert _wynik(acc, profit_pct=1.0, trading_days=9) is None
    assert acc.payout_ready_at is None


def test_awans_fazy_zeruje_straznikow_progow():
    """Druga ewaluacja ma własny cel i własne dni — bez resetu trader
    przeszedłby ją w zupełnej ciszy."""
    acc = _konto()
    acc.target_50_at = acc.target_75_at = acc.min_days_at = TERAZ
    rt = rules.AccountRuntime(phase=rules.Phase.EVAL_1, balance=acc.balance,
                              equity=acc.equity)
    poller._advance_phase(acc, rt)
    assert acc.phase == "eval_2"
    assert acc.target_50_at is None and acc.target_75_at is None
    assert acc.min_days_at is None
    # nowy cel (5%) liczy się od nowa
    assert _wynik(acc, profit_pct=2.6)[0] == "target_50"


def test_wszystkie_cztery_zdarzenia_slucha_preferencji():
    """Zdarzenie spoza `_PREF_BY_EVENT` omija wyłącznik tradera — te cztery
    muszą tam być, inaczej wyciszone konto i tak dostanie pusha."""
    for ev in ("target_50", "target_75", "min_days_met", "payout_ready"):
        assert notify._PREF_BY_EVENT.get(ev) == "notify_trading"


def _trader(nazwa, notify_trading=True):
    """Świeży trader na każde uruchomienie — baza testowa jest wspólna i trwała,
    więc stały e-mail/kod polecający zderzyłby się z poprzednim przebiegiem."""
    znacznik = uuid.uuid4().hex[:8]
    email = f"{nazwa}-{znacznik}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="State Tester", referral_code=f"ST{znacznik.upper()}",
                notify_trading=notify_trading)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid, email


def test_wpis_ladauje_w_centrum_powiadomien():
    tid, email = _trader("state-centrum")
    push.send_event("target_75", email,
                    "971234567: three quarters of the 8% profit target reached")
    s = SessionLocal()
    wpis = (s.query(Notification)
            .filter(Notification.trader_id == tid,
                    Notification.event == "target_75").one())
    assert "three quarters" in wpis.title
    s.close()


def test_pref_notify_trading_off_wycisza():
    tid, email = _trader("state-cisza", notify_trading=False)
    assert push.send_event("payout_ready", email,
                           "971234567: eligible to request a payout") == 0
    s = SessionLocal()
    assert s.query(Notification).filter(Notification.trader_id == tid).count() == 0
    s.close()


def test_tresci_nie_uzywaja_ramki_near_miss():
    """Regulatorzy wytykają „zostało ci tylko X" — próg opisany brakującym
    dystansem robi z limitu ryzyka zachętę do dalszej jazdy. Tu wolno mówić,
    co się stało; nie wolno mówić, ile brakuje ani kiedy trader ma zagrać."""
    zakazane = ("only ", " to go", "left to", "away from", "so close",
                "hurry", "act now", "right now", "don't miss", "last chance")
    for ev in ("target_50", "target_75", "min_days_met", "payout_ready"):
        tresc = push._BODY[ev].lower()
        trafienia = [z for z in zakazane if z in tresc]
        assert not trafienia, f"{ev}: {trafienia}"
