"""Pass-through combat and White-Tower combat block.

Rule: a roller may choose to stop at *any* enemy-occupied square on their
move-path and engage combat there; doing so ends the turn at that square
(combat → TURN_END). Combat is also forbidden inside the White Tower.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import RuleError, _GLOBAL_RNG, apply
from server.game.state import (
    GameState,
    PendingMove,
    Phase,
    PlayerState,
    TurnContext,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def dagger(owner: str) -> Card:
    return Card(id=f"w-{owner}", kind="tower", category="weapon", name="Dagger", value=3)


def _state(
    alice_pos: str, bob_pos: str, alice_accredited: bool = True,
    alice_armed: bool = True,
) -> GameState:
    # Both sides carry a weapon by default: an unarmed player can't start a
    # fight at all, so without one these tests would be exercising the refusal
    # rather than the pass-through rule.
    alice = PlayerState(username="alice", color="red", position=alice_pos,
                        accredited=alice_accredited)
    if alice_armed:
        alice.hand = [dagger("alice")]
    bob = PlayerState(username="bob", color="blue", position=bob_pos,
                      accredited=True, hand=[dagger("bob")])
    s = GameState(
        mode="fast",
        players=[alice, bob],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    s.phase = Phase.PRE_ROLL
    s.turn = TurnContext(visited_this_turn=[alice_pos])
    return s


def test_enemy_on_path_shows_as_stop_destination():
    """Rolling a forward-only wall-walk move that passes bob at ww05 should
    expose ww05 (bob's square) as an additional pickable destination."""
    from server.game.movement import compute_destinations

    player = PlayerState(username="alice", color="red", position="ww00_start")
    opts = compute_destinations(
        BOARD, "ww00_start", 7, player,
        other_player_positions={"ww05"},
        visited_this_turn=["ww00_start"],
    )
    assert "ww07" in opts.destinations
    assert "ww05" in opts.destinations  # early-stop target for combat
    path = opts.destinations["ww05"]
    assert path[0] == "ww00_start"
    assert path[-1] == "ww05"
    assert len(path) == 6  # 5 steps (ww00→ww05)
    assert "ww07" in opts.intermediate_enemies
    assert opts.forced_single is False  # combat choice prevents auto-commit


def test_roll_with_enemy_on_path_enters_choosing_path():
    game = _state("ww00_start", "ww05")
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    # Seed a deterministic roll of 3+4=7 via dice override path. Instead, call
    # the underlying movement-phase helper through a dice payload.
    rng2 = Rng(seed=0)
    _GLOBAL_RNG.set(rng2)
    # Simulate a roll directly by patching the roll to 7 via assign-movement:
    # use apply() with the roll intent once it's our turn. Easier: call the
    # internal helper directly.
    from server.game.rules import _enter_movement_phase

    game.phase = Phase.MOVING
    evs = _enter_movement_phase(game, BOARD, game.players[0], 7)
    assert game.phase == Phase.CHOOSING_PATH
    dest_keys = set(game.turn.pending_move.destinations.keys())
    assert {"ww05", "ww07"}.issubset(dest_keys)


def test_landing_on_enemy_emits_combat_available_and_end_turn_is_valid():
    game = _state("ww00_start", "ww05")
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    # Put alice on CHOOSING_PATH with ww05 as a destination option.
    from server.game.rules import _enter_movement_phase
    game.phase = Phase.MOVING
    _enter_movement_phase(game, BOARD, game.players[0], 5)
    assert game.phase == Phase.CHOOSING_PATH
    # Pick bob's square.
    game, events = apply(
        game, "choose_move_path",
        {"username": "alice", "destination": "ww05"},
        board=BOARD, rng=rng,
    )
    assert game.players[0].position == "ww05"
    assert game.phase == Phase.TURN_END
    # A combat_available hint was emitted.
    kinds = [e["kind"] for e in events]
    assert "combat_available" in kinds
    combat_event = next(e for e in events if e["kind"] == "combat_available")
    assert combat_event["payload"]["targets"] == ["bob"]

    # The player may still initiate combat despite phase=TURN_END.
    game2, evs2 = apply(
        game, "initiate_combat",
        {"username": "alice", "target": "bob"},
        board=BOARD, rng=rng,
    )
    assert game2.phase == Phase.COMBAT


def test_no_combat_inside_white_tower():
    game = _state("wt_11_2", "wt_11_2")
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    game.phase = Phase.TURN_END
    with pytest.raises(RuleError, match="White Tower"):
        apply(
            game, "initiate_combat",
            {"username": "alice", "target": "bob"},
            board=BOARD, rng=rng,
        )


def test_an_unarmed_player_cannot_start_a_fight():
    """Declaring combat bare-handed is a guaranteed loss dressed up as a
    choice — the engine refuses it."""
    game = _state("ww05", "ww05", alice_armed=False)
    rng = Rng(seed=1)
    _GLOBAL_RNG.set(rng)
    game.phase = Phase.TURN_END
    with pytest.raises(RuleError, match="weapon"):
        apply(game, "initiate_combat", {"username": "alice", "target": "bob"},
              board=BOARD, rng=rng)


def test_an_unarmed_player_is_not_offered_a_stop_at_an_enemy():
    """No weapon, no fight — so the enemy's square stops being a destination
    and the move is walked in full."""
    from server.game.rules import _enter_movement_phase

    game = _state("ww00_start", "ww05", alice_armed=False)
    _GLOBAL_RNG.set(Rng(seed=0))
    game.phase = Phase.MOVING
    _enter_movement_phase(game, BOARD, game.players[0], 7)

    dests = set((game.turn.pending_move.destinations if game.turn.pending_move else {}))
    assert "ww05" not in dests


def test_no_stop_offered_at_an_enemy_inside_the_white_tower():
    """Combat is forbidden in the White Tower, so stopping short on somebody
    standing there is not a real choice — it was only ever a way to dodge the
    square you would otherwise have landed on."""
    from server.game.rules import _enter_movement_phase

    # wt_12_11 is one square before the Rack Sender on the White Tower chain.
    game = _state("wt_11_10_crown_ste", "wt_12_11")
    _GLOBAL_RNG.set(Rng(seed=0))
    game.phase = Phase.MOVING
    _enter_movement_phase(game, BOARD, game.players[0], 3)

    dests = set((game.turn.pending_move.destinations if game.turn.pending_move else {}))
    assert "wt_12_11" not in dests
    # Walked in full: three steps lands on the Rack Sender, like it or not.
    assert dests == {"wt_13_11_rack_sender"} or game.phase == Phase.TURN_END
