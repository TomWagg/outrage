"""Tests for the Rack and the benches.

Both are space *kinds* rather than space *actions*, and neither was wired into
``_resolve_landing`` — landing on the White Tower's rack-sender square or on
either bench used to do nothing at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _resolve_landing, apply, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")

RACK_SENDER = next(s.id for s in BOARD.data.spaces if s.kind == "rack_sender")


def make_player(pos: str) -> PlayerState:
    return PlayerState(username="p1", color="red", position=pos)


def make_state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[player.position])
    _GLOBAL_RNG.set(Rng(seed=3))
    return s


def dummy_card(n: int) -> Card:
    return Card(id=f"tc-{n}", kind="tower", category="utility", name="Dummy", value=0)


def kinds_of(evs) -> list[str]:
    return [e["kind"] for e in evs]


# ---------------------------------------------------------------------------
# Rack sender
# ---------------------------------------------------------------------------


def test_rack_sender_racks_the_player_for_three_turns():
    player = make_player(RACK_SENDER)
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)

    assert "rack_sender_triggered" in kinds_of(evs)
    assert player.position == BOARD.data.rack_space
    assert player.status == Status.RACKED
    assert player.status_turns_remaining == 3
    assert state.phase == Phase.TURN_END


def test_rack_entry_costs_the_coin_when_one_is_held():
    player = make_player(RACK_SENDER)
    player.has_coin = True
    player.hand = [dummy_card(1), dummy_card(2)]
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)

    assert "rack_coin_lost" in kinds_of(evs)
    assert not player.has_coin
    # Coin paid, hand kept.
    assert len(player.hand) == 2


def test_rack_entry_costs_the_whole_hand_without_a_coin():
    player = make_player(RACK_SENDER)
    player.hand = [dummy_card(1), dummy_card(2)]
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "rack_hand_lost")

    assert ev["payload"]["count"] == 2
    assert player.hand == []
    assert len(state.tower_discard) == 2


# ---------------------------------------------------------------------------
# Rack countdown — the player must be able to get off it again
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["roll_dice", "end_turn"])
def test_rack_counts_down_whether_the_player_rolls_or_not(intent):
    """A racked player can't sit out the sentence by never pressing Roll."""
    player = make_player(BOARD.data.rack_space)
    player.status = Status.RACKED
    player.status_turns_remaining = 3
    state = GameState(mode="fast", players=[player], turn_order=[player.username])
    state.phase = Phase.TURN_START
    state.turn = TurnContext()
    rng = Rng(seed=5)
    _GLOBAL_RNG.set(rng)

    for expected in (2, 1, 0):
        state.phase = Phase.TURN_START
        state, _ = apply(
            state, intent, {"username": player.username}, board=BOARD, rng=rng,
        )
        assert state.players[0].status_turns_remaining == expected

    assert state.players[0].status == Status.NORMAL


# ---------------------------------------------------------------------------
# Benches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bench_id", BOARD.data.bench_space_ids)
def test_landing_on_a_bench_costs_the_next_turn(bench_id):
    player = make_player(bench_id)
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)

    assert "resting_on_bench" in kinds_of(evs)
    assert player.miss_next_turn


# ---------------------------------------------------------------------------
# Hospital / Shop also cost a turn; Royal Armouries hands out a card
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("space_id", ["iw_hospital", "iw_shop"])
def test_hospital_and_shop_cost_the_next_turn(space_id):
    player = make_player(space_id)
    state = make_state(player)
    state.tower_draw = [dummy_card(1)]

    evs = _resolve_landing(state, BOARD, player)

    assert "miss_turn_on_landing" in kinds_of(evs)
    assert player.miss_next_turn
    # They don't hand out a tower card.
    assert "tower_card_drawn" not in kinds_of(evs)
    assert player.hand == []


def test_royal_armouries_hands_out_a_tower_card_and_costs_nothing():
    player = make_player("iw_royal_armouries")
    state = make_state(player)
    state.tower_draw = [dummy_card(1)]

    evs = _resolve_landing(state, BOARD, player)

    assert "tower_card_drawn" in kinds_of(evs)
    assert len(player.hand) == 1
    assert not player.miss_next_turn
