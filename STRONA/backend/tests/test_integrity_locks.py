"""Integralność kont przy równoległych przebiegach (serverless = wiele instancji).

- rekord z puli MT5 trafia do DOKŁADNIE jednego konta;
- zapis konta na podstawie starego odczytu (tick pollera trzymający konto
  przez wywołania sieciowe) nie nadpisuje świeżej zmiany — blokada
  optymistyczna `row_version`;
- API zamienia taki konflikt na 409 „spróbuj ponownie", nie 500.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm.exc import StaleDataError  # noqa: E402

from app import provisioning  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, PoolAccount  # noqa: E402

init_db()
LICZNIK = iter(range(10_000))
ROZMIAR = 77_000.0   # rozmiar spoza katalogu — inne testy nie zostawiają takiej puli


def _konto(status="provisioning", balance=ROZMIAR) -> int:
    s = SessionLocal()
    acc = Account(login=f"91{next(LICZNIK):05d}", trader_name="Integrity", product_key="2step-50k",
                  preset="2step-50k", initial_balance=ROZMIAR, steps=2, phase="eval_1",
                  status=status, balance=balance, equity=balance, peak_equity=balance,
                  day_start_equity=balance, day_start_balance=balance)
    s.add(acc); s.commit(); aid = acc.id; s.close()
    return aid


def _pula() -> int:
    s = SessionLocal()
    n = next(LICZNIK)
    p = PoolAccount(account_size=ROZMIAR, platform_login=f"55{n:05d}", platform_password="pw",
                    platform_server="Demo", claimed=False)
    s.add(p); s.commit(); pid = p.id; s.close()
    return pid


def test_jeden_rekord_puli_dla_dokladnie_jednego_konta():
    pid = _pula()
    a1, a2 = _konto(), _konto()
    s1, s2 = SessionLocal(), SessionLocal()
    acc1, acc2 = s1.get(Account, a1), s2.get(Account, a2)
    assert provisioning.claim_pool_account(s1, acc1) is True
    s1.commit()
    assert provisioning.claim_pool_account(s2, acc2) is False
    s2.rollback()
    s1.close(); s2.close()
    s = SessionLocal()
    p = s.get(PoolAccount, pid)
    assert p.claimed and p.claimed_by_account_id == a1
    s.close()


def test_przegrany_wyscigu_bierze_nastepny_rekord():
    """Kandydat przeczytany jako wolny, a zajęty przez inną instancję tuż
    przed naszym UPDATE: warunkowy UPDATE daje 0 wierszy i bierzemy następny."""
    p1, p2 = _pula(), _pula()
    aid = _konto()
    inna = SessionLocal()          # druga instancja zajmuje p1 i commituje
    inna.query(PoolAccount).filter(PoolAccount.id == p1).update({PoolAccount.claimed: True})
    inna.commit(); inna.close()

    class _StaraLista:
        """Odpowiedź zapytania o kandydatów sprzed zajęcia p1."""
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def all(self): return [(p1,), (p2,)]

    class _Sesja:
        def __init__(self, s): self._s = s
        def query(self, *a, **k):
            if a and a[0] is PoolAccount.id:
                return _StaraLista()
            return self._s.query(*a, **k)
        def __getattr__(self, n): return getattr(self._s, n)

    s = SessionLocal()
    acc = s.get(Account, aid)
    assert provisioning.claim_pool_account(_Sesja(s), acc) is True
    s.commit()
    assert s.get(PoolAccount, p2).claimed_by_account_id == aid
    assert s.get(PoolAccount, p1).claimed_by_account_id is None, "p1 było cudze — nie ruszamy"
    s.close()


def test_stary_odczyt_nie_nadpisuje_swiezego_salda():
    aid = _konto(status="active", balance=110_000.0)
    tick = SessionLocal()
    stary = tick.get(Account, aid)          # tick czyta konto (saldo 110k)
    stary.equity                             # wymuś załadowanie

    admin = SessionLocal()
    acc = admin.get(Account, aid)
    acc.balance = ROZMIAR                   # zatwierdzona wypłata zeruje zysk
    admin.commit(); admin.close()

    stary.balance = 110_000.0 + 250.0       # tick dopisuje P&L do STAREGO salda
    with pytest.raises(StaleDataError):
        tick.commit()
    tick.rollback(); tick.close()

    s = SessionLocal()
    assert s.get(Account, aid).balance == ROZMIAR, "reset salda po wypłacie nie może zniknąć"
    s.close()


def test_konflikt_zapisu_w_api_to_409():
    from app.main import app as aplikacja

    @aplikacja.get("/__test_stale")
    def _stale():
        raise StaleDataError("row changed")

    with TestClient(aplikacja) as c:
        r = c.get("/__test_stale")
    assert r.status_code == 409 and "try again" in r.json()["detail"]


def test_straznik_przepuszcza_dokladnie_jedna_instancje():
    """Dwie instancje czytają ten sam stary znacznik; druga zapisuje pierwsza.
    Warunkowy UPDATE pierwszej dostaje 0 wierszy — robota idzie raz."""
    from app.zamki import zajmij_ustawienie
    klucz = f"test_guard_{next(LICZNIK)}"
    s0 = SessionLocal()
    assert zajmij_ustawienie(s0, klucz, "v1", lambda stara: True) is True
    s0.close()

    wyniki = []

    def pora_z_wyscigiem(stara):
        # w chwili, gdy instancja A „myśli", instancja B wchodzi i wygrywa
        b = SessionLocal()
        wyniki.append(zajmij_ustawienie(b, klucz, "v2-B", lambda s: True))
        b.close()
        return True

    a = SessionLocal()
    wyniki.append(zajmij_ustawienie(a, klucz, "v2-A", pora_z_wyscigiem))
    a.close()
    assert wyniki == [True, False]


def test_druga_wyplata_pending_odrzucona_przez_baze():
    from sqlalchemy.exc import IntegrityError
    from app.models import PayoutRequest
    from app.models import Trader
    aid = _konto(status="funded", balance=80_000.0)
    s = SessionLocal()
    tr = Trader(email=f"integ{next(LICZNIK)}@test.pl", password_hash="x", full_name="Integ",
                referral_code=f"INT{next(LICZNIK):05d}")
    s.add(tr); s.commit(); tid = tr.id
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=3000.0,
                        trader_share=2400.0, method="usdt", status="pending"))
    s.commit()
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=3000.0,
                        trader_share=2400.0, method="usdt", status="pending"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()
    # zamknięte (paid/rejected) wypłaty nie blokują kolejnej
    s.add(PayoutRequest(account_id=aid, trader_id=tid, profit_amount=1.0,
                        trader_share=1.0, method="usdt", status="paid"))
    s.commit()
    s.close()


def test_post_przejety_przez_inna_instancje_nie_wychodzi_drugi_raz(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import contentbot
    from app.models import ChannelPost
    wyslane = []
    monkeypatch.setattr(contentbot, "opublikuj",
                        lambda s, post, **k: (wyslane.append(post.id), {"posted": True})[1])
    s = SessionLocal()
    s.query(ChannelPost).filter(ChannelPost.status.in_(("scheduled", "publishing"))) \
        .update({ChannelPost.status: "draft"}, synchronize_session=False)
    teraz = datetime.now(timezone.utc)
    post = ChannelPost(channel="mgmt", kind="text", body="x", status="publishing",
                       scheduled_for=(teraz - timedelta(minutes=5)).replace(tzinfo=None),
                       updated_at=(teraz - timedelta(minutes=1)).replace(tzinfo=None))
    s.add(post); s.commit(); pid = post.id
    # inna instancja właśnie go wysyła (publishing, świeży) — nie ruszamy
    assert contentbot.wyslij_zaplanowane(s)["sent"] == 0 and wyslane == []
    # ...a jeśli tamta padła kwadrans temu, post wraca do kolejki i wychodzi raz
    s.query(ChannelPost).filter(ChannelPost.id == pid).update(
        {ChannelPost.updated_at: (teraz - timedelta(minutes=20)).replace(tzinfo=None)})
    s.commit()
    contentbot.wyslij_zaplanowane(s)
    assert wyslane == [pid]
    s.close()


def test_zamek_pelnego_ticka():
    """Jeden pełny przebieg naraz; zamek po padniętym procesie wygasa."""
    import time as _t
    from app import poller
    klucz = f"test_lock_{next(LICZNIK)}"
    t1 = poller.zajmij_zamek(klucz)
    assert t1 and poller.zajmij_zamek(klucz) is None          # ktoś liczy
    poller.zwolnij_zamek(klucz, t1)
    t2 = poller.zajmij_zamek(klucz)
    assert t2 and t2 != t1                                     # zwolniony = wolny
    # „padnięty" proces: run sprzed 5 minut — wolno przejąć
    from app.models import AppSetting
    s = SessionLocal()
    s.get(AppSetting, klucz).value = f"run:{_t.time() - 300:.6f}"
    s.commit(); s.close()
    t3 = poller.zajmij_zamek(klucz)
    assert t3
    poller.zwolnij_zamek(klucz, t2)                            # cudzy token nic nie zwalnia
    assert poller.zajmij_zamek(klucz) is None
