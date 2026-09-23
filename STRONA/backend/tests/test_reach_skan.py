"""Reach BOT: skan kanałów jako zapas dla webhooka + stan webhooka w panelu.

2026-09-23 post wrzucony ręcznie na @forex_passing nie dostał zasięgu: bot był
adminem („auto ready"), ale Telegram nie zapukał na webhook. Skan publicznego
podglądu `t.me/s/<kanał>` łapie takie posty; wspólny znacznik per kanał pilnuje,
żeby żaden post nie był opłacony dwa razy (webhook, publikacja, skan).
"""
from datetime import datetime, timedelta, timezone

from tests.test_reach import ADMIN, _dostawca, _sesja, _transport, _wyczysc, client  # noqa: F401

from app import main, reach

TERAZ = datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)


def _strona(kanal: str, posty: list[tuple[int, datetime]], serwisowe=(1,)) -> str:
    """Minimalna kopia znaczników z t.me/s/<kanał>."""
    bloki = []
    for mid in serwisowe:
        bloki.append(f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message '
                     f'service_message" data-post="{kanal}/{mid}"><time datetime="2026-09-19T16:06:10+00:00">')
    for mid, kiedy in posty:
        bloki.append(f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
                     f'<div class="tgme_widget_message" data-post="{kanal}/{mid}">'
                     f'<a class="tgme_widget_message_date"><time datetime="{kiedy.isoformat()}" class="time">')
    return "<html>" + "".join(bloki) + "</html>"


def _fetch(strony: dict[str, str]):
    def f(url):
        return strony[url.rsplit("/", 1)[-1]]
    return f


def _linki(log):
    return sorted({p["link"] for p in log if p.get("action") == "add"})


def test_parser_pomija_komunikaty_serwisowe_i_obce_kanaly():
    html = _strona("fx_passing", [(3, TERAZ - timedelta(days=2)), (8, TERAZ - timedelta(hours=4))])
    html += '<div class="tgme_widget_message_wrap"><div data-post="inny_kanal/99"><time datetime="2026-09-23T10:00:00+00:00">'
    posty = reach.posty_kanalu("fx_passing", fetch=lambda u: html)
    assert [m for m, _ in posty] == [3, 8]
    assert posty[-1][1] == TERAZ - timedelta(hours=4)


def test_pierwszy_skan_zamawia_tylko_najnowszy_swiezy_post():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])
        strona = _strona("fx_passing", [(6, TERAZ - timedelta(hours=28)),
                                        (7, TERAZ - timedelta(hours=27)),
                                        (8, TERAZ - timedelta(hours=4))])
        log = []
        with _dostawca():
            wynik = reach.skanuj_kanaly(s, fetch=_fetch({"fx_passing": strona}),
                                        transport=_transport(log=log), teraz=TERAZ)
            znowu = reach.skanuj_kanaly(s, fetch=_fetch({"fx_passing": strona}),
                                        transport=_transport(log=log), teraz=TERAZ, wymus=True)
        assert wynik["posts"] == ["@fx_passing/8"]
        assert _linki(log) == ["https://t.me/fx_passing/8"]    # #6 i #7 nie od nowa
        assert znowu["ordered"] == 0
    finally:
        s.close()


def test_kolejne_posty_lapie_skan_a_stare_i_bardzo_swieze_pomija():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])
        assert reach._zajmij_post(s, "fx_passing", 8)
        strona = _strona("fx_passing", [(8, TERAZ - timedelta(hours=4)),
                                        (9, TERAZ - timedelta(minutes=30)),
                                        (10, TERAZ - timedelta(seconds=40))])
        log = []
        with _dostawca():
            wynik = reach.skanuj_kanaly(s, fetch=_fetch({"fx_passing": strona}),
                                        transport=_transport(log=log), teraz=TERAZ)
        # #10 ma 40 s — zostaje webhookowi; skan weźmie go przy następnym przebiegu.
        assert wynik["posts"] == ["@fx_passing/9"]
    finally:
        s.close()


def test_skan_nie_dubluje_posta_z_webhooka_ani_z_publikacji():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])
        log = []
        with _dostawca():
            reach.z_kanalu(s, {"message_id": 20, "text": "x",
                               "chat": {"id": -100, "username": "fx_passing"}},
                           transport=_transport(log=log))
            reach.po_publikacji(s, "https://t.me/fx_passing/21", transport=_transport(log=log))
            strona = _strona("fx_passing", [(20, TERAZ - timedelta(hours=1)),
                                            (21, TERAZ - timedelta(minutes=20))])
            wynik = reach.skanuj_kanaly(s, fetch=_fetch({"fx_passing": strona}),
                                        transport=_transport(log=log), teraz=TERAZ)
        assert wynik["ordered"] == 0
        assert _linki(log) == ["https://t.me/fx_passing/20", "https://t.me/fx_passing/21"]
        assert len([p for p in log if p.get("action") == "add"]) == 4   # 2 posty × 2 usługi
    finally:
        s.close()


def test_stary_znacznik_webhooka_pod_id_czatu_dalej_obowiazuje():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])
        reach._ustaw(s, "seen_-100", "30:")
        s.commit()
        with _dostawca():
            wynik = reach.z_kanalu(s, {"message_id": 30, "text": "x",
                                       "chat": {"id": -100, "username": "fx_passing"}},
                                   transport=_transport())
        assert wynik["skipped"] == "duplicate"
    finally:
        s.close()


def test_skan_z_throttlem_i_wylacznikiem():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])

        def fetch(url):  # pragma: no cover - nie ma prawa się wykonać
            raise AssertionError("wyłączony bot nie skanuje")

        with _dostawca():
            assert reach.skanuj_kanaly(s, fetch=fetch, teraz=TERAZ)["skipped"] == "reach bot off"
            reach.zapisz_ustawienia(s, enabled=True)
            pusta = _fetch({"fx_passing": _strona("fx_passing", [])})
            assert reach.skanuj_kanaly(s, fetch=pusta, teraz=TERAZ)["ordered"] == 0
            assert reach.skanuj_kanaly(s, fetch=fetch, teraz=TERAZ + timedelta(minutes=3))["skipped"] == "not yet"
            assert "skipped" not in reach.skanuj_kanaly(s, fetch=pusta, teraz=TERAZ + timedelta(minutes=11))
    finally:
        s.close()


def test_blad_pobrania_strony_nie_wywraca_skanu():
    s = _sesja()
    try:
        _wyczysc(s)
        reach.zapisz_ustawienia(s, enabled=True)
        reach.zapisz_kanaly(s, [{"username": "fx_passing", "on": True}])

        def fetch(url):
            raise OSError("timed out")

        with _dostawca():
            wynik = reach.skanuj_kanaly(s, fetch=fetch, teraz=TERAZ)
        assert wynik["ordered"] == 0 and "timed out" in wynik["errors"][0]
    finally:
        s.close()


# --------------------------------------------------------------------------- #
#  Stan webhooka                                                               #
# --------------------------------------------------------------------------- #
def test_stan_webhooka(monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_bot_token", "T:K", raising=False)
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", "S", raising=False)
    monkeypatch.setattr(main.settings, "app_base_url", "https://protradersfunding.com", raising=False)
    nasz = "https://www.protradersfunding.com/api/telegram/webhook"
    assert main._reach_webhook_stan({"url": ""})["state"] == "off"
    assert main._reach_webhook_stan({"url": nasz})["state"] == "ok"
    assert main._reach_webhook_stan({"url": nasz, "allowed_updates": ["message", "callback_query"]})["state"] \
        == "no_channel_posts"
    obcy = main._reach_webhook_stan({"url": "https://hook.make.com/abc"})
    assert obcy["state"] == "elsewhere" and obcy["host"] == "hook.make.com" and obcy["fixable"]
    assert main._reach_webhook_stan({"error": "Unauthorized"})["state"] == "unknown"


def test_naprawa_webhooka_dopisuje_channel_post_i_pyta_o_cudzy_adres(monkeypatch):
    monkeypatch.setattr(main.settings, "telegram_bot_token", "T:K", raising=False)
    monkeypatch.setattr(main.settings, "telegram_webhook_secret", "S", raising=False)
    monkeypatch.setattr(main.settings, "app_base_url", "https://protradersfunding.com", raising=False)
    stan = {"url": "https://protradersfunding.com/api/telegram/webhook",
            "allowed_updates": ["message", "callback_query"]}
    wolania = []

    def ustaw(url, secret, allowed=None, **k):
        wolania.append((url, secret, allowed))
        stan.update(url=url, allowed_updates=allowed or stan["allowed_updates"])
        return True, ""

    monkeypatch.setattr(main.telegram, "webhook_info", lambda **k: dict(stan))
    monkeypatch.setattr(main.telegram, "ustaw_webhook", ustaw)
    r = client.post("/api/admin/reach/webhook", headers=ADMIN, json={})
    assert r.status_code == 200 and r.json()["webhook"]["state"] == "ok"
    assert wolania[-1] == ("https://protradersfunding.com/api/telegram/webhook", "S",
                           ["callback_query", "channel_post", "message"])

    stan.update(url="https://hook.make.com/abc", allowed_updates=[])
    r = client.post("/api/admin/reach/webhook", headers=ADMIN, json={})
    assert r.status_code == 409 and "hook.make.com" in r.json()["detail"]
    r = client.post("/api/admin/reach/webhook", headers=ADMIN, json={"force": True})
    assert r.status_code == 200 and wolania[-1][2] is None   # domyślna lista zostaje
