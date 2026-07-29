"""Realne zakładanie kont demo MT5 przez REST API MetaApi (bez SDK).

Dlaczego REST, a nie `metaapi-cloud-sdk`:
  * SDK jest ciężkie i asynchronicznie „żywe" (trzyma połączenia) — do jednorazowego
    założenia konta wystarczą dwa POST-y;
  * REST da się przetestować bez sieci (patrz `tests/test_provisioning_api.py`);
  * brak SDK nie blokuje provisioningu, gdy i tak czytamy equity innym kanałem.

Przepływ (dwa kroki, oba wymagane):
  1. POST /users/current/provisioning-profiles/{profileId}/mt5-demo-accounts
     → broker zakłada PRAWDZIWE konto demo i zwraca login/hasło/serwer.
  2. POST /users/current/accounts
     → rejestrujemy te poświadczenia w MetaApi, żeby móc czytać equity i zamykać
       pozycje przy breachu. Zwraca `metaapi_account_id`.

Idempotencja: nagłówek `transaction-id` (32 znaki) jest STAŁY dla jednej logicznej
próby założenia konta. Dzięki temu ponowienie po timeoucie/429 nie tworzy drugiego
konta u brokera — MetaApi rozpozna tę samą transakcję.

Limit tempa: brokerzy rate-limitują rejestrację dem, a MetaApi wprost zaleca
„reasonably low rate". Cały proces przechodzi przez wspólny `asyncio.Lock`
z minimalnym odstępem między wywołaniami (METAAPI_DEMO_MIN_INTERVAL_SEC).
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"

# Statusy, przy których ponawiamy z tym samym transaction-id.
RETRY_STATUSES = frozenset({202, 408, 429, 500, 502, 503, 504})


class ProvisioningError(RuntimeError):
    """Nie udało się założyć/zarejestrować konta."""

    def __init__(self, message: str, *, status: int | None = None, body: object = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ProvisioningPending(ProvisioningError):
    """MetaApi przyjął zlecenie (202), ale konto nie było gotowe w limicie prób."""


@dataclass
class DemoSpec:
    """Dane, których broker wymaga do założenia dema."""

    name: str
    email: str
    balance: float
    phone: str = "+10000000000"
    leverage: int = 100
    account_type: str = "standard"
    server_name: str | None = None
    keywords: list[str] = field(default_factory=list)

    def to_body(self) -> dict:
        # MetaApi wymaga imienia i nazwiska — samo imię bywa odrzucane przez brokera.
        name = " ".join((self.name or "").split()) or "Prop Trader"
        if len(name.split()) < 2:
            name = f"{name} Trader"
        body: dict = {
            "accountType": self.account_type,
            "balance": int(round(self.balance)),
            "email": self.email,
            "leverage": int(self.leverage),
            "name": name[:64],
            "phone": self.phone,
        }
        # serverName ALBO keywords — keywords każą MetaApi wyszukać serwer po nazwie brokera.
        if self.server_name:
            body["serverName"] = self.server_name
        if self.keywords:
            body["keywords"] = list(self.keywords)
        return body


@dataclass
class DemoCredentials:
    login: str
    password: str
    server: str
    investor_password: str | None = None

    @classmethod
    def from_body(cls, body: dict) -> "DemoCredentials":
        login = body.get("login")
        password = body.get("password")
        server = body.get("serverName") or body.get("server_name") or body.get("server")
        if not (login and password and server):
            raise ProvisioningError(
                f"MetaApi response is missing credentials: {sorted(body)}", body=body
            )
        return cls(
            login=str(login),
            password=str(password),
            server=str(server),
            investor_password=body.get("investorPassword") or body.get("investor_password"),
        )


def new_transaction_id() -> str:
    """32 znaki — tyle wymaga nagłówek `transaction-id`."""
    return secrets.token_hex(16)


class MetaApiProvisioner:
    """Klient REST. `transport` pozwala podstawić fałszywy HTTP w testach.

    Kontrakt transportu:
        async def __call__(method: str, url: str, *, headers: dict, json: dict)
            -> tuple[int, dict | None, dict]   # (status, body, response_headers)
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport=None,
        max_attempts: int = 5,
        min_interval_sec: float = 2.0,
        backoff_base_sec: float = 1.5,
        timeout_sec: float = 30.0,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ) -> None:
        if not token:
            raise ProvisioningError("METAAPI_TOKEN is missing — cannot create a demo account")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport or self._httpx_transport
        self._max_attempts = max(1, max_attempts)
        self._min_interval = max(0.0, min_interval_sec)
        self._backoff_base = backoff_base_sec
        self._timeout = timeout_sec
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_call_at: float | None = None

    # ---------------------------------------------------------------- HTTP --
    async def _httpx_transport(self, method: str, url: str, *, headers: dict, json: dict):
        import httpx  # lazy — testy nie potrzebują sieci

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(method, url, headers=headers, json=json)
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body, dict(resp.headers)

    async def _throttle(self) -> None:
        """Trzyma minimalny odstęp między wywołaniami do brokera."""
        if self._min_interval <= 0:
            return
        now = self._clock()
        if self._last_call_at is not None:
            wait = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                await self._sleep(wait)
        self._last_call_at = self._clock()

    def _retry_delay(self, attempt: int, resp_headers: dict) -> float:
        retry_after = (resp_headers or {}).get("retry-after") or (resp_headers or {}).get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        return self._backoff_base * (2 ** attempt)

    async def _post(self, path: str, body: dict, *, transaction_id: str) -> dict:
        """POST z ponowieniami. Ten sam transaction-id we wszystkich próbach."""
        url = f"{self._base_url}{path}"
        headers = {
            "auth-token": self._token,
            "transaction-id": transaction_id,
            "content-type": "application/json",
            "accept": "application/json",
        }
        last_status: int | None = None
        last_body: object = None
        for attempt in range(self._max_attempts):
            await self._throttle()
            try:
                status, resp_body, resp_headers = await self._transport(
                    "POST", url, headers=headers, json=body
                )
            except Exception as e:  # sieć padła — ponawiamy
                last_status, last_body, resp_headers = None, str(e), {}
                status = None
            else:
                last_status, last_body = status, resp_body
                if 200 <= status < 300 and status != 202 and isinstance(resp_body, dict):
                    return resp_body
                if status not in RETRY_STATUSES:
                    raise ProvisioningError(
                        f"MetaApi {path} → HTTP {status}: {_short(resp_body)}",
                        status=status,
                        body=resp_body,
                    )
            if attempt < self._max_attempts - 1:
                await self._sleep(self._retry_delay(attempt, resp_headers))

        if last_status == 202:
            raise ProvisioningPending(
                f"MetaApi {path} wciąż przetwarza zlecenie (202) po {self._max_attempts} próbach",
                status=202,
                body=last_body,
            )
        raise ProvisioningError(
            f"MetaApi {path} unreachable after {self._max_attempts} attempts "
            f"(last: {last_status} {_short(last_body)})",
            status=last_status,
            body=last_body,
        )

    # ------------------------------------------------------------ operacje --
    async def create_demo_account(
        self, profile_id: str, spec: DemoSpec, *, transaction_id: str | None = None
    ) -> DemoCredentials:
        """Krok 1: broker zakłada prawdziwe konto demo MT5."""
        if not profile_id:
            raise ProvisioningError("METAAPI_PROVISIONING_PROFILE_ID is missing")
        body = await self._post(
            f"/users/current/provisioning-profiles/{profile_id}/mt5-demo-accounts",
            spec.to_body(),
            transaction_id=transaction_id or new_transaction_id(),
        )
        return DemoCredentials.from_body(body)

    async def register_account(
        self,
        creds: DemoCredentials,
        *,
        name: str,
        provisioning_profile_id: str | None = None,
        keywords: list[str] | None = None,
        transaction_id: str | None = None,
    ) -> str:
        """Krok 2: rejestruje konto w MetaApi → zwraca metaapi_account_id."""
        body = {
            "name": name[:64],
            "type": "cloud",
            "login": creds.login,
            "password": creds.password,
            "server": creds.server,
            "platform": "mt5",
            "magic": 0,
            "application": "MetaApi",
        }
        if keywords:
            body["keywords"] = list(keywords)
        if provisioning_profile_id:
            body["provisioningProfileId"] = provisioning_profile_id
        resp = await self._post(
            "/users/current/accounts", body, transaction_id=transaction_id or new_transaction_id()
        )
        account_id = resp.get("id") or resp.get("_id") or resp.get("accountId")
        if not account_id:
            raise ProvisioningError(
                f"MetaApi did not return an account id: {sorted(resp)}", body=resp
            )
        return str(account_id)

    async def provision(self, profile_id: str, spec: DemoSpec) -> dict:
        """Oba kroki naraz. Zwraca dict zgodny z `Feed.provision()`.

        Lock serializuje provisioning w obrębie procesu — dwa równoległe zakupy
        nie wyślą do brokera dwóch żądań w tej samej milisekundzie.
        """
        async with self._lock:
            creds = await self.create_demo_account(profile_id, spec)
            account_id = await self.register_account(
                creds,
                name=spec.name,
                provisioning_profile_id=profile_id,
                keywords=spec.keywords or None,
            )
        return {
            "metaapi_account_id": account_id,
            "login": creds.login,
            "password": creds.password,
            "server": creds.server,
            "investor_password": creds.investor_password,
        }


def _short(body: object, limit: int = 300) -> str:
    text = str(body)
    return text if len(text) <= limit else text[:limit] + "…"


def make_provisioner(settings=None) -> MetaApiProvisioner | None:
    """Buduje provisionera z ustawień; None gdy nie skonfigurowano."""
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    if not (settings.metaapi_token and settings.metaapi_provisioning_profile_id):
        return None
    return MetaApiProvisioner(
        settings.metaapi_token,
        min_interval_sec=settings.metaapi_demo_min_interval_sec,
        max_attempts=settings.metaapi_demo_max_attempts,
    )


def spec_from_account(acc, trader, settings) -> DemoSpec:
    """Buduje DemoSpec z rekordów Account/Trader."""
    return DemoSpec(
        name=(getattr(trader, "full_name", None) or acc.trader_name or "Prop Trader"),
        email=(getattr(trader, "email", None) or settings.mail_from),
        balance=acc.initial_balance,
        phone=(getattr(trader, "phone", None) or settings.metaapi_demo_phone),
        leverage=settings.metaapi_demo_leverage,
        account_type=settings.metaapi_account_type or "standard",
        server_name=(settings.metaapi_demo_server_name or None),
        keywords=[k.strip() for k in (settings.metaapi_demo_keywords or "").split(",") if k.strip()],
    )
