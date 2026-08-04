"""Weryfikacja wizualna całej strony: screenshoty desktop+mobile każdej podstrony,
zero błędów konsoli, brak poziomego overflow.

Użycie:  python scripts/verify_site.py [http://127.0.0.1:8010]
Zrzuty lądują w backend/verify-out/.
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
OUT = Path(__file__).resolve().parent.parent / "verify-out"
OUT.mkdir(exist_ok=True)

PAGES = ["/", "/faq", "/affiliate", "/objectives", "/terms", "/privacy", "/risk-disclosure",
         "/refund-policy", "/verify", "/portal"]
# /admin celowo poza lista: panel jest zamkniety i bez sesji administratora
# zwraca 404, wiec sprawdzanie go tutaj zawsze konczyloby sie bledem.
VIEWPORTS = {"desktop": {"width": 1440, "height": 900},
             "mobile": {"width": 390, "height": 844}}
IGNORE = ("cdn.tailwindcss.com",)   # ostrzeżenie dev-buildu Tailwinda w portalu/adminie


async def main() -> int:
    failures: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for vp_name, vp in VIEWPORTS.items():
            page = await browser.new_page(viewport=vp)
            errors: list[str] = []
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            for path in PAGES:
                errors.clear()
                await page.goto(BASE + path, wait_until="networkidle")
                await page.wait_for_timeout(900)
                slug = path.strip("/").replace("/", "-") or "home"
                await page.screenshot(path=str(OUT / f"{slug}-{vp_name}.png"), full_page=True)
                real = [e for e in errors if not any(i in e for i in IGNORE)]
                if real:
                    failures.extend(f"{path} [{vp_name}] {e}" for e in real)
                overflow = await page.evaluate(
                    "document.documentElement.scrollWidth - window.innerWidth")
                if overflow > 1:
                    failures.append(f"{path} [{vp_name}] HORIZONTAL OVERFLOW +{overflow}px")
            await page.close()
        await browser.close()

    print(f"Zrzuty: {OUT}")
    if failures:
        print("PROBLEMY:")
        for f in failures:
            print("  -", f)
        return 1
    print("OK: zero błędów konsoli, zero poziomego overflow na wszystkich stronach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
