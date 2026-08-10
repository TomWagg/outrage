"""Tests for jewel-theft attempts.

Covers the immediate attempt queued by the ``go_to_jewel_view`` raven card,
which is drawn on landing and auto-resolves (it needs no player input) — the
pending attempt used to be dropped by the MOVING → TURN_END fallthrough at the
end of ``_resolve_landing``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import RuleError, _resolve_landing, apply, _GLOBAL_RNG
from server.game.state import (
    GameState, PendingJewelAttempt, Phase, PlayerState, TurnContext,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def make_player(pos: str) -> PlayerState:
    return PlayerState(username="p1", color="red", position=pos)


def make_state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[player.position])
    # start_game seeds this from the board; we're calling the landing resolver
    # directly, so do it by hand.
    s.jewels_available = dict(BOARD.data.initial_jewel_locations)
    return s


def raven_jewel_view(jewel: str) -> Card:
    return Card(
        id=f"raven:go_to_jewel_view:{jewel}",
        kind="raven",
        name="Go to Jewel View",
        effect_key="go_to_jewel_view",
        params={"jewel": jewel},
    )


def a_raven_trigger_space() -> str:
    """Any inner-ward square that draws a raven card on landing."""
    return next(s.id for s in BOARD.data.spaces if s.kind == "raven_trigger")


def test_go_to_jewel_view_offers_the_attempt_instead_of_ending_the_turn():
    player = make_player(a_raven_trigger_space())
    state = make_state(player)
    _GLOBAL_RNG.set(Rng(seed=1))
    # Place the sword where the board says it lives, then stack the raven deck.
    jewel_space = state.jewels_available["sword"]
    state.raven_draw = [raven_jewel_view("sword")]

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]

    assert "raven_card_drawn" in kinds
    assert "jewel_attempt_offered" in kinds
    assert player.position == jewel_space
    # The whole point: we stop at the attempt rather than falling through to
    # TURN_END and discarding it.
    assert state.phase == Phase.JEWEL_ATTEMPT
    assert state.turn.pending_jewel is not None
    assert state.turn.pending_jewel.jewel_id == "sword"
    assert state.turn.pending_jewel.source == "raven_view"


def test_burglary_tools_survive_a_successful_attempt():
    """Tools are always re-usable — success shouldn't consume them."""
    jewel_space = BOARD.data.initial_jewel_locations["sword"]
    player = make_player(jewel_space)
    tool = Card(id="tc-file", kind="tower", category="burglary", name="File", value=12)
    player.hand = [tool]
    state = make_state(player)
    # As if they had just landed on it.
    state.phase = Phase.JEWEL_ATTEMPT
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id="sword", space_id=jewel_space, source="landing",
    )

    # threshold = 12 - 12 = 0, so any roll succeeds.
    state, evs = apply(
        state, "attempt_jewel",
        {"username": player.username, "tool_card_ids": ["tc-file"]},
        board=BOARD, rng=Rng(seed=1),
    )
    attempt = next(e for e in evs if e["kind"] == "jewel_attempt")

    assert attempt["payload"]["success"] is True
    assert [c.id for c in state.players[0].hand] == ["tc-file"]
    assert state.tower_discard == []
    assert "sword" in state.players[0].jewels


def test_a_failed_attempt_leaves_the_thief_standing_on_the_jewel():
    jewel_space = BOARD.data.initial_jewel_locations["sword"]
    player = make_player(jewel_space)
    state = make_state(player)
    # As if they had just landed on it.
    state.phase = Phase.JEWEL_ATTEMPT
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id="sword", space_id=jewel_space, source="landing",
    )

    # No tools → threshold 12; seed chosen so the roll falls short.
    state, evs = apply(
        state, "attempt_jewel",
        {"username": player.username, "tool_card_ids": []},
        board=BOARD, rng=Rng(seed=1),
    )
    attempt = next(e for e in evs if e["kind"] == "jewel_attempt")

    assert attempt["payload"]["success"] is False
    assert "jewel_attempt_retry_available" in [e["kind"] for e in evs]
    assert state.players[0].position == jewel_space
    assert state.jewels_available["sword"] == jewel_space
    assert state.phase == Phase.TURN_END


def test_the_attempt_can_be_retried_at_the_start_of_the_next_turn():
    """No pending attempt, but standing on the jewel → the intent still works."""
    jewel_space = BOARD.data.initial_jewel_locations["sword"]
    player = make_player(jewel_space)
    state = make_state(player)
    state.phase = Phase.TURN_START
    state.turn = TurnContext()

    state, evs = apply(
        state, "attempt_jewel",
        {"username": player.username, "tool_card_ids": []},
        board=BOARD, rng=Rng(seed=2),
    )

    assert "jewel_attempt" in [e["kind"] for e in evs]


def test_retry_is_refused_when_not_standing_on_a_jewel():
    player = make_player("ww01")
    state = make_state(player)
    state.phase = Phase.TURN_START
    state.turn = TurnContext()

    with pytest.raises(RuleError):
        apply(state, "attempt_jewel", {"username": player.username, "tool_card_ids": []},
              board=BOARD, rng=Rng(seed=1))


def test_go_to_jewel_view_for_a_taken_jewel_just_ends_the_turn():
    player = make_player(a_raven_trigger_space())
    state = make_state(player)
    _GLOBAL_RNG.set(Rng(seed=1))
    state.jewels_available.pop("sword")
    state.raven_draw = [raven_jewel_view("sword")]

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]

    assert "jewel_already_taken" in kinds
    assert state.turn.pending_jewel is None
    assert state.phase == Phase.TURN_END
