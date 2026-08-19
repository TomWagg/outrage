"""Regression tests for split-7 assignment (``assign_split_seven`` intent).

These cover the wall-walk / forward-only branch because it's the only
configuration the turn engine enters on roll==7 from the start square, and
are sufficient to catch a previous ``TypeError: _ev() got multiple values for
argument 'kind'`` regression in the target-movement event.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, RuleError, _split_movable_targets, apply
from server.game.state import (
    GameState,
    PendingSplitSeven,
    Phase,
    PlayerState,
    TurnContext,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _arm_split(game: GameState, roller: str = "alice") -> GameState:
    """Fill in the movability map the roll handler would normally compute.

    ``assign_split_seven`` refuses to hand steps to a player who has no legal
    move of that size, so a hand-built ``PendingSplitSeven`` needs this.
    """
    split = game.turn.pending_split
    assert split is not None
    split.movable_targets = _split_movable_targets(
        game, BOARD, game.player(roller), split.total,
    )
    return game


def _make_split_state(alice_pos: str = "ww00_start", bob_pos: str = "ww05") -> GameState:
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position=alice_pos),
            PlayerState(username="bob", color="blue", position=bob_pos),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=7))
    return _arm_split(game)


def test_split_seven_moves_both_players_on_wall_walk():
    game = _make_split_state()
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    new, events = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 3, "n_other": 4, "target": "bob"},
        board=BOARD, rng=rng,
    )
    assert new.phase == Phase.TURN_END
    assert new.players[0].position == "ww03"
    assert new.players[1].position == "ww09_st_thomas"

    kinds = [e["kind"] for e in events]
    assert "split_assigned" in kinds
    moves = [e for e in events if e["kind"] == "player_moved"]
    assert [m["payload"]["player"] for m in moves] == ["alice", "bob"]
    # The target's move is tagged so the log can distinguish it.
    assert moves[1]["payload"].get("move_kind") == "split_seven"


def test_split_seven_target_leg_resumes_after_path_choice():
    """When the self-leg has multiple destinations, the second leg is deferred.
    After ``choose_move_path`` commits the chosen self-destination, the
    target's leg must still run.
    """
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="iw_5_3", accredited=True),
            PlayerState(username="bob", color="blue", position="ww05"),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=7))
    _arm_split(game)
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)

    game, _ = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 2, "n_other": 5, "target": "bob"},
        board=BOARD, rng=rng,
    )
    assert game.phase == Phase.CHOOSING_PATH
    assert game.turn.pending_move is not None
    assert game.turn.pending_move.split_target == "bob"
    assert game.turn.pending_move.remaining_steps == 5

    game, events = apply(
        game, "choose_move_path",
        {"username": "alice", "destination": "iw_3_3"},
        board=BOARD, rng=rng,
    )
    assert game.phase == Phase.TURN_END
    assert game.players[0].position == "iw_3_3"
    assert game.players[1].position != "ww05"  # bob actually moved
    moves = [e for e in events if e["kind"] == "player_moved"]
    assert [m["payload"]["player"] for m in moves] == ["alice", "bob"]
    assert moves[1]["payload"].get("move_kind") == "split_seven"


def test_split_seven_target_first_leg_order():
    """leg_order='target_first' moves the target before the roller."""
    game = _make_split_state()
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    new, events = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 3, "n_other": 4, "target": "bob",
         "leg_order": "target_first"},
        board=BOARD, rng=rng,
    )
    assert new.phase == Phase.TURN_END
    moves = [e for e in events if e["kind"] == "player_moved"]
    assert [m["payload"]["player"] for m in moves] == ["bob", "alice"]
    assert moves[0]["payload"].get("move_kind") == "split_seven"


def test_split_seven_roller_chooses_target_destination():
    """When the target (bob) is in the inner ward with multiple reachable
    destinations, the engine enters CHOOSING_PATH with ``is_for_target=True``
    so the roller picks exactly where bob lands — including full landing effects.
    """
    # Alice is on the wall walk (linear → forced-single self-leg).
    # Bob is already in the inner ward and accredited — multiple destinations.
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww00_start"),
            PlayerState(username="bob", color="blue", position="iw_5_3", accredited=True),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.SPLIT_SEVEN_ASSIGN
    game.turn = TurnContext(roll=[3, 4], pending_split=PendingSplitSeven(total=7))
    _arm_split(game)
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)

    # alice takes 5 for herself (wall-walk, forced-single → auto-resolves),
    # bob gets 2 steps from iw_5_3 → multiple inner-ward destinations.
    game, _ev1 = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 5, "n_other": 2, "target": "bob"},
        board=BOARD, rng=rng,
    )
    assert game.phase == Phase.CHOOSING_PATH, f"Expected CHOOSING_PATH, got {game.phase}"
    pm = game.turn.pending_move
    assert pm is not None
    assert pm.is_for_target is True
    assert pm.target_for_split == "bob"
    # Alice already committed her leg.
    assert game.player("alice").position == "ww05"
    # Multiple options for bob must be offered.
    dests = list(pm.destinations.keys())
    assert len(dests) > 1, f"Expected multiple destinations for bob, got: {dests}"

    # Roller picks where bob lands.
    chosen = dests[0]
    game, _ev2 = apply(
        game, "choose_move_path",
        {"username": "alice", "destination": chosen},
        board=BOARD, rng=rng,
    )
    assert game.phase in (Phase.TURN_END, Phase.RAVEN_EFFECT, Phase.JEWEL_ATTEMPT)
    assert game.player("bob").position == chosen
    # A player_moved event for bob must appear.
    moves = [e for e in _ev2 if e["kind"] == "player_moved"]
    assert any(m["payload"]["player"] == "bob" for m in moves), "No player_moved for bob"


def test_split_seven_all_to_self_no_target_needed():
    # Place bob off the self-path so pass-through combat doesn't force a
    # CHOOSING_PATH prompt; this test is about the "all to self" branch.
    game = _make_split_state(bob_pos="ww20")
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    new, events = apply(
        game, "assign_split_seven",
        {"username": "alice", "n_self": 7, "n_other": 0},
        board=BOARD, rng=rng,
    )
    assert new.phase == Phase.TURN_END
    assert new.players[0].position == "ww07"
    assert new.players[1].position == "ww20"  # unchanged
    assert new.turn.pending_split is None


def test_a_boxed_in_opponent_is_not_offered_as_a_split_target():
    """An un-accredited piece on Queen's House is stuck: the wall walk is
    forward-only and dead-ends there, so no leg size moves them anywhere."""
    game = _make_split_state(bob_pos=BOARD.data.queens_house_space)
    assert game.turn.pending_split is not None
    assert game.turn.pending_split.movable_targets == {}

    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    try:
        apply(
            game, "assign_split_seven",
            {"username": "alice", "n_self": 3, "n_other": 4, "target": "bob"},
            board=BOARD, rng=rng,
        )
    except RuleError as exc:
        assert "cannot be moved" in str(exc)
    else:
        raise AssertionError("expected the split to be refused")


def test_rolling_a_seven_with_nobody_movable_skips_the_split():
    """The roller takes the whole 7 rather than being asked to split it."""
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww00_start"),
            PlayerState(username="bob", color="blue",
                        position=BOARD.data.queens_house_space),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    game.phase = Phase.TURN_START
    rng = _SevenRng()
    _GLOBAL_RNG.set(rng)

    game, events = apply(game, "roll_dice", {"username": "alice"}, board=BOARD, rng=rng)

    kinds = [e["kind"] for e in events]
    assert "split_unavailable" in kinds
    assert "split_assign_required" not in kinds
    assert game.phase != Phase.SPLIT_SEVEN_ASSIGN
    assert game.player("alice").position == "ww07"


class _SevenRng(Rng):
    def __init__(self):
        super().__init__(seed=1)

    def roll_dice(self, n: int = 2) -> list[int]:
        return [3, 4]
