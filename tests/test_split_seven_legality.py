"""Split-7 leg legality: no stopping short, and landing effects hit the right player.

Two rules are enforced here that the original split-7 implementation got wrong:

* A leg is walked in full. On a normal roll a player may stop early at any
  opponent on their path and fight; on a split leg the *length* was already a
  free choice, so allowing an early stop as well lets a leg of 4 be spent as a
  leg of 1.
* A landing effect belongs to whoever landed. The extra-turn square credited
  ``turn.extra_turns_queued`` unconditionally, which handed the bonus to the
  roller whenever they'd pushed an opponent onto it.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, _split_movable_targets, apply
from server.game.state import (
    GameState,
    PendingSplitSeven,
    Phase,
    PlayerState,
    TurnContext,
)

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _state(positions: dict[str, str], *, total: int = 7) -> GameState:
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username=n, color=c, position=p)
            for (n, p), c in zip(positions.items(), ["red", "blue", "green", "gold"])
        ],
        turn_order=list(positions),
        current_turn_index=0,
        seed=7,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=total))
    game.turn.pending_split.movable_targets = _split_movable_targets(
        game, BOARD, game.player(list(positions)[0]), total,
    )
    return game


def _apply(game: GameState, intent: str, payload: dict):
    rng = Rng(seed=7)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_split_leg_cannot_stop_short_at_an_opponent():
    # carl sits one square ahead of bob. bob is handed 6 steps, so the only
    # legal destination is 6 along — not carl's square.
    game = _state({"alice": "ww00_start", "bob": "ww02", "carl": "ww03"})
    new, _ = _apply(game, "assign_split_seven", {
        "username": "alice", "n_self": 1, "n_other": 6, "target": "bob",
    })
    assert new.player("bob").position == "ww08"
    # Auto-committed: with the early stop gone there is nothing left to choose.
    assert new.phase == Phase.TURN_END


def test_roller_split_leg_cannot_stop_short_at_an_opponent():
    # Same rule from the roller's side: alice takes 4 with bob standing on the
    # square 1 along, so ww01 must not be offered as a destination.
    game = _state({"alice": "ww00_start", "bob": "ww01", "carl": "ww40"})
    new, _ = _apply(game, "assign_split_seven", {
        "username": "alice", "n_self": 3, "n_other": 4, "target": "carl",
    })
    assert new.player("alice").position == "ww03"


def test_extra_turn_square_credits_the_player_who_landed_on_it():
    # alice pushes bob onto the extra-turn square. The turn is bob's to take.
    game = _state({"alice": "ww00_start", "bob": "ww48"})
    new, events = _apply(game, "assign_split_seven", {
        "username": "alice", "n_self": 6, "n_other": 1, "target": "bob",
    })
    assert new.player("bob").position == "ww49_extra_turn"
    granted = [e for e in events if e["kind"] == "extra_turn_granted"]
    assert [e["payload"]["player"] for e in granted] == ["bob"]
    assert new.player("bob").extra_turns_pending == 1
    # The roller gets nothing.
    assert new.turn.extra_turns_queued == 0

    # Play passes to bob, who now owes himself a second turn.
    new, _ = _apply(new, "end_turn", {"username": "alice"})
    assert new.current_player().username == "bob"
    assert new.player("bob").extra_turns_pending == 0
    assert new.turn.extra_turns_queued == 1

    # ...and taking it keeps the turn with bob rather than passing it on.
    new, events = _apply(new, "end_turn", {"username": "bob"})
    assert new.current_player().username == "bob"
    assert [e["kind"] for e in events] == ["extra_turn_used"]
