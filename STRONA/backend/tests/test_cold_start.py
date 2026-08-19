"""Zimny start na serverless: migracje raz na deploy, nie przy kazdym zadaniu.

Na Vercelu proces wstaje od nowa co kilka minut, a `init_db` + `sync_catalog`
to ~33 round-tripy do bazy. Przy Postgresie po drugiej stronie lacza doklejaja
sie w calosci do PIERWSZEGO zadania uzytkownika (zmierzone: 3,1 s). Wynik
zalezy tylko od wersji kodu, wiec odcisk deployu w bazie pozwala je pominac.

Test pilnuje obu stron kontraktu: ze powtorzony start tej samej wersji NIE
rusza schematu i ze nowy deploy go domigruje. Trzecia strona jest rownie
wazna: bez odcisku (lokalnie, testy) pelna sciezka ma isc zawsze, bo modele
zmieniaja sie tam miedzy restartami bez zadnego commita.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("AUTO_SEED", "false")

from sqlalchemy import inspect, text  # noqa: E402

from app import main as main_mod  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.models import AppSetting  # noqa: E402

init_db()   # odcisk ladujemy do `app_settings`, wiec tabela musi istniec


def _wyczysc_odcisk() -> None:
    s = SessionLocal()
    s.query(AppSetting).filter(AppSetting.key == "schema_fingerprint").delete()
    s.commit()
    s.close()


def test_schemat_migruje_sie_raz_na_deploy(monkeypatch):
    _wyczysc_odcisk()
    zrobione: list[str] = []
    monkeypatch.setattr(main_mod, "init_db", lambda: zrobione.append("schemat"))
    monkeypatch.setattr(main_mod, "sync_catalog", lambda: zrobione.append("cennik"))
    monkeypatch.setattr(main_mod, "_migruj_login_admina", lambda: None)

    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "deploy-pierwszy")
    main_mod._przygotuj_baze()
    assert zrobione == ["schemat", "cennik"], "pierwszy start deployu musi zrobic wszystko"

    main_mod._przygotuj_baze()
    main_mod._przygotuj_baze()
    assert zrobione == ["schemat", "cennik"], \
        "kolejne zimne starty tej samej wersji nie moga ruszac bazy"

    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "deploy-drugi")
    main_mod._przygotuj_baze()
    assert zrobione.count("schemat") == 2, "nowy deploy musi domigrowac schemat"
    assert zrobione.count("cennik") == 2, "nowy deploy musi wgrac aktualny cennik"

    # Cofniecie deployu to tez zmiana odcisku — schemat idzie ponownie.
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "deploy-pierwszy")
    main_mod._przygotuj_baze()
    assert zrobione.count("schemat") == 3, "rollback nie moze zostac ze schematem nowszej wersji"

    _wyczysc_odcisk()


def test_bez_odcisku_deployu_pelna_sciezka_idzie_zawsze(monkeypatch):
    _wyczysc_odcisk()
    zrobione: list[str] = []
    monkeypatch.setattr(main_mod, "init_db", lambda: zrobione.append("schemat"))
    monkeypatch.setattr(main_mod, "sync_catalog", lambda: None)
    monkeypatch.setattr(main_mod, "_migruj_login_admina", lambda: None)
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)

    main_mod._przygotuj_baze()
    main_mod._przygotuj_baze()
    assert zrobione == ["schemat", "schemat"]

    s = SessionLocal()
    zapisany = s.query(AppSetting).filter(AppSetting.key == "schema_fingerprint").first()
    s.close()
    assert zapisany is None, "bez odcisku nie ma czego zapisywac"


def test_indeksy_dokladaja_sie_do_istniejacej_tabeli():
    """`create_all` pomija tabele, ktora juz istnieje — razem z jej indeksami.

    Sam `index=True` w modelu obsluguje wiec tylko swieze bazy. Produkcja stoi
    od miesiecy, wiec bez osobnej migracji jedyna baza, na ktorej zalezy, nigdy
    by tych indeksow nie zobaczyla. Test kasuje indeks (udajac stara baze) i
    sprawdza, ze start aplikacji go odtwarza — i ze drugi przebieg nie wybucha.
    """
    from app.db import _add_missing_indexes, _NEW_INDEXES

    for tabela, kolumna in _NEW_INDEXES:
        nazwa = f"ix_{tabela}_{kolumna}"
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX IF EXISTS {nazwa}"))
        assert nazwa not in [i["name"] for i in inspect(engine).get_indexes(tabela)]

    init_db()

    for tabela, kolumna in _NEW_INDEXES:
        nazwa = f"ix_{tabela}_{kolumna}"
        assert nazwa in [i["name"] for i in inspect(engine).get_indexes(tabela)], \
            f"{nazwa} nie wrocil — stara baza zostalaby ze skanem calej tabeli"

    _add_missing_indexes()   # idempotencja: kazdy zimny start to powtarza


def test_stara_baza_dostaje_kolumny_karty_leada():
    """`create_all` NIE robi ALTER-ow, wiec kolumna dolozona do modelu nie
    pojawia sie w tabeli, ktora juz istnieje.

    Panel oddawal przez to 500 na samej liscie leadow: model pytal o `owner`,
    `owner_at` i `tg_message_id`, a produkcyjna tabela `leads` — zalozona przy
    pierwszym zgloszeniu z landingu — nigdy ich nie dostala. Nowa tabela
    (`lead_reminders`) powstala normalnie, wiec z zewnatrz wygladalo to na
    udana migracje.

    Test zabiera te kolumny z gotowej tabeli i sprawdza, ze start aplikacji je
    oddaje, a SELECT z modelu — ten, na ktorym wywracal sie panel — przechodzi.
    """
    from app.db import _add_missing_columns, _NEW_COLUMNS
    from app.models import Lead

    nowe = {"owner", "owner_at", "tg_message_id", "tg_chat_id"}
    assert nowe <= set(_NEW_COLUMNS.get("leads", {})), \
        "kolumny karty leada musza byc zadeklarowane w _NEW_COLUMNS"

    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_leads_tg_message_id"))
        for kolumna in nowe:
            conn.execute(text(f"ALTER TABLE leads DROP COLUMN {kolumna}"))

    assert not (nowe & {c["name"] for c in inspect(engine).get_columns("leads")}), \
        "test nie mierzy niczego, jesli kolumny zostaly w tabeli"

    init_db()

    maja = {c["name"] for c in inspect(engine).get_columns("leads")}
    assert nowe <= maja, f"init_db nie oddal kolumn karty leada: {nowe - maja}"

    s = SessionLocal()
    try:
        s.query(Lead).all()
    finally:
        s.close()

    _add_missing_columns()   # idempotencja: kazdy zimny start to powtarza
