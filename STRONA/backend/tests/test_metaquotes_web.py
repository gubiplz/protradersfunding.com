"""Testy parsera panelu „New account opened" z web terminala MetaQuotes.

Wejście to dosłowny `innerText` odczytany z prawdziwej strony po założeniu konta —
dzięki temu regresja przy zmianie układu strony wychodzi tu, a nie u klienta.
"""
import pytest

from app.metaquotes_web import (
    MQ_SERVER,
    WebDemoSpec,
    WebProvisioningError,
    looks_successful,
    parse_result,
)

# Zrzut z realnej rejestracji (dane testowe).
REAL_EN = """Trading accounts: MetaQuotes Ltd.
Connect to account
Open Demo account
Test Konto
5053494560 100 000.00 USD
© 2000 – 2026, MetaQuotes Ltd.
End-User License Agreement
New account opened
Name
Test Konto
Server
MetaQuotes-Demo
Account type
Forex Hedged USD
Deposit
100 000 USD
Login
5053494560
Password
2_KwVdLr
Investor
-z3zAgRn  (Read only password)
Get started with your account
Please keep your username and passwords in a safe place
Copy to clipboard"""


def test_parsuje_prawdziwy_zrzut_strony():
    c = parse_result(REAL_EN)
    assert c.login == "5053494560"
    assert c.password == "2_KwVdLr"
    assert c.server == "MetaQuotes-Demo"
    assert c.investor_password == "-z3zAgRn", "dopisek '(Read only password)' musi zostać odcięty"


def test_rozumie_polskie_etykiety():
    pl = """Nowy rachunek otwarty
Serwer
MetaQuotes-Demo
Login
7788990011
Hasło
abcD1234
Inwestor
xyzQ9876  (Hasło tylko do odczytu)"""
    c = parse_result(pl)
    assert (c.login, c.password, c.investor_password) == ("7788990011", "abcD1234", "xyzQ9876")


def test_brak_serwera_domysla_metaquotes_demo():
    c = parse_result("Login\n1234567\nPassword\nabc")
    assert c.server == MQ_SERVER


def test_brak_hasla_to_blad_a_nie_polowiczne_konto():
    with pytest.raises(WebProvisioningError):
        parse_result("New account opened\nLogin\n5053494560")


def test_login_musi_byc_numerem_konta():
    """Gdyby strona podmieniła układ, nie chcemy wysłać traderowi śmiecia."""
    with pytest.raises(WebProvisioningError):
        parse_result("Login\nOpen Demo account\nPassword\nabc123")


def test_pusty_ekran_to_blad():
    with pytest.raises(WebProvisioningError):
        parse_result("")


def test_marker_sukcesu():
    assert looks_successful(REAL_EN)
    assert not looks_successful("Open Demo account\nName\nEmail")


# ------------------------------------------------------------------- spec ---
class _S:
    mail_from = "no-reply@propfunding.local"
    metaquotes_web_default_phone = "+10000000000"
    metaquotes_web_leverage = 100
    metaquotes_web_account_type = "Forex Hedged USD"


class _Trader:
    def __init__(self, **kw):
        self.email = "klient@example.com"
        self.full_name = ""
        self.first_name = None
        self.last_name = None
        self.phone = None
        self.__dict__.update(kw)


class _Acc:
    trader_name = "Zapasowa Nazwa"
    initial_balance = 50_000


def test_spec_bierze_dane_z_kroku_platnosci():
    spec = WebDemoSpec.from_trader(
        _Trader(first_name="Anna", last_name="Nowak", phone="+48501002003"), _Acc(), _S()
    )
    assert (spec.first_name, spec.last_name, spec.phone) == ("Anna", "Nowak", "+48501002003")
    assert spec.email == "klient@example.com"
    assert spec.deposit == 50_000


def test_spec_rozbija_full_name_gdy_brak_osobnych_pol():
    spec = WebDemoSpec.from_trader(_Trader(full_name="Jan Maria Kowalski"), _Acc(), _S())
    assert spec.first_name == "Jan"
    assert spec.last_name == "Maria Kowalski", "nazwisko dwuczłonowe nie może się urwać"


def test_spec_ma_zawsze_komplet_pol_bo_formularz_ich_wymaga():
    spec = WebDemoSpec.from_trader(_Trader(), _Acc(), _S())
    assert spec.first_name and spec.last_name and spec.phone and spec.email


# ------------------------------------------------- odczyt stanu konta ------
from app.metaquotes_web import WebReadError, parse_account_state  # noqa: E402

# Zrzut z realnie zalogowanego terminala.
TERMINAL = """Symbol Ticket Time Type Volume Price
Balance: 10 000.00
Equity: 9 850.25
Margin: 120.00
Free margin: 9 730.25
Level: 8208.54%
You don't have any positions
Create New Order"""


def test_czyta_saldo_i_equity_z_terminala():
    st = parse_account_state(TERMINAL)
    assert st.balance == 10_000.0
    assert st.equity == 9_850.25, "equity musi być czytane osobno od salda — na nim liczymy DD"
    assert st.margin == 120.0
    assert st.free_margin == 9_730.25


def test_wykrywa_brak_pozycji():
    assert parse_account_state(TERMINAL).has_open_position is False


def test_wykrywa_otwarte_pozycje():
    z_pozycja = TERMINAL.replace("You don't have any positions", "EURUSD 123456 buy 0.10 1.08500")
    assert parse_account_state(z_pozycja).has_open_position is True


def test_radzi_sobie_z_polskim_terminalem_i_przecinkiem():
    pl = "Saldo: 25 000,00\nŚrodki: 24 100,50\nBrak pozycji"
    st = parse_account_state(pl)
    assert (st.balance, st.equity) == (25_000.0, 24_100.50)


def test_brak_danych_to_blad_a_nie_zerowe_equity():
    """Gdyby sesja wygasła, zwrócenie 0.0 wyglądałoby jak katastrofalny DD
    i błędnie zamknęłoby konto tradera."""
    with pytest.raises(WebReadError):
        parse_account_state("Zaloguj się\nEnter Login")


# ---------------- odczyt wolumenu pozycji ----------------
def test_parse_open_volume_sumuje_pozycje():
    from app.metaquotes_web import parse_open_volume
    ekran = """Balance: 100 000.00  Equity: 100 250.00
EURUSD  buy 2.50  1.08512  1.08600  +220.00
XAUUSD  sell 0.75  2650.10  2648.90  +30.00"""
    lots, known = parse_open_volume(ekran)
    assert known and lots == 3.25


def test_parse_open_volume_brak_pozycji():
    from app.metaquotes_web import parse_open_volume
    assert parse_open_volume("You don't have any positions") == (0.0, True)


def test_parse_open_volume_nieczytelny_ekran_oznaczony_jako_niepewny():
    """Widac cos, ale nie wolumen — silnik ma NIE karac tradera."""
    from app.metaquotes_web import parse_open_volume
    lots, known = parse_open_volume("Balance: 100 000.00 Equity: 99 900.00")
    assert lots == 0.0 and known is False


def test_stan_konta_zawiera_wolumen():
    from app.metaquotes_web import parse_account_state
    st = parse_account_state("""Balance: 50 000.00
Equity: 50 100.00
Margin: 1 000.00
Free margin: 49 100.00
EURUSD buy 1.00 1.08512""")
    assert st.volume_lots == 1.0 and st.volume_known is True
