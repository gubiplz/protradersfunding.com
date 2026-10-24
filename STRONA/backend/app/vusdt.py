"""Testowe wypłaty w vUSDT na Polygonie — PTF sam podpisuje i wysyła transfer.

vUSDT („Virtual USDT", 6 miejsc po przecinku) to token TESTOWY bez wartości,
kontrakt z ~/vusdt-faucet. Ścieżka: trader prosi → admin odblokowuje → trader
podaje kwotę + adres 0x (+ Turnstile) → transfer wychodzi sam. Włącza go
`Settings.vusdt_payouts_enabled` (flaga VUSDT_TEST_PAYOUTS + klucze).

Logika przeniesiona z kranika (lib/sender.ts, lib/sweep.ts):
- jeden nadawca naraz: `pg_advisory_xact_lock` (Postgres) albo zamek procesu
  (SQLite) — dwa równoległe podpisy wzięłyby ten sam nonce;
- nonce = max(licznik `pending` z łańcucha, nasz ostatni świeży nonce + 1),
  bo publiczny RPC spóźnia się z `pending`;
- strażnik salda: bez POL na gaz albo bez vUSDT wysyłka czeka w kolejce
  (status `queued`) zamiast palić nonce;
- EIP-1559: napiwek min. 30 gwei (Polygon odrzuca niższe), sufit opłaty z env;
- odświeżanie przy pollingu: receipt → confirmed/failed; transakcja wypchnięta
  z mempoola → z powrotem do kolejki (maks. 2 razy).
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, text

from .config import get_settings
from .models import VusdtTransfer

CHAIN_ID = 137
DECIMALS = 6
SYMBOL = "vUSDT"
EXPLORER = "https://polygonscan.com"
SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SEK = 12
GWEI = 10 ** 9
MIN_TIP = 30 * GWEI
MAX_REQUEUES = 2
DROPPED_AFTER = timedelta(minutes=3)
POLYGON_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZAMEK_KLUCZ = 72_730_137
_zamek_procesu = threading.Lock()


class VusdtError(Exception):
    pass


class RpcError(VusdtError):
    """Błąd JSON-RPC zwrócony przez węzeł (nie awaria łącza)."""


def enabled() -> bool:
    return get_settings().vusdt_payouts_enabled


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_units(amount: float) -> int:
    return int((Decimal(str(amount)) * 10 ** DECIMALS).quantize(Decimal(1), ROUND_HALF_UP))


def fmt_units(units: int) -> str:
    return f"{Decimal(units) / 10 ** DECIMALS:,.2f}"


def details_json(details: dict | None) -> str:
    """Dane wypłaty w trybie testowym: tylko Polygon + adres 0x."""
    adres = str((details or {}).get("address") or "").strip()
    if not POLYGON_ADDRESS.match(adres):
        raise ValueError("Enter a Polygon wallet address: 0x followed by 40 hex characters")
    if adres.lower() in {sender_address().lower(), get_settings().vusdt_token_address.lower(),
                         "0x" + "0" * 40}:
        raise ValueError("Enter your own wallet address, not the payout wallet or the token contract")
    return json.dumps({"network": "POLYGON", "address": checksum(adres), "token": SYMBOL})


def checksum(adres: str) -> str:
    from eth_utils import to_checksum_address
    return to_checksum_address(adres)


# --------------------------------------------------------------------------- #
#  Sieć: Turnstile + JSON-RPC                                                  #
# --------------------------------------------------------------------------- #
def verify_turnstile(token: str | None, remote_ip: str | None) -> bool:
    if not token or len(token) > 2048:
        return False
    form = {"secret": get_settings().turnstile_secret_key, "response": token}
    if remote_ip and remote_ip not in ("127.0.0.1", "::1", "testclient"):
        form["remoteip"] = remote_ip
    req = urllib.request.Request(SITEVERIFY, data=urllib.parse.urlencode(form).encode())
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get("success") is True
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"[vusdt] siteverify: {e}")
        return False


def _rpc_urls() -> list[str]:
    return [u.strip() for u in get_settings().polygon_rpc_urls.split(",") if u.strip()]


def rpc(method: str, params: list):
    """Wywołanie JSON-RPC z zapasowymi węzłami. Błąd ZWRÓCONY przez węzeł
    (np. „nonce too low") nie przeskakuje na następny — to odpowiedź, nie awaria."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    ostatni = None
    for url in _rpc_urls():
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "ptf-vusdt/1"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as r:
                odp = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            ostatni = e
            continue
        if "error" in odp:
            raise RpcError(str((odp["error"] or {}).get("message") or odp["error"]))
        return odp.get("result")
    raise VusdtError(f"all Polygon RPCs failed: {ostatni}")


def _hex(n: int) -> str:
    return hex(n)


def _int(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v or 0)


def _konto():
    from eth_account import Account
    klucz = get_settings().vusdt_sender_private_key.strip()
    return Account.from_key(klucz if klucz.startswith("0x") else "0x" + klucz)


def sender_address() -> str:
    return _konto().address


def _calldata_transfer(to: str, units: int) -> str:
    return "0xa9059cbb" + to[2:].lower().rjust(64, "0") + format(units, "x").rjust(64, "0")


def _vusdt_balance(holder: str) -> int:
    data = "0x70a08231" + holder[2:].lower().rjust(64, "0")
    return _int(rpc("eth_call", [{"to": get_settings().vusdt_token_address, "data": data}, "latest"]))


def _fees() -> tuple[int, int] | None:
    """(maxFeePerGas, maxPriorityFeePerGas) albo None, gdy sieć jest droższa niż sufit."""
    s = get_settings()
    base = _int((rpc("eth_getBlockByNumber", ["latest", False]) or {}).get("baseFeePerGas"))
    try:
        tip = max(_int(rpc("eth_maxPriorityFeePerGas", [])), MIN_TIP)
    except RpcError:
        tip = MIN_TIP
    cap = int(s.vusdt_max_fee_gwei * GWEI)
    if base + tip > cap:
        return None
    return min(2 * base + tip, cap), tip


# --------------------------------------------------------------------------- #
#  Kolejka                                                                     #
# --------------------------------------------------------------------------- #
@contextmanager
def jeden_nadawca(session):
    """Zamek na czas transakcji sesji — puszcza go commit/rollback wołającego."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ZAMEK_KLUCZ})
        yield
    else:
        with _zamek_procesu:
            yield


def _next_nonce(session, adres: str) -> int:
    chain = _int(rpc("eth_getTransactionCount", [adres, "pending"]))
    # Tylko ŚWIEŻE wysyłki: stary nonce z transakcji, która wypadła z mempoola,
    # zrobiłby dziurę i każda następna wisiałaby za nią w nieskończoność.
    nasz = (session.query(func.max(VusdtTransfer.nonce))
            .filter(VusdtTransfer.status == "sent",
                    VusdtTransfer.sent_at > _utcnow() - timedelta(minutes=2)).scalar())
    return max(chain, (nasz + 1) if nasz is not None else 0)


def process_queue(session) -> str | None:
    """Wysyła zaległe transfery. Zwraca powód zatrzymania (LOW_POL, LOW_VUSDT,
    FEE_CAP, RPC) albo None. Commituje sesję — to zwalnia zamek."""
    konto = _konto()
    powod = None
    try:
        with jeden_nadawca(session):
            kolejka = (session.query(VusdtTransfer).filter(VusdtTransfer.status == "queued")
                       .order_by(VusdtTransfer.id).limit(5).all())
            if not kolejka:
                session.commit()
                return None
            pol = _int(rpc("eth_getBalance", [konto.address, "latest"]))
            if pol < int(get_settings().vusdt_min_pol * 10 ** 18):
                powod = "LOW_POL"
            vusdt = _vusdt_balance(konto.address) if not powod else 0
            oplaty = _fees() if not powod else None
            if not powod and oplaty is None:
                powod = "FEE_CAP"
            for t in kolejka:
                if powod:
                    t.error = powod
                    continue
                if vusdt < t.amount_units:
                    powod = t.error = "LOW_VUSDT"
                    continue
                _wyslij(session, konto, t, *oplaty)
                if t.status == "sent":
                    vusdt -= t.amount_units
                elif t.error and t.error.startswith("RPC"):
                    powod = "RPC"
            session.commit()
    except VusdtError as e:
        session.rollback()
        print(f"[vusdt] kolejka: {e}")
        return "RPC"
    except Exception:
        session.rollback()
        raise
    return powod


def _wyslij(session, konto, t: VusdtTransfer, max_fee: int, tip: int) -> None:
    s = get_settings()
    data = _calldata_transfer(t.address, int(t.amount_units))
    try:
        gas = int(_int(rpc("eth_estimateGas", [{"from": konto.address,
                                                "to": s.vusdt_token_address, "data": data}])) * 1.25)
    except RpcError as e:
        t.error = f"RPC estimate: {e}"[:200]
        return
    nonce = _next_nonce(session, konto.address)
    for _ in range(3):
        podpisana = konto.sign_transaction({
            "type": 2, "chainId": CHAIN_ID, "nonce": nonce, "to": s.vusdt_token_address,
            "value": 0, "data": data, "gas": gas,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": tip})
        try:
            tx_hash = rpc("eth_sendRawTransaction", ["0x" + bytes(podpisana.raw_transaction).hex()])
        except RpcError as e:
            msg = str(e).lower()
            if "already known" in msg:      # dokładnie ta transakcja już jest w mempoolu
                tx_hash = None
            # Nonce zajęty (np. kranik wysłał z tego samego portfela) — następny.
            elif "nonce too low" in msg or "replacement transaction" in msg:
                nonce += 1
                continue
            elif "insufficient funds" in msg:
                t.error = "LOW_POL"
                return
            else:
                t.error = f"RPC send: {e}"[:200]
                return
        t.status, t.tx_hash, t.nonce = "sent", tx_hash or "0x" + bytes(podpisana.hash).hex(), nonce
        t.sent_at, t.error = _utcnow(), None
        session.flush()
        return
    t.error = "RPC send: nonce kept colliding"


def refresh(session, t: VusdtTransfer) -> VusdtTransfer:
    """Polling z portalu napędza kolejkę i domyka statusy."""
    if t.status == "queued":
        process_queue(session)
        session.refresh(t)
        return t
    if t.status != "sent" or not t.tx_hash:
        return t
    try:
        rc = rpc("eth_getTransactionReceipt", [t.tx_hash])
        if rc:
            t.status = "confirmed" if _int(rc.get("status")) == 1 else "failed"
            t.confirmed_at = _utcnow()
            if t.status == "failed":
                t.error = "Reverted on-chain"
        elif t.sent_at and _utcnow() - t.sent_at > DROPPED_AFTER:
            latest = _int(rpc("eth_getTransactionCount", [sender_address(), "latest"]))
            if t.nonce is not None and latest > t.nonce:
                # Nonce zużyty przez inną transakcję, a naszej nie ma — wypadła.
                if (t.requeues or 0) < MAX_REQUEUES:
                    t.status, t.tx_hash, t.nonce, t.sent_at = "queued", None, None, None
                    t.requeues = (t.requeues or 0) + 1
                else:
                    t.status, t.error = "failed", "Dropped from the network"
        session.commit()
    except VusdtError as e:
        session.rollback()
        print(f"[vusdt] refresh {t.id}: {e}")
    return t


_NOTKI = {
    "LOW_POL": "The test payout wallet is out of gas. Your transfer is saved and goes out automatically after a top-up.",
    "LOW_VUSDT": "The test payout wallet is out of vUSDT. Your transfer is saved and goes out automatically after a refill.",
    "FEE_CAP": "Polygon fees are unusually high right now. Your transfer goes out as soon as they drop.",
}


def view(t: VusdtTransfer) -> dict:
    nota = None
    if t.status == "queued":
        nota = _NOTKI.get(t.error or "", "In line, sending in a few seconds…")
    elif t.status == "failed":
        nota = "The transfer failed on-chain. Contact support."
    return {
        "status": t.status, "address": t.address, "amount": fmt_units(int(t.amount_units)),
        "symbol": SYMBOL, "tx_hash": t.tx_hash,
        "explorer_url": f"{EXPLORER}/tx/{t.tx_hash}" if t.tx_hash else None,
        "note": nota,
    }


def public_config() -> dict | None:
    """Co portal musi wiedzieć o trybie testowym (bez sekretów)."""
    if not enabled():
        return None
    s = get_settings()
    return {"site_key": s.turnstile_site_key, "token": s.vusdt_token_address, "symbol": SYMBOL,
            "decimals": DECIMALS, "chain_id": CHAIN_ID,
            "image": "/static/img/vusdt-256.png"}
