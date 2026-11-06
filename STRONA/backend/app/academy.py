"""Academy: moduły szkoleniowe z lekcjami (kafelki z okładką, czas czytania, strona lekcji).

Treść lekcji leży w `academy_data.py`; tu jest struktura (moduły, kolejność, poziom),
czas czytania liczony ze słów i okładki rysowane jako SVG w kolorach PTF — lekkie,
ostre na każdym ekranie i bez cudzych grafik.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .academy_data import LESSONS_RAW

WPM = 200   # tempo czytania do „X min read"

# Moduły w kolejności nauki: (nazwa z danych, slug, tytuł, opis, poziom, kolor akcentu)
MODULES = [
    ("Getting started", "getting-started", "Getting started",
     "How a prop evaluation works, the numbers the rules are checked against, and how to set up.",
     "Beginner", "#36b9a9"),
    ("Risk management", "risk-management", "Risk management",
     "Position sizing, the daily loss limit, static drawdown and stops — the maths that keeps an account alive.",
     "Beginner", "#c6a15b"),
    ("Trading the challenge", "trading-the-challenge", "Trading the challenge",
     "A plan that fits the rules: how many trades, news, weekends and when to call it a day.",
     "Intermediate", "#6d8cff"),
    ("Psychology", "psychology", "Psychology",
     "Why challenges really fail, revenge trading, losing streaks and staying the same trader once funded.",
     "Intermediate", "#e07a9a"),
]

# Czytelne adresy lekcji (slug z tytułu bywał brzydki: „pandl", „isn-t").
SLUGS = {
    "what-prop-trading-actually-is-and-isn-t": "what-prop-trading-is",
    "balance-equity-and-floating-pandl-the-numbers-that-matter": "balance-equity-floating-pnl",
    "why-traders-fail-challenges-it-s-rarely-the-strategy": "why-traders-fail-challenges",
    "from-evaluation-to-funded-keeping-the-same-discipline": "from-evaluation-to-funded",
}

# Motyw okładki dla każdej lekcji (klucz → rysunek w `_motif`).
MOTIF = {
    "what-prop-trading-is": "candles", "how-a-two-step-evaluation-works": "steps",
    "balance-equity-floating-pnl": "equity", "setting-up-metatrader-5-the-right-way": "screen",
    "choosing-your-account-size": "sizes", "position-sizing-the-only-formula-you-need": "formula",
    "understanding-the-daily-loss-limit": "floor", "static-drawdown-explained": "drawdown",
    "stop-losses-where-to-put-them-and-why": "stop", "risk-reward-ratios-that-survive-the-math": "rr",
    "a-trading-plan-that-fits-the-rules": "plan", "how-many-trades-a-day-fewer-than-you-think": "count",
    "trading-the-news-without-blowing-up": "news", "weekend-holding-and-overnight-risk": "gap",
    "when-to-stop-trading-for-the-day": "pause", "why-traders-fail-challenges": "target",
    "revenge-trading-spotting-it-before-it-starts": "flame", "handling-a-losing-streak": "streak",
    "from-evaluation-to-funded": "flag",
}


@dataclass
class Lesson:
    slug: str
    title: str
    module: str          # slug modułu
    n: int               # numer w module (1…)
    minutes: int
    summary: str
    body: str
    motif: str


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _summary(html: str) -> str:
    """Pierwsze zdanie lekcji (do ~150 znaków) jako opis kafelka."""
    t = _text(html).replace("{{ site_name }}", "PTF")
    zd = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    return zd if len(zd) <= 150 else zd[:147].rsplit(" ", 1)[0] + "…"


def _load() -> list[Lesson]:
    out, licz = [], {}
    nazwy = {m[0]: m[1] for m in MODULES}
    for raw_slug, title, cat, body in LESSONS_RAW:
        slug = SLUGS.get(raw_slug, raw_slug)
        mod = nazwy[cat]
        licz[mod] = licz.get(mod, 0) + 1
        out.append(Lesson(slug, title, mod, licz[mod], max(1, math.ceil(len(_text(body).split()) / WPM)),
                          _summary(body), body, MOTIF.get(slug, "candles")))
    return out


LESSONS = _load()
BY_SLUG = {x.slug: x for x in LESSONS}


def modules() -> list[dict]:
    res = []
    for _, slug, title, desc, level, color in MODULES:
        ls = [x for x in LESSONS if x.module == slug]
        res.append({"slug": slug, "title": title, "desc": desc, "level": level, "color": color,
                    "lessons": ls, "minutes": sum(x.minutes for x in ls)})
    return res


def module_of(lesson: Lesson) -> dict:
    return next(m for m in modules() if m["slug"] == lesson.module)


def neighbours(lesson: Lesson) -> tuple[Lesson | None, Lesson | None]:
    i = LESSONS.index(lesson)
    return (LESSONS[i - 1] if i else None, LESSONS[i + 1] if i + 1 < len(LESSONS) else None)


# --------------------------------------------------------------------------- #
#  Okładki SVG                                                                 #
# --------------------------------------------------------------------------- #
def _motif(k: str, c: str) -> str:
    """Rysunek tematu lekcji w polu 400×225 (prawa część okładki)."""
    s = f'stroke="{c}" fill="none" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"'
    soft = f'fill="{c}" fill-opacity=".16"'
    up, dn = "#36b9a9", "#e5687a"
    if k == "candles":
        sw = [(210, 120, 160, 140, 175, up), (240, 100, 150, 115, 160, up), (270, 80, 140, 95, 125, up),
              (300, 95, 150, 105, 135, dn), (330, 60, 130, 70, 100, up)]
        return "".join(f'<line x1="{x}" y1="{h}" x2="{x}" y2="{lo}" stroke="{col}" stroke-width="2.5"/>'
                       f'<rect x="{x - 9}" y="{o}" width="18" height="{cl - o}" rx="2" fill="{col}"/>'
                       for x, h, lo, o, cl, col in sw)
    if k == "steps":
        return (f'<path d="M190 180h50v-40h50v-40h50v-40" {s}/><circle cx="380" cy="60" r="9" fill="{c}"/>'
                f'<text x="215" y="172" fill="{c}" font-size="16" font-weight="700">1</text>'
                f'<text x="265" y="132" fill="{c}" font-size="16" font-weight="700">2</text>')
    if k == "equity":
        return (f'<path d="M190 160 C230 150 250 110 290 115 S350 70 380 60" {s}/>'
                f'<path d="M190 175 C230 170 260 140 300 140 S350 110 380 100" stroke="{c}" stroke-opacity=".45" '
                f'fill="none" stroke-width="2.5" stroke-dasharray="6 6"/>'
                f'<circle cx="380" cy="60" r="6" fill="{c}"/><circle cx="380" cy="100" r="5" fill="{c}" fill-opacity=".5"/>')
    if k == "screen":
        return (f'<rect x="200" y="55" width="180" height="115" rx="10" {s}/><path d="M270 190h40M290 170v20" {s}/>'
                f'<path d="M218 140l30-30 25 18 35-40 40 25" {s}/>')
    if k == "sizes":
        return "".join(f'<rect x="{x}" y="{180 - h}" width="34" height="{h}" rx="5" {soft} stroke="{c}" stroke-width="2.5"/>'
                       for x, h in ((205, 40), (250, 70), (295, 100), (340, 135)))
    if k == "formula":
        return (f'<rect x="205" y="60" width="170" height="110" rx="12" {s}/>'
                f'<text x="290" y="108" text-anchor="middle" fill="{c}" font-size="22" font-weight="700" '
                f'font-family="ui-monospace,monospace">risk ÷ stop</text>'
                f'<text x="290" y="145" text-anchor="middle" fill="{c}" fill-opacity=".7" font-size="16" '
                f'font-family="ui-monospace,monospace">= lots</text>')
    if k == "floor":
        return (f'<path d="M190 80 L230 100 L260 90 L300 130 L330 120 L370 150" {s}/>'
                f'<line x1="190" y1="165" x2="390" y2="165" stroke="{dn}" stroke-width="2.5" stroke-dasharray="8 6"/>'
                f'<text x="390" y="188" text-anchor="end" fill="{dn}" font-size="13" font-weight="700">DAILY FLOOR</text>')
    if k == "drawdown":
        return (f'<path d="M190 120 L225 90 L255 105 L290 70 L320 95 L355 60 L385 80" {s}/>'
                f'<line x1="190" y1="170" x2="390" y2="170" stroke="{c}" stroke-width="3"/>'
                f'<path d="M200 170v-8M240 170v-8M280 170v-8M320 170v-8M360 170v-8" stroke="{c}" stroke-width="2"/>')
    if k == "stop":
        return (f'<path d="M190 70 L240 95 L280 85 L320 120" {s}/>'
                f'<line x1="190" y1="150" x2="390" y2="150" stroke="{dn}" stroke-width="2.5"/>'
                f'<rect x="300" y="138" width="80" height="24" rx="5" fill="{dn}"/>'
                f'<text x="340" y="155" text-anchor="middle" fill="#fff" font-size="13" font-weight="700">SL</text>')
    if k == "rr":
        return (f'<rect x="230" y="50" width="120" height="70" rx="6" fill="{up}" fill-opacity=".22" stroke="{up}" stroke-width="2"/>'
                f'<rect x="230" y="120" width="120" height="35" rx="6" fill="{dn}" fill-opacity=".22" stroke="{dn}" stroke-width="2"/>'
                f'<text x="290" y="92" text-anchor="middle" fill="{up}" font-size="20" font-weight="700">2R</text>'
                f'<text x="290" y="143" text-anchor="middle" fill="{dn}" font-size="15" font-weight="700">1R</text>')
    if k == "plan":
        return (f'<rect x="225" y="45" width="130" height="150" rx="10" {s}/>'
                + "".join(f'<path d="M245 {y}l7 7 13-14" {s}/><line x1="280" y1="{y + 2}" x2="335" y2="{y + 2}" '
                          f'stroke="{c}" stroke-opacity=".5" stroke-width="3" stroke-linecap="round"/>' for y in (78, 113, 148)))
    if k == "count":
        return "".join(f'<rect x="{205 + i * 38}" y="95" width="28" height="60" rx="6" '
                       f'{soft if i < 3 else ""} stroke="{c}" stroke-opacity="{1 if i < 3 else .3}" stroke-width="2.5" fill-opacity="{.16 if i < 3 else 0}" fill="{c if i < 3 else "none"}"/>'
                       for i in range(5))
    if k == "news":
        return (f'<path d="M300 40 L255 120 H295 L270 195 L345 100 H305 L330 40 Z" fill="{c}" fill-opacity=".85"/>'
                f'<circle cx="300" cy="118" r="78" stroke="{c}" stroke-opacity=".3" fill="none" stroke-width="2"/>')
    if k == "gap":
        return (f'<path d="M190 130 L220 120 L250 125 L270 115" {s}/><path d="M320 75 L345 85 L370 70 L390 80" {s}/>'
                f'<rect x="282" y="50" width="26" height="140" rx="4" fill="{c}" fill-opacity=".12"/>'
                f'<path d="M270 115 L320 75" stroke="{c}" stroke-dasharray="5 6" stroke-width="2.5"/>'
                f'<text x="295" y="210" text-anchor="middle" fill="{c}" font-size="12" font-weight="700">SAT · SUN</text>')
    if k == "pause":
        return (f'<circle cx="290" cy="118" r="70" {s}/><rect x="265" y="88" width="16" height="60" rx="4" fill="{c}"/>'
                f'<rect x="299" y="88" width="16" height="60" rx="4" fill="{c}"/>')
    if k == "target":
        return "".join(f'<circle cx="290" cy="118" r="{r}" stroke="{c}" stroke-opacity="{o}" fill="none" stroke-width="3"/>'
                       for r, o in ((70, .35), (48, .6), (26, 1))) + f'<circle cx="290" cy="118" r="7" fill="{c}"/>' \
            f'<path d="M340 60 L298 110" {s}/><path d="M340 60l12-4-4 12" {s}/>'
    if k == "flame":
        return (f'<path d="M290 40 C320 80 345 100 340 140 C336 175 312 195 290 195 C262 195 240 175 240 145 '
                f'C240 120 255 105 265 90 C268 110 278 118 285 118 C280 92 282 66 290 40 Z" fill="{c}" fill-opacity=".85"/>')
    if k == "streak":
        return "".join(f'<rect x="{205 + i * 34}" y="{70 + i * 18}" width="22" height="{40 + (i % 2) * 10}" rx="4" fill="{dn}" '
                       f'fill-opacity="{.9 - i * .12}"/>' for i in range(5)) + \
            f'<path d="M200 70 C260 90 300 150 380 165" stroke="{c}" stroke-dasharray="5 6" fill="none" stroke-width="2.5"/>'
    if k == "flag":
        return (f'<path d="M250 195V45" {s}/><path d="M250 50h110l-20 30 20 30H250" fill="{c}" fill-opacity=".85"/>'
                f'<path d="M200 195h160" {s}/>')
    return ""


def cover_svg(motif: str, color: str, label: str = "") -> str:
    """Okładka 400×225: granat PTF, poświata w kolorze modułu, siatka, rysunek tematu."""
    g = f"g{abs(hash((motif, color))) % 10 ** 6}"
    lab = (f'<text x="22" y="40" fill="{color}" font-size="13" font-weight="700" letter-spacing="1.5" '
           f'font-family="Inter,system-ui,sans-serif">{label}</text>') if label else ""
    return (f'<svg viewBox="0 0 400 225" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" '
            f'font-family="Inter,system-ui,sans-serif">'
            f'<defs><linearGradient id="{g}b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1220"/>'
            f'<stop offset="1" stop-color="#152238"/></linearGradient>'
            f'<radialGradient id="{g}r" cx=".75" cy=".45" r=".6"><stop offset="0" stop-color="{color}" stop-opacity=".32"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>'
            f'<pattern id="{g}p" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" '
            f'stroke="#fff" stroke-opacity=".05"/></pattern></defs>'
            f'<rect width="400" height="225" fill="url(#{g}b)"/><rect width="400" height="225" fill="url(#{g}p)"/>'
            f'<rect width="400" height="225" fill="url(#{g}r)"/>{lab}{_motif(motif, color)}</svg>')
