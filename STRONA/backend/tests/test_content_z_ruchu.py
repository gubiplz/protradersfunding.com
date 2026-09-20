"""Kolejka treści publikuje z ruchu strony, w tempie pilnowanym przez bazę.

`_content_tick` był wołany wyłącznie z `/api/tick`, a cron na planie Hobby
budzi się raz na dobę. Rozrzucone pory publikacji — losowa minuta w oknie,
żeby kanał nie wyglądał jak automat — nie miały więc żadnego znaczenia:
decydowały o kolejności, nie o porze.

Strażnik musi siedzieć w BAZIE, nie w pamięci procesu. Instancji jest wiele
i każda miałaby własny licznik, czyli limit przestałby cokolwiek ograniczać.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import main  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AppSetting  # noqa: E402

init_db()
KLUCZ = "contentbot_last_sweep"


@pytest.fixture(autouse=True)
def czysty_straznik():
    def skasuj():
        s = SessionLocal()
        try:
            s.query(AppSetting).filter(AppSetting.key == KLUCZ).delete(
                synchronize_session=False)
            s.commit()
        finally:
            s.close()
    skasuj()
    yield
    skasuj()


@pytest.fixture
def przebiegi(monkeypatch):
    ile: list[int] = []
    monkeypatch.setattr(main, "_content_tick", lambda: ile.append(1))
    return ile


def _znacznik(kiedy: datetime):
    s = SessionLocal()
    try:
        row = s.get(AppSetting, KLUCZ) or AppSetting(key=KLUCZ, value="")
        row.value = kiedy.isoformat()
        s.add(row)
        s.commit()
    finally:
        s.close()


def test_pierwszy_ruch_odpala_przebieg(przebiegi):
    main._content_sweep_z_ruchu()

    assert len(przebiegi) == 1


def test_kolejny_ruch_w_oknie_nie_odpala(przebiegi):
    """Sedno: ruch na panelu idzie kilkadziesiat razy na minute. Bez straznika
    kazde wejscie probowaloby opublikowac kolejny post."""
    for _ in range(30):
        main._content_sweep_z_ruchu()

    assert len(przebiegi) == 1


def test_po_uplywie_okna_przebieg_wraca(przebiegi):
    main._content_sweep_z_ruchu()
    _znacznik(datetime.now(timezone.utc)
              - timedelta(minutes=main.CONTENT_SWEEP_MIN + 1))

    main._content_sweep_z_ruchu()

    assert len(przebiegi) == 2


def test_znacznik_zapisany_przed_robota(monkeypatch):
    """Dwa rownolegle requesty nie moga wyslac dwoch postow, wiec znacznik
    musi byc w bazie ZANIM ruszy publikacja — nie po niej."""
    widziany: list[str] = []

    def podczas_przebiegu():
        s = SessionLocal()
        try:
            row = s.get(AppSetting, KLUCZ)
            widziany.append(row.value if row else "")
        finally:
            s.close()

    monkeypatch.setattr(main, "_content_tick", podczas_przebiegu)
    main._content_sweep_z_ruchu()

    assert widziany and widziany[0], "znacznik ma byc zapisany zanim ruszy publikacja"


def test_wywrocona_publikacja_nie_wywraca_requestu(monkeypatch):
    """Ta funkcja siedzi w middleware kazdego wejscia — post na kanale nie
    moze byc wazniejszy niz odpowiedz dla klienta."""
    def wybuch():
        raise RuntimeError("Telegram padl")

    monkeypatch.setattr(main, "_content_tick", wybuch)

    main._content_sweep_z_ruchu()   # brak wyjatku = test zdany


def test_okno_jest_rozsadne():
    """Za krotkie i zalegle posty wychodza sciana; za dlugie i wylosowana
    minuta publikacji znow przestaje cokolwiek znaczyc."""
    assert 5 <= main.CONTENT_SWEEP_MIN <= 30
