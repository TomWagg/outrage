"""Slow-mode scoring, and the two ways a slow game ends.

Only *banked* jewels score: a jewel is banked by carrying it out through the
Cradle Tower, and until then it can be taken off you in a fight. That is what
decides when the game is over —

- somebody's banked pile beats what any rival could still reach even by taking
  every jewel that is left ("clinched"), or
- there is nothing left to bank, and the ranking settles it.

Ranking sorts by banked count -> top jewel value -> sum of jewel values, with a
deterministic username tie-break.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, _slow_ranking, apply
from server.game.state import (
    GameState, PendingMove, Phase, PlayerState, TurnContext,
)


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
EXIT = "out_18_m3_cradle_escape"


def _slow_state(players: list[PlayerState]) -> GameState:
    s = GameState(
        mode="slow",
        players=players,
        turn_order=[p.username for p in players],
        current_turn_index=0,
        seed=1,
    )
    s.phase = Phase.TURN_END
    s.turn = TurnContext()
    return s


def _walk_out(state: GameState, username: str):
    """Step the named player through the Cradle Tower from one square away."""
    p = state.player(username)
    p.position = "out_17_m3"
    p.accredited = True
    p.has_coin = True
    state.current_turn_index = state.turn_order.index(username)
    state.phase = Phase.CHOOSING_PATH
    state.turn.visited_this_turn = ["out_17_m3"]
    state.turn.pending_move = PendingMove(
        steps=1, destinations={EXIT: ["out_17_m3", EXIT]},
    )
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    return apply(state, "choose_move_path",
                 {"username": username, "destination": EXIT},
                 board=BOARD, rng=rng)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_slow_ranking_count_then_top_value():
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    banked_jewels=["orb", "sword"]),      # 2 banked, top 2
        PlayerState(username="bob", color="blue", position="ww00_start",
                    banked_jewels=["crown_st_edward", "sword"]),  # 2 banked, top 5
        PlayerState(username="carol", color="green", position="ww00_start",
                    banked_jewels=["sceptre"]),           # 1 banked, top 3
    ]
    state = _slow_state(players)
    ranking = _slow_ranking(state)
    assert [r["username"] for r in ranking] == ["bob", "alice", "carol"]
    assert ranking[0]["jewel_count"] == 2
    assert ranking[0]["jewel_top_value"] == 5


def test_slow_ranking_ignores_jewels_still_being_carried():
    """A pocketful of jewels that never left the Tower scores nothing."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    banked_jewels=["sword"]),
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=["crown_st_edward", "orb", "sceptre"]),
    ]
    ranking = _slow_ranking(_slow_state(players))
    assert [r["username"] for r in ranking] == ["alice", "bob"]
    assert ranking[1]["jewel_count"] == 0
    assert ranking[1]["carrying"] == ["crown_st_edward", "orb", "sceptre"]


def test_slow_ranking_deterministic_username_tiebreak():
    players = [
        PlayerState(username="zack", color="red", position="ww00_start"),
        PlayerState(username="alice", color="blue", position="ww00_start"),
    ]
    state = _slow_state(players)
    assert [r["username"] for r in _slow_ranking(state)] == ["alice", "zack"]


# ---------------------------------------------------------------------------
# Ending the game
# ---------------------------------------------------------------------------


def test_banking_a_third_jewel_clinches_the_game():
    """Three of five in the hideout: two is all anybody else can still reach."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    banked_jewels=["orb", "sceptre"], jewels=["sword"]),
        PlayerState(username="bob", color="blue", position="ww00_start"),
    ]
    state = _slow_state(players)

    state, events = _walk_out(state, "alice")

    assert state.phase == Phase.GAME_OVER
    ev = next(e for e in events if e["kind"] == "slow_game_over")
    assert ev["payload"]["winner"] == "alice"
    assert ev["payload"]["reason"] == "majority_clinched"


def test_banking_a_second_jewel_does_not_end_it():
    """Two banked against three still out there is a lead, not a win."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    banked_jewels=["orb"], jewels=["sword"]),
        PlayerState(username="bob", color="blue", position="ww00_start"),
    ]
    state = _slow_state(players)

    state, events = _walk_out(state, "alice")

    assert state.phase != Phase.GAME_OVER
    assert not any(e["kind"] == "slow_game_over" for e in events)
    assert state.player("alice").banked_jewels == ["orb", "sword"]


def test_game_ends_when_the_last_jewel_is_banked():
    """Nothing left to bank, so the ranking settles it — and a 2-2-1 split is
    broken by who carried out the better jewel."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start",
                    banked_jewels=["orb", "sword"]),                  # top 2
        PlayerState(username="bob", color="blue", position="ww00_start",
                    banked_jewels=["crown_st_edward"],
                    jewels=["sceptre"]),                              # will be 2, top 5
        PlayerState(username="carol", color="green", position="ww00_start",
                    banked_jewels=["crown_prince_of_wales"]),
    ]
    state = _slow_state(players)

    state, events = _walk_out(state, "bob")

    assert state.phase == Phase.GAME_OVER
    ev = next(e for e in events if e["kind"] == "slow_game_over")
    assert ev["payload"]["reason"] == "all_jewels_banked"
    assert ev["payload"]["winner"] == "bob"
    assert [r["username"] for r in ev["payload"]["ranking"]] == ["bob", "alice", "carol"]


def test_carrying_every_jewel_does_not_end_the_game():
    """The bug this rule replaced: stealing the fifth jewel used to end the
    game on the spot, even though every one of them could still be lost in a
    fight before it reached the hideout."""
    players = [
        PlayerState(username="alice", color="red", position="ww00_start"),
        PlayerState(username="bob", color="blue", position="ww00_start",
                    jewels=["crown_prince_of_wales", "orb", "sword",
                            "crown_st_edward", "sceptre"]),
    ]
    state = _slow_state(players)
    state.jewels_available = {}
    state.loose_jewels = {}
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)

    state, events = apply(state, "end_turn", {"username": "alice"},
                          board=BOARD, rng=rng)

    assert state.phase != Phase.GAME_OVER
    assert not any(e["kind"] == "slow_game_over" for e in events)


# ---------------------------------------------------------------------------
# What the exit actually costs
# ---------------------------------------------------------------------------


def test_walking_out_surrenders_everything_and_redeals():
    """Jewels to the hideout, coin back to the Devereux pile, hand shuffled
    into the deck and replaced, and you start again from the start square."""
    from server.game.cards import Card

    alice = PlayerState(username="alice", color="red", position="ww00_start",
                        jewels=["orb"])
    bob = PlayerState(username="bob", color="blue", position="ww00_start")
    state = _slow_state([alice, bob])
    state.coins_total = 2
    state.coins_available = 0          # both coins are out with the players
    bob.has_coin = True
    alice.hand = [
        Card(id=f"old-{i}", kind="tower", category="utility", name="Old", value=0)
        for i in range(3)
    ]
    state.tower_draw = [
        Card(id=f"new-{i}", kind="tower", category="utility", name="New", value=0)
        for i in range(20)
    ]

    state, events = _walk_out(state, "alice")
    alice = state.player("alice")

    assert alice.banked_jewels == ["orb"] and alice.jewels == []
    assert not alice.has_coin
    assert state.coins_available == 1                      # returned to Devereux
    assert alice.position == BOARD.data.start_space
    assert alice.accredited                                # the paperwork stands
    assert len(alice.hand) == 6                            # a fresh opening hand
    assert not any(c.id.startswith("old-") for c in alice.hand)
    # The surrendered cards went back into the deck, not out of the game.
    assert {c.id for c in state.tower_draw} >= {"old-0", "old-1", "old-2"}
    ev = next(e for e in events if e["kind"] == "jewels_banked")
    assert ev["payload"]["cards_surrendered"] == 3
    assert ev["payload"]["cards_dealt"] == 6


def test_walking_out_empty_handed_just_redeals():
    """No jewel required — this is how a player stripped on the Rack gets a
    hand back."""
    from server.game.cards import Card

    alice = PlayerState(username="alice", color="red", position="ww00_start")
    bob = PlayerState(username="bob", color="blue", position="ww00_start")
    state = _slow_state([alice, bob])
    state.tower_draw = [
        Card(id=f"new-{i}", kind="tower", category="utility", name="New", value=0)
        for i in range(20)
    ]

    state, events = _walk_out(state, "alice")
    alice = state.player("alice")

    assert alice.banked_jewels == []
    assert len(alice.hand) == 6
    assert state.phase != Phase.GAME_OVER
    assert "jewels_banked" in [e["kind"] for e in events]


def test_being_shoved_onto_the_exit_does_not_bank_for_you():
    """Cashing in costs your whole hand and can win the game outright, so it
    has to be your own decision — not something another player's seven does to
    you."""
    from server.game.cards import Card
    from server.game.rules import _resolve_landing

    alice = PlayerState(username="alice", color="red", position=EXIT,
                        accredited=True, has_coin=True,
                        banked_jewels=["orb", "sceptre"], jewels=["sword"])
    alice.hand = [Card(id="c1", kind="tower", category="utility", name="X", value=0)]
    state = _slow_state([alice, PlayerState(username="bob", color="blue",
                                            position="ww00_start")])
    _GLOBAL_RNG.set(Rng(seed=0))

    evs = _resolve_landing(state, BOARD, alice, own_move=False)

    assert "jewels_banked" not in [e["kind"] for e in evs]
    assert alice.jewels == ["sword"]
    assert alice.has_coin
    assert alice.position == EXIT
    assert state.phase != Phase.GAME_OVER
