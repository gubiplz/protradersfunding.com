"""Backoff provisioningu: nieudane założenie dema nie może być ponawiane co tick.

Poller kręci się domyślnie co 3 s. Bez backoffu jeden broker odrzucający dema
oznaczałby ~1200 żądań na godzinę na KAŻDE oczekujące konto.

Stan siedzi w `app_settings`, a nie w pamięci procesu: na hostingu
bezserwerowym każde żądanie to inny proces, więc słownik modułu byłby pusty
przy każdej próbie i backoff nie istniałby w ogóle.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}"
os.environ["FEED"] = "sim"
os.environ["AUTO_SEED"] = "false"

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app import provisioning  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

init_db()


@pytest.fixture
def s():
    sesja = SessionLocal()
    yield sesja
    sesja.close()


def test_pierwsza_proba_jest_dozwolona(s):
    provisioning._clear_backoff(s, 1001)
    assert provisioning._may_attempt(s, 1001) is True


def test_po_bledzie_konto_czeka_i_odstep_rosnie_wykladniczo(s):
    provisioning._clear_backoff(s, 1002)

    first = provisioning._apply_backoff(s, 1002)
    assert first == 30.0
    assert provisioning._may_attempt(s, 1002) is False, "zaraz po błędzie nie ponawiamy"

    second = provisioning._apply_backoff(s, 1002)
    assert second == 60.0, "kolejna porażka => dwa razy dłuższa przerwa"


def test_backoff_ma_sufit(s):
    provisioning._clear_backoff(s, 1003)
    delays = [provisioning._apply_backoff(s, 1003) for _ in range(12)]
    assert max(delays) == provisioning._PROVISION_BACKOFF_MAX_SEC == 1800.0


def test_sukces_kasuje_backoff(s):
    provisioning._clear_backoff(s, 1004)
    provisioning._apply_backoff(s, 1004)
    provisioning._clear_backoff(s, 1004)
    assert provisioning._may_attempt(s, 1004) is True


def test_stan_przezywa_inna_sesje(s):
    """Sedno zmiany: druga instancja aplikacji ma widzieć tę samą przerwę."""
    provisioning._clear_backoff(s, 1006)
    provisioning._apply_backoff(s, 1006)

    inna = SessionLocal()
    try:
        assert provisioning._may_attempt(inna, 1006) is False
    finally:
        inna.close()


def test_zepsuty_wiersz_nie_blokuje_na_zawsze(s):
    from app.models import AppSetting
    provisioning._clear_backoff(s, 1007)
    s.add(AppSetting(key=provisioning._backoff_key(1007), value="śmieć"))
    s.commit()

    assert provisioning._may_attempt(s, 1007) is True


class _BoomFeed:
    async def provision(self, spec):
        raise RuntimeError("broker nie pozwala na programowe dema")


class _Acc:
    id = 1005
    initial_balance = 50_000
    trader_name = "Jan Kowalski"


def test_nieudane_zalozenie_zwraca_none_i_naklada_backoff(s):
    """Żaden kanał nie oddaje poświadczeń — funkcja ma to przyznać, nie zmyślić."""
    provisioning._clear_backoff(s, _Acc.id)
    settings = provisioning.get_settings()

    out = asyncio.run(provisioning._create_demo_account(_BoomFeed(), _Acc(), None, settings, s))

    assert out is None, "porażka nie może zwrócić udawanych poświadczeń"
    assert provisioning._may_attempt(s, _Acc.id) is False
