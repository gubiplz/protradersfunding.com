"""Przełącznik „imported shown/hidden" na zakładce Payouts.

Zakładka pokazywała 65 wierszy, z czego 62 to konta archiwalne (import ewidencji
i Payout BOT — adres `@imported.local`, za którym nie stoi żaden klient). Trzy
prawdziwe wnioski traderów tonęły w tym tak samo, jak tonęły na listach ludzi,
zanim dostały ten sam przełącznik.

Tu jest jedna różnica wobec pozostałych zakładek i jest celowa: filtr liczy się
w PRZEGLĄDARCE, nie na serwerze. `/api/admin/payouts` to księga pieniędzy i
jedyna droga do wystawienia certyfikatu, więc ma oddawać komplet; poza tym pełna
lista w pamięci pozwala napisać „62 imported hidden" zamiast „3 of 3", które
wyglądałoby jak utrata bazy.

Test naprawdę uruchamia `renderPayoutsView()` (Node + minimalny shim DOM,
`panel_payouts_harness.mjs`), bo asercja „w kodzie jest taki napis" przeszłaby
także dla kodu, który się nie wykonuje albo liczy źle.
"""
import inspect
import json
import pathlib
import shutil
import subprocess

import pytest

KATALOG = pathlib.Path(__file__).resolve().parent
HARNESS = KATALOG / "panel_payouts_harness.mjs"
PANEL = KATALOG.parent / "static" / "js" / "admin-panel.js"

ARCHIWALNY_CSV = {
    "kind": "payout", "id": 1, "ts": "2026-05-01T10:00:00", "account_id": 11,
    "account_login": "90001", "trader_email": "john.smith@imported.local",
    "profit_amount": 4000, "trader_share": 3200, "method": "bank transfer",
    "details": {}, "status": "paid", "reject_reason": None,
    "note": "imported from records", "express": False,
    "cert_url": "/payout/abc", "show_on_lp": True,
}
ARCHIWALNY_BOT = {**ARCHIWALNY_CSV, "id": 2, "ts": "2026-06-01T10:00:00",
                  "account_login": "90002", "trader_email": "amy.lane@imported.local",
                  "note": "payout bot", "cert_url": "/payout/def"}
PRAWDZIWY_WNIOSEK = {
    "kind": "request", "id": 8, "ts": "2026-09-18T04:28:04", "account_id": 13,
    "account_login": "90003", "trader_email": "geolsarki@gmail.com",
    "profit_amount": 900, "trader_share": 720, "method": "usdt",
    "details": {"network": "TRC20", "address": "T123"}, "status": "pending",
    "reject_reason": None, "note": None, "cert_url": None, "express": False,
}


def _render(wiersze, *, imported_shown):
    """Zwraca HTML, który widok naprawdę wypluł."""
    node = shutil.which("node")
    if not node:
        pytest.skip("brak node — test rysuje prawdziwy widok, nie szuka napisów")
    wynik = subprocess.run(
        [node, str(HARNESS), str(PANEL), json.dumps(wiersze),
         "1" if imported_shown else "0"],
        capture_output=True, text=True, timeout=60)
    assert wynik.returncode == 0, (
        f"harness zwrócił {wynik.returncode}: {wynik.stderr.strip()[:400]}")
    return wynik.stdout


# --------------------------------------------------------------------------- #
#  Co widać przy każdym ustawieniu przełącznika
# --------------------------------------------------------------------------- #
def test_ukryte_zabieraja_wiersze_archiwalne_ale_nie_wniosek():
    """Sedno: znikają konta `@imported.local`, zostaje człowiek z prawdziwym mailem."""
    html = _render([ARCHIWALNY_CSV, ARCHIWALNY_BOT, PRAWDZIWY_WNIOSEK],
                   imported_shown=False)

    assert "geolsarki@gmail.com" in html, \
        "wniosek prawdziwego tradera nie ma prawa zniknąć — to jedyna robota do zrobienia"
    assert "john.smith@imported.local" not in html
    assert "amy.lane@imported.local" not in html


def test_licznik_mowi_ile_schowano():
    """„3 of 3" po ukryciu wyglądałoby jak utrata bazy — stąd liczba ukrytych."""
    html = _render([ARCHIWALNY_CSV, ARCHIWALNY_BOT, PRAWDZIWY_WNIOSEK],
                   imported_shown=False)

    assert "1 of 3" in html
    assert "2 imported hidden" in html
    assert "imported hidden</span>" in html, "sam przełącznik ma być widoczny"


def test_pokazane_wracaja_wszystkie():
    html = _render([ARCHIWALNY_CSV, ARCHIWALNY_BOT, PRAWDZIWY_WNIOSEK],
                   imported_shown=True)

    assert "3 of 3" in html
    assert "john.smith@imported.local" in html and "amy.lane@imported.local" in html
    assert "imported hidden</span>" not in html
    assert "imported shown</span>" in html


def test_certyfikaty_wracaja_razem_z_wierszami():
    """Po co w ogóle je pokazywać: to jedyne miejsce, gdzie wystawia się certyfikat."""
    ukryte = _render([ARCHIWALNY_CSV, PRAWDZIWY_WNIOSEK], imported_shown=False)
    widoczne = _render([ARCHIWALNY_CSV, PRAWDZIWY_WNIOSEK], imported_shown=True)

    assert "/payout/abc" not in ukryte
    assert "/payout/abc" in widoczne


def test_sama_archiwum_daje_wlasny_komunikat():
    """„No payouts match" wskazywałoby na wyszukiwarkę, a schował je przełącznik."""
    html = _render([ARCHIWALNY_CSV, ARCHIWALNY_BOT], imported_shown=False)

    assert "Only imported payouts here" in html
    assert "No payouts match" not in html
    assert "toggleImported()" in html, "komunikat ma dawać drogę powrotną"


def test_pusta_ksiega_zostaje_pusta_ksiega():
    """Brak wypłat to inna sytuacja niż wszystko schowane — inny komunikat."""
    html = _render([], imported_shown=False)

    assert "No payouts yet" in html
    assert "Only imported payouts here" not in html


# --------------------------------------------------------------------------- #
#  Czego ta zmiana NIE mogła ruszyć
# --------------------------------------------------------------------------- #
def test_ksiega_pieniedzy_dalej_oddaje_komplet():
    """Filtr siedzi w przeglądarce.

    Gdyby ktoś „dla spójności" dołożył `imported: int = 0` do endpointu, wiersze
    zniknęłyby po stronie serwera — a wtedy panel nie miałby z czego policzyć,
    ile ich schował, i nie dałoby się wystawić certyfikatu bez grzebania w API.
    """
    from app.main import admin_payouts_all

    assert "imported" not in inspect.signature(admin_payouts_all).parameters
