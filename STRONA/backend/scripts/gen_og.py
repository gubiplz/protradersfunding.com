"""Generuje static/img/og.png — obrazek Open Graph do podglądów linków.

Renderuje HTML w Playwrighcie, żeby karta korzystała z tego samego design
systemu co strona (te same fonty, ta sama paleta, to samo logo). Po zmianie
nazwy marki albo znaku wystarczy:

    python scripts/gen_og.py

Serwer deweloperski nie jest potrzebny — plik ładuje assety z dysku.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402

STATIC = BACKEND / "static"
WYNIK = STATIC / "img" / "og.png"
SZEROKOSC, WYSOKOSC = 1200, 630


def _html(nazwa: str) -> str:
    # Strona jest wstrzykiwana przez set_content (origin about:blank), wiec
    # podzasoby z file:// sa blokowane — logo idzie jako data URI.
    import base64

    fonts = (STATIC / "fonts").as_uri()
    logo = "data:image/png;base64," + base64.b64encode(
        (STATIC / "img" / "logo.png").read_bytes()).decode()
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Inter';src:url('{fonts}/inter.woff2') format('woff2');font-weight:100 900}}
@font-face{{font-family:'Space Grotesk';src:url('{fonts}/space-grotesk-latin.woff2') format('woff2');font-weight:300 700}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:{SZEROKOSC}px;height:{WYSOKOSC}px;background:#f6f7fb;font-family:'Inter',sans-serif;
  color:#0f172a;position:relative;overflow:hidden}}
.glow{{position:absolute;border-radius:50%;filter:blur(90px);opacity:.5}}
.g1{{width:520px;height:520px;left:-160px;top:-200px;background:rgba(129,140,248,.45)}}
.g2{{width:460px;height:460px;right:-140px;bottom:-190px;background:rgba(196,181,253,.5)}}
.in{{position:relative;padding:64px 72px;height:100%;display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:16px;margin-bottom:56px}}
.brand img{{width:66px;height:66px}}
.brand span{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:40px;letter-spacing:-.02em}}
h1{{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:66px;line-height:1.1;
  letter-spacing:-.03em;max-width:900px}}
h1 em{{font-style:normal;color:#6366f1}}
.chips{{display:flex;gap:14px;margin-top:auto}}
.chip{{border:1px solid #e2e5ee;background:#fff;border-radius:12px;padding:14px 20px;
  font-size:19px;color:#475569;font-weight:500}}
</style></head><body>
<div class="glow g1"></div><div class="glow g2"></div>
<div class="in">
  <div class="brand"><img src="{logo}" alt=""><span>{nazwa}</span></div>
  <h1>Trade our capital.<br><em>Keep up to 90%</em> of the rewards.</h1>
  <div class="chips">
    <div class="chip">MetaTrader 5</div>
    <div class="chip">Up to $200,000</div>
    <div class="chip">Automated risk engine</div>
  </div>
</div></body></html>"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    nazwa = get_settings().site_name
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": SZEROKOSC, "height": WYSOKOSC}, device_scale_factor=1)
        pg.set_content(_html(nazwa), wait_until="load")
        pg.wait_for_timeout(700)          # fonty
        pg.screenshot(path=str(WYNIK))
        b.close()
    print(f"zapisano {WYNIK} ({nazwa})")


if __name__ == "__main__":
    main()
