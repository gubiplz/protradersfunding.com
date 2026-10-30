# Rendery do sekcji „Three steps to a funded account"

Landing (`templates/home.html`, sekcja `#how`) pokazuje obraz na górze każdej karty,
gdy plik istnieje; bez pliku karta używa dotychczasowej ikony SVG. Pliki:

| Plik | Krok | Co ma być na obrazku |
|---|---|---|
| `step-1.webp` | Choose your challenge | wachlarz trzech metalowych kart (granat, srebro, złoto), jedna ze świecącą błękitną krawędzią |
| `step-2.webp` | Get MT5 credentials in minutes | ciemny szklany tablet z abstrakcyjnym błękitnym wykresem świecowym, obok mały złoty klucz |
| `step-3.webp` | Hit your target, get funded | granatowe drzwi sejfu uchylone, wysypujące się złote monety, błękitna poświata ze środka |
| `step-4.webp` | Start earning | granatowy smartfon pod kątem z błękitnym wykresem zysku na ekranie, wokół unoszące się złote monety |

Format: **webp, 1600×1200 (4:3), ≤ 120 kB**, obiekt na środku z zapasem miejsca, **czyste czarne tło**
(karta ma ciemne tło, więc czarne tło obrazka zlewa się z nią). Bez tekstu, logo i ludzi.

## Prompt do Gemini (Nano Banana) — wspólny styl

> Premium 3D product render, studio lighting, pure black background (#000000), single object centered with
> generous margin, cool dark-navy and electric-blue accents (#2b8bff, #67d2ff) with a touch of brushed gold
> (#e8c47c), subtle glass and metal materials, soft rim light, shallow depth of field, photoreal, 4:3 aspect
> ratio, no text, no logos, no people, no watermark.

Dopisz do niego obiekt:

1. `step-1.webp`: *A fan of three sleek credit-card-sized metal plates floating mid-air at a slight angle:
   one dark navy, one brushed silver, one brushed gold; the navy card has a thin glowing electric-blue edge.*
2. `step-2.webp`: *A floating dark glass tablet, slightly tilted, showing an abstract glowing blue candlestick
   chart line; a small brushed-gold key rests beside it; soft light reflections on the glass.*
3. `step-3.webp`: *A round dark-navy bank vault door, half open, with gold coins spilling out of it onto the
   floor; electric-blue glow coming from inside the vault.*
4. `step-4.webp`: *A sleek dark-navy smartphone floating at a slight angle, its screen showing a glowing
   electric-blue upward profit chart; a few brushed-gold coins drifting in the air around it.*

Po wygenerowaniu: przytnij do 4:3, zapisz jako webp (np. `cwebp -q 82 in.png -o step-1.webp`) i wgraj
do tego folderu. Nic więcej nie trzeba zmieniać.
