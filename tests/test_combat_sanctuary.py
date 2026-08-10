"""Sanctuary cancels the fight but not its cost.

Both players lose the cards they committed and draw that many replacements —
the weapons were spent even though the combat never resolved.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import apply, _GLOBAL_RNG
from server.game.state import GameState, Phase, PlayerState, TurnContext


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def weapon(cid: str, value: int = 5) -> Card:
    return Card(id=cid, kind="tower", category="weapon", name=f"Weapon {cid}", value=value)


def sanctuary_card() -> Card:
    return Card(id="sanct-1", kind="tower", category="utility", name="Sanctuary",
                value=0, effect_key="sanctuary")


def fresh(cid: str) -> Card:
    return Card(id=cid, kind="tower", category="utility", name=f"Fresh {cid}", value=0)


def setup_combat() -> tuple[GameState, Rng]:
    """Attacker and defender co-located and mid-combat, both cards committed."""
    atk = PlayerState(username="atk", color="red", position="ww10",
                      accredited=True, hand=[weapon("a1"), weapon("a2")])
    dfn = PlayerState(username="dfn", color="blue", position="ww10",
                      accredited=True, hand=[weapon("d1"), sanctuary_card()])
    state = GameState(mode="fast", players=[atk, dfn], turn_order=["atk", "dfn"])
    state.phase = Phase.TURN_END
    state.turn = TurnContext()
    # Plenty of replacements available.
    state.tower_draw = [fresh(f"f{i}") for i in range(6)]
    rng = Rng(seed=9)
    _GLOBAL_RNG.set(rng)

    state, _ = apply(state, "initiate_combat",
                     {"username": "atk", "target": "dfn"}, board=BOARD, rng=rng)
    state, _ = apply(state, "select_combat_cards",
                     {"username": "atk", "card_ids": ["a1", "a2"]}, board=BOARD, rng=rng)
    state, _ = apply(state, "select_combat_cards",
                     {"username": "dfn", "card_ids": ["d1"]}, board=BOARD, rng=rng)
    return state, rng


def test_sanctuary_costs_both_players_their_committed_cards():
    state, rng = setup_combat()

    state, evs = apply(state, "play_combat_special",
                       {"username": "dfn", "card_id": "sanct-1"}, board=BOARD, rng=rng)
    ev = next(e for e in evs if e["kind"] == "sanctuary_taken")

    atk = state.player("atk")
    dfn = state.player("dfn")
    atk_ids = {c.id for c in atk.hand}
    dfn_ids = {c.id for c in dfn.hand}

    assert ev["payload"]["attacker_cards_lost"] == 2
    assert ev["payload"]["defender_cards_lost"] == 1
    # Committed cards are gone from both hands...
    assert not ({"a1", "a2"} & atk_ids)
    assert "d1" not in dfn_ids
    # ...and replaced one for one.
    assert len(atk_ids) == 2
    assert len(dfn_ids) == 1
    discarded = {c.id for c in state.tower_discard}
    assert {"a1", "a2", "d1", "sanct-1"} <= discarded


def test_combat_resolved_reports_the_totals_and_the_spoils():
    """The event has to carry enough for the UI to narrate the whole outcome."""
    state, rng = setup_combat()
    # Give the defender something worth taking, and make sure they lose.
    dfn = state.player("dfn")
    dfn.jewels = ["sword"]
    dfn.has_coin = True

    state, evs = apply(state, "reveal_combat", {"username": "atk"}, board=BOARD, rng=rng)
    p = next(e for e in evs if e["kind"] == "combat_resolved")["payload"]

    # atk committed 2x5, dfn committed 1x5.
    assert p["attacker_total"] == 10
    assert p["defender_total"] == 5
    assert p["winner"] == "atk"
    assert p["loser"] == "dfn"
    assert p["tie"] is False
    assert p["jewels_taken"] == ["sword"]
    assert p["coin_taken"] is True
    assert p["coin_overflowed"] is False
    assert p["cards_drawn"] == 2
    assert p["loser_sent_to"] == BOARD.data.hospital_space
    # And the spoils actually moved.
    assert state.player("atk").jewels == ["sword"]
    assert state.player("atk").has_coin
    assert state.player("dfn").position == BOARD.data.hospital_space


def test_sanctuary_teleports_the_defender_and_ends_the_turn():
    state, rng = setup_combat()

    state, _ = apply(state, "play_combat_special",
                     {"username": "dfn", "card_id": "sanct-1"}, board=BOARD, rng=rng)

    assert state.player("dfn").position == BOARD.data.chapel_royal_space
    assert state.phase == Phase.TURN_END
    assert state.combat is None or state.combat.sanctuary_cancelled
