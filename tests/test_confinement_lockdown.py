"""A confined piece cannot be moved — by its owner or by anyone else.

Before this, confinement only stopped a player from taking their *own* turn.
Everything that moved a piece from the outside — a split 7, a Lasso — moved a
prisoner just as happily as a free player, leaving them flagged ``IMPRISONED``
while standing somewhere else entirely. Sanctuary let them teleport out under
their own steam.

The Rack is stricter again: rolling can't shorten it, so a racked player has no
decision to make and their turn is skipped outright rather than parked on a
screen with one usable button.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.cards_effects import EffectError, dispatch as dispatch_effect
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, _split_movable_targets, apply
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _game(**overrides) -> GameState:
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww00_start"),
            PlayerState(username="bob", color="blue", position="ww05"),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=3,
        **overrides,
    )
    game.phase = Phase.TURN_START
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=3)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


@pytest.mark.parametrize("status", [Status.IMPRISONED, Status.TORTURED, Status.RACKED])
def test_confined_player_is_not_a_split_seven_target(status):
    game = _game()
    bob = game.player("bob")
    bob.status = status
    bob.status_turns_remaining = 3
    assert _split_movable_targets(game, BOARD, game.player("alice"), 7) == {}


@pytest.mark.parametrize("status", [Status.IMPRISONED, Status.TORTURED, Status.RACKED])
def test_confined_player_cannot_be_lassoed(status):
    game = _game()
    game.player("bob").position = "ww02"
    game.player("bob").status = status
    game.player("bob").status_turns_remaining = 3
    rng = Rng(seed=3)
    with pytest.raises(EffectError, match="locked up"):
        dispatch_effect(
            "lasso", game, game.player("alice"), {"target": "bob"},
            board=BOARD, rng=rng,
        )
    assert game.player("bob").position == "ww02"


@pytest.mark.parametrize("status", [Status.IMPRISONED, Status.TORTURED, Status.RACKED])
def test_sanctuary_cannot_be_claimed_from_confinement(status):
    game = _game()
    alice = game.player("alice")
    alice.accredited = True
    alice.status = status
    alice.status_turns_remaining = 3
    alice.position = BOARD.data.bloody_tower_space
    rng = Rng(seed=3)
    with pytest.raises(EffectError, match="locked up"):
        dispatch_effect("sanctuary", game, alice, {}, board=BOARD, rng=rng)
    assert alice.position == BOARD.data.bloody_tower_space


def test_sanctuary_card_play_is_rejected_from_confinement():
    """Same rule through the real intent, so the card is not silently spent."""
    game = _game()
    alice = game.player("alice")
    alice.accredited = True
    alice.status = Status.IMPRISONED
    alice.status_turns_remaining = 3
    card = Card(
        id="tower:sanctuary:1", kind="tower", name="Sanctuary",
        category="utility", effect_key="sanctuary",
    )
    alice.hand.append(card)
    from server.game.rules import RuleError
    with pytest.raises(RuleError, match="locked up"):
        _apply(game, "play_card_pre_roll", {"username": "alice", "card_id": card.id})
    # The card is refunded, not burned.
    assert [c.id for c in alice.hand] == [card.id]
    assert game.tower_discard == []


def test_racked_player_turn_is_skipped_without_input():
    game = _game()
    bob = game.player("bob")
    bob.status = Status.RACKED
    bob.status_turns_remaining = 3
    game.phase = Phase.TURN_END

    new, events = _apply(game, "end_turn", {"username": "alice"})
    # Play went straight past bob and back to alice; bob served a turn anyway.
    assert new.current_player().username == "alice"
    assert new.player("bob").status_turns_remaining == 2
    assert "rack_turn_skipped" in [e["kind"] for e in events]


def test_racked_player_is_released_after_serving_the_sentence():
    game = _game()
    bob = game.player("bob")
    bob.status = Status.RACKED
    bob.status_turns_remaining = 1
    game.phase = Phase.TURN_END

    new, events = _apply(game, "end_turn", {"username": "alice"})
    assert new.player("bob").status == Status.NORMAL
    kinds = [e["kind"] for e in events]
    assert "rack_expired" in kinds
    # Release still costs bob the turn — same as rolling while racked.
    assert new.current_player().username == "alice"


def test_all_racked_still_hands_the_turn_over():
    """Degenerate case: nobody can act, so sentences must still tick down."""
    game = _game()
    for name in ("alice", "bob"):
        p = game.player(name)
        p.status = Status.RACKED
        p.status_turns_remaining = 2
    game.phase = Phase.TURN_END

    new, _ = _apply(game, "end_turn", {"username": "alice"})
    assert new.current_player().username == "bob"
    assert new.player("bob").status_turns_remaining == 2
