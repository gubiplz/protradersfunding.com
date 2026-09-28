"""Update o koncie z PRAWDZIWYCH liczb — pod mail „Weekly update" i DM na Telegram.

Mail „Weekly update" wyszedł raz z pustymi nawiasami. Teraz serwer składa
treść z metryk konta, księgi transakcji i wypłat: stan konta, transakcje, co
dalej. Pisane jak człowiek: akapity, bez etykiet „Where it stands:", bez
punktorów, najwyżej jedna pauza, bez dni handlowych. Ujęć jest dużo i każde
zmienia kilka zdań naraz („Another wording"). Bez kont — pusto, wprost.
"""
import re
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, insights, mail_templates  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Trade, Trader  # noqa: E402

init_db()
client = TestClient(app)
ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(10000))
TERAZ = datetime.now(timezone.utc).replace(tzinfo=None)


def _trader(name="Ada Obi"):
    s = SessionLocal()
    tr = Trader(email=f"ins{next(LICZNIK)}@test.pl", password_hash=auth.hash_password("haslo12345"),
                full_name=name, referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.commit(); tid = tr.id; s.close()
    return tid


def _konto(tid, *, balance=26200.0, status="active", phase="eval_1", days=3, min_days=5,
           breach=None, trades=()):
    s = SessionLocal()
    acc = Account(trader_id=tid, login=f"7{next(LICZNIK)}", product_key="2step-25k",
                  initial_balance=25000.0, balance=balance, equity=balance,
                  peak_equity=max(balance, 25000.0), day_start_equity=balance,
                  status=status, phase=phase, trading_days_count=days,
                  min_trading_days=min_days, profit_target_p1=8.0, profit_target_p2=5.0,
                  max_daily_loss_pct=5.0, max_overall_loss_pct=10.0, steps=2,
                  profit_split_pct=90.0, breach_reason=breach, source="purchase")
    s.add(acc); s.flush()
    for i, (symbol, pnl) in enumerate(trades):
        s.add(Trade(account_id=acc.id, symbol=symbol, side="buy", lots=0.1,
                    open_price=1.0, close_price=1.0, pnl=pnl, status="closed", source="bot",
                    opened_at=TERAZ - timedelta(hours=10 - i), closed_at=TERAZ - timedelta(hours=9 - i)))
    s.commit(); aid = acc.id; login = acc.login; s.close()
    return aid, login


def _jak_czlowiek(tekst):
    """Bez znaków AI: etykiet sekcji, punktorów, średników, lawiny pauz, nawiasów-rusztowań."""
    for zakaz in ("Where it stands", "What we did", "What's next", "What is next", "•", ";", "["):
        assert zakaz not in tekst, (zakaz, tekst)
    assert tekst.count("–") + tekst.count("—") <= 2, tekst


def test_bez_kont_nie_ma_czego_raportowac():
    tid = _trader()
    r = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["accounts"] == [] and r.json()["variants"] == [] and r.json()["mail_variants"] == []


def test_update_z_liczbami_konta_w_ukladzie_weekly_update():
    tid = _trader("Ada Obi")
    _, login = _konto(tid, trades=[("XAUUSD", 300.0), ("XAUUSD", 500.0), ("US30", -100.0),
                                   ("XAUUSD", 200.0), ("XAUUSD", 300.0), ("XAUUSD", 100.0)])
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    assert dane["name"] == "Ada"
    (k,) = dane["accounts"]
    assert k["login"] == login and k["trades"] == 6 and k["wins"] == 5
    assert k["best_symbol"] == "XAUUSD" and k["streak"] == 3
    assert abs(k["profit_pct"] - 4.8) < 0.01 and k["days"] == 3 and k["min_days"] == 5

    # DM: dużo ujęć, każde z tymi samymi liczbami, pisane jak człowiek.
    warianty = dane["variants"]
    assert len(warianty) >= 40 and len(set(warianty)) == len(warianty)
    for tekst in warianty:
        assert tekst.startswith(("Hey Ada", "Ada,", "Hi Ada"))
        _jak_czlowiek(tekst)
        assert login in tekst and "$26,200" in tekst and "4.8%" in tekst
        assert "3.2%" in tekst and "6" in tekst and "XAUUSD" in tekst and "$1,300" in tekst
        assert "trading days" not in tekst and "minimum" not in tekst
        assert "http" not in tekst
    # „Another wording" ma z czego zmieniać: otwarcia, zdania o stanie i o
    # transakcjach występują w kilku wersjach.
    assert len({w.split("\n")[0] for w in warianty}) >= 5
    assert len({z for w in warianty for z in re.findall(r"[^.\n]*trades[^.\n]*\.", w)}) >= 3
    # Mail: akapity, link do desku, stopka.
    assert len(dane["mail_variants"]) >= 12
    for tekst in dane["mail_variants"]:
        assert tekst.startswith("Hi Ada,\n\n")
        _jak_czlowiek(tekst)
        assert "$26,200" in tekst and "trading days" not in tekst
        assert tekst.rstrip().endswith("--\nForex Passing")


def test_konto_ktore_padlo_mowi_to_wprost():
    tid = _trader("Bob")
    _, login = _konto(tid, balance=22400.0, status="failed", breach="max daily loss")
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    assert dane["accounts"][0]["status"] == "failed"
    for t in dane["variants"]:
        assert login in t and "(max daily loss)" in t and "closed" in t
        assert "$22,400" in t and "If you want to go again" in t


def test_bez_zamknietych_transakcji_mowi_ze_nic_do_raportu():
    tid = _trader("Cy")
    _konto(tid, trades=[])
    for t in client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["variants"]:
        assert "othing closed yet" in t


def test_dwa_konta_to_dwa_bloki_bez_punktorow():
    tid = _trader("Dee")
    _, a = _konto(tid, balance=27500.0)          # cel 8% zaliczony
    _, b = _konto(tid, balance=25500.0)          # jeszcze nie
    for t in client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["variants"]:
        _jak_czlowiek(t)
        # Każde konto to blok, który zaczyna się od nagłówka z numerem i fazą.
        naglowki = [ln for ln in t.split("\n") if re.fullmatch(rf"(Account )?({a}|{b})(, | \()phase 1\)?", ln)]
        assert len(naglowki) == 2, t
        # „Co dalej" widzi, że drugie konto jeszcze nie ma celu.
        assert "next stage from here" not in t and "Target's done" not in t


def _konto_po_awansie(tid, *, phase, balance, initial=100_000.0, od=None, stare=(), nowe=(),
                      status=None, days=1, min_days=5):
    """Konto po awansie: `stare` zamknięte przed startem fazy, `nowe` po nim.
    `od=None` = konto sprzed kolumny `phase_started_at` (bez daty awansu)."""
    s = SessionLocal()
    acc = Account(trader_id=tid, login=f"7{next(LICZNIK)}", product_key="2step-100k",
                  initial_balance=initial, balance=balance, equity=balance,
                  peak_equity=max(balance, initial), day_start_equity=balance,
                  status=status or ("funded" if phase == "funded" else "active"), phase=phase,
                  trading_days_count=days, min_trading_days=min_days,
                  profit_target_p1=8.0, profit_target_p2=5.0, max_daily_loss_pct=5.0,
                  max_overall_loss_pct=10.0, steps=2, profit_split_pct=80.0, source="purchase",
                  phase_started_at=od)
    s.add(acc); s.flush()
    for i, (symbol, pnl) in enumerate([*stare, *nowe]):
        # Stare dni temu, nowe w ostatnich godzinach — po kolei, jak w księdze.
        kiedy = (TERAZ - timedelta(days=10, hours=-i) if i < len(stare)
                 else TERAZ - timedelta(hours=4) + timedelta(minutes=i))
        s.add(Trade(account_id=acc.id, symbol=symbol, side="buy", lots=0.1, open_price=1.0,
                    close_price=1.0, pnl=pnl, status="closed", source="bot",
                    opened_at=kiedy - timedelta(minutes=30), closed_at=kiedy))
    s.commit(); login = acc.login; s.close()
    return login


def test_funded_po_awansie_to_nowy_track_record():
    """Konto funded na $100,035: bez „80 closed trades, +$15,683" z ewaluacji,
    z dokładnym procentem i bez odliczania dni do okna wypłaty."""
    tid = _trader("Sunny Sohi")
    ewaluacje = [("NAS100", 900.0)] * 15 + [("NAS100", -200.0)] * 5   # +12,500 w fazach 1 i 2
    login = _konto_po_awansie(tid, phase="funded", balance=100_035.0,
                              od=TERAZ - timedelta(hours=5), stare=ewaluacje,
                              nowe=[("EURUSD", 20.0), ("XAUUSD", 15.0)])
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    (k,) = dane["accounts"]
    assert k["trades"] == 2 and k["wins"] == 2 and k["net_pnl"] == 35.0
    assert k["best_symbol"] == "EURUSD"
    for tekst in dane["variants"] + dane["mail_variants"]:
        assert login in tekst and "$100,035" in tekst and "0.04%" in tekst, tekst
        assert "0.0%" not in tekst and "NAS100" not in tekst and "$12,500" not in tekst
        assert "payout window" not in tekst and "trading days" not in tekst
        # Co dalej na funded to sposób prowadzenia konta, nigdy wypłata.
        assert "payout" not in tekst.lower()
        _jak_czlowiek(tekst)
    assert any("+$35" in t for t in dane["variants"])
    # „Another wording" ma z czego zmieniać także zdanie o dalszym kroku.
    dalej = {z for z in insights._DALEJ_FUNDED for t in dane["variants"] if z in t}
    assert len(dalej) >= 7, dalej


def test_funded_pod_startem_mowi_o_odrabianiu_nie_o_wyplacie():
    tid = _trader("Max")
    _konto_po_awansie(tid, phase="funded", balance=99_400.0, od=TERAZ - timedelta(hours=5),
                      nowe=[("XAUUSD", -600.0)])
    warianty = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["variants"]
    for tekst in warianty:
        assert "payout" not in tekst.lower()
        assert "down 0.6%" in tekst or "-0.6%" in tekst, tekst
        assert any(z.replace("{it}", "it") in tekst for z in insights._DALEJ_FUNDED_POD_KRESKA)


def test_duzo_roznych_ujec_dla_another_wording():
    tid = _trader("Ada Obi")
    _konto(tid, trades=[("XAUUSD", 300.0), ("XAUUSD", 500.0), ("US30", -100.0)])
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    dm, mail = dane["variants"], dane["mail_variants"]
    assert len(dm) >= 100 and len(set(dm)) == len(dm)
    assert len(mail) >= 50 and len(set(mail)) == len(mail)
    # Otwarcia, zakończenia i zdania o transakcjach w wielu wersjach, nie w trzech.
    assert len({t.split("\n")[0].split(".")[0] for t in dm}) >= 8
    assert len({t.rsplit("\n\n", 1)[-1].split(". ")[-1] for t in dm}) >= 8
    assert len({z for w in dm for z in re.findall(r"[^.\n]*trades[^.\n]*\.", w)}) >= 6
    for tekst in dm + mail:
        _jak_czlowiek(tekst)


def test_phase_2_liczy_od_zera():
    tid = _trader("Ola")
    login = _konto_po_awansie(tid, phase="eval_2", balance=101_200.0,
                              od=TERAZ - timedelta(hours=5),
                              stare=[("US30", 5_000.0), ("US30", 3_400.0)],
                              nowe=[("XAUUSD", 800.0), ("XAUUSD", 400.0)])
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    (k,) = dane["accounts"]
    assert k["trades"] == 2 and k["net_pnl"] == 1_200.0 and k["best_symbol"] == "XAUUSD"
    for tekst in dane["variants"]:
        assert login in tekst and "1.2%" in tekst and "3.8%" in tekst   # 5% - 1.2%
        assert "US30" not in tekst and "$9,600" not in tekst and "$8,400" not in tekst


def test_stare_konto_bez_daty_awansu_odtwarza_granice_z_ksiegi():
    """Awans sprzed kolumny `phase_started_at`: faza 1 skończyła się na +8%,
    faza 2 na +5% — funded to dopiero dwie ostatnie transakcje."""
    tid = _trader("Lee")
    _konto_po_awansie(tid, phase="funded", balance=25_035.0, initial=25_000.0, od=None,
                      stare=[("US30", 1_000.0), ("US30", 1_100.0), ("GER40", 700.0),
                             ("GER40", 600.0)],
                      nowe=[("EURUSD", 20.0), ("EURUSD", 15.0)])
    (k,) = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["accounts"]
    assert k["trades_known"] and k["trades"] == 2 and k["net_pnl"] == 35.0


def test_faza_nie_do_ustalenia_pomija_transakcje_zamiast_pokazac_stare():
    tid = _trader("Kim")
    _konto_po_awansie(tid, phase="funded", balance=25_035.0, initial=25_000.0, od=None,
                      stare=[("US30", 100.0), ("US30", -50.0)])
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    assert dane["accounts"][0]["trades_known"] is False
    for tekst in dane["variants"]:
        assert "trades" not in tekst and "US30" not in tekst and "othing closed" not in tekst
        assert "$25,035" in tekst and "0.14%" in tekst and "\n\n\n" not in tekst
        assert " \n" not in tekst and "\n " not in tekst and not tekst.endswith("\n")


def test_procent_bez_gubienia_malych_ruchow():
    assert insights._pc(0.035) == "0.04"
    assert insights._pc((100_035 - 100_000) / 100_000 * 100) == "0.04"
    assert insights._pc(4.8) == "4.8" and insights._pc(8.0) == "8" and insights._pc(3.25) == "3.25"
    assert insights._pc(0.004) == "0.004" and insights._pc(0) == "0"
    assert insights._proc(-1.5) == "-1.5%" and insights._proc(0.035) == "+0.04%"


def test_szablony_dynamiczne_i_wiecej_ujec():
    lista = {t["id"]: t for t in client.get("/api/admin/email-templates", headers=ADMIN).json()}
    assert lista["b:fx-weekly"]["dynamic"] == "insights"
    assert lista["b:tg-update"]["dynamic"] == "insights" and lista["b:tg-update"]["sender"] == "tg"
    assert lista["b:fx-accepted"].get("dynamic") is None
    # Ujęcia × zakończenia: co najmniej 12 tekstów na zwykły szablon TG.
    for klucz in ("b:tg-hello", "b:tg-free-ready", "b:tg-need-login", "b:tg-checkin"):
        w = lista[klucz]["variants"]
        assert len(w) >= 12 and len(set(w)) == len(w), klucz
    assert all("{name}" in w for w in lista["b:tg-hello"]["variants"])


def test_lista_klientow_ma_paid_usd_i_panel_filtr_bought():
    from app.models import Order
    tid = _trader("Ed")
    s = SessionLocal()
    s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=149.0, status="paid",
                provider="stripe"))
    s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=0.0, status="paid",
                provider="grant"))
    s.commit(); s.close()
    wiersz = next(t for t in client.get("/api/admin/traders", headers=ADMIN).json() if t["id"] == tid)
    assert wiersz["paid_usd"] == 149.0
    kod = client.get("/static/js/admin-panel.js").text
    assert "['bought','Bought',t=>(t.paid_usd||0)>0]" in kod
    assert "function pickWording(" in kod and "async function fillMailTpl()" in kod
    assert "'/api/admin/traders/'+traderId+'/insights'" in kod
    assert 'id="lm-shuffle"' in kod and "function mailShuffle()" in kod
