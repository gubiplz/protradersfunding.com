"""KYC: upload dokumentów do BAZY (nie na dysk — Vercel ma read-only FS),
podgląd w adminie serwowany z bazy, walidacja MIME/rozmiaru, pełny flow
formularz -> pending -> widoczny w kolejce admina.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, KycFile, Trader  # noqa: E402

init_db()
client = TestClient(app)

ADMIN = {"X-Admin-Token": get_settings().admin_token}
LICZNIK = iter(range(1000))

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 40          # nagłówek JPEG + śmieci
PDF = b"%PDF-1.4 test"


def _trader(funded: bool = True):
    """Trader z kontem funded — weryfikacja otwiera sie dopiero po ewaluacji
    (`main.kyc_dostepne`), wiec bez konta kazdy tutejszy test dostalby 403."""
    email = f"kycup{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo1234"),
                full_name="Kyc Uploader", referral_code=auth.secrets.token_hex(3))
    s.add(tr); s.flush()
    tid = tr.id
    if funded:
        s.add(_konto_funded(tid))
    s.commit()
    s.close()
    return tid, {"Authorization": f"Bearer {auth.make_token(tid)}"}


def _konto_funded(trader_id: int) -> Account:
    login = f"77{next(LICZNIK):05d}"
    return Account(login=login, trader_id=trader_id, trader_name="Kyc Uploader",
                   product_key="2step-50k", preset="2step-50k", initial_balance=50_000.0,
                   steps=2, phase="funded", status="funded", balance=50_000.0,
                   equity=50_000.0, peak_equity=50_000.0, day_start_equity=50_000.0,
                   day_start_balance=50_000.0)


def test_upload_jpeg_i_pdf_laduje_w_bazie():
    tid, h = _trader()
    r = client.post("/api/me/kyc/docs", headers=h, files={
        "id_front": ("front.jpg", JPEG, "image/jpeg"),
        "residence": ("bill.pdf", PDF, "application/pdf"),
    })
    assert r.status_code == 200
    assert sorted(r.json()["uploaded"]) == ["id_front", "residence"]
    s = SessionLocal()
    rows = {f.kind: f for f in s.query(KycFile).filter(KycFile.trader_id == tid).all()}
    assert set(rows) == {"id_front", "residence"}
    assert rows["id_front"].data == JPEG and rows["id_front"].mime == "image/jpeg"
    assert rows["residence"].data == PDF and rows["residence"].mime == "application/pdf"
    # nazwa pliku zapisana też na traderze (kolejka KYC pokazuje, co wgrano)
    tr = s.get(Trader, tid)
    assert tr.kyc_doc_front and tr.kyc_doc_front.endswith(".jpg")
    s.close()


def test_reupload_nadpisuje_a_nie_dokleja():
    tid, h = _trader()
    for bajty in (b"\xff\xd8\xff\xe0pierwszy", b"\xff\xd8\xff\xe0drugi"):
        r = client.post("/api/me/kyc/docs", headers=h,
                        files={"id_front": ("f.jpg", bajty, "image/jpeg")})
        assert r.status_code == 200
    s = SessionLocal()
    rows = s.query(KycFile).filter(KycFile.trader_id == tid,
                                   KycFile.kind == "id_front").all()
    assert len(rows) == 1 and rows[0].data.endswith(b"drugi")
    s.close()


def test_admin_podglad_serwuje_z_bazy():
    tid, h = _trader()
    client.post("/api/me/kyc/docs", headers=h,
                files={"id_back": ("back.png", b"\x89PNG\r\n" + b"p" * 10, "image/png")})
    r = client.get(f"/api/admin/kyc/{tid}/doc/id_back", headers=ADMIN)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(b"\x89PNG")
    # bez tokenu admina dokumentów nie ma
    assert client.get(f"/api/admin/kyc/{tid}/doc/id_back").status_code in (401, 403)
    # brak dokumentu => 404, nie 500
    assert client.get(f"/api/admin/kyc/{tid}/doc/residence",
                      headers=ADMIN).status_code == 404


def test_zly_mime_i_pusty_request_to_400():
    tid, h = _trader()
    r = client.post("/api/me/kyc/docs", headers=h,
                    files={"id_front": ("x.heic", b"heic", "image/heic")})
    assert r.status_code == 400 and "image/heic" in r.json()["detail"]
    assert client.post("/api/me/kyc/docs", headers=h).status_code == 400
    s = SessionLocal()
    assert s.query(KycFile).filter(KycFile.trader_id == tid).count() == 0
    s.close()


def test_formularz_do_pending_i_kolejki_admina():
    tid, h = _trader()
    r = client.post("/api/me/kyc", headers=h, json={
        "full_name": "Jan Testowy", "country": "Poland",
        "dob": "1990-01-01", "address": "Testowa 1", "id_type": "Passport",
        "id_number": "AB123456",
    })
    assert r.status_code == 200 and r.json()["kyc_status"] == "pending"
    kolejka = client.get("/api/admin/kyc", headers=ADMIN).json()
    assert any(t["trader_id"] == tid for t in kolejka["pending"])


def test_bez_konta_funded_weryfikacja_zamknieta():
    """Weryfikacja otwiera sie po przejsciu ewaluacji: mniej pustych zgloszen do
    przejrzenia i mniej zebranych dokumentow tozsamosci. Bramka musi stac na OBU
    wejsciach — formularz i wysylka plikow — inaczej skany wchodza bokiem."""
    _, h = _trader(funded=False)

    r = client.post("/api/me/kyc", headers=h, json={
        "full_name": "Bez Konta", "country": "Poland", "dob": "01/01/1990",
        "address": "ul. Testowa 1", "id_type": "passport", "id_number": "X1"})
    assert r.status_code == 403

    d = client.post("/api/me/kyc/docs", headers=h,
                    files={"id_front": ("f.jpg", JPEG, "image/jpeg")})
    assert d.status_code == 403, "skanow nie wolno przyjac z pominieciem formularza"

    s = SessionLocal()
    assert s.query(KycFile).count() >= 0            # nic nie wybuchlo
    s.close()
    assert client.get("/api/auth/me", headers=h).json()["kyc_available"] is False


def test_konto_po_breachu_dalej_otwiera_weryfikacje():
    """Konto, ktore zdobylo funded i potem zlamalo regule, ma phase='funded'
    i status='breached'. Taki trader moze miec nierozliczona wyplate, wiec
    odciecie go od KYC zablokowaloby mu wlasne pieniadze."""
    tid, h = _trader(funded=False)
    s = SessionLocal()
    acc = _konto_funded(tid)
    acc.status = "breached"
    s.add(acc); s.commit(); s.close()

    assert client.get("/api/auth/me", headers=h).json()["kyc_available"] is True
    r = client.post("/api/me/kyc", headers=h, json={
        "full_name": "Po Breachu", "country": "Poland", "dob": "01/01/1990",
        "address": "ul. Testowa 2", "id_type": "passport", "id_number": "X2"})
    assert r.status_code == 200
