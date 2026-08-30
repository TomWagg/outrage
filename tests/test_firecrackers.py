"""Firecrackers: marks all White Tower occupants; escape-on-landing clears
the flag; ending a turn still inside the White Tower sends the player to the
Rack with the standard Rack entry penalty.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.cards_effects import dispatch as dispatch_effect
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, apply
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _fc_card(pid: str = "fc1") -> Card:
    return Card(
        id=pid,
        kind="tower",
        name="Firecrackers",
        category="custom",
        effect_key="firecrackers",
    )


def _state(alice_pos: str, bob_pos: str, alice_hand=None, alice_coin=False) -> GameState:
    s = GameState(
        mode="fast",
        players=[
            PlayerState(
                username="alice", color="red", position=alice_pos,
                accredited=True, has_coin=alice_coin,
                hand=list(alice_hand or []),
            ),
            PlayerState(username="bob", color="blue", position=bob_pos, accredited=True),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=1,
    )
    s.phase = Phase.PRE_ROLL
    s.turn = TurnContext(visited_this_turn=[alice_pos])
    return s


def test_firecrackers_marks_all_white_tower_players():
    game = _state("wt_11_2", "wt_10_2")
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    dispatch_effect("firecrackers", game, game.players[0], {}, board=BOARD, rng=rng)
    assert set(game.firecrackers_affected) == {"alice", "bob"}


def test_firecrackers_refuses_outside_white_tower():
    from server.game.cards_effects import EffectError

    game = _state("ww00_start", "wt_10_2")
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)
    with pytest.raises(EffectError, match="White Tower"):
        dispatch_effect("firecrackers", game, game.players[0], {}, board=BOARD, rng=rng)


def test_firecrackers_end_turn_racks_still_inside_coin_forfeit():
    """Player with a coin who fails to leave the White Tower loses the coin
    (not the hand) and is sent to the Rack with RACKED status."""
    card = Card(id="tc1", kind="tower", name="Sword", category="weapon", value=5)
    game = _state("wt_11_2", "ww10", alice_hand=[card], alice_coin=True)
    game.firecrackers_affected = ["alice"]
    game.phase = Phase.TURN_END
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)

    game, events = apply(game, "end_turn", {"username": "alice"}, board=BOARD, rng=rng)

    alice = game.player("alice")
    assert alice.position == BOARD.data.rack_space
    assert alice.status == Status.RACKED
    assert alice.status_turns_remaining == 3
    assert alice.has_coin is False
    assert len(alice.hand) == 1  # coin was paid, hand spared
    assert "firecrackers_racked" in [e["kind"] for e in events]
    sent = next(e for e in events if e["kind"] == "sent_to_rack")
    assert sent["payload"]["penalty"] == "coin"
    assert sent["payload"]["cause"] == "firecrackers"
    assert alice.rack_escrow.coin is True
    assert "alice" not in game.firecrackers_affected


def test_firecrackers_end_turn_racks_no_coin_discards_hand():
    card1 = Card(id="tc1", kind="tower", name="Mace", category="weapon", value=2)
    card2 = Card(id="tc2", kind="tower", name="File", category="burglary", value=2)
    game = _state("wt_11_2", "ww10", alice_hand=[card1, card2], alice_coin=False)
    game.firecrackers_affected = ["alice"]
    game.phase = Phase.TURN_END
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)

    game, events = apply(game, "end_turn", {"username": "alice"}, board=BOARD, rng=rng)

    alice = game.player("alice")
    assert alice.position == BOARD.data.rack_space
    assert alice.status == Status.RACKED
    assert alice.hand == []
    assert len(alice.rack_escrow.cards) == 2
    assert game.tower_discard == []   # held until the sentence is served
    sent = next(e for e in events if e["kind"] == "sent_to_rack")
    assert sent["payload"]["penalty"] == "hand"
    assert sent["payload"]["cards_taken"] == 2


def test_firecrackers_escapes_when_landing_outside_white_tower():
    """A marked player who moves out of the White Tower during their turn
    clears the flag and is not racked at end of turn."""
    from server.game.rules import _commit_move

    # wt_chapel_st_john borders iw_14_4 (inner ward), giving a single-step
    # exit path out of the White Tower.
    game = _state("wt_chapel_st_john", "ww00_start")
    game.firecrackers_affected = ["alice"]
    rng = Rng(seed=0)
    _GLOBAL_RNG.set(rng)

    alice = game.players[0]
    move_events = _commit_move(
        game, BOARD, alice, "iw_14_4", [alice.position, "iw_14_4"]
    )

    assert "alice" not in game.firecrackers_affected
    assert any(e["kind"] == "firecrackers_escaped" for e in move_events)

    # Now end_turn should NOT rack her.
    game.phase = Phase.TURN_END
    game, events = apply(game, "end_turn", {"username": "alice"}, board=BOARD, rng=rng)
    assert alice.status != Status.RACKED
    assert not any(e["kind"] == "firecrackers_racked" for e in events)
