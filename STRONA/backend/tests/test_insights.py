"""Update o koncie z PRAWDZIWYCH liczb — pod mail „Weekly update" i DM na Telegram.

Mail „Weekly update" wyszedł raz z pustymi nawiasami. Teraz serwer składa
treść z metryk konta, księgi transakcji i wypłat w układzie Where it stands /
What we did / What is next, w kilku ujęciach (otwarcie × styl × zakończenie),
a panel losuje jedno i ma „Another wording". Bez kont — pusto, wprost.
"""
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

    # DM: otwarcia × style × zakończenia — dużo ujęć, każde z tymi samymi liczbami.
    assert len(dane["variants"]) >= 24 and len(set(dane["variants"])) == len(dane["variants"])
    for tekst in dane["variants"]:
        assert tekst.startswith(("Hey Ada", "Ada,", "Hi Ada"))
        assert "Where it stands:" in tekst and "What we did:" in tekst and "What's next:" in tekst
        assert login in tekst and "$26,200" in tekst and "+4.8%" in tekst
        assert "3.2% left to the 8% target" in tekst
        assert "3 of 5 minimum trading days" in tekst
        assert "6 closed trades, 83% winners, net +$1,300, best on XAUUSD" in tekst
        assert "Last 3 in a row were winners" in tekst
        assert "http" not in tekst and "[" not in tekst
    # Mail: układ szablonu — akapity, link do desku, stopka.
    assert len(dane["mail_variants"]) >= 6
    for tekst in dane["mail_variants"]:
        assert tekst.startswith("Hi Ada,\n\n")
        assert "Where it stands:" in tekst and "What is next:" in tekst
        assert tekst.rstrip().endswith("--\nForex Passing")
        assert "[" not in tekst


def test_konto_ktore_padlo_mowi_to_wprost():
    tid = _trader("Bob")
    _, login = _konto(tid, balance=22400.0, status="failed", breach="max daily loss")
    dane = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()
    assert dane["accounts"][0]["status"] == "failed"
    t = dane["variants"][0]
    assert f"account {login} hit a rule and is closed (max daily loss)" in t
    assert "$22,400" in t and "If you want to go again" in t


def test_bez_zamknietych_transakcji_mowi_ze_nic_do_raportu():
    tid = _trader("Cy")
    _konto(tid, trades=[])
    t = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["variants"][0]
    assert "nothing closed yet" in t


def test_dwa_konta_to_dwie_linie():
    tid = _trader("Dee")
    _, a = _konto(tid)
    _, b = _konto(tid, balance=25500.0)
    t = client.get(f"/api/admin/traders/{tid}/insights", headers=ADMIN).json()["variants"][0]
    assert f"• account {a}" in t or f"• {a}" in t
    assert f"• account {b}" in t or f"• {b}" in t


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
