"""Trading n cards for n - 1 instead of rolling.

The lost card is the whole cost of the deal, which makes a one-card redraw a
pure forfeit — refused rather than honoured. Handed-in cards go to the discard
pile so they can come back around on a reshuffle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, RuleError, apply
from server.game.state import GameState, Phase, PlayerState, Status

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _tower(n: int) -> Card:
    return Card(
        id=f"tower:dagger:{n}", kind="tower", name="Dagger",
        category="weapon", value=n,
    )


def _game(hand: int = 4, deck: int = 6) -> GameState:
    alice = PlayerState(username="alice", color="red", position="ww05")
    alice.hand = [_tower(i) for i in range(1, hand + 1)]
    game = GameState(
        mode="fast",
        players=[alice, PlayerState(username="bob", color="blue", position="ww20")],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=4,
    )
    game.tower_draw = [_tower(100 + i) for i in range(deck)]
    game.phase = Phase.TURN_START
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=4)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_three_cards_in_two_out():
    game = _game()
    ids = [c.id for c in game.player("alice").hand[:3]]
    new, events = _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})

    alice = new.player("alice")
    assert len(alice.hand) == 3           # 4 - 3 + 2
    assert not [c for c in alice.hand if c.id in ids]
    assert [c.id for c in new.tower_discard] == ids
    ev = next(e for e in events if e["kind"] == "cards_redrawn")
    assert ev["payload"]["given_count"] == 3
    assert ev["payload"]["received_count"] == 2
    # The turn is spent standing still.
    assert new.player("alice").position == "ww05"
    assert new.phase == Phase.TURN_END


def test_two_cards_yields_exactly_one():
    game = _game()
    ids = [c.id for c in game.player("alice").hand[:2]]
    new, _ = _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})
    assert len(new.player("alice").hand) == 3   # 4 - 2 + 1


def test_a_single_card_is_refused():
    game = _game()
    ids = [game.player("alice").hand[0].id]
    with pytest.raises(RuleError, match="at least 2"):
        _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})
    assert len(game.player("alice").hand) == 4


def test_duplicate_selection_is_refused():
    game = _game()
    cid = game.player("alice").hand[0].id
    with pytest.raises(RuleError, match="Duplicate"):
        _apply(game, "redraw_cards", {"username": "alice", "card_ids": [cid, cid]})
    assert len(game.player("alice").hand) == 4


def test_cards_not_in_hand_are_refused():
    game = _game()
    ids = [game.player("alice").hand[0].id, "tower:nonsense:9"]
    with pytest.raises(RuleError, match="Not in your hand"):
        _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})
    assert len(game.player("alice").hand) == 4


def test_confined_player_cannot_trade():
    game = _game()
    alice = game.player("alice")
    alice.status = Status.IMPRISONED
    alice.status_turns_remaining = 3
    ids = [c.id for c in alice.hand[:2]]
    with pytest.raises(RuleError, match="locked up"):
        _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})


def test_an_exhausted_deck_reports_the_shortfall():
    """The discard pile is reshuffled in, so only a truly empty table falls short."""
    game = _game(hand=3, deck=0)
    ids = [c.id for c in game.player("alice").hand]
    new, events = _apply(game, "redraw_cards", {"username": "alice", "card_ids": ids})
    ev = next(e for e in events if e["kind"] == "cards_redrawn")
    # The three cards just discarded are the only stock, so both draws land.
    assert ev["payload"]["received_count"] == 2
    assert ev["payload"]["short_by"] == 0
    assert len(new.player("alice").hand) == 2
