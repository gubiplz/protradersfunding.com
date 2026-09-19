"""Poniedziałkowy przegląd tygodnia — `push.weekly_review` + `/api/me/weekly`.

Tydzień musi być ZAMKNIĘTY: liczymy poprzedni poniedziałek–niedzielę, nigdy dni
w toku. Tytuł powiadomienia nie niesie liczby (kwota w systemowej belce czyta
się jak wynik do pobicia), a obserwacje opisują to, co już było — bez rad,
sygnałów i celów. Tu pilnujemy okna, guardu raz-na-tydzień, ciszy przy zerze
transakcji i tego, że ten sam wniosek nie wraca trzeci raz z rzędu.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.NamedTemporaryFile(
    suffix=".db", delete=False).name)
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, push  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, AppSetting, Notification, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)

# Poniedziałek 2026-09-07, 08:00 w Warszawie. Tydzień do podsumowania to
# 2026-08-31 (pon) – 2026-09-06 (niedz).
PONIEDZIALEK = datetime(2026, 9, 7, 6, 0, tzinfo=timezone.utc)
START = datetime(2026, 8, 31)


def _trader(email: str, **kw) -> int:
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Weekly Tester", referral_code=uuid4().hex[:10].upper(), **kw)
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _konto(tid: int, login: str) -> int:
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Weekly Tester",
                  platform_login=login, platform_password="x",
                  platform_server="MetaQuotes-Demo", product_key="2step-25k",
                  initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                  peak_equity=25_000.0, day_start_equity=25_000.0,
                  day_start_balance=25_000.0, steps=2, profit_target_p1=8.0,
                  profit_target_p2=5.0, max_daily_loss_pct=5.0,
                  max_overall_loss_pct=10.0, min_trading_days=4,
                  status="active", phase="eval_1")
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _trade(aid: int, dzien: int, pnl: float, *, tydzien: int = 0) -> None:
    """`dzien` to numer dnia tygodnia (0 = poniedziałek startu okna)."""
    kiedy = START + timedelta(days=dzien - 7 * tydzien, hours=12)
    s = SessionLocal()
    s.add(Trade(account_id=aid, symbol="EURUSD", side="buy", lots=1.0,
                open_price=1.1, close_price=1.1, pnl=pnl, status="closed",
                opened_at=kiedy, closed_at=kiedy))
    s.commit(); s.close()


def _wyczysc_guard() -> None:
    s = SessionLocal()
    g = s.get(AppSetting, "last_weekly_week")
    if g:
        s.delete(g); s.commit()
    s.close()


def _wpisy(tid: int) -> list[Notification]:
    s = SessionLocal()
    w = (s.query(Notification).filter(Notification.trader_id == tid,
                                      Notification.event == "weekly_review")
         .order_by(Notification.id).all())
    s.close()
    return w


def test_okno_to_poprzedni_pelny_tydzien():
    # Środa 2026-09-09 patrzy na ten sam tydzień co poniedziałek 2026-09-07:
    # przegląd nigdy nie komentuje dni, które jeszcze trwają.
    for dzien in (PONIEDZIALEK, PONIEDZIALEK + timedelta(days=2)):
        od, do = push.week_window(dzien)
        assert od == START and do == START + timedelta(days=7)


def test_przeglad_wychodzi_raz_w_tygodniu_i_tylko_w_poniedzialek():
    _wyczysc_guard()
    tid = _trader("tydzien@example.com")
    aid = _konto(tid, "wk-guard")
    _trade(aid, 1, 300.0)
    _trade(aid, 3, -120.0)

    # Niedziela wieczorem — tydzień jeszcze trwa.
    assert push.weekly_review(now=PONIEDZIALEK - timedelta(hours=8))["sent"] == 0
    # Poniedziałek 03:00 czasu polskiego — za wcześnie, guard nietknięty.
    assert push.weekly_review(now=PONIEDZIALEK - timedelta(hours=5))["sent"] == 0

    assert push.weekly_review(now=PONIEDZIALEK)["sent"] >= 1
    wpisy = _wpisy(tid)
    assert len(wpisy) == 1
    assert wpisy[0].title == "Your week in review"
    # Tytuł bez liczby: kwota w belce systemowej czyta się jak wynik do pobicia.
    assert not any(z.isdigit() for z in wpisy[0].title)
    assert wpisy[0].url == "/portal?view=weekly"

    # Drugi przebieg tego samego dnia nie dokłada wpisu.
    assert push.weekly_review(now=PONIEDZIALEK + timedelta(hours=3))["sent"] == 0
    assert len(_wpisy(tid)) == 1


def test_tydzien_bez_transakcji_to_cisza():
    _wyczysc_guard()
    tid = _trader("cichy@example.com")
    _konto(tid, "wk-cisza")
    push.weekly_review(now=PONIEDZIALEK)
    assert _wpisy(tid) == []


def test_wypisany_z_ofert_nie_dostaje_przegladu():
    _wyczysc_guard()
    tid = _trader("bezofert@example.com", notify_marketing=False)
    aid = _konto(tid, "wk-optout")
    _trade(aid, 2, 500.0)
    push.weekly_review(now=PONIEDZIALEK)
    assert _wpisy(tid) == []


def test_konto_firmowe_bez_wlasciciela_nie_wywraca_przegladu():
    # Konta firmowe mają trader_id = NULL. Taki wiersz w oknie tygodnia wywracał
    # CAŁY przegląd (sortowanie zbioru z None-em), a try/except zamieniał to
    # w ciche „sent: 0" — dla wszystkich traderów naraz.
    _wyczysc_guard()
    tid = _trader("zfirmowym@example.com")
    _trade(_konto(tid, "wk-obok"), 2, 260.0)

    s = SessionLocal()
    firma = Account(login="wk-firmowe", trader_id=None, trader_name="Firma",
                    platform_login="wk-firmowe", platform_password="x",
                    platform_server="MetaQuotes-Demo", product_key="2step-25k",
                    initial_balance=25_000.0, balance=25_000.0, equity=25_000.0,
                    peak_equity=25_000.0, day_start_equity=25_000.0,
                    day_start_balance=25_000.0, steps=2, profit_target_p1=8.0,
                    profit_target_p2=5.0, max_daily_loss_pct=5.0,
                    max_overall_loss_pct=10.0, min_trading_days=4,
                    status="active", phase="eval_1")
    s.add(firma); s.commit(); fid = firma.id; s.close()
    _trade(fid, 2, 999.0)

    assert "error" not in push.weekly_review(now=PONIEDZIALEK)
    assert len(_wpisy(tid)) == 1


def test_ten_sam_wniosek_nie_wraca_trzeci_raz():
    # Trzy tygodnie z rzędu wyglądające tak samo: jeden dzień robi cały wynik.
    # Za trzecim razem przegląd musi zejść do kolejnej prawdziwej obserwacji.
    tid = _trader("powtorka@example.com")
    aid = _konto(tid, "wk-powtorka")
    for tydzien in (0, 1, 2):
        _trade(aid, 1, 40.0, tydzien=tydzien)
        _trade(aid, 3, 900.0, tydzien=tydzien)
        _wyczysc_guard()
        push.weekly_review(now=PONIEDZIALEK - timedelta(days=7 * tydzien))
    tresci = [w.body for w in _wpisy(tid)]
    assert len(tresci) == 3
    assert tresci[0] == tresci[1] and tresci[2] != tresci[1]


def test_obserwacje_opisuja_przeszlosc_bez_rad():
    s = {"days": [{"day": "2026-08-31", "name": "Monday", "label": "Mon", "pnl": 120.0, "trades": 2},
                  {"day": "2026-09-01", "name": "Tuesday", "label": "Tue", "pnl": 90.0, "trades": 2},
                  {"day": "2026-09-02", "name": "Wednesday", "label": "Wed", "pnl": -1_200.0, "trades": 5},
                  {"day": "2026-09-03", "name": "Thursday", "label": "Thu", "pnl": 0.0, "trades": 0},
                  {"day": "2026-09-04", "name": "Friday", "label": "Fri", "pnl": 0.0, "trades": 0},
                  {"day": "2026-09-05", "name": "Saturday", "label": "Sat", "pnl": 0.0, "trades": 0},
                  {"day": "2026-09-06", "name": "Sunday", "label": "Sun", "pnl": 0.0, "trades": 0}],
         "trades": 9, "net_pnl": -990.0, "green_days": 2, "red_days": 1, "trading_days": 3}
    s["best_day"] = s["days"][0]
    s["worst_day"] = s["days"][2]
    obs = push.weekly_observations(s, {"trades": 0})
    klucze = [k for k, _ in obs]
    assert klucze[0] == "one_day" and "gave_back" in klucze and klucze[-1] == "plain"
    zdania = " ".join(t for _, t in obs).lower()
    for slowo in ("should", "try", "next week", "aim", "target", "almost", "nearly"):
        assert slowo not in zdania


def test_endpoint_oddaje_siedem_dni_i_poprzedni_tydzien(monkeypatch):
    tid = _trader("ekran@example.com")
    aid = _konto(tid, "wk-ekran")
    _trade(aid, 0, 250.0)
    _trade(aid, 0, -50.0)
    _trade(aid, 4, 700.0)
    _trade(aid, 2, 111.0, tydzien=1)   # tydzień wcześniej

    import app.main as main
    klasa = main.datetime

    class Zegar(klasa):
        @classmethod
        def now(cls, tz=None):
            return PONIEDZIALEK

    monkeypatch.setattr(main, "datetime", Zegar)
    r = client.get("/api/me/weekly",
                   headers={"Authorization": f"Bearer {auth.make_token(tid)}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["from"] == "2026-08-31" and d["to"] == "2026-09-06"
    assert len(d["days"]) == 7 and [x["label"] for x in d["days"]][:2] == ["Mon", "Tue"]
    assert d["trades"] == 3 and d["net_pnl"] == 900.0
    assert d["trading_days"] == 2 and d["green_days"] == 2 and d["red_days"] == 0
    assert d["best_day"]["pnl"] == 700.0
    assert d["prev_trades"] == 1 and d["prev_net_pnl"] == 111.0
    assert 1 <= len(d["observations"]) <= 3
