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
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException

from . import catalog, loyalty, metaapi_provisioning, metaquotes_web, notify, telemetry
from .config import get_settings
from .models import (Account, AppSetting, CreditLedger, FlashOffer, Order,
                     PoolAccount, Product, RewardCode, Trader)

PLATFORM_SERVER = "MetaQuotes-Demo"


def _gen_login() -> str:
    """9 cyfr — tyle ma numer rachunku na MetaQuotes-Demo."""
    return str(secrets.randbelow(900_000_000) + 100_000_000)


def _gen_password() -> str:
    """Format jak hasla wydawane przez MetaQuotes-Demo: 8 znakow, male litery
    i cyfry (np. `ichhl00j`). Placeholder ma wygladac jak dane z tego serwera."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def create_account_from_order(session, order: Order, notify_admin: bool = True) -> Account:
    """Tworzy konto challenge zgodnie z parametrami produktu z zamówienia.

    `notify_admin=False` gdy płatność domyka sam admin z panelu (mark-paid) —
    nie ma sensu wysyłać mu pusha o jego własnym kliknięciu."""
    # Atomowe przejęcie zamówienia: UPDATE ... WHERE status != 'paid' wygrywa
    # dokładnie raz. Wszyscy wołający (webhook, mock, mark-paid, free) robią
    # wcześniej "sprawdź status i provisionuj" — na serverless dwa równoległe
    # requesty przechodzą ten check jednocześnie i bez tego guardu powstałyby
    # dwa konta z podwójnym zbiciem kredytów.
    claimed = (session.query(Order)
               .filter(Order.id == order.id, Order.status != "paid")
               .update({Order.status: "paid"}, synchronize_session=False))
    if not claimed:
        session.rollback()
        raise HTTPException(409, "Order already processed")
    product = session.query(Product).filter(Product.key == order.product_key).first()
    trader = session.get(Trader, order.trader_id)
    now = datetime.now(timezone.utc)
    settings = get_settings()
    real_mode = chce_realnego_mt5(order, settings, session)

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
        # Split Boost: +10 pp do splitu planu (kupiony przy checkoucie; compute_price
        # dopuszcza go wylacznie na Instant, wiec 70 -> 80).
        profit_split_pct=(product.profit_split_pct
                          + (catalog.SPLIT_BOOST_PP
                             if getattr(order, "addon_split_boost", False) else 0)),
        express_payout=bool(getattr(order, "addon_express_payout", False)),
        # Copytrading: zgoda dla tradera, a dla nas decyzja o REALNYM rachunku
        # MT5 (patrz `chce_realnego_mt5`). Czytane przez `getattr`, bo ta sama
        # funkcja obsluguje zamowienia sprzed dodania kolumny.
        copytrading=bool(getattr(order, "addon_copytrading", False)),
        max_lots=getattr(product, "max_lots", 0.0) or 0.0,
        # Instant funding (steps=0) omija ewaluacje — konto od razu jest funded.
        # `open_funded` to ta sama obietnica zlozona recznie z panelu (oferta
        # imienna), wiec dziala dla planu z dowolna liczba krokow.
        phase=("funded" if product.steps == 0 or getattr(order, "open_funded", False)
               else "eval_1"),
        # przy realnym provisioningu konto czeka na poświadczenia (nie jest jeszcze handlowalne)
        status=("provisioning" if real_mode
                else ("funded" if product.steps == 0 or getattr(order, "open_funded", False)
                      else "active")),
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
    przyznany = round(float(getattr(order, "credits_used", 0) or 0), 2)
    zuzycie = min(przyznany, round(float(trader.credits_usd or 0), 2))
    if zuzycie > 0:
        trader.credits_usd = round(float(trader.credits_usd) - zuzycie, 2)
        session.add(CreditLedger(trader_id=trader.id, amount=-zuzycie,
                                 note=f"Applied to order #{order.id}", order_id=order.id))
    if przyznany > zuzycie:
        # Rabat zszedl z ceny, ale pokrycia w saldzie juz nie ma — rownolegly
        # checkout w drugiej karcie zdazyl zuzyc te same kredyty. Roznice
        # doplaca firma, wiec musi zostac flaga i dzwonek; `credits_used`
        # zostaje pelne, bo taki rabat NAPRAWDE poszedl (ledger trzyma tylko
        # to, co realnie zeszlo z salda).
        order.flag = "credits_shortfall"
        notify.notify_admins("admin_order",
                             f"Credits shortfall ${round(przyznany - zuzycie, 2):,.2f} "
                             f"on order #{order.id} — discount granted without balance coverage",
                             trader.email)
    # Kod kupiony za punkty jest JEDNORAZOWY i schodzi w tym samym momencie co
    # kredyty: przy domknietej platnosci. Znacznik ustawiamy warunkowym UPDATE-em,
    # wiec dwa rownolegle zamowienia z tym samym kodem zaliczy tylko jedno.
    kod = (order.coupon or "").strip().upper()
    if kod.startswith(loyalty.CODE_PREFIX):
        zajete = (session.query(RewardCode)
                  .filter(RewardCode.code == kod, RewardCode.trader_id == trader.id,
                          RewardCode.used_at == None)                       # noqa: E711
                  .update({RewardCode.used_at: now, RewardCode.order_id: order.id},
                          synchronize_session=False))
        if not zajete:
            print(f"[provisioning] kod {kod} byl juz zuzyty przy zamowieniu #{order.id}", flush=True)
    # Oferta flash schodzi tak samo jak kod nagrody: dopiero przy domknietej
    # platnosci (porzucony koszyk jej nie pali) i tylko gdy jest jednorazowa.
    # Oferta wygasla miedzy checkoutem a zaplata honoruje cene z zamowienia.
    if getattr(order, "flash_offer_id", None):
        (session.query(FlashOffer)
         .filter(FlashOffer.id == order.flash_offer_id,
                 FlashOffer.single_use == True,                              # noqa: E712
                 FlashOffer.used_at == None)                                 # noqa: E711
         .update({FlashOffer.used_at: now, FlashOffer.order_id: order.id},
                 synchronize_session=False))
    session.commit()
    telemetry.track("order_paid", trader.id, order=order.id, product=order.product_key,
                    amount=order.amount_usd, provider=order.provider)
    # Granty pomijamy zawsze: przyznaje je admin, wiec sam o nich wie.
    if notify_admin and order.provider != "grant":
        notify.notify_admins("admin_order",
                             f"New order: {order.product_key} ${order.amount_usd:,.0f}",
                             trader.email)

    if not real_mode:
        notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
    # przy realnym provisioningu: konto zostaje 'provisioning' — poller je uzbroi i wyśle mail

    # Buy 1 Get 1 Free: drugie konto tego samego rozmiaru jako grant ($0, ta sama
    # ścieżka provisioningu, własny mail z poświadczeniami). Guard na `grant`
    # przerywa rekurencję — grant_challenge wraca do tej funkcji ze swoim
    # zamówieniem. Podwójnego grantu pilnuje atomowe przejęcie zamówienia wyżej:
    # ten kod wykona się najwyżej raz na zamówienie. Import leniwy (billing
    # importuje provisioning na poziomie modułu). Błąd grantu NIE wywraca
    # opłaconego zamówienia — klient ma już pierwsze konto, admin dostaje alert
    # i przyznaje drugie ręcznie z panelu.
    if getattr(order, "bogo", False) and order.provider != "grant":
        from . import billing
        try:
            billing.grant_challenge(session, trader, order.product_key, "Buy 1 Get 1 Free")
        except Exception as e:
            print(f"[provisioning] BOGO grant dla zamowienia #{order.id} nie wyszedl: {e}", flush=True)
            # Sesja mogła zostać z na wpół dodanym grantem — bez rollbacku każdy
            # kolejny commit w tym request-cie przepchnąłby te obiekty do bazy.
            session.rollback()
            # Dzwonek łatwo przegapić, a bez trwałego śladu opłacone zamówienie
            # z obietnicą drugiego konta wygląda w panelu na obsłużone. Flaga
            # trzyma się zamówienia, panel pokazuje ją przy statusie.
            order.flag = "bogo_grant_failed"
            session.commit()
            notify.notify_admins("admin_order",
                                 f"BOGO grant FAILED for order #{order.id} ({order.product_key}) — grant manually",
                                 trader.email)
    return acc


def real_provisioning_enabled(settings=None) -> bool:
    """Czy konto ma czekać na REALNE poświadczenia MT5.

    Jedna flaga, bo realnych ścieżek jest kilka (web terminal, MetaApi, pula
    gotowych kont) i pula nie wymaga włączenia żadnego kanału. Domyślnie
    wyłączone: konto dostaje poświadczenia wygenerowane lokalnie i jest
    handlowalne od razu.
    """
    return bool((settings or get_settings()).mt5_provisioning)


def chce_realnego_mt5(obiekt, settings=None, session=None) -> bool:
    """Czy TO konkretne konto ma dostac realny rachunek MT5.

    Realny rachunek kosztuje nas u dostawcy co miesiac, wiec nie dostaje go
    kazdy, kto cos kupil — tylko ten, kto doplacil za add-on Copytrading.

    Dwie drogi, w tej kolejnosci:

      * `MT5_PROVISIONING` w srodowisku — tryb globalny: REALNE rachunki dla
        wszystkich, niezaleznie od dodatkow. Tak dziala pula kont i tak bylo
        od poczatku; ta galaz zostaje nietknieta.
      * przelacznik „Copytrading accounts" w zakladce MT5 Pool — dla
        produkcji, gdzie env jest wylaczone i konta stoja na poswiadczeniach
        lokalnych. Wtedy realny rachunek dostaje WYLACZNIE ten, kto doplacil
        za add-on. Przelacznik siedzi w bazie, bo na hostingu bezserwerowym
        zmiana env to redeploy, a to ma dzialac od klikniecia — dokladnie tak
        dzialaja juz sim_fallback i real_fallback.

    Przyjmuje zarowno Order (`addon_copytrading`), jak i Account
    (`copytrading`), bo ta sama decyzja zapada DWA RAZY: przy zakladaniu konta
    (czy czeka na poswiadczenia) i przy provisioningu (ktora sciezka). Gdyby
    kryteria sie rozjechaly, konto zalozone jako lokalne wpadloby do kolejki
    realnego provisioningu i utknelo tam na zawsze.
    """
    if real_provisioning_enabled(settings):
        return True
    kupil = bool(getattr(obiekt, "copytrading", False)
                 or getattr(obiekt, "addon_copytrading", False))
    return kupil and session is not None and copytrading_real_enabled(session)


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
    # Konto założone ZA klienta (ręczne zamówienie, import wypłat) ma hasło,
    # którego nie zna nikt. Ten mail jest pierwszym powodem, żeby wejść do
    # portalu — więc to tutaj musi być droga do środka, inaczej człowiek czyta
    # „zaloguj się" i nie ma czym. Link jest jednorazowy (odcisk hasła siedzi
    # w tokenie) i ważny 7 dni; po wygaśnięciu zostaje „forgot password"
    # i mail mówi o tym wprost.
    base = get_settings().app_base_url
    setup_url = None
    if getattr(trader, "must_set_password", False):
        from . import auth   # tutaj, nie u góry: auth ciągnie config i sesje
        setup_url = (f"{base}/portal"
                     f"?reset={auth.make_setup_token(trader.id, trader.password_hash)}")
    return {
        "setup_url": setup_url,
        "portal_url": f"{base}/portal",
        # Furtka dla drugiej strony tego `if`. Flaga mówi tylko tyle, że wiersz
        # tradera założyliśmy my — konto starsze niż sama flaga (2026-08-11) albo
        # przejęte przez Google ma ją zgaszoną, choć hasła nadal nie ma. Nie da
        # się tego rozstrzygnąć z kolumn, więc mail nie zgaduje: pokazuje drogę,
        # która jest nieszkodliwa też dla kogoś, kto hasło ma.
        "forgot_url": f"{base}/portal?forgot=1&email={quote(trader.email)}",
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


REJESTRACJA = "metaapi_register"


async def zarejestruj_w_metaapi(session, acc: Account, settings=None) -> str | None:
    """Podpina rachunek MT5 tego konta pod MetaApi. Zwraca metaapi_account_id.

    Bez tego kroku silnik nie ma czego czytac: `MetaApiRestFeed` adresuje konto
    przez identyfikator MetaApi, a nie przez login u brokera. Dotad trzeba bylo
    wkleic go recznie przy KAZDYM koncie.

    Osobna funkcja od zakladania dema, bo to dwie rozne rzeczy i tylko jedna
    dziala: MetaApi odmawia zalozenia dema na MetaQuotes-Demo, ale podlaczenie
    istniejacego konta na tym samym serwerze przyjmuje.

    Nie rzuca. Rejestracja kosztuje u dostawcy, wiec robimy ja WYLACZNIE dla
    konta, ktore ma na to zgode (`chce_realnego_mt5`), i z backoffem — konto
    odrzucone przez MetaApi nie ma dobijac sie przy kazdym tyknieciu.
    """
    if acc.metaapi_account_id:
        return acc.metaapi_account_id
    if not (acc.platform_login and acc.platform_password and acc.platform_server):
        return None
    if not chce_realnego_mt5(acc, settings, session):
        return None
    if not _may_attempt(session, acc.id, REJESTRACJA):
        return None

    rejestrator = metaapi_provisioning.make_registrar(settings)
    if rejestrator is None:
        return None
    creds = metaapi_provisioning.DemoCredentials(
        login=str(acc.platform_login),
        password=str(acc.platform_password),
        server=str(acc.platform_server),
    )
    try:
        aid = await rejestrator.register_account(creds, name=(acc.trader_name or f"Account {acc.id}"))
    except Exception as e:
        delay = _apply_backoff(session, acc.id, REJESTRACJA)
        print(f"[provisioning] konto {acc.id}: rejestracja w MetaApi nieudana ({e}) "
              f"— kolejna proba za {delay:.0f}s", flush=True)
        return None

    acc.metaapi_account_id = aid
    session.commit()
    _clear_backoff(session, acc.id, REJESTRACJA)
    print(f"[provisioning] konto {acc.id} = {acc.platform_login}@{acc.platform_server} "
          f"podpiete pod MetaApi ({aid})", flush=True)
    return aid


async def dopnij_brakujace_rejestracje(session_factory, settings=None) -> int:
    """Przechodzi po kontach, ktore maja rachunek, ale nie maja go w MetaApi.

    Rejestracja jest tu DOGRYWANA, a nie warunkiem uruchomienia konta. Trader,
    ktory zaplacil, dostaje login od reki; gdy MetaApi akurat nie odpowiada, to
    NASZ problem z odczytem, nie powod, zeby trzymac go w kolejce. Wolane
    z ticku ryzyka, wiec kolejna proba jest za minute, a nie za dobe.
    """
    s = session_factory()
    try:
        kandydaci = [a.id for a in s.query(Account).filter(
            Account.status.in_(["active", "funded"]),
            Account.metaapi_account_id.is_(None),
            Account.copytrading == True,                      # noqa: E712
            Account.mt5_backed == True,                       # noqa: E712
            Account.platform_password.isnot(None)).all()]
    finally:
        s.close()
    zrobione = 0
    for aid in kandydaci:
        s = session_factory()
        try:
            acc = s.get(Account, aid)
            if acc and await zarejestruj_w_metaapi(s, acc, settings):
                zrobione += 1
        except Exception as e:  # pragma: no cover - cudza dostepnosc
            s.rollback()
            print(f"[provisioning] konto {aid}: dogrywka rejestracji padla: {e}", flush=True)
        finally:
            s.close()
    return zrobione


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
        if not chce_realnego_mt5(acc, settings, s):
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
                and _may_attempt(s, aid)):
            creds = await _create_demo_account(feed, acc, trader, settings, s)
            if creds:
                _apply_credentials(acc, creds)
                acc.status = "funded" if acc.phase == "funded" else "active"
                s.commit()
                _clear_backoff(s, aid)
                if trader:
                    notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
                print(f"[provisioning] konto {aid} = realne demo MT5 "
                      f"{acc.platform_login}@{acc.platform_server} (metaapi_id={acc.metaapi_account_id})")
                return

        # 2) PULA gotowych kont — domyślnie jedyne źródło poświadczeń.
        if claim_pool_account(s, acc):
            acc.status = "funded" if acc.phase == "funded" else "active"
            s.commit()
            # Pierwsza proba podpiecia pod MetaApi. Nieudana NIE wstrzymuje
            # konta — trader ma dzialajace poswiadczenia, a dogrywka z ticku
            # ryzyka sprobuje ponownie za minute.
            await zarejestruj_w_metaapi(s, acc, settings)
            if trader:
                notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
            print(f"[provisioning] konto {aid} = {acc.platform_login}@{acc.platform_server} "
                  f"z puli, trader {trader.email if trader else '?'}")
            return

        # 3) pula pusta + auto real MT5 (web.metatrader.app) — przed symulacją,
        #    bo realne poświadczenia są cenniejsze. Wymaga METAQUOTES_WEB_ENABLED
        #    i przeglądarki (lokalnej albo BROWSER_CDP_URL).
        if (real_fallback_enabled(s)
                and (settings.metaquotes_web_enabled or settings.metaapi_auto_create)
                and _may_attempt(s, aid)):
            creds = await _create_demo_account(feed, acc, trader, settings, s)
            if creds:
                _apply_credentials(acc, creds)
                acc.status = "funded" if acc.phase == "funded" else "active"
                s.commit()
                _clear_backoff(s, aid)
                if trader:
                    notify.send(_creds_event(acc), trader.email, _creds_ctx(trader, acc))
                print(f"[provisioning] konto {aid} = realne demo MT5 "
                      f"{acc.platform_login}@{acc.platform_server} (auto-fallback)")
                return

        # 4) pula pusta, ale admin włączył w panelu auto-provisioning symulowanych
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

        # 5) konto czeka, aż admin doda rachunek do puli. Panel pokazuje takie
        #    konta w sekcji MT5 Pool jako oczekujące.
        print(f"[provisioning] konto {aid} czeka na rachunek ${acc.initial_balance:,.0f} z puli")
    finally:
        s.close()


SIM_FALLBACK_KEY = "provision_sim_fallback"
REAL_FALLBACK_KEY = "provision_real_fallback"
COPYTRADING_REAL_KEY = "provision_copytrading_real"


def sim_fallback_enabled(session) -> bool:
    """Czy przy pustej puli auto-generować symulowane poświadczenia.

    Przełącznik z panelu admina, trzymany w bazie — na hostingu bezserwerowym
    zmiana env oznaczałaby redeploy, a to ma działać od kliknięcia."""
    row = session.get(AppSetting, SIM_FALLBACK_KEY)
    return bool(row and row.value == "1")


def real_fallback_enabled(session) -> bool:
    """Czy przy pustej puli zakładać realne demo MT5 przez web.metatrader.app.

    Osobny przełącznik od sim_fallback: realne konta wymagają przeglądarki
    (lokalnej albo BROWSER_CDP_URL / Browserless) i trwają ~20–30 s sztuka.
    Gdy obie flagi są włączone, real ma pierwszeństwo przed symulacją."""
    row = session.get(AppSetting, REAL_FALLBACK_KEY)
    return bool(row and row.value == "1")


def copytrading_real_enabled(session) -> bool:
    """Czy konta z add-onem Copytrading maja dostawac REALNY rachunek MT5.

    Przelacznik z zakladki MT5 Pool. Wylaczony = dodatek dalej sie sprzedaje
    i dalej znaczy zgode na kopiowanie, ale konto idzie na lokalne
    poswiadczenia — tak jak cala dotychczasowa produkcja. Dzieki temu sprzedaz
    mozna wlaczyc, zanim rachunek u dostawcy bedzie gotowy, i nie zostawic
    klienta z kontem wiszacym w kolejce.
    """
    row = session.get(AppSetting, COPYTRADING_REAL_KEY)
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


async def _create_demo_account(feed, acc: Account, trader: Trader | None,
                               settings, session=None) -> dict | None:
    """Zaklada realne konto demo MT5. Kanaly w kolejnosci:

      1. REST MetaApi — jedyny, ktory dziala na hostingu bezserwerowym,
      2. web terminal MetaQuotes (Playwright) — wymaga przegladarki,
      3. feed.provision() (SDK), gdy skonfigurowany jest tylko ten kanal.

    MetaApi idzie PIERWSZE, choc jest platne. Wczesniej pierwszy byl web
    terminal, a `METAQUOTES_WEB_ENABLED` domyslnie jest wlaczone — na hostingu
    bez Chromium ta galaz rzucala wyjatkiem przy kazdej probie i backoff odcinal
    konto, ZANIM ktokolwiek siegnal po MetaApi. Do tego kanal, ktory padnie, nie
    blokuje juz nastepnego: kazdy ma wlasny `try`.

    Zwraca poswiadczenia albo None. Przy porazce wszystkich kanalow naklada
    backoff na to konto, zeby poller nie zalewal zewnetrznego serwisu.
    """
    powody = []
    for nazwa, kanal in _kanaly_demo(feed, acc, trader, settings):
        try:
            creds = await kanal()
        except Exception as e:
            powody.append(f"{nazwa}: {e}")
            continue
        if creds:
            if powody:
                print(f"[provisioning] konto {acc.id}: {nazwa} zadzialal po "
                      f"nieudanych probach ({'; '.join(powody)})", flush=True)
            return creds
        powody.append(f"{nazwa}: brak poswiadczen")
    delay = _apply_backoff(session, acc.id)
    print(f"[provisioning] konto {acc.id}: zakladanie dema nieudane "
          f"({'; '.join(powody) or 'brak skonfigurowanego kanalu'}) "
          f"— kolejna proba za {delay:.0f}s", flush=True)
    return None


def _kanaly_demo(feed, acc: Account, trader: Trader | None, settings):
    """Lista (nazwa, funkcja) — tylko kanaly, ktore sa skonfigurowane.

    Budowa specyfikacji siedzi WEWNATRZ funkcji kanalu, nie tutaj: zly numer
    telefonu albo brakujace nazwisko ma wywrocic jeden kanal, a nie cala
    kolejke, zanim ktorykolwiek ruszy.
    """
    kanaly = []

    provisioner = metaapi_provisioning.make_provisioner(settings)
    if provisioner is not None:
        async def _metaapi():
            spec = metaapi_provisioning.spec_from_account(acc, trader, settings)
            return await provisioner.provision(settings.metaapi_provisioning_profile_id, spec)
        kanaly.append(("metaapi", _metaapi))

    opener = metaquotes_web.make_opener(settings)
    if opener is not None:
        async def _web():
            spec = metaquotes_web.WebDemoSpec.from_trader(trader, acc, settings)
            creds = await opener.open_demo_account(spec)
            return {"login": creds.login, "password": creds.password, "server": creds.server}
        kanaly.append(("web terminal", _web))

    if not kanaly:
        async def _feed():
            return await feed.provision({
                "name": acc.trader_name,
                "email": (trader.email if trader else None),
                "balance": acc.initial_balance,
            })
        kanaly.append(("feed", _feed))

    return kanaly


# --- backoff per konto (w BAZIE, nie w pamieci procesu) ------------------------
# Wczesniej stal w slownikach modulu. Na hostingu bezserwerowym kazde zadanie to
# inny proces, wiec slownik byl pusty przy KAZDEJ probie i backoff nie istnial:
# broker odrzucajacy dema dostawal zapytanie przy kazdym tyknieciu. Wiersz w
# `app_settings` przezywa proces i jest wspolny dla wszystkich instancji.
_PROVISION_BACKOFF_BASE_SEC = 30.0
_PROVISION_BACKOFF_MAX_SEC = 1800.0


def _backoff_key(account_id: int, rodzaj: str = "provision") -> str:
    return f"{rodzaj}_backoff:{account_id}"


def _backoff_row(session, account_id: int, rodzaj: str = "provision"):
    if session is None:
        return None
    return session.get(AppSetting, _backoff_key(account_id, rodzaj))


def _may_attempt(session, account_id: int, rodzaj: str = "provision") -> bool:
    row = _backoff_row(session, account_id, rodzaj)
    if not row or not row.value:
        return True
    try:
        _prob, kiedy = row.value.split("|", 1)
        return time.time() >= float(kiedy)
    except (ValueError, TypeError):
        # Zepsuty wiersz nie ma prawa zablokowac provisioningu na zawsze.
        return True


def _apply_backoff(session, account_id: int, rodzaj: str = "provision") -> float:
    prob = 0
    row = _backoff_row(session, account_id, rodzaj)
    if row and row.value:
        try:
            prob = int(row.value.split("|", 1)[0])
        except (ValueError, TypeError):
            prob = 0
    delay = min(_PROVISION_BACKOFF_BASE_SEC * (2 ** prob), _PROVISION_BACKOFF_MAX_SEC)
    if session is not None:
        wartosc = f"{prob + 1}|{time.time() + delay}"
        if row:
            row.value = wartosc
        else:
            session.add(AppSetting(key=_backoff_key(account_id, rodzaj), value=wartosc))
        session.commit()
    return delay


def _clear_backoff(session, account_id: int, rodzaj: str = "provision") -> None:
    row = _backoff_row(session, account_id, rodzaj)
    if row is not None:
        session.delete(row)
        session.commit()
