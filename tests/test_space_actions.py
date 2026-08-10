"""Tests for the per-space ``action`` keys dispatched on landing.

``extra_turn`` / ``go_back_by_roll`` / ``go_to_and_accredit`` are covered in
test_landing_and_no_revisit.py; this file covers the teleport + miss-turn
family that used to fall through to ``unhandled_space_action``.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _resolve_landing, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def make_player(pos: str) -> PlayerState:
    return PlayerState(username="p1", color="red", position=pos)


def make_state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[player.position])
    _GLOBAL_RNG.set(Rng(seed=7))
    return s


def kinds_of(evs) -> list[str]:
    return [e["kind"] for e in evs]


# ---------------------------------------------------------------------------
# go_to
# ---------------------------------------------------------------------------


def test_go_to_queens_house_teleports_and_starts_accreditation():
    player = make_player("ww16_go_qh")
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)

    assert "sent_to_space" in kinds_of(evs)
    assert player.position == BOARD.data.queens_house_space
    # The destination's own landing effect ran: Queen's House starts the trial.
    assert "trying_accreditation" in kinds_of(evs)
    assert player.trying_accreditation
    assert BOARD.data.queens_house_space in state.turn.visited_this_turn


def test_go_to_by_explicit_space_id_reaches_broad_arrow_and_runs_its_action():
    player = make_player("ww45_go_broad_arrow")
    player.hand = []
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)

    assert player.position == "ww29_broad_arrow"
    # Broad Arrow's own action fired at the destination.
    assert "weapons_surrendered" in kinds_of(evs)


def test_go_to_shop_teleports():
    player = make_player("ww26_go_shop")
    state = make_state(player)

    _resolve_landing(state, BOARD, player)

    assert player.position == BOARD.data.shop_space


# ---------------------------------------------------------------------------
# surrender_weapons (Broad Arrow Tower)
# ---------------------------------------------------------------------------


def weapon(cid: str) -> Card:
    return Card(id=cid, kind="tower", category="weapon", name=f"Weapon {cid}", value=5)


def tool(cid: str) -> Card:
    return Card(id=cid, kind="tower", category="burglary", name=f"Tool {cid}", value=2)


def test_broad_arrow_takes_weapons_and_leaves_everything_else():
    player = make_player("ww29_broad_arrow")
    player.hand = [weapon("w1"), weapon("w2"), tool("t1")]
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "weapons_surrendered")

    assert ev["payload"]["count"] == 2
    assert [c.id for c in player.hand] == ["t1"]
    assert {c.id for c in state.tower_discard} == {"w1", "w2"}


def test_broad_arrow_with_no_weapons_is_a_no_op():
    player = make_player("ww29_broad_arrow")
    player.hand = [tool("t1")]
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "weapons_surrendered")

    assert ev["payload"]["count"] == 0
    assert [c.id for c in player.hand] == ["t1"]


def test_broad_arrow_does_not_also_hand_out_a_tower_card():
    """It's in tower_card_draw_exception_space_ids — the frisking is the effect."""
    player = make_player("ww29_broad_arrow")
    state = make_state(player)
    state.tower_draw = [tool("fresh")]

    evs = _resolve_landing(state, BOARD, player)

    assert "tower_card_drawn" not in kinds_of(evs)
    assert player.hand == []


# ---------------------------------------------------------------------------
# miss_turn
# ---------------------------------------------------------------------------


def test_questioned_by_a_guard_costs_the_next_turn():
    player = make_player("ww60_questioned")
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "miss_turn_queued")

    assert player.miss_next_turn
    assert ev["payload"]["label"] == "Questioned by a guard"
    assert state.phase == Phase.TURN_END


def test_plain_miss_turn_squares_also_set_the_flag():
    for space_id in ("ww21_miss", "ww28_miss"):
        player = make_player(space_id)
        state = make_state(player)

        _resolve_landing(state, BOARD, player)

        assert player.miss_next_turn, space_id


# ---------------------------------------------------------------------------
# go_to_and_miss_turn
# ---------------------------------------------------------------------------


def test_buy_a_guidebook_sends_you_to_the_shop_and_costs_a_turn():
    player = make_player("ww04_guidebook")
    state = make_state(player)

    evs = _resolve_landing(state, BOARD, player)
    ev = next(e for e in evs if e["kind"] == "sent_to_space")

    assert player.position == BOARD.data.shop_space
    assert player.miss_next_turn
    assert ev["payload"]["misses_turn"] is True


# ---------------------------------------------------------------------------
# No action square is left unhandled
# ---------------------------------------------------------------------------


# Action keys the engine has no branch for. Every key board.json uses is now
# implemented, so this is empty — a new key added to the board without a
# handler will fail the assertion below.
KNOWN_UNIMPLEMENTED: set[str] = set()


def test_no_unexpected_unhandled_action_keys():
    """Landing on an action square must not silently fall through the dispatch.

    ``unhandled_space_action`` is the engine's own "I don't know this key"
    signal; anything emitting it is a dead square on the board.
    """
    unhandled: list[tuple[str, str]] = []
    for sp in BOARD.data.spaces:
        if sp.action is None:
            continue
        player = make_player(sp.id)
        state = make_state(player)
        # Give go_back_by_roll a roll to work with.
        state.turn.roll = [2, 1]
        evs = _resolve_landing(state, BOARD, player)
        if "unhandled_space_action" in kinds_of(evs):
            unhandled.append((sp.id, sp.action.key))
    assert {key for _, key in unhandled} == KNOWN_UNIMPLEMENTED, unhandled
