#!/bin/bash
# Skleja paletę „złoto + teal": nagłówek + blok wygenerowany (błękit certyfikatu -> teal)
# + tokeny wspólne + część pliku. Ładowana PO theme-cert-*.css.
# usage: scripts/theme_cert/build_theme_gold.sh   (z katalogu STRONA/backend)
set -e
S=scripts/theme_cert
CSS=static/css
TMP=$(mktemp -d)
python3 -I "$S/gen_theme_gold.py" . "$TMP/site.css" "$TMP/portal.css" "$TMP/admin.css"
naglowek() {
  printf '/* ============================================================\n'
  printf '   Paleta „złoto + teal" (%s): złoto #C6A15B = marka, teal = interfejs,\n' "$1"
  printf '   tła deep navy #0B1220 / off-white #F4F6F8. Tylko wygląd.\n'
  printf '   Ładowana jako OSTATNI arkusz (po theme-cert-*): ta sama specyficzność\n'
  printf '   + później = wygrywa. Zdjęcie palety = usunięcie linku.\n'
  printf '   Blok „wygenerowany": scripts/theme_cert/gen_theme_gold.py.\n'
  printf '   ============================================================ */\n\n'
}
for part in site portal; do
  { naglowek "$part"; cat "$TMP/$part.css"; printf '\n'; cat "$S/gold-common.css" "$S/gold-$part.css"; } > "$CSS/theme-gold-$part.css"
  wc -c "$CSS/theme-gold-$part.css"
done
{ naglowek "panel admina, po theme-gold-portal.css"; cat "$TMP/admin.css"; } > "$CSS/theme-gold-admin.css"
wc -c "$CSS/theme-gold-admin.css"
rm -rf "$TMP"
