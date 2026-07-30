"""Własna telemetria produktowa — wiersze w telemetry_events.

track() NIGDY nie rzuca i ma własną sesję: zdarzenie analityczne nie może
wywrócić requestu biznesowego (rejestracji, płatności), który je emituje.
Marketing/ruch na stronie publicznej łapie GA4 (base.html) — tu są wyłącznie
zdarzenia produktu, agregowane w adminie (GET /api/admin/telemetry).
"""
from __future__ import annotations

import json


def track(name: str, trader_id: int | None = None, **props) -> None:
    try:
        from .db import SessionLocal
        from .models import TelemetryEvent

        session = SessionLocal()
        try:
            session.add(TelemetryEvent(
                trader_id=trader_id, name=name[:40],
                props=(json.dumps(props)[:400] if props else None)))
            session.commit()
        finally:
            session.close()
    except Exception as e:  # pragma: no cover
        print(f"[telemetry] {name}: {e}")
