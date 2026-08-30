"""What the server layer does with a rule engine that says no.

Driven through :func:`server.main._handle_game_intent` with a stub socket
rather than a real WebSocket: the question here is which error code comes back
and what state survives, and neither needs a live connection.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG
from server.game.state import Combat, GameState, Phase, PlayerState
from server.main import _handle_game_intent, _update_stats_from_events
from server.server_state import AppState


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


class StubConn:
    """Stands in for a :class:`~server.net.connection.Connection`."""

    def __init__(self, username: str):
        self.username = username
        self.sent: list[dict] = []

    async def send(self, msg) -> None:
        self.sent.append(msg.model_dump() if hasattr(msg, "model_dump") else msg)

    def errors(self) -> list[dict]:
        return [m for m in self.sent if m.get("type") == "error"]


def make_app() -> tuple[AppState, GameState]:
    _GLOBAL_RNG.set(Rng(seed=4))
    game = GameState(
        players=[
            PlayerState(username="alice", color="red", position=BOARD.data.start_space),
            PlayerState(username="bob", color="blue", position=BOARD.data.start_space),
        ],
        turn_order=["alice", "bob"],
        phase=Phase.TURN_START,
    )
    app = AppState()
    app.board = BOARD
    app.game = game
    app.rng = Rng(seed=4)
    return app, game


def dispatch(app: AppState, conn: StubConn, name: str, payload: dict) -> None:
    asyncio.run(_handle_game_intent(app, conn, name, payload, "req-1"))


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


def test_a_wrong_turn_intent_is_a_rule_error():
    app, _ = make_app()
    conn = StubConn("bob")
    dispatch(app, conn, "roll_dice", {})
    assert [e["code"] for e in conn.errors()] == ["rule_error"]


def test_a_combat_refusal_is_a_rule_error_not_an_internal_one():
    """``combat.py`` speaks ``CombatError``, which is neither a ``RuleError``
    nor an engine fault. Left untranslated it lands in the catch-all and the
    player is told the server broke."""
    app, game = make_app()
    game.phase = Phase.COMBAT
    game.combat = Combat(attacker="alice", defender="bob",
                         space_id=BOARD.data.start_space,
                         phase="defender_selecting")
    conn = StubConn("alice")
    dispatch(app, conn, "reveal_combat", {})

    errs = conn.errors()
    assert [e["code"] for e in errs] == ["rule_error"]
    assert "defender_selecting" in errs[0]["message"]


def test_acting_as_somebody_else_is_refused_before_the_engine_sees_it():
    app, _ = make_app()
    conn = StubConn("bob")
    dispatch(app, conn, "roll_dice", {"username": "alice"})
    assert [e["code"] for e in conn.errors()] == ["auth"]


# ---------------------------------------------------------------------------
# A refused intent must not leave a mark
# ---------------------------------------------------------------------------


def test_a_refused_intent_leaves_the_installed_game_untouched():
    app, game = make_app()
    jewel_space = BOARD.data.initial_jewel_locations["sword"]
    game.player("alice").position = jewel_space
    game.jewels_available = {"sword": jewel_space}
    before = game.model_dump()

    conn = StubConn("alice")
    dispatch(app, conn, "attempt_jewel", {"tool_card_ids": ["not-a-card"]})

    assert [e["code"] for e in conn.errors()] == ["rule_error"]
    assert app.game is game, "a refused intent must not swap the installed game"
    assert app.game.model_dump() == before
    assert app.game.turn.pending_jewel is None


def test_an_accepted_intent_installs_the_new_state():
    app, game = make_app()
    conn = StubConn("alice")
    dispatch(app, conn, "roll_dice", {})

    assert conn.errors() == []
    assert app.game is not game, "an accepted intent installs the state it produced"
    assert app.game.turn.roll


# ---------------------------------------------------------------------------
# Lifetime stats
# ---------------------------------------------------------------------------


def test_lifetime_steps_count_squares_walked_not_pips():
    """A teleport carries no path and a wasted roll no movement, so neither
    should show up as distance covered."""
    app, _ = make_app()
    _update_stats_from_events(app, [
        {"kind": "dice_rolled", "payload": {"player": "alice", "roll": [6, 6]}},
        {"kind": "player_moved",
         "payload": {"player": "alice", "path": ["a", "b", "c", "d"]}},
        # A teleport: no path, so no distance.
        {"kind": "player_moved",
         "payload": {"player": "alice", "src": "d", "dst": "z", "move_kind": "teleport"}},
    ])
    alice = app.stats.get("alice")
    assert alice.total_steps_taken == 3
    assert alice.doubles_rolled == 1
    assert not hasattr(alice, "total_dice_rolls")


def test_stats_saved_before_the_rename_load_without_complaint():
    """The old ``total_dice_rolls`` key is simply dropped; a stats file written
    by an earlier build must still load rather than wiping everyone's history."""
    from server.stats import StatsStore
    store = StatsStore.model_validate({
        "by_username": {
            "alice": {"username": "alice", "games_played": 3, "total_dice_rolls": 375},
        },
    })
    assert store.get("alice").games_played == 3
    assert store.get("alice").total_steps_taken == 0


# ---------------------------------------------------------------------------
# Saves written by an older build
# ---------------------------------------------------------------------------


def test_a_save_carrying_a_since_removed_field_still_loads():
    """Every state model forbids extra fields, so dropping a field from the
    schema would otherwise make the live game unloadable — it fails validation,
    is discarded on the next restart, and the game in progress is gone."""
    from server.server_state import _load_saved_game

    _GLOBAL_RNG.set(Rng(seed=1))
    saved = GameState(
        players=[PlayerState(username="alice", color="red",
                             position=BOARD.data.start_space)],
        turn_order=["alice"],
    ).model_dump()
    saved["turn"]["binary_disruption_armed"] = False   # a field this build dropped
    saved["players"][0]["some_old_flag"] = True

    game = _load_saved_game(saved)

    assert game.players[0].username == "alice"
    assert not hasattr(game.turn, "binary_disruption_armed")


def test_a_genuinely_corrupt_save_is_still_refused():
    """The retry drops only the exact keys Pydantic named, so it cannot paper
    over a save that is wrong in some other way."""
    from pydantic import ValidationError
    from server.server_state import _load_saved_game

    with pytest.raises(ValidationError):
        _load_saved_game({"players": "not a list", "turn_order": []})
