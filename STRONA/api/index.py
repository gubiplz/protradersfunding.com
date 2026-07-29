"""Wejście aplikacji dla hostingu bezserwerowego (Vercel, runtime Pythona).

Vercel szuka w katalogu `api/` modułu, który eksportuje aplikację ASGI jako
`app`. Cała aplikacja mieszka w `backend/app/main.py` — tutaj tylko dokładamy
`backend/` do `sys.path` i re-eksportujemy `app`, żeby nie było dwóch kopii
kodu, które mogłyby się rozjechać.

WAŻNE — czego tu NIE ma:

* **Pollera.** Na serverless proces nie żyje między requestami, więc pętla
  silnika ryzyka nie ma się na czym kręcić. Ustaw `POLLER_ENABLED=false`
  i napędzaj silnik cronem uderzającym w `/api/tick` (wpis jest już
  w `vercel.json`). Bez tego dashboard zamarza: Trade BOT chodzi w tej samej
  pętli, więc stoją salda, Phase Progress i krzywa equity.
* **Provisioningu realnych kont MT5.** Wymaga Playwrighta z Chromium (~150 MB),
  czego na Vercela się nie wgra. Trzymaj `MT5_PROVISIONING=false` — konta
  dostają wtedy poświadczenia generowane lokalnie, prosto w requeście zakupu.

Do realnych kont MT5 potrzebny jest zwykły host z procesem w tle (VPS, Railway,
Render). Ten plik jest po to, żeby dashboard dało się pokazać na domenie.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402,F401  (re-eksport dla runtime'u Vercela)
