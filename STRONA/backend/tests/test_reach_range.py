"""Reach BOT: stała ilość albo ZAKRES losowany pod każdym postem.

2026-09-24, prośba właściciela: równe „400 views, 30 reactions" pod każdym
kolejnym postem zdradzają zakup. Przełącznik Fixed/Range: w zakresie każdy post
dostaje losową liczbę z [od, do]; kanał może mieć własny zakres („20-40"),
ręczny boost dalej zamawia dokładnie tyle, ile wpisano.
"""
import random

from tests.test_reach import ADMIN, _dostawca, _sesja, _transport, _wyczysc, client  # noqa: F401

from app import reach


def _ilosci_z_logu(log):
    return [int(p["quantity"]) for p in log if p.get("action") == "add"]


def test_zakres_losuje_rozne_liczby_w_granicach():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True, qty_mode="range", qty_reactions=20,
                                qty_reactions_max=40, qty_views=300, qty_views_max=500)
        cfg = reach.ustawienia(s)
        assert cfg["qty_mode"] == "range" and cfg["qty_reactions_max"] == 40
        reakcje, widoki = set(), set()
        with _dostawca():
            for n in range(12):
                log = []
                reach.zamow(s, f"https://t.me/kanal_testowy/{100 + n}", transport=_transport(log=log))
                r, v = _ilosci_z_logu(log)
                assert 20 <= r <= 40 and 300 <= v <= 500
                reakcje.add(r); widoki.add(v)
        assert len(reakcje) > 3 and len(widoki) > 3        # nie za każdym razem tyle samo
    finally:
        s.close()


def test_tryb_staly_ignoruje_zapisany_koniec_zakresu():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True, qty_mode="range", qty_reactions=20,
                                qty_reactions_max=40, qty_views=300, qty_views_max=500)
        reach.zapisz_ustawienia(s, qty_mode="fixed")
        log = []
        with _dostawca():
            reach.zamow(s, "https://t.me/kanal_testowy/7", transport=_transport(log=log))
        assert _ilosci_z_logu(log) == [20, 300]
    finally:
        s.close()


def test_kanal_z_wlasnym_zakresem_i_boost_dokladny():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True, qty_reactions=30, qty_views=400)
        reach.zapisz_kanaly(s, [{"username": "kanal_zakres", "on": True, "qty_mode": "range",
                                 "qty_reactions": 10, "qty_reactions_max": 15,
                                 "qty_views": 100, "qty_views_max": 180}])
        # ten sam kanał w trybie Fixed: górny koniec się nie liczy
        reach.zapisz_kanaly(s, [{"username": "kanal_staly", "on": True, "qty_mode": "fixed",
                                 "qty_reactions": 10, "qty_reactions_max": 15}])
        st = reach.ilosci(s, "kanal_staly")
        assert st["qty_reactions"] == st["qty_reactions_max"] == 10
        reach.zapisz_kanaly(s, [{"username": "kanal_zakres", "on": True, "qty_mode": "range",
                                 "qty_reactions": 10, "qty_reactions_max": 15,
                                 "qty_views": 100, "qty_views_max": 180}])
        z = reach.ilosci(s, "kanal_zakres")
        assert (z["qty_reactions"], z["qty_reactions_max"], z["qty_views"], z["qty_views_max"]) \
            == (10, 15, 100, 180)
        wylosowane = reach.losuj_ilosci(z, random.Random(3))
        assert 10 <= wylosowane["qty_reactions"] <= 15 and 100 <= wylosowane["qty_views"] <= 180
        # ręczny boost = dokładnie tyle
        log = []
        with _dostawca():
            reach.zamow(s, "https://t.me/kanal_zakres/5", transport=_transport(log=log),
                        qty_reactions=12, qty_views=150)
        assert _ilosci_z_logu(log) == [12, 150]
    finally:
        s.close()


def test_bramka_salda_liczy_gorny_koniec_zakresu():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True, qty_mode="range", qty_reactions=10,
                                qty_reactions_max=100, qty_views=100, qty_views_max=1000)
        reach._ustaw(s, "rate_reactions", "10"); reach._ustaw(s, "rate_views", "1"); s.commit()
        # koszt górnego końca: 100*10/1000 + 1000*1/1000 = 2.0
        assert reach.ustawienia(s)["unit_cost"] == 2.0
    finally:
        s.close()


def test_walidacja_zakresu_i_panel():
    s = _sesja()
    try:
        _wyczysc(s)
    finally:
        s.close()
    r = client.post("/api/admin/reach", headers=ADMIN,
                    json={"qty_mode": "range", "qty_reactions": 30, "qty_reactions_max": 10})
    assert r.status_code == 400 and "upside down" in r.text
    r = client.post("/api/admin/reach", headers=ADMIN,
                    json={"qty_mode": "range", "qty_reactions": 10, "qty_reactions_max": 30,
                          "qty_views": 200, "qty_views_max": 260})
    assert r.status_code == 200
    body = r.json()
    assert body["qty_mode"] == "range" and body["qty_views_max"] == 260
    s = _sesja()
    try:
        import pytest
        with pytest.raises(ValueError):
            reach.zapisz_kanaly(s, [{"username": "kanal_zly", "qty_mode": "range",
                                     "qty_views": 300, "qty_views_max": 100}])
    finally:
        s.close()
