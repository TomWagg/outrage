"""Raven cards that summon a player must resolve the destination's landing.

Previously ``_send_to`` just moved the piece, so being summoned to the Museum
drew no tower card and being summoned onto an action square fired no action.
Punishment cards (hospital / Rack / prison towers) deliberately still skip
landing resolution — the card is the whole effect.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _resolve_landing, apply, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, Status, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")

RAVEN_SPACE = next(s.id for s in BOARD.data.spaces if s.kind == "raven_trigger")


def make_player(pos: str = RAVEN_SPACE) -> PlayerState:
    return PlayerState(username="p1", color="red", position=pos)


def make_state(player: PlayerState) -> GameState:
    s = GameState(mode="fast", players=[player], turn_order=[player.username])
    s.phase = Phase.MOVING
    s.turn = TurnContext(visited_this_turn=[player.position])
    s.jewels_available = dict(BOARD.data.initial_jewel_locations)
    _GLOBAL_RNG.set(Rng(seed=11))
    return s


def raven(effect_key: str, **params) -> Card:
    return Card(id=f"raven:{effect_key}:1", kind="raven", name=effect_key,
                effect_key=effect_key, params=params)


def dummy_tower_cards(n: int) -> list[Card]:
    return [Card(id=f"tc-{i}", kind="tower", category="utility", name="Dummy", value=0)
            for i in range(n)]


def kinds_of(evs) -> list[str]:
    return [e["kind"] for e in evs]


def land_and_reveal(state: GameState, player: PlayerState, **resolve_params):
    """Land on the square, then have the drawer turn the raven card over.

    Raven effects deliberately don't fire on landing any more — the card is
    dealt face-down and resolves only when the drawer reveals it, so nobody
    sees a piece move before they've seen why.

    A card that still wants an answer after the reveal (a Summons, which may be
    refused) gets ``resolve_params`` sent straight back at it.
    """
    evs = _resolve_landing(state, BOARD, player)
    if state.turn.pending_raven is not None:
        _, more = apply(
            state, "reveal_raven_notice", {"username": player.username},
            board=BOARD, rng=_GLOBAL_RNG.get(),
        )
        evs = evs + more
    if state.turn.pending_raven is not None and resolve_params:
        _, more = apply(
            state, "resolve_raven_effect",
            {"username": player.username, "params": dict(resolve_params)},
            board=BOARD, rng=_GLOBAL_RNG.get(),
        )
        evs = evs + more
    return evs


def test_summons_to_the_museum_draws_a_tower_card():
    player = make_player()
    state = make_state(player)
    state.tower_draw = dummy_tower_cards(1)
    state.raven_draw = [raven("go_to_location", location="museum")]

    evs = land_and_reveal(state, player, accept=True)

    assert player.position == BOARD.data.museum_space
    assert "tower_card_drawn" in kinds_of(evs)
    assert len(player.hand) == 1


def test_summons_to_devereux_grants_the_coin():
    player = make_player()
    state = make_state(player)
    state.coins_available = 5
    state.tower_draw = dummy_tower_cards(1)
    state.raven_draw = [raven("go_to_location", location="devereux_tower")]

    evs = land_and_reveal(state, player, accept=True)

    assert player.position == BOARD.data.devereux_space
    assert "coin_picked_up" in kinds_of(evs)
    assert player.has_coin


def test_shop_for_film_summons_and_resolves_the_shop():
    player = make_player()
    state = make_state(player)
    state.raven_draw = [raven("shop_for_film")]

    land_and_reveal(state, player)

    assert player.position == BOARD.data.shop_space


def test_governors_tea_starts_the_accreditation_trial_at_queens_house():
    player = make_player()
    state = make_state(player)
    state.raven_draw = [raven("governors_tea")]

    evs = land_and_reveal(state, player)

    assert player.position == BOARD.data.queens_house_space
    assert "trying_accreditation" in kinds_of(evs)
    assert player.trying_accreditation
    assert player.miss_next_turn


def test_summons_onto_a_raven_square_does_not_draw_a_second_raven_card():
    """The suppression that stops summons recursing through the raven deck."""
    player = make_player()
    state = make_state(player)
    # Beauchamp Tower is a wall-walk square; use an inner-ward raven square as
    # the destination instead so the guard is actually exercised.
    dest = next(
        s.id for s in BOARD.data.spaces
        if s.kind == "raven_trigger" and s.id != RAVEN_SPACE
    )
    # The deck is drawn from the end, so the summons goes last.
    state.raven_draw = [
        # A second card that must NOT be drawn.
        raven("pecked_by_ravens"),
        raven("go_to_location", location=dest),
    ]

    evs = land_and_reveal(state, player, accept=True)

    assert player.position == dest
    assert kinds_of(evs).count("raven_card_drawn") == 1
    assert player.status != Status.HOSPITAL
    assert len(state.raven_draw) == 1


def test_summons_to_the_broad_arrow_tower_still_costs_your_weapons():
    player = make_player()
    player.hand = [Card(id="w1", kind="tower", category="weapon", name="Mace", value=5)]
    state = make_state(player)
    state.raven_draw = [raven("go_to_location", location="broad_arrow_tower")]

    evs = land_and_reveal(state, player, accept=True)
    ev = next(e for e in evs if e["kind"] == "weapons_surrendered")

    assert player.position == "ww29_broad_arrow"
    assert ev["payload"]["count"] == 1
    assert player.hand == []


def test_punishment_cards_do_not_resolve_their_destination():
    """Pecked by ravens sends you to the Hospital; it must not also draw."""
    player = make_player()
    state = make_state(player)
    state.tower_draw = dummy_tower_cards(1)
    state.raven_draw = [raven("pecked_by_ravens")]

    evs = land_and_reveal(state, player)

    assert player.position == BOARD.data.hospital_space
    assert player.status == Status.HOSPITAL
    assert "tower_card_drawn" not in kinds_of(evs)
