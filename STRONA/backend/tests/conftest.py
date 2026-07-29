"""Izolacja testów od lokalnego `backend/.env`.

`config.py` woła `load_dotenv()`, więc bez tego pliku testy dziedziczyłyby
deweloperskie ustawienia — m.in. `METAQUOTES_WEB_ENABLED=true`, przez co
provisioning w testach startowałby prawdziwą przeglądarkę i strzelał do
MetaQuotes (7 testów padało po ~100 s zamiast 3 s).

conftest.py wykonuje się PRZED modułami testowymi, więc twarde przypisania
poniżej wygrywają z `.env` (load_dotenv nie nadpisuje istniejących zmiennych).
"""
import os

# Kanały provisioningu MUSZĄ być wyłączone — żaden test nie rusza sieci.
os.environ["METAQUOTES_WEB_ENABLED"] = "false"
os.environ["METAAPI_AUTO_CREATE"] = "false"
os.environ["METAAPI_TOKEN"] = ""
os.environ["METAAPI_PROVISIONING_PROFILE_ID"] = ""
os.environ["STRIPE_SECRET_KEY"] = ""     # checkout w trybie MOCK
os.environ["SMTP_HOST"] = ""             # maile na konsolę, bez wysyłki
os.environ["NOTIFY_WEBHOOK_URL"] = ""
# Poller nie moze chodzic w tle testow — przestawialby salda kont.
os.environ["POLLER_ENABLED"] = "false"

# Sekret crona musi byc znany JUZ TERAZ: `get_settings()` jest cache'owane, wiec
# ustawienie go dopiero w module testowym trafia w gotowy obiekt ustawien i
# `/api/tick` odbijalby crona 401-ka przy pelnym przebiegu.
os.environ.setdefault("CRON_SECRET", "sekret-crona")

# Feed i seed: poszczególne pliki testów mogą nadpisać własnym setdefault-em
# tylko wtedy, gdy ustawimy to przez setdefault (nie twardo).
os.environ.setdefault("FEED", "sim")
os.environ.setdefault("AUTO_SEED", "false")
