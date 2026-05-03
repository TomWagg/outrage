"""Per-WebSocket connection wrapper with send queue and username binding."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import WebSocket
from pydantic import BaseModel

log = logging.getLogger(__name__)


class Connection:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.username: Optional[str] = None
        self._send_lock = asyncio.Lock()

    async def send(self, msg: BaseModel | dict[str, Any]) -> None:
        data = msg.model_dump() if isinstance(msg, BaseModel) else msg
        async with self._send_lock:
            try:
                await self.ws.send_json(data)
            except Exception as exc:  # noqa: BLE001
                log.warning("send failed to %s: %s", self.username, exc)

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
