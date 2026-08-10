"""Combat sub-state machine as pure functions.

The combat lifecycle is:

1. ``begin`` — attacker has declared; we're waiting for them to pick cards.
2. ``set_attacker_cards`` — attacker commits any subset of weapons from hand.
3. ``set_defender_cards`` — defender commits any subset of weapons from hand
   (may include ``Suit of Armour``; attacker may not play ``defender_only``
   cards).
4. ``play_defender_special`` — ``Sanctuary`` cancels combat (defender is
   teleported to Chapel Royal); ``Mass Accretor`` steals one random weapon
   from the attacker's commit pile into the defender's hand.
5. ``reveal`` — compute winner. Max total value wins; ties go to defender.
6. ``resolve`` — transfer jewels and coin (excess coins overflow back to
   devereux), loser goes to hospital and misses next turn, victor draws a
   number of cards equal to the cards *they* played.

All of these operate on a :class:`Combat` struct and a small bundle of
side-channel parameters (rng, decks, the players involved). They do NOT know
about the board-at-large; the rule engine wires them together.
"""
from __future__ import annotations

import logging
from typing import Optional

from .cards import Card, Deck
from .rng import Rng
from .state import Combat, GameState, PlayerState, Status

log = logging.getLogger(__name__)


class CombatError(Exception):
    """Raised when an intent breaks combat invariants (wrong phase, etc.)."""


# ---------- helpers --------------------------------------------------------


def _weapon_ids(player: PlayerState) -> set[str]:
    return {c.id for c in player.hand if c.category == "weapon"}


def _pop_cards(player: PlayerState, card_ids: list[str]) -> list[Card]:
    out: list[Card] = []
    seen: set[str] = set()
    for cid in card_ids:
        if cid in seen:
            raise CombatError(f"Duplicate card id in selection: {cid}")
        seen.add(cid)
        c = player.remove_card(cid)
        if c is None:
            # Restore removed for rollback.
            for rc in out:
                player.add_card(rc)
            raise CombatError(f"Player {player.username!r} has no card {cid!r}")
        out.append(c)
    return out


# ---------- state transitions ---------------------------------------------


def begin(state: GameState, attacker: str, defender: str, space_id: str) -> Combat:
    if attacker == defender:
        raise CombatError("Cannot attack self")
    # Ensure both exist.
    state.player(attacker)
    state.player(defender)
    combat = Combat(attacker=attacker, defender=defender, space_id=space_id, phase="attacker_selecting")
    state.combat = combat
    return combat


def set_attacker_cards(state: GameState, card_ids: list[str]) -> Combat:
    combat = _require_combat(state)
    if combat.phase != "attacker_selecting":
        raise CombatError(f"Cannot set attacker cards in phase {combat.phase}")
    attacker = state.player(combat.attacker)
    picked = _pop_cards(attacker, card_ids)
    for c in picked:
        if c.category != "weapon":
            _return_cards(attacker, picked)
            raise CombatError(f"{c.name} is not a weapon")
        if c.defender_only:
            _return_cards(attacker, picked)
            raise CombatError(f"{c.name} is defender-only")
    combat.attacker_cards = picked
    combat.attacker_committed = True
    combat.phase = "defender_selecting"
    return combat


def set_defender_cards(state: GameState, card_ids: list[str]) -> Combat:
    combat = _require_combat(state)
    if combat.phase != "defender_selecting":
        raise CombatError(f"Cannot set defender cards in phase {combat.phase}")
    defender = state.player(combat.defender)
    picked = _pop_cards(defender, card_ids)
    for c in picked:
        if c.category != "weapon":
            _return_cards(defender, picked)
            raise CombatError(f"{c.name} is not a weapon")
    combat.defender_cards = picked
    combat.defender_committed = True
    combat.phase = "defender_specials"
    return combat


def play_defender_special(
    state: GameState,
    card_id: str,
    chapel_royal_space: str,
    rng: Rng,
    tower_deck: Optional[Deck] = None,
) -> Combat:
    """Apply ``Sanctuary`` or ``Mass Accretor`` to the current combat.

    - ``Sanctuary`` (utility): defender teleports to Chapel Royal; combat is
      cancelled. The card is moved to the tower discard. Both players lose the
      cards they committed and draw that many replacements — the weapons were
      spent even though the fight never happened. (``tower_deck`` is required
      for this; without it the cards are discarded and not replaced.)
    - ``Mass Accretor`` (custom): steal one random weapon from the attacker's
      committed pile into the defender's hand.
    """
    combat = _require_combat(state)
    if combat.phase not in ("defender_specials", "defender_selecting", "attacker_selecting"):
        raise CombatError(f"Cannot play defender special in phase {combat.phase}")
    defender = state.player(combat.defender)
    card = defender.remove_card(card_id)
    if card is None:
        raise CombatError(f"Defender has no card {card_id!r}")
    if card.effect_key == "sanctuary":
        defender.position = chapel_royal_space
        combat.sanctuary_cancelled = True
        combat.phase = "resolved"
        attacker = state.player(combat.attacker)
        for player, committed in (
            (attacker, combat.attacker_cards), (defender, combat.defender_cards),
        ):
            for c in committed:
                if tower_deck is not None:
                    tower_deck.discard(c)
            if tower_deck is not None:
                for _ in committed:
                    drew = tower_deck.draw(rng)
                    if drew is None:
                        break
                    player.add_card(drew)
        combat.resolved_events.append(
            f"sanctuary:atk_lost={len(combat.attacker_cards)},"
            f"def_lost={len(combat.defender_cards)}"
        )
        combat.attacker_cards = []
        combat.defender_cards = []
        return combat
    if card.effect_key == "mass_accretor":
        if not combat.attacker_cards:
            # Nothing to steal — discard the card and continue.
            combat.mass_accretor_played = True
            combat.resolved_events.append("mass_accretor_no_target")
            return combat
        stolen = combat.attacker_cards.pop(rng.randint(0, len(combat.attacker_cards) - 1))
        defender.add_card(stolen)
        combat.mass_accretor_played = True
        combat.resolved_events.append(f"mass_accretor_stole:{stolen.id}")
        return combat
    # Not a valid special: give the card back and complain.
    defender.add_card(card)
    raise CombatError(f"Card {card.name!r} is not a defender special")


def reveal(state: GameState) -> Combat:
    combat = _require_combat(state)
    if combat.phase != "defender_specials":
        raise CombatError(f"Cannot reveal in phase {combat.phase}")
    atk_total = sum(c.value for c in combat.attacker_cards)
    def_total = sum(c.value for c in combat.defender_cards)
    # Ties go to defender.
    combat.winner = combat.attacker if atk_total > def_total else combat.defender
    combat.phase = "revealed"
    combat.resolved_events.append(f"revealed:atk={atk_total},def={def_total}")
    return combat


def resolve(
    state: GameState,
    tower_deck: Deck,
    hospital_space: str,
    devereux_max_coins: int,
    rng: Rng,
) -> Combat:
    """Transfer spoils, discard played weapons, draw cards for the victor.

    - Winner takes all jewels and (if loser has one) the coin from the loser.
    - If winner already had a coin, the "excess" coin returns to devereux
      (``state.coins_available``) up to ``devereux_max_coins``.
    - Loser → hospital, status = HOSPITAL, miss_next_turn = True.
    - Winner draws N tower cards where N = count of the victor's played
      weapons.
    - Every committed weapon goes to the tower discard pile (they were used).
    """
    combat = _require_combat(state)
    if combat.sanctuary_cancelled:
        state.combat = None
        return combat
    if combat.phase != "revealed":
        raise CombatError(f"Cannot resolve in phase {combat.phase}")
    assert combat.winner is not None
    winner = state.player(combat.winner)
    loser = state.player(combat.defender if combat.winner == combat.attacker else combat.attacker)

    # Transfer jewels.
    winner.jewels.extend(loser.jewels)
    loser.jewels = []
    # Transfer coin with overflow back to devereux.
    if loser.has_coin:
        loser.has_coin = False
        if not winner.has_coin:
            winner.has_coin = True
        else:
            state.coins_available = min(devereux_max_coins, state.coins_available + 1)

    # Discard weapons.
    for c in combat.attacker_cards + combat.defender_cards:
        tower_deck.discard(c)

    # Count winner's plays for draws.
    winner_plays = combat.attacker_cards if combat.winner == combat.attacker else combat.defender_cards
    draws = len(winner_plays)
    for _ in range(draws):
        drew = tower_deck.draw(rng)
        if drew is None:
            break
        winner.add_card(drew)

    # Loser goes to hospital.
    loser.position = hospital_space
    loser.status = Status.HOSPITAL
    loser.status_turns_remaining = 0
    loser.miss_next_turn = True

    combat.phase = "resolved"
    combat.resolved_events.append(
        f"loser={loser.username},winner={winner.username},drew={draws}"
    )
    state.combat = None
    return combat


# ---------- utilities -----------------------------------------------------


def _require_combat(state: GameState) -> Combat:
    if state.combat is None:
        raise CombatError("No combat in progress")
    return state.combat


def _return_cards(player: PlayerState, cards: list[Card]) -> None:
    for c in cards:
        player.add_card(c)
