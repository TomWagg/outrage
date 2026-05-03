"""Broadcast helpers for fanning events out to connected clients."""
from __future__ import annotations

import asyncio
from typing import Iterable

from pydantic import BaseModel

from .connection import Connection


async def broadcast(conns: Iterable[Connection], msg: BaseModel | dict) -> None:
    await asyncio.gather(*(c.send(msg) for c in conns), return_exceptions=True)


async def send_to(conn: Connection, msg: BaseModel | dict) -> None:
    await conn.send(msg)
