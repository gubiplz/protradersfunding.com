"""Countdown GIF for campaign emails ("11:54:54 LEFT").

Email clients run no JavaScript, so the timer is an animated GIF rendered when the email is
opened: 60 one-second frames counting down from the remaining time. Each open fetches a fresh
image (no caching), so the starting point is always current. An 8-colour palette keeps it small.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).resolve().parent.parent / "static" / "fonts" / "email" / "SpaceGrotesk.ttf"
W, H = 1200, 160          # shown at 600x80 in the email: sharp on retina screens
BG = (15, 138, 126)       # #0f8a7e, site accent
FG = (255, 255, 255)
FRAMES = 60


@lru_cache(maxsize=4)
def _font(size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    try:
        f.set_variation_by_axes([700])   # variable font: bold
    except Exception:  # pragma: no cover - static fallback
        pass
    return f


def tekst(sekundy: int) -> str:
    """Label for the given remaining seconds: '11:54:54 LEFT', '2d 03:10:05 LEFT', 'OFFER ENDED'."""
    if sekundy <= 0:
        return "OFFER ENDED"
    d, r = divmod(sekundy, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    zegar = f"{h:02d}:{m:02d}:{s:02d}"
    return f"{d}d {zegar} LEFT" if d else f"{zegar} LEFT"


def _klatka(napis: str, wzor: str) -> Image.Image:
    """One frame. `wzor` (widest label of the same shape) fixes the start x, so proportional
    digits don't make the text jump left and right every second."""
    img = Image.new("RGB", (W, H), BG)
    rys = ImageDraw.Draw(img)
    font = _font(92)
    x0, _, x1, _ = rys.textbbox((0, 0), wzor, font=font)
    _, y0, _, y1 = rys.textbbox((0, 0), "0:LEFT", font=font)
    rys.text(((W - (x1 - x0)) / 2 - x0, (H - (y1 - y0)) / 2 - y0), napis, font=font, fill=FG)
    # 8 shades of the two colours: smooth edges, still a small GIF.
    return img.quantize(colors=8, method=Image.Quantize.MEDIANCUT)


def _wzor(napis: str) -> str:
    return "".join("8" if c.isdigit() else c for c in napis)


def gif(koniec: datetime, teraz: datetime | None = None) -> bytes:
    """Animated GIF counting down to `koniec` (aware or naive UTC)."""
    teraz = teraz or datetime.now(timezone.utc)
    if koniec.tzinfo is None:
        koniec = koniec.replace(tzinfo=timezone.utc)
    zostalo = int((koniec - teraz).total_seconds())
    if zostalo <= 0:
        klatki = [_klatka(tekst(0), tekst(0))]
    else:
        wzor = _wzor(tekst(zostalo))
        klatki = [_klatka(tekst(zostalo - i), wzor) for i in range(min(FRAMES, zostalo + 1))]
    out = io.BytesIO()
    klatki[0].save(out, format="GIF", save_all=True, append_images=klatki[1:], duration=1000,
                   loop=0, optimize=True, disposal=1)
    return out.getvalue()
