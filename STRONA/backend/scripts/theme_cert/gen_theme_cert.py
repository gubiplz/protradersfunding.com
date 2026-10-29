"""Generate the purple -> certificate-blue override block for the "Certificate" theme.

For every rule in the source stylesheets that uses a purple/indigo colour, emit the
same selector (inside the same @media) with only the affected declarations, colours
remapped. Loaded after the sources, equal specificity + later position wins, so
site.css / portal.css stay byte-identical.

usage: gen_theme_cert.py <backend dir> <out site css> <out portal css>
"""
import colorsys
import re
import sys
from pathlib import Path

HEX = {
    "6366f1": "1b6ef3", "8b5cf6": "1b4bd8", "7c3aed": "1b4bd8", "6d28d9": "1440b8",
    "a855f7": "67d2ff", "7d87f8": "67d2ff", "818cf8": "67d2ff", "a78bfa": "8fdcff",
    "a5b4fc": "a9e4ff", "c4b5fd": "bfeaff", "4f46e5": "1b6ef3", "4338ca": "1440b8",
    "3730a3": "10379e", "5b21b6": "10379e", "9333ea": "2b8bff",
    "eef0ff": "eaf4ff", "f1ecfe": "e8f6ff", "f6f0ff": "eef8ff", "dfe3ff": "d3e6ff", "e2d8fb": "d6ecfb",
}
RGB = {
    (99, 102, 241): (43, 139, 255), (139, 92, 246): (27, 75, 216), (124, 58, 237): (27, 75, 216),
    (168, 85, 247): (103, 210, 255), (125, 135, 248): (103, 210, 255), (79, 70, 229): (27, 110, 243),
    (167, 139, 250): (143, 220, 255), (109, 40, 217): (20, 64, 184),
}
HUE_BLUE = 212 / 360
# Tekst na granacie: głęboki błękit gaśnie, więc w ciemnym motywie kolory TEKSTU
# (i gradienty przycięte do tekstu) idą na cyjan certyfikatu.
DARK_TEXT_HEX = {"6366f1": "67d2ff", "4f46e5": "67d2ff", "8b5cf6": "2b8bff", "7c3aed": "2b8bff",
                 "6d28d9": "2b8bff", "a855f7": "a9e4ff", "4338ca": "67d2ff"}
TEXT_PROPS = ("color", "-webkit-text-fill-color", "fill", "stroke")
# Tekst na własnym białym tle (przyciski na pasku promo i banerze) — ten sam w obu
# motywach, cyjan byłby na nim nieczytelny.
DARK_TEXT_SKIP = re.compile(r"promo|gb-btn")


def dark_prefix(part):
    part = part.strip()
    if part.startswith("html"):
        return "html[data-theme=dark]" + part[4:]
    return "html[data-theme=dark] " + part


def purple(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return s > .25 and 245 / 360 <= h <= 290 / 360 and max(r, g, b) - min(r, g, b) > 25


def shift(r, g, b):
    _, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(HUE_BLUE, l, s))


def map_hex(m):
    h = m.group(1).lower()
    if len(h) == 3:
        h6 = "".join(c * 2 for c in h)
    else:
        h6 = h
    if h6 in HEX:
        return "#" + HEX[h6]
    r, g, b = (int(h6[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % shift(r, g, b) if purple(r, g, b) else m.group(0)


def map_rgb(m):
    fn, parts = m.group(1), [p.strip() for p in m.group(2).split(",")]
    try:
        r, g, b = (int(float(p)) for p in parts[:3])
    except ValueError:
        return m.group(0)
    if (r, g, b) in RGB:
        nr = RGB[(r, g, b)]
    elif purple(r, g, b):
        nr = shift(r, g, b)
    else:
        return m.group(0)
    return f"{fn}({','.join(map(str, nr))}{(',' + ','.join(parts[3:])) if parts[3:] else ''})"


HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB_RE = re.compile(r"\b(rgba?)\(([^()]*)\)")


def remap(value):
    return RGB_RE.sub(map_rgb, HEX_RE.sub(map_hex, value))


def split_decls(body):
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [d.strip() for d in out if d.strip()]


def walk(css):
    """Yield (media or None, selector, body) for plain rules, one level of @media/@supports."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n = 0, len(css)
    stack = []
    while i < n:
        j = css.find("{", i)
        k = css.find("}", i)
        if k != -1 and (j == -1 or k < j):
            if stack:
                stack.pop()
            i = k + 1
            continue
        if j == -1:
            break
        head = css[i:j].strip()
        if head.startswith("@"):
            stack.append(head)
            i = j + 1
            continue
        end = css.find("}", j)
        yield (stack[-1] if stack else None), head, css[j + 1:end]
        i = end + 1


def remap_dark_text(value):
    def hx(m):
        h = m.group(1).lower()
        return "#" + DARK_TEXT_HEX[h] if h in DARK_TEXT_HEX else map_hex(m)
    return RGB_RE.sub(map_rgb, HEX_RE.sub(hx, value))


def build(sources):
    blocks, dark = [], []
    for src in sources:
        css = src.read_text()
        count = 0
        for media, sel, body in walk(css):
            if media and media.startswith(("@keyframes", "@font-face")):
                continue
            changed, dark_changed = [], []
            clip_text = re.search(r"background-clip\s*:\s*text", body) is not None
            for d in split_decls(body):
                if ":" not in d:
                    continue
                prop, val = d.split(":", 1)
                prop = prop.strip()
                nv = remap(val)
                if nv != val:
                    changed.append(f"{prop}:{nv.strip()}")
                    if prop in TEXT_PROPS or (clip_text and prop.startswith("background")):
                        dv = remap_dark_text(val)
                        if dv != nv:
                            dark_changed.append(f"{prop}:{dv.strip()}")
            if changed:
                rule = f"{sel}{{{';'.join(changed)}}}"
                blocks.append(f"{media}{{{rule}}}" if media else rule)
                count += 1
            if dark_changed and "data-theme" not in sel and not DARK_TEXT_SKIP.search(sel):
                dsel = ",".join(dark_prefix(s) for s in sel.split(","))
                rule = f"{dsel}{{{';'.join(dark_changed)}}}"
                dark.append(f"{media}{{{rule}}}" if media else rule)
        blocks.insert(len(blocks) - count, f"/* z {src.name}: {count} reguł */")
    if dark:
        blocks.append(f"/* ciemny motyw: tekst w cyjanie certyfikatu ({len(dark)} reguł) */")
        blocks.extend(dark)
    return "\n".join(blocks) + "\n"


def main():
    root, out_site, out_portal = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    css = root / "static" / "css"
    site = build([css / f for f in ("site.css", "site-motion.css", "site-theme.css", "site-glass.css")])
    portal = build([css / f for f in ("portal.css", "portal-glass.css", "notifications.css", "apple.css")])
    out_site.write_text(site)
    out_portal.write_text(portal)
    print(f"site: {site.count('{')} bloków, {len(site)} B; portal: {portal.count('{')} bloków, {len(portal)} B")


if __name__ == "__main__":
    main()
