"""Mass Accretor turns a stolen weapon on its owner in the same fight.

Taking the card into the defender's *hand* made it a card for some later fight,
so the only visible effect here was the attacker's total dropping. It belongs in
the defender's committed pile: one swing takes value off the attacker and adds
the same value to the defence.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.cards import Card
from server.game.rng import Rng
from server.game.rules import _GLOBAL_RNG, apply
from server.game.state import Combat, GameState, Phase, PlayerState

BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def _weapon(name: str, value: int, n: int) -> Card:
    return Card(id=f"tower:{name}:{n}", kind="tower", name=name,
                category="weapon", value=value)


def _accretor() -> Card:
    return Card(id="tower:mass_accretor:1", kind="tower", name="Mass Accretor",
                category="custom", effect_key="mass_accretor")


def _game(*, attacker_weapons, defender_weapons, with_accretor=True) -> GameState:
    atk = PlayerState(username="atk", color="red", position="ww05", accredited=True)
    dfn = PlayerState(username="dfn", color="blue", position="ww05", accredited=True)
    if with_accretor:
        dfn.hand.append(_accretor())
    game = GameState(
        mode="fast", players=[atk, dfn],
        turn_order=["atk", "dfn"], current_turn_index=0, seed=9,
    )
    game.tower_draw = [_weapon("Dagger", 1, 90 + i) for i in range(5)]
    game.phase = Phase.COMBAT
    game.combat = Combat(
        attacker="atk", defender="dfn", space_id="ww05",
        attacker_cards=list(attacker_weapons),
        defender_cards=list(defender_weapons),
        attacker_committed=True, defender_committed=True,
        phase="defender_specials",
    )
    return game


def _apply(game, intent, payload):
    rng = Rng(seed=9)
    _GLOBAL_RNG.set(rng)
    return apply(game, intent, payload, board=BOARD, rng=rng)


def test_stolen_weapon_is_played_immediately_in_defence():
    game = _game(
        attacker_weapons=[_weapon("Crossbow", 10, 1)],
        defender_weapons=[_weapon("Mace", 2, 2)],
    )
    new, events = _apply(game, "play_combat_special",
                         {"username": "dfn", "card_id": "tower:mass_accretor:1"})
    c = new.combat
    # The weapon left the attacker's pile and joined the defence, not the hand.
    assert c.attacker_cards == []
    assert [x.name for x in c.defender_cards] == ["Mace", "Crossbow"]
    assert "Crossbow" not in [x.name for x in new.player("dfn").hand]
    assert "mass_accretor_stole" in [e["kind"] for e in events]


def test_the_double_swing_decides_the_fight():
    """10 vs 2 becomes 0 vs 12 — the theft is meant to be able to turn a loss."""
    game = _game(
        attacker_weapons=[_weapon("Crossbow", 10, 1)],
        defender_weapons=[_weapon("Mace", 2, 2)],
    )
    new, _ = _apply(game, "play_combat_special",
                    {"username": "dfn", "card_id": "tower:mass_accretor:1"})
    new, events = _apply(new, "reveal_combat", {"username": "dfn"})
    resolved = next(e for e in events if e["kind"] == "combat_resolved")
    assert resolved["payload"]["attacker_total"] == 0
    assert resolved["payload"]["defender_total"] == 12
    assert resolved["payload"]["winner"] == "dfn"


def test_the_card_is_spent_exactly_once():
    game = _game(
        attacker_weapons=[_weapon("Crossbow", 10, 1)],
        defender_weapons=[],
    )
    new, _ = _apply(game, "play_combat_special",
                    {"username": "dfn", "card_id": "tower:mass_accretor:1"})
    ids = [c.id for c in new.tower_discard]
    assert ids.count("tower:mass_accretor:1") == 1


def test_nothing_to_steal_still_spends_the_card():
    game = _game(attacker_weapons=[], defender_weapons=[_weapon("Mace", 2, 2)])
    new, events = _apply(game, "play_combat_special",
                         {"username": "dfn", "card_id": "tower:mass_accretor:1"})
    assert "mass_accretor_no_target" in [e["kind"] for e in events]
    assert new.player("dfn").hand == []
    assert [c.id for c in new.tower_discard] == ["tower:mass_accretor:1"]
