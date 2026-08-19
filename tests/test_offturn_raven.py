"""A raven card belongs to whoever landed on the square, not to whoever rolled.

A split-7 leg can shove an opponent onto a raven square. The card is then
theirs to reveal and resolve even though it isn't their turn — the engine has
always allowed that, and this pins it down so the client can rely on it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import RuleError, _GLOBAL_RNG, _split_movable_targets, apply
from server.game.state import (
    GameState,
    PendingSplitSeven,
    Phase,
    PlayerState,
    TurnContext,
    Warder,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
BARRACKS = BOARD.data.barracks_space


def _raven_neighbour() -> tuple[str, str]:
    """An inner-ward space and an adjacent raven-trigger square."""
    raven = {s.id for s in BOARD.data.spaces if s.kind == "raven_trigger"}
    for s in BOARD.data.spaces:
        if s.region != "inner_ward" or s.id in raven:
            continue
        hit = next((n for n in s.neighbors if n in raven), None)
        if hit:
            return s.id, hit
    pytest.skip("board has no inner-ward square next to a raven square")


def _setup() -> tuple[GameState, str]:
    """alice rolls a 7 and gives one step to bob, who lands on a raven square."""
    bob_pos, dest = _raven_neighbour()
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww00_start"),
            PlayerState(username="bob", color="blue", position=bob_pos,
                        accredited=True),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=7))
    game.turn.pending_split.movable_targets = _split_movable_targets(
        game, BOARD, game.player("alice"), 7,
    )
    # Two warders out of barracks, so the recall card has to ask which one.
    game.warders = [
        Warder(id="w1", location="iw_warder_scaffold"),
        Warder(id="w2", location="iw_warder_chapel"),
    ]
    game.raven_draw = [Card(id="raven:recall:1", kind="raven", name="recall",
                            effect_key="return_warder_to_barracks")]
    return game, dest


def test_the_target_of_a_split_leg_owns_the_raven_card_they_land_on():
    game, dest = _setup()
    rng = Rng(seed=2)
    _GLOBAL_RNG.set(rng)

    game, _ = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 6, "n_other": 1, "target": "bob",
         "target_destination": dest},
        board=BOARD, rng=rng,
    )

    assert game.player("bob").position == dest
    assert game.phase == Phase.RAVEN_EFFECT
    # The card is bob's, even though it is alice's turn.
    assert game.turn.pending_raven is not None
    assert game.turn.pending_raven.drawer == "bob"
    assert game.active_raven_notice.drawer == "bob"

    # Alice cannot answer for him.
    with pytest.raises(RuleError):
        apply(game, "reveal_raven_notice", {"username": "alice"}, board=BOARD, rng=rng)

    game, _ = apply(game, "reveal_raven_notice", {"username": "bob"}, board=BOARD, rng=rng)
    # Two warders out → the card waits for bob to pick one.
    assert game.phase == Phase.RAVEN_EFFECT
    assert game.turn.pending_raven is not None

    game, evs = apply(
        game, "resolve_raven_effect",
        {"username": "bob", "params": {"warder_id": "w2"}},
        board=BOARD, rng=rng,
    )
    assert "warder_moved" in [e["kind"] for e in evs]
    assert next(w for w in game.warders if w.id == "w2").location == BARRACKS
    assert game.turn.pending_raven is None
    assert game.phase == Phase.TURN_END


def test_the_rollers_leg_survives_the_targets_raven_card():
    """target_first: alice's own 6 steps must not be lost to bob's raven card."""
    game, dest = _setup()
    rng = Rng(seed=2)
    _GLOBAL_RNG.set(rng)

    game, _ = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 6, "n_other": 1, "target": "bob",
         "target_destination": dest, "leg_order": "target_first"},
        board=BOARD, rng=rng,
    )
    # Bob's card holds the table; alice hasn't moved and her leg is parked.
    assert game.phase == Phase.RAVEN_EFFECT
    assert game.player("alice").position == "ww00_start"
    assert game.turn.deferred_split_leg is not None
    assert game.turn.deferred_split_leg.steps == 6

    game, _ = apply(game, "reveal_raven_notice", {"username": "bob"}, board=BOARD, rng=rng)
    game, evs = apply(
        game, "resolve_raven_effect",
        {"username": "bob", "params": {"warder_id": "w2"}},
        board=BOARD, rng=rng,
    )

    # Answering the card releases alice's leg.
    assert game.player("alice").position == "ww06"
    assert game.turn.deferred_split_leg is None
    assert any(
        e["kind"] == "player_moved" and e["payload"]["player"] == "alice" for e in evs
    )


def test_the_targets_leg_survives_the_rollers_raven_card():
    """The mirror case: alice moves first and lands on the raven square."""
    alice_pos, raven_square = _raven_neighbour()
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position=alice_pos,
                        accredited=True),
            PlayerState(username="bob", color="blue", position="ww00_start"),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=7))
    game.turn.pending_split.movable_targets = _split_movable_targets(
        game, BOARD, game.player("alice"), 7,
    )
    game.warders = [
        Warder(id="w1", location="iw_warder_scaffold"),
        Warder(id="w2", location="iw_warder_chapel"),
    ]
    game.raven_draw = [Card(id="raven:recall:1", kind="raven", name="recall",
                            effect_key="return_warder_to_barracks")]
    rng = Rng(seed=2)
    _GLOBAL_RNG.set(rng)

    game, _ = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 1, "n_other": 6, "target": "bob"},
        board=BOARD, rng=rng,
    )
    if game.phase == Phase.CHOOSING_PATH:
        game, _ = apply(
            game, "choose_move_path",
            {"username": "alice", "destination": raven_square},
            board=BOARD, rng=rng,
        )

    assert game.player("alice").position == raven_square
    assert game.phase == Phase.RAVEN_EFFECT
    assert game.turn.pending_raven.drawer == "alice"
    # Bob hasn't been moved yet — his leg is parked behind alice's card.
    assert game.player("bob").position == "ww00_start"
    assert game.turn.deferred_split_leg is not None
    assert game.turn.deferred_split_leg.kind == "target"

    game, _ = apply(game, "reveal_raven_notice", {"username": "alice"}, board=BOARD, rng=rng)
    game, _ = apply(
        game, "resolve_raven_effect",
        {"username": "alice", "params": {"warder_id": "w1"}},
        board=BOARD, rng=rng,
    )

    assert game.player("bob").position == "ww06"
    assert game.turn.deferred_split_leg is None
