"""End-to-end /ws integration tests using FastAPI's TestClient.

Covers:
  - Lobby: join, set_mode, chat
  - start_game builds a fresh GameState from the lobby and deals hands
  - A game intent (roll_dice) is routed through rules.apply and broadcast
  - Per-player snapshot redaction hides opponents' hands
  - Unknown intents return an error
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_and_saves(tmp_path, monkeypatch):
    """Point the app at a temporary saves dir and a fresh module import."""
    # Copy-safe: point SAVES_DIR at tmp_path before importing server modules.
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    # Reset the module state by re-importing after redirecting save files.
    import server.server_state as ss

    tmp_saves = tmp_path / "saves"
    tmp_saves.mkdir()
    monkeypatch.setattr(ss, "SAVES_DIR", tmp_saves)
    monkeypatch.setattr(ss, "GAME_FILE", tmp_saves / "current_game.json")
    monkeypatch.setattr(ss, "STATS_FILE", tmp_saves / "stats.json")

    # Need to also reset the singleton.
    ss.set_app(None) if False else None  # intentionally skip — use fresh AppState
    ss.APP = None

    # Re-import main so it picks up the patched module-level constants used
    # at *runtime* (they are only used in load_game / save_game which is
    # called from build_app_state + AppState.persist, both of which read
    # GAME_FILE at call time from the server_state module).

    from server.main import app
    # Reset APP, then lifespan will rebuild fresh.
    yield app, tmp_saves


def _json_loop(ws):
    """Drain whatever messages are currently queued on the socket."""
    out = []
    while True:
        try:
            msg = ws.receive_json()
        except Exception:
            break
        out.append(msg)
        # The TestClient websocket has no peek; we bail when we've seen the
        # obvious terminator events in the tests that call this.
        if len(out) > 50:
            break
    return out


def _drain(ws, n):
    """Receive exactly n json messages."""
    return [ws.receive_json() for _ in range(n)]


def test_lobby_join_and_set_mode(app_and_saves):
    app, saves = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a:
            ws_a.send_json({"type": "intent", "name": "join", "payload": {"username": "alice"}, "request_id": "r1"})
            msgs = _drain(ws_a, 3)  # ack, snapshot, lobby_updated event
            assert msgs[0]["type"] == "ack"
            assert msgs[1]["type"] == "snapshot"
            assert msgs[1]["state"]["you"] == "alice"
            assert msgs[1]["state"]["phase"] == "LOBBY"
            assert msgs[2]["type"] == "event" and msgs[2]["name"] == "lobby_updated"

            ws_a.send_json({"type": "intent", "name": "set_mode", "payload": {"mode": "slow"}, "request_id": "r2"})
            msgs = _drain(ws_a, 2)
            assert msgs[0]["type"] == "ack"
            assert msgs[1]["name"] == "lobby_updated"
            lobby = msgs[1]["payload"]["lobby"]
            assert lobby["mode"] == "slow"


def test_chat_fanout(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            ws_a.send_json({"type": "intent", "name": "join", "payload": {"username": "alice"}})
            _drain(ws_a, 3)
            ws_b.send_json({"type": "intent", "name": "join", "payload": {"username": "bob"}})
            _drain(ws_b, 3)
            _drain(ws_a, 1)  # alice sees bob's lobby_updated broadcast

            ws_a.send_json({"type": "intent", "name": "chat", "payload": {"text": "hi bob"}})
            # Both receive the chat event.
            m_a = ws_a.receive_json()
            m_b = ws_b.receive_json()
            assert m_a["name"] == "chat" and m_a["payload"]["text"] == "hi bob"
            assert m_b["name"] == "chat" and m_b["payload"]["from"] == "alice"


def test_start_game_and_roll(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            ws_a.send_json({"type": "intent", "name": "join", "payload": {"username": "alice"}})
            _drain(ws_a, 3)
            ws_b.send_json({"type": "intent", "name": "join", "payload": {"username": "bob"}})
            _drain(ws_b, 3)
            _drain(ws_a, 1)

            # alice starts the game with a deterministic seed.
            ws_a.send_json({
                "type": "intent", "name": "start_game",
                "payload": {"seed": 42}, "request_id": "s1",
            })

            # Each client should receive: ack(on alice), a flurry of events
            # (game_started, turn_start, lobby_updated), then a Snapshot.
            # We drain until we see a Snapshot for alice.
            alice_saw_game = False
            bob_saw_game = False
            for _ in range(10):
                try:
                    m = ws_a.receive_json()
                except Exception:
                    break
                if m.get("type") == "snapshot" and m["state"].get("game"):
                    alice_saw_game = True
                    alice_snapshot = m
                    break
            for _ in range(10):
                try:
                    m = ws_b.receive_json()
                except Exception:
                    break
                if m.get("type") == "snapshot" and m["state"].get("game"):
                    bob_saw_game = True
                    bob_snapshot = m
                    break
            assert alice_saw_game and bob_saw_game

            # Hand redaction: alice sees her hand, bob's hand is hidden.
            alice_game = alice_snapshot["state"]["game"]
            assert alice_game["phase"] in ("TURN_START", "PRE_ROLL")
            assert len(alice_game["players"]) == 2
            for p in alice_game["players"]:
                if p["username"] == "alice":
                    assert p["hand_size"] >= 4
                    assert len(p["hand"]) == p["hand_size"]
                else:
                    assert p["hand_size"] >= 4
                    assert p["hand"] == []

            bob_game = bob_snapshot["state"]["game"]
            for p in bob_game["players"]:
                if p["username"] == "bob":
                    assert len(p["hand"]) == p["hand_size"]
                else:
                    assert p["hand"] == []

            # Determine who has the first turn and have them roll.
            first = alice_game["turn_order"][alice_game["current_turn_index"]]
            roller_ws = ws_a if first == "alice" else ws_b
            other_ws = ws_b if first == "alice" else ws_a

            roller_ws.send_json({
                "type": "intent", "name": "roll_dice",
                "payload": {"username": first}, "request_id": "r",
            })

            # Find the dice_rolled event on the roller's side.
            rolled = None
            for _ in range(20):
                m = roller_ws.receive_json()
                if m.get("type") == "event" and m["name"] == "dice_rolled":
                    rolled = m
                    break
            assert rolled is not None
            assert rolled["payload"]["player"] == first
            assert len(rolled["payload"]["roll"]) == 2


def test_unknown_intent_errors(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "intent", "name": "join", "payload": {"username": "zoe"}})
            _drain(ws, 3)
            ws.send_json({"type": "intent", "name": "nonsense", "payload": {}, "request_id": "x"})
            m = ws.receive_json()
            assert m["type"] == "error"
            assert m["code"] == "unknown_intent"


def test_start_game_needs_two_players(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "intent", "name": "join", "payload": {"username": "solo"}})
            _drain(ws, 3)
            ws.send_json({"type": "intent", "name": "start_game", "payload": {}, "request_id": "s"})
            m = ws.receive_json()
            assert m["type"] == "error"
            assert m["code"] == "need_players"


def test_game_intent_rejected_before_start(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "intent", "name": "join", "payload": {"username": "alice"}})
            _drain(ws, 3)
            ws.send_json({"type": "intent", "name": "roll_dice", "payload": {}, "request_id": "r"})
            m = ws.receive_json()
            assert m["type"] == "error"
            assert m["code"] == "no_game"
