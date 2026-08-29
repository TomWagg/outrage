"""A raven card is dealt face-down; its effect fires only when it's revealed.

Previously the effect resolved at draw time, so pieces moved before anyone had
seen the card that moved them. Reveal is server state (not a local animation)
so the whole table turns it over together, and only the drawer may do it.
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
RAVEN_SPACE = next(s.id for s in BOARD.data.spaces if s.kind == "raven_trigger")


def make_state() -> tuple[GameState, PlayerState]:
    p1 = PlayerState(username="p1", color="red", position=RAVEN_SPACE)
    p2 = PlayerState(username="p2", color="blue", position="ww01")
    s = GameState(mode="fast", players=[p1, p2], turn_order=["p1", "p2"])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[p1.position])
    s.jewels_available = dict(BOARD.data.initial_jewel_locations)
    _GLOBAL_RNG.set(Rng(seed=5))
    # A summons, so "did the effect fire?" is just "did the piece move?".
    s.raven_draw = [Card(id="raven:go:1", kind="raven", name="go",
                         effect_key="go_to_location",
                         params={"location": "museum"})]
    return s, p1


def test_drawing_parks_the_card_without_firing_it():
    state, player = make_state()

    evs = _resolve_landing(state, BOARD, player)

    assert "raven_card_drawn" in [e["kind"] for e in evs]
    # The card is on the table but face-down: nothing has happened yet.
    assert state.active_raven_notice is not None
    assert state.active_raven_notice.revealed is False
    assert state.turn.pending_raven is not None
    assert state.phase == Phase.RAVEN_EFFECT
    assert player.position == RAVEN_SPACE


def test_revealing_fires_the_effect():
    state, player = make_state()
    # A card with nothing to ask, so the reveal alone settles it.
    state.raven_draw = [Card(id="raven:pecked:1", kind="raven", name="pecked",
                             effect_key="pecked_by_ravens")]
    _resolve_landing(state, BOARD, player)

    state, evs = apply(state, "reveal_raven_notice", {"username": "p1"},
                       board=BOARD, rng=Rng(seed=5))
    kinds = [e["kind"] for e in evs]

    assert "raven_notice_revealed" in kinds
    assert state.active_raven_notice.revealed is True
    assert state.players[0].position == BOARD.data.hospital_space
    assert state.turn.pending_raven is None


def test_a_summons_is_obeyed_only_when_accepted():
    state, player = make_state()
    _resolve_landing(state, BOARD, player)
    state, evs = apply(state, "reveal_raven_notice", {"username": "p1"},
                       board=BOARD, rng=Rng(seed=5))

    # Revealing a Summons asks the question rather than moving the piece.
    assert "raven_needs_input" in [e["kind"] for e in evs]
    assert state.player("p1").position == RAVEN_SPACE

    state, _ = apply(state, "resolve_raven_effect",
                     {"username": "p1", "params": {"accept": True}},
                     board=BOARD, rng=Rng(seed=5))
    assert state.player("p1").position == BOARD.data.museum_space
    assert state.turn.pending_raven is None


def test_a_summons_can_be_refused_at_the_cost_of_a_turn():
    state, player = make_state()
    _resolve_landing(state, BOARD, player)
    state, _ = apply(state, "reveal_raven_notice", {"username": "p1"},
                     board=BOARD, rng=Rng(seed=5))

    state, evs = apply(state, "resolve_raven_effect",
                       {"username": "p1", "params": {"decline": True}},
                       board=BOARD, rng=Rng(seed=5))

    assert "summons_declined" in [e["kind"] for e in evs]
    assert state.player("p1").position == RAVEN_SPACE
    assert state.player("p1").miss_next_turn is True
    assert state.turn.pending_raven is None


def test_only_the_drawer_may_reveal():
    state, player = make_state()
    _resolve_landing(state, BOARD, player)

    with pytest.raises(RuleError):
        apply(state, "reveal_raven_notice", {"username": "p2"},
              board=BOARD, rng=Rng(seed=5))

    # Still face-down, still unresolved.
    assert state.active_raven_notice.revealed is False
    assert state.players[0].position == RAVEN_SPACE


def test_revealing_twice_is_refused():
    state, player = make_state()
    _resolve_landing(state, BOARD, player)
    state, _ = apply(state, "reveal_raven_notice", {"username": "p1"},
                     board=BOARD, rng=Rng(seed=5))

    with pytest.raises(RuleError):
        apply(state, "reveal_raven_notice", {"username": "p1"},
              board=BOARD, rng=Rng(seed=5))


def test_effects_needing_input_wait_for_it_after_the_reveal():
    """Reveal flips the card; the follow-up choice still comes separately."""
    state, player = make_state()
    state.raven_draw = [Card(id="raven:choose:1", kind="raven", name="choose",
                             effect_key="go_to_location",
                             params={"location": "player_choice"})]
    _resolve_landing(state, BOARD, player)

    state, evs = apply(state, "reveal_raven_notice", {"username": "p1"},
                       board=BOARD, rng=Rng(seed=5))

    assert "raven_needs_input" in [e["kind"] for e in evs]
    assert state.active_raven_notice.revealed is True
    # Parked for the choice rather than resolved.
    assert state.turn.pending_raven is not None
    assert state.phase == Phase.RAVEN_EFFECT

    state, _ = apply(state, "resolve_raven_effect",
                     {"username": "p1", "params": {"chosen": "ww23_salt"}},
                     board=BOARD, rng=Rng(seed=5))
    assert state.players[0].position == "ww23_salt"
    assert state.turn.pending_raven is None


def test_the_summons_only_reaches_a_tower():
    """"Go to any tower" means a tower — anywhere that deals you a tower card —
    not any square on the board."""
    state, player = make_state()
    state.raven_draw = [Card(id="raven:choose:2", kind="raven", name="choose",
                             effect_key="go_to_location",
                             params={"location": "player_choice"})]
    _resolve_landing(state, BOARD, player)
    state, _ = apply(state, "reveal_raven_notice", {"username": "p1"},
                     board=BOARD, rng=Rng(seed=5))
    start = state.players[0].position

    state, evs = apply(state, "resolve_raven_effect",
                       {"username": "p1", "params": {"chosen": "ww05"}},
                       board=BOARD, rng=Rng(seed=5))

    assert "raven_effect_failed" in [e["kind"] for e in evs]
    assert state.players[0].position == start
    assert set(BOARD.tower_card_spaces()) >= {"ww23_salt", "iw_museum"}
    assert "ww05" not in BOARD.tower_card_spaces()


def test_a_face_down_card_cannot_be_dismissed():
    """Otherwise the turn strands in RAVEN_EFFECT with nothing left to reveal."""
    state, player = make_state()
    _resolve_landing(state, BOARD, player)

    with pytest.raises(RuleError):
        apply(state, "dismiss_raven_notice", {"username": "p2", "card_id": "raven:go:1"},
              board=BOARD, rng=Rng(seed=5))

    assert state.active_raven_notice is not None
    assert state.turn.pending_raven is not None


def test_resolving_before_revealing_is_refused():
    state, player = make_state()
    state.raven_draw = [Card(id="raven:choose:1", kind="raven", name="choose",
                             effect_key="go_to_location",
                             params={"location": "player_choice"})]
    _resolve_landing(state, BOARD, player)

    with pytest.raises(RuleError):
        apply(state, "resolve_raven_effect",
              {"username": "p1", "params": {"chosen": "ww05"}},
              board=BOARD, rng=Rng(seed=5))
