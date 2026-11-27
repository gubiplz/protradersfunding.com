"""Gotowe teksty kampanii i przycisk Campaign w Offers.

Preset wskazuje szablon grafiki po kluczu — literówka dałaby kampanię z cudzą
grafiką (serwer spada na flash-sale), więc każdy klucz musi istnieć w
CAMPAIGN_TEMPLATES. Niewypełnione {pct}/{plans} blokuje wysyłkę w panelu.
"""
import os
import re
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import notify  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _js() -> str:
    return client.get("/static/js/admin-panel.js").text


def test_presety_wskazuja_istniejace_szablony():
    kod = _js()
    blok = kod[kod.index("const CAMP_PRESETS=["):kod.index("function campVars")]
    klucze = re.findall(r"tpl:'([a-z-]+)'", blok)
    assert len(klucze) >= 5
    assert set(klucze) <= set(notify.CAMPAIGN_TEMPLATES)


def test_offers_ma_przyciski_kampanii_i_blokade_nawiasow():
    kod = _js()
    assert 'onclick="offerCampaign(${o.id})"' in kod
    assert kod.count('onclick="openCampaign()"') >= 2      # Newsletter + Offers
    assert "Fill in ${brak[0]} first" in kod
    assert 'id="camp-preset"' in kod
