"""Cards that only make sense once the dice are down.

A Tower Pass is worth buying an extra turn with when you can see you need one;
Sanctuary is a retreat you take after finding out where you landed. Confession
was worse than awkward — it requires ``TORTURED``, which only a *landing* can
apply, so gating card play to the pre-roll phases made it unplayable at the one
moment it exists for.
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
BOWYER = "ww47_bowyer"


def _card(name: str, effect: str) -> Card:
    return Card(
        id=f"tower:{effect}:1", kind="tower", name=name,
        category="utility", effect_key=effect,
    )


def _game(*, phase: Phase = Phase.TURN_END) -> GameState:
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww05", accredited=True),
            PlayerState(username="bob", color="blue", position="ww20", accredited=True),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=11,
    )
    game.phase = phase
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=11)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_tower_pass_buys_an_extra_turn_after_moving():
    game = _game()
    card = _card("Tower Pass", "tower_pass")
    game.player("alice").hand.append(card)
    new, _ = _apply(game, "play_card_pre_roll", {
        "username": "alice", "card_id": card.id, "params": {"mode": "extra_turn"},
    })
    assert new.turn.extra_turns_queued == 1


def test_sanctuary_retreats_to_the_chapel_after_moving():
    game = _game()
    card = _card("Sanctuary", "sanctuary")
    game.player("alice").hand.append(card)
    new, _ = _apply(game, "play_card_pre_roll", {
        "username": "alice", "card_id": card.id,
    })
    assert new.player("alice").position == BOARD.data.chapel_royal_space


def test_confession_is_playable_the_moment_the_bowyer_tower_locks_you_in():
    game = _game()
    alice = game.player("alice")
    alice.position = BOWYER
    alice.status = Status.TORTURED
    alice.status_turns_remaining = 3
    card = _card("Confession", "confession")
    alice.hand.append(card)

    new, events = _apply(game, "play_card_pre_roll", {
        "username": "alice", "card_id": card.id, "params": {"target": "bob"},
    })
    assert new.player("alice").status == Status.NORMAL
    assert new.player("alice").position == "ww20"
    assert new.player("bob").status == Status.TORTURED
    assert new.player("bob").position == BOWYER
    assert "framed" in [e["kind"] for e in events]


def test_confession_cannot_frame_someone_already_locked_up():
    game = _game()
    alice = game.player("alice")
    alice.position = BOWYER
    alice.status = Status.TORTURED
    alice.status_turns_remaining = 3
    bob = game.player("bob")
    bob.status = Status.RACKED
    bob.status_turns_remaining = 2
    card = _card("Confession", "confession")
    alice.hand.append(card)

    with pytest.raises(RuleError, match="already locked up"):
        _apply(game, "play_card_pre_roll", {
            "username": "alice", "card_id": card.id, "params": {"target": "bob"},
        })
    # Refunded, not burned.
    assert [c.id for c in alice.hand] == [card.id]


def test_roll_dependent_cards_are_still_refused_after_the_roll():
    game = _game()
    card = _card("Binary Disruption", "binary_disruption")
    game.player("alice").hand.append(card)
    with pytest.raises(RuleError, match="before you roll"):
        _apply(game, "play_card_pre_roll", {"username": "alice", "card_id": card.id})
    assert [c.id for c in game.player("alice").hand] == [card.id]


def test_rack_pardon_can_be_played_off_turn():
    """A racked player's turn is skipped, so the card must not need their turn.

    Without this, the Rack skip and the Rack Pardon cancel each other out: the
    only phase the card was legal in is the one a racked player never reaches.
    """
    game = _game(phase=Phase.TURN_START)
    bob = game.player("bob")
    bob.status = Status.RACKED
    bob.status_turns_remaining = 3
    bob.position = BOARD.data.rack_space
    card = _card("Rack Pardon", "rack_pardon")
    bob.hand.append(card)

    # It's alice's turn, not bob's.
    assert game.current_player().username == "alice"
    new, events = _apply(game, "play_card_pre_roll", {
        "username": "bob", "card_id": card.id,
    })
    assert new.player("bob").status == Status.NORMAL
    assert "pardoned" in [e["kind"] for e in events]
    # alice's turn is untouched.
    assert new.current_player().username == "alice"
    assert new.phase == Phase.TURN_START
    assert new.turn.cards_played_this_turn == []


def test_off_turn_play_is_refused_for_cards_that_are_not_self_rescue():
    game = _game(phase=Phase.TURN_START)
    bob = game.player("bob")
    bob.status = Status.RACKED
    bob.status_turns_remaining = 3
    card = _card("Tower Pass", "tower_pass")
    bob.hand.append(card)
    with pytest.raises(RuleError, match="Not bob's turn"):
        _apply(game, "play_card_pre_roll", {
            "username": "bob", "card_id": card.id, "params": {"mode": "extra_turn"},
        })
