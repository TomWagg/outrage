"""Tests for:
  - the simple-path reachable() that respects a turn-wide visited set
  - forward-only dead-end at Queen's House
  - landing-effect dispatch (devereux coin+card, museum card, broad-arrow
    exception skipping the auto-draw, extra_turn / go_back_by_roll /
    go_to_and_accredit action keys)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _resolve_landing, _commit_move, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def make_player(pos: str = "ww00_start") -> PlayerState:
    return PlayerState(username="p1", color="red", position=pos)


def make_state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[player.position])
    return s


# ---------------------------------------------------------------------------
# reachable() — simple-path enumeration
# ---------------------------------------------------------------------------


def test_forward_only_walks_wall_walk():
    dests = BOARD.reachable("ww00_start", 3, forward_only=True)
    assert list(dests.keys()) == ["ww03"]
    assert dests["ww03"] == ["ww00_start", "ww01", "ww02", "ww03"]


def test_forward_only_stops_at_dead_end():
    # Queen's House (order 77) is the last wall-walk space; nothing forward.
    dests = BOARD.reachable("ww77_queens_house", 1, forward_only=True)
    assert dests == {}


def test_free_movement_respects_prior_visited():
    # Simulate: the player has stood on ww02 earlier this turn (e.g. from a
    # previous partial move), and is now accredited so free-movement applies.
    player = make_player("ww02")
    player.accredited = True
    dests = BOARD.reachable(
        "ww02", 2, forward_only=False, visited={"ww02", "ww00_start", "ww01"},
    )
    # ww00_start and ww01 are excluded; only forward neighbours count.
    assert all(d not in {"ww00_start", "ww01"} for d in dests)


# ---------------------------------------------------------------------------
# Landing: devereux grants coin AND draws a tower card
# ---------------------------------------------------------------------------


def test_devereux_landing_grants_coin_and_tower_card():
    # Put the player on Devereux Tower directly.
    player = make_player("ww56_devereux")
    state = make_state(player)
    # Seed the tower deck with a dummy card.
    dummy = Card(id="tc-x", kind="tower", category="utility", name="Dummy", value=0)
    state.tower_draw = [dummy]
    _GLOBAL_RNG.set(Rng(seed=1))

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "coin_picked_up" in kinds
    assert "tower_card_drawn" in kinds
    assert player.has_coin
    assert dummy in player.hand


def test_broad_arrow_exception_skips_tower_card_and_surrenders_weapons():
    # ww29_broad_arrow has kind=tower but is listed as an exception, so landing
    # should NOT auto-draw a tower card; instead the `surrender_weapons`
    # action runs and drops all weapon cards from the player's hand.
    player = make_player("ww29_broad_arrow")
    weapon = Card(id="w1", kind="tower", category="weapon", name="Sword", value=5)
    utility = Card(id="u1", kind="tower", category="utility", name="Disguise", value=0)
    player.hand = [weapon, utility]
    state = make_state(player)
    deck_card = Card(id="tc-deck", kind="tower", category="utility", name="X", value=0)
    state.tower_draw = [deck_card]  # should remain in deck
    _GLOBAL_RNG.set(Rng(seed=1))

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "tower_card_drawn" not in kinds
    assert "weapons_surrendered" in kinds
    # Weapon discarded, utility kept, tower card still in deck.
    assert weapon not in player.hand
    assert utility in player.hand
    assert deck_card in state.tower_draw


def test_museum_landing_draws_tower_card():
    player = make_player("iw_museum")
    state = make_state(player)
    dummy = Card(id="tc-m", kind="tower", category="utility", name="Dummy", value=0)
    state.tower_draw = [dummy]
    _GLOBAL_RNG.set(Rng(seed=1))

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "tower_card_drawn" in kinds
    assert dummy in player.hand


# ---------------------------------------------------------------------------
# Space action dispatch
# ---------------------------------------------------------------------------


def test_extra_turn_queues_and_resets_doubles():
    player = make_player("ww49_extra_turn")
    state = make_state(player)
    state.turn.consecutive_doubles = 2

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "extra_turn_granted" in kinds
    assert state.turn.extra_turns_queued == 1
    assert state.turn.consecutive_doubles == 0


def test_go_back_by_roll_teleports_back_along_wall():
    player = make_player("ww65_go_back")
    state = make_state(player)
    state.turn.roll = [3, 2]  # total 5 → back to wall_walk_order 60

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "go_back_by_roll" in kinds
    # ww60_questioned has wall_walk_order 60.
    assert player.position == "ww60_questioned"


def test_go_to_and_accredit_teleports_and_ends_turn():
    player = make_player("ww31_qh_accredit")
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    kinds = [e["kind"] for e in evs]
    assert "go_to_and_accredit" in kinds
    assert "accredited" in kinds
    assert player.position == "ww77_queens_house"
    assert player.accredited
    assert state.phase == Phase.TURN_END


# ---------------------------------------------------------------------------
# No-revisit across a turn: commit a move, then verify the next pathfinder
# call cannot re-enter any walked square.
# ---------------------------------------------------------------------------


def test_commit_move_extends_visited_and_blocks_reentry():
    player = make_player("ww00_start")
    state = make_state(player)
    # Simulate a 5-step forward move along the wall.
    path = ["ww00_start", "ww01", "ww02", "ww03", "ww04_guidebook", "ww05"]
    _GLOBAL_RNG.set(Rng(seed=1))
    _commit_move(state, BOARD, player, "ww05", path)

    # Every square in the path must now be in visited_this_turn.
    assert set(path).issubset(set(state.turn.visited_this_turn))
    # The next forward-only move of 1 from ww05 is ww06 (untouched).
    dests = BOARD.reachable(
        "ww05", 1, forward_only=True, visited=state.turn.visited_this_turn,
    )
    assert list(dests) == ["ww06"]

    # A 6-step forward move would need to re-enter ww05 (already visited) to
    # make it to ww11 — wait no, forward-only from ww05 advances to ww06..11
    # without revisiting ww05. But the starting space ww05 is in visited_set
    # and the forward walker never re-enters its starting space, so this should
    # still work fine.
    dests = BOARD.reachable(
        "ww05", 6, forward_only=True, visited=state.turn.visited_this_turn,
    )
    assert list(dests) == ["ww11"]
