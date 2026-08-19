"""Disguise is chosen when the destinations are, not before the dice.

Played pre-roll, a Disguise was a blind bet: you spent it and then found out
whether you'd even rolled far enough to reach the post. Now every route through
a manned post is offered alongside the free ones and flagged, and the card is
charged only if the player actually commits to one — the same shape as the
``[fight]`` annotation on pass-through combat stops.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.movement import compute_destinations
from server.game.rng import Rng
from server.game.rules import (
    _GLOBAL_RNG,
    RuleError,
    _enter_movement_phase,
    apply,
)
from server.game.state import GameState, Phase, PlayerState, Warder

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")

# One step south of the Scaffold post, which the initial warder layout mans.
# From here a two-step move can only reach iw_8_11 / iw_7_11 by crossing it.
SOUTH_OF_SCAFFOLD = "iw_8_9"
BEYOND_SCAFFOLD = "iw_8_11"


def _disguise(n: int = 1) -> Card:
    return Card(
        id=f"tower:disguise:{n}", kind="tower", name="Disguise",
        category="utility", effect_key="disguise",
    )


def _game(*, with_disguise: bool) -> GameState:
    alice = PlayerState(
        username="alice", color="red", position=SOUTH_OF_SCAFFOLD, accredited=True,
    )
    if with_disguise:
        alice.hand.append(_disguise())
    game = GameState(
        mode="fast",
        players=[alice, PlayerState(username="bob", color="blue", position="ww05")],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=5,
    )
    game.warders = [Warder(id=w.id, location=w.location) for w in BOARD.data.initial_warders]
    game.phase = Phase.TURN_START
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=5)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_post_routes_are_hidden_without_a_disguise():
    p = PlayerState(username="a", color="red", position=SOUTH_OF_SCAFFOLD, accredited=True)
    opts = compute_destinations(
        BOARD, SOUTH_OF_SCAFFOLD, 2, p, warder_blocking_spaces={"post_scaffold"},
    )
    assert BEYOND_SCAFFOLD not in opts.destinations
    assert opts.requires_disguise == set()


def test_post_routes_are_offered_and_flagged_with_a_disguise():
    p = PlayerState(username="a", color="red", position=SOUTH_OF_SCAFFOLD, accredited=True)
    opts = compute_destinations(
        BOARD, SOUTH_OF_SCAFFOLD, 2, p,
        warder_blocking_spaces={"post_scaffold"}, disguise_available=True,
    )
    assert BEYOND_SCAFFOLD in opts.destinations
    assert BEYOND_SCAFFOLD in opts.requires_disguise
    # Squares reachable without passing the post are not billed for the card.
    assert "iw_8_7" in opts.destinations
    assert "iw_8_7" not in opts.requires_disguise
    # Spending a card is a decision, so this must never auto-commit.
    assert opts.forced_single is False


def test_committing_a_post_route_spends_the_disguise():
    game = _game(with_disguise=True)
    alice = game.player("alice")
    _enter_movement_phase(game, BOARD, alice, 2)
    assert game.phase == Phase.CHOOSING_PATH
    assert BEYOND_SCAFFOLD in game.turn.pending_move.requires_disguise

    new, events = _apply(game, "choose_move_path", {
        "username": "alice", "destination": BEYOND_SCAFFOLD,
    })
    assert new.player("alice").position == BEYOND_SCAFFOLD
    assert new.player("alice").hand == []
    assert [c.name for c in new.tower_discard] == ["Disguise"]
    assert "disguise_played" in [e["kind"] for e in events]
    assert new.turn.disguise_used is True


def test_a_free_route_leaves_the_disguise_in_hand():
    game = _game(with_disguise=True)
    alice = game.player("alice")
    _enter_movement_phase(game, BOARD, alice, 2)

    new, events = _apply(game, "choose_move_path", {
        "username": "alice", "destination": "iw_8_7",
    })
    assert [c.name for c in new.player("alice").hand] == ["Disguise"]
    assert "disguise_played" not in [e["kind"] for e in events]
    assert new.turn.disguise_used is False


def test_post_route_is_refused_if_the_disguise_vanishes_mid_turn():
    """Guards the offered-list / charged-path split against a stale prompt."""
    game = _game(with_disguise=True)
    alice = game.player("alice")
    _enter_movement_phase(game, BOARD, alice, 2)
    alice.hand.clear()
    with pytest.raises(RuleError, match="Disguise"):
        _apply(game, "choose_move_path", {
            "username": "alice", "destination": BEYOND_SCAFFOLD,
        })
