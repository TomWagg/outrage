"""Reaching the exit is enough; you don't have to land on it exactly.

Every other destination has to be hit with the roll to the step. The Cradle
Tower is a door, not a square you have to pace out — a player one step from it
who rolls a 12 was being told they couldn't leave, which is the opposite of what
a big roll should mean.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.movement import compute_destinations
from server.game.state import PlayerState

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
EXIT = "out_18_m3_cradle_escape"


def _player(pos: str, **kw) -> PlayerState:
    return PlayerState(username="a", color="red", position=pos, accredited=True, **kw)


def _dests(pos: str, steps: int, **kw):
    # A coin is the price of the door, and only a coin-holder gets the
    # overshoot courtesy — so these reach tests carry one unless they say not.
    kw.setdefault("has_coin", True)
    p = _player(pos, **kw)
    return compute_destinations(BOARD, pos, steps, p, visited_this_turn=[pos]).destinations


def test_exit_is_offered_on_an_exact_roll():
    assert EXIT in _dests("out_17_m3", 1)


def test_exit_is_offered_on_an_overshooting_roll():
    # One step from the door with the biggest roll in the game: still allowed out.
    assert EXIT in _dests("out_17_m3", 12)


def test_exit_is_offered_from_further_back_once_the_roll_covers_it():
    # out_13_m3 is five steps from the door.
    assert EXIT not in _dests("out_13_m3", 2)
    assert EXIT in _dests("out_13_m3", 5)
    assert EXIT in _dests("out_13_m3", 11)


def test_exit_is_offered_without_a_jewel():
    """No jewel needed: walking out empty-handed to be dealt a new hand is a
    legitimate play, so the door is offered to any coin-holder who reaches it."""
    assert EXIT in _dests("out_17_m3", 6, jewels=[])


def test_the_overshoot_courtesy_needs_a_coin():
    """Without a coin the Cradle Tower is an ordinary square: you may stand on
    it with an exact roll, but a big roll no longer stops you at the door."""
    assert EXIT in _dests("out_17_m3", 1, has_coin=False)
    assert EXIT not in _dests("out_17_m3", 6, has_coin=False)


def test_reaching_the_exit_is_never_auto_committed():
    """Leaving the Tower is a decision, so it must always be offered as a choice."""
    p = _player("out_17_m3", has_coin=True, jewels=["sword"])
    opts = compute_destinations(BOARD, "out_17_m3", 3, p, visited_this_turn=["out_17_m3"])
    assert EXIT in opts.destinations
    assert opts.forced_single is False


def test_un_accredited_player_is_not_offered_the_exit():
    """The route runs through the wards, which are closed until they're signed in."""
    p = PlayerState(username="a", color="red", position="ww05", accredited=False,
                    has_coin=True)
    opts = compute_destinations(BOARD, "ww05", 6, p, visited_this_turn=["ww05"])
    assert EXIT not in opts.destinations


# ---------------------------------------------------------------------------
# Through the real intent, since the offered path is deliberately shorter than
# the roll and _commit_move has to accept that.
# ---------------------------------------------------------------------------

from server.game.rng import Rng                                    # noqa: E402
from server.game.rules import _GLOBAL_RNG, apply                   # noqa: E402
from server.game.state import GameState, PendingMove, Phase        # noqa: E402


def _game(*, has_coin: bool, jewels: list[str]) -> GameState:
    p = PlayerState(
        username="a", color="red", position="out_17_m3", accredited=True,
        has_coin=has_coin, jewels=list(jewels),
    )
    game = GameState(
        mode="fast", players=[p, PlayerState(username="b", color="blue", position="ww05")],
        turn_order=["a", "b"], current_turn_index=0, seed=13,
    )
    game.phase = Phase.CHOOSING_PATH
    game.turn.roll = [6, 6]
    game.turn.visited_this_turn = ["out_17_m3"]
    game.turn.pending_move = PendingMove(
        steps=12, destinations={EXIT: ["out_17_m3", EXIT]},
    )
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=13)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_walking_out_on_a_twelve_from_one_square_away_wins():
    game = _game(has_coin=True, jewels=["sword"])
    new, events = _apply(game, "choose_move_path", {"username": "a", "destination": EXIT})
    a = new.player("a")
    assert a.banked_jewels == ["sword"]
    assert new.winner == "a"
    assert "fast_win" in [e["kind"] for e in events]


def test_stopping_on_the_exit_with_no_coin_is_just_a_square():
    """The coin is the price of the door. Without one you are simply standing
    on the last square of the south row."""
    game = _game(has_coin=False, jewels=[])
    new, events = _apply(game, "choose_move_path", {"username": "a", "destination": EXIT})
    assert new.player("a").position == EXIT
    assert new.winner is None
    assert "jewels_banked" not in [e["kind"] for e in events]
