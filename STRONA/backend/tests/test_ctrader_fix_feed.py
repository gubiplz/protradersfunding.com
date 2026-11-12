"""FIX QUOTE FTMO → świece 1m (atrapa gniazda, bez sieci)."""
import asyncio
import os
import tempfile
import time
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.NamedTemporaryFile(suffix='.db', delete=False).name}")

from app import broker_quotes, ctrader_fix_feed as ff  # noqa: E402
from app.ctrader_feed import FEED_N  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AppSetting, TerminalCandle  # noqa: E402

init_db()
SOH = ff.SOH


@pytest.fixture(autouse=True)
def _czysto(monkeypatch):
    s = ff.get_settings()
    for k, v in {"ctrader_fix_password": "secret", "ctrader_fix_sender_comp_id": "live.ftmo.17246722",
                 "ctrader_fix_username": "17246722", "ctrader_fix_target_comp_id": "CSERVER",
                 "ctrader_fix_host": "live.cfixapi.com", "ctrader_fix_quote_port": "5211",
                 "ctrader_fix_trade_port": "5212"}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(ff, "ZAPIS_CO_S", 0.05)
    db = SessionLocal()
    db.query(TerminalCandle).delete()
    db.query(AppSetting).filter(AppSetting.key.in_((ff.SYMBOLE_KLUCZ, broker_quotes.KLUCZ))).delete(synchronize_session=False)
    db.commit(); db.close()
    broker_quotes.wyczysc()
    yield
    db = SessionLocal()
    db.query(AppSetting).filter(AppSetting.key == broker_quotes.KLUCZ).delete(synchronize_session=False)
    db.commit(); db.close()
    broker_quotes.wyczysc()


def test_zbuduj_i_parsuj_checksum():
    raw = ff.zbuduj([("35", "A"), ("49", "live.ftmo.17246722"), ("56", "CSERVER"), ("34", 1),
                     ("52", "20260101-00:00:00.000"), ("50", "QUOTE"), ("57", "QUOTE"),
                     ("98", 0), ("108", 20), ("141", "Y"), ("553", "17246722"), ("554", "x")])
    assert raw.startswith(b"8=FIX.4.4") and raw.endswith(SOH.encode())
    m = ff.parsuj(raw.decode())
    assert m["35"] == "A" and m["49"] == "live.ftmo.17246722" and m["553"] == "17246722"
    assert m["10"] == ff._checksum(raw.decode().rsplit("10=", 1)[0])


def test_mapa_z_security_list():
    # Skrót odpowiedzi 35=y z tagami 55/1007 jak w RoE Spotware.
    raw = (f"8=FIX.4.4{SOH}9=99{SOH}35=y{SOH}55=1{SOH}1007=EURUSD{SOH}1008=5{SOH}"
           f"55=2{SOH}1007=GBPUSD{SOH}55=4{SOH}1007=USDJPY{SOH}55=99{SOH}1007=BTCUSD{SOH}10=000{SOH}")
    mapa = ff._mapa_z_security_list(raw)
    assert mapa == {"1": "xyz:EUR", "2": "xyz:GBP", "4": "xyz:JPY"}


def test_spot_z_md_snapshot():
    raw = (f"8=FIX.4.4{SOH}9=80{SOH}35=W{SOH}55=1{SOH}268=2{SOH}"
           f"269=0{SOH}270=1.10950{SOH}269=1{SOH}270=1.10960{SOH}10=000{SOH}")
    assert ff._spot_z_md(raw) == [("1", 1.1095, 1.1096)]


class Atrapa:
    """Kolejka bajtów udająca StreamReader/Writer FIX."""

    def __init__(self, odpowiedzi: list[str]):
        self.odp = list(odpowiedzi)
        self.wyslane: list[str] = []
        self._buf = b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def write(self, data: bytes):
        self.wyslane.append(data.decode())
        typ = ff.parsuj(data.decode()).get("35")
        if typ == "A" and self.odp:
            self._buf += self.odp.pop(0).encode()
        elif typ == "V" and self.odp:
            self._buf += self.odp.pop(0).encode()

    async def drain(self):
        return None

    async def read(self, n: int) -> bytes:
        if self._buf:
            out, self._buf = self._buf, b""
            return out
        if self.odp:
            return self.odp.pop(0).encode()
        # Nie zamykaj gniazda — niech FixSesja.recv wypali TimeoutError.
        await asyncio.sleep(60)
        return b""

    def close(self):
        return None

    async def wait_closed(self):
        return None


def test_sluchaj_sklada_swiece_z_fix_quote(monkeypatch):
    logon = ff.zbuduj([("35", "A"), ("49", "CSERVER"), ("56", "live.ftmo.17246722"), ("34", 1),
                       ("52", "20260101-00:00:00.000"), ("50", "QUOTE")]).decode()
    snap = (f"8=FIX.4.4{SOH}9=90{SOH}35=W{SOH}55=1{SOH}268=2{SOH}"
            f"269=0{SOH}270=1.10950{SOH}269=1{SOH}270=1.10960{SOH}10=001{SOH}")
    atrapa = Atrapa([logon, snap])

    async def polacz():
        return atrapa, atrapa

    monkeypatch.setattr(ff, "ZAPIS_CO_S", 0.05)
    w = asyncio.run(ff.sluchaj(0.35, polacz_quote=polacz, mapa_override={"1": "xyz:EUR"}))
    assert w["ticks"] >= 1 and w["account"] == "17246722" and not w.get("error")
    assert "xyz:EUR" in w["symbols"]
    q = broker_quotes.kwoty(["xyz:EUR"])
    assert q["xyz:EUR"][:2] == (1.1095, 1.1096)
    s = SessionLocal()
    try:
        rows = s.query(TerminalCandle).filter(TerminalCandle.coin == "xyz:EUR").all()
        assert rows and all(r.n == FEED_N for r in rows)
    finally:
        s.close()
    typy = [ff.parsuj(x).get("35") for x in atrapa.wyslane]
    assert typy[0] == "A" and "V" in typy
