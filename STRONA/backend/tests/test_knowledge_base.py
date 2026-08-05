"""Baza wiedzy agenta AI ma się zgadzać z kodem, bo inaczej bot okłamuje klientów.

Cennik i limity żyją w `catalog.py`, a agent obsługi cytuje je z `knowledge/`.
Podwyżka ceny wdrożona bez aktualizacji bazy wiedzy nie wywala niczego — bot po
prostu dalej podaje starą kwotę i firma ma spór o pieniądze zamiast literówki.
Te testy są jedynym połączeniem między tymi dwoma miejscami.
"""
from pathlib import Path

import pytest

from app.catalog import (AFFILIATE_COMMISSION_PCT, COUPONS, WEEKEND_ADDON_USD, _CATALOG,
                         apply_coupon, max_lots_for)
from app.rules import ChallengeConfig, DrawdownType, _daily_loss_floor, AccountRuntime

WIEDZA = Path(__file__).resolve().parent.parent / "knowledge"
FAKTY = (WIEDZA / "02-facts.md").read_text(encoding="utf-8")

# Forex Passing to osobny projekt i osobne repo, ale jego agent sprzedażowy cytuje
# NASZ cennik — z rabatem partnerskim policzonym co do grosza. Podwyżka u nas
# oznacza, że tamten bot podaje klientowi cenę, której nasz checkout nie wystawi.
# Ścieżka jest względna wobec katalogu roboczego, więc na cudzym klonie po prostu
# nie istnieje i te testy się pomijają — nie ma tu czego naprawiać.
FP_KB = (Path(__file__).resolve().parents[4] / "forexpassing.com" / "AGENT-KB" / "06-prop-firm.md")
_fp_missing = pytest.mark.skipif(not FP_KB.is_file(),
                                 reason=f"brak repo Forex Passing pod {FP_KB}")


def test_pliki_bazy_wiedzy_istnieja():
    for nazwa in ("README.md", "01-agent-brief.md", "02-facts.md", "03-answers.md"):
        assert (WIEDZA / nazwa).is_file(), f"brakuje {nazwa} — agent straci część kontekstu"


@pytest.mark.parametrize("key,price", [(r[0], r[4]) for r in _CATALOG])
def test_kazda_cena_z_katalogu_jest_w_bazie_wiedzy(key, price):
    assert f"${price:,}" in FAKTY, (
        f"plan {key} kosztuje ${price:,}, a tej kwoty nie ma w knowledge/02-facts.md — "
        "zaktualizuj tabelę cen, zanim bot poda klientowi starą stawkę")


@pytest.mark.parametrize("steps,split", sorted({(r[3], r[11]) for r in _CATALOG}))
def test_profit_split_zgadza_sie_z_katalogiem(steps, split):
    rodzina = "Instant Funding" if steps == 0 else "2-Step"
    assert f"{split}%" in FAKTY, f"split {split}% ({rodzina}) nie występuje w bazie wiedzy"


@pytest.mark.parametrize("pole,idx", [("max daily loss", 7), ("max overall loss", 8)])
def test_limity_strat_sa_w_bazie_wiedzy(pole, idx):
    for wartosc in sorted({r[idx] for r in _CATALOG}):
        assert f"{wartosc}%" in FAKTY, f"{pole} {wartosc}% brakuje w bazie wiedzy"


def test_kody_rabatowe_opisane():
    for kod in COUPONS:
        assert kod in FAKTY, (
            f"kod {kod} istnieje w catalog.py, ale nie w bazie wiedzy — bot albo go nie "
            "zaproponuje, albo nie rozpozna, gdy klient go poda")


def test_afiliacja_i_dodatek_weekendowy():
    assert f"{AFFILIATE_COMMISSION_PCT:.0f}% commission" in FAKTY
    assert f"${WEEKEND_ADDON_USD:.0f} add-on" in FAKTY


def test_limit_wolumenu_przyklady_zgadzaja_sie_ze_wzorem():
    # Przykłady w §4 mają być policzone tym samym wzorem, nie przepisane z głowy.
    for rozmiar, oczekiwany in ((25_000, "1.5"), (100_000, "6"), (1_000_000, "60")):
        assert max_lots_for(rozmiar) == float(oczekiwany)
        assert f"${rozmiar:,} → {oczekiwany} lots" in FAKTY


def test_przyklad_limitu_dziennego_zgadza_sie_z_silnikiem_a_nie_z_faq():
    """Baza wiedzy podaje 97 000; publiczne FAQ podaje 96 900. Rację ma silnik.

    Gdyby ktoś "poprawił" bazę wiedzy pod FAQ, bot zacząłby obiecywać klientom
    o 100 USD więcej luzu, niż daje im silnik ryzyka — czyli generować reklamacje
    z cytatem z naszej własnej odpowiedzi.
    """
    cfg = ChallengeConfig(initial_balance=100_000, profit_target_pct=10,
                          max_daily_loss_pct=5, max_overall_loss_pct=10,
                          drawdown_type=DrawdownType.STATIC)
    rt = AccountRuntime(day_start_equity=102_000)
    podloga = _daily_loss_floor(cfg, rt)

    assert podloga == 97_000.0
    assert f"${podloga:,.0f}" in FAKTY
    assert "$96,900" in FAKTY, "ostrzeżenie o błędnej liczbie z FAQ zniknęło z bazy wiedzy"


# --------------------------------------------------------------------------- #
#  Baza agenta sprzedażowego Forex Passing (osobne repo, ten sam cennik)       #
# --------------------------------------------------------------------------- #

@_fp_missing
@pytest.mark.parametrize("key,price", [(r[0], r[4]) for r in _CATALOG])
def test_cennik_dla_agenta_forex_passing_jest_aktualny(key, price):
    """Cena listowa ORAZ cena po rabacie partnerskim — obie muszą się zgadzać.

    Sama cena listowa nie wystarczy: agent podaje klientowi kwotę do zapłaty, więc
    to ona jest obietnicą. Rabat liczymy tą samą funkcją co checkout, żeby test nie
    powtarzał arytmetyki, tylko ją weryfikował.
    """
    kb = FP_KB.read_text(encoding="utf-8")
    po_rabacie, pct = apply_coupon(price, "VIP20")
    assert pct == 20.0, "kupon partnerski przestał dawać 20% — popraw bazę, zanim agent obieca za dużo"

    assert f"${price:,}" in kb, f"{key}: cena listowa ${price:,} nie występuje w 06-prop-firm.md"
    assert f"${po_rabacie:,.2f}" in kb, (
        f"{key}: cena po rabacie ${po_rabacie:,.2f} nie występuje w 06-prop-firm.md — "
        "agent poda klientowi kwotę, której checkout nie wystawi")
    assert f"${price - po_rabacie:,.2f}" in kb, f"{key}: kwota oszczędności się nie zgadza"


@_fp_missing
def test_agent_sprzedazowy_nie_obiecuje_niewymuszanych_regul():
    """Trzy rzeczy, których nasz system NIE pilnuje, a strona je obiecuje.

    Gdyby ktoś wyciął te ostrzeżenia, bot zacząłby sprzedawać reguły, których nie
    ma — a to są akurat te, po których klient wraca z pretensją: 30 dni handlowych
    przy Instant, harmonogram wypłat i minimalna kwota wypłaty.
    """
    kb = FP_KB.read_text(encoding="utf-8")
    for fragment in ("not enforced anywhere", "no mechanism behind it",
                     "No minimum payout amount"):
        assert fragment in kb, f"zniknęło ostrzeżenie: {fragment!r}"
