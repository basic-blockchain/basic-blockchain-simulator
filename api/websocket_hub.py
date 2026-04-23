from __future__ import annotations

import asyncio
import json
from typing import Set

from quart import websocket as quart_ws


class WebSocketHub:
    """Manages active WebSocket connections and broadcasts events to all clients."""

    def __init__(self) -> None:
        self._clients: Set[asyncio.Queue] = set()

    def _make_queue(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._clients.add(q)
        return q

    def _remove_queue(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    async def serve(self, send_fn=None) -> None:
        if send_fn is None:
            send_fn = quart_ws.send
        q = self._make_queue()
        try:
            while True:
                message = await q.get()
                await send_fn(message)
        except asyncio.CancelledError:
            pass
        finally:
            self._remove_queue(q)

    def broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        for q in list(self._clients):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    @property
    def connection_count(self) -> int:
        return len(self._clients)
