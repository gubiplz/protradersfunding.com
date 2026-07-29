"""Testy zakładania realnych kont demo MT5 przez REST MetaApi.

Zero sieci: transport HTTP jest podstawiany atrapą, a zegar i sleep są fałszywe,
więc testy backoffu/throttlingu chodzą natychmiast.
"""
import asyncio

from app.metaapi_provisioning import (
    DemoCredentials,
    DemoSpec,
    MetaApiProvisioner,
    ProvisioningError,
    ProvisioningPending,
)

DEMO_OK = {
    "login": "86053193",
    "password": "2y8kpft",
    "investorPassword": "dc56esco",
    "serverName": "ICMarketsSC-Demo",
}
ACCOUNT_OK = {"id": "1eda642a-a9a3-457c-99af-3bc5e8d5c4c9"}


class FakeTransport:
    """Oddaje kolejne odpowiedzi z listy; zapisuje wszystkie żądania."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, *, headers, json):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        item = self.responses.pop(0) if self.responses else (500, {"error": "brak odpowiedzi"}, {})
        if isinstance(item, Exception):
            raise item
        return item


def _provisioner(transport, **kw):
    kw.setdefault("min_interval_sec", 0)
    kw.setdefault("sleep", _noop_sleep)
    return MetaApiProvisioner("tok-123", transport=transport, **kw)


async def _noop_sleep(_seconds):
    return None


# --------------------------------------------------------------- happy path --
def test_provision_zaklada_konto_i_rejestruje_je():
    t = FakeTransport([(201, DEMO_OK, {}), (201, ACCOUNT_OK, {})])
    out = asyncio.run(
        _provisioner(t).provision("prof-1", DemoSpec(name="Jan Kowalski", email="j@k.pl", balance=100_000))
    )

    assert out["login"] == "86053193"
    assert out["password"] == "2y8kpft"
    assert out["server"] == "ICMarketsSC-Demo"
    assert out["investor_password"] == "dc56esco"
    assert out["metaapi_account_id"] == ACCOUNT_OK["id"]

    # krok 1 poszedł na endpoint dem konkretnego profilu
    assert t.calls[0]["url"].endswith("/users/current/provisioning-profiles/prof-1/mt5-demo-accounts")
    # krok 2 zarejestrował TE SAME poświadczenia do czytania equity
    reg = t.calls[1]["json"]
    assert (reg["login"], reg["password"], reg["server"]) == ("86053193", "2y8kpft", "ICMarketsSC-Demo")
    assert reg["platform"] == "mt5"


def test_naglowki_maja_token_i_32_znakowy_transaction_id():
    t = FakeTransport([(201, DEMO_OK, {}), (201, ACCOUNT_OK, {})])
    asyncio.run(_provisioner(t).provision("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=25_000)))

    for call in t.calls:
        assert call["headers"]["auth-token"] == "tok-123"
        assert len(call["headers"]["transaction-id"]) == 32


# ------------------------------------------------------------------- retry ---
def test_429_jest_ponawiane_z_tym_samym_transaction_id():
    """Idempotencja: ponowienie nie może założyć u brokera drugiego konta."""
    t = FakeTransport([(429, {"error": "slow down"}, {"retry-after": "0"}), (201, DEMO_OK, {})])
    creds = asyncio.run(
        _provisioner(t).create_demo_account("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000))
    )

    assert creds.login == "86053193"
    assert len(t.calls) == 2
    assert t.calls[0]["headers"]["transaction-id"] == t.calls[1]["headers"]["transaction-id"]


def test_blad_sieci_jest_ponawiany():
    t = FakeTransport([ConnectionError("padło"), (201, DEMO_OK, {})])
    creds = asyncio.run(
        _provisioner(t).create_demo_account("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000))
    )
    assert creds.login == "86053193"


def test_202_bez_konca_konczy_sie_pending_a_nie_falszywym_kontem():
    t = FakeTransport([(202, {}, {})] * 3)
    try:
        asyncio.run(
            _provisioner(t, max_attempts=3).create_demo_account(
                "prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000)
            )
        )
    except ProvisioningPending as e:
        assert e.status == 202
        assert len(t.calls) == 3
    else:
        raise AssertionError("oczekiwano ProvisioningPending")


def test_400_nie_jest_ponawiane():
    t = FakeTransport([(400, {"error": "validation failed"}, {})])
    try:
        asyncio.run(
            _provisioner(t, max_attempts=5).create_demo_account(
                "prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000)
            )
        )
    except ProvisioningError as e:
        assert e.status == 400
        assert len(t.calls) == 1, "walidacja nie ma sensu do ponawiania"
    else:
        raise AssertionError("oczekiwano ProvisioningError")


def test_niepelna_odpowiedz_to_blad_a_nie_puste_poswiadczenia():
    t = FakeTransport([(201, {"login": "123"}, {})])  # brak hasła i serwera
    try:
        asyncio.run(
            _provisioner(t).create_demo_account("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000))
        )
    except ProvisioningError:
        pass
    else:
        raise AssertionError("brak hasła/serwera musi wysadzić provisioning")


def test_brak_id_konta_przy_rejestracji_to_blad():
    t = FakeTransport([(201, DEMO_OK, {}), (201, {"status": "ok"}, {})])
    try:
        asyncio.run(_provisioner(t).provision("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000)))
    except ProvisioningError:
        pass
    else:
        raise AssertionError("bez metaapi_account_id nie da się czytać equity")


# ---------------------------------------------------------------- throttle ---
def test_throttle_trzyma_minimalny_odstep():
    """Brokerzy rate-limitują dema — sprawdzamy, że czekamy zadany czas."""
    slept = []
    now = [100.0]

    async def fake_sleep(sec):
        slept.append(sec)
        now[0] += sec

    t = FakeTransport([(201, DEMO_OK, {}), (201, ACCOUNT_OK, {})])
    p = MetaApiProvisioner(
        "tok", transport=t, min_interval_sec=2.0, sleep=fake_sleep, clock=lambda: now[0]
    )
    asyncio.run(p.provision("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000)))

    assert slept and abs(slept[0] - 2.0) < 1e-6, f"oczekiwano odstępu 2 s, było {slept}"


def test_retry_after_z_naglowka_ma_pierwszenstwo_przed_backoffem():
    slept = []

    async def fake_sleep(sec):
        slept.append(sec)

    t = FakeTransport([(429, {}, {"retry-after": "7"}), (201, DEMO_OK, {})])
    p = MetaApiProvisioner("tok", transport=t, min_interval_sec=0, sleep=fake_sleep, backoff_base_sec=99)
    asyncio.run(p.create_demo_account("prof-1", DemoSpec(name="A B", email="a@b.pl", balance=5_000)))

    assert 7.0 in slept, f"powinniśmy uszanować Retry-After=7, spaliśmy {slept}"


# -------------------------------------------------------------------- spec ---
def test_spec_uzupelnia_nazwisko_bo_broker_wymaga_dwoch_czlonow():
    body = DemoSpec(name="Jan", email="j@k.pl", balance=10_000).to_body()
    assert len(body["name"].split()) >= 2


def test_spec_server_name_albo_keywords():
    z_serwerem = DemoSpec(name="A B", email="a@b.pl", balance=1, server_name="ICMarketsSC-Demo").to_body()
    assert z_serwerem["serverName"] == "ICMarketsSC-Demo"
    assert "keywords" not in z_serwerem

    z_keywords = DemoSpec(name="A B", email="a@b.pl", balance=1, keywords=["IC Markets"]).to_body()
    assert z_keywords["keywords"] == ["IC Markets"]
    assert "serverName" not in z_keywords


def test_spec_balance_jest_liczba_calkowita():
    body = DemoSpec(name="A B", email="a@b.pl", balance=99_999.7).to_body()
    assert body["balance"] == 100_000 and isinstance(body["balance"], int)


def test_credentials_akceptuje_snake_case_z_sdk():
    creds = DemoCredentials.from_body(
        {"login": 1, "password": "p", "server_name": "X-Demo", "investor_password": "i"}
    )
    assert (creds.login, creds.server, creds.investor_password) == ("1", "X-Demo", "i")
