"""
displ_api.py — Stub para desarrollo.
Reemplázalo por tu wrapper real que expone: get_devices(only_ids: list[int]|None) -> dict
Estructura esperada: {"payload": [ { "id": 123, "name": "...", "location": "...",
                                     "status": "online/offline", "last_seen": "2025-09-01T12:34:56Z", ... }, ... ]}
"""

import os
from datetime import datetime, timezone, timedelta

def get_devices(only_ids=None):
    """
    MODO DEMO: genera 3 dispositivos. Sirve para validar que el monitor y el correo funcionen.
    Reemplaza esta función por tu llamada real a la API de DISPL/Displayforce.
    """
    now = datetime.now(timezone.utc)
    demo = [
        {"id": 13900, "name": "Totem Lobby", "location": "Hotel A", "status": "online",
         "last_seen": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"id": 12757, "name": "Kiosko Bar", "location": "Hotel B", "status": "online",
         "last_seen": (now - timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ")},  # caerá OFFLINE si umbral<25
        {"id": 13902, "name": "Pantalla Recepción", "location": "Hotel C", "status": "offline",
         "last_seen": (now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")},
    ]
    if only_ids:
        demo = [d for d in demo if d["id"] in set(only_ids)]
    return {"payload": demo}
