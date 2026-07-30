"""Provisioning konta challenge po opłaceniu zamówienia.

DOMYŚLNIE (`MT5_PROVISIONING=false`): konto powstaje NATYCHMIAST z poświadczeniami
wygenerowanymi lokalnie — 9-cyfrowy login, hasło w formacie MetaQuotes-Demo — i od
razu jest `active`. Nie leci ani jeden request do MetaQuotes. Konta z tej ścieżki
mają `mt5_backed=False`, żeby po ewentualnym włączeniu feedu poller nie próbował
się nimi logować do web terminala wymyślonym loginem.

UWAGA: te poświadczenia wskazują na serwer, na którym konta NIE MA — trader, który
spróbuje zalogować się w MetaTraderze, dostanie błąd. To świadomy etap przejściowy;
`MT5_PROVISIONING=true` przywraca realne konta poniżej.

`MT5_PROVISIONING=true` (jak firmy fundingowe bez własnego serwera MT5): konto
startuje jako 'provisioning' i czeka na poświadczenia z PULI, którą admin
uzupełnia ręcznie w panelu (login, hasło, serwer, rozmiar). Poller przydziela
pierwszy wolny rachunek o pasującym rozmiarze i zapisuje, komu przypadł. Gdy
puli brakuje rachunku danego rozmiaru, konto po prostu czeka — panel pokazuje
takie zamówienia w sekcji MT5 Pool.

Pula jest domyślnym i jedynym źródłem, bo brokerzy blokują programowe zakładanie
dem, a admin wie, które rachunki naprawdę istnieją. `PROVISIONING_SOURCE=auto`
przywraca wcześniejszą ścieżkę: najpierw próba założenia konta demo (web terminal
MetaQuotes / REST MetaApi, z backoffem 30 s → 30 min), pula dopiero awaryjnie.

Dopiero po uzyskaniu poświadczeń konto przechodzi w 'active' i trader dostaje
maila. Do tego czasu nie jest handlowalne.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from time import monotonic

from . import metaapi_provisioning, metaquotes_web, notify
from .config import get_settings
from .models import Account, AppSetting, CreditLedger, Order, PoolAccount, Product, Trader

PLATFORM_SERVER = "MetaQuotes-Demo"


def _gen_login() -> str:
    """9 cyfr — tyle ma numer rachunku na MetaQuotes-Demo."""
    return str(secrets.randbelow(900_000_000) + 100_000_000)


def _gen_password() -> str:
    """Format jak hasla wydawane przez MetaQuotes-Demo: 8 znakow, male litery
    i cyfry (np. `ichhl00j`). Placeholder ma wygladac jak dane z tego serwera."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def create_account_from_order(session, order: Order) -> Account:
    """Tworzy konto challenge zgodnie z parametrami produktu z zamówienia."""
    product = session.query(Product).filter(Product.key == order.product_key).first()
    trader = session.get(Trader, order.trader_id)
    now = datetime.now(timezone.utc)
    settings = get_settings()
    real_mode = real_provisioning_enabled(settings)

    login = _gen_login()
    password = _gen_password()
    bal = product.account_size

    # Gdy konto ma dostac REALNE poswiadczenia (kanal MetaQuotes/MetaApi), nie
    # wypelniamy ich placeholderem — inaczej trader przez chwile widzi w portalu
    # login, ktory po chwili znika. Do czasu provisioningu pola pozostaja puste,
    # a UI pokazuje status.
    acc = Account(
        login=login, trader_name=trader.full_name or trader.email, trader_id=trader.id,
        platform_login=(None if real_mode else login),
        platform_password=(None if real_mode else password),
        platform_server=(None if real_mode else PLATFORM_SERVER),
        # False = poswiadczenia wygenerowane lokalnie, za kontem nie stoi rachunek MT5
        mt5_backed=real_mode,
        product_key=product.key, preset=product.key, initial_balance=bal,
        steps=product.steps,
        profit_target_p1=product.profit_target_p1, profit_target_p2=product.profit_target_p2,
        max_daily_loss_pct=product.max_daily_loss_pct, max_overall_loss_pct=product.max_overall_loss_pct,
        min_trading_days=product.min_trading_days, drawdown_type=product.drawdown_type,
        profit_split_pct=product.profit_split_pct,
        max_lots=getattr(product, "max_lots", 0.0) or 0.0,
        # Instant funding (steps=0) omija ewaluacje — konto od razu jest funded.
        phase=("funded" if product.steps == 0 else "eval_1"),
        # przy realnym provisioningu konto czeka na poświadczenia (nie jest jeszcze handlowalne)
        status=("provisioning" if real_mode
                else ("funded" if product.steps == 0 else "active")),
        source=("grant" if order.provider == "grant" else "purchase"),
        grant_note=(order.coupon if order.provider == "grant" else None),
        bogo_paid_size=_bogo_paid_size(session, order),
        weekend_trading=bool(getattr(order, "weekend_trading", False)),
        balance=bal, equity=bal, peak_equity=bal, day_start_equity=bal, day_start_balance=bal,
        day_key=now.strftime("%Y-%m-%d"),
        created_at=now, started_at=now,
    )
    session.add(acc)
    session.flush()  # nadaje acc.id

    order.status = "paid"
    order.account_id = acc.id
    order.paid_at = now
    # Kredyty sklepowe schodza z salda dopiero TERAZ — platnosc jest domknieta.
    # Ponowny min() na wypadek rownoleglego zakupu, ktory zdazyl zuzyc saldo;
    # zamowienia nie wywracamy (klient juz zaplacil pomniejszona kwote).
    zuzycie = min(round(float(getattr(order, "credits_used", 0) or 0), 2),
                  round(float(trader.credits_usd or 0), 2))
    if zuzycie > 0:
        trader.credits_usd = round(float(trader.credits_usd) - zuzycie, 2)
        order.credits_used = zuzycie
        session.add(CreditLedger(trader_id=trader.id, amount=-zuzycie,
                                 note=f"Applied to order #{order.id}", order_id=order.id))
    session.commit()

    if not real_mode:
        notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
    # przy realnym provisioningu: konto zostaje 'provisioning' — poller je uzbroi i wyśle mail
    return acc


def real_provisioning_enabled(settings=None) -> bool:
    """Czy konto ma czekać na REALNE poświadczenia MT5.

    Jedna flaga, bo realnych ścieżek jest kilka (web terminal, MetaApi, pula
    gotowych kont) i pula nie wymaga włączenia żadnego kanału. Domyślnie
    wyłączone: konto dostaje poświadczenia wygenerowane lokalnie i jest
    handlowalne od razu.
    """
    return bool((settings or get_settings()).mt5_provisioning)


def _bogo_paid_size(session, order) -> float | None:
    """Rozmiar tieru, za który klient zapłacił (promocja BOGO). None = brak."""
    key = getattr(order, "bogo_paid_key", None)
    if not key:
        return None
    paid = session.query(Product).filter(Product.key == key).first()
    return paid.account_size if paid else None


def _creds_event(acc: Account) -> str:
    """Konto przyznane przez admina dostaje wlasny szablon maila."""
    return "challenge_granted" if getattr(acc, "source", "purchase") == "grant" else "credentials"


def _creds_ctx(trader: Trader, acc: Account) -> dict:
    return {
        "name": trader.full_name or trader.email, "login": acc.platform_login,
        "platform_login": acc.platform_login, "platform_password": acc.platform_password,
        "platform_server": acc.platform_server, "initial_balance": acc.initial_balance,
        "steps": acc.steps, "product_key": acc.product_key,
        "profit_split_pct": acc.profit_split_pct,
        "grant_note": getattr(acc, "grant_note", None),
        "bogo_paid_size": getattr(acc, "bogo_paid_size", None),
        "email": trader.email,
    }


def claim_pool_account(session, acc: Account) -> bool:
    """Przydziel kontu wolne konto MT5 z puli o pasującym rozmiarze. True gdy się udało.

    Przy okazji zapisuje, KOMU rachunek przypadł i kiedy — bez tego pula jest
    workiem loginów, z którego nie da się odtworzyć, czyj jest dany rachunek.
    """
    pool = (session.query(PoolAccount)
            .filter(PoolAccount.claimed == False,  # noqa: E712
                    PoolAccount.account_size == acc.initial_balance)
            .order_by(PoolAccount.id).first())
    if not pool:
        return False
    # metaapi_account_id zwykle jest puste (admin go nie podaje) — kopiujemy tylko,
    # gdy ktoś kiedyś zarejestrował ten rachunek w MetaApi.
    if pool.metaapi_account_id:
        acc.metaapi_account_id = pool.metaapi_account_id
    acc.platform_login = pool.platform_login
    acc.platform_password = pool.platform_password
    acc.platform_server = pool.platform_server
    acc.login = pool.platform_login
    # Wpis symulowany = zmyslone poswiadczenia; realny feed nie ma sie nimi logowac.
    acc.mt5_backed = not bool(getattr(pool, "simulated", False))
    pool.claimed = True
    pool.claimed_by_account_id = acc.id
    pool.claimed_by_trader_id = acc.trader_id
    pool.claimed_at = datetime.now(timezone.utc)
    return True


async def provision_pending(session_factory, feed) -> None:
    """Dla każdego konta w stanie 'provisioning' próbuje przydzielić pulę (lub,
    opcjonalnie, auto-utworzyć konto przez MetaApi). Wołane z pollera w każdej pętli."""
    s = session_factory()
    try:
        ids = [a.id for a in s.query(Account).filter(Account.status == "provisioning").all()]
    finally:
        s.close()
    for aid in ids:
        await _provision_one(session_factory, feed, aid)


async def _provision_one(session_factory, feed, aid: int) -> None:
    s = session_factory()
    try:
        acc = s.get(Account, aid)
        if not acc or acc.status != "provisioning":
            return
        # Konto zalozone recznie przez admina moze nie miec wlasciciela — bez tego
        # warunku SQLAlchemy ostrzega o odpytywaniu o klucz NULL przy kazdym tyknieciu.
        trader = s.get(Trader, acc.trader_id) if acc.trader_id else None
        settings = get_settings()

        # 0) Realny provisioning wyłączony — dokończ konto lokalnie. Bez tego konta
        #    założone przy poprzedniej konfiguracji wisiałyby w 'provisioning' na wieki.
        if not real_provisioning_enabled(settings):
            _apply_local_credentials(acc)
            acc.status = "funded" if acc.phase == "funded" else "active"
            s.commit()
            if trader:
                notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
            print(f"[provisioning] konto {aid} aktywne z lokalnymi poświadczeniami "
                  f"({acc.platform_login}@{acc.platform_server})")
            return

        # 1) Automatyczne zakładanie konta demo — TYLKO gdy ktoś świadomie wybierze
        #    PROVISIONING_SOURCE=auto. Domyślnie ta gałąź nie jest brana pod uwagę:
        #    źródłem jest wyłącznie pula, którą admin uzupełnia ręcznie. Brokerzy
        #    i tak blokują programowe zakładanie dem, a admin wie, które rachunki
        #    naprawdę istnieją.
        if (settings.provisioning_source == "auto"
                and (settings.metaquotes_web_enabled or settings.metaapi_auto_create)
                and _may_attempt(aid)):
            creds = await _create_demo_account(feed, acc, trader, settings)
            if creds:
                _apply_credentials(acc, creds)
                acc.status = "funded" if acc.phase == "funded" else "active"
                s.commit()
                _clear_backoff(aid)
                if trader:
                    notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
                print(f"[provisioning] konto {aid} = realne demo MT5 "
                      f"{acc.platform_login}@{acc.platform_server} (metaapi_id={acc.metaapi_account_id})")
                return

        # 2) PULA gotowych kont — domyślnie jedyne źródło poświadczeń.
        if claim_pool_account(s, acc):
            acc.status = "funded" if acc.phase == "funded" else "active"
            s.commit()
            if trader:
                notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
            print(f"[provisioning] konto {aid} = {acc.platform_login}@{acc.platform_server} "
                  f"z puli, trader {trader.email if trader else '?'}")
            return

        # 3) pula pusta, ale admin włączył w panelu auto-provisioning symulowanych
        #    poświadczeń — generujemy je od ręki zamiast trzymać tradera w kolejce.
        if sim_fallback_enabled(s):
            _apply_local_credentials(acc)
            acc.status = "funded" if acc.phase == "funded" else "active"
            s.commit()
            if trader:
                notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
            print(f"[provisioning] konto {aid} = SYMULOWANE poświadczenia "
                  f"({acc.platform_login}@{acc.platform_server}) — pula była pusta")
            return

        # 4) konto czeka, aż admin doda rachunek do puli. Panel pokazuje takie
        #    konta w sekcji MT5 Pool jako oczekujące.
        print(f"[provisioning] konto {aid} czeka na rachunek ${acc.initial_balance:,.0f} z puli")
    finally:
        s.close()


SIM_FALLBACK_KEY = "provision_sim_fallback"


def sim_fallback_enabled(session) -> bool:
    """Czy przy pustej puli auto-generować symulowane poświadczenia.

    Przełącznik z panelu admina, trzymany w bazie — na hostingu bezserwerowym
    zmiana env oznaczałaby redeploy, a to ma działać od kliknięcia."""
    row = session.get(AppSetting, SIM_FALLBACK_KEY)
    return bool(row and row.value == "1")


def _looks_like_account_number(value: str | None) -> bool:
    return bool(value) and len(value) == 9 and value.isdigit()


def _apply_local_credentials(acc: Account) -> None:
    """Poświadczenia wygenerowane u nas — bez kontaktu z MetaQuotes.

    Konto, które utknęło w 'provisioning' przy starszej konfiguracji, ma login
    w starym formacie (7 cyfr) i NIKT go jeszcze nie widział — portal pokazywał
    wtedy „MT5 account pending…". Nadajemy mu więc świeży numer 9-cyfrowy.
    """
    existing = acc.platform_login or acc.login
    acc.platform_login = existing if _looks_like_account_number(existing) else _gen_login()
    acc.platform_password = acc.platform_password or _gen_password()
    acc.platform_server = acc.platform_server or PLATFORM_SERVER
    acc.login = acc.platform_login
    acc.mt5_backed = False


def _apply_credentials(acc: Account, creds: dict) -> None:
    acc.metaapi_account_id = creds.get("metaapi_account_id") or acc.metaapi_account_id
    acc.platform_login = str(creds.get("login") or acc.platform_login or acc.login)
    acc.platform_password = creds.get("password") or acc.platform_password
    acc.platform_server = creds.get("server") or acc.platform_server
    acc.login = acc.platform_login
    acc.mt5_backed = True


async def _create_demo_account(feed, acc: Account, trader: Trader | None, settings) -> dict | None:
    """Zakłada realne konto demo MT5. Kolejność kanałów:

      1. web terminal MetaQuotes (darmowy, serwer MetaQuotes-Demo),
      2. REST MetaApi (płatny generator, konto u brokera),
      3. feed.provision() (SDK), gdy ktoś skonfigurował tylko ten kanał.

    Zwraca poświadczenia albo None. Przy błędzie nakłada backoff na to konto,
    żeby poller (domyślnie co 3 s) nie zalewał zewnętrznego serwisu.
    """
    try:
        opener = metaquotes_web.make_opener(settings)
        if opener is not None:
            spec = metaquotes_web.WebDemoSpec.from_trader(trader, acc, settings)
            creds = await opener.open_demo_account(spec)
            return {
                "login": creds.login,
                "password": creds.password,
                "server": creds.server,
            }

        provisioner = metaapi_provisioning.make_provisioner(settings)
        if provisioner is not None:
            spec = metaapi_provisioning.spec_from_account(acc, trader, settings)
            return await provisioner.provision(settings.metaapi_provisioning_profile_id, spec)

        # Brak profilu/tokenu → spróbuj kanałem feedu (SDK), jeśli go implementuje.
        return await feed.provision({
            "name": acc.trader_name,
            "email": (trader.email if trader else None),
            "balance": acc.initial_balance,
        })
    except Exception as e:
        delay = _apply_backoff(acc.id)
        print(f"[provisioning] konto {acc.id}: zakładanie dema nieudane ({e}) "
              f"— kolejna próba za {delay:.0f}s")
        return None


# --- backoff per konto (proces-lokalny; wystarcza, bo poller jest jeden) --------
_PROVISION_BACKOFF_BASE_SEC = 30.0
_PROVISION_BACKOFF_MAX_SEC = 1800.0
_attempts: dict[int, int] = {}
_next_attempt_at: dict[int, float] = {}


def _may_attempt(account_id: int) -> bool:
    return monotonic() >= _next_attempt_at.get(account_id, 0.0)


def _apply_backoff(account_id: int) -> float:
    n = _attempts.get(account_id, 0)
    _attempts[account_id] = n + 1
    delay = min(_PROVISION_BACKOFF_BASE_SEC * (2 ** n), _PROVISION_BACKOFF_MAX_SEC)
    _next_attempt_at[account_id] = monotonic() + delay
    return delay


def _clear_backoff(account_id: int) -> None:
    _attempts.pop(account_id, None)
    _next_attempt_at.pop(account_id, None)
