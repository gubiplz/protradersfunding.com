"""Zakładanie kont demo MT5 na serwerze MetaQuotes-Demo przez web terminal.

Kanał darmowy: sterujemy formularzem „Open Demo account" na
https://web.metatrader.app/terminal?mode=demo i odczytujemy poświadczenia,
które MetaQuotes pokazuje po założeniu konta.

Dane klienta (imię, nazwisko, e-mail, telefon) pochodzą z kroku płatności —
konto powstaje NA JEGO DANE, nie na wymyślone.

Czego się tu spodziewać:
  * Formularz jest zwykłym DOM-em (pola `firstName`, `secondName`, `email`,
    `phone`, `deposit`, `disclaimer` + 2 selecty). Bez captchy — nic nie obchodzimy.
  * Poświadczenia NIE wracają w odpowiedzi HTTP, tylko renderują się w panelu
    „New account opened". Dlatego parsujemy tekst — patrz `parse_result()`,
    które jest czystą funkcją i ma testy jednostkowe bez przeglądarki.
  * Interfejs bywa lokalizowany, więc parser rozumie etykiety EN i PL, a
    przeglądarkę i tak uruchamiamy z `locale="en-US"` dla powtarzalności.

Ograniczenia, o których trzeba pamiętać przy 100 użytkownikach:
  * MetaQuotes limituje rejestracje po IP — stąd `min_interval_sec` (domyślnie
    20 s) i globalny lock. Jedno konto to ~20–30 s, więc kolejka jest szeregowa.
  * Konto żyje na publicznym serwerze demo MetaQuotes: nie masz tam uprawnień
    serwerowych i nie zablokujesz loginu. Egzekucja limitów odbywa się przez API
    (zamykanie pozycji), a nie po stronie serwera.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from .metaapi_provisioning import DemoCredentials

DEFAULT_URL = "https://web.metatrader.app/terminal?mode=demo"
MQ_SERVER = "MetaQuotes-Demo"

# Etykiety w panelu wyniku — kolejność ma znaczenie tylko dla czytelności.
_LABELS = {
    "login": ("Login",),
    "password": ("Password", "Hasło"),
    "investor": ("Investor", "Inwestor"),
    "server": ("Server", "Serwer"),
}
_SUCCESS_MARKERS = ("New account opened", "Nowy rachunek", "Konto zostało otwarte")


class WebProvisioningError(RuntimeError):
    """Nie udało się założyć konta przez web terminal."""


class WebReadError(RuntimeError):
    """Nie udało się odczytać stanu konta z web terminala."""


@dataclass
class AccountState:
    """Stan konta odczytany z paska podsumowania terminala."""

    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    has_open_position: bool = False
    # Laczny wolumen otwartych pozycji w lotach. 0.0 gdy brak pozycji ALBO gdy
    # nie udalo sie go odczytac — patrz `volume_known`.
    volume_lots: float = 0.0
    volume_known: bool = True


# „100 000.00" / „1 234 567,89" -> float
_NUM = r"(-?[\d  ]+(?:[.,]\d+)?)"
_STATE_LABELS = {
    "balance": ("Balance", "Saldo"),
    "equity": ("Equity", "Środki"),
    "margin": ("Margin", "Depozyt zabezpieczający"),
    "free_margin": ("Free margin", "Wolne środki"),
}
# Wiersz pozycji w terminalu: "EURUSD  buy 0.50  1.08512  ..." albo
# "XAUUSD sell 1.25 ...". Bierzemy liczbe TUZ PO kierunku — to wolumen w lotach.
_POSITION_ROW = re.compile(
    r"\b(?:buy|sell|kupno|sprzeda[żz])\b[^\d\-]{0,12}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE)


def parse_open_volume(text: str) -> tuple[float, bool]:
    """Suma wolumenu otwartych pozycji (loty) + czy odczyt jest wiarygodny.

    Zwraca (0.0, True) gdy terminal jawnie mowi, ze pozycji nie ma.
    Zwraca (x, False) gdy widac pozycje, ale nie da sie odczytac wolumenu —
    wtedy silnik nie karze tradera za nasz problem z parsowaniem.
    """
    if any(marker.lower() in (text or "").lower() for marker in _NO_POSITIONS):
        return 0.0, True
    lots = [_to_float(m.group(1)) for m in _POSITION_ROW.finditer(text or "")]
    lots = [x for x in lots if 0 < x <= 1000]           # odsiewamy ceny/ID
    if not lots:
        return 0.0, False
    return round(sum(lots), 2), True


_NO_POSITIONS = ("don't have any positions", "don’t have any positions", "Brak pozycji")


def _to_float(raw: str) -> float:
    cleaned = raw.replace(" ", "").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    return float(cleaned)


def parse_account_state(text: str) -> AccountState:
    """Wyciąga saldo/equity z tekstu terminala. Czysta funkcja — ma testy."""
    values: dict[str, float] = {}
    for field_name, labels in _STATE_LABELS.items():
        for label in labels:
            m = re.search(rf"{re.escape(label)}\s*:?\s*{_NUM}", text or "", re.IGNORECASE)
            if m:
                try:
                    values[field_name] = _to_float(m.group(1))
                except ValueError:
                    continue
                break

    if "balance" not in values or "equity" not in values:
        raise WebReadError(
            f"Terminal nie pokazał salda/equity — sesja mogła wygasnąć. "
            f"Odczytano: {sorted(values)}"
        )

    has_open = not any(marker.lower() in (text or "").lower() for marker in _NO_POSITIONS)
    vol, vol_known = parse_open_volume(text or "")
    return AccountState(
        volume_lots=vol,
        volume_known=vol_known,
        balance=values["balance"],
        equity=values["equity"],
        margin=values.get("margin", 0.0),
        free_margin=values.get("free_margin", 0.0),
        has_open_position=has_open,
    )


@dataclass
class WebDemoSpec:
    first_name: str
    last_name: str
    email: str
    phone: str
    deposit: float
    leverage: int = 100
    account_type: str = "Forex Hedged USD"
    hedging: bool = True

    @classmethod
    def from_trader(cls, trader, acc, settings) -> "WebDemoSpec":
        """Buduje spec z danych klienta; imię/nazwisko rozbija z `full_name`,
        gdy nie podano ich osobno przy płatności."""
        first = (getattr(trader, "first_name", None) or "").strip()
        last = (getattr(trader, "last_name", None) or "").strip()
        if not (first and last):
            parts = ((getattr(trader, "full_name", None) or acc.trader_name or "").strip().split())
            first = first or (parts[0] if parts else "Trader")
            last = last or (" ".join(parts[1:]) if len(parts) > 1 else "Account")
        return cls(
            first_name=first[:40],
            last_name=last[:40],
            email=(getattr(trader, "email", None) or settings.mail_from),
            phone=(getattr(trader, "phone", None) or settings.metaquotes_web_default_phone),
            deposit=acc.initial_balance,
            leverage=settings.metaquotes_web_leverage,
            account_type=settings.metaquotes_web_account_type,
        )


def parse_result(text: str) -> DemoCredentials:
    """Wyciąga poświadczenia z tekstu panelu wyniku.

    Panel ma układ „etykieta, potem wartość w kolejnej linii". Funkcja jest
    czysta, żeby dało się ją testować bez odpalania przeglądarki.
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    def value_after(names: tuple[str, ...]) -> str | None:
        for i, ln in enumerate(lines):
            if ln in names and i + 1 < len(lines):
                return lines[i + 1]
        return None

    login = value_after(_LABELS["login"])
    password = value_after(_LABELS["password"])
    investor = value_after(_LABELS["investor"])
    server = value_after(_LABELS["server"]) or MQ_SERVER

    # „-z3zAgRn  (Read only password)" -> „-z3zAgRn"
    if investor:
        investor = re.split(r"\s{2,}|\s*\(", investor)[0].strip() or None

    if not (login and password):
        raise WebProvisioningError(
            "Panel wyniku bez loginu/hasła — MetaQuotes zmienił układ strony "
            f"albo rejestracja się nie powiodła. Odczytano: {lines[:12]}"
        )
    if not re.fullmatch(r"\d{4,}", login):
        raise WebProvisioningError(f"Login nie wygląda na numer konta MT5: {login!r}")

    return DemoCredentials(login=login, password=password, server=server, investor_password=investor)


def looks_successful(text: str) -> bool:
    return any(m.lower() in (text or "").lower() for m in _SUCCESS_MARKERS)


class MetaQuotesWebOpener:
    """Otwiera konta demo, po jednym naraz, z odstępem między rejestracjami."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        headless: bool = True,
        boot_wait_ms: int = 9000,
        timeout_ms: int = 45_000,
        min_interval_sec: float = 20.0,
        screenshot_dir: str | None = None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ) -> None:
        self._url = url
        self._headless = headless
        self._boot_wait_ms = boot_wait_ms
        self._timeout_ms = timeout_ms
        self._min_interval = max(0.0, min_interval_sec)
        self._screenshot_dir = screenshot_dir
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_open_at: float | None = None

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        if self._last_open_at is not None:
            wait = self._min_interval - (self._clock() - self._last_open_at)
            if wait > 0:
                print(f"[metaquotes-web] odstęp od poprzedniej rejestracji: czekam {wait:.0f}s")
                await self._sleep(wait)
        self._last_open_at = self._clock()

    async def open_demo_account(self, spec: WebDemoSpec) -> DemoCredentials:
        async with self._lock:
            await self._throttle()
            return await self._open(spec)

    async def _open(self, spec: WebDemoSpec) -> DemoCredentials:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover - zależność opcjonalna
            raise WebProvisioningError(
                "Brak playwrighta. Zainstaluj: pip install playwright && playwright install chromium"
            ) from e

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await browser.new_page(locale="en-US", viewport={"width": 1500, "height": 1000})
                page.set_default_timeout(self._timeout_ms)
                await page.goto(self._url, wait_until="domcontentloaded")

                # Aplikacja montuje formularz dopiero po bootstrapie JS.
                await page.wait_for_selector("input[name=firstName]", timeout=self._timeout_ms)
                await page.wait_for_timeout(self._boot_wait_ms)

                await self._fill_form(page, spec)
                await page.click('button:has-text("Open Demo account")')

                text = await self._wait_for_result(page)
                creds = parse_result(text)
                print(f"[metaquotes-web] konto {creds.login}@{creds.server} założone "
                      f"dla {spec.email}")
                return creds
            except Exception:
                await self._dump(locals().get("page"))
                raise
            finally:
                await browser.close()

    async def _fill_form(self, page, spec: WebDemoSpec) -> None:
        await page.fill("input[name=firstName]", spec.first_name)
        await page.fill("input[name=secondName]", spec.last_name)
        await page.fill("input[name=email]", spec.email)
        await page.fill("input[name=phone]", spec.phone)

        # KOLEJNOŚĆ MA ZNACZENIE: zmiana hedgingu i typu rachunku RESETUJE pole
        # depozytu do wartości domyślnej (100 000). Depozyt ustawiamy na końcu,
        # inaczej każdy challenge dostaje 100k niezależnie od kupionego planu.
        try:
            if await page.is_checked("input[name=hedging]") != spec.hedging:
                await page.click("input[name=hedging]", force=True)
        except Exception as e:
            print(f"[metaquotes-web] nie ustawiono hedgingu ({e}) — zostawiam domyślny")

        await self._select_by_label(page, spec.account_type, "typ rachunku")
        await self._select_by_label(page, str(spec.leverage), "dźwignię")

        deposit = str(int(round(spec.deposit)))
        await page.fill("input[name=deposit]", deposit)
        await page.wait_for_timeout(300)
        got = await page.input_value("input[name=deposit]")
        if got.replace(" ", "") != deposit:
            raise WebProvisioningError(
                f"Formularz nie przyjął depozytu {deposit} (pokazuje {got!r}) — "
                f"konto miałoby zły kapitał startowy"
            )

        await page.check("input[name=disclaimer]")

    async def _select_by_label(self, page, wanted: str, what: str) -> None:
        """Selecty nie mają nazw — dopasowujemy po treści opcji."""
        for sel in await page.query_selector_all("select"):
            options = [(await o.inner_text()).strip() for o in await sel.query_selector_all("option")]
            if any(o == wanted for o in options):
                await sel.select_option(label=wanted)
                return
        print(f"[metaquotes-web] nie znalazłem opcji {wanted!r} — zostawiam domyślną {what}")

    async def _wait_for_result(self, page) -> str:
        """Czeka na panel z poświadczeniami. Sam 'Login' nie wystarcza —
        w formularzu też bywa to słowo, więc wymagamy markera sukcesu."""
        deadline = self._clock() + self._timeout_ms / 1000.0
        last = ""
        while self._clock() < deadline:
            last = await page.inner_text("body")
            if looks_successful(last) and re.search(r"\b\d{6,}\b", last):
                return last
            await page.wait_for_timeout(1000)
        raise WebProvisioningError(
            f"Brak potwierdzenia założenia konta w {self._timeout_ms/1000:.0f}s. "
            f"Ostatni ekran: {last[:300]!r}"
        )

    # ------------------------------------------------------- odczyt stanu --
    async def read_state(self, login: str, password: str) -> AccountState:
        """Loguje się na konto i czyta saldo/equity/pozycje z terminala.

        To jest darmowa alternatywa dla MetaApi jako źródło danych dla silnika
        reguł. Kosztem jest czas: jedno logowanie to ~20 s, więc przy dużej
        liczbie kont sweep trwa i breach wykrywasz z opóźnieniem.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise WebReadError("Brak playwrighta — pip install playwright") from e

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await browser.new_page(locale="en-US", viewport={"width": 1500, "height": 1000})
                page.set_default_timeout(self._timeout_ms)
                await page.goto(self._url.split("?")[0], wait_until="domcontentloaded")
                await page.wait_for_selector("input[name=login]", timeout=self._timeout_ms)
                await page.wait_for_timeout(self._boot_wait_ms)

                await page.fill("input[name=login]", str(login))
                await page.fill("input[name=password]", password)
                await page.click('button:has-text("Connect to account")')

                deadline = self._clock() + self._timeout_ms / 1000.0
                last = ""
                while self._clock() < deadline:
                    last = await page.inner_text("body")
                    if re.search(r"(Balance|Saldo)\s*:", last, re.IGNORECASE):
                        return parse_account_state(last)
                    await page.wait_for_timeout(1000)
                raise WebReadError(
                    f"Nie zalogowano się na konto {login} w {self._timeout_ms/1000:.0f}s "
                    f"(hasło mogło zostać zmienione). Ekran: {last[:200]!r}"
                )
            except Exception:
                await self._dump(locals().get("page"))
                raise
            finally:
                await browser.close()

    @staticmethod
    async def _accept_one_click_dialog(page) -> bool:
        """Akceptuje „One Click Trading — Disclaimer".

        Przy PIERWSZEJ próbie zamknięcia pozycji MetaQuotes pokazuje zgodę na tryb
        jednego kliknięcia i BLOKUJE zamknięcie do czasu jej zaakceptowania —
        bez tego enforcement breachu tylko wyglądał na wykonany.
        """
        try:
            popup = page.locator('[class*="popup"]:has-text("One Click Trading")').last
            if await popup.count() == 0:
                return False
            box = popup.locator('input[type="checkbox"]')
            if await box.count() and not await box.first.is_checked():
                await box.first.check(timeout=4000)
            accept = popup.locator('button:has-text("Accept")')
            if await accept.count():
                await accept.first.click(timeout=6000)
                print("[metaquotes-web] zaakceptowano One Click Trading")
                return True
        except Exception as e:      # pragma: no cover - UI terminala bywa kapryśne
            print(f"[metaquotes-web] dialog One Click Trading: {e}")
        return False


    async def close_all_positions(self, login: str, password: str) -> int:
        """Zamyka wszystkie otwarte pozycje przez interfejs terminala.

        Egzekucja breachu na koncie MetaQuotes-Demo: nie mamy uprawnień
        serwerowych, więc pozycje zamykamy „ręcznie" w UI.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise WebReadError("Brak playwrighta — pip install playwright") from e

        closed = 0
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                page = await browser.new_page(locale="en-US", viewport={"width": 1500, "height": 1000})
                page.set_default_timeout(self._timeout_ms)
                await page.goto(self._url.split("?")[0], wait_until="domcontentloaded")
                await page.wait_for_selector("input[name=login]", timeout=self._timeout_ms)
                await page.wait_for_timeout(self._boot_wait_ms)
                await page.fill("input[name=login]", str(login))
                await page.fill("input[name=password]", password)
                await page.click('button:has-text("Connect to account")')
                await page.wait_for_timeout(self._boot_wait_ms)

                # Przycisk zamknięcia pozycji w terminalu MetaQuotes ma tytuł
                # „Close #<ticket> buy 20.00 XAUUSD …" i POJAWIA SIĘ DOPIERO po
                # najechaniu na wiersz pozycji — stąd hover + klik z force.
                # (Klasy są generowane przez Svelte i zmieniają się przy każdym
                # deployu terminala, więc celujemy w atrybut title.)
                stalled = 0
                for _ in range(30):
                    body = await page.inner_text("body")
                    if any(m.lower() in body.lower() for m in _NO_POSITIONS):
                        break
                    buttons = page.locator('button[title^="Close #"]')
                    before = await buttons.count()
                    if before == 0:
                        buttons = page.locator("button.close")          # zapas
                        before = await buttons.count()
                        if before == 0:
                            print("[metaquotes-web] nie znalazłem przycisku zamknięcia pozycji "
                                  "— układ terminala mógł się zmienić")
                            break
                    btn = buttons.first
                    # Przycisk jest w DOM, ale NIEWIDOCZNY dopóki kursor nie stanie
                    # nad wierszem pozycji — najpierw hover na wierszu (rodzicu).
                    row = btn.locator("xpath=ancestor::*[self::tr or self::div][1]")
                    try:
                        await row.scroll_into_view_if_needed(timeout=5000)
                        await row.hover(timeout=5000)
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
                    try:
                        await btn.click(timeout=8000)
                    except Exception:
                        # Zapas: zdarzenie DOM pomija sprawdzanie widoczności.
                        await btn.dispatch_event("click")
                    await page.wait_for_timeout(1500)
                    # Pierwszy klik NIE zamyka pozycji — otwiera zgodę na One Click
                    # Trading. Po jej akceptacji trzeba kliknąć PONOWNIE (sprawdzone
                    # krok po kroku na żywej pozycji: klik → Accept → klik → zamknięte).
                    if await self._accept_one_click_dialog(page):
                        try:
                            await row.hover(timeout=5000)
                            await page.wait_for_timeout(300)
                            await btn.click(timeout=8000)
                        except Exception:
                            await btn.dispatch_event("click")
                    await page.wait_for_timeout(2500)
                    after = await page.locator('button[title^="Close #"]').count()
                    if after < before:
                        closed += before - after
                        stalled = 0
                    else:
                        stalled += 1
                        if stalled >= 3:
                            print("[metaquotes-web] pozycje nie znikają mimo klikania "
                                  "— przerywam, żeby nie zapętlić enforcementu")
                            break
                return closed
            except Exception:
                await self._dump(locals().get("page"))
                raise
            finally:
                await browser.close()

    async def _dump(self, page) -> None:
        """Zrzut ekranu przy błędzie — bez tego debugowanie zmian na stronie jest zgadywanką."""
        if not (self._screenshot_dir and page):
            return
        try:
            import os
            os.makedirs(self._screenshot_dir, exist_ok=True)
            path = os.path.join(self._screenshot_dir, f"mq-fail-{int(time.time())}.png")
            await page.screenshot(path=path)
            print(f"[metaquotes-web] zrzut błędu: {path}")
        except Exception as e:  # pragma: no cover
            print(f"[metaquotes-web] nie udało się zapisać zrzutu: {e}")


def chromium_available() -> bool:
    """Czy przegladarka Playwrighta jest zainstalowana (`playwright install chromium`).

    Bez niej provisioning failuje przy pierwszym zakupie, a konto wisi w
    statusie `provisioning` — lepiej ostrzec juz przy starcie serwera.
    """
    import os
    from pathlib import Path as _Path
    env = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    roots = [_Path(env)] if env else [
        _Path.home() / "Library" / "Caches" / "ms-playwright",      # macOS
        _Path.home() / ".cache" / "ms-playwright",                  # Linux
        _Path(os.getenv("LOCALAPPDATA", "")) / "ms-playwright",     # Windows
    ]
    return any(r.is_dir() and any(r.glob("chromium*")) for r in roots)


def make_opener(settings=None) -> MetaQuotesWebOpener | None:
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    if not settings.metaquotes_web_enabled:
        return None
    return MetaQuotesWebOpener(
        url=settings.metaquotes_web_url,
        headless=settings.metaquotes_web_headless,
        min_interval_sec=settings.metaquotes_web_min_interval_sec,
        timeout_ms=settings.metaquotes_web_timeout_sec * 1000,
        screenshot_dir=(settings.metaquotes_web_screenshot_dir or None),
    )
