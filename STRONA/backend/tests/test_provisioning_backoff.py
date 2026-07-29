"""Backoff provisioningu: nieudane założenie dema nie może być ponawiane co tick.

Poller kręci się domyślnie co 3 s. Bez backoffu jeden broker odrzucający dema
oznaczałby ~1200 żądań na godzinę na KAŻDE oczekujące konto.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ["FEED"] = "sim"
os.environ["AUTO_SEED"] = "false"

import asyncio  # noqa: E402

from app import provisioning  # noqa: E402


def test_pierwsza_proba_jest_dozwolona():
    provisioning._clear_backoff(1001)
    assert provisioning._may_attempt(1001) is True


def test_po_bledzie_konto_czeka_i_odstep_rosnie_wykladniczo():
    provisioning._clear_backoff(1002)

    first = provisioning._apply_backoff(1002)
    assert first == 30.0
    assert provisioning._may_attempt(1002) is False, "zaraz po błędzie nie ponawiamy"

    second = provisioning._apply_backoff(1002)
    assert second == 60.0, "kolejna porażka => dwa razy dłuższa przerwa"


def test_backoff_ma_sufit():
    provisioning._clear_backoff(1003)
    delays = [provisioning._apply_backoff(1003) for _ in range(12)]
    assert max(delays) == provisioning._PROVISION_BACKOFF_MAX_SEC == 1800.0


def test_sukces_kasuje_backoff():
    provisioning._clear_backoff(1004)
    provisioning._apply_backoff(1004)
    provisioning._clear_backoff(1004)
    assert provisioning._may_attempt(1004) is True


class _BoomFeed:
    async def provision(self, spec):
        raise RuntimeError("broker nie pozwala na programowe dema")


class _Acc:
    id = 1005
    initial_balance = 50_000
    trader_name = "Jan Kowalski"


def test_nieudane_zalozenie_zwraca_none_i_naklada_backoff():
    """Bez tokenu/profilu leci ścieżka feedu — tu celowo wysadzona."""
    provisioning._clear_backoff(_Acc.id)
    settings = provisioning.get_settings()

    out = asyncio.run(provisioning._create_demo_account(_BoomFeed(), _Acc(), None, settings))

    assert out is None, "porażka nie może zwrócić udawanych poświadczeń"
    assert provisioning._may_attempt(_Acc.id) is False
