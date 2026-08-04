"""Modele ORM.

Account trzyma konfigurację reguł + bieżący stan runtime (equity, peak, baseline
dnia, liczniki). Dochodzą modele biznesowe prop firmy: Trader (konto klienta),
Product (plan challenge'a), Order (zakup przez Stripe), PayoutRequest (wypłata)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Trader — konto klienta (onboarding/login)                                  #
# --------------------------------------------------------------------------- #
class Trader(Base):
    __tablename__ = "traders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Dane wymagane przez formularz rejestracji konta demo MT5 (zbierane przy płatności).
    first_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ISO2 kraju wybranego przy numerze. Dopuszczalna długość numeru zależy od
    # kraju, a z samego „+1" nie da się odtworzyć, czy to USA, Kanada, czy
    # któraś z wysp karaibskich — dlatego wybór trzymamy osobno.
    phone_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # program afiliacyjny / referral
    referral_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    # Indeks: /api/auth/me liczy poleconych i prowizje przy KAZDYM wejsciu do
    # portalu, a bez niego oba zapytania skanuja cala tabele traderow.
    referred_by: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # KYC
    kyc_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|approved|rejected
    kyc_fullname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kyc_country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kyc_doc_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kyc_dob: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kyc_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kyc_id_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kyc_id_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # nazwy plikow dokumentow w backend/uploads/kyc/{trader_id}/ (podglad tylko admin)
    kyc_doc_front: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kyc_doc_back: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kyc_doc_residence: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # preferencje powiadomien e-mail (Settings -> Notification Preferences)
    notify_updates: Mapped[bool] = mapped_column(Boolean, default=True)     # welcome/creds/kyc
    notify_trading: Mapped[bool] = mapped_column(Boolean, default=True)     # fazy/funded/breach
    notify_payouts: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_marketing: Mapped[bool] = mapped_column(Boolean, default=True)   # nic nie wysylamy, ale pref istnieje
    # Preferencje UI (JSON jako string, np. {"sort": {...}}) — zapisywane z
    # portalu i panelu admina przez PATCH /api/me, trzymane na koncie.
    ui_prefs: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Weryfikacja adresu e-mail: nowi traderzy dostają 6-cyfrowy kod przy
    # rejestracji; istniejące konta są uznane za zweryfikowane (DEFAULT TRUE).
    email_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verify_code: Mapped[str | None] = mapped_column(String(6), nullable=True)

    # Moment akceptacji regulaminu i polityki prywatności przy rejestracji
    # (checkbox jest wymagany; konta sprzed wdrożenia mają NULL).
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Stabilny identyfikator Google (claim `sub` z id_tokenu) — konto założone
    # lub podpięte przez „Sign in with Google". E-mail może się u Google
    # zmienić, sub nigdy.
    google_sub: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Kredyty sklepowe (USD) — nadaje admin, automatycznie odliczane od ceny
    # nastepnego zakupu w checkoucie. Pelna historia w tabeli credit_ledger.
    credits_usd: Mapped[float] = mapped_column(Float, default=0.0)
    kyc_submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kyc_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Powód odrzucenia KYC — pokazywany traderowi w portalu i w mailu;
    # czyszczony przy approve/reset.
    kyc_reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # engagement (portal mobilny): dzienny check-in + mystery reveal
    checkin_streak: Mapped[int] = mapped_column(Integer, default=0)
    checkin_last: Mapped[str | None] = mapped_column(String(10), nullable=True)     # UTC "YYYY-MM-DD"
    bonus_points: Mapped[int] = mapped_column(Integer, default=0)
    # Punkty juz wymienione na kody rabatowe. Saldo do wydania to
    # (wydane na challenge'e + bonus_points) - points_spent. TIER liczy sie z
    # sumy DOZYWOTNIEJ, wiec skorzystanie z nagrody nie cofa nikogo ze statusu.
    points_spent: Mapped[int] = mapped_column(Integer, default=0)
    reveal_last: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reveal_payload: Mapped[str | None] = mapped_column(String(240), nullable=True)  # JSON dzisiejszego wyniku
    streak_freezes: Mapped[int] = mapped_column(Integer, default=1)                 # ratuje serię po 1 dniu przerwy

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    accounts: Mapped[list["Account"]] = relationship(back_populates="trader")
    orders: Mapped[list["Order"]] = relationship(back_populates="trader")


# --------------------------------------------------------------------------- #
#  Product — plan challenge'a sprzedawany w sklepie                           #
# --------------------------------------------------------------------------- #
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    account_size: Mapped[float] = mapped_column(Float)
    steps: Mapped[int] = mapped_column(Integer, default=2)          # 1-step / 2-step
    price_usd: Mapped[float] = mapped_column(Float)

    profit_target_p1: Mapped[float] = mapped_column(Float, default=8)
    profit_target_p2: Mapped[float] = mapped_column(Float, default=5)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5)
    max_overall_loss_pct: Mapped[float] = mapped_column(Float, default=10)
    drawdown_type: Mapped[str] = mapped_column(String(16), default="static")
    min_trading_days: Mapped[int] = mapped_column(Integer, default=4)
    profit_split_pct: Mapped[float] = mapped_column(Float, default=80)
    # Limit lacznego wolumenu otwartych pozycji (w lotach) — anty-„fullport”.
    max_lots: Mapped[float] = mapped_column(Float, default=6.0)
    consistency_pct: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------- #
#  Order — zakup challenge'a (Stripe lub mock)                                #
# --------------------------------------------------------------------------- #
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    product_key: Mapped[str] = mapped_column(String(48))
    amount_usd: Mapped[float] = mapped_column(Float)
    coupon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|paid|failed
    provider: Mapped[str] = mapped_column(String(16), default="mock")    # stripe|mock
    stripe_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # BOGO: klucz produktu, za ktory klient zaplacil (gdy admin przyznaje wiekszy tier)
    bogo_paid_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # Add-on Weekend Trading ($199): 2 dodatkowe dni handlu w tygodniu.
    weekend_trading: Mapped[bool] = mapped_column(Boolean, default=False)
    # Kredyty sklepowe odliczone od ceny tego zamowienia. Saldo tradera schodzi
    # dopiero przy DOMKNIECIU platnosci — porzucony checkout nie pali srodkow.
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)
    # Reczna flaga admina dla nieoplaconych zamowien: NULL | awaiting_crypto
    flag: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Powod recznego oznaczenia jako failed — widoczny w panelu przy statusie.
    fail_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Indeks: zamowienia konta czyta sie w petli po kontach (lista kont,
    # ranking, faktury) — bez niego kazdy obrot skanuje cala tabele zamowien.
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trader: Mapped[Trader] = relationship(back_populates="orders")


# --------------------------------------------------------------------------- #
#  Centrum powiadomień w portalu (dzwonek); subskrypcje push — PushSubscription
#  na końcu pliku                                                             #
# --------------------------------------------------------------------------- #
class Notification(Base):
    """Wpis w centrum powiadomień (dzwonek w portalu). Trzymamy najnowsze 50
    na tradera — starsze kasuje retencja przy insercie."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(400), default="")
    url: Mapped[str] = mapped_column(String(200), default="/portal")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# --------------------------------------------------------------------------- #
#  CreditLedger — historia kredytów sklepowych (zasilenia admina i zużycie)   #
# --------------------------------------------------------------------------- #
class CreditLedger(Base):
    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)     # dodatni = zasilenie, ujemny = zużycie/korekta
    note: Mapped[str | None] = mapped_column(String(160), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# --------------------------------------------------------------------------- #
#  RewardCode — kod rabatowy KUPIONY za punkty lojalnosciowe                  #
# --------------------------------------------------------------------------- #
class RewardCode(Base):
    """Osobisty kod jednorazowy, wymieniony przez tradera za punkty.

    Osobny byt, bo zadna z istniejacych rzeczy tego nie unosi: `catalog.COUPONS`
    to zaszyty slownik kodow GLOBALNYCH (kazdy zna, kazdy uzyje, bez konca), a
    `Trader.reveal_payload` trzyma JEDEN slot nadpisywany kazdego dnia — kod
    kupiony za punkty zginalby traderowi przy nastepnym losowaniu.

    Jednorazowosc pilnuje `used_at`: kod schodzi dopiero przy DOMKNIECIU
    platnosci (provisioning), tak samo jak kredyty sklepowe, wiec porzucony
    checkout go nie pali.
    """
    __tablename__ = "reward_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    pct: Mapped[float] = mapped_column(Float)              # procent znizki
    points_spent: Mapped[int] = mapped_column(Integer)     # ile punktow kosztowal
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)


# --------------------------------------------------------------------------- #
#  AchievementReward — nagroda za prog odznak (3/8, 5/8, 8/8)                 #
# --------------------------------------------------------------------------- #
class AchievementReward(Base):
    """Slad odebrania nagrody za prog odznak. Jeden wiersz = jeden odbior.

    Osobna tabela, a nie flaga na traderze, bo jednorazowosc ma pilnowac BAZA:
    `UniqueConstraint(trader_id, tier)` zamyka wyscig dwoch rownoleglych klikniec
    „Claim" mocniej niz jakikolwiek `if` w kodzie. Sama nagroda mieszka tam,
    gdzie jej miejsce — kod rabatowy w `reward_codes`, przyznane konto w
    `accounts` — a tutaj zostaje tylko wskaznik.
    """
    __tablename__ = "achievement_rewards"
    __table_args__ = (UniqueConstraint("trader_id", "tier", name="uq_ach_reward_trader_tier"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    tier: Mapped[int] = mapped_column(Integer)             # ile odznak: 3, 5, 8
    code: Mapped[str | None] = mapped_column(String(24), nullable=True)      # nagroda = kod rabatowy
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)  # nagroda = konto
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# --------------------------------------------------------------------------- #
#  Account — konto challenge / funded                                         #
# --------------------------------------------------------------------------- #
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), index=True)
    trader_name: Mapped[str] = mapped_column(String(120), default="")
    trader_id: Mapped[int | None] = mapped_column(ForeignKey("traders.id"), nullable=True, index=True)
    metaapi_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # poświadczenia platformy MT5 dostarczane traderowi
    platform_login: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_password: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Kolumna zostaje dla kont zalozonych wczesniej; NIC juz jej nie zapisuje
    # ani nie pokazuje — hasla inwestora nie generujemy.
    platform_investor_password: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Czy za kontem stoi REALNY rachunek MT5. False = poswiadczenia wygenerowane
    # lokalnie; feed musi takie konto pominac, inaczej probowalby sie logowac do
    # web terminala loginem, ktorego tam nie ma.
    mt5_backed: Mapped[bool] = mapped_column(Boolean, default=True)

    # Skad wzielo sie konto: zakup klienta czy przyznanie przez admina
    # (promocja/BOGO/rekompensata). Trader widzi to w portalu i w mailu.
    source: Mapped[str] = mapped_column(String(16), default="purchase")   # purchase|grant
    grant_note: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # BOGO: rozmiar tieru, za ktory klient FAKTYCZNIE zaplacil. Bez tego pola
    # zdanie „you paid for the $25K tier” byloby zmyslone — jest albo prawdziwe,
    # albo mail w ogole go nie zawiera.
    bogo_paid_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekend_trading: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Trade BOT (admin) ---
    # Gdy wlaczony, konto NIE jest czytane z MT5 — snapshoty generuje tradebot.py.
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bot_style: Mapped[str | None] = mapped_column(String(16), nullable=True)   # scalper|balanced|swing
    bot_pace: Mapped[str | None] = mapped_column(String(16), nullable=True)    # light|steady|busy
    bot_target_pct: Mapped[float] = mapped_column(Float, default=0.0)          # 0 = bez limitu
    # Pauza: bot NIE otwiera nowych pozycji, ale konto zostaje pod jego kontrolą
    # (saldo nie resynchronizuje sie do feedu, jak przy pelnym Stopie).
    bot_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Nieodgadywalny token certyfikatu — publiczny link /certificate/{token}
    # i weryfikacja /verify/{token} działają bez logowania, ale nie da się
    # enumerować kont po kolejnych ID.
    cert_token: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)

    # --- konfiguracja challenge'a (z Product) ---
    product_key: Mapped[str] = mapped_column(String(48), default="2step-100k")
    preset: Mapped[str] = mapped_column(String(32), default="2step-100k")  # back-compat
    initial_balance: Mapped[float] = mapped_column(Float, default=100_000)
    steps: Mapped[int] = mapped_column(Integer, default=2)
    profit_target_p1: Mapped[float] = mapped_column(Float, default=8)
    profit_target_p2: Mapped[float] = mapped_column(Float, default=5)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5)
    max_overall_loss_pct: Mapped[float] = mapped_column(Float, default=10)
    min_trading_days: Mapped[int] = mapped_column(Integer, default=4)
    drawdown_type: Mapped[str] = mapped_column(String(16), default="static")
    profit_split_pct: Mapped[float] = mapped_column(Float, default=80.0)
    max_lots: Mapped[float] = mapped_column(Float, default=6.0)
    consistency_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = wyłączona; 40 = reguła 40% best-day

    # --- stan runtime ---
    phase: Mapped[str] = mapped_column(String(16), default="eval_1")
    status: Mapped[str] = mapped_column(String(16), default="active")
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    open_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    day_key: Mapped[str] = mapped_column(String(16), default="")
    day_start_equity: Mapped[float] = mapped_column(Float, default=0.0)
    day_start_balance: Mapped[float] = mapped_column(Float, default=0.0)
    best_day_profit: Mapped[float] = mapped_column(Float, default=0.0)
    trading_days_count: Mapped[int] = mapped_column(Integer, default=0)
    last_counted_trading_day: Mapped[str] = mapped_column(String(16), default="")
    breach_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Ile razy konto poszlo o szczebel w gore planem skalowania. Po skalowaniu
    # rozmiar znow rowna sie rozmiarowi planu z cennika, wiec porownanie
    # `initial_balance > Product.account_size` przestalo odrozniac konto
    # wyskalowane od swiezo kupionego — odznaka potrzebuje wlasnego licznika.
    scale_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trader: Mapped[Trader | None] = relationship(back_populates="accounts")
    snapshots: Mapped[list["EquitySnapshot"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    breaches: Mapped[list["Breach"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    payouts: Mapped[list["Payout"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    payout_requests: Mapped[list["PayoutRequest"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    open_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    day_key: Mapped[str] = mapped_column(String(16), default="")

    account: Mapped[Account] = relationship(back_populates="snapshots")


class Trade(Base):
    """Rejestr pojedynczych transakcji na koncie.

    Dzis wypelnia go wylacznie Trade BOT (`source='bot'`), ale tabela jest
    swiadomie ogolna: gdy kiedys uda sie czytac historie z MT5, trafi w to samo
    miejsce i portal nie bedzie wymagal zmian. Pozycja otwarta ma
    `status='open'` i `closed_at=None` — dzieki temu restart procesu nie gubi
    biezacej pozycji bota.
    """
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))            # buy|sell
    lots: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)  # dla otwartej = floating
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open|closed
    source: Mapped[str] = mapped_column(String(8), default="bot")
    # Plan bota dla tej pozycji (docelowy wynik i moment zamkniecia) — trzymany
    # w bazie, zeby restart nie przerywal transakcji w polowie.
    plan_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    plan_close_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Certificate(Base):
    """Certyfikat osiagniecia — jeden wiersz na KAZDE osiagniecie konta.

    Wczesniej konto mialo jeden `cert_token`, wiec nie dalo sie wystawic osobnego
    dokumentu za zaliczenie etapu 1, etapu 2 i za funded. Tutaj kazdy z nich ma
    wlasny numer, ktory da sie zweryfikowac osobno.
    """
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)   # phase_1|phase_2|funded
    cert_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Breach(Base):
    __tablename__ = "breaches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(255))
    equity_at_breach: Mapped[float] = mapped_column(Float)

    account: Mapped[Account] = relationship(back_populates="breaches")


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    profit_amount: Mapped[float] = mapped_column(Float)
    trader_share: Mapped[float] = mapped_column(Float)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Nieodgadywalny token certyfikatu wyplaty — link mozna udostepniac, ale nie
    # da sie enumerowac cudzych wyplat po ID.
    cert_token: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # Czy ta wyplata zdjela zysk z konta (saldo wrocilo do startowego). Krzywa
    # equity musi wiedziec, w ktorym miejscu balans spadl — inaczej ostatni punkt
    # wykresu nie zgadzalby sie z realnym saldem konta.
    balance_reset: Mapped[bool] = mapped_column(Boolean, default=True)
    # Czy wpis pokazuje sie na pasie certyfikatow na landingu. Dotyczy WYLACZNIE
    # publikacji: dokument, jego QR i weryfikacja pod /payout/{token} powstaja
    # zawsze. Wczesniej jedynym sposobem zdjecia wyplaty ze strony bylo cofniecie
    # certyfikatu, ktore zabijalo tez publiczny link tradera.
    show_on_lp: Mapped[bool] = mapped_column(Boolean, default=True)
    # Czy pas oddaje PELNY certyfikat: imie i nazwisko, kwota co do centa i
    # token, ktorym kazdy moze zweryfikowac dokument. Domyslnie True, bo trader
    # zgadza sie na publikacje przy zakladaniu konta.
    #
    # Zostaje jako WYLACZNIK, per wyplata. Zgoda dana przy rejestracji da sie
    # cofnac i wtedy musi byc gdzie to klikniac — bez tego jedynym sposobem
    # zdjecia nazwiska ze strony byloby wycofanie certyfikatu, ktore zabija tez
    # prywatny link tradera. Wylaczona flaga wraca do wpisu zamaskowanego
    # ("Imogen I.", kwota w pelnych dolarach, bez linku), a nie usuwa wpisu.
    cert_public: Mapped[bool] = mapped_column(Boolean, default=True)

    account: Mapped[Account] = relationship(back_populates="payouts")


class PayoutRequest(Base):
    __tablename__ = "payout_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    profit_amount: Mapped[float] = mapped_column(Float)
    trader_share: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(40), default="bank")
    # Dane wypłaty (JSON): usdt -> network+address, bank -> holder+iban+swift(+bank_name),
    # wise -> email. Bez nich admin nie miałby dokąd wysłać pieniędzy.
    details: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|paid|rejected
    # Powód odrzucenia — trader widzi go przy wniosku, więc pisany do klienta.
    reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    account: Mapped[Account] = relationship(back_populates="payout_requests")


class PoolAccount(Base):
    """Pula gotowych kont MT5, ktore admin wrzuca recznie, do przydzielenia przy zakupie.

    Tak dziala firma fundingowa bez wlasnego serwera MT5: konta powstaja u brokera
    poza systemem, admin wkleja tu ich poswiadczenia, a provisioning tylko je
    przypisuje. Do wpisu potrzebne sa cztery rzeczy: login, haslo, serwer i rozmiar.
    """
    __tablename__ = "pool_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_login: Mapped[str] = mapped_column(String(64))
    platform_password: Mapped[str] = mapped_column(String(64))
    platform_server: Mapped[str] = mapped_column(String(64))
    account_size: Mapped[float] = mapped_column(Float, index=True)
    # Zostaje dla kont, ktore ktos kiedys zarejestrowal w MetaApi — admin tego
    # NIE wypelnia, pole nie pojawia sie w panelu.
    metaapi_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    claimed_by_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Komu konto trafilo — bez tego nie da sie odpowiedziec na pytanie "czyj jest
    # ten rachunek MT5", a przy realnych pieniadzach to pierwsze pytanie.
    claimed_by_trader_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Powod wycofania rachunku z obiegu (np. konto tradera skasowane). Rachunek
    # raz wydany NIE wraca do puli — ma historie transakcji i zna go poprzedni
    # wlasciciel — a to pole mowi panelowi, dlaczego wpis nie jest wolny.
    retired_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # SYMULOWANY wpis: login/haslo wygenerowane u nas, za rachunkiem nie stoi
    # zaden serwer MT5. Konto, ktore go dostanie, ma miec mt5_backed=False —
    # inaczej realny feed probowalby logowac sie zmyslonymi danymi.
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AppSetting(Base):
    """Ustawienia przelaczane z panelu w czasie dzialania (env wymagalby deployu)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")


class JournalEntry(Base):
    """Dziennik tradera — prywatne notatki, opcjonalnie przypiete do konta."""
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text, default="")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|answered|closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author: Mapped[str] = mapped_column(String(12))  # trader|admin
    body: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PushSubscription(Base):
    """Subskrypcja web push jednego urządzenia (trader może mieć kilka).

    Endpoint jest kluczem naturalnym: przeglądarka po re-instalacji PWA potrafi
    oddać ten sam endpoint dla innego zalogowanego tradera, więc subscribe robi
    upsert po endpointcie zamiast plodzić duplikaty."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(600), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))    # klucz szyfrowania przegladarki
    auth: Mapped[str] = mapped_column(String(100))      # sekret uwierzytelniajacy push service
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class KycFile(Base):
    """Dokument KYC trzymany w bazie, nie na dysku — Vercel ma read-only
    filesystem, a /tmp jest ulotny między requestami. Przy ≤5 MB × 3 pliki
    na tradera Postgres spokojnie to udźwignie. Jeden wiersz na (trader, kind);
    re-upload nadpisuje. UWAGA: kolumny `data` nie ładować w list-query'ach."""
    __tablename__ = "kyc_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))       # id_front|id_back|residence
    filename: Mapped[str] = mapped_column(String(120))
    mime: Mapped[str] = mapped_column(String(40))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TelemetryEvent(Base):
    """Zdarzenie produktowe (telemetria wewnętrzna, bez zewnętrznych usług).

    Zapisywane głównie server-side w miejscach, gdzie zdarzenie i tak zachodzi
    (login, zamówienie, KYC...); frontend może dorzucić tylko whitelistowane
    nazwy przez POST /api/telemetry. Agregacja w panelu admina."""
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trader_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    props: Mapped[str | None] = mapped_column(String(400), nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
