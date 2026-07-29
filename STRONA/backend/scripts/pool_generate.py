"""Zakłada konta demo MT5 na MetaQuotes-Demo i wrzuca je do puli.

Ta sama ścieżka, którą chodzi przycisk „Auto-generate" w panelu: formularz
„Open Demo account" na web.metatrader.app sterowany Playwrightem. Skrypt jest
po to, żeby dało się to zrobić Z MASZYNY, KTÓRA MA PRZEGLĄDARKĘ — aplikacja na
hostingu bezserwerowym nie uruchomi Chromium, więc tam przycisk tylko wyjaśnia,
że trzeba użyć tej drogi.

    python scripts/pool_generate.py --size 50000 --count 5

Domyślnie pisze wprost do bazy z `DATABASE_URL`. Żeby zamiast tego uderzyć
w zdalny panel (np. produkcję na Vercelu), podaj adres i token admina:

    python scripts/pool_generate.py --size 50000 --count 5 \\
        --api https://protradersfunding.com --admin-token TWOJ_TOKEN

Wymaga jednorazowo: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import metaquotes_web  # noqa: E402
from app.config import get_settings  # noqa: E402


def _zapisz_lokalnie(creds, rozmiar: float) -> int:
    from app.db import SessionLocal, init_db
    from app.models import PoolAccount

    init_db()
    s = SessionLocal()
    try:
        p = PoolAccount(platform_login=str(creds.login), platform_password=creds.password,
                        platform_server=creds.server, account_size=rozmiar)
        s.add(p)
        s.commit()
        return p.id
    finally:
        s.close()


def _zapisz_przez_api(creds, rozmiar: float, api: str, token: str) -> int:
    dane = json.dumps({"platform_login": str(creds.login), "platform_password": creds.password,
                       "platform_server": creds.server, "account_size": rozmiar}).encode()
    req = urllib.request.Request(f"{api.rstrip('/')}/api/admin/pool", data=dane, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Admin-Token": token})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["id"]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Zakłada konta demo MT5 i dodaje je do puli")
    ap.add_argument("--size", type=float, required=True, help="rozmiar konta w USD, np. 50000")
    ap.add_argument("--count", type=int, default=1, help="ile kont założyć (domyślnie 1)")
    ap.add_argument("--first-name", default="Pro")
    ap.add_argument("--last-name", default="Trader")
    ap.add_argument("--api", default="", help="adres panelu; puste = zapis wprost do DATABASE_URL")
    ap.add_argument("--admin-token", default="", help="token admina, gdy używasz --api")
    args = ap.parse_args()

    if args.api and not args.admin_token:
        ap.error("--api wymaga --admin-token")

    settings = get_settings()
    if not metaquotes_web.chromium_available():
        print("BRAK CHROMIUM. Uruchom: pip install playwright && playwright install chromium")
        raise SystemExit(1)

    opener = metaquotes_web.MetaQuotesWebOpener(
        url=settings.metaquotes_web_url, headless=settings.metaquotes_web_headless,
        min_interval_sec=settings.metaquotes_web_min_interval_sec,
        timeout_ms=settings.metaquotes_web_timeout_sec * 1000,
        screenshot_dir=(settings.metaquotes_web_screenshot_dir or None),
    )

    udane = 0
    for i in range(1, args.count + 1):
        spec = metaquotes_web.WebDemoSpec(
            first_name=args.first_name, last_name=args.last_name,
            email=settings.mail_from, phone=settings.metaquotes_web_default_phone,
            deposit=args.size, leverage=settings.metaquotes_web_leverage,
            account_type=settings.metaquotes_web_account_type,
        )
        print(f"[{i}/{args.count}] zakładam konto ${args.size:,.0f} …", flush=True)
        try:
            creds = await opener.open_demo_account(spec)
        except Exception as e:
            print(f"    NIE UDAŁO SIĘ: {e}")
            continue
        wpis = (_zapisz_przez_api(creds, args.size, args.api, args.admin_token) if args.api
                else _zapisz_lokalnie(creds, args.size))
        print(f"    OK — {creds.login}@{creds.server} (wpis w puli #{wpis})")
        udane += 1

    print(f"\ngotowe: {udane}/{args.count} kont w puli")


if __name__ == "__main__":
    asyncio.run(main())
