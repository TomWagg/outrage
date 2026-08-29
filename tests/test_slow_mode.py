"""Slow-mode end-of-game scoring and ranking.

- Game ends when only one non-escaped player remains, OR when every jewel is
  banked (none in the White Tower, none loose, and none in the pocket of a
  player still on the board).
- Ranking sorts by jewel count → top jewel value → sum of jewel values,
  with a deterministic username tie-break.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, _slow_ranking, apply
from server.game.state import GameState, Phase, PlayerState, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _slow_state(players: list[PlayerState]) -> GameState:
    s = GameState(
        mode="slow",
        players=players,
        turn_order=[p.username for p in players],
        current_turn_index=0,
        seed=1,
    )
    s.phase = Phase.TURN_END
    s.turn = TurnContext()
    return s


def test_slow_ranking_count_then_top_value():
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    jewels=["orb", "sword"]),  # 2 jewels, top 2
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=["crown_st_edward", "sword"]),  # 2 jewels, top 5
        PlayerState(username="carol", color="green", position="ww00_start",
                    jewels=["sceptre"]),  # 1 jewel, top 3
    ]
    state = _slow_state(players)
    ranking = _slow_ranking(state)
    assert [r["username"] for r in ranking] == ["bob", "alice", "carol"]
    assert ranking[0]["jewel_count"] == 2
    assert ranking[0]["jewel_top_value"] == 5


def test_slow_ranking_deterministic_username_tiebreak():
    players = [
        PlayerState(username="zack", color="red", position="ww00_start", jewels=[]),
        PlayerState(username="alice", color="blue", position="ww00_start", jewels=[]),
    ]
    state = _slow_state(players)
    assert [r["username"] for r in _slow_ranking(state)] == ["alice", "zack"]


def test_slow_game_ends_when_every_jewel_is_banked():
    """Once no jewel is left to steal *or* to take off somebody, the game ends
    at the next turn transition."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    jewels=[], accredited=True),
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=["crown_prince_of_wales", "orb", "sword",
                            "crown_st_edward", "sceptre"],
                    accredited=True, escaped=True),
        PlayerState(username="carol", color="green", position="ww00_start",
                    jewels=[], accredited=True),
    ]
    state = _slow_state(players)
    # No jewels in the White Tower, none loose, and the only holder is out —
    # so two players are still walking but there is nothing left to play for.
    state.jewels_available = {}
    state.loose_jewels = {}
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    state, events = apply(state, "end_turn", {"username": "alice"},
                          board=BOARD, rng=rng)
    assert state.phase == Phase.GAME_OVER
    ev = next(e for e in events if e["kind"] == "slow_game_over")
    assert ev["payload"]["winner"] == "bob"
    assert ev["payload"]["reason"] == "jewels_exhausted"
    assert len(ev["payload"]["ranking"]) == 3
    assert ev["payload"]["ranking"][0]["username"] == "bob"


def test_slow_game_continues_while_a_jewel_is_still_carried():
    """A jewel in the pocket of somebody still on the board is not banked.

    The last jewel leaving its plinth used to end the game on the spot, handing
    the win to whoever grabbed it — even though the other player could still
    have taken it off them in a fight.
    """
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    jewels=[], accredited=True),
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=["crown_prince_of_wales", "orb", "sword",
                            "crown_st_edward", "sceptre"], accredited=True),
    ]
    state = _slow_state(players)
    state.jewels_available = {}
    state.loose_jewels = {}
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    state, events = apply(state, "end_turn", {"username": "alice"},
                          board=BOARD, rng=rng)
    assert state.phase != Phase.GAME_OVER
    assert not any(e["kind"] == "slow_game_over" for e in events)


def test_slow_game_ends_when_last_player_remaining():
    """If everyone else has escaped, the game ends even with jewels still
    available in the White Tower."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    jewels=["crown_st_edward"], escaped=True),
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=[]),
    ]
    state = _slow_state(players)
    state.jewels_available = {"orb": "wt_11_2"}  # still something to steal
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    state, events = apply(state, "end_turn", {"username": "alice"},
                          board=BOARD, rng=rng)
    assert state.phase == Phase.GAME_OVER
    ev = next(e for e in events if e["kind"] == "slow_game_over")
    assert ev["payload"]["reason"] == "last_player"
    assert ev["payload"]["winner"] == "alice"
