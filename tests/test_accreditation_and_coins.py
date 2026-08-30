"""Accreditation retries on a double, and the size of the Devereux coin pile.

Every double is an even total, so under the plain "odd total = accredited" rule
a double at Queen's House was an automatic failure. It now buys another go.

The coin pile holds exactly one coin per player, and coins handed back (a
served Rack sentence, a fight won by someone already carrying one) return to
it without ever overfilling it.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards_effects import send_to_rack
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, apply
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
QUEENS_HOUSE = BOARD.data.queens_house_space


class FixedRng(Rng):
    """An Rng whose dice come from a scripted list."""

    def __init__(self, rolls: list[list[int]]):
        super().__init__(seed=1)
        self._rolls = list(rolls)

    def roll_dice(self, n: int = 2) -> list[int]:
        return self._rolls.pop(0)


def _on_trial() -> tuple[GameState, PlayerState]:
    p = PlayerState(username="p1", color="red", position=QUEENS_HOUSE,
                    trying_accreditation=True)
    s = GameState(mode="fast", players=[p], turn_order=["p1"])
    s.phase = Phase.TURN_START
    s.turn = TurnContext()
    return s, p


def test_a_double_at_queens_house_earns_another_roll():
    state, player = _on_trial()
    rng = FixedRng([[3, 3]])
    _GLOBAL_RNG.set(rng)

    state, evs = apply(state, "roll_dice", {"username": "p1"}, board=BOARD, rng=rng)

    assert "accreditation_retry" in [e["kind"] for e in evs]
    assert state.phase == Phase.PRE_ROLL
    assert player.trying_accreditation is True
    assert player.accredited is False


def test_a_plain_even_roll_still_fails_the_trial():
    state, player = _on_trial()
    rng = FixedRng([[2, 4]])
    _GLOBAL_RNG.set(rng)

    state, evs = apply(state, "roll_dice", {"username": "p1"}, board=BOARD, rng=rng)

    assert "accreditation_failed" in [e["kind"] for e in evs]
    assert state.phase == Phase.TURN_END
    assert player.accredited is False


def test_three_doubles_on_trial_still_lands_you_in_the_bloody_tower():
    state, player = _on_trial()
    rng = FixedRng([[1, 1], [2, 2], [3, 3]])
    _GLOBAL_RNG.set(rng)

    for _ in range(3):
        state, evs = apply(state, "roll_dice", {"username": "p1"}, board=BOARD, rng=rng)

    assert "three_doubles_bloody_tower" in [e["kind"] for e in evs]
    assert state.player("p1").position == BOARD.data.bloody_tower_space
    assert state.player("p1").status == Status.IMPRISONED


def test_the_coin_pile_is_one_bigger_than_the_table():
    players = [
        PlayerState(username=f"p{i}", color="red", position=BOARD.data.start_space)
        for i in range(3)
    ]
    state = GameState(mode="fast", players=players,
                      turn_order=[p.username for p in players])
    state.tower_draw = []
    rng = Rng(seed=3)
    _GLOBAL_RNG.set(rng)
    # start_game needs a deck to deal from.
    from server.game.cards import Card
    state.tower_draw = [
        Card(id=f"tc-{i}", kind="tower", category="utility", name="Dummy", value=0)
        for i in range(30)
    ]

    state, _ = apply(state, "start_game", {}, board=BOARD, rng=rng)

    assert state.coins_total == 3
    assert state.coins_available == 3


def test_a_forfeited_coin_goes_back_on_the_pile_but_never_overfills_it():
    """The Rack takes the coin into escrow; the pile only gets it back once the
    sentence is served and the forfeit becomes permanent."""
    from server.game.rules import _forfeit_rack_escrow

    p = PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    has_coin=True)
    state = GameState(mode="fast", players=[p], turn_order=["p1"])
    state.coins_total = 2
    state.coins_available = 1

    send_to_rack(state, p, BOARD)
    assert p.has_coin is False
    assert state.coins_available == 1, "held in escrow, not yet back on the pile"

    _forfeit_rack_escrow(state, BOARD, p)
    assert state.coins_available == 2
    assert p.rack_escrow is None

    # A second return with the pile already full is a no-op, not a third coin.
    p.has_coin = True
    send_to_rack(state, p, BOARD)
    _forfeit_rack_escrow(state, BOARD, p)
    assert state.coins_available == 2
