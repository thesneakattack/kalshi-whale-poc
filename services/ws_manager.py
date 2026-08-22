"""
The dashboard's own push-update websocket connection registry
(GET /api/ws in main.py). Extracted 2026-08-21 as part of main.py's
modularization pass - this has zero dependency on services.app_state or
anything else in the app, so it's the simplest possible extraction: a
self-contained utility, not a per-concern module.
"""
import asyncio
import json

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message)
        async with self.lock:
            connections = list(self.active_connections)
        for connection in connections:
            try:
                await connection.send_text(text)
            except Exception:
                await self.disconnect(connection)


ws_manager = WebSocketManager()
