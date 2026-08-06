"""Kto zaczął płacić i nie dokończył, dostaje JEDEN mail — i nikt inny.

Mail po porzuconym koszyku jest tanim sposobem na odzyskanie sprzedaży i
najdroższym sposobem na spalenie domeny: leci do ludzi, którzy o niego nie
prosili, w sprawie pieniędzy. Dlatego testy pilnują nie tego, że mail wychodzi
(to łatwe), tylko tego, do kogo NIE wychodzi i że nigdy nie wychodzi dwa razy.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")

import pytest  # noqa: E402

from app import auth, catalog, notify  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import _checkout_recovery  # noqa: E402
from app.models import Order, Trader  # noqa: E402

init_db()
s = SessionLocal(); catalog.seed_products(s); s.close()

LICZNIK = iter(range(10000))


@pytest.fixture
def maile(monkeypatch):
    """Przechwytuje wysyłkę na najniższym poziomie — `send` samo w sobie tylko
    kolejkuje, więc podstawka musi siedzieć pod nim, nie na nim."""
    zebrane = []
    monkeypatch.setattr(notify, "_send_teraz",
                        lambda event, to, ctx=None: zebrane.append((event, to, ctx or {})))
    return zebrane


def _zamowienie(*, wiek_h: float = 2, status: str = "pending", flag=None,
                credits: float = 0.0, marketing: bool = True) -> tuple[int, str]:
    email = f"recov{next(LICZNIK)}@test.pl"
    s = SessionLocal()
    tr = Trader(email=email, password_hash=auth.hash_password("haslo12345"),
                full_name="Recovery Tester", first_name="Jan",
                referral_code=email.split("@")[0].upper(), notify_marketing=marketing)
    s.add(tr); s.commit()
    zam = Order(trader_id=tr.id, product_key="2step-25k", amount_usd=299.0,
                status=status, provider="stripe", flag=flag, credits_used=credits,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(hours=wiek_h))
    s.add(zam); s.commit()
    oid = zam.id; s.close()
    return oid, email


def test_porzucony_koszyk_dostaje_mail(maile):
    oid, email = _zamowienie()
    wynik = _checkout_recovery()
    assert wynik["sent"] >= 1
    moje = [m for m in maile if m[1] == email]
    assert len(moje) == 1, "porzucony koszyk nie dostał maila"
    assert moje[0][0] == "checkout_recovery"
    assert moje[0][2]["product_label"], "mail bez nazwy produktu nic klientowi nie mówi"


def test_mail_leci_tylko_raz(maile):
    oid, email = _zamowienie()
    _checkout_recovery()
    maile.clear()
    _checkout_recovery()                      # drugi przebieg crona tego samego dnia
    assert [m for m in maile if m[1] == email] == [], "drugi mail o tym samym koszyku"
    s = SessionLocal()
    assert s.get(Order, oid).recovery_sent_at is not None
    s.close()


def test_swiezy_koszyk_nie_dostaje_maila(maile):
    """Ktoś może właśnie wpisywać numer karty — mail w tej chwili to sabotaż."""
    _, email = _zamowienie(wiek_h=0.2)        # 12 minut, próg to 60
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_oplacone_zamowienie_nie_dostaje_maila(maile):
    _, email = _zamowienie(status="paid")
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_recznie_oznaczone_failed_nie_dostaje_maila(maile):
    """Cron rusza WYŁĄCZNIE nierozstrzygnięte koszyki (`pending`).

    `failed` admin stawia ręcznie i wie, dlaczego (jest pole `fail_reason`) —
    bywa, że z powodu fraudu albo po rozmowie z klientem. Automat, który dosyła
    tam „dokończ zakup", pisze wtedy dokładnie do kogoś, komu odmówiono.
    Jeśli kiedyś ma to być odwrotnie, niech to będzie decyzja, a nie skutek
    uboczny — dlatego stoi tu test, a nie sam filtr w kodzie.
    """
    _, email = _zamowienie(status="failed")
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_czekajace_na_krypto_nie_dostaje_maila(maile):
    """`awaiting_crypto` to nie porzucenie — klient płaci, tylko poza Stripe'em."""
    _, email = _zamowienie(flag="awaiting_crypto")
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_stary_koszyk_poza_oknem_nie_dostaje_maila(maile):
    """Pierwszy przebieg na produkcji nie może odkopać zamówień sprzed tygodni."""
    _, email = _zamowienie(wiek_h=24 * 30)
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_kto_kupil_pozniej_nie_dostaje_maila(maile):
    """Sprawa rozwiązała się sama — przypomnienie o starym koszyku wygląda źle."""
    oid, email = _zamowienie(wiek_h=5)
    s = SessionLocal()
    tid = s.get(Order, oid).trader_id
    s.add(Order(trader_id=tid, product_key="2step-25k", amount_usd=299.0,
                status="paid", provider="stripe",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(hours=1)))
    s.commit(); s.close()
    _checkout_recovery()
    assert [m for m in maile if m[1] == email] == []


def test_kto_wypisal_sie_z_ofert_nie_dostaje_maila(monkeypatch):
    """Bramka kategorii siedzi w środku `_send_teraz`, więc ten jeden test idzie
    PRAWDZIWĄ ścieżką wysyłki, a podstawka siada dopiero na gnieździe SMTP.
    Atrapa założona wyżej (jak w pozostałych testach) przepuściłaby każdą
    regresję w samej bramce — mail marketingowy poszedłby do wypisanych."""
    wyslane = []

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): wyslane.append(msg["To"])

    monkeypatch.setattr(notify.settings, "smtp_host", "smtp.atrapa")
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    _, bez_zgody = _zamowienie(marketing=False)
    _, ze_zgoda = _zamowienie(marketing=True)
    _checkout_recovery()
    assert ze_zgoda in wyslane, "bramka blokuje za dużo — opt-in nie dostał maila"
    assert bez_zgody not in wyslane, "mail marketingowy poszedł do wypisanego z ofert"


def test_kredyty_widoczne_w_mailu(maile):
    """Najczęstsze pytanie po porzuconym koszyku brzmi „czy zjadło mi kredyty"."""
    _, email = _zamowienie(credits=50.0)
    _checkout_recovery()
    ctx = [m for m in maile if m[1] == email][0][2]
    assert ctx["credits_used"] == 50.0
    _, tresc = notify._render("checkout_recovery", ctx)
    assert "NOT used" in tresc, "mail nie uspokaja klienta co do kredytów"
    html = notify._render_html("checkout_recovery", ctx, "x")
    assert html and "not used" in html


def test_mail_bez_kredytow_nie_wspomina_o_kredytach(maile):
    _, email = _zamowienie(credits=0.0)
    _checkout_recovery()
    ctx = [m for m in maile if m[1] == email][0][2]
    _, tresc = notify._render("checkout_recovery", ctx)
    assert "store credit" not in tresc
