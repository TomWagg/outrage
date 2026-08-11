"""Calling a Yeoman Warder to a post must pick an *empty* post.

A post holds one warder; stacking a second there hides it and makes the block
impossible to clear, so the choice is restricted to free posts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards_effects import EffectError, dispatch, free_warder_posts
from server.game.rng import Rng
from server.game.state import GameState, Phase, PlayerState, TurnContext, Warder


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
BARRACKS = BOARD.data.barracks_space
POSTS = {wp.id: wp.space_id for wp in BOARD.data.warder_posts}


def make_state(warder_locations: list[str]) -> GameState:
    player = PlayerState(username="p1", color="red", position="ww01")
    s = GameState(mode="fast", players=[player], turn_order=["p1"])
    s.phase = Phase.RAVEN_EFFECT
    s.turn = TurnContext()
    s.warders = [Warder(id=f"w{i}", location=loc)
                 for i, loc in enumerate(warder_locations)]
    return s


def call_warder(state: GameState, **params):
    return dispatch("call_warder_to_post", state, state.players[0], params,
                    board=BOARD, rng=Rng(seed=1))


def test_free_posts_excludes_manned_ones():
    state = make_state([POSTS["scaffold"], BARRACKS])
    free = free_warder_posts(state, BOARD)

    assert "scaffold" not in free
    assert set(free) == set(POSTS) - {"scaffold"}


def test_choosing_an_occupied_post_is_refused():
    state = make_state([POSTS["scaffold"], BARRACKS])

    with pytest.raises(EffectError):
        call_warder(state, post="chooser", chosen_post="scaffold")


def test_choosing_a_free_post_moves_a_warder_out_of_the_barracks():
    state = make_state([POSTS["scaffold"], BARRACKS])

    _, evs = call_warder(state, post="chooser", chosen_post="chapel")

    assert [e["kind"] for e in evs] == ["warder_moved"]
    assert state.warders[1].location == POSTS["chapel"]


def test_the_prompt_only_offers_free_posts():
    state = make_state([POSTS["scaffold"], POSTS["chapel"], BARRACKS])

    _, evs = call_warder(state, post="chooser")
    ev = next(e for e in evs if e["kind"] == "raven_needs_input")

    assert set(ev["payload"]["posts"]) == {"waterloo", "lanthorn"}


def test_a_single_free_post_is_taken_without_asking():
    manned = [POSTS[p] for p in ("scaffold", "chapel", "waterloo")]
    state = make_state([*manned, BARRACKS])

    _, evs = call_warder(state, post="chooser")

    assert [e["kind"] for e in evs] == ["warder_moved"]
    assert state.warders[3].location == POSTS["lanthorn"]


def test_no_free_posts_is_a_clean_no_op():
    manned = [POSTS[p] for p in POSTS]
    state = make_state([*manned, BARRACKS])

    _, evs = call_warder(state, post="chooser")

    assert [e["kind"] for e in evs] == ["no_free_warder_posts"]


def test_a_fixed_post_card_does_nothing_when_that_post_is_manned():
    state = make_state([POSTS["scaffold"], BARRACKS])

    _, evs = call_warder(state, post="scaffold")

    assert [e["kind"] for e in evs] == ["warder_post_occupied"]
    # The barracks warder stays put.
    assert state.warders[1].location == BARRACKS
