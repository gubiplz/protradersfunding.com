"""Panel klienta: journal, tickety, ustawienia, KYC docs, activity, achievements.

Jak w pozostałych plikach: pytest współdzieli moduły/bazę — unikalne e-maile,
asercje odporne na cudze wiersze.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ADMIN_TOKEN", "tajny-token")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, catalog, notify  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Account, Breach, Certificate, EquitySnapshot, Order,  # noqa: E402
                        Payout, PayoutRequest, Trade, Trader)

init_db()
_s = SessionLocal()
catalog.seed_products(_s)
_s.close()

ADMIN_H = {"X-Admin-Token": get_settings().admin_token}


def _trader(email, full_name="Client Area", **extra):
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name=full_name, referral_code=auth.secrets.token_hex(3), **extra)
    s.add(tr); s.commit()
    tid = tr.id
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _konto(tid, login, *, status="passed", balance=10_000.0, initial=10_000.0):
    s = SessionLocal()
    acc = Account(login=login, trader_id=tid, trader_name="Client Area",
                  platform_login=login, platform_password="x", platform_server="MetaQuotes-Demo",
                  product_key="2step-25k", initial_balance=initial, balance=balance, equity=balance,
                  peak_equity=initial, day_start_equity=initial, day_start_balance=initial,
                  status=status, phase="eval_1")
    s.add(acc); s.commit()
    aid = acc.id
    s.close()
    return aid


# ---------------- journal ----------------
def test_journal_crud_owner_only():
    _, h = _trader("journal@test.pl")
    _, h_obcy = _trader("journal-obcy@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/journal", headers=h, json={"title": "Setup EURUSD", "content": "Notatka"})
        eid = r.json()["id"]
        assert r.status_code == 200
        assert any(e["id"] == eid for e in c.get("/api/me/journal", headers=h).json())
        assert c.get("/api/me/journal", headers=h_obcy).json() == []
        assert c.delete(f"/api/me/journal/{eid}", headers=h_obcy).status_code == 404
        assert c.delete(f"/api/me/journal/{eid}", headers=h).status_code == 200


def test_journal_pusty_tytul_odrzucony():
    _, h = _trader("journal2@test.pl")
    with TestClient(app) as c:
        assert c.post("/api/me/journal", headers=h, json={"title": "  "}).status_code == 400


# ---------------- tickety ----------------
def test_ticket_flow_trader_admin():
    _, h = _trader("ticket@test.pl")
    with TestClient(app) as c:
        t = c.post("/api/me/tickets", headers=h,
                   json={"subject": "MT5 login issue", "message": "Cannot log in"}).json()
        tid = t["id"]
        lista = c.get("/api/me/tickets", headers=h).json()
        assert any(x["id"] == tid and x["status"] == "open" for x in lista)

        # admin widzi i odpowiada -> status answered + wiadomość w wątku
        adm = c.get("/api/admin/tickets", headers=ADMIN_H).json()
        assert any(x["id"] == tid and x["trader_email"] == "ticket@test.pl" for x in adm)
        r = c.post(f"/api/admin/tickets/{tid}/reply", headers=ADMIN_H,
                   json={"message": "Please try the investor password."})
        assert r.json()["status"] == "answered"
        thread = c.get(f"/api/me/tickets/{tid}", headers=h).json()["thread"]
        assert any(m["author"] == "admin" for m in thread)

        # odpowiedź tradera otwiera ponownie; zamknięcie blokuje odpowiedzi
        assert c.post(f"/api/me/tickets/{tid}/reply", headers=h,
                      json={"message": "Still broken"}).json()["status"] == "open"
        c.post(f"/api/admin/tickets/{tid}/reply", headers=ADMIN_H, json={"close": True})
        assert c.post(f"/api/me/tickets/{tid}/reply", headers=h,
                      json={"message": "x"}).status_code == 400


def test_cudzy_ticket_niewidoczny():
    _, h1 = _trader("ticket-a@test.pl")
    _, h2 = _trader("ticket-b@test.pl")
    with TestClient(app) as c:
        tid = c.post("/api/me/tickets", headers=h1,
                     json={"subject": "prywatne", "message": "sekret"}).json()["id"]
        assert c.get(f"/api/me/tickets/{tid}", headers=h2).status_code == 404


# ---------------- settings ----------------
def test_zmiana_hasla():
    _, h = _trader("haslo@test.pl")
    with TestClient(app) as c:
        assert c.post("/api/me/password", headers=h,
                      json={"current_password": "zle", "new_password": "nowehaslo1"}).status_code == 400
        assert c.post("/api/me/password", headers=h,
                      json={"current_password": "haslo1234", "new_password": "nowehaslo1"}).status_code == 200
        assert c.post("/api/auth/login",
                      json={"email": "haslo@test.pl", "password": "nowehaslo1"}).status_code == 200


def test_delete_anonimizuje_i_uniewaznia_token():
    tid, h = _trader("kasacja@test.pl")
    with TestClient(app) as c:
        assert c.post("/api/me/delete", headers=h, json={"password": "haslo1234", "terms_accepted": True}).json()["deleted"]
        assert c.get("/api/auth/me", headers=h).status_code == 401, "token po delete ma być martwy"
        assert c.post("/api/auth/login",
                      json={"email": "kasacja@test.pl", "password": "haslo1234", "terms_accepted": True}).status_code == 401
    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert tr.email.endswith("@removed.invalid") and tr.full_name == "Deleted User"
    s.close()


def test_patch_prefsow_i_notify_pomija_mail():
    _, h = _trader("prefsy@test.pl")
    with TestClient(app) as c:
        r = c.patch("/api/me", headers=h, json={"notify_payouts": False, "full_name": "Nowe Imie"})
        assert r.json()["notify"]["payouts"] is False and r.json()["full_name"] == "Nowe Imie"
    assert notify._email_allowed("payout_approved", "prefsy@test.pl") is False
    assert notify._email_allowed("breached", "prefsy@test.pl") is True
    assert notify._email_allowed("payout_approved", "nieistnieje@test.pl") is True


# ---------------- activity / achievements / payouts ----------------
def test_activity_liczy_dni_i_transakcje_ze_snapshotow():
    tid, h = _trader("activity@test.pl")
    aid = _konto(tid, "880001")
    s = SessionLocal()
    t0 = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    dane = [("2026-07-20", 10_000), ("2026-07-20", 10_150), ("2026-07-21", 10_150),
            ("2026-07-21", 10_050), ("2026-07-21", 10_260)]
    for i, (dk, bal) in enumerate(dane):
        s.add(EquitySnapshot(account_id=aid, ts=t0 + timedelta(hours=i * 5),
                             balance=bal, equity=bal, open_pnl=0, day_key=dk))
    s.commit(); s.close()
    with TestClient(app) as c:
        r = c.get(f"/api/me/accounts/{aid}/activity", headers=h)
        _, h_obcy = _trader("activity-obcy@test.pl")
        obcy = c.get(f"/api/me/accounts/{aid}/activity", headers=h_obcy)
    assert r.status_code == 200 and obcy.status_code == 404
    days = {d["day"]: d["pnl"] for d in r.json()["days"]}
    # konto bez zapisanych transakcji -> dzienny wynik z roznicy sald w snapshotach
    assert days == {"2026-07-20": 150.0, "2026-07-21": 110.0}
    assert r.json()["ledger"] == []   # brak tradow i wyplat = pusta ksiega


def test_achievements_odblokowuja_sie_z_realnych_zdarzen():
    tid, h = _trader("badges@test.pl")
    with TestClient(app) as c:
        przed = {b["key"]: b["unlocked"] for b in c.get("/api/me/achievements", headers=h).json()["badges"]}
        assert przed["first_challenge"] is False
        s = SessionLocal()
        s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=89, status="paid"))
        s.commit(); s.close()
        po = {b["key"]: b["unlocked"] for b in c.get("/api/me/achievements", headers=h).json()["badges"]}
    assert po["first_challenge"] is True and po["funded"] is False


def test_payouts_podsumowanie():
    tid, h = _trader("payouty@test.pl")
    # +5% zysku, czyli poniżej progu skalowania (+15%). Skalowanie jest dziś
    # decyzją tradera, więc nic samo nie ruszy konta w trakcie testu.
    aid = _konto(tid, "880002", status="funded", balance=10_500, initial=10_000)
    s = SessionLocal()
    s.add(Payout(account_id=aid, profit_amount=500, trader_share=400, paid=True))
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=1000, trader_share=800,
                        status="pending"))
    s.commit(); s.close()
    with TestClient(app) as c:
        r = c.get("/api/me/payouts", headers=h).json()
    assert r["summary"]["total_paid"] == 400.0
    assert r["summary"]["pending"] == 1
    # konto funded tyka poller z sim feedem, ktory nadpisuje balans od initial —
    # dokladna wartosc "available" jest niedeterministyczna w tescie
    assert r["summary"]["available"] >= 0.0
    assert any(x["account"] == "880002" and x["status"] == "pending" for x in r["requests"])


# ---------------- KYC docs ----------------
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_kyc_upload_i_podglad_admina():
    tid, h = _trader("kycdocs@test.pl")
    with TestClient(app) as c:
        anon = c.post("/api/me/kyc/docs", files={"id_front": ("f.png", PNG, "image/png")})
        assert anon.status_code == 401
        r = c.post("/api/me/kyc/docs", headers=h,
                   files={"id_front": ("f.png", PNG, "image/png"),
                          "residence": ("r.pdf", b"%PDF-1.4 dane", "application/pdf")})
        assert r.status_code == 200 and set(r.json()["uploaded"]) == {"id_front", "residence"}
        zly = c.post("/api/me/kyc/docs", headers=h,
                     files={"id_front": ("f.gif", b"GIF89a", "image/gif")})
        assert zly.status_code == 400
        assert c.get(f"/api/admin/kyc/{tid}/doc/id_front", headers=ADMIN_H).status_code == 200
        assert c.get(f"/api/admin/kyc/{tid}/doc/id_front").status_code in (401, 403)
    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert tr.kyc_doc_front and tr.kyc_doc_residence and not tr.kyc_doc_back
    s.close()


def test_kyc_rozszerzony_formularz():
    tid, h = _trader("kycform@test.pl")
    with TestClient(app) as c:
        r = c.post("/api/me/kyc", headers=h, json={
            "full_name": "Kyc Formularz", "country": "Poland", "dob": "1990-05-01",
            "address": "ul. Testowa 1, Warszawa", "id_type": "Passport", "id_number": "AB1234567"})
        assert r.json()["kyc_status"] == "pending"
    s = SessionLocal()
    tr = s.get(Trader, tid)
    assert tr.kyc_id_type == "Passport" and tr.kyc_dob == "1990-05-01"
    assert tr.kyc_doc_ref == "AB1234567"
    s.close()


# ---------------- grant challenge (admin nadaje konto) ----------------
def test_admin_grant_tworzy_konto_bez_platnosci():
    tid, h = _trader("grant@test.pl", "Grant Owy")
    with TestClient(app) as c:
        lista = c.get("/api/admin/traders", headers=ADMIN_H)
        assert lista.status_code == 200
        assert any(t["id"] == tid for t in lista.json())
        assert c.get("/api/admin/traders").status_code in (401, 403)

        r = c.post("/api/admin/grant", headers=ADMIN_H,
                   json={"trader_id": tid, "product_key": "2step-25k", "note": "BOGO promotion"})
        assert r.status_code == 200 and r.json()["granted"] is True

        accs = c.get("/api/me/accounts", headers=h).json()
        acc = next(a for a in accs if a["product_key"] == "2step-25k")
        assert acc["source"] == "grant"
        assert acc["grant_note"] == "BOGO promotion"
        assert acc["initial_balance"] == 25_000

        orders = c.get("/api/orders", headers=h).json()
        o = next(o for o in orders if o["product_key"] == "2step-25k")
        assert o["amount_usd"] == 0 and o["provider"] == "grant" and o["status"] == "paid"


def test_grant_wymaga_admina_i_istniejacego_tradera():
    tid, h = _trader("grant-auth@test.pl")
    with TestClient(app) as c:
        assert c.post("/api/admin/grant", headers=h,
                      json={"trader_id": tid, "product_key": "2step-25k"}).status_code in (401, 403)
        assert c.post("/api/admin/grant", headers=ADMIN_H,
                      json={"trader_id": 999999, "product_key": "2step-25k"}).status_code == 404
        assert c.post("/api/admin/grant", headers=ADMIN_H,
                      json={"trader_id": tid, "product_key": "nie-ma"}).status_code == 404


def test_mail_grantu_ma_wersje_html_z_kwota_i_poswiadczeniami():
    html = notify._render_html("challenge_granted", {
        "name": "Ethan Walker", "initial_balance": 50_000, "steps": 2,
        "platform_server": "MetaQuotes-Demo", "platform_login": "11546513",
        "platform_password": "ichhl00j", "email": "ethan@example.com",
        "grant_note": "BOGO activation complete"}, "subj")
    assert "BOGO activation complete" in html
    assert "$50,000" in html and "2-Step challenge on MT5" in html
    assert "11546513" in html and "ichhl00j" in html and "MetaQuotes-Demo" in html
    assert "View Dashboard" in html
    # zwykle powiadomienia tez dostaja HTML we wspolnym layoucie z logo
    welcome = notify._render_html("welcome", {"name": "x"}, "s")
    assert welcome and "logo.png" in welcome


# --------------------------------------------------------------------------- #
#  BOGO — nigdzie nie piszemy „aktywowane przez nasz zespół"                   #
# --------------------------------------------------------------------------- #
_BOGO_CTX = {"name": "Ethan Walker", "initial_balance": 50_000, "steps": 2,
             "platform_server": "MetaQuotes-Demo", "platform_login": "11546513",
             "platform_password": "ichhl00j", "email": "ethan@example.com"}


def test_mail_bogo_mowi_o_upgradzie_a_nie_o_zespole():
    ctx = {**_BOGO_CTX, "bogo_paid_size": 25_000, "grant_note": "BOGO activation complete"}
    html = notify._render_html("challenge_granted", ctx, "subj")
    subject, body = notify._render("challenge_granted", ctx)

    assert "You paid for the $25K tier and we upgraded your allocation to $50,000" in html
    assert "Your upgraded challenge is live" in html
    assert "by our team" not in html and "by our team" not in body
    assert "upgraded your allocation to $50,000" in body
    assert "upgraded" in subject


def test_mail_bogo_bez_oplaconego_tieru_nie_sugeruje_platnosci():
    """Bez zapisanego tieru zdanie „you paid for..." byłoby zmyślone."""
    ctx = {**_BOGO_CTX, "grant_note": "BOGO promotion"}
    html = notify._render_html("challenge_granted", ctx, "subj")
    _, body = notify._render("challenge_granted", ctx)
    for tekst in (html, body):
        assert "buy one, get one free" in tekst
        assert "You paid for" not in tekst
        assert "by our team" not in tekst


def test_bogo_upgrade_tylko_gdy_tier_jest_mniejszy():
    assert notify._bogo_upgrade({"bogo_paid_size": 25_000, "initial_balance": 50_000})
    # ten sam rozmiar to nie upgrade — nie ogłaszamy podbicia, którego nie było
    assert not notify._bogo_upgrade({"bogo_paid_size": 50_000, "initial_balance": 50_000})
    assert not notify._bogo_upgrade({"initial_balance": 50_000})


# --------------------------------------------------------------------------- #
#  Certyfikaty wypłat wystawiane z panelu admina                              #
# --------------------------------------------------------------------------- #
def _konto_z_zyskiem(email, zysk=2_500.0):
    """Konto FUNDED — tylko z takiego wolno wystawić wypłatę. Duży kapitał trzyma
    procent zysku nisko, żeby te konta nie wypychały cudzych z top-20 rankingu."""
    KAPITAL = 200_000.0
    tid, h = _trader(email, "Payout Owy")
    s = SessionLocal()
    acc = Account(login=f"9{tid:08d}"[:9], trader_id=tid, trader_name="Payout Owy",
                  product_key="2step-50k", initial_balance=KAPITAL, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=KAPITAL + zysk, equity=KAPITAL + zysk, peak_equity=KAPITAL + zysk,
                  day_start_equity=KAPITAL + zysk, day_start_balance=KAPITAL + zysk)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return tid, aid, h


def test_admin_wystawia_wyplate_z_certyfikatem():
    _, aid, _ = _konto_z_zyskiem("payout-cert@test.pl")
    with TestClient(app) as c:
        podglad = c.get(f"/api/admin/accounts/{aid}/payouts", headers=ADMIN_H).json()
        assert podglad["profit"] == 2_500 and podglad["suggested_share"] == 2_000   # 80%

        r = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 2_000, "method": "crypto", "note": "1st payout"})
        assert r.status_code == 200
        p = r.json()
        assert p["trader_share"] == 2_000 and p["paid"] is True and p["cert_token"]
        assert p["cert_url"] == f"/payout/{p['cert_token']}"

        # certyfikat jest publiczny i pokazuje kwotę
        cert = c.get(p["cert_url"])
        assert cert.status_code == 200
        # Asercje pilnują WYMAGAŃ dokumentu, nie brzmienia copy — inaczej każda
        # zmiana tekstu marketingowego wywala test bez żadnej wartości.
        assert "$2,000<" in cert.text                     # okrągła kwota bez groszy
        assert "Payout Owy" in cert.text                  # komu wystawiono
        assert p["cert_token"] in cert.text               # numer certyfikatu przy QR
        assert f"/verify/{p['cert_token']}" in cert.text  # weryfikowalne na naszej stronie
        assert "<svg" in cert.text and "qrline" in cert.text   # kod QR do weryfikacji
        assert c.get("/payout/nie-ma-takiego").status_code == 404


def test_weryfikacja_dziala_dla_certyfikatu_wyplaty():
    """Numer z certyfikatu wypłaty MUSI przechodzić przez /verify — inaczej ktoś,
    komu trader go pokaże, dostaje „nie znaleziono"."""
    _, aid, _ = _konto_z_zyskiem("payout-verify@test.pl")
    with TestClient(app) as c:
        p = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 1_500}).json()
        r = c.get(f"/verify/{p['cert_token']}")
        assert r.status_code == 200
        assert "Certificate is valid" in r.text and "Payout paid" in r.text
        assert "$1,500.00" in r.text
        assert f'href="/payout/{p["cert_token"]}"' in r.text     # link do właściwego dokumentu
        # nazwisko na stronie weryfikacji zostaje maskowane
        assert "Payout Owy" not in r.text and "Payout O." in r.text
        # nieistniejący token dalej odbija się od weryfikacji
        assert "Certificate not found" in c.get("/verify/zmyslony-token").text


def test_historia_pokazuje_wyplate_i_nie_falszuje_salda():
    """Regresja: saldo w historii liczone WSTECZ od bieżącego przesuwało całą
    historię o wypłaconą kwotę, a samej wypłaty w ogóle nie było widać."""
    tid, h = _trader("ledger@test.pl", "Ledger Owy")
    s = SessionLocal()
    acc = Account(login="900500", trader_id=tid, trader_name="Ledger Owy",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  profit_split_pct=80, status="funded", phase="funded",
                  balance=50_000, equity=50_000, peak_equity=50_000,
                  day_start_equity=50_000, day_start_balance=50_000)
    s.add(acc); s.commit(); accid = acc.id
    baza = datetime(2026, 7, 27, 10, 0)
    # dwa zyskowne trady, potem wypłata cofająca saldo do kapitału startowego
    for i, (pnl, saldo) in enumerate([(300.0, 50_300.0), (200.0, 50_500.0)]):
        s.add(Trade(account_id=accid, symbol="XAUUSD", side="buy", lots=0.5,
                    open_price=2385.0, close_price=2391.0, pnl=pnl, status="closed",
                    opened_at=baza + timedelta(minutes=i * 10),
                    closed_at=baza + timedelta(minutes=i * 10 + 5)))
        s.add(EquitySnapshot(account_id=accid, ts=baza + timedelta(minutes=i * 10 + 5),
                             balance=saldo, equity=saldo, day_key="2026-07-27"))
    s.add(Payout(account_id=accid, ts=baza + timedelta(hours=1), profit_amount=500.0,
                 trader_share=400.0, paid=True))
    s.add(EquitySnapshot(account_id=accid, ts=baza + timedelta(hours=1), balance=50_000.0,
                         equity=50_000.0, day_key="2026-07-27"))
    s.commit(); s.close()

    with TestClient(app) as c:
        act = c.get(f"/api/me/accounts/{accid}/activity", headers=h).json()

    ksiega = act["ledger"]
    assert [r["kind"] for r in ksiega] == ["payout", "trade", "trade"]   # od najnowszego
    wyplata = ksiega[0]
    assert wyplata["pnl"] == -500.0 and wyplata["trader_share"] == 400.0
    assert wyplata["balance"] == 50_000.0
    # salda transakcji NIE są przesunięte o wypłatę — biorą się ze snapshotów
    assert ksiega[1]["balance"] == 50_500.0
    assert ksiega[2]["balance"] == 50_300.0

    # kalendarz liczy dzień z TRANSAKCJI, więc wypłata go nie zeruje
    dzien = {d["day"]: d["pnl"] for d in act["days"]}
    assert dzien["2026-07-27"] == 500.0


def test_admin_nadaje_konto_od_razu_funded():
    tid, h = _trader("grant-funded@test.pl", "Od Razu Funded")
    with TestClient(app) as c:
        r = c.post("/api/admin/grant", headers=ADMIN_H,
                   json={"trader_id": tid, "product_key": "2step-50k", "funded": True})
        assert r.status_code == 200 and r.json()["phase"] == "funded"
        acc = next(a for a in c.get("/api/me/accounts", headers=h).json()
                   if a["product_key"] == "2step-50k")
        assert acc["phase"] == "funded" and acc["status"] == "funded"


def test_kyc_zapisuje_kraj_i_oddaje_go_do_formularza():
    """Kraj jest wybierany z listy — formularz musi umiec podswietlic zapisany."""
    _, h = _trader("kyc-kraj@test.pl")
    with TestClient(app) as c:
        assert c.get("/api/auth/me", headers=h).json()["kyc_country"] is None
        c.post("/api/me/kyc", headers=h,
               json={"full_name": "Jan Kowalski", "country": "United States"})
        assert c.get("/api/auth/me", headers=h).json()["kyc_country"] == "United States"


def test_admin_recznie_breachuje_konto():
    from app.models import Breach
    _, aid, h = _konto_z_zyskiem("breach-recznie@test.pl")
    with TestClient(app) as c:
        assert c.post(f"/api/admin/accounts/{aid}/breach", json={"reason": "x"}).status_code in (401, 403)

        r = c.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN_H,
                   json={"reason": "News trading during NFP"})
        assert r.status_code == 200 and r.json()["status"] == "failed"
        acc = c.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()
        assert acc["status"] == "failed" and acc["breach_reason"] == "News trading during NFP"
        # powod zostaje w historii breachy, nie tylko w polu na koncie
        assert any(b["type"] == "manual" and "NFP" in b["detail"] for b in acc["breaches"])
        # drugi raz sie nie da
        assert c.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN_H,
                      json={}).status_code == 400

        # cofniecie fazy otwiera konto z powrotem
        c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "eval_1"}, headers=ADMIN_H)
        assert c.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["status"] == "active"


def test_breach_bez_powodu_ma_domyslny_opis():
    _, aid, _ = _konto_z_zyskiem("breach-pusty@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN_H, json={"reason": "   "})
        assert r.status_code == 200 and r.json()["reason"], "pusty powod nie moze dac pustego opisu"


def test_breach_zatrzymuje_trade_bota():
    """Zamkniete konto nie moze dalej byc rozgrywane przez bota."""
    _, aid, _ = _konto_z_zyskiem("breach-bot@test.pl")
    with TestClient(app) as c:
        c.post(f"/api/admin/accounts/{aid}/bot", json={"pace": "demo"}, headers=ADMIN_H)
        assert c.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()["bot_enabled"] is True
        c.post(f"/api/admin/accounts/{aid}/breach", headers=ADMIN_H, json={"reason": "test"})
        d = c.get(f"/api/accounts/{aid}", headers=ADMIN_H).json()
    assert d["status"] == "failed" and d["bot_enabled"] is False


def test_admin_recznie_przestawia_faze():
    _, aid, _ = _konto_z_zyskiem("phase-move@test.pl")
    with TestClient(app) as c:
        r = c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "eval_2"}, headers=ADMIN_H)
        assert r.status_code == 200 and r.json()["phase"] == "eval_2"
        assert c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "kosmos"},
                      headers=ADMIN_H).status_code == 400
        assert c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "funded"}).status_code in (401, 403)

    # awans resetuje metryki tak samo jak automatyczny
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.balance == acc.initial_balance and acc.trading_days_count == 0
    s.close()


def test_qr_prowadzi_na_host_z_ktorego_serwowano_dokument():
    """QR bierze adres z ŻĄDANIA, nie z APP_BASE_URL. Inaczej po wdrożeniu na
    domenę — gdyby ktoś zapomniał przestawić konfig — kod QR na wydanych
    certyfikatach prowadziłby na localhost."""
    _, aid, _ = _konto_z_zyskiem("payout-qr@test.pl")
    with TestClient(app, base_url="https://mypropfunds.example") as c:
        p = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 900}).json()
        cert = c.get(p["cert_url"]).text
    assert f"https://mypropfunds.example/verify/{p['cert_token']}" in cert
    assert "localhost" not in cert and "127.0.0.1" not in cert


def test_certyfikat_nie_gubi_groszy_przy_niecalej_kwocie():
    _, aid, _ = _konto_z_zyskiem("payout-grosze@test.pl")
    with TestClient(app) as c:
        p = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 1049.78}).json()
        assert "$1,049.78" in c.get(p["cert_url"]).text


def test_certyfikat_nie_pokazuje_numeru_konta_ani_rozbicia_wyplaty():
    """Dokument idzie na zewnątrz: numer rachunku MT5 nikomu tam nie służy,
    a zysk/split/metoda to dane wewnętrzne rachunku."""
    _, aid, _ = _konto_z_zyskiem("payout-prywatnosc@test.pl")
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.login = acc.platform_login = "998877665"
    s.commit(); s.close()

    with TestClient(app) as c:
        p = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                   json={"amount": 1_000, "method": "crypto"}).json()
        cert = c.get(p["cert_url"]).text

    assert "998877665" not in cert, "numer rachunku MT5 wyciekł na certyfikat"
    for zbedne in ("Profit generated", "Profit split", "Method", "Crypto"):
        assert zbedne not in cert, f"'{zbedne}' nie powinno byc na certyfikacie"
    # rozmiar konta zostaje — to ranga osiągnięcia, nie dane dostępowe
    assert "$200,000" in cert

    # wypłacony zysk znika z konta — inaczej dałoby się go wypłacić w kółko
    s = SessionLocal()
    acc = s.get(Account, aid)
    assert acc.balance == acc.initial_balance == 200_000
    assert s.query(Payout).filter(Payout.account_id == aid).count() == 1
    s.close()


def test_wyplaty_tylko_z_konta_funded():
    """Z konta w ewaluacji nie ma czego wypłacać — zysk nie jest jeszcze zarobiony."""
    tid, h = _trader("payout-challenge@test.pl", "W Ewaluacji")
    s = SessionLocal()
    acc = Account(login="900700", trader_id=tid, trader_name="W Ewaluacji",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  profit_split_pct=80, status="active", phase="eval_1",
                  balance=53_000, equity=53_000, peak_equity=53_000,
                  day_start_equity=53_000, day_start_balance=53_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    with TestClient(app) as c:
        r = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H, json={"amount": 100})
        assert r.status_code == 400 and "funded" in r.text
        # po awansie na funded ta sama wypłata przechodzi
        c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "funded"}, headers=ADMIN_H)
        assert c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                      json={"amount": 100}).status_code == 200


def test_certyfikaty_za_kazdy_etap_osobno():
    """Osobny, weryfikowalny dokument za etap 1, etap 2 i funded."""
    tid, h = _trader("cert-etapy@test.pl", "Etapy Owy")
    s = SessionLocal()
    acc = Account(login="900800", trader_id=tid, trader_name="Etapy Owy",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  profit_split_pct=80, status="active", phase="eval_1",
                  balance=50_000, equity=50_000, peak_equity=50_000,
                  day_start_equity=50_000, day_start_balance=50_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    with TestClient(app) as c:
        # konto w etapie 1 — nic jeszcze nie osiagnelo
        dost = {x["kind"]: x["available"] for x in
                c.get(f"/api/admin/accounts/{aid}/certificates", headers=ADMIN_H).json()}
        assert dost == {"phase_1": False, "phase_2": False, "funded": False}
        r = c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "phase_1"}, headers=ADMIN_H)
        assert r.status_code == 400, "nie wolno wystawic certyfikatu za nieosiagniety etap"

        # po awansie na etap 2 mozna wystawic certyfikat za etap 1
        c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "eval_2"}, headers=ADMIN_H)
        p1 = c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "phase_1"},
                    headers=ADMIN_H).json()
        assert p1["token"] and c.get(p1["url"]).status_code == 200
        assert "Phase 1 passed" in c.get(p1["url"]).text

        # po funded dochodza dwa kolejne, kazdy z wlasnym numerem
        c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "funded"}, headers=ADMIN_H)
        p2 = c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "phase_2"},
                    headers=ADMIN_H).json()
        fu = c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "funded"},
                    headers=ADMIN_H).json()
        assert len({p1["token"], p2["token"], fu["token"]}) == 3
        assert "Phase 2 passed" in c.get(p2["url"]).text
        assert "Funded trader" in c.get(fu["url"]).text

        # ponowne wystawienie nie zmienia numeru — raz udostepniony link zyje dalej
        assert c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "funded"},
                      headers=ADMIN_H).json()["token"] == fu["token"]
        # kazdy weryfikuje sie osobno
        for cert in (p1, p2, fu):
            v = c.get(f"/verify/{cert['token']}")
            assert "Certificate is valid" in v.text
        assert c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "xxx"},
                      headers=ADMIN_H).status_code == 400
        assert c.post(f"/api/admin/accounts/{aid}/certificate", json={"kind": "funded"}).status_code in (401, 403)


def test_trader_sam_wystawia_swoje_certyfikaty():
    """Zakladka Certificates w portalu: trader widzi i pobiera wlasne dokumenty."""
    tid, h = _trader("moje-certy@test.pl", "Moje Certy")
    s = SessionLocal()
    acc = Account(login="900900", trader_id=tid, trader_name="Moje Certy",
                  product_key="2step-50k", initial_balance=50_000, steps=2,
                  profit_split_pct=80, status="active", phase="eval_1",
                  balance=50_000, equity=50_000, peak_equity=50_000,
                  day_start_equity=50_000, day_start_balance=50_000)
    s.add(acc); s.commit(); aid = acc.id; s.close()

    with TestClient(app) as c:
        d = c.get("/api/me/certificates", headers=h).json()
        moje = next(x for x in d["accounts"] if x["account_id"] == aid)
        assert [i["available"] for i in moje["items"]] == [False, False, False]
        # nie wolno wystawic za nieosiagniety etap
        assert c.post("/api/me/certificates", headers=h,
                      json={"account_id": aid, "kind": "phase_1"}).status_code == 400

        c.post(f"/api/admin/accounts/{aid}/phase", json={"phase": "funded"}, headers=ADMIN_H)
        r = c.post("/api/me/certificates", headers=h, json={"account_id": aid, "kind": "funded"})
        assert r.status_code == 200 and c.get(r.json()["url"]).status_code == 200

        # cudze konto jest niewidoczne i nie da sie na nim nic wystawic
        _, obcy = _trader("obcy-certy@test.pl")
        assert all(x["account_id"] != aid for x in
                   c.get("/api/me/certificates", headers=obcy).json()["accounts"])
        assert c.post("/api/me/certificates", headers=obcy,
                      json={"account_id": aid, "kind": "funded"}).status_code == 404

        # wyplata bez tokenu — trader dorabia go sobie sam
        pay = c.post(f"/api/admin/accounts/{aid}/payout", headers=ADMIN_H,
                     json={"amount": 250}).json()
        s = SessionLocal()
        p = s.get(Payout, pay["id"]); p.cert_token = None; s.commit(); s.close()
        d = c.get("/api/me/certificates", headers=h).json()
        assert d["payouts"] and d["payouts"][0]["url"] is None
        r = c.post(f"/api/me/payouts/{pay['id']}/certificate", headers=h)
        assert r.status_code == 200 and c.get(r.json()["url"]).status_code == 200
        assert c.post(f"/api/me/payouts/{pay['id']}/certificate", headers=obcy).status_code == 404


def test_faktura_pokazuje_cene_z_cennika_i_rozbicie_bogo():
    """Grant BOGO ma na fakturze cene tieru, za ktory klient zaplacil — nie zero."""
    tid, h = _trader("faktura-bogo@test.pl", "Faktura Owy")
    with TestClient(app) as c:
        c.post("/api/admin/grant", headers=ADMIN_H,
               json={"trader_id": tid, "product_key": "2step-50k",
                     "note": "BOGO promotion", "bogo_paid_key": "2step-25k"})
        o = next(x for x in c.get("/api/orders", headers=h).json()
                 if x["product_key"] == "2step-50k")

    assert o["list_price"] and o["list_price"] > 0          # cena planu z cennika
    assert o["bogo_paid_label"] and o["bogo_paid_price"] > 0
    assert o["bogo_paid_size"] == 25_000 and o["account_size"] == 50_000
    # kwota zamowienia = cena OPLACONEGO tieru, a nie 0
    assert o["amount_usd"] == o["bogo_paid_price"]


def test_grant_bez_oplaconego_tieru_ma_cene_z_cennika_do_pokazania():
    """Stare granty maja w bazie 0 USD. Faktura nie moze pokazywac zera, wiec
    front spada na `list_price` — dlatego API musi ta cene oddawac."""
    tid, h = _trader("faktura-grant@test.pl", "Grant Owy")
    with TestClient(app) as c:
        c.post("/api/admin/grant", headers=ADMIN_H,
               json={"trader_id": tid, "product_key": "2step-50k", "note": "BOGO promotion"})
        o = next(x for x in c.get("/api/orders", headers=h).json()
                 if x["product_key"] == "2step-50k")
    assert o["amount_usd"] == 0
    assert o["list_price"] > 0 and o["product_label"]


def test_wyplata_wymaga_admina_i_dodatniej_kwoty():
    _, aid, h = _konto_z_zyskiem("payout-guard@test.pl", zysk=0.0)
    with TestClient(app) as c:
        assert c.post(f"/api/admin/accounts/{aid}/payout", json={"amount": 100},
                      headers=h).status_code in (401, 403)
        # konto bez zysku i bez podanej kwoty -> czytelny 400, nie wypłata na zero
        assert c.post(f"/api/admin/accounts/{aid}/payout", json={},
                      headers=ADMIN_H).status_code == 400
        assert c.post(f"/api/admin/accounts/{aid}/payout", json={"amount": -5},
                      headers=ADMIN_H).status_code == 400
        assert c.post("/api/admin/accounts/999999/payout", json={"amount": 10},
                      headers=ADMIN_H).status_code == 404


def test_certyfikat_mozna_dorobic_do_starej_wyplaty():
    """Wypłaty z zatwierdzonych wniosków nie mają tokenu — panel musi go dorobić."""
    _, aid, _ = _konto_z_zyskiem("payout-stara@test.pl")
    s = SessionLocal()
    stara = Payout(account_id=aid, profit_amount=1_000, trader_share=800, paid=True)
    s.add(stara); s.commit(); pid = stara.id
    assert stara.cert_token is None
    s.close()

    with TestClient(app) as c:
        r = c.post(f"/api/admin/payouts/{pid}/certificate", headers=ADMIN_H)
        assert r.status_code == 200 and r.json()["cert_token"]
        assert c.get(r.json()["cert_url"]).status_code == 200
        # ponowne wywołanie nie zmienia tokenu (link zostaje stabilny)
        assert c.post(f"/api/admin/payouts/{pid}/certificate",
                      headers=ADMIN_H).json()["cert_token"] == r.json()["cert_token"]


def test_usuniecie_konta_nie_lamie_kluczy_obcych():
    """Regresja: `session.delete(acc)` wywalało 500 na Postgresie, bo orders.account_id
    ma klucz obcy. Lokalnie przechodziło, bo SQLite domyślnie FK ignoruje."""
    tid, h = _trader("delete-fk@test.pl", "Do Skasowania")
    with TestClient(app) as c:
        r = c.post("/api/admin/grant", headers=ADMIN_H,
                   json={"trader_id": tid, "product_key": "2step-25k", "note": "BOGO promotion"})
        aid = r.json()["account_id"]

        s = SessionLocal()
        s.add(Trade(account_id=aid, symbol="XAUUSD", side="buy", lots=0.1,
                    open_price=2385.0, close_price=2390.0, pnl=50.0, status="closed"))
        # kazda z tych tabel ma FK na accounts.id — jeden pozostawiony wiersz
        # i DELETE konta konczyl sie 500 (ForeignKeyViolation)
        s.add(EquitySnapshot(account_id=aid, equity=10_050, balance=10_050))
        s.add(Breach(account_id=aid, type="daily_dd", detail="test", equity_at_breach=9_400))
        s.add(Certificate(account_id=aid, kind="phase_1", cert_token="DELFK1TOKEN"))
        s.add(Payout(account_id=aid, profit_amount=500, trader_share=400, paid=True))
        s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=500,
                            trader_share=400, status="paid"))
        s.commit(); s.close()

        assert c.delete(f"/api/accounts/{aid}", headers=ADMIN_H).status_code == 200

    s = SessionLocal()
    assert s.get(Account, aid) is None
    for model in (Trade, EquitySnapshot, Breach, Certificate, Payout, PayoutRequest):
        assert s.query(model).filter(model.account_id == aid).count() == 0
    # zamówienie to dokument płatności — przeżywa konto, tylko traci powiązanie
    zam = s.query(Order).filter(Order.trader_id == tid).all()
    assert zam and all(o.account_id is None for o in zam)
    s.close()


def test_usuniecie_konta_NIE_zwalnia_slotu_w_puli():
    from app.models import PoolAccount
    tid, _ = _trader("delete-pool@test.pl")
    s = SessionLocal()
    acc = Account(login="900001", trader_id=tid, trader_name="Pool", product_key="2step-25k",
                  initial_balance=10_000, balance=10_000, equity=10_000, peak_equity=10_000,
                  day_start_equity=10_000, day_start_balance=10_000, status="active")
    s.add(acc); s.commit(); aid = acc.id
    s.add(PoolAccount(metaapi_account_id="MT-DEL-1", platform_login="1", platform_password="x",
                      platform_server="S", account_size=10_000, claimed=True,
                      claimed_by_account_id=aid))
    s.commit(); s.close()

    with TestClient(app) as c:
        assert c.delete(f"/api/accounts/{aid}", headers=ADMIN_H).status_code == 200

    # Rachunek MT5 byl juz w rekach tradera — ma historie transakcji i zna go
    # poprzedni wlasciciel. Powrot do puli oznaczalby, ze nowy klient dostaje
    # cudze konto, a stary zachowuje do niego dzialajace haslo.
    s = SessionLocal()
    pool = s.query(PoolAccount).filter(PoolAccount.metaapi_account_id == "MT-DEL-1").first()
    assert pool.claimed is True
    assert pool.retired_reason, "wpis ma nosic powod wycofania"
    s.close()


def test_grant_zapisuje_oplacony_tier_na_koncie():
    tid, h = _trader("bogo-grant@test.pl", "Bogo Owy")
    with TestClient(app) as c:
        r = c.post("/api/admin/grant", headers=ADMIN_H,
                   json={"trader_id": tid, "product_key": "2step-50k",
                         "note": "BOGO promotion", "bogo_paid_key": "2step-25k"})
        assert r.status_code == 200
        acc = next(a for a in c.get("/api/me/accounts", headers=h).json()
                   if a["product_key"] == "2step-50k")
        # portal potrzebuje tej kwoty, żeby napisać „$25K -> $50,000"
        assert acc["bogo_paid_size"] == 25_000

        # nieistniejący tier odrzucamy zamiast zapisywać śmieć
        assert c.post("/api/admin/grant", headers=ADMIN_H,
                      json={"trader_id": tid, "product_key": "2step-50k",
                            "bogo_paid_key": "nie-ma"}).status_code == 404


def test_kyc_reject_i_historia_decyzji():
    """Admin widzi pending + historię (approved/rejected); reject pozwala
    traderowi wysłać wniosek ponownie."""
    tid, h = _trader("kyc-hist@test.pl", "Historia Kyc")
    with TestClient(app) as c:
        c.post("/api/me/kyc", headers=h, json={"full_name": "Historia Kyc",
                                               "country": "PL", "id_type": "passport"})
        d = c.get("/api/admin/kyc", headers=ADMIN_H).json()
        assert any(t["trader_id"] == tid for t in d["pending"])

        r = c.post(f"/api/admin/kyc/{tid}/reject", headers=ADMIN_H)
        assert r.status_code == 200
        d = c.get("/api/admin/kyc", headers=ADMIN_H).json()
        assert not any(t["trader_id"] == tid for t in d["pending"])
        wpis = next(t for t in d["history"] if t["trader_id"] == tid)
        assert wpis["status"] == "rejected" and wpis["reviewed_at"]

        # trader może poprawić i wysłać ponownie -> wraca do pending
        c.post("/api/me/kyc", headers=h, json={"full_name": "Historia Kyc",
                                               "country": "PL", "id_type": "passport"})
        d = c.get("/api/admin/kyc", headers=ADMIN_H).json()
        assert any(t["trader_id"] == tid for t in d["pending"])

        c.post(f"/api/admin/kyc/{tid}/approve", headers=ADMIN_H)
        d = c.get("/api/admin/kyc", headers=ADMIN_H).json()
        wpis = next(t for t in d["history"] if t["trader_id"] == tid)
        assert wpis["status"] == "approved"


def test_ui_prefs_zapisywane_na_koncie():
    """PATCH /api/me przyjmuje ui_prefs (JSON, np. sortowanie tabel) i oddaje
    je w /api/auth/me — preferencja trzyma się konta, nie przeglądarki."""
    tid, h = _trader("uiprefs@test.pl", "Prefs Tester")
    with TestClient(app) as c:
        assert c.get("/api/auth/me", headers=h).json()["ui_prefs"] == {}
        # sortowanie tabel i filtr zakładki Challenges żyją w JEDNYM blobie —
        # zapis jednego klucza nie może zgubić drugiego
        r = c.patch("/api/me", headers=h,
                    json={"ui_prefs": {"sort": {"portal.orders": [2, -1]},
                                       "chalFilter": "funded"}})
        assert r.status_code == 200
        assert (c.get("/api/auth/me", headers=h).json()["ui_prefs"]
                == {"sort": {"portal.orders": [2, -1]}, "chalFilter": "funded"})
        # cap 2000 znaków — zbyt duży blob odrzucamy zamiast ucinać
        za_duzo = {"sort": {f"tabela-{i}": [i, 1] for i in range(300)}}
        assert c.patch("/api/me", headers=h,
                       json={"ui_prefs": za_duzo}).status_code == 400
        # PATCH bez ui_prefs nie kasuje zapisanych preferencji
        c.patch("/api/me", headers=h, json={"full_name": "Prefs Tester"})
        assert (c.get("/api/auth/me", headers=h).json()["ui_prefs"]
                == {"sort": {"portal.orders": [2, -1]}, "chalFilter": "funded"})
        # izolacja: preferencje nie przeciekają między traderami
        _tid2, h2 = _trader("uiprefs2@test.pl", "Prefs Dwa")
        assert c.get("/api/auth/me", headers=h2).json()["ui_prefs"] == {}


def test_kyc_reject_z_powodem_widocznym_dla_tradera():
    """Powód odrzucenia KYC trafia do /api/auth/me (portal go pokazuje)
    i jest czyszczony przy approve."""
    tid, h = _trader("kyc-powod@test.pl", "Powod Kyc")
    with TestClient(app) as c:
        c.post("/api/me/kyc", headers=h, json={"full_name": "Powod Kyc",
                                               "country": "PL", "id_type": "passport"})
        r = c.post(f"/api/admin/kyc/{tid}/reject", headers=ADMIN_H,
                   json={"reason": "Document expired"})
        assert r.status_code == 200 and r.json()["reason"] == "Document expired"
        me = c.get("/api/auth/me", headers=h).json()
        assert me["kyc_status"] == "rejected"
        assert me["kyc_reject_reason"] == "Document expired"

        c.post(f"/api/admin/kyc/{tid}/approve", headers=ADMIN_H)
        me = c.get("/api/auth/me", headers=h).json()
        assert me["kyc_reject_reason"] is None


def test_otwarte_pozycje_konta_widzi_tylko_wlasciciel():
    """GET /api/me/accounts/{id}/positions — tabela "Open trades" w portalu.

    Zwraca wylacznie wiersze Trade ze status='open' (dzis pisze je bot);
    zamkniete transakcje maja swoja historie w /activity. Cudze konto = 404,
    bez tokenu = 401 — endpoint nie moze wyciekac pozycji innych traderow.
    """
    tid, h = _trader("pozycje@test.pl", "Otwarte Pozycje")
    aid = _konto(tid, "880011")
    s = SessionLocal()
    s.add(Trade(account_id=aid, symbol="XAUUSD", side="buy", lots=0.5,
                open_price=2400.0, pnl=37.5, status="open", source="bot"))
    s.add(Trade(account_id=aid, symbol="EURUSD", side="sell", lots=1.0,
                open_price=1.09, close_price=1.08, pnl=100.0, status="closed",
                closed_at=datetime.now(timezone.utc), source="bot"))
    s.commit(); s.close()
    _, h_obcy = _trader("pozycje-obcy@test.pl", "Obcy Trader")
    with TestClient(app) as c:
        moje = c.get(f"/api/me/accounts/{aid}/positions", headers=h)
        cudze = c.get(f"/api/me/accounts/{aid}/positions", headers=h_obcy)
        anonim = c.get(f"/api/me/accounts/{aid}/positions")
    assert moje.status_code == 200
    rows = moje.json()
    assert len(rows) == 1, "zamknieta transakcja nie moze trafic do Open trades"
    p = rows[0]
    assert p["symbol"] == "XAUUSD" and p["side"] == "buy" and p["lots"] == 0.5
    assert p["pnl"] == 37.5 and p["ticket"] and p["opened_at"]
    assert cudze.status_code == 404
    assert anonim.status_code == 401


# ---------------- weryfikacja adresu e-mail ----------------
def test_signup_wysyla_kod_i_weryfikacja_kodem_dziala():
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={
            "email": "verify-code@test.pl", "password": "haslo1234", "terms_accepted": True, "full_name": "Vera"})
        assert r.status_code == 200
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        assert c.get("/api/auth/me", headers=h).json()["email_verified"] is False
        s = SessionLocal()
        tr = s.query(Trader).filter(Trader.email == "verify-code@test.pl").first()
        code = tr.email_verify_code
        s.close()
        assert code and len(code) == 6
        zly = "000000" if code != "000000" else "111111"
        assert c.post("/api/me/verify-email", headers=h, json={"code": zly}).status_code == 400
        assert c.post("/api/me/verify-email", headers=h, json={"code": code}).status_code == 200
        assert c.get("/api/auth/me", headers=h).json()["email_verified"] is True


def test_weryfikacja_linkiem_dziala_bez_logowania():
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={
            "email": "verify-link@test.pl", "password": "haslo1234", "terms_accepted": True, "full_name": "Vera"})
        tid = r.json()["trader"]["id"]
        assert c.post("/api/auth/verify-email",
                      json={"token": auth.make_verify_token(tid)}).status_code == 200
        s = SessionLocal()
        tr = s.get(Trader, tid)
        assert tr.email_verified is True and tr.email_verify_code is None
        s.close()
        assert c.post("/api/auth/verify-email", json={"token": "zly"}).status_code == 400


def test_resend_generuje_nowy_kod():
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={
            "email": "verify-resend@test.pl", "password": "haslo1234", "terms_accepted": True, "full_name": "Vera"})
        tid = r.json()["trader"]["id"]
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        s = SessionLocal(); stary = s.get(Trader, tid).email_verify_code; s.close()
        assert c.post("/api/me/verify-email/resend", headers=h).status_code == 200
        s = SessionLocal(); nowy = s.get(Trader, tid).email_verify_code; s.close()
        assert nowy and len(nowy) == 6 and nowy != stary


def test_mail_verify_email_ma_szablon_html_z_kodem():
    html = notify._render_html("verify_email", {"name": "x", "code": "123456",
                                                "verify_url": "https://x/portal?verify=t"}, "s")
    assert html and "123456" in html and "logo.png" in html and "verify=t" in html


def test_mail_credits_granted_ma_szablon_html():
    html = notify._render_html("credits_granted", {"name": "x", "amount": 50, "balance": 75}, "s")
    assert html and "logo.png" in html and "view=store" in html


# ---------------- plan skalowania: wybor zamiast automatu ----------------
def test_skalowanie_ofertowane_dopiero_od_progu_i_tylko_na_funded():
    """`scale_offer` to jedyne miejsce liczace, czy wyzszy plan przysluguje."""
    from app import poller

    class _Acc:
        def __init__(self, status, initial, balance, steps=2):
            self.status, self.initial_balance, self.balance = status, initial, balance
            self.steps = steps

    assert poller.scale_offer(_Acc("funded", 100_000, 114_999.99)) is None
    # DOKLADNIE na progu: 100000 * 1.15 to w floatach 114999.99999999999, wiec bez
    # zaokraglenia obu stron konto na progu nie dostawaloby oferty.
    assert poller.scale_offer(_Acc("funded", 100_000, 115_000)) == 200_000
    # Krok to nastepny tier z CENNIKA, a nie procent — dlatego 300k, nie 450k.
    assert poller.scale_offer(_Acc("funded", 200_000, 500_000)) == 300_000
    # Instant ma wlasna drabine tej samej dlugosci, ale rodzin nie mieszamy.
    assert poller.scale_offer(_Acc("funded", 25_000, 99_000, steps=0)) == 50_000
    # Konta wyskalowane starym mechanizmem siedza na rozmiarach spoza katalogu.
    assert poller.scale_offer(_Acc("funded", 150_000, 200_000)) == 200_000
    # Szczyt drabiny: 2M nie ma dokad rosnac.
    assert poller.scale_offer(_Acc("funded", 2_000_000, 3_000_000)) is None
    assert poller.scale_offer(_Acc("active", 100_000, 200_000)) is None
    assert poller.scale_offer(_Acc("breached", 100_000, 200_000)) is None


def test_skalowanie_wgrywa_caly_nastepny_plan_a_nie_sam_rozmiar():
    """Konto po skalowaniu ma byc ZWYKLYM planem z cennika.

    Stara wersja podnosila samo `initial_balance` o 50%, zostawiajac
    `product_key` i `max_lots` z poprzedniego tieru — konto 150k handlowalo z
    limitem wolumenu konta 100k i nie odpowiadalo zadnej pozycji w ofercie.
    """
    from app import poller
    from app.models import Product

    tid, _ = _trader("skalowanie-plan@test.pl")
    aid = _konto(tid, "990105", status="funded", balance=115_000, initial=100_000)
    with TestClient(app) as c:      # lifespan zasiewa katalog produktow
        c.get("/api/products")
        s = SessionLocal()
        acc = s.get(Account, aid)
        stary_login = acc.login
        nowy = poller.apply_scale_up(s, acc)
        s.commit()

        cel = s.query(Product).filter(Product.key == "2step-200k").first()
        assert nowy == 200_000 and acc.initial_balance == 200_000
        assert acc.product_key == "2step-200k" and acc.preset == "2step-200k"
        assert acc.max_lots == cel.max_lots, "limit wolumenu musi isc za rozmiarem"
        assert acc.profit_split_pct == cel.profit_split_pct
        assert acc.max_overall_loss_pct == cel.max_overall_loss_pct
        # od zera, ale od razu funded
        assert acc.phase == "funded" and acc.balance == 200_000
        assert acc.peak_equity == 200_000 and acc.day_start_equity == 200_000
        assert acc.trading_days_count == 0 and acc.best_day_profit == 0.0
        # nowy rachunek MT5: stary numer trzyma stare saldo u brokera
        assert acc.login != stary_login
        assert acc.platform_login is None and acc.platform_password is None
        assert acc.status == "provisioning" and acc.scale_count == 1
        s.close()


def test_skalowanie_nie_wpada_w_breach_gdy_feed_poda_saldo_konta():
    """REGRESJA na to, co realnie zabijalo konta po skalowaniu.

    Saldo nie nalezy do nas — feed czyta je z brokera (a SimulatedFeed trzyma
    wlasny stan PER LOGIN). Stara wersja podnosila `initial_balance` w bazie i
    zostawiala ten sam login, wiec na nastepnym ticku wracalo stare saldo, a
    prog max DD liczony od nowego rozmiaru stal juz nad nim: konto szlo w breach
    zamiast urosnac. Tu przechodzimy pelna sciezke — skalowanie, provisioning,
    kilka tickow — i konto ma zyc.
    """
    import asyncio

    from app import poller, provisioning
    from app.feed import SimulatedFeed

    feed = SimulatedFeed()
    tid, _ = _trader("skalowanie-feed@test.pl")
    aid = _konto(tid, "990106", status="funded", balance=100_000, initial=100_000)
    with TestClient(app) as c:
        c.get("/api/products")
        s = SessionLocal()
        acc = s.get(Account, aid)
        acc.phase = "funded"
        s.commit()
        # feed poznaje konto pod STARYM loginem i zapamietuje jego stan
        asyncio.run(poller.process_account(s, acc, feed))
        acc.balance = 115_000.0
        acc.equity = 115_000.0
        acc.open_pnl = 0.0
        s.commit()

        poller.apply_scale_up(s, acc)
        s.commit()
        asyncio.run(provisioning.provision_pending(SessionLocal, feed))
        s.refresh(acc)
        assert acc.status == "funded", "po provisioningu konto ma byc handlowalne"
        assert acc.platform_login and acc.platform_password

        for _ in range(5):
            asyncio.run(poller.process_account(s, acc, feed))
            s.refresh(acc)
        assert acc.status == "funded" and not acc.breach_reason
        assert acc.balance > 150_000, "feed musi podawac saldo NOWEGO konta, nie starego"
        s.close()


def test_endpoint_scale_up_wymaga_progu_i_konta_funded():
    tid, h = _trader("skalowanie@test.pl")
    maly = _konto(tid, "990101", status="funded", balance=10_200, initial=10_000)
    obcy_tid, _ = _trader("skalowanie-obcy@test.pl")
    obce = _konto(obcy_tid, "990102", status="funded", balance=20_000, initial=10_000)
    najwiekszy = _konto(tid, "990107", status="funded", balance=3_000_000, initial=2_000_000)
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{maly}/scale-up", headers=h)
        assert r.status_code == 400 and "unlocks" in r.json()["detail"]
        assert c.post(f"/api/accounts/{obce}/scale-up", headers=h).status_code == 404
        # szczyt drabiny dostaje INNY komunikat: to nie jest brak progu
        r = c.post(f"/api/accounts/{najwiekszy}/scale-up", headers=h)
        assert r.status_code == 400 and "largest" in r.json()["detail"]


def test_endpoint_scale_up_wgrywa_wyzszy_plan_i_widac_to_w_api():
    tid, h = _trader("skalowanie-ok@test.pl")
    aid = _konto(tid, "990103", status="funded", balance=11_500, initial=10_000)
    with TestClient(app) as c:
        konta = {a["id"]: a for a in c.get("/api/me/accounts", headers=h).json()}
        # 10k nie ma juz w ofercie, wiec kolejny szczebel to najmniejszy plan: 25k
        assert konta[aid]["scale_up_to"] == 25_000, "oferta musi byc widoczna w payloadzie"
        assert konta[aid]["scale_trigger_pct"] == 15.0
        r = c.post(f"/api/accounts/{aid}/scale-up", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["previous_size"] == 10_000 and d["new_size"] == 25_000
        assert d["balance"] == 25_000, "saldo startuje od rozmiaru nowego planu"
        assert d["product_key"] == "2step-25k" and d["status"] == "provisioning"
        # druga proba pod rzad nie ma prawa przejsc: konto jest dopiero na starcie
        assert c.post(f"/api/accounts/{aid}/scale-up", headers=h).status_code == 400
        # odznaka rozpoznaje skalowanie po liczniku, nie po rozmiarze konta
        odznaki = {b["key"]: b for b in c.get("/api/me/achievements", headers=h).json()["badges"]}
        assert odznaki["scaled"]["unlocked"] is True


def test_otwarta_pozycja_blokuje_skalowanie():
    """Zysk otwartej pozycji rozliczylby sie juz wobec NOWEGO konta."""
    tid, h = _trader("skalowanie-pozycja@test.pl")
    aid = _konto(tid, "990108", status="funded", balance=115_000, initial=100_000)
    s = SessionLocal()
    acc = s.get(Account, aid)
    acc.open_pnl = 240.0
    s.commit()
    s.close()
    with TestClient(app) as c:
        r = c.post(f"/api/accounts/{aid}/scale-up", headers=h)
        assert r.status_code == 400 and "Close your open positions" in r.json()["detail"]


def test_mail_account_scaled_ma_szablon():
    ctx = {"name": "x", "login": "990104", "previous_size": 10_000, "new_size": 25_000}
    tresc = notify._render("account_scaled", ctx)
    assert "25,000" in tresc[0] and "25,000" in tresc[1]
    # mail NIE moze zapraszac do handlu: konto czeka na poswiadczenia
    assert "separate email" in tresc[1]
    html = notify._render_html("account_scaled", ctx, "s")
    assert html and "25,000" in html and "view=accounts" in html


# ---------------- szerokość pudełek w portalu ----------------
def test_pudelka_w_widoku_maja_jedna_miare_szerokosci():
    """Kanarek na rozjazd, którego z serwera nie widać.

    Na jednej zakładce sąsiadowały ze sobą: rząd kafelków (pełna szerokość),
    tabela 1400px, karta 1450px, lista kont 1560px i pusty stan 820px. Efekt:
    „No payout requests yet" kończyło się w ~72% szerokości obszaru treści,
    podczas gdy kafelki nad nim dochodziły do krawędzi.
    """
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "portal.css").read_text()

    assert "--wrap:" in css, "jedna miara szerokosci dla widokow"
    assert ".content>:where(*){max-width:var(--wrap)}" in css, (
        "miara musi obejmowac KAZDE pudelko najwyzszego poziomu — wyliczona lista "
        "klas zostawia poza nia bezklasowe <div>-y naglowkow zakladek")
    # Zapis przez `.content`, nie `#view`: identyfikator podbilby specyficznosc
    # do (1,0,0) i skasowal wyjatki zapisane dwiema klasami.
    assert "#view>:where(*)" not in css
    for wyjatek in (".tbl-wrap.tw-wide{max-width:none}", ".sec-card.card-sm{max-width:1030px}"):
        assert wyjatek in css, f"celowy wyjatek musi przezyc: {wyjatek}"

    assert "max-width:820px" not in css, "stary limit pustego stanu"
    assert "minmax(215px,340px)" not in css, (
        "cap na kafelku dawal odwrotny rozjazd: tabela szersza niz rzad nad nia")


def test_kyc_i_support_nie_trzymaja_szerokosci_inline():
    """Inline `style="max-width:…"` bije każdą regułę arkusza — te sześć
    atrybutów było jedynym powodem, dla którego KYC i Support nie dochodziły
    do krawędzi, i żadna zmiana w CSS by ich nie ruszyła."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates" / "portal.html").read_text()
    for szer in ("max-width:560px", "max-width:760px", "max-width:720px"):
        assert szer not in html, f"pozostal inline limit {szer}"
    # 640px zostaje WYŁĄCZNIE w ustawieniach — te karty siedzą w układzie
    # wielokolumnowym (.card-cols), gdzie wąska kolumna jest zamierzona.
    # Prefiks bez cudzysłowu, bo „Danger Zone" dokłada w tym samym atrybucie
    # jeszcze kolor obramowania.
    assert html.count('style="max-width:640px') == 5


def test_pas_upsellu_dzieli_sie_rowno_na_dwa_rzedy():
    """`auto-fill` upychał tyle kafelków, ile weszło — przy dziewięciu planach
    dawało to 6+3, czyli pełny pierwszy rząd i ogon w drugim.

    Liczba kafelków zależy od konta (to plany WIĘKSZE od obecnego rozmiaru),
    więc podział musi wychodzić równo dla dowolnej liczby, nie tylko dziewięciu.
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates" / "portal.html").read_text()
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "portal.css").read_text()

    assert "const kolumn=fam.length<=4?fam.length:Math.ceil(fam.length/2)" in html, (
        "liczbe kolumn liczy widok — 9 -> 5+4, 8 -> 4+4, 7 -> 4+3, <=4 -> jeden rzad")
    assert 'style="--up-cols:${kolumn};--up-max:${rzadMax}px"' in html
    assert ".upsell-row{grid-template-columns:repeat(var(--up-cols,4),minmax(0,1fr));max-width:var(--up-max,none)}" in css

    # Konto blisko szczytu oferty ma jeden albo dwa wieksze plany. Bez gornego
    # limitu szerokosci rzedu grid rozrzucal je po calej szerokosci panelu
    # (jeden kafelek na 1510px), bo kolumny sa `1fr`.
    assert "const rzadMax=kolumn*300" in html

    # ten sam bursztyn co w sklepie i na landingu — jeden jezyk dla „Best value"
    assert ".upsell-card.pop" in css and ".uc-ribbon" in css
    assert "#f0b95c" in css and "#fbbf4e,#f2860f" in css
    assert "uc-ribbon" in html and "p.popular?' pop'" in html

    # odstepy: 12px bylo za ciasno, kafelki zlewaly sie w pas
    assert "gap:12px;overflow-x:auto" not in css
    assert ".upsell-row{display:flex;gap:26px" in css


def test_kafelki_upsellu_sa_zwarte_i_odsuniete_od_listy_kont():
    """Pas upsellu byl wyzszy od samej listy kont (539px kontra 90px) i stykal
    sie z nia bez zadnego odstepu.

    Ubytek wysokosci bierze sie z usunietego powtorzenia, nie ze zmniejszonego
    tekstu: obramowany przycisk „Upgrade" w kazdym z dziewieciu kafelkow i
    plakietka „+5,2%", identyczna wszedzie i podana juz w podtytule panelu.
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates" / "portal.html").read_text()
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "portal.css").read_text()

    # odstep od listy kont wiekszy niz zwykly odstep miedzy kartami (14px)
    assert ".upsell{margin-top:34px;margin-bottom:18px}" in css

    # caly kafelek klikalny — takze z klawiatury
    assert 'role="button" tabindex="0" onclick="openBuy(' in html
    assert "event.key==='Enter'||event.key===' '" in html
    assert '<button class="btn-o sm" onclick="openBuy(' not in html, (
        "przycisk w kazdym kafelku odpowiadal za wiekszosc jego wysokosci")

    # zdjeta plakietka procentu — ta sama liczba na wszystkich kafelkach
    assert "uc-pct" not in html and "uc-pct" not in css
    assert 'Based on your <b class="up">+${pct.toFixed(2)}%</b>' in html, (
        "procent musi zostac w podtytule, skoro znika z kafelkow")

    # kolumna moze byc szeroka, kafelek nie — nadmiar idzie w odstep
    assert ".upsell-card{flex:none;width:100%;max-width:260px}" in css
    assert "justify-items:center" in css


def test_historia_transakcji_idzie_stronami():
    """Lista rosła bez końca — po kilkudziesięciu trejdach podgląd konta był
    jedną długą tabelą.

    Kluczowa rzecz do utrzymania: wiersze zostają W CAŁOŚCI w DOM i są tylko
    ukrywane. Sortowanie kolumn (sortable.js) przestawia wiersze tabeli, więc
    gdyby renderowana była jedna strona, kliknięcie nagłówka posortowałoby
    wyłącznie te kilkanaście widocznych wierszy zamiast całej historii.
    """
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "templates" / "portal.html").read_text()
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "portal.css").read_text()

    assert "const TX_PER_PAGE=15" in html
    assert "function txPage(" in html and "function txPager(" in html and "function txInit(" in html
    assert 'id="tx-tbl"' in html and 'id="tx-pager"' in html
    # cala historia w DOM, strony robione widocznoscia wierszy. Od czasu filtra
    # dnia z kalendarza najpierw chowamy WSZYSTKIE wiersze, potem odslaniamy
    # strone z tych, ktore przeszly filtr.
    assert "wszystkie.forEach(tr=>{tr.style.display='none'})" in html
    assert "rows.forEach((tr,i)=>{if(i>=od&&i<od+TX_PER_PAGE)tr.style.display=''})" in html
    # po sortowaniu „pierwsza strona" znaczy co innego — wracamy na nia
    assert "tbl._txObs=new MutationObserver(()=>txPage(1))" in html
    # tabela zostaje sortowalna
    assert 'id="tx-tbl" class="tbl sortable" data-tkey="portal.history"' in html
    for regula in (".pager{", ".pg-btn{", ".pg-btn.on{", ".pg-dots{"):
        assert regula in css, f"brak stylu {regula}"


def test_serwer_oddaje_wiecej_niz_sto_wpisow_historii():
    """Limit 100 był niewidoczny, dopóki lista była nieskończona — przy
    stronicowaniu stałby się realnym końcem historii konta."""
    from app.main import LEDGER_MAX
    assert LEDGER_MAX >= 300


def test_klasa_flagi_nie_zderza_sie_z_etykieta_pliku_w_kyc():
    """Picker numeru kierunkowego wprowadził globalną klasę `.fl` (kafelek flagi
    20x15px). KYC od dawna używał `class="fl"` na etykiecie pola pliku
    (`.file-row .fl`), więc reguła flagi zgniatała etykiety do 20x15px — tekst
    łamał się po jednym słowie i wylatywał poza kartę.

    Klasa flagi musi być opisowa, żeby kolizja nie wróciła.
    """
    from pathlib import Path
    baza = Path(__file__).resolve().parents[1]
    html = (baza / "templates" / "portal.html").read_text()
    pcss = (baza / "static" / "css" / "portal.css").read_text()
    fcss = (baza / "static" / "css" / "flags.css").read_text()
    gen = (baza.parent / "scripts" / "gen_countries.py").read_text()

    # zadnej dwuliterowej reguly globalnej
    assert "\n.fl{" not in pcss and "\n.fl{" not in fcss, "globalne `.fl` znow istnieje"
    assert "\n.fl-" not in fcss, "flagi krajow musza uzywac dlugiej nazwy"

    # picker uzywa nowej nazwy w kazdym z trzech miejsc
    assert '<span class="flag" id="c-cc-flag"></span>' in html
    assert 'f.className="flag flag-"+c.i.toLowerCase()' in html
    assert '<span class="flag flag-${c.i.toLowerCase()}"></span>' in html
    assert "\n.flag{" in pcss and "\n.flag{" in fcss
    assert fcss.count("\n.flag-") >= 200, "flagi krajow musza byc w pliku"

    # generator ma emitowac to samo, inaczej nastepne odswiezenie cofnie zmiane
    assert ".flag-{iso2.lower()}" in gen and '".flag{width:20px' in gen

    # etykieta w KYC dalej ma swoja klase i swoje reguly
    assert '<div class="file-row"><div class="fl">' in html
    assert ".file-row .fl b" in pcss and ".file-row .fl p" in pcss


def test_klikniety_dzien_kalendarza_filtruje_historie_transakcji():
    """Kalendarz pokazywał wynik dnia, ale nie dawało się zobaczyć, z czego on
    wyszedł — lista pod spodem szła całą historią niezależnie od kliknięcia.

    Filtr musi wchodzić w to samo miejsce co stronicowanie, inaczej licznik
    stron liczyłby wiersze spoza wybranego dnia.
    """
    from pathlib import Path
    baza = Path(__file__).resolve().parents[1]
    html = (baza / "templates" / "portal.html").read_text()
    css = (baza / "static" / "css" / "portal.css").read_text()

    # klikalne WYLACZNIE dni z obrotem
    assert "cls+=' has'+(key===window._calSel?' sel':'')" in html
    assert 'onclick="calPick(\'${key}\')"' in html
    assert "event.key==='Enter'||event.key===' '" in html

    # przelacznik, nie przejscie w jedna strone
    assert "window._calSel=(window._calSel===day)?null:day" in html
    assert "function calClear(){window._calSel=null;calRender();txPage(1)}" in html

    # filtr po `data-sort`, czyli po tym samym polu, po ktorym sortuje tabela
    assert "wszystkie.filter(tr=>tr.cells[0]&&tr.cells[0].dataset.sort===dzien)" in html
    # stronicowanie liczy PRZEFILTROWANE wiersze
    assert "const stron=Math.max(1,Math.ceil(rows.length/TX_PER_PAGE));" in html
    # najpierw chowamy wszystko — inaczej zostalyby wiersze z poprzedniego dnia
    assert "wszystkie.forEach(tr=>{tr.style.display='none'})" in html

    # wybrany dzien nie przechodzi na inne konto
    assert "window._calSel=null;\n" in html

    # widoczny znacznik filtra i wyjscie z niego
    assert '<div id="tx-filter" class="tx-filter"></div>' in html
    assert 'onclick="calClear()"' in html
    assert ".cal-day.sel" in css and ".cal-day.has{cursor:pointer" in css
    assert "#tx-card{scroll-margin-top:92px}" in css


# ---------------- nagrody za progi odznak (3/8, 5/8, 8/8) ----------------
def _odblokuj(tid: int, ile: int) -> None:
    """Odblokowuje `ile` odznak najtańszymi realnymi zdarzeniami.

    Kolejność jest ta sama co w `achievements.badges`, więc test nie zgaduje —
    idzie po tych samych warunkach, które liczy serwer.
    """
    s = SessionLocal()
    tr = s.get(Trader, tid)
    if ile >= 1:  # first_challenge
        s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=299, status="paid"))
    if ile >= 8:  # kyc
        tr.kyc_status = "approved"
    if ile >= 7:  # referrer
        s.add(Trader(email=f"polecony-{tid}@test.pl", password_hash="x",
                     referred_by=tr.referral_code, referral_code=auth.secrets.token_hex(3)))
    acc = None
    if ile >= 2:  # phase_passed + funded + days_5 + scaled na jednym koncie
        acc = Account(login=f"ach-{tid}", trader_id=tid, trader_name="Ach Test",
                      product_key="2step-10k", initial_balance=10_000, balance=10_000,
                      equity=10_000, peak_equity=10_000, day_start_equity=10_000,
                      day_start_balance=10_000, status="passed", phase="eval_2")
        if ile >= 3:
            acc.status, acc.phase = "funded", "funded"
        if ile >= 5:
            acc.trading_days_count = 5
        if ile >= 6:
            acc.scale_count = 1
        s.add(acc)
    s.commit()
    aid = acc.id if acc is not None else None
    s.close()
    if ile >= 4 and aid:  # first_payout
        s = SessionLocal()
        s.add(Payout(account_id=aid, profit_amount=500.0, trader_share=400.0, paid=True))
        s.commit(); s.close()


def test_nagroda_za_prog_odznak_wymaga_progu_i_idzie_raz():
    tid, h = _trader("ach-prog@test.pl")
    _odblokuj(tid, 3)
    with TestClient(app) as c:
        stan = c.get("/api/me/achievements", headers=h).json()
        assert stan["unlocked"] == 3, stan["badges"]
        progi = {r["tier"]: r for r in stan["rewards"]}
        assert progi[3]["status"] == "ready"
        assert progi[5]["status"] == "locked" and progi[5]["remaining"] == 2
        assert progi[8]["status"] == "locked" and progi[8]["remaining"] == 5

        # prog wyzszy niz zdobyte odznaki — serwer liczy sam, nie wierzy przegladarce
        za_wysoko = c.post("/api/me/achievements/claim", json={"tier": 8}, headers=h)
        assert za_wysoko.status_code == 400

        r = c.post("/api/me/achievements/claim", json={"tier": 3}, headers=h)
        assert r.status_code == 200
        kod = r.json()["code"]
        assert kod and kod.startswith("PF-")

        # drugi raz ta sama nagroda — odbita
        znow = c.post("/api/me/achievements/claim", json={"tier": 3}, headers=h)
        assert znow.status_code == 409

        po = c.get("/api/me/achievements", headers=h).json()
        odebrana = next(x for x in po["rewards"] if x["tier"] == 3)
        assert odebrana["status"] == "claimed" and odebrana["code"] == kod


def test_kody_z_odznak_maja_wlasciwy_procent_i_sa_unikalne():
    from app.models import RewardCode
    kody = []
    for i, (prog, pct) in enumerate(((3, 20.0), (5, 25.0))):
        tid, h = _trader(f"ach-pct{prog}@test.pl")
        _odblokuj(tid, prog)
        with TestClient(app) as c:
            r = c.post("/api/me/achievements/claim", json={"tier": prog}, headers=h)
        assert r.status_code == 200, r.text
        s = SessionLocal()
        k = s.query(RewardCode).filter(RewardCode.code == r.json()["code"]).one()
        assert k.pct == pct
        assert k.trader_id == tid, "kod musi byc wystawiony na tego tradera"
        assert k.points_spent == 0, "nagroda z odznak nie kosztuje punktow"
        assert k.used_at is None
        kody.append(k.code)
        s.close()
    assert len(set(kody)) == 2, "kody musza byc rozne"


def test_komplet_odznak_daje_darmowy_challenge_50k():
    tid, h = _trader("ach-komplet@test.pl")
    _odblokuj(tid, 8)
    with TestClient(app) as c:
        stan = c.get("/api/me/achievements", headers=h).json()
        assert stan["unlocked"] == 8, stan["badges"]
        r = c.post("/api/me/achievements/claim", json={"tier": 8}, headers=h)
        assert r.status_code == 200, r.text
        konto = r.json()["account"]
    assert konto and konto["account_size"] == 50_000
    assert r.json()["code"] is None, "za komplet idzie konto, nie kod"
    s = SessionLocal()
    acc = s.get(Account, konto["account_id"])
    zam = s.get(Order, konto["order_id"])
    assert acc.trader_id == tid
    assert zam.provider == "grant" and zam.amount_usd == 0.0, "darmowy challenge to grant na $0"
    s.close()
    # drugi komplet nie da drugiego konta
    with TestClient(app) as c:
        assert c.post("/api/me/achievements/claim", json={"tier": 8}, headers=h).status_code == 409
