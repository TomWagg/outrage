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
    player.jewels = ["sword"]
    player.hand = [dummy_card(1), dummy_card(2)]
    state = make_state(player)
    coins_before = state.coins_available

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "sent_to_rack")

    assert ev["payload"]["penalty"] == "coin"
    assert not player.has_coin
    assert len(player.hand) == 2          # coin paid, hand spared
    assert player.jewels == []            # jewels go whatever the toll was
    assert player.rack_escrow.coin is True
    assert player.rack_escrow.jewels == ["sword"]
    # Held, not destroyed: the coin does not go back on the pile while a Rack
    # Pardon could still undo the sentence.
    assert state.coins_available == coins_before


def test_rack_entry_costs_the_whole_hand_without_a_coin():
    player = make_player(RACK_SENDER)
    player.hand = [dummy_card(1), dummy_card(2)]
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "sent_to_rack")

    assert ev["payload"]["penalty"] == "hand"
    assert ev["payload"]["cards_taken"] == 2
    assert player.hand == []
    assert len(player.rack_escrow.cards) == 2
    assert state.tower_discard == []      # held in escrow, not discarded yet


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


# ---------------------------------------------------------------------------
# Leaving the Rack
# ---------------------------------------------------------------------------


def _two_player_state(a: PlayerState, b: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[a, b],
                  turn_order=[a.username, b.username], current_turn_index=0)
    s.phase = Phase.TURN_END
    s.turn = TurnContext()
    _GLOBAL_RNG.set(Rng(seed=3))
    return s


def test_serving_the_sentence_steps_out_of_the_rack():
    """The Rack is a dead end. Clearing the status without moving the piece
    left the freed player still sitting in the cell — and one forced step from
    being sent straight back down."""
    a = PlayerState(username="p1", color="red", position="ww00_start")
    b = PlayerState(username="p2", color="blue", position=BOARD.data.rack_space,
                    status=Status.RACKED, status_turns_remaining=1)
    state = _two_player_state(a, b)
    rng = Rng(seed=3)
    _GLOBAL_RNG.set(rng)

    state, _ = apply(state, "end_turn", {"username": "p1"}, board=BOARD, rng=rng)

    freed = state.player("p2")
    assert freed.status == Status.NORMAL
    assert freed.position == BOARD.rack_exit_space == RACK_SENDER


def test_a_split_seven_cannot_reach_a_player_in_the_white_tower():
    """Everything inside the White Tower is immune to being shoved about.

    A player released onto the Rack Sender was being pushed one square by
    somebody else's seven, which walked them back onto the sender and racked
    them a second time.
    """
    from server.game.rules import _split_movable_targets

    roller = PlayerState(username="p1", color="red", position="ww00_start")
    victim = PlayerState(username="p2", color="blue", position=RACK_SENDER)
    state = _two_player_state(roller, victim)

    assert _split_movable_targets(state, BOARD, roller, 7) == {}


def test_the_rack_exit_does_not_hand_out_the_rope_route():
    """The Rack Sender reaches (13,13) only by rope. It was also listed as a
    plain neighbour, so a freed player with an empty hand was offered the far
    side of it."""
    from server.game.movement import compute_destinations

    player = PlayerState(username="p1", color="red", position=RACK_SENDER)
    opts = compute_destinations(BOARD, RACK_SENDER, 6, player,
                                visited_this_turn=[RACK_SENDER])

    assert "iw_13_13" not in opts.destinations
    # ...and no route sneaks across it on the way somewhere else. The only way
    # out of the White Tower on foot is forward, through the Chapel of St John.
    assert all("iw_13_13" not in path for path in opts.destinations.values())
    assert set(opts.destinations) == {"iw_14_4"}


# ---------------------------------------------------------------------------
# Being dragged off a resting square
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rest_space", ["iw_shop", "iw_hospital", "iw_bench_10_13"])
def test_a_forced_move_off_a_resting_square_cancels_the_missed_turn(rest_space):
    """Browsing the Shop costs you a turn because you are in the Shop. A seven
    that hauls you out of it is allowed to, and takes the errand with it."""
    from server.game.rules import cancel_rest_if_moved_off

    resting = PlayerState(username="p2", color="blue", position=rest_space,
                          miss_next_turn=True)
    state = _two_player_state(
        PlayerState(username="p1", color="red", position="ww00_start"), resting)

    resting.position = "iw_17_9"
    evs = cancel_rest_if_moved_off(state, BOARD, resting, rest_space)

    assert not resting.miss_next_turn
    assert "rest_interrupted" in kinds_of(evs)


def test_staying_put_keeps_the_missed_turn():
    """The cancellation is about being moved, not about being targeted."""
    from server.game.rules import cancel_rest_if_moved_off

    resting = PlayerState(username="p2", color="blue", position="iw_shop",
                          miss_next_turn=True)
    state = _two_player_state(
        PlayerState(username="p1", color="red", position="ww00_start"), resting)

    assert cancel_rest_if_moved_off(state, BOARD, resting, "iw_shop") == []
    assert resting.miss_next_turn


def test_a_move_off_an_ordinary_square_leaves_the_missed_turn_alone():
    """A miss that isn't tied to where you are standing — a raven card's
    penalty, say — survives being shoved around."""
    from server.game.rules import cancel_rest_if_moved_off

    victim = PlayerState(username="p2", color="blue", position="iw_17_9",
                         miss_next_turn=True)
    state = _two_player_state(
        PlayerState(username="p1", color="red", position="ww00_start"), victim)

    victim.position = "iw_17_10"
    assert cancel_rest_if_moved_off(state, BOARD, victim, "iw_17_9") == []
    assert victim.miss_next_turn
