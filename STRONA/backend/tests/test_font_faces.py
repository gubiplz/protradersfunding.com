"""Czcionki strony: wszystkie @font-face jednej rodziny muszą mieć ten sam zakres font-weight.

Chrome grupuje twarze rodziny po możliwościach (waga, szerokość, nachylenie) i do tekstu bierze JEDNĄ
grupę — twarz arabska z wagą 100 900 przy JetBrains Mono 100 800 zabrała całą rodzinę: ceny w /trade
rysowały się czcionką systemową (2026-11-26). Każdy plik podzbioru musi też istnieć."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FACE = re.compile(r"@font-face\{font-family:'([^']+)';src:url\('([^']+)'\)[^}]*font-weight:([^;}]+)")


def test_jedna_waga_na_rodzine_i_pliki_istnieja():
    for css in ("site.css", "portal.css", "terminal.css"):
        wagi: dict[str, set[str]] = {}
        for rodzina, url, waga in FACE.findall((ROOT / "static/css" / css).read_text(encoding="utf-8")):
            wagi.setdefault(rodzina, set()).add(waga.strip())
            assert (ROOT / url.lstrip("/")).is_file(), f"{css}: brak pliku {url}"
        assert {"Inter", "JetBrains Mono", "Space Grotesk"} <= set(wagi), css
        for rodzina, zbior in wagi.items():
            assert len(zbior) == 1, f"{css}: {rodzina} ma różne zakresy font-weight {zbior}"
