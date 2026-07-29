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
from app.models import (Account, EquitySnapshot, Order, Payout, PayoutRequest,  # noqa: E402
                        Trade, Trader)

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
                  product_key="2step-10k", initial_balance=initial, balance=balance, equity=balance,
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
        assert c.post("/api/me/delete", headers=h, json={"password": "haslo1234"}).json()["deleted"]
        assert c.get("/api/auth/me", headers=h).status_code == 401, "token po delete ma być martwy"
        assert c.post("/api/auth/login",
                      json={"email": "kasacja@test.pl", "password": "haslo1234"}).status_code == 401
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
        przed = {b["key"]: b["unlocked"] for b in c.get("/api/me/achievements", headers=h).json()}
        assert przed["first_challenge"] is False
        s = SessionLocal()
        s.add(Order(trader_id=tid, product_key="2step-10k", amount_usd=89, status="paid"))
        s.commit(); s.close()
        po = {b["key"]: b["unlocked"] for b in c.get("/api/me/achievements", headers=h).json()}
    assert po["first_challenge"] is True and po["funded"] is False


def test_payouts_podsumowanie():
    tid, h = _trader("payouty@test.pl")
    # +5% zysku — poniżej progu skalowania (+10%), żeby poller z sim feedem
    # nie podbił initial_balance w trakcie testu (_maybe_scale)
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
                   json={"trader_id": tid, "product_key": "2step-10k", "note": "BOGO promotion"})
        assert r.status_code == 200 and r.json()["granted"] is True

        accs = c.get("/api/me/accounts", headers=h).json()
        acc = next(a for a in accs if a["product_key"] == "2step-10k")
        assert acc["source"] == "grant"
        assert acc["grant_note"] == "BOGO promotion"
        assert acc["initial_balance"] == 10_000

        orders = c.get("/api/orders", headers=h).json()
        o = next(o for o in orders if o["product_key"] == "2step-10k")
        assert o["amount_usd"] == 0 and o["provider"] == "grant" and o["status"] == "paid"


def test_grant_wymaga_admina_i_istniejacego_tradera():
    tid, h = _trader("grant-auth@test.pl")
    with TestClient(app) as c:
        assert c.post("/api/admin/grant", headers=h,
                      json={"trader_id": tid, "product_key": "2step-10k"}).status_code in (401, 403)
        assert c.post("/api/admin/grant", headers=ADMIN_H,
                      json={"trader_id": 999999, "product_key": "2step-10k"}).status_code == 404
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
    # zwykle powiadomienia zostaja plain-textem
    assert notify._render_html("welcome", {"name": "x"}, "s") is None


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
                   json={"trader_id": tid, "product_key": "2step-10k", "note": "BOGO promotion"})
        aid = r.json()["account_id"]

        s = SessionLocal()
        s.add(Trade(account_id=aid, symbol="XAUUSD", side="buy", lots=0.1,
                    open_price=2385.0, close_price=2390.0, pnl=50.0, status="closed"))
        s.commit(); s.close()

        assert c.delete(f"/api/accounts/{aid}", headers=ADMIN_H).status_code == 200

    s = SessionLocal()
    assert s.get(Account, aid) is None
    assert s.query(Trade).filter(Trade.account_id == aid).count() == 0
    # zamówienie to dokument płatności — przeżywa konto, tylko traci powiązanie
    zam = s.query(Order).filter(Order.trader_id == tid).all()
    assert zam and all(o.account_id is None for o in zam)
    s.close()


def test_usuniecie_konta_NIE_zwalnia_slotu_w_puli():
    from app.models import PoolAccount
    tid, _ = _trader("delete-pool@test.pl")
    s = SessionLocal()
    acc = Account(login="900001", trader_id=tid, trader_name="Pool", product_key="2step-10k",
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


def test_zmiana_full_name_propaguje_na_konta_i_certyfikaty():
    """Zmiana nazwiska w ustawieniach ma byc widoczna na certyfikatach —
    dokument renderuje acc.trader_name, wiec PATCH /api/me robi write-through."""
    from datetime import datetime, timezone
    from app.models import Certificate
    tid, h = _trader("rename@ca.pl", full_name="Old Name")
    aid = _konto(tid, "660001")
    s = SessionLocal()
    s.add(Certificate(account_id=aid, kind="phase1", cert_token="tok-rename-1",
                      issued_at=datetime.now(timezone.utc)))
    s.commit(); s.close()

    with TestClient(app) as c:
        r = c.patch("/api/me", headers=h, json={"full_name": "New Fancy Name"})
        assert r.status_code == 200 and r.json()["full_name"] == "New Fancy Name"

        s = SessionLocal()
        assert s.get(Account, aid).trader_name == "New Fancy Name"
        s.close()
        page = c.get("/certificate/tok-rename-1").text
        assert "New Fancy Name" in page and "Old Name" not in page
        ver = c.get("/api/verify/tok-rename-1").json()
        assert ver["trader_name"] == "New Fancy Name"

        # pusta nazwa NIE czysci dokumentow
        c.patch("/api/me", headers=h, json={"full_name": "  "})
        s = SessionLocal()
        assert s.get(Account, aid).trader_name == "New Fancy Name"
        s.close()
