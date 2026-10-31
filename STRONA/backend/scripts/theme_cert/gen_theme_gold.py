"""Generate the certificate-blue -> teal override block for the gold + teal palette.

Brand palette (owner's choice, 2026-10-31): champagne gold #C6A15B is the brand
colour (logo, eyebrows, headline highlight), teal is the interface colour
(buttons, links, focus, active states), deep navy #0B1220 / off-white #F4F6F8
are the backgrounds. Per theme: teal #087F73 on light and #36B9A9 on dark,
gold #A17B39 on light and #C6A15B on dark.

Same technique as gen_theme_cert.py: for every rule in the stylesheets loaded
before this layer that uses a certificate blue (or a purple the certificate
layer left behind), emit the same selector with only those declarations
recoloured. Loaded last, so equal specificity + later position wins and no
source stylesheet changes. Market category colours (.asset, data-c) keep
their own blue on purpose.

usage: gen_theme_gold.py <backend dir> <out site css> <out portal css> <out admin css>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_theme_cert import HEX_RE, RGB_RE, split_decls, walk  # noqa: E402

HEX = {
    # akcent (przyciski, linki, aktywne)
    "1b6ef3": "0a8a7d", "1b4bd8": "07695f", "1440b8": "065a51", "10379e": "054c45",
    "2b8bff": "159e91", "2563eb": "0f8a7e", "67d2ff": "36b9a9", "8fdcff": "66cbbe",
    "a9e4ff": "8ad8ce", "38bdf8": "2fb3a4", "0284c7": "0a7d72", "6ab3f3": "4fbcae",
    "5ca4f6": "3fae9f", "6a8cff": "3fb0a2",
    # resztki fioletu marki, których nie przemapował motyw certyfikatu
    "6366f1": "0f8a7e", "7d87f8": "36b9a9", "a5b4fc": "9ad9d0",
    # jasne tinty tła
    "dfe3ff": "d5eeea", "d3e6ff": "c9ebe5", "eaf4ff": "e7f5f3", "e8f4fd": "e6f4f2",
    "cfe8f8": "cbe9e4", "e8f6ff": "e6f6f3", "d6ecfb": "cdece7", "e8edff": "e7efee",
    "f5f8fd": "f4f6f8", "f7faff": "f7f9fa", "f1f6ff": "f1f4f6",
    # granat ciemnego motywu -> deep navy #0B1220
    "0b142c": "111a2e", "070d1e": "0d1525", "050914": "0b1220", "101b38": "16213a",
    "0e1934": "15203a", "0c1834": "13203a", "0a1230": "0f182c", "0d1a3a": "141f36",
    "0f2a5c": "123b4a",
}
RGB = {
    (43, 139, 255): (21, 158, 145), (103, 210, 255): (54, 185, 169),
    (27, 75, 216): (7, 105, 95), (27, 110, 243): (10, 138, 125), (20, 64, 184): (6, 90, 81),
    (120, 170, 255): (130, 176, 190), (180, 215, 255): (190, 226, 224),
    (150, 200, 255): (150, 210, 204), (140, 180, 255): (140, 190, 196),
    (143, 220, 255): (102, 203, 190), (99, 102, 241): (15, 138, 126),
    (125, 135, 248): (54, 185, 169), (14, 165, 233): (20, 160, 148),
    (56, 189, 248): (47, 179, 164), (232, 237, 255): (231, 239, 238), (8, 14, 32): (11, 18, 32),
}
# Kolory kategorii rynków i wykresów mają zostać swoje.
POMIN = re.compile(r"\.asset|data-c|\.ic\b|\.mk-|candle|\.chart-")


def map_hex(m):
    h = m.group(1).lower()
    h6 = "".join(c * 2 for c in h) if len(h) == 3 else h
    return "#" + HEX[h6] if h6 in HEX else m.group(0)


def map_rgb(m):
    fn, parts = m.group(1), [p.strip() for p in m.group(2).split(",")]
    try:
        r, g, b = (int(float(p)) for p in parts[:3])
    except ValueError:
        return m.group(0)
    if (r, g, b) not in RGB:
        return m.group(0)
    nr = RGB[(r, g, b)]
    return f"{fn}({','.join(map(str, nr))}{(',' + ','.join(parts[3:])) if parts[3:] else ''})"


def remap(value):
    return RGB_RE.sub(map_rgb, HEX_RE.sub(map_hex, value))


def build(sources):
    blocks = []
    for src in sources:
        count = 0
        for media, sel, body in walk(src.read_text()):
            if media and media.startswith(("@keyframes", "@font-face")):
                continue
            if POMIN.search(sel):
                continue
            changed = []
            for d in split_decls(body):
                if ":" not in d:
                    continue
                prop, val = d.split(":", 1)
                nv = remap(val)
                if nv != val:
                    changed.append(f"{prop.strip()}:{nv.strip()}")
            if changed:
                rule = f"{sel}{{{';'.join(changed)}}}"
                blocks.append(f"{media}{{{rule}}}" if media else rule)
                count += 1
        blocks.insert(len(blocks) - count, f"/* z {src.name}: {count} reguł */")
    return "\n".join(blocks) + "\n"


def restores(admin_sources, portal_sources):
    """Gdzie admin.css ustawia dla tego samego klucza kolor NIE-niebieski, a blok
    portalu (ładowany wcześniej) by go przemalował — oddajemy wartość admina."""
    keys = set()
    for src in portal_sources:
        for media, sel, body in walk(src.read_text()):
            for d in split_decls(body):
                if ":" in d and remap(d.split(":", 1)[1]) != d.split(":", 1)[1]:
                    keys.add((media, sel.strip(), d.split(":", 1)[0].strip()))
    out = []
    for src in admin_sources:
        for media, sel, body in walk(src.read_text()):
            decls = [f"{p.strip()}:{v.strip()}" for p, v in
                     (d.split(":", 1) for d in split_decls(body) if ":" in d)
                     if remap(v) == v and (media, sel.strip(), p.strip()) in keys]
            if decls:
                rule = f"{sel}{{{';'.join(decls)}}}"
                out.append(f"{media}{{{rule}}}" if media else rule)
    return (f"/* admin.css ma tu własny kolor: {len(out)} reguł */\n" + "\n".join(out) + "\n") if out else ""


def main():
    root = Path(sys.argv[1])
    css = root / "static" / "css"
    site = build([css / f for f in ("site.css", "site-motion.css", "site-theme.css", "site-glass.css",
                                    "theme-cert-site.css", "helpdesk.css")])
    portal_src = [css / f for f in ("portal.css", "portal-glass.css", "notifications.css", "apple.css",
                                    "helpdesk.css", "theme-cert-portal.css")]
    portal = build(portal_src)
    admin_src = [css / f for f in ("admin.css", "theme-cert-admin.css")]
    admin = build(admin_src) + restores(admin_src, portal_src)
    for out, txt in zip(sys.argv[2:5], (site, portal, admin)):
        Path(out).write_text(txt)
        print(f"{Path(out).name}: {txt.count('{')} bloków, {len(txt)} B")


if __name__ == "__main__":
    main()
