import asyncio
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug(f"Client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.debug(f"Client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast to all connected clients concurrently."""
        if not self.active_connections:
            return

        async def send_to_one(connection: WebSocket):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Error sending to ws client: {e}")
                self.disconnect(connection)

        # Use gather to send to everyone in parallel so one slow client
        # doesn't block the entire broadcast loop.
        await asyncio.gather(
            *[send_to_one(conn) for conn in self.active_connections],
            return_exceptions=True
        )

manager = ConnectionManager()
