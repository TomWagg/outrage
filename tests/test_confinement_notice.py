"""The public confinement banner.

Raised by watching player statuses either side of every intent rather than by
hooking each of the seven places that lock somebody up, so a route nobody
remembered still announces itself. Dismissable only by the player it happened
to — the rest of the table just watches.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, apply
from server.game.state import (
    ConfinementNotice,
    GameState,
    PendingMove,
    Phase,
    PlayerState,
    Status,
)

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
BOWYER = "ww47_bowyer"


def _game(phase: Phase = Phase.TURN_START) -> GameState:
    game = GameState(
        mode="fast",
        players=[
            PlayerState(username="alice", color="red", position="ww46", accredited=True),
            PlayerState(username="bob", color="blue", position="ww20", accredited=True),
        ],
        turn_order=["alice", "bob"],
        current_turn_index=0,
        seed=2,
    )
    game.phase = phase
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=2)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_walking_into_the_bowyer_tower_raises_the_banner():
    game = _game(Phase.CHOOSING_PATH)
    alice = game.player("alice")
    game.turn.pending_move = PendingMove(
        steps=1, destinations={BOWYER: ["ww46", BOWYER]},
    )
    new, _ = _apply(game, "choose_move_path", {
        "username": "alice", "destination": BOWYER,
    })
    assert new.player("alice").status == Status.TORTURED
    notice = new.active_confinement_notice
    assert notice is not None
    assert notice.username == "alice"
    assert notice.status == Status.TORTURED
    assert notice.space_id == BOWYER
    assert notice.turns == 3
    assert notice.cause == "landed"


def test_no_banner_when_nobody_was_locked_up():
    game = _game(Phase.CHOOSING_PATH)
    game.turn.pending_move = PendingMove(
        steps=1, destinations={"ww45_go_broad_arrow": ["ww46", "ww45_go_broad_arrow"]},
    )
    new, _ = _apply(game, "choose_move_path", {
        "username": "alice", "destination": "ww45_go_broad_arrow",
    })
    assert new.active_confinement_notice is None


def test_only_the_victim_can_dismiss_the_banner():
    game = _game(Phase.TURN_END)
    alice = game.player("alice")
    alice.status = Status.IMPRISONED
    alice.status_turns_remaining = 3
    alice.position = BOARD.data.bloody_tower_space
    # Raise it via the watcher by flipping status inside an intent-driven step.
    game.player("alice").status = Status.NORMAL
    game.player("alice").status_turns_remaining = 0
    game.active_confinement_notice = ConfinementNotice(
        username="alice", status=Status.IMPRISONED,
        space_id=BOARD.data.bloody_tower_space, turns=3, cause="landed",
    )
    game.player("alice").status = Status.IMPRISONED
    game.player("alice").status_turns_remaining = 3

    # bob's click does nothing.
    new, events = _apply(game, "dismiss_confinement_notice", {"username": "bob"})
    assert new.active_confinement_notice is not None
    assert events == []

    new, events = _apply(new, "dismiss_confinement_notice", {"username": "alice"})
    assert new.active_confinement_notice is None
    assert [e["kind"] for e in events] == ["confinement_notice_dismissed"]


def test_a_pardon_clears_a_stale_banner():
    game = _game(Phase.TURN_START)
    alice = game.player("alice")
    alice.status = Status.IMPRISONED
    alice.status_turns_remaining = 3
    alice.position = BOARD.data.bloody_tower_space
    game.active_confinement_notice = ConfinementNotice(
        username="alice", status=Status.IMPRISONED,
        space_id=BOARD.data.bloody_tower_space, turns=3, cause="landed",
    )
    card = Card(
        id="tower:royal_pardon:1", kind="tower", name="Royal Pardon",
        category="utility", effect_key="royal_pardon",
    )
    alice.hand.append(card)
    new, _ = _apply(game, "play_card_pre_roll", {"username": "alice", "card_id": card.id})
    assert new.player("alice").status == Status.NORMAL
    assert new.active_confinement_notice is None
