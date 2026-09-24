"""Strażniki „raz na okres" w `app_settings`, bezpieczne przy wielu instancjach.

Na serverless ten sam request (np. `/api/public/stats` z landingu) obsługują
równolegle różne instancje. Zwykłe „przeczytaj znacznik → porównaj → zapisz"
przepuszczało wtedy obie: dwa razy te same przypomnienia o leadach, dwa
dzienne podsumowania na telefon, dwa posty na kanale.

Sedno to warunkowy UPDATE `WHERE value = <to, co przeczytałem>` — baza wykonuje
go atomowo, więc z dwóch instancji dokładnie jedna dostaje rowcount 1.
Ten sam wzorzec co `poller._zajmij_tick_ryzyka` i `reach._pora_skanu`.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .models import AppSetting


def zajmij_ustawienie(session, klucz: str, nowa: str,
                      wolno: Callable[[str | None], bool]) -> bool:
    """True = ten proces wygrał i ma zrobić robotę (znacznik już zapisany).

    `wolno(stara_wartosc)` decyduje, czy w ogóle pora (np. minęło N minut,
    inny dzień). Commituje — wołać na świeżej sesji, przed właściwą pracą.
    """
    row = session.get(AppSetting, klucz)
    stara = row.value if row is not None else None
    if not wolno(stara):
        return False
    if row is None:
        try:
            session.add(AppSetting(key=klucz, value=nowa))
            session.commit()
            return True
        except IntegrityError:          # inna instancja właśnie założyła wiersz
            session.rollback()
            return False
    warunek = AppSetting.value.is_(None) if stara is None else AppSetting.value == stara
    wynik = session.execute(update(AppSetting)
                            .where(AppSetting.key == klucz, warunek)
                            .values(value=nowa))
    session.commit()
    return wynik.rowcount == 1
