"""Pydantic schemas for WebSocket messages.

All messages are JSON objects with a "type" discriminator.

Client → server: {"type": "intent", "name": ..., "payload": {...}, "request_id": "..."}
Server → client (addressed): {"type": "ack"|"error", "request_id": ..., ...}
Server → all (broadcast):    {"type": "event", "name": ..., "payload": {...}}
Server → client (snapshot):  {"type": "snapshot", "state": <redacted game state>}
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# -------- Client → Server --------


class Intent(BaseModel):
    type: Literal["intent"] = "intent"
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None


# -------- Server → Client --------


class Ack(BaseModel):
    type: Literal["ack"] = "ack"
    request_id: Optional[str] = None
    detail: Optional[str] = None


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    request_id: Optional[str] = None
    code: str
    message: str


class Event(BaseModel):
    type: Literal["event"] = "event"
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Snapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    state: dict[str, Any]
