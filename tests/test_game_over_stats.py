"""End-of-game tallies, folded from the event log.

Also covers the end-of-game hand reveal: once it's over there's nothing left to
protect, and the results screen shows everyone's final hand.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import compute_game_stats, apply, _GLOBAL_RNG
from server.game.state import GameState, LogEntry, Phase, PlayerState, TurnContext
from server.net.redact import redact_game_for_player


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def make_state() -> GameState:
    a = PlayerState(username="alice", color="red", position="ww01")
    b = PlayerState(username="bob", color="blue", position="ww02")
    s = GameState(mode="fast", players=[a, b], turn_order=["alice", "bob"])
    s.phase = Phase.TURN_START
    s.turn = TurnContext()
    _GLOBAL_RNG.set(Rng(seed=1))
    return s


def log(state: GameState, kind: str, **payload) -> None:
    state.log.append(LogEntry(kind=kind, payload=payload))


def test_stats_are_folded_out_of_the_log():
    s = make_state()
    log(s, "turn_start", player="alice")
    log(s, "turn_start", player="bob")
    log(s, "turn_start", player="alice")
    log(s, "dice_rolled", player="alice", roll=[3, 3])   # a double
    log(s, "dice_rolled", player="alice", roll=[2, 5])
    log(s, "player_moved", player="alice", path=["ww01", "ww02", "ww03", "ww04"])
    log(s, "player_moved", player="alice", path=["ww04", "ww05"])
    log(s, "player_moved", player="bob", move_kind="teleport")   # no path
    log(s, "tower_card_drawn", player="alice", card="tc-1")
    log(s, "raven_card_drawn", player="bob", card="rv-1")
    log(s, "jewel_attempt", player="alice", success=False)
    log(s, "jewel_attempt", player="alice", success=True)
    log(s, "jewel_acquired", player="alice", jewel="sword")
    log(s, "coin_picked_up", player="bob")
    log(s, "missed_turn", player="bob")
    log(s, "rack_sender_triggered", player="bob", space="wt_13_11_rack_sender")
    log(s, "combat_resolved", winner="alice", loser="bob", jewels_taken=["orb"])

    st = compute_game_stats(s)
    alice, bob = st["alice"], st["bob"]

    assert alice.turns_taken == 2 and bob.turns_taken == 1
    assert alice.doubles_rolled == 1
    # 3 steps then 1 step; bob's teleport carries no path and doesn't count.
    assert alice.steps_taken == 4
    assert bob.steps_taken == 0
    assert alice.tower_cards_drawn == 1
    assert bob.raven_cards_drawn == 1
    assert alice.jewel_attempts == 2
    # One stolen outright, one taken off bob in the fight.
    assert alice.jewels_collected == 2
    assert bob.coins_picked_up == 1
    assert bob.turns_lost == 1
    assert bob.times_locked_up == 1
    assert alice.fights_won == 1 and bob.fights_lost == 1


def test_stats_are_snapshotted_when_the_game_ends():
    """apply() is the hook, so the tally sees the whole log including the
    events of the very turn that ended the game."""
    s = make_state()
    s.mode = "slow"
    log(s, "turn_start", player="alice")
    log(s, "jewel_acquired", player="alice", jewel="sword")
    # Alice is out with her haul; bob is the last one left, which ends a slow
    # game on the next end_turn.
    alice = s.player("alice")
    alice.escaped = True
    alice.jewels = ["sword"]
    s.current_turn_index = 1
    s.phase = Phase.TURN_END

    s, evs = apply(s, "end_turn", {"username": "bob"}, board=BOARD, rng=Rng(seed=1))

    assert "slow_game_over" in [e["kind"] for e in evs]
    assert s.phase == Phase.GAME_OVER
    assert set(s.final_stats) == {"alice", "bob"}
    assert s.final_stats["alice"].jewels_collected == 1
    assert s.final_stats["alice"].turns_taken == 1


def test_hands_stay_hidden_until_the_game_is_over():
    s = make_state()
    card = Card(id="tc-1", kind="tower", category="weapon", name="Mace", value=2)
    s.player("bob").hand = [card]

    mid = redact_game_for_player(s, "alice")
    bob_mid = next(p for p in mid["players"] if p["username"] == "bob")
    assert bob_mid["hand"] == []
    assert bob_mid["hand_size"] == 1

    s.phase = Phase.GAME_OVER
    end = redact_game_for_player(s, "alice")
    bob_end = next(p for p in end["players"] if p["username"] == "bob")
    assert [c["id"] for c in bob_end["hand"]] == ["tc-1"]
    assert bob_end["hand_size"] == 1
