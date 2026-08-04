"""Zrzut certyfikatu do obrazka — przez zewnętrzną przeglądarkę, po HTTP.

DLACZEGO TAK
------------
Serwer nie potrafi zrobić rastra certyfikatu i nie jest to niedopatrzenie:
w portalu JPG powstaje w przeglądarce klienta (`static/js/cert-jpg.js`:
SVG foreignObject -> canvas), a jedyny renderer HTML->PNG w repo
(`scripts/gen_og.py`) to Playwright z Chromium (~150 MB), którego nie da się
wgrać do funkcji bezserwerowej.

Odrysowanie certyfikatu w Pythonie (Pillow) odpadło świadomie. Nagłówek
`cert-jpg.js` jest zapisem tego, jak to się kończy: reimplementacja CSS gubi
`line-height`, grafikę z `::before/::after` i `mix-blend-mode`. Powstałaby też
CZWARTA ręcznie utrzymywana kopia tego samego designu (są już trzy:
`certificate.html`, `verify.html`, `site.js:certsStrip`) — i pierwsza zmiana
w `cert.css` rozjechałaby grafikę z dokumentem.

Zamiast tego zrzucamy TĘ SAMĄ stronę, którą widzi klient: `/payout/{token}?bare=1`.
To prawdziwy Chrome renderujący prawdziwy dokument, więc zgodność jest 1:1
z definicji, a nie z troski. `?bare=1` zdejmuje przyciski i stopkę, przez co
zwykły zrzut całej strony JEST już gotową grafiką — nie trzeba liczyć na to, że
dana usługa umie kadrować po selektorze CSS.

KONFIGURACJA
------------
`SHOT_API_URL` decyduje o trybie:

* zawiera `{url}`  -> GET z podstawionym, zakodowanym adresem. Tak działają
  usługi hostowane, np.
  `https://api.screenshotone.com/take?access_key=XXX&format=png&url={url}`
* nie zawiera     -> POST z JSON-em w formacie `browserless/chrome`, np.
  `https://przegladarka.twoj-vps.pl/screenshot?token=XXX`

`SHOT_API_TOKEN` (opcjonalny) leci jako `Authorization: Bearer`. Usługi, które
chcą klucza w adresie, mają go wpisany wprost w `SHOT_API_URL`.

Przy jednym obrazku dziennie (~30/miesiąc) darmowy próg wystarcza wszędzie.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .config import get_settings

settings = get_settings()

# Karta ma `width:min(620px,100%)`, a `.cert-bare` dokłada 20 px marginesu
# z każdej strony — 660 px to dokładnie kadr ze zrzutu, bez pasków po bokach.
SZEROKOSC = 660
SKALA = 2                  # jak `SCALE` w cert-jpg.js — obrazek ma być ostry
TIMEOUT_SEK = 45           # zimny start przeglądarki potrafi trwać

# Sygnatury formatów, które Telegram przyjmie jako zdjęcie. Sprawdzamy je, bo
# usługi przy błędzie zwracają JSON-a ze statusem 200 — bez tego poleciałby on
# na kanał jako „grafika" i post byłby pusty.
_MAGIA = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")

# Kontrakt transportu (ten sam kształt co w telegram.py):
#     transport(url: str, body: bytes | None, content_type: str | None)
#         -> tuple[int, bytes]


def is_enabled() -> bool:
    if os.getenv("SHOT_ENABLED", "true").lower() != "true":
        return False
    return bool(settings.shot_api_url)


def _urllib_transport(url: str, body: bytes | None,
                      content_type: str | None) -> tuple[int, bytes]:
    naglowki = {}
    if content_type:
        naglowki["Content-Type"] = content_type
    if settings.shot_api_token:
        naglowki["Authorization"] = f"Bearer {settings.shot_api_token}"
    req = urllib.request.Request(url, data=body, headers=naglowki,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEK) as odp:
            return odp.status, odp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def przytnij_do_kwadratu(png: bytes) -> bytes:
    """Ucina z PNG wszystko poniżej kwadratu. Zwraca oryginał, gdy się nie da.

    Usługi zrzutu mają różne okna, a przy `full_page` wysokość obrazka to WIĘKSZA
    z dwóch: treści i okna. Przy oknie pionowym pod certyfikatem zostawało czarne
    pole. Nie da się temu zapobiec parametrami — każdy dostawca nazywa je inaczej
    i część je ignoruje — więc przycinamy u siebie i przestajemy od nich zależeć.

    Karta ZAWSZE wypełnia szerokość (skaluje ją `zoom` w `certificate.html`)
    i jest kwadratowa, więc nadmiar może być wyłącznie na dole. To upraszcza
    rzecz zasadniczo: wystarczy zostawić pierwsze `szerokość` wierszy obrazu.

    Dzięki temu nie dekodujemy pikseli. Filtry PNG odwołują się do wiersza
    POPRZEDNIEGO, więc obcięcie od dołu nie psuje tych, które zostają — bajty
    zachowanych wierszy przechodzą bez zmiany.
    """
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        return png                      # JPEG albo coś innego — nie ruszamy
    try:
        kanaly = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
        pozycja, ihdr, idat = 8, None, bytearray()
        while pozycja < len(png):
            dlugosc = int.from_bytes(png[pozycja:pozycja + 4], "big")
            typ = png[pozycja + 4:pozycja + 8]
            dane = png[pozycja + 8:pozycja + 8 + dlugosc]
            if typ == b"IHDR":
                ihdr = dane
            elif typ == b"IDAT":
                idat += dane
            elif typ == b"IEND":
                break
            pozycja += 12 + dlugosc     # dlugosc + typ + dane + CRC

        if ihdr is None or len(ihdr) < 13:
            return png
        szer = int.from_bytes(ihdr[0:4], "big")
        wys = int.from_bytes(ihdr[4:8], "big")
        glebia, typ_koloru, interlace = ihdr[8], ihdr[9], ihdr[12]
        if wys <= szer or interlace != 0 or typ_koloru not in kanaly:
            return png                  # już kwadrat albo format nie do cięcia

        bity = glebia * kanaly[typ_koloru]
        bajty_wiersza = 1 + (szer * bity + 7) // 8
        surowe = zlib.decompress(bytes(idat))
        if len(surowe) < bajty_wiersza * wys:
            return png
        obciete = surowe[:bajty_wiersza * szer]

        nowy_ihdr = ihdr[0:4] + szer.to_bytes(4, "big") + ihdr[8:]
        return (png[:8]
                + _chunk(b"IHDR", nowy_ihdr)
                + _chunk(b"IDAT", zlib.compress(obciete, 6))
                + _chunk(b"IEND", b""))
    except Exception as e:
        print(f"[certshot] nie udało się przyciąć obrazka: {e}")
        return png


def _chunk(typ: bytes, dane: bytes) -> bytes:
    return (len(dane).to_bytes(4, "big") + typ + dane
            + zlib.crc32(typ + dane).to_bytes(4, "big"))


def _zadanie(cel: str) -> tuple[str, bytes | None, str | None]:
    """Buduje żądanie zgodnie z trybem wynikającym z `SHOT_API_URL`."""
    wzor = settings.shot_api_url
    if "{url}" in wzor:
        return wzor.replace("{url}", urllib.parse.quote(cel, safe="")), None, None
    cialo = json.dumps({
        "url": cel,
        # fullPage, bo `?bare=1` przycina stronę dokładnie do wysokości karty.
        "options": {"type": "png", "fullPage": True},
        "viewport": {"width": SZEROKOSC, "height": SZEROKOSC,
                     "deviceScaleFactor": SKALA},
        "gotoOptions": {"waitUntil": "networkidle0"},
    }).encode()
    return wzor, cialo, "application/json"


def render(cel_url: str, *, transport=None) -> bytes | None:
    """Bajty obrazka certyfikatu albo `None`. NIGDY nie rzuca.

    Wywołujący ma zadziałać mimo braku obrazka (patrz `telegram.send_message`) —
    wypłata i jej publiczny certyfikat już istnieją, więc awaria renderu nie może
    ani cofnąć rekordu, ani wywrócić przebiegu crona.
    """
    if not is_enabled():
        print("[certshot] pominięto: brak SHOT_API_URL")
        return None
    url, cialo, content_type = _zadanie(cel_url)
    try:
        status, dane = (transport or _urllib_transport)(url, cialo, content_type)
    except Exception as e:  # pragma: no cover - sieć
        print(f"[certshot] błąd sieci: {e}")
        return None
    if status != 200:
        print(f"[certshot] usługa zrzutu odpowiedziała {status}: "
              f"{(dane or b'')[:200].decode('utf-8', 'replace')}")
        return None
    if not dane or not dane.startswith(_MAGIA):
        # Najczęstszy przypadek: usługa zwróciła JSON-a z opisem błędu ze statusem 200.
        print(f"[certshot] odpowiedź nie jest obrazkiem: "
              f"{(dane or b'')[:200].decode('utf-8', 'replace')}")
        return None
    # Nadmiar pod certyfikatem obcinamy SAMI — okno usługi bywa pionowe, a jej
    # parametry nie sa wspolne dla dostawcow. Patrz `przytnij_do_kwadratu`.
    return przytnij_do_kwadratu(dane)
