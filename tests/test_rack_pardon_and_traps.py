"""The Rack's toll, the Shop's dead end, and Binary Disruption's window.

Three rules that all turn on *when* something is settled: the Rack's toll is
only provisional until the sentence is served, the Shop's missed turn has to end
with you off the Shop, and Binary Disruption is played on a roll that has landed
but not yet been walked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.cards_effects import send_to_rack
from server.game.rng import Rng
from server.game.rules import RuleError, _GLOBAL_RNG, _resolve_landing, apply
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
SHOP = BOARD.data.shop_space
SHOP_EXIT = BOARD.data.rules.miss_turn_exit_spaces[SHOP]


class FixedRng(Rng):
    """An Rng whose dice come from a scripted list."""

    def __init__(self, rolls: list[list[int]]):
        super().__init__(seed=1)
        self._rolls = list(rolls)

    def roll_dice(self, n: int = 2) -> list[int]:
        return self._rolls.pop(0)


def card(name: str, effect_key: str | None = None, **kw) -> Card:
    return Card(id=f"tower:{name}:1", kind="tower", name=name,
                category=kw.pop("category", "utility"), effect_key=effect_key, **kw)


def make_state(players: list[PlayerState], phase: Phase = Phase.TURN_START) -> GameState:
    _GLOBAL_RNG.set(Rng(seed=6))
    g = GameState(mode="fast", players=players,
                  turn_order=[p.username for p in players], phase=phase)
    g.turn = TurnContext()
    g.jewels_available = dict(BOARD.data.initial_jewel_locations)
    return g


# ===========================================================================
# The Rack takes everything, but only provisionally
# ===========================================================================


def test_the_rack_takes_the_jewels_as_well_as_the_coin():
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=True, jewels=["orb", "sword"],
                    hand=[card("Mace", category="weapon", value=2)])
    state = make_state([p])

    send_to_rack(state, p, BOARD)

    assert p.jewels == []
    assert p.has_coin is False
    assert len(p.hand) == 1, "the coin was the toll, so the hand is spared"
    assert sorted(p.rack_escrow.jewels) == ["orb", "sword"]


def test_a_rack_pardon_hands_back_everything_the_rack_took():
    """The card's whole purpose: the sentence costs you nothing."""
    pardon = card("Rack Pardon", "rack_pardon")
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=True, jewels=["orb"], hand=[pardon])
    state = make_state([p])
    send_to_rack(state, p, BOARD)
    assert p.has_coin is False and p.jewels == []

    state, evs = apply(state, "play_card_pre_roll",
                       {"username": "p1", "card_id": pardon.id},
                       board=BOARD, rng=Rng(seed=1))
    p = state.player("p1")

    assert p.has_coin is True, "the coin comes back"
    assert p.jewels == ["orb"], "so do the jewels"
    assert p.status == Status.NORMAL
    assert p.rack_escrow is None
    assert p.position != BOARD.data.rack_space, "and you walk out of the cell"
    ev = next(e for e in evs if e["kind"] == "pardoned")
    assert ev["payload"]["coin_returned"] is True
    assert ev["payload"]["jewels_returned"] == ["orb"]


def test_the_rack_cannot_confiscate_the_card_that_undoes_it():
    """Taking the Pardon along with the rest of the hand would make it a card
    that can never be played at the one moment it exists for."""
    pardon = card("Rack Pardon", "rack_pardon")
    spare = [card("Mace", category="weapon", value=2), card("File", category="burglary", value=2)]
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=False, hand=[pardon, *spare])
    state = make_state([p])

    evs = send_to_rack(state, p, BOARD)

    assert [c.id for c in p.hand] == [pardon.id]
    assert sorted(c.id for c in p.rack_escrow.cards) == sorted(c.id for c in spare)
    assert next(e for e in evs if e["kind"] == "sent_to_rack")["payload"]["cards_taken"] == 2


def test_a_rack_pardon_hands_back_a_confiscated_hand():
    pardon = card("Rack Pardon", "rack_pardon")
    spare = [card("Mace", category="weapon", value=2), card("File", category="burglary", value=2)]
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=False, hand=[pardon, *spare])
    state = make_state([p])
    send_to_rack(state, p, BOARD)

    state, _ = apply(state, "play_card_pre_roll",
                     {"username": "p1", "card_id": pardon.id},
                     board=BOARD, rng=Rng(seed=1))

    assert sorted(c.id for c in state.player("p1").hand) == sorted(c.id for c in spare)
    assert [c.id for c in state.tower_discard] == [pardon.id], \
        "only the spent Pardon is discarded"


def test_serving_the_sentence_makes_the_loss_permanent():
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=True, jewels=["orb"])
    state = make_state([p])
    state.coins_available = 0
    send_to_rack(state, p, BOARD)
    p.status_turns_remaining = 1

    state.phase = Phase.TURN_START
    state, evs = apply(state, "end_turn", {"username": "p1"}, board=BOARD, rng=Rng(seed=1))
    p = state.player("p1")

    assert p.status == Status.NORMAL
    assert p.rack_escrow is None
    assert p.has_coin is False
    assert state.coins_available == 1, "the coin is back on the Devereux pile"
    assert state.jewels_available["orb"] == BOARD.data.initial_jewel_locations["orb"], \
        "the jewel is back on its square for somebody else to steal"
    assert "rack_forfeit" in [e["kind"] for e in evs]


def test_a_pardon_after_the_sentence_is_served_is_refused():
    """There is nothing left to pardon, and nothing left in escrow to refund."""
    pardon = card("Rack Pardon", "rack_pardon")
    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    hand=[pardon])
    state = make_state([p])

    with pytest.raises(RuleError):
        apply(state, "play_card_pre_roll", {"username": "p1", "card_id": pardon.id},
              board=BOARD, rng=Rng(seed=1))


# ===========================================================================
# The Shop is a dead end whose only neighbour sends you back
# ===========================================================================


def test_the_shop_only_leads_back_to_the_square_that_sent_you_there():
    """The premise of the fix, asserted so a board edit can't quietly undo it."""
    assert BOARD.space(SHOP).neighbors == [SHOP_EXIT]
    sender = BOARD.space(SHOP_EXIT)
    assert sender.action is not None
    assert sender.action.params.get("destination_kind") == "shop"


@pytest.mark.parametrize("intent", ["roll_dice", "end_turn"])
def test_serving_the_shop_turn_puts_you_outside_it(intent: str):
    """Staying put would leave exactly one legal move: back into the Shop."""
    p = PlayerState(username="p1", color="red", position=SHOP, miss_next_turn=True)
    state = make_state([p, PlayerState(username="p2", color="blue",
                                       position=BOARD.data.start_space)])
    rng = FixedRng([[2, 3]])
    _GLOBAL_RNG.set(rng)

    state, evs = apply(state, intent, {"username": "p1"}, board=BOARD, rng=rng)
    p = state.player("p1")

    assert "missed_turn" in [e["kind"] for e in evs]
    assert p.miss_next_turn is False
    assert p.position == SHOP_EXIT
    # Stepping out must not re-trigger the square's "go to the Shop" action.
    assert "sent_to_space" not in [e["kind"] for e in evs]


def test_a_bench_does_not_eject_you():
    """Only squares that would trap you are listed; a bench's neighbour is an
    ordinary square, so serving the turn there leaves the piece alone."""
    bench = BOARD.data.bench_space_ids[0]
    assert bench not in BOARD.data.rules.miss_turn_exit_spaces
    p = PlayerState(username="p1", color="red", position=bench, miss_next_turn=True)
    state = make_state([p, PlayerState(username="p2", color="blue",
                                       position=BOARD.data.start_space)])

    state, _ = apply(state, "end_turn", {"username": "p1"}, board=BOARD, rng=Rng(seed=1))
    assert state.player("p1").position == bench


# ===========================================================================
# Binary Disruption: after the roll, and only as the dice fell
# ===========================================================================


def _disruption_state(pos_a: str = "ww10", pos_b: str = "ww12"):
    disruption = card("Binary Disruption", "binary_disruption", category="custom")
    a = PlayerState(username="alice", color="red", position=pos_a,
                    hand=[disruption], accredited=True)
    b = PlayerState(username="bob", color="blue", position=pos_b, accredited=True)
    return make_state([a, b]), disruption


def test_binary_disruption_cannot_be_played_before_the_roll():
    """It deals out the dice you can see; before the roll there are none."""
    state, disruption = _disruption_state()
    with pytest.raises(RuleError, match="after you roll"):
        apply(state, "play_card_pre_roll",
              {"username": "alice", "card_id": disruption.id},
              board=BOARD, rng=Rng(seed=1))


def test_binary_disruption_splits_the_roll_exactly_as_it_fell():
    state, disruption = _disruption_state()
    rng = FixedRng([[5, 3]])
    _GLOBAL_RNG.set(rng)
    state, _ = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)
    assert state.phase == Phase.CHOOSING_PATH, "the window has to exist to be used"

    state, evs = apply(state, "play_card_pre_roll",
                       {"username": "alice", "card_id": disruption.id},
                       board=BOARD, rng=rng)

    assert state.phase == Phase.SPLIT_SEVEN_ASSIGN
    split = state.turn.pending_split
    assert split.source == "binary_disruption"
    assert split.total == 8
    assert split.allowed_legs == [3, 5], "one die each — not any split of 8"
    assert "binary_disruption_played" in [e["kind"] for e in evs]


def test_binary_disruption_refuses_a_split_the_dice_do_not_allow():
    """A 5 and a 3 cannot be dealt out as 7 and 1."""
    state, disruption = _disruption_state()
    rng = FixedRng([[5, 3]])
    _GLOBAL_RNG.set(rng)
    state, _ = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)
    state, _ = apply(state, "play_card_pre_roll",
                     {"username": "alice", "card_id": disruption.id},
                     board=BOARD, rng=rng)

    with pytest.raises(RuleError, match="can only be split"):
        apply(state, "assign_split_seven",
              {"username": "alice", "n_self": 7, "n_other": 1, "target": "bob"},
              board=BOARD, rng=rng)


def test_binary_disruption_accepts_a_split_the_dice_do_allow():
    state, disruption = _disruption_state()
    rng = FixedRng([[5, 3]])
    _GLOBAL_RNG.set(rng)
    state, _ = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)
    state, _ = apply(state, "play_card_pre_roll",
                     {"username": "alice", "card_id": disruption.id},
                     board=BOARD, rng=rng)
    legs = state.turn.pending_split.movable_targets["bob"]

    n_other = legs[0]
    state, evs = apply(state, "assign_split_seven",
                       {"username": "alice", "n_self": 8 - n_other,
                        "n_other": n_other, "target": "bob"},
                       board=BOARD, rng=rng)
    assert "split_assigned" in [e["kind"] for e in evs]


def test_binary_disruption_must_hand_a_die_to_somebody():
    """It rearranges the roll between two players; keeping both is not on offer."""
    state, disruption = _disruption_state()
    rng = FixedRng([[5, 3]])
    _GLOBAL_RNG.set(rng)
    state, _ = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)
    state, _ = apply(state, "play_card_pre_roll",
                     {"username": "alice", "card_id": disruption.id},
                     board=BOARD, rng=rng)

    with pytest.raises(RuleError, match="hand one of the two dice"):
        apply(state, "assign_split_seven",
              {"username": "alice", "n_self": 8, "n_other": 0},
              board=BOARD, rng=rng)


def test_binary_disruption_stays_in_hand_when_nobody_can_be_moved():
    """Spending the card for no effect is the failure mode worth refusing."""
    state, disruption = _disruption_state()
    # Lock the only opponent up, so neither die can move them.
    state.player("bob").status = Status.IMPRISONED
    state.player("bob").status_turns_remaining = 3
    rng = FixedRng([[5, 3]])
    _GLOBAL_RNG.set(rng)
    state, _ = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)

    with pytest.raises(RuleError):
        apply(state, "play_card_pre_roll",
              {"username": "alice", "card_id": disruption.id},
              board=BOARD, rng=rng)
    assert any(c.id == disruption.id for c in state.player("alice").hand)


def test_a_natural_seven_still_splits_any_way_at_all():
    """The dice restriction belongs to the card, not to every split."""
    state, _ = _disruption_state()
    state.player("alice").hand = []
    rng = FixedRng([[4, 3]])
    _GLOBAL_RNG.set(rng)
    state, evs = apply(state, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)

    assert state.phase == Phase.SPLIT_SEVEN_ASSIGN
    assert state.turn.pending_split.allowed_legs == [1, 2, 3, 4, 5, 6]
    assert state.turn.pending_split.source == "seven"
