"""Tests for the "Change a card" squares (ww41/58/69) and ww75's swap.

Both park a ``PendingCardChange`` prompt and are resolved by the
``change_card`` intent — the player picks what they give up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import RuleError, _resolve_landing, apply, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")

CHANGE_SPACE = "ww41_change"
SWAP_SPACE = "ww75_swap"


def card(cid: str, name: str = "Dummy") -> Card:
    return Card(id=cid, kind="tower", category="utility", name=name, value=0)


def make_state(*players: PlayerState) -> GameState:
    s = GameState(
        mode="fast",
        players=list(players),
        turn_order=[p.username for p in players],
    )
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[players[0].position])
    _GLOBAL_RNG.set(Rng(seed=4))
    return s


def kinds_of(evs) -> list[str]:
    return [e["kind"] for e in evs]


# ---------------------------------------------------------------------------
# Change a card
# ---------------------------------------------------------------------------


def test_change_card_square_parks_a_prompt():
    p1 = PlayerState(username="p1", color="red", position=CHANGE_SPACE,
                     hand=[card("a"), card("b")])
    state = make_state(p1)

    evs = _resolve_landing(state, BOARD, p1)

    assert "card_change_offered" in kinds_of(evs)
    assert state.phase == Phase.CARD_CHANGE
    assert state.turn.pending_card_change is not None
    assert state.turn.pending_card_change.kind == "change"


def test_change_card_discards_the_chosen_card_and_draws_a_replacement():
    p1 = PlayerState(username="p1", color="red", position=CHANGE_SPACE,
                     hand=[card("a"), card("b")])
    state = make_state(p1)
    state.tower_draw = [card("fresh", "Fresh")]
    _resolve_landing(state, BOARD, p1)

    rng = Rng(seed=1)
    state, evs = apply(
        state, "change_card", {"username": "p1", "card_id": "a"}, board=BOARD, rng=rng,
    )
    ev = next(e for e in evs if e["kind"] == "card_changed")

    hand_ids = {c.id for c in state.players[0].hand}
    assert hand_ids == {"b", "fresh"}
    assert ev["payload"]["discarded"] == "a"
    assert ev["payload"]["drawn"] == "fresh"
    assert [c.id for c in state.tower_discard] == ["a"]
    assert state.turn.pending_card_change is None
    assert state.phase == Phase.TURN_END


def test_change_card_rejects_a_card_the_player_does_not_hold():
    p1 = PlayerState(username="p1", color="red", position=CHANGE_SPACE,
                     hand=[card("a")])
    state = make_state(p1)
    state.tower_draw = [card("fresh")]
    _resolve_landing(state, BOARD, p1)

    with pytest.raises(RuleError):
        apply(state, "change_card", {"username": "p1", "card_id": "nope"},
              board=BOARD, rng=Rng(seed=1))


def test_change_card_with_an_empty_hand_just_draws():
    p1 = PlayerState(username="p1", color="red", position=CHANGE_SPACE, hand=[])
    state = make_state(p1)
    state.tower_draw = [card("fresh")]

    evs = _resolve_landing(state, BOARD, p1)

    assert "card_change_skipped" in kinds_of(evs)
    assert [c.id for c in p1.hand] == ["fresh"]
    # No prompt: there was nothing to choose between.
    assert state.phase == Phase.TURN_END
    assert state.turn.pending_card_change is None


# ---------------------------------------------------------------------------
# Swap a card with another player
# ---------------------------------------------------------------------------


def test_swap_square_offers_the_eligible_opponents():
    p1 = PlayerState(username="p1", color="red", position=SWAP_SPACE, hand=[card("a")])
    p2 = PlayerState(username="p2", color="blue", position="ww01", hand=[card("x")])
    p3 = PlayerState(username="p3", color="green", position="ww02", hand=[])
    state = make_state(p1, p2, p3)

    evs = _resolve_landing(state, BOARD, p1)
    ev = next(e for e in evs if e["kind"] == "card_swap_offered")

    # p3 has no cards, so they can't be traded with.
    assert ev["payload"]["candidates"] == ["p2"]
    assert state.phase == Phase.CARD_CHANGE


def test_swap_gives_the_chosen_card_and_takes_a_random_one():
    p1 = PlayerState(username="p1", color="red", position=SWAP_SPACE,
                     hand=[card("mine1"), card("mine2")])
    p2 = PlayerState(username="p2", color="blue", position="ww01",
                     hand=[card("theirs1"), card("theirs2")])
    state = make_state(p1, p2)
    _resolve_landing(state, BOARD, p1)

    state, evs = apply(
        state, "change_card",
        {"username": "p1", "card_id": "mine1", "target": "p2"},
        board=BOARD, rng=Rng(seed=2),
    )
    ev = next(e for e in evs if e["kind"] == "card_swapped")

    mine = {c.id for c in state.players[0].hand}
    theirs = {c.id for c in state.players[1].hand}
    assert ev["payload"]["given"] == "mine1"
    assert ev["payload"]["received"] in {"theirs1", "theirs2"}
    # The chosen card went across; the random one came back.
    assert "mine1" not in mine and "mine1" in theirs
    assert ev["payload"]["received"] in mine
    assert len(mine) == 2 and len(theirs) == 2
    assert state.phase == Phase.TURN_END


def test_swap_rejects_an_opponent_who_was_not_offered():
    p1 = PlayerState(username="p1", color="red", position=SWAP_SPACE, hand=[card("a")])
    p2 = PlayerState(username="p2", color="blue", position="ww01", hand=[card("x")])
    state = make_state(p1, p2)
    _resolve_landing(state, BOARD, p1)

    with pytest.raises(RuleError):
        apply(state, "change_card",
              {"username": "p1", "card_id": "a", "target": "nobody"},
              board=BOARD, rng=Rng(seed=1))


def test_swap_is_skipped_when_no_opponent_holds_cards():
    p1 = PlayerState(username="p1", color="red", position=SWAP_SPACE, hand=[card("a")])
    p2 = PlayerState(username="p2", color="blue", position="ww01", hand=[])
    state = make_state(p1, p2)

    evs = _resolve_landing(state, BOARD, p1)
    ev = next(e for e in evs if e["kind"] == "card_swap_skipped")

    assert ev["payload"]["reason"] == "no_eligible_opponent"
    assert state.phase == Phase.TURN_END
