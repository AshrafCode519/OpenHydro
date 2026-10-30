"""
OpenHydro WebSocket Telemetry Hub
Broadcasts real-time download events, dynamic chunks, and network speeds to all connected clients.
"""

import json
from typing import List
from fastapi import WebSocket


class TelemetryHub:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return

        payload = json.dumps(message)
        dead_connections = []

        # Iterate over a shallow copy to prevent concurrency errors
        for conn in list(self.active_connections):
            try:
                await conn.send_text(payload)
            except Exception:
                dead_connections.append(conn)

        for dead in dead_connections:
            self.disconnect(dead)
