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


def land_and_reveal(state, player):
    """Land, then turn the raven card over — effects fire on reveal, not on landing.

    ``apply`` works on a copy, so the caller must take the state it hands back
    rather than keep looking at the one it passed in.
    """
    evs = _resolve_landing(state, BOARD, player)
    if state.turn.pending_raven is not None:
        state, more = apply(
            state, "reveal_raven_notice", {"username": player.username},
            board=BOARD, rng=_GLOBAL_RNG.get(),
        )
        evs = evs + more
    return state, evs


def test_go_to_jewel_view_offers_the_attempt_instead_of_ending_the_turn():
    player = make_player(a_raven_trigger_space())
    state = make_state(player)
    _GLOBAL_RNG.set(Rng(seed=1))
    # Place the sword where the board says it lives, then stack the raven deck.
    jewel_space = state.jewels_available["sword"]
    state.raven_draw = [raven_jewel_view("sword")]

    state, evs = land_and_reveal(state, player)
    kinds = [e["kind"] for e in evs]

    assert "raven_card_drawn" in kinds
    assert "jewel_attempt_offered" in kinds
    assert state.player("p1").position == jewel_space
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

    state, evs = land_and_reveal(state, player)
    kinds = [e["kind"] for e in evs]

    assert "jewel_already_taken" in kinds
    assert state.turn.pending_jewel is None
    assert state.phase == Phase.TURN_END


# ---------------------------------------------------------------------------
# Whose attempt is it, and what a double buys
# ---------------------------------------------------------------------------


def _two_player_state() -> GameState:
    """A roller and a victim, both on the start square, jewels on their plinths."""
    a = PlayerState(username="p1", color="red", position="ww00_start")
    b = PlayerState(username="p2", color="blue", position="ww00_start")
    s = GameState(mode="fast", players=[a, b], turn_order=["p1", "p2"],
                  current_turn_index=0)
    s.phase = Phase.TURN_END
    s.turn = TurnContext()
    s.jewels_available = dict(BOARD.data.initial_jewel_locations)
    return s


class FixedRng(Rng):
    """Rng that returns a scripted pair from ``roll_dice``."""

    def __init__(self, pair):
        super().__init__(seed=1)
        self._pair = list(pair)

    def roll_dice(self, n=2):
        return list(self._pair)


def test_a_double_on_the_jewel_roll_buys_another_turn():
    """A double is a double whatever you rolled it for."""
    state = _two_player_state()
    player = state.current_player()
    jewel_space = state.jewels_available["sword"]
    player.position = jewel_space
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id="sword", space_id=jewel_space, player=player.username,
    )
    state.phase = Phase.JEWEL_ATTEMPT
    rng = FixedRng([3, 3])
    _GLOBAL_RNG.set(rng)

    state, evs = apply(state, "attempt_jewel",
                       {"username": player.username, "tool_card_ids": []},
                       board=BOARD, rng=rng)

    assert state.turn.extra_turns_queued == 1
    assert next(e for e in evs if e["kind"] == "jewel_attempt")["payload"]["doubled"]


def test_a_non_double_buys_nothing():
    state = _two_player_state()
    player = state.current_player()
    jewel_space = state.jewels_available["sword"]
    player.position = jewel_space
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id="sword", space_id=jewel_space, player=player.username,
    )
    state.phase = Phase.JEWEL_ATTEMPT
    rng = FixedRng([2, 5])
    _GLOBAL_RNG.set(rng)

    state, _ = apply(state, "attempt_jewel",
                     {"username": player.username, "tool_card_ids": []},
                     board=BOARD, rng=rng)

    assert state.turn.extra_turns_queued == 0


def test_the_attempt_belongs_to_whoever_was_put_on_the_jewel():
    """A split 7 that shoves an opponent onto a jewel hands *them* the theft.
    The roller was being offered the attempt — and taking the jewel."""
    state = _two_player_state()
    roller = state.current_player()
    victim = next(p for p in state.players if p.username != roller.username)
    jewel_space = state.jewels_available["sword"]
    victim.position = jewel_space
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id="sword", space_id=jewel_space, player=victim.username,
    )
    state.phase = Phase.JEWEL_ATTEMPT
    rng = FixedRng([6, 6])
    _GLOBAL_RNG.set(rng)

    with pytest.raises(RuleError, match=victim.username):
        apply(state, "attempt_jewel",
              {"username": roller.username, "tool_card_ids": []},
              board=BOARD, rng=rng)

    state, _ = apply(state, "attempt_jewel",
                     {"username": victim.username, "tool_card_ids": []},
                     board=BOARD, rng=rng)
    assert state.player(victim.username).jewels == ["sword"]
    assert state.player(roller.username).jewels == []
    # The double they rolled is owed to them, not spent on the roller's turn.
    assert state.player(victim.username).extra_turns_pending == 1
    assert state.turn.extra_turns_queued == 0
