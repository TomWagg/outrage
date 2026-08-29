"""Stopped and Searched, and the two things a Disguise is for.

The search used to resolve itself the instant the card was turned over: it went
straight to the forfeit branch and confiscated everything, so the Disguise sat
unasked-for in the victim's hand. And a Disguise played against a search was
never actually spent.

The card itself reads "escape prison (not torture or rack), OR slip past a
Yeoman Warder" — only the second half was implemented.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.cards_effects import EffectError, dispatch
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG
from server.game.state import (
    GameState, Phase, PlayerState, Status, TurnContext,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def disguise_card() -> Card:
    return Card(id="tc-disguise", kind="tower", category="utility",
                name="Disguise", value=0, effect_key="disguise")


def _state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.RAVEN_EFFECT
    s.turn = TurnContext()
    _GLOBAL_RNG.set(Rng(seed=2))
    return s


def _run(state, player, params):
    return dispatch("stopped_and_searched", state, player, params,
                    board=BOARD, rng=Rng(seed=2))


# ---------------------------------------------------------------------------
# Stopped and Searched
# ---------------------------------------------------------------------------


def test_a_search_with_a_jewel_asks_before_it_takes():
    player = PlayerState(username="p1", color="red", position="iw_17_9",
                         jewels=["sword"], hand=[disguise_card()])
    state = _state(player)

    _, evs = _run(state, player, {})

    assert [e["kind"] for e in evs] == ["raven_needs_input"]
    assert evs[0]["payload"]["input_kind"] == "stopped_and_searched"
    assert evs[0]["payload"]["has_disguise"] is True
    # Nothing taken yet.
    assert player.jewels == ["sword"]
    assert player.status == Status.NORMAL


def test_showing_a_disguise_spends_it_and_saves_the_haul():
    player = PlayerState(username="p1", color="red", position="iw_17_9",
                         jewels=["sword"], hand=[disguise_card()])
    state = _state(player)

    _, evs = _run(state, player, {"play_disguise": True})

    assert "disguise_shown" in [e["kind"] for e in evs]
    assert player.jewels == ["sword"]
    assert player.status == Status.NORMAL
    # The card was never being consumed, so the Disguise was free.
    assert player.hand == []
    assert [c.id for c in state.tower_discard] == ["tc-disguise"]


def test_showing_a_disguise_you_do_not_have_is_refused():
    player = PlayerState(username="p1", color="red", position="iw_17_9",
                         jewels=["sword"])
    state = _state(player)

    with pytest.raises(EffectError, match="Disguise"):
        _run(state, player, {"play_disguise": True})


def test_declining_forfeits_the_haul_and_the_weapons():
    weapon = Card(id="w1", kind="tower", category="weapon", name="Dagger", value=3)
    keeper = Card(id="u1", kind="tower", category="utility", name="Thing", value=0)
    player = PlayerState(username="p1", color="red", position="iw_17_9",
                         jewels=["sword"], hand=[weapon, keeper])
    state = _state(player)

    _, evs = _run(state, player, {"play_disguise": False})

    assert "stopped_forfeit" in [e["kind"] for e in evs]
    assert player.jewels == []
    assert state.jewels_available["sword"] == BOARD.data.initial_jewel_locations["sword"]
    assert [c.id for c in player.hand] == ["u1"]
    assert player.position == BOARD.data.bloody_tower_space
    assert player.status == Status.IMPRISONED


def test_a_search_with_no_jewel_is_nothing_at_all():
    player = PlayerState(username="p1", color="red", position="iw_17_9")
    state = _state(player)

    _, evs = _run(state, player, {})

    assert [e["kind"] for e in evs] == ["stopped_and_searched"]
    assert evs[0]["payload"]["carried_jewels"] == 0


# ---------------------------------------------------------------------------
# The Disguise itself
# ---------------------------------------------------------------------------


def test_a_disguise_walks_you_out_of_prison():
    """Its own rules text promises this; only the warder half was built."""
    player = PlayerState(username="p1", color="red",
                         position=BOARD.data.bloody_tower_space,
                         status=Status.IMPRISONED, status_turns_remaining=3)
    state = _state(player)

    _, evs = dispatch("disguise", state, player, {}, board=BOARD, rng=Rng(seed=2))

    assert player.status == Status.NORMAL
    assert player.status_turns_remaining == 0
    assert next(e for e in evs if e["kind"] == "disguise_played")["payload"]["via"] == "prison"


@pytest.mark.parametrize("status", [Status.TORTURED, Status.RACKED])
def test_a_disguise_does_not_answer_the_rack_or_the_questioning(status):
    player = PlayerState(username="p1", color="red", position="iw_17_9",
                         status=status, status_turns_remaining=3)
    state = _state(player)

    dispatch("disguise", state, player, {}, board=BOARD, rng=Rng(seed=2))

    assert player.status == status
    assert state.turn.disguise_used is True


def test_a_free_disguise_still_opens_the_warder_posts():
    player = PlayerState(username="p1", color="red", position="iw_17_9")
    state = _state(player)

    dispatch("disguise", state, player, {}, board=BOARD, rng=Rng(seed=2))

    assert state.turn.disguise_used is True
