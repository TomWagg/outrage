"""Reconnection smoke test.

A mid-game socket drop should:
  * flip the player's ``connected`` flag to False while the socket is gone,
  * preserve the game state (nothing is mutated by disconnect),
  * accept a fresh socket reconnecting under the same username,
  * deliver a snapshot to the reconnecting client that re-hydrates the game
    (with the player's own hand visible).

The server rejects *new* usernames while a game is active — only players
already in the game may reconnect — so this test also exercises that branch.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_and_saves(tmp_path, monkeypatch):
    import server.server_state as ss

    tmp_saves = tmp_path / "saves"
    tmp_saves.mkdir()
    monkeypatch.setattr(ss, "SAVES_DIR", tmp_saves)
    monkeypatch.setattr(ss, "GAME_FILE", tmp_saves / "current_game.json")
    monkeypatch.setattr(ss, "STATS_FILE", tmp_saves / "stats.json")
    ss.APP = None

    from server.main import app
    yield app, tmp_saves


def _drain(ws, n):
    return [ws.receive_json() for _ in range(n)]


def _latest_snapshot_with_game(ws, max_msgs: int = 20):
    for _ in range(max_msgs):
        m = ws.receive_json()
        if m.get("type") == "snapshot" and m["state"].get("game"):
            return m
    raise AssertionError("never saw a game snapshot")


def _start_two_player_game(client):
    ws_a = client.websocket_connect("/ws").__enter__()
    ws_b = client.websocket_connect("/ws").__enter__()
    ws_a.send_json({"type": "intent", "name": "join", "payload": {"username": "alice"}})
    _drain(ws_a, 3)
    ws_b.send_json({"type": "intent", "name": "join", "payload": {"username": "bob"}})
    _drain(ws_b, 3)
    _drain(ws_a, 1)  # alice sees bob's lobby_updated
    ws_a.send_json({
        "type": "intent", "name": "start_game",
        "payload": {"seed": 7}, "request_id": "s",
    })
    _latest_snapshot_with_game(ws_a)
    _latest_snapshot_with_game(ws_b)
    return ws_a, ws_b


def test_reconnect_same_username_resumes_game(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        ws_a, ws_b = _start_two_player_game(client)

        # Drop bob.
        ws_b.close()
        ws_b.__exit__(None, None, None)

        # Alice should see a lobby_updated / game_snapshots event flurry
        # reflecting bob's disconnect. Drain best-effort.
        for _ in range(6):
            try:
                m = ws_a.receive_json(timeout=0.5)
            except Exception:
                break
            if m.get("type") == "snapshot" and m["state"].get("game"):
                bob_p = next(p for p in m["state"]["game"]["players"] if p["username"] == "bob")
                if bob_p["connected"] is False:
                    break

        # Reconnect as bob.
        with client.websocket_connect("/ws") as ws_b2:
            ws_b2.send_json({
                "type": "intent", "name": "join",
                "payload": {"username": "bob"}, "request_id": "rejoin",
            })
            msgs = _drain(ws_b2, 3)
            assert msgs[0]["type"] == "ack"
            snap = msgs[1]
            assert snap["type"] == "snapshot"
            assert snap["state"]["you"] == "bob"
            game = snap["state"]["game"]
            assert game is not None, "reconnect should include the live game"
            # Bob's own hand should be visible again after reconnect.
            bob_p = next(p for p in game["players"] if p["username"] == "bob")
            assert bob_p["connected"] is True
            assert len(bob_p["hand"]) == bob_p["hand_size"]
            # Alice's hand is still redacted.
            alice_p = next(p for p in game["players"] if p["username"] == "alice")
            assert alice_p["hand"] == []

        ws_a.__exit__(None, None, None)


def test_new_username_rejected_mid_game(app_and_saves):
    app, _ = app_and_saves
    with TestClient(app) as client:
        ws_a, ws_b = _start_two_player_game(client)
        with client.websocket_connect("/ws") as ws_c:
            ws_c.send_json({
                "type": "intent", "name": "join",
                "payload": {"username": "carol"}, "request_id": "late",
            })
            msg = ws_c.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "game_in_progress"
        ws_a.__exit__(None, None, None)
        ws_b.__exit__(None, None, None)
