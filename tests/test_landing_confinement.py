"""Walking onto the Bloody or Bowyer Tower locks you up.

Both squares used to be "just visiting" — only the matching raven card put you
behind their doors. Beauchamp Tower stays that way on purpose
(``beauchamp_tower_confinement_only_from_raven_card``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, _log, _resolve_landing, compute_game_stats
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _space_of_kind(kind: str) -> str:
    return next(s.id for s in BOARD.data.spaces if s.kind == kind)


def _land_on(space_id: str) -> tuple[GameState, PlayerState, list[dict]]:
    player = PlayerState(username="p1", color="red", position=space_id,
                         accredited=True)
    state = GameState(mode="fast", players=[player], turn_order=["p1"])
    state.phase = Phase.MOVING
    state.turn = TurnContext(visited_this_turn=[space_id])
    _GLOBAL_RNG.set(Rng(seed=7))
    evs = _resolve_landing(state, BOARD, player)
    return state, player, evs


@pytest.mark.parametrize("kind,status", [
    ("bloody_tower", Status.IMPRISONED),
    ("bowyer_tower", Status.TORTURED),
])
def test_landing_locks_you_up(kind: str, status: Status):
    state, player, evs = _land_on(_space_of_kind(kind))

    ev = next(e for e in evs if e["kind"] == "confined_on_landing")
    assert player.status == status
    assert player.status_turns_remaining == 3
    assert ev["payload"]["status"] == status.value
    assert state.phase == Phase.TURN_END


def test_beauchamp_tower_is_still_just_visiting():
    _, player, evs = _land_on(_space_of_kind("beauchamp_tower"))

    assert player.status == Status.NORMAL
    assert "confined_on_landing" not in [e["kind"] for e in evs]


def test_being_locked_up_on_landing_counts_towards_the_end_of_game_tally():
    state, player, evs = _land_on(_space_of_kind("bloody_tower"))
    # The intent handlers log on the way out; _resolve_landing only returns.
    _log(state, evs)

    assert compute_game_stats(state)["p1"].times_locked_up == 1
