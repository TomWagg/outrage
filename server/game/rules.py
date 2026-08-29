"""Pure intent dispatch for the rule engine.

Every externally-observable action (rolling dice, playing a card, etc.) is
an ``Intent`` — a ``(name, payload)`` pair. :func:`apply` validates the intent
against the current :class:`GameState` and either returns
``(new_state, events)`` or raises :class:`RuleError`.

State is mutated **in place** and the same object is returned as ``new_state``.
Handlers are written to validate before mutating, but a mid-handler exception
will leave the state partially changed. The caller (:mod:`server.main`) only
replaces ``AppState.game`` on success, so a :class:`RuleError` leaves the
live game untouched.

``events`` is a plain list of dicts ``{"kind": ..., "payload": {...}}``.
:mod:`server.main` broadcasts these to all clients as individual ``Event``
messages and then pushes a fresh per-player redacted snapshot.

RNG access from auto-triggered paths (raven draws that fire during landing
resolution) uses the module-level :data:`_GLOBAL_RNG` bridge rather than
threading the :class:`~server.game.rng.Rng` through every call stack.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from .board import Board
from .cards import Card, Deck
from .cards_effects import EffectError, dispatch as dispatch_effect
from . import combat as combat_mod
from .movement import compute_destinations, split_movement
from .rng import Rng
from .state import (
    CONFINED_STATUSES,
    Combat,
    ConfinementNotice,
    GameState,
    GameStats,
    LogEntry,
    JewelId,
    DeferredSplitLeg,
    PendingCardChange,
    PendingJewelAttempt,
    PendingMove,
    PendingRavenEffect,
    PendingSplitSeven,
    Phase,
    PlayerState,
    RavenNotice,
    Status,
    Warder,
)

log = logging.getLogger(__name__)

# Fallback coin cap for states built without ``start_game`` (unit tests, mostly).
# A live game overwrites ``coins_total`` with ``len(players)``.
MAX_COINS = 5
# Initial hand sizes by player count.
DEAL_2_4 = 6
DEAL_5_6 = 4


class RuleError(Exception):
    """Raised by rule handlers when an intent is invalid for the current state."""


def _ev(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "payload": payload}


def _log(state: GameState, evs: Iterable[dict[str, Any]]) -> None:
    for e in evs:
        state.log.append(LogEntry(kind=e["kind"], payload=e.get("payload", {})))


# Events that put a player behind bars, for the "times locked up" tally.
_LOCKUP_EVENTS = {
    "three_doubles_bloody_tower", "beauchamp_imprisonment", "bowyer_questioning",
    "rack_sender_triggered", "firecrackers_racked", "stopped_forfeit",
    "confined_on_landing",
}


def compute_game_stats(state: GameState) -> dict[str, GameStats]:
    """Fold the event log into per-player tallies for the end-of-game screen.

    The log is the authoritative record of the whole game, so one pass over it
    beats scattering counters through the rule engine — and it means a stat can
    be added later without touching the rules at all.
    """
    stats = {p.username: GameStats() for p in state.players}

    def of(name: Any) -> Optional[GameStats]:
        return stats.get(name) if isinstance(name, str) else None

    for entry in state.log:
        p = entry.payload or {}
        s = of(p.get("player"))
        kind = entry.kind

        if kind == "turn_start" and s:
            s.turns_taken += 1
        elif kind == "dice_rolled" and s:
            roll = p.get("roll") or []
            if len(roll) == 2 and roll[0] == roll[1]:
                s.doubles_rolled += 1
        elif kind == "player_moved" and s:
            # Only walked squares count; a teleport carries no path.
            path = p.get("path") or []
            if len(path) > 1:
                s.steps_taken += len(path) - 1
        elif kind == "tower_card_drawn" and s:
            s.tower_cards_drawn += 1
        elif kind == "raven_card_drawn" and s:
            s.raven_cards_drawn += 1
        elif kind == "jewel_attempt" and s:
            s.jewel_attempts += 1
        elif kind in ("jewel_acquired", "jewel_auto_acquired") and s:
            s.jewels_collected += 1
        elif kind == "coin_picked_up" and s:
            s.coins_picked_up += 1
        elif kind == "missed_turn" and s:
            s.turns_lost += 1
        elif kind == "combat_resolved":
            w, l = of(p.get("winner")), of(p.get("loser"))
            if w:
                w.fights_won += 1
                # Jewels taken off the loser count towards the victor's haul.
                w.jewels_collected += len(p.get("jewels_taken") or [])
            if l:
                l.fights_lost += 1
        elif kind in _LOCKUP_EVENTS and s:
            s.times_locked_up += 1

    return stats


def _disguise_card(player: PlayerState) -> Optional[Card]:
    """The first unplayed Disguise in ``player``'s hand, if they hold one."""
    return next(
        (c for c in player.hand if c.kind == "tower" and c.effect_key == "disguise"),
        None,
    )


def _warder_blocked_spaces(state: GameState, board: "Board") -> set[str]:
    """Return post space ids that are occupied by a warder this turn.

    Returns an empty set when the current player has a Disguise armed
    (``turn.disguise_used``), allowing them to pass freely.
    """
    if state.turn.disguise_used:
        return set()
    barracks = board.data.barracks_space
    return {w.location for w in state.warders if w.location != barracks}


def immune_to_forced_moves(board: "Board", player: PlayerState) -> bool:
    """True when nothing another player does may shift this piece.

    Two sources: the square itself (``immune_to_forced_moves`` in the board
    file) and the White Tower as a whole, which you walk out of under your own
    steam or not at all. A split 7, a Lasso or a raven card that shoves a piece
    around must all consult this — the White Tower is forward-only, so a forced
    step inside it either goes nowhere legal or dumps the victim onto the Rack
    Sender, which is exactly the way a released prisoner used to get re-racked.
    """
    space = board.space(player.position)
    if space.immune_to_forced_moves:
        return True
    return space.region == "white_tower" and bool(
        getattr(board.data.rules, "white_tower_immune_to_forced_moves", True)
    )


def cancel_rest_if_moved_off(
    state: GameState, board: "Board", player: PlayerState, old_space: str,
) -> list[dict[str, Any]]:
    """A player dragged off a resting square stops resting.

    The Shop, the benches and the Hospital cost you your next turn because you
    are *there* — browsing, sitting, convalescing. Somebody else's seven or a
    Lasso is allowed to haul you off the square, and when it does the errand
    goes with it: the turn you owed is written off rather than following you
    around the board.

    Call this after moving the piece and *before* resolving the landing, or a
    victim shoved from one bench onto another would have the fresh penalty
    cancelled instead of the old one.
    """
    if not player.miss_next_turn or player.position == old_space:
        return []
    rest_kinds = set(getattr(board.data.rules, "miss_turn_on_landing_kinds", []) or [])
    if board.space(old_space).kind not in rest_kinds:
        return []
    player.miss_next_turn = False
    if player.status == Status.HOSPITAL:
        player.status = Status.NORMAL
    return [_ev("rest_interrupted", player=player.username, space=old_space)]


def _release_from_rack(
    state: GameState, player: PlayerState, board: "Board",
) -> list[dict[str, Any]]:
    """Unlock the Rack and step the player out of the cell.

    Serving the sentence used to clear the status and leave the piece sitting
    on the Rack itself. That is not a square you can be left standing on: it is
    a dead end whose only exit is the Rack Sender, so the freed player was
    still one forced step away from being sent straight back down. Walk them
    out as part of the release.
    """
    player.status = Status.NORMAL
    player.status_turns_remaining = 0
    evs = [_ev("rack_expired", player=player.username)]
    exit_space = board.rack_exit_space
    if exit_space and player.position == board.data.rack_space:
        src = player.position
        player.position = exit_space
        evs.append(_ev(
            "player_moved", player=player.username,
            src=src, dst=exit_space, move_kind="rack_release",
        ))
    return evs


# Phases that own the table: something is waiting on a decision (or the game is
# finished), so nothing else may reset the phase to TURN_END underneath them.
_SUB_PHASES = (
    Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT, Phase.CARD_CHANGE,
    Phase.CHOOSING_PATH, Phase.COMBAT, Phase.SPLIT_SEVEN_ASSIGN, Phase.GAME_OVER,
)


def _sub_phase_active(state: GameState) -> bool:
    return state.phase in _SUB_PHASES


def _require_phase(state: GameState, *phases: Phase) -> None:
    if state.phase not in phases:
        raise RuleError(f"Wrong phase: {state.phase.value}, expected one of {[p.value for p in phases]}")


def _require_current_player(state: GameState, username: str) -> PlayerState:
    cur = state.current_player()
    if cur.username != username:
        raise RuleError(f"Not {username}'s turn (current: {cur.username})")
    return cur


# =========================================================================
# Intent dispatch
# =========================================================================


#: Event kinds that explain *why* somebody ended up locked up, mapped to the
#: banner copy. Only used to flavour the notice — the notice itself is raised by
#: watching statuses change, so a route with no entry here still announces
#: itself.
_CONFINEMENT_CAUSES = {
    "confined_on_landing": "landed",
    "three_doubles_bloody_tower": "three_doubles",
    "rack_sender_triggered": "rack_sender",
    "firecrackers_racked": "firecrackers",
    "beauchamp_imprisonment": "raven",
    "rack_of_torment": "raven",
    "stopped_forfeit": "searched",
    "combat_resolved": "combat",
    "framed": "framed",
}


def _raise_confinement_notice(
    state: GameState,
    before: dict[str, Status],
    events: list[dict[str, Any]],
) -> None:
    """Announce any player who has just crossed into confinement.

    Driven by comparing statuses either side of the handler rather than by
    hooking each site that locks somebody up — there are seven of those (a
    landing, three doubles, the rack-sender square, two raven cards, a lost
    fight, Firecrackers) and a new one would silently skip the banner.
    """
    newly = [
        p for p in state.players
        if p.confined and not (before.get(p.username) in CONFINED_STATUSES)
    ]
    if newly:
        # More than one at once isn't reachable today; announce the first and
        # let the log carry the rest rather than dropping the banner entirely.
        victim = newly[0]
        cause = ""
        for ev in events:
            mapped = _CONFINEMENT_CAUSES.get(ev.get("kind", ""))
            if mapped and ev.get("payload", {}).get("player") in (victim.username, None):
                cause = mapped
                break
        state.active_confinement_notice = ConfinementNotice(
            username=victim.username,
            status=victim.status,
            space_id=victim.position,
            turns=victim.status_turns_remaining,
            cause=cause,
        )
        return
    # Released (pardon, sentence served, confessed away): the banner is stale.
    notice = state.active_confinement_notice
    if notice is not None:
        try:
            if not state.player(notice.username).confined:
                state.active_confinement_notice = None
        except KeyError:
            state.active_confinement_notice = None


def apply(
    state: GameState,
    intent_name: str,
    payload: dict[str, Any],
    *,
    board: Board,
    rng: Rng,
) -> tuple[GameState, list[dict[str, Any]]]:
    handler = _INTENTS.get(intent_name)
    if handler is None:
        raise RuleError(f"Unknown intent: {intent_name}")
    statuses_before = {p.username: p.status for p in state.players}
    new_state, events = handler(state, payload, board=board, rng=rng)
    _raise_confinement_notice(new_state, statuses_before, events)
    # Tally here rather than at each game-over site: handlers log their events
    # on the way out, so this is the first point where the log is complete for
    # the turn that ended the game.
    if new_state.phase == Phase.GAME_OVER and not new_state.final_stats:
        new_state.final_stats = compute_game_stats(new_state)
    return new_state, events


# =========================================================================
# Start / setup
# =========================================================================


def _intent_start_game(state, payload, *, board, rng):
    _require_phase(state, Phase.LOBBY)
    if len(state.players) < 2:
        raise RuleError("Need at least 2 players")
    if len(state.players) > 6:
        raise RuleError("Max 6 players")
    # Deal starting hands.
    per_player = DEAL_2_4 if len(state.players) <= 4 else DEAL_5_6
    if not state.tower_draw:
        raise RuleError("Tower deck is empty; cannot deal")
    for p in state.players:
        for _ in range(per_player):
            if not state.tower_draw:
                break
            p.add_card(state.tower_draw.pop())
        p.position = board.data.start_space
    # Same number of coins as there are players: everybody gets one.
    state.coins_total = len(state.players)
    state.coins_available = state.coins_total
    # Initialise warders and jewels if not already.
    if not state.warders:
        for w in board.data.initial_warders:
            state.warders.append(Warder(id=w.id, location=w.location))
    if not state.jewels_available:
        state.jewels_available = dict(board.data.initial_jewel_locations)
    # Random turn order if not pre-set by the lobby.
    if not state.turn_order:
        order = [p.username for p in state.players]
        rng.shuffle(order)
        state.turn_order = order
    state.phase = Phase.TURN_START
    evs = [_ev(
        "game_started", order=state.turn_order, hand_size=per_player,
        mode=state.mode, coins=state.coins_total,
    )]
    _log(state, evs)
    return state, evs


# =========================================================================
# Turn start / pre-roll
# =========================================================================


#: Effects that are still legal once the dice are down and the move is over.
#:
#: Some cards are only worth playing when you know how the turn went. A Tower
#: Pass buys the extra turn you now know you need; Sanctuary is a retreat you
#: take after seeing where you landed. The Pardons and Confession go further —
#: they answer a confinement that a *landing* imposed, so before this they were
#: unreachable at the only moment they mattered.
#:
#: Everything absent from this set stays pre-roll-only, because it acts on a
#: roll that has already happened: Disguise (handled during path choice
#: instead), Firecrackers, Binary Disruption, Lasso.
POST_MOVE_PLAYABLE_EFFECTS = frozenset({
    "tower_pass",
    "sanctuary",
    "confession",
    "royal_pardon",
    "rack_pardon",
    "traversal_beauchamp_escape",
})


#: Cards a locked-up player may play at any time, on their turn or off it.
#:
#: These only ever act on their owner, and the Rack in particular gives them no
#: turn to act on: a racked player is skipped outright (see ``_intent_end_turn``),
#: so requiring it to be their turn would make a Rack Pardon a card that can
#: never be played.
SELF_RESCUE_EFFECTS = frozenset({
    "rack_pardon",
    "royal_pardon",
    "traversal_beauchamp_escape",
})

#: Phases in which no card may be played at all: the game isn't running, or a
#: fight owns the table and has its own card-play path.
_NO_CARD_PLAY_PHASES = (Phase.LOBBY, Phase.COMBAT, Phase.GAME_OVER)


def _intent_play_card_pre_roll(state, payload, *, board, rng):
    """Play a utility/custom card from hand.

    Named for the pre-roll case it was written for, but it now covers three
    windows:

    * the pre-roll phases, as before;
    * ``TURN_END`` for the cards in :data:`POST_MOVE_PLAYABLE_EFFECTS`, which
      are only worth playing once you can see how the turn went;
    * any time at all, for a confined player reaching for one of
      :data:`SELF_RESCUE_EFFECTS`.
    """
    username = payload["username"]
    card_id = payload["card_id"]
    try:
        player = state.player(username)
    except KeyError as exc:
        raise RuleError(f"Unknown player: {username}") from exc
    card = next((c for c in player.hand if c.id == card_id), None)
    if card is None:
        raise RuleError(f"No such card in hand: {card_id}")
    if state.phase in _NO_CARD_PLAY_PHASES:
        raise RuleError(f"Cannot play cards during {state.phase.value}")
    if not (player.confined and card.effect_key in SELF_RESCUE_EFFECTS):
        _require_phase(
            state, Phase.TURN_START, Phase.PRE_ROLL, Phase.ACCREDITATION_ATTEMPT,
            Phase.TURN_END,
        )
        _require_current_player(state, username)
    # Only certain categories are playable pre-roll.
    if card.kind != "tower":
        raise RuleError("Only tower cards are played pre-roll")
    if card.category in ("weapon", "burglary"):
        raise RuleError(f"Cannot play {card.name} pre-roll")
    if card.effect_key is None:
        raise RuleError(f"Card {card.name} has no effect")
    if state.phase == Phase.TURN_END and card.effect_key not in POST_MOVE_PLAYABLE_EFFECTS:
        raise RuleError(f"{card.name} must be played before you roll")
    # Remove card first so dispatch can't see it in hand.
    player.remove_card(card_id)
    state.tower_discard.append(card)
    try:
        _, evs = dispatch_effect(card.effect_key, state, player, dict(payload.get("params") or {}) | card.params,
                                 board=board, rng=rng)
    except EffectError as exc:
        # Restore card on failure.
        state.tower_discard.remove(card)
        player.add_card(card)
        raise RuleError(str(exc)) from exc
    # Both of these belong to the acting player's own turn. An off-turn
    # self-rescue must not write into somebody else's turn context, nor nudge
    # their phase out from under them.
    if state.turn_order and username == state.turn_order[state.current_turn_index]:
        state.turn.cards_played_this_turn.append(card_id)
        if state.phase == Phase.TURN_START:
            state.phase = Phase.PRE_ROLL
    _log(state, evs)
    return state, evs


# =========================================================================
# Card redraw (spend the turn trading cards instead of moving)
# =========================================================================


def _intent_redraw_cards(state, payload, *, board, rng):
    """Trade ``n`` cards from hand for ``n - 1`` off the tower deck.

    Offered instead of rolling: the player stays exactly where they are and
    spends the whole turn on the exchange. The card you lose on the deal is the
    price — which is why a single card buys nothing and the intent refuses it
    rather than quietly burning the card for an empty hand.

    Whether the new cards are any better is the gamble; the old ones go to the
    discard pile, so a hand emptied this way can come back around.
    """
    _require_phase(state, Phase.TURN_START, Phase.PRE_ROLL)
    username = payload["username"]
    player = _require_current_player(state, username)
    if player.confined:
        raise RuleError("You cannot trade cards while locked up")
    if player.miss_next_turn:
        raise RuleError("You are missing this turn")

    card_ids = list(payload.get("card_ids") or [])
    if len(set(card_ids)) != len(card_ids):
        raise RuleError("Duplicate card in redraw selection")
    if len(card_ids) < 2:
        raise RuleError("Redraw needs at least 2 cards — one is the fee")
    held = {c.id: c for c in player.hand}
    missing = [cid for cid in card_ids if cid not in held]
    if missing:
        raise RuleError(f"Not in your hand: {', '.join(missing)}")

    # Discard first, so the cards handed in can come back out of a reshuffle
    # rather than being unavailable for the rest of the game.
    given = [player.remove_card(cid) for cid in card_ids]
    state.tower_discard.extend(c for c in given if c is not None)

    wanted = len(card_ids) - 1
    received: list[Card] = []
    for _ in range(wanted):
        drew = _draw_tower(state)
        if drew is None:
            break
        player.add_card(drew)
        received.append(drew)

    evs = [_ev(
        "cards_redrawn",
        player=player.username,
        given=[c.id for c in given if c is not None],
        received=[c.id for c in received],
        given_count=len(card_ids),
        received_count=len(received),
        # Only relevant when the deck (and its discard pile) ran dry mid-deal.
        short_by=wanted - len(received),
    )]
    # The trade is the turn. Land on TURN_END rather than auto-advancing so a
    # Tower Pass is still on the table.
    state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


# =========================================================================
# Dice rolling
# =========================================================================


def _intent_roll_dice(state, payload, *, board, rng):
    _require_phase(state, Phase.TURN_START, Phase.PRE_ROLL, Phase.ACCREDITATION_ATTEMPT)
    username = payload["username"]
    player = _require_current_player(state, username)

    # Confinement / miss turn checks.
    if player.miss_next_turn:
        player.miss_next_turn = False
        # Hospital auto-clears on the missed turn.
        if player.status == Status.HOSPITAL:
            player.status = Status.NORMAL
        state.phase = Phase.TURN_END
        _log(state, [_ev("missed_turn", player=player.username)])
        return state, [_ev("missed_turn", player=player.username)]

    if player.status in (Status.IMPRISONED, Status.TORTURED, Status.RACKED):
        # Must roll a double to escape (except Rack — rolling doesn't help).
        roll = rng.roll_dice(2)
        total = sum(roll)
        state.turn.roll = roll
        evs = [_ev("dice_rolled", player=player.username, roll=roll, total=total)]
        is_double = roll[0] == roll[1]
        if player.status == Status.RACKED:
            # No escape by rolling. Decrement timer.
            player.status_turns_remaining = max(0, player.status_turns_remaining - 1)
            if player.status_turns_remaining == 0:
                evs.extend(_release_from_rack(state, player, board))
            state.phase = Phase.TURN_END
            _log(state, evs)
            return state, evs
        if is_double:
            # Escape! Use the roll for movement.
            player.status = Status.NORMAL
            player.status_turns_remaining = 0
            evs.append(_ev("confinement_escaped", player=player.username))
            # Then fall through to normal movement.
        else:
            player.status_turns_remaining = max(0, player.status_turns_remaining - 1)
            if player.status_turns_remaining == 0:
                player.status = Status.NORMAL
                evs.append(_ev("confinement_expired", player=player.username))
            state.phase = Phase.TURN_END
            _log(state, evs)
            return state, evs
    else:
        roll = rng.roll_dice(2)
        total = sum(roll)
        state.turn.roll = roll
        evs = [_ev("dice_rolled", player=player.username, roll=roll, total=total)]
        is_double = roll[0] == roll[1]

    def _three_doubles() -> bool:
        """Bump the doubles counter; True when the third one sends you down."""
        state.turn.consecutive_doubles += 1
        if state.turn.consecutive_doubles < 3:
            return False
        player.position = board.data.bloody_tower_space
        player.status = Status.IMPRISONED
        player.status_turns_remaining = 3
        state.turn.consecutive_doubles = 0
        state.phase = Phase.TURN_END
        evs.append(_ev("three_doubles_bloody_tower", player=player.username))
        return True

    # Accreditation trial.
    if player.trying_accreditation and not player.accredited:
        if total % 2 == 1:
            player.accredited = True
            player.trying_accreditation = False
            evs.append(_ev("accredited", player=player.username, via="odd_roll"))
            # Use roll to move in the inner ward (free graph movement).
        elif is_double:
            # Every double is even, so without this a double would be an
            # automatic failure. The clerks give you another go instead — the
            # three-doubles rule still applies.
            if not _three_doubles():
                state.phase = Phase.PRE_ROLL
                evs.append(_ev(
                    "accreditation_retry", player=player.username, roll=roll,
                ))
            _log(state, evs)
            return state, evs
        else:
            state.phase = Phase.TURN_END
            evs.append(_ev("accreditation_failed", player=player.username))
            _log(state, evs)
            return state, evs

    # Doubles tracking.
    if is_double:
        if _three_doubles():
            _log(state, evs)
            return state, evs
        # Regular double: grant an extra roll at the end of this turn.
        state.turn.extra_turns_queued += 1

    # Binary disruption or split-7?
    if state.turn.binary_disruption_armed or total == 7:
        movable = _split_movable_targets(state, board, player, total)
        if movable:
            state.phase = Phase.SPLIT_SEVEN_ASSIGN
            state.turn.pending_split = PendingSplitSeven(
                total=total,
                source="binary_disruption" if state.turn.binary_disruption_armed else "seven",
                movable_targets=movable,
            )
            state.turn.binary_disruption_armed = False
            evs.append(_ev(
                "split_assign_required", total=total,
                movable_targets={k: list(v) for k, v in movable.items()},
            ))
            _log(state, evs)
            return state, evs
        # Nobody can be given any part of the roll (everyone else is boxed in),
        # so there is nothing to split — the roller takes the lot.
        state.turn.binary_disruption_armed = False
        evs.append(_ev("split_unavailable", player=player.username, total=total))

    # Normal movement path.
    evs.extend(_enter_movement_phase(state, board, player, total))
    _log(state, evs)
    return state, evs


def _split_movable_targets(
    state: GameState, board: Board, roller: PlayerState, total: int,
) -> dict[str, list[int]]:
    """Opponents who could actually be moved by part of a split roll.

    Maps username → the leg sizes (1..total-1) that give them at least one legal
    destination. An un-accredited piece parked on Queen's House is the case that
    prompted this: the wall walk is forward-only and dead-ends there, so no leg
    size moves them anywhere and offering them as a split target would silently
    burn the roller's steps.
    """
    blocked = _warder_blocked_spaces(state, board)
    movable: dict[str, list[int]] = {}
    for p in state.players:
        if p.username == roller.username:
            continue
        # A locked-up piece stays locked up. Moving it would leave the player
        # "imprisoned" on some unrelated square, which is how confinement was
        # being laundered into a free teleport.
        if p.confined:
            continue
        # Nor may the roll reach into the White Tower (or any square the board
        # marks immune). Inside, movement is forward-only along a fixed chain,
        # so a "free" step for the roller is a shove down the queue for the
        # victim — one square past the jewels sits the Rack Sender.
        if immune_to_forced_moves(board, p):
            continue
        others = [q.position for q in state.players if q.username != p.username]
        legs = [
            n for n in range(1, total)
            if compute_destinations(
                board, p.position, n, p,
                other_player_positions=others,
                visited_this_turn=[p.position],
                warder_blocking_spaces=blocked,
                allow_combat_stops=False,
            ).destinations
        ]
        if legs:
            movable[p.username] = legs
    return movable


def _enter_movement_phase(state: GameState, board: Board, player: PlayerState, steps: int) -> list[dict[str, Any]]:
    others = [p.position for p in state.players if p.username != player.username]
    # Seed the visited-this-turn list with the player's current position on
    # first use so the pathfinder excludes it consistently.
    if not state.turn.visited_this_turn:
        state.turn.visited_this_turn = [player.position]
    opts = compute_destinations(
        board, player.position, steps, player,
        other_player_positions=others,
        visited_this_turn=state.turn.visited_this_turn,
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
        disguise_available=_disguise_card(player) is not None,
    )
    if not opts.destinations:
        # No legal move — just end turn.
        state.phase = Phase.TURN_END
        return [_ev("no_legal_move", player=player.username, steps=steps)]
    state.turn.pending_move = PendingMove(
        steps=steps,
        destinations=opts.destinations,
        requires_disguise=sorted(opts.requires_disguise),
    )
    if opts.forced_single and (only := opts.only_destination()):
        # Auto-commit.
        return _commit_move(state, board, player, only, opts.destinations[only])
    state.phase = Phase.CHOOSING_PATH
    return [_ev("choose_path", player=player.username, destinations=list(opts.destinations.keys()))]


def _commit_move(state: GameState, board: Board, player: PlayerState, dest: str, path: list[str]) -> list[dict[str, Any]]:
    old = player.position
    # The exit used to grab anyone who crossed it holding a jewel and a coin.
    # It can't any more: cashing in costs you your whole hand, so walking past
    # the door has to be allowed. ``compute_destinations`` offers the exit as a
    # destination whenever a coin-holder can reach it — choosing it is the
    # decision, and landing on it is what cashes you in.
    player.position = dest
    state.turn.pending_move = None
    # Record every newly-stepped-on square so subsequent movement this turn
    # (doubles re-roll, split-7 second leg, etc.) cannot revisit them.
    for sid in path:
        if sid not in state.turn.visited_this_turn:
            state.turn.visited_this_turn.append(sid)
    state.phase = Phase.MOVING
    evs = [_ev("player_moved", player=player.username, src=old, dst=dest, path=path)]
    evs.extend(_resolve_landing(state, board, player))
    return evs


def resolve_landing_after_summons(
    state: GameState, board: Board, player: PlayerState,
) -> list[dict[str, Any]]:
    """Landing resolution for a player a card effect has just summoned.

    Public because ``cards_effects`` calls it (via a deferred import) from
    ``_summon_to``. The raven draw is suppressed: being summoned onto a raven
    square must not draw a second raven card while the first is still resolving.
    """
    return _resolve_landing(state, board, player, depth=1, allow_raven=False)


def _resolve_landing(
    state: GameState,
    board: Board,
    player: PlayerState,
    depth: int = 0,
    allow_raven: bool = True,
    own_move: bool = True,
) -> list[dict[str, Any]]:
    """Trigger landing effects for the current space, updating the phase.

    ``depth`` counts teleports chained by space actions (land on "Go to Shop",
    resolve the Shop, ...). It guards against a board topology that could send
    a player round a cycle of teleporting squares forever.

    ``own_move=False`` says somebody else put this player here — a split 7, a
    Lasso, a card. The Cradle Tower is skipped in that case: cashing in costs
    you your hand and can win you the game, so it has to be your own decision,
    not a side effect of another player's roll.
    """
    evs: list[dict[str, Any]] = []
    space = board.space(player.position)
    rules_cfg = board.data.rules

    # Loose jewels (Metallicity) auto-acquire.
    loose = state.loose_jewels.get(space.id)
    if loose:
        for jid in loose:
            player.jewels.append(jid)
            evs.append(_ev("jewel_auto_acquired", player=player.username, jewel=jid))
        state.loose_jewels[space.id] = []

    # devereux: pick up coin if available and not already held.
    if space.kind == "devereux" and getattr(rules_cfg, "devereux_grants_coin", True):
        if not player.has_coin and state.coins_available > 0:
            player.has_coin = True
            state.coins_available -= 1
            evs.append(_ev(
                "coin_picked_up", player=player.username,
                remaining=state.coins_available, total=state.coins_total,
            ))

    # Rack sender (wt_13_11): straight to the Rack, no roll, no appeal.
    if space.kind == "rack_sender":
        from .cards_effects import send_to_rack as _send_to_rack
        evs.append(_ev("rack_sender_triggered", player=player.username, space=space.id))
        evs.extend(_send_to_rack(state, player, board))
        state.phase = Phase.TURN_END
        return evs

    # Bloody / Bowyer Tower: walking in is the same as being marched in — the
    # door locks behind you. Driven by ``confine_on_landing_kinds`` so the
    # exception (Beauchamp, which confines only via its raven card) stays
    # visible in the board data rather than buried here.
    confine_kinds = dict(getattr(rules_cfg, "confine_on_landing_kinds", {}) or {})
    confine_status = confine_kinds.get(space.kind)
    if confine_status:
        player.status = Status(confine_status)
        player.status_turns_remaining = int(getattr(rules_cfg, "confinement_turns", 3))
        evs.append(_ev(
            "confined_on_landing",
            player=player.username, space=space.id, label=space.label or None,
            status=confine_status, turns=player.status_turns_remaining,
        ))
        state.phase = Phase.TURN_END
        return evs

    # Squares that simply cost you your next turn: the benches, the Hospital,
    # and the Shop (browsing takes a while). Driven by the board so the data
    # stays authoritative — see ``miss_turn_on_landing_kinds``.
    miss_kinds = set(getattr(rules_cfg, "miss_turn_on_landing_kinds", []) or [])
    if space.kind in miss_kinds:
        player.miss_next_turn = True
        # NB: ``kind`` is _ev's positional event-name parameter — the payload
        # key for the space kind has to be called something else.
        evs.append(_ev(
            "miss_turn_on_landing" if space.kind != "bench" else "resting_on_bench",
            player=player.username, space=space.id, space_kind=space.kind,
            label=space.label or None,
        ))

    # Jewel space (unclaimed): enter attempt phase.
    if space.kind == "jewel":
        jewel = state.jewel_at_space(space.id)
        if jewel is not None:
            state.turn.pending_jewel = PendingJewelAttempt(jewel_id=jewel, space_id=space.id, source="landing")
            state.phase = Phase.JEWEL_ATTEMPT
            evs.append(_ev("jewel_attempt_offered", jewel=jewel, space=space.id))
            return evs

    # Queens House: mark "trying accreditation" for future turns.
    if space.kind == "queens_house" and not player.accredited:
        player.trying_accreditation = True
        evs.append(_ev("trying_accreditation", player=player.username))

    # Raven trigger.
    if space.kind == "raven_trigger" and allow_raven:
        evs.extend(_draw_raven_and_resolve(state, board, player))
        if state.phase == Phase.RAVEN_EFFECT:
            return evs
        # Non-interactive effects auto-resolve inside the draw above and leave
        # the phase at MOVING. ``go_to_jewel_view`` queues an immediate theft
        # attempt that way, so honour it here — otherwise the tail of this
        # function would force MOVING → TURN_END and silently drop it. (The
        # interactive path does the same check in _intent_resolve_raven_effect.)
        if state.turn.pending_jewel is not None:
            state.phase = Phase.JEWEL_ATTEMPT
            return evs
        if player.position != space.id:
            # The card moved the player. Their destination has already had its
            # own landing resolved (or, for punishment cards, deliberately
            # hasn't) — either way, don't go on applying *this* square's
            # effects to someone who is no longer standing on it.
            if state.phase == Phase.MOVING:
                state.phase = Phase.TURN_END
            return evs

    # Tower-card-on-landing: consult the board rules for which kinds trigger,
    # and skip spaces explicitly listed as exceptions (e.g. Broad Arrow Tower,
    # which triggers its own ``surrender_weapons`` action instead).
    draw_kinds = set(getattr(rules_cfg, "tower_card_draw_kinds", []) or ["tower"])
    draw_exceptions = set(getattr(rules_cfg, "tower_card_draw_exception_space_ids", []) or [])
    if space.kind in draw_kinds and space.id not in draw_exceptions:
        drew = _draw_tower(state)
        if drew is not None:
            player.add_card(drew)
            evs.append(_ev("tower_card_drawn", player=player.username, card=drew.id))

    # Space-specific action (e.g. extra_turn, go_back_by_roll, go_to_and_accredit,
    # surrender_weapons). These fire *after* the generic card draw so e.g.
    # ww29_broad_arrow (excluded above) can still run its surrender action.
    if space.action is not None:
        action_evs, handled_terminal = _dispatch_space_action(state, board, player, space, depth)
        evs.extend(action_evs)
        if handled_terminal:
            return evs

    # The Cradle Tower. Costs a coin, not a jewel: walking out empty-handed to
    # be dealt a fresh hand is a legitimate play, and the one way back from a
    # Rack that took every card you had. Returns straight away — the player is
    # standing on the start square now, so nothing else about this square
    # (co-located enemies, least of all) still applies to them.
    if space.kind == "escape" and player.has_coin and own_move:
        evs.extend(_use_the_exit(state, board, player))
        return evs

    # Bloody / Beauchamp / Bowyer / Rack landed on by normal movement:
    # "just visiting" unless the move was directed by an effect. Our landing
    # resolver is called only from normal movement, so these are visit-only.
    # (Raven effects teleport and set status themselves.)

    # Firecrackers escape: a player affected by Firecrackers escapes the
    # effect the moment they land outside the White Tower.
    if space.region != "white_tower" and player.username in state.firecrackers_affected:
        state.firecrackers_affected.remove(player.username)
        evs.append(_ev("firecrackers_escaped", player=player.username, space=space.id))

    # Pass-through combat: if another player is sharing this space (and we're
    # not in the White Tower), surface the option. The player may send
    # ``initiate_combat`` before ``end_turn``; either way, the turn ends here.
    if space.region != "white_tower":
        co_located = [
            p.username for p in state.players
            if p.username != player.username and p.position == player.position
        ]
        if co_located:
            evs.append(_ev(
                "combat_available", player=player.username,
                targets=co_located, space=space.id,
            ))

    # After landing resolution (no pending prompts), drop to TURN_END — the
    # engine's auto-transitions may queue extra turns.
    if state.phase in (Phase.MOVING,):
        state.phase = Phase.TURN_END
    return evs


def _use_the_exit(state: GameState, board: Board, player: PlayerState) -> list[dict[str, Any]]:
    """Slip out through the Cradle Tower, bank the haul, and come back in.

    Leaving the Tower is not the end of anybody's game. You hand over
    everything you are carrying: the jewels go to a hideout where nothing can
    reach them and where they finally count for something, the coin goes back
    to the Devereux pile for somebody else to pick up, and the whole hand is
    shuffled back into the tower deck. Then you are dealt a fresh opening hand
    and put back on the start square — still accredited, because the clerks'
    paperwork does not expire while you are out.

    A coin is the whole price of admission. Going out with no jewels at all is
    a real play: it is how a player stripped of their hand on the Rack gets a
    new one.
    """
    banked = list(player.jewels)
    player.banked_jewels.extend(banked)
    player.jewels = []

    if player.has_coin:
        player.has_coin = False
        state.coins_available = min(state.coins_total, state.coins_available + 1)

    surrendered = player.hand
    player.hand = []
    if getattr(board.data.rules, "escape_reshuffles_old_hand_into_deck", True):
        state.tower_draw.extend(surrendered)
        try:
            _GLOBAL_RNG.get().shuffle(state.tower_draw)
        except RuntimeError:
            # RNG not wired (a unit test calling in directly); leave the order.
            pass
    else:
        state.tower_discard.extend(surrendered)

    dealt = 0
    for _ in range(_deal_size(state)):
        card = _draw_tower(state)
        if card is None:
            break
        player.add_card(card)
        dealt += 1

    player.position = board.data.start_space
    state.turn.pending_move = None
    evs = [_ev(
        "jewels_banked", player=player.username, jewels=banked,
        cards_surrendered=len(surrendered), cards_dealt=dealt,
    )]
    evs.extend(_check_jewel_endgame(state, board))
    if state.phase != Phase.GAME_OVER:
        state.phase = Phase.TURN_END
    return evs


def _deal_size(state: GameState) -> int:
    """Opening hand size, and therefore the size of an escapee's new one."""
    return DEAL_2_4 if len(state.players) <= 4 else DEAL_5_6


def _jewels_in_play(board: Board) -> int:
    """How many jewels the game is played for. Five on the real board."""
    return len(board.data.initial_jewel_locations) or 5


def _check_jewel_endgame(state: GameState, board: Board) -> list[dict[str, Any]]:
    """End the game if the banked piles have settled it. Called after a bank.

    Banking is the only thing that can move this needle, so this is the only
    place it needs asking.

    Fast: the first jewel anybody banks wins it.

    Slow: the game runs until the lead is beyond reach. A player has clinched
    when their banked pile beats what every rival could still reach even by
    winning every remaining jewel — so with five in play, banking three ends
    it, because two is all that is left for anyone else. Failing that, it ends
    when there is nothing left to bank, and the ranking decides it.
    """
    total = _jewels_in_play(board)
    banked = {p.username: len(p.banked_jewels) for p in state.players}

    if state.mode == "fast":
        winner = next((p.username for p in state.players if p.banked_jewels), None)
        if winner is None:
            return []
        state.phase = Phase.GAME_OVER
        state.winner = winner
        return [_ev("fast_win", player=winner)]

    unbanked = max(0, total - sum(banked.values()))
    leader = max(banked, key=lambda u: (banked[u], u)) if banked else None
    clinched = (
        leader is not None
        and all(banked[leader] > banked[u] + unbanked for u in banked if u != leader)
    )
    if not clinched and unbanked > 0:
        return []

    state.phase = Phase.GAME_OVER
    ranking = _slow_ranking(state)
    state.winner = ranking[0]["username"] if ranking else None
    return [_ev(
        "slow_game_over", winner=state.winner, ranking=ranking,
        reason="majority_clinched" if clinched else "all_jewels_banked",
    )]


def _draw_tower(state: GameState) -> Optional[Card]:
    if not state.tower_draw:
        if not state.tower_discard:
            return None
        state.tower_draw = state.tower_discard
        state.tower_discard = []
        try:
            _GLOBAL_RNG.get().shuffle(state.tower_draw)
        except RuntimeError:
            # RNG not wired (e.g. unit test called directly); leave order as-is.
            pass
    if state.tower_draw:
        return state.tower_draw.pop()
    return None


# =========================================================================
# Space action dispatch (landing on a space with a named ``action.key``)
# =========================================================================


# A space action that teleports resolves the destination's own landing, which
# may teleport again. Real boards don't cycle, but a data edit could — bail out
# rather than blowing the stack.
_MAX_LANDING_DEPTH = 4


def _action_destination(board: Board, params: dict[str, Any]) -> Optional[str]:
    """Destination for a teleporting space action.

    Accepts either an explicit ``space_id`` or a ``destination_kind`` naming one
    of the board's ``<kind>_space`` anchors (``queens_house`` → the board's
    ``queens_house_space``, ``shop`` → ``shop_space``, ...).
    """
    sid = params.get("space_id")
    if sid and board.has_space(sid):
        return sid
    kind = params.get("destination_kind")
    if kind:
        anchor = getattr(board.data, f"{kind}_space", None)
        if anchor and board.has_space(anchor):
            return anchor
    return None


def _dispatch_space_action(
    state: GameState, board: Board, player: PlayerState, space, depth: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """Run the per-space ``action`` handler for the just-landed space.

    Returns ``(events, terminal)``. ``terminal`` is True when the handler has
    itself set the phase (e.g. TURN_END after teleport) and the caller
    (``_resolve_landing``) must stop further landing resolution.
    """
    action = space.action
    key = action.key
    params = dict(action.params or {})
    evs: list[dict[str, Any]] = []

    if key == "extra_turn":
        # The player who lands here gets the extra turn — which is not always
        # the player whose turn it is. A split-7 leg can push an opponent onto
        # this square, and crediting ``turn.extra_turns_queued`` would hand the
        # bonus to the roller. Park it on the opponent instead; ``_intent_end_turn``
        # pays it out when play reaches them.
        if player.username == state.current_player().username:
            state.turn.extra_turns_queued += 1
            if params.get("resets_consecutive_doubles"):
                state.turn.consecutive_doubles = 0
        else:
            player.extra_turns_pending += 1
        evs.append(_ev("extra_turn_granted", player=player.username, space=space.id))
        return evs, False

    if key == "go_back_by_roll":
        # "Go back the number thrown" means back along the route you just walked,
        # not back that many positions in wall-walk order. Since the number
        # thrown is the number that brought you here, retracing it normally puts
        # you exactly where you started the turn — which is the point of the
        # square. Counting board positions instead landed players on squares
        # they had never been near.
        total = sum(state.turn.roll) if state.turn.roll else 0
        if total <= 0:
            return evs, False
        trail = state.turn.visited_this_turn
        # Where are we on the trail? Normally the last entry, but be defensive:
        # a teleport chain could have appended something after us.
        try:
            here = len(trail) - 1 - trail[::-1].index(player.position)
        except ValueError:
            here = len(trail) - 1
        idx = here - total
        target_id = trail[idx] if idx >= 0 else (trail[0] if trail else player.position)
        # A split 7 hands out fewer steps than the dice show, so the retrace can
        # run off the start of the trail. Keep going backwards along the wall
        # walk for the remainder; a trail that starts off the wall walk simply
        # stops at its beginning.
        overshoot = max(0, -idx)
        while overshoot > 0:
            prev = board.prev_wall_walk_space(target_id)
            if prev is None:
                break
            target_id = prev
            overshoot -= 1
        if target_id == player.position:
            return evs, False
        old = player.position
        player.position = target_id
        # Visited tracking: the teleport destination counts as visited too.
        if target_id not in state.turn.visited_this_turn:
            state.turn.visited_this_turn.append(target_id)
        evs.append(_ev(
            "go_back_by_roll", player=player.username,
            src=old, dst=target_id, steps=total,
        ))
        # Resolve landing on the new square. The wall walk is linear and the
        # retrace only moves backwards, so this cannot land on another
        # go_back_by_roll square and recurse.
        evs.extend(_resolve_landing(state, board, player))
        return evs, True

    if key == "go_to_and_accredit":
        qh = board.data.queens_house_space
        if not qh:
            return evs, False
        old = player.position
        player.position = qh
        if qh not in state.turn.visited_this_turn:
            state.turn.visited_this_turn.append(qh)
        player.accredited = True
        player.trying_accreditation = False
        evs.append(_ev(
            "go_to_and_accredit", player=player.username, src=old, dst=qh,
        ))
        evs.append(_ev("accredited", player=player.username, via="space_action"))
        if params.get("ends_turn_on_landing"):
            state.phase = Phase.TURN_END
            return evs, True
        return evs, False

    if key in ("go_to", "go_to_and_miss_turn"):
        # "Go to Queen's House" / "Go to Shop" / "Go to Broad Arrow Tower", and
        # ww04_guidebook which also costs the player their next turn.
        dest = _action_destination(board, params)
        if dest is None:
            evs.append(_ev(
                "space_action_failed", space=space.id, key=key,
                reason="unresolved_destination",
            ))
            return evs, False
        if key == "go_to_and_miss_turn":
            player.miss_next_turn = True
        if depth >= _MAX_LANDING_DEPTH:
            evs.append(_ev("landing_chain_truncated", space=space.id, key=key))
            state.phase = Phase.TURN_END
            return evs, True
        old = player.position
        player.position = dest
        if dest not in state.turn.visited_this_turn:
            state.turn.visited_this_turn.append(dest)
        evs.append(_ev(
            "sent_to_space", player=player.username, src=old, dst=dest,
            space=space.id, label=space.label or None,
            misses_turn=(key == "go_to_and_miss_turn"),
        ))
        # Resolve the destination's own landing effects — the Shop and Queen's
        # House both do something on arrival.
        evs.extend(_resolve_landing(state, board, player, depth=depth + 1))
        return evs, True

    if key == "change_card":
        # ww41 / ww58 / ww69: discard one card from hand, draw the top of the
        # tower deck. The player picks the discard, so park a prompt.
        if not player.hand:
            drew = _draw_tower(state)
            if drew is not None:
                player.add_card(drew)
                evs.append(_ev("tower_card_drawn", player=player.username, card=drew.id))
            evs.append(_ev(
                "card_change_skipped", player=player.username,
                space=space.id, reason="empty_hand",
            ))
            return evs, False
        state.turn.pending_card_change = PendingCardChange(kind="change", space_id=space.id)
        state.phase = Phase.CARD_CHANGE
        evs.append(_ev("card_change_offered", player=player.username, space=space.id))
        return evs, True

    if key == "swap_random_with_other_player":
        # ww75: give a card of your choosing to an opponent of your choosing,
        # and take a random one from their hand in exchange.
        candidates = [
            p.username for p in state.players
            if p.username != player.username and p.hand
        ]
        if not player.hand or not candidates:
            evs.append(_ev(
                "card_swap_skipped", player=player.username, space=space.id,
                reason="empty_hand" if not player.hand else "no_eligible_opponent",
            ))
            return evs, False
        state.turn.pending_card_change = PendingCardChange(
            kind="swap", space_id=space.id, candidates=candidates,
        )
        state.phase = Phase.CARD_CHANGE
        evs.append(_ev(
            "card_swap_offered", player=player.username,
            space=space.id, candidates=candidates,
        ))
        return evs, True

    if key == "miss_turn":
        # ww21_miss, ww28_miss, and ww60_questioned ("Questioned by a guard").
        player.miss_next_turn = True
        evs.append(_ev(
            "miss_turn_queued", player=player.username,
            space=space.id, label=space.label or None,
        ))
        return evs, False

    if key == "surrender_weapons":
        # ww29_broad_arrow: player discards every weapon card in hand.
        surrendered: list[str] = []
        for c in list(player.hand):
            if c.category == "weapon":
                player.remove_card(c.id)
                state.tower_discard.append(c)
                surrendered.append(c.id)
        evs.append(_ev(
            "weapons_surrendered", player=player.username, cards=surrendered,
            count=len(surrendered), space=space.id,
        ))
        return evs, False

    # Unknown action key — log and continue so the engine stays loud rather
    # than silently dropping data.
    evs.append(_ev("unhandled_space_action", space=space.id, key=key))
    return evs, False


def _draw_raven_and_resolve(state: GameState, board: Board, player: PlayerState) -> list[dict[str, Any]]:
    if not state.raven_draw:
        # Reshuffle.
        if state.raven_discard:
            state.raven_draw = state.raven_discard
            state.raven_discard = []
            # Rng will be used elsewhere; we'll trust the engine to shuffle.
    if not state.raven_draw:
        return [_ev("raven_deck_empty")]
    card = state.raven_draw.pop()
    state.raven_discard.append(card)
    state.active_raven_notice = RavenNotice(
        card_id=card.id,
        effect_key=card.effect_key or "",
        drawer=player.username,
        params=dict(card.params),
    )
    evs: list[dict[str, Any]] = [_ev(
        "raven_card_drawn",
        player=player.username, card=card.id, effect=card.effect_key,
        params=dict(card.params),
    )]
    # Nothing fires yet. The card is dealt face-down and parked; the drawer
    # turns it over (``reveal_raven_notice``) and only then does the effect
    # resolve — otherwise pieces move before anyone has seen why.
    state.turn.pending_raven = PendingRavenEffect(
        effect_key=card.effect_key or "",
        card_id=card.id,
        params=dict(card.params),
        drawer=player.username,
    )
    state.phase = Phase.RAVEN_EFFECT
    return evs


def _resolve_pending_raven(
    state: GameState, board: Board, rng: Rng, extra_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Dispatch the parked raven effect and settle the phase afterwards."""
    pr = state.turn.pending_raven
    if pr is None:
        return []
    player = state.player(pr.drawer)
    merged = dict(pr.params) | dict(extra_params)
    from .cards_effects import dispatch as _dispatch
    try:
        _, evs = _dispatch(pr.effect_key, state, player, merged, board=board, rng=rng)
    except EffectError as exc:
        evs = [_ev("raven_effect_failed", effect=pr.effect_key, error=str(exc))]

    # An effect can come back still wanting input (e.g. "go to a location of
    # your choice" with nothing chosen yet). Leave it parked in that case.
    if any(e["kind"] == "raven_needs_input" for e in evs):
        return evs

    state.turn.pending_raven = None
    if state.phase == Phase.RAVEN_EFFECT:
        # go_to_jewel_view queues an immediate theft attempt; honour it.
        if state.turn.pending_jewel is not None:
            state.phase = Phase.JEWEL_ATTEMPT
        else:
            state.phase = Phase.TURN_END
    evs.extend(_resume_deferred_split_leg(state, board))
    return evs


def _raven_needs_input(ek: str, params: dict, state: GameState, board: Board, player: PlayerState) -> bool:
    if ek == "go_to_location":
        # Always: a Summons can be refused (at the cost of your next turn).
        return True
    if ek == "call_warder_to_post" and params.get("post") == "chooser":
        # Only worth asking if there's a warder to call *and* somewhere free
        # to call them to.
        from .cards_effects import free_warder_posts
        if (any(w.location == board.data.barracks_space for w in state.warders)
                and len(free_warder_posts(state, board)) > 1):
            return True
    if ek == "return_warder_to_barracks":
        out = [w for w in state.warders if w.location != board.data.barracks_space]
        if len(out) > 1:
            return True
    if ek == "rest_on_bench" and len(board.data.bench_space_ids) > 1:
        return True
    if ek == "photo_with_warder":
        occupied = [w.location for w in state.warders if w.location != board.data.barracks_space]
        candidates: set[str] = set()
        for ps in occupied:
            candidates.update(board.neighbors(ps))
        if len(candidates) > 1:
            return True
    if ek == "stopped_and_searched" and player.jewels:
        return True
    return False


# =========================================================================
# Choose move path
# =========================================================================


def _intent_choose_move_path(state, payload, *, board, rng):
    _require_phase(state, Phase.CHOOSING_PATH)
    username = payload["username"]
    player = _require_current_player(state, username)
    pm = state.turn.pending_move
    if pm is None:
        raise RuleError("No pending move")
    dest = payload["destination"]
    if dest not in pm.destinations:
        raise RuleError(f"Invalid destination: {dest}")

    # --- target-destination branch (roller choosing where split-7 target goes) ---
    if pm.is_for_target:
        target_name = pm.target_for_split
        roller_steps = pm.roller_steps_after_target
        if not target_name:
            raise RuleError("No target recorded for is_for_target move")
        target = state.player(target_name)
        path = pm.destinations[dest]
        old_pos = target.position
        target.position = dest
        state.turn.pending_move = None
        rest_evs = cancel_rest_if_moved_off(state, board, target, old_pos)
        # Clear CHOOSING_PATH now that the selection is committed; _resolve_landing
        # may override to RAVEN_EFFECT / JEWEL_ATTEMPT etc., and the guard below
        # will set TURN_END if nothing else grabs the phase.
        state.phase = Phase.MOVING
        evs: list[dict[str, Any]] = [_ev(
            "player_moved", player=target.username,
            src=old_pos, dst=dest, path=path, move_kind="split_seven",
        )]
        evs.extend(rest_evs)
        evs.extend(_resolve_landing(state, board, target, own_move=False))
        # target_first: the roller's own leg comes next, but only if the
        # target's landing hasn't opened a prompt. If it has, park the steps —
        # answering the prompt picks them back up.
        if roller_steps > 0:
            if _sub_phase_active(state):
                _defer_split_leg(state, DeferredSplitLeg(kind="roller", steps=roller_steps))
            else:
                evs.extend(_run_roller_split_leg(state, board, player, roller_steps, target=None, target_destination=None))
        if not _sub_phase_active(state):
            state.phase = Phase.TURN_END
        _log(state, evs)
        return state, evs

    # --- normal branch (roller choosing their own destination) ---
    # Optional: allow stopping early to initiate combat.
    stop_at = payload.get("stop_at")
    if stop_at is not None:
        path = pm.destinations[dest]
        if stop_at not in path:
            raise RuleError(f"{stop_at} not on selected path")
        dest = stop_at
        path = path[: path.index(stop_at) + 1]
    else:
        path = pm.destinations[dest]
    # Slipping past a manned post costs a Disguise. Charged off the committed
    # path rather than the offered ``requires_disguise`` list so that stopping
    # short of the post — which is free — isn't billed for it.
    pre_evs: list[dict[str, Any]] = []
    posts = _warder_blocked_spaces(state, board)
    if any(sid in posts for sid in path[1:]):
        card = _disguise_card(player)
        if card is None:
            raise RuleError("That route passes a Yeoman Warder and you have no Disguise")
        player.remove_card(card.id)
        state.tower_discard.append(card)
        state.turn.disguise_used = True
        state.turn.cards_played_this_turn.append(card.id)
        pre_evs.append(_ev(
            "disguise_played", player=player.username, via="move", space=dest,
        ))
    # Snapshot the split-7 continuation *before* _commit_move clears pending_move.
    split_target = pm.split_target
    split_n_other = pm.remaining_steps
    split_tdest_override = payload.get("target_destination") or pm.split_target_destination
    evs = pre_evs + _commit_move(state, board, player, dest, path)
    if split_target is not None and split_n_other > 0:
        # The roller's own landing may have opened a prompt; the target's leg
        # waits for it rather than overwriting the phase.
        if _sub_phase_active(state):
            _defer_split_leg(state, DeferredSplitLeg(
                kind="target", steps=split_n_other,
                target=split_target, target_destination=split_tdest_override,
            ))
        else:
            evs.extend(_resolve_split_target_leg(state, board, split_target, split_n_other, split_tdest_override))
            # Landing effects of either leg may have forced a new phase; only
            # fall through to TURN_END when nothing else needs the player's
            # attention.
            if not _sub_phase_active(state):
                state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


# =========================================================================
# Split 7
# =========================================================================


def _intent_assign_split_seven(state, payload, *, board, rng):
    _require_phase(state, Phase.SPLIT_SEVEN_ASSIGN)
    username = payload["username"]
    player = _require_current_player(state, username)
    split = state.turn.pending_split
    if split is None:
        raise RuleError("No pending split assignment")
    n_self = int(payload["n_self"])
    n_other = int(payload["n_other"])
    target_name = payload.get("target")
    target_destination = payload.get("target_destination")
    leg_order = payload.get("leg_order", "self_first")
    if leg_order not in ("self_first", "target_first"):
        raise RuleError("leg_order must be 'self_first' or 'target_first'")
    if n_self + n_other != split.total:
        raise RuleError("n_self + n_other must equal total")
    if n_self < 0 or n_other < 0:
        raise RuleError("Non-negative splits required")
    if n_other > 0:
        # Only hand steps to someone the roll can actually move — otherwise the
        # steps vanish and the roller has quietly thrown part of their turn away.
        legs = split.movable_targets.get(target_name or "")
        if not legs:
            raise RuleError(f"{target_name or 'That player'} cannot be moved by this roll")
        if n_other not in legs:
            raise RuleError(f"{target_name} has no legal move of {n_other}")
    evs: list[dict[str, Any]] = [_ev(
        "split_assigned", self=n_self, other=n_other,
        target=target_name, leg_order=leg_order,
    )]

    target_first = leg_order == "target_first" and n_other > 0 and target_name
    if target_first:
        # Resolve the target's leg before the roller moves. Drop out of
        # SPLIT_SEVEN_ASSIGN first so the phase the target's landing leaves
        # behind is readable — anything but MOVING/TURN_END means it opened a
        # prompt and the roller's leg has to wait.
        state.turn.pending_split = None
        state.phase = Phase.MOVING
        evs.extend(_resolve_split_target_leg(state, board, target_name, n_other, target_destination))
        if state.phase == Phase.CHOOSING_PATH:
            # Target has multiple destinations; roller must pick one via
            # choose_move_path.  Stash the roller's pending steps so we can
            # resume them after the target is placed.
            if n_self > 0 and state.turn.pending_move is not None:
                state.turn.pending_move.roller_steps_after_target = n_self
            _log(state, evs)
            return state, evs
        # Now the roller's leg — unless the target's landing opened a prompt of
        # its own, in which case running it here would overwrite that phase and
        # strand whoever owes the answer.
        if n_self > 0:
            if _sub_phase_active(state):
                _defer_split_leg(state, DeferredSplitLeg(kind="roller", steps=n_self))
            else:
                evs.extend(_run_roller_split_leg(state, board, player, n_self, target=None, target_destination=None))
        if not _sub_phase_active(state):
            state.phase = Phase.TURN_END
        _log(state, evs)
        return state, evs

    # Self first (default). Resolve the roller's leg; defer target leg if the
    # self-leg enters CHOOSING_PATH.
    if n_self > 0:
        evs.extend(_run_roller_split_leg(
            state, board, player, n_self,
            target=target_name if n_other > 0 else None,
            target_destination=target_destination,
        ))
        if state.phase == Phase.CHOOSING_PATH:
            _log(state, evs)
            return state, evs
    if n_self == 0 or not state.turn.pending_move:
        state.turn.pending_split = None
    # Target leg (only if not already deferred via pending_move).
    if state.turn.pending_move is None or state.turn.pending_move.split_target is None:
        if n_other > 0 and target_name:
            # The roller's landing may have opened a prompt of its own; park
            # the target's leg rather than trampling that phase.
            if _sub_phase_active(state):
                _defer_split_leg(state, DeferredSplitLeg(
                    kind="target", steps=n_other,
                    target=target_name, target_destination=target_destination,
                ))
            else:
                evs.extend(_resolve_split_target_leg(state, board, target_name, n_other, target_destination))
    if not _sub_phase_active(state):
        state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


def _run_roller_split_leg(
    state: GameState,
    board: Board,
    player: PlayerState,
    n_self: int,
    *,
    target: Optional[str],
    target_destination: Optional[str],
) -> list[dict[str, Any]]:
    """Resolve the roller's side of a split-7 move.

    On CHOOSING_PATH, stores ``target`` / ``target_destination`` in
    ``pending_move`` so the second leg can be picked up by
    ``_intent_choose_move_path``. Pass ``target=None`` when the target leg
    has already been resolved (e.g. ``leg_order='target_first'``).
    """
    evs: list[dict[str, Any]] = []
    others = [p.position for p in state.players if p.username != player.username]
    if not state.turn.visited_this_turn:
        state.turn.visited_this_turn = [player.position]
    opts = compute_destinations(
        board, player.position, n_self, player,
        other_player_positions=others,
        visited_this_turn=state.turn.visited_this_turn,
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
        # A split leg is walked in full: the roller already chose its length,
        # so stopping short at an opponent would be a second bite at that
        # choice. Landing exactly on someone still offers the fight.
        allow_combat_stops=False,
        disguise_available=_disguise_card(player) is not None,
    )
    if not opts.destinations:
        evs.append(_ev("no_legal_move", player=player.username, steps=n_self))
        state.phase = Phase.TURN_END
        state.turn.pending_split = None
        return evs
    if opts.forced_single and (only := opts.only_destination()):
        evs.extend(_commit_move(state, board, player, only, opts.destinations[only]))
        return evs
    state.turn.pending_move = PendingMove(
        steps=n_self,
        destinations=opts.destinations,
        remaining_steps=0 if target is None else (0),
        split_target=target,
        split_target_destination=target_destination,
        requires_disguise=sorted(opts.requires_disguise),
    )
    # If we're deferring a target leg, encode remaining_steps so the choose_move_path
    # handler knows how far to move the target.
    if target is not None:
        # remaining_steps carries n_other for the deferred target leg.
        state.turn.pending_move.remaining_steps = (state.turn.pending_split.total - n_self) if state.turn.pending_split else 0
    state.turn.pending_split = None
    state.phase = Phase.CHOOSING_PATH
    evs.append(_ev("choose_path", player=player.username, destinations=list(opts.destinations.keys())))
    return evs


def _defer_split_leg(state: GameState, leg: DeferredSplitLeg) -> None:
    state.turn.deferred_split_leg = leg


def _resume_deferred_split_leg(state: GameState, board: Board) -> list[dict[str, Any]]:
    """Run a split-7 leg that was held back while someone answered a prompt.

    A no-op unless the table has just come free (``TURN_END``) with a leg still
    owed. Called from every intent that closes one of the prompts a landing can
    open, so neither half of a split can be lost to the other half's landing.
    """
    leg = state.turn.deferred_split_leg
    if leg is None or leg.steps <= 0 or state.phase != Phase.TURN_END:
        return []
    state.turn.deferred_split_leg = None
    if leg.kind == "roller":
        evs = _run_roller_split_leg(
            state, board, state.current_player(), leg.steps,
            target=None, target_destination=None,
        )
    else:
        evs = _resolve_split_target_leg(
            state, board, leg.target, leg.steps, leg.target_destination,
        )
    if not _sub_phase_active(state):
        state.phase = Phase.TURN_END
    return evs


def _resolve_split_target_leg(
    state: GameState,
    board: Board,
    target_name: Optional[str],
    n_other: int,
    target_destination: Optional[str],
) -> list[dict[str, Any]]:
    """Second leg of a split-7: move ``target_name`` by ``n_other`` squares.

    Returns an event list. No-op when ``n_other == 0`` or no target is given
    (the "take all yourself" case). If the target has no legal destinations,
    the leg is silently skipped — their movement is lost.
    """
    evs: list[dict[str, Any]] = []
    if n_other <= 0 or target_name is None:
        return evs
    target = state.player(target_name)
    others = [p.position for p in state.players if p.username != target.username]
    # The no-revisit rule applies to the target's own movement this turn
    # too, but since the target was not the acting player, they have no
    # visited_this_turn trail — their current space is the only exclusion.
    opts = compute_destinations(
        board, target.position, n_other, target,
        other_player_positions=others,
        visited_this_turn=[target.position],
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
        # Same rule as the roller's leg — the assigned steps are spent in full.
        # Without this, a target given 4 could be parked on a player standing 1
        # square away, which is how a 4 was quietly being spent as a 1.
        allow_combat_stops=False,
    )
    if not opts.destinations:
        return evs
    # If a destination was already specified (and is valid), use it directly.
    tdest = target_destination if (target_destination and target_destination in opts.destinations) else None
    if tdest is None and len(opts.destinations) > 1:
        # Multiple options and no pre-selected destination: ask the roller to
        # choose where the target moves by entering CHOOSING_PATH again.
        # Callers inspect state.phase after this call and must not do
        # further processing when we return in CHOOSING_PATH.
        state.turn.pending_move = PendingMove(
            steps=n_other,
            destinations=opts.destinations,
            is_for_target=True,
            target_for_split=target_name,
        )
        state.phase = Phase.CHOOSING_PATH
        evs.append(_ev(
            "choose_path",
            player=state.current_player().username,
            destinations=list(opts.destinations.keys()),
            for_target=target_name,
        ))
        return evs
    # Single destination or explicit choice — resolve immediately.
    if tdest is None:
        tdest = next(iter(opts.destinations))
    tpath = opts.destinations[tdest]
    old = target.position
    target.position = tdest
    evs.append(_ev(
        "player_moved", player=target.username,
        src=old, dst=tdest, path=tpath, move_kind="split_seven",
    ))
    evs.extend(cancel_rest_if_moved_off(state, board, target, old))
    # Resolve target's landing effect fully.
    evs.extend(_resolve_landing(state, board, target, own_move=False))
    return evs


# =========================================================================
# Combat intents
# =========================================================================


def _intent_initiate_combat(state, payload, *, board, rng):
    """Declare combat against another player on a space the roller reaches/passes."""
    username = payload["username"]
    player = _require_current_player(state, username)
    target_name = payload["target"]
    if target_name == username:
        raise RuleError("Cannot attack self")
    target = state.player(target_name)
    if target.position != player.position:
        raise RuleError("Target must be on your space")
    # White Tower check takes priority — forbidden regardless of accreditation.
    if board.space(player.position).region == "white_tower":
        raise RuleError("No combat inside the White Tower")
    if not player.accredited:
        raise RuleError("You must be accredited to initiate combat")
    if not target.accredited:
        raise RuleError("You cannot attack an unaccredited player")
    combat_mod.begin(state, username, target_name, player.position)
    state.phase = Phase.COMBAT
    evs = [_ev("combat_started", attacker=username, defender=target_name, space=player.position)]
    _log(state, evs)
    return state, evs


def _intent_select_combat_cards(state, payload, *, board, rng):
    _require_phase(state, Phase.COMBAT)
    username = payload["username"]
    card_ids = list(payload.get("card_ids") or [])
    if state.combat is None:
        raise RuleError("No combat in progress")
    if username == state.combat.attacker:
        combat_mod.set_attacker_cards(state, card_ids)
    elif username == state.combat.defender:
        combat_mod.set_defender_cards(state, card_ids)
    else:
        raise RuleError("You are not in this combat")
    evs = [_ev("combat_cards_selected", player=username, count=len(card_ids))]
    _log(state, evs)
    return state, evs


def _intent_play_combat_special(state, payload, *, board, rng):
    _require_phase(state, Phase.COMBAT)
    username = payload["username"]
    if state.combat is None or username != state.combat.defender:
        raise RuleError("Only the defender may play combat specials")
    card_id = payload["card_id"]
    defender = state.player(username)
    # Look up before combat mutates things; we rely on the card being in their hand.
    card = next((c for c in defender.hand if c.id == card_id), None)
    if card is None:
        raise RuleError(f"Defender has no card {card_id}")
    # Sanctuary burns both players' committed cards and replaces them, so the
    # special needs the tower deck.
    tower_deck = _deck_view(state, "tower")
    attacker_committed = len(state.combat.attacker_cards)
    defender_committed = len(state.combat.defender_cards)
    combat = combat_mod.play_defender_special(
        state, card_id, board.data.chapel_royal_space, rng, tower_deck,
    )
    _sync_deck(state, "tower", tower_deck)
    # Discard the special card.
    state.tower_discard.append(card)
    evs = [_ev("combat_special", player=username, card=card.name)]
    # Mass Accretor turns one of the attacker's own weapons on them mid-fight,
    # which changes both totals. Surface it rather than leaving it buried in
    # ``combat.resolved_events``: from the outside the attacker's score simply
    # dropped for no stated reason.
    # Scoped to this card: ``resolved_events`` accumulates for the whole fight,
    # so a Sanctuary played after a Mass Accretor would otherwise re-announce
    # the earlier theft.
    stole = next(
        (e.split(":", 1)[1] for e in reversed(combat.resolved_events)
         if e.startswith("mass_accretor_stole:")),
        None,
    ) if card.effect_key == "mass_accretor" else None
    if stole is not None:
        evs.append(_ev(
            "mass_accretor_stole", player=username,
            attacker=combat.attacker, card=stole,
        ))
    elif (card.effect_key == "mass_accretor"
          and "mass_accretor_no_target" in combat.resolved_events):
        evs.append(_ev(
            "mass_accretor_no_target", player=username, attacker=combat.attacker,
        ))
    if combat.sanctuary_cancelled:
        evs.append(_ev(
            "sanctuary_taken",
            defender=combat.defender, attacker=combat.attacker,
            attacker_cards_lost=attacker_committed,
            defender_cards_lost=defender_committed,
        ))
        state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


def _intent_reveal_combat(state, payload, *, board, rng):
    _require_phase(state, Phase.COMBAT)
    combat = combat_mod.reveal(state)
    # Snapshot everything the resolution consumes, so the event can narrate the
    # whole outcome — totals, spoils and all — rather than just naming a winner.
    atk_total = sum(c.value for c in combat.attacker_cards)
    def_total = sum(c.value for c in combat.defender_cards)
    loser_name = combat.defender if combat.winner == combat.attacker else combat.attacker
    loser_state = state.player(loser_name)
    winner_state = state.player(combat.winner)
    jewels_taken = list(loser_state.jewels)
    coin_taken = loser_state.has_coin
    winner_had_coin = winner_state.has_coin
    winner_plays = (
        combat.attacker_cards if combat.winner == combat.attacker
        else combat.defender_cards
    )
    cards_drawn = len(winner_plays)
    # The committed cards themselves, so every client can replay the reveal one
    # card at a time. resolve() discards them, so snapshot them first.
    def _card_rows(cards):
        return [{"id": c.id, "name": c.name, "value": c.value} for c in cards]
    attacker_rows = _card_rows(combat.attacker_cards)
    defender_rows = _card_rows(combat.defender_cards)
    hand_before = {c.id for c in winner_state.hand}

    # Auto-resolve right away — there are no more decisions to make.
    tower_deck = _deck_view(state, "tower")
    combat_mod.resolve(
        state,
        tower_deck,
        hospital_space=board.data.hospital_space,
        devereux_max_coins=state.coins_total or MAX_COINS,
        rng=rng,
    )
    _sync_deck(state, "tower", tower_deck)
    # Which cards the victor actually drew. Diffing the hand avoids threading a
    # return value back out of combat.resolve(). Only the winner can turn these
    # ids into names — every other client has an empty hand for them.
    winner_drew = [c.id for c in winner_state.hand if c.id not in hand_before]
    evs = [_ev(
        "combat_resolved",
        winner=combat.winner,
        loser=loser_name,
        attacker=combat.attacker,
        defender=combat.defender,
        attacker_cards=attacker_rows,
        defender_cards=defender_rows,
        attacker_total=atk_total,
        defender_total=def_total,
        tie=atk_total == def_total,
        jewels_taken=jewels_taken,
        coin_taken=coin_taken,
        # A second coin can't be held, so it goes back to Devereux.
        coin_overflowed=coin_taken and winner_had_coin,
        cards_drawn=cards_drawn,
        winner_drew=winner_drew,
        loser_sent_to=board.data.hospital_space,
    )]
    state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


def _deck_view(state: GameState, which: str) -> Deck:
    if which == "tower":
        return Deck(draw_pile=state.tower_draw, discard_pile=state.tower_discard)
    return Deck(draw_pile=state.raven_draw, discard_pile=state.raven_discard)


def _sync_deck(state: GameState, which: str, deck: Deck) -> None:
    if which == "tower":
        state.tower_draw = deck.draw_pile
        state.tower_discard = deck.discard_pile
    else:
        state.raven_draw = deck.draw_pile
        state.raven_discard = deck.discard_pile


# =========================================================================
# Jewel attempt
# =========================================================================


def _intent_attempt_jewel(state, payload, *, board, rng):
    # TURN_START / PRE_ROLL are allowed for the re-attempt: a failed thief stays
    # standing on the jewel, so on their next turn they may either try again or
    # simply roll and walk away. The choice is theirs, so we don't force the
    # JEWEL_ATTEMPT phase on them at turn start.
    _require_phase(state, Phase.JEWEL_ATTEMPT, Phase.TURN_START, Phase.PRE_ROLL)
    username = payload["username"]
    player = _require_current_player(state, username)
    pj = state.turn.pending_jewel
    if pj is None:
        standing_on = state.jewel_at_space(player.position)
        if standing_on is None:
            raise RuleError("No pending jewel attempt")
        pj = PendingJewelAttempt(
            jewel_id=standing_on, space_id=player.position, source="landing",
        )
        state.turn.pending_jewel = pj
    # Optional subset of burglary tool card ids to play.
    tool_ids: list[str] = list(payload.get("tool_card_ids") or [])
    tools: list[Card] = []
    for cid in tool_ids:
        card = next((c for c in player.hand if c.id == cid), None)
        if card is None or card.category != "burglary":
            raise RuleError(f"Not a burglary card: {cid}")
        tools.append(card)
    total_val = sum(c.value for c in tools)
    roll = rng.roll_dice(2)
    threshold = 12 - total_val
    success = sum(roll) >= threshold
    # Burglary tools are always re-usable — they stay in hand whether the
    # attempt succeeds or fails.
    evs: list[dict[str, Any]] = [_ev(
        "jewel_attempt",
        player=player.username,
        jewel=pj.jewel_id,
        roll=roll,
        threshold=threshold,
        success=success,
        tools=[c.id for c in tools],
    )]
    if success:
        state.jewels_available.pop(pj.jewel_id, None)
        player.jewels.append(pj.jewel_id)
        evs.append(_ev("jewel_acquired", player=player.username, jewel=pj.jewel_id))
    else:
        # Failure costs the turn, not the chance: the thief stays put and can
        # try again next turn (see _offer_standing_jewel_attempt).
        evs.append(_ev(
            "jewel_attempt_retry_available",
            player=player.username, jewel=pj.jewel_id, space=pj.space_id,
        ))
    state.turn.pending_jewel = None
    state.phase = Phase.TURN_END
    evs.extend(_resume_deferred_split_leg(state, board))
    _log(state, evs)
    return state, evs




# =========================================================================
# Change a card / swap a card (the ww41/58/69 and ww75 prompts)
# =========================================================================


def _intent_change_card(state, payload, *, board, rng):
    """Resolve the prompt parked by a ``change_card`` / swap square.

    Payload: ``card_id`` (the card being given up) and, for a swap, ``target``
    (the opponent to trade with).
    """
    _require_phase(state, Phase.CARD_CHANGE)
    username = payload["username"]
    player = _require_current_player(state, username)
    pending = state.turn.pending_card_change
    if pending is None:
        raise RuleError("No pending card change")

    card_id = payload.get("card_id")
    given = next((c for c in player.hand if c.id == card_id), None)
    if given is None:
        raise RuleError(f"Card not in hand: {card_id}")

    evs: list[dict[str, Any]] = []
    if pending.kind == "swap":
        target_name = payload.get("target")
        if target_name not in pending.candidates:
            raise RuleError(f"Not a valid swap target: {target_name}")
        target = state.player(target_name)
        if not target.hand:
            raise RuleError(f"{target_name} has no cards to swap")
        # They choose what they give; what comes back is pot luck.
        received = target.hand[rng.randint(0, len(target.hand) - 1)]
        player.remove_card(given.id)
        target.remove_card(received.id)
        player.add_card(received)
        target.add_card(given)
        evs.append(_ev(
            "card_swapped", player=player.username, target=target_name,
            given=given.id, received=received.id, space=pending.space_id,
        ))
    else:
        player.remove_card(given.id)
        state.tower_discard.append(given)
        drew = _draw_tower(state)
        if drew is not None:
            player.add_card(drew)
        evs.append(_ev(
            "card_changed", player=player.username,
            discarded=given.id, drawn=drew.id if drew else None,
            space=pending.space_id,
        ))

    state.turn.pending_card_change = None
    state.phase = Phase.TURN_END
    evs.extend(_resume_deferred_split_leg(state, board))
    _log(state, evs)
    return state, evs


# =========================================================================
# Accreditation (explicit skip / no-op intent — the real mechanic is in roll)
# =========================================================================


def _intent_attempt_accreditation(state, payload, *, board, rng):
    """Convenience alias for rolling during accreditation trial."""
    return _intent_roll_dice(state, payload, board=board, rng=rng)


# =========================================================================
# Raven effect resolution (player input step)
# =========================================================================


def _intent_reveal_raven_notice(state, payload, *, board, rng):
    """The drawer turns the card face-up; the effect fires on the way out.

    Reveal is shared state rather than a per-client animation, so the whole
    table flips together and nobody sees the consequences before the cause.
    """
    _require_phase(state, Phase.RAVEN_EFFECT)
    pr = state.turn.pending_raven
    notice = state.active_raven_notice
    if pr is None or notice is None:
        raise RuleError("No raven card to reveal")
    username = payload["username"]
    if username != pr.drawer:
        raise RuleError("Only the drawer can reveal the raven card")
    if notice.revealed:
        raise RuleError("That raven card is already face-up")

    notice.revealed = True
    evs: list[dict[str, Any]] = [_ev(
        "raven_notice_revealed",
        player=username, card=notice.card_id, effect=pr.effect_key,
    )]
    evs.extend(_resolve_pending_raven(state, board, rng, {}))
    _log(state, evs)
    return state, evs


def _intent_resolve_raven_effect(state, payload, *, board, rng):
    _require_phase(state, Phase.RAVEN_EFFECT)
    pr = state.turn.pending_raven
    if pr is None:
        raise RuleError("No pending raven effect")
    username = payload["username"]
    if username != pr.drawer:
        raise RuleError("Only the drawer can resolve their raven effect")
    notice = state.active_raven_notice
    if notice is not None and not notice.revealed:
        raise RuleError("Reveal the raven card before resolving it")
    evs = _resolve_pending_raven(state, board, rng, payload.get("params") or {})
    _log(state, evs)
    return state, evs


# =========================================================================
# End turn
# =========================================================================


def _intent_end_turn(state, payload, *, board, rng):
    _require_phase(state, Phase.TURN_END, Phase.PRE_ROLL, Phase.ACCREDITATION_ATTEMPT, Phase.TURN_START)
    # Hospital miss: if the player never rolled (called end_turn from TURN_START),
    # still honour the hospital/miss flag so they can't bypass it.
    username = payload.get("username")
    if username:
        try:
            ender = state.player(username)
            if ender.miss_next_turn and state.phase == Phase.TURN_START:
                ender.miss_next_turn = False
                if ender.status == Status.HOSPITAL:
                    ender.status = Status.NORMAL
                # Don't consume extra_turns_queued — the missed turn was forced.
                state.turn.extra_turns_queued = 0
                state.phase = Phase.TURN_END
                ev = _ev("missed_turn", player=ender.username)
                _log(state, [ev])
                # Fall through to normal end_turn advance by continuing.
            elif state.phase == Phase.TURN_START and ender.status in (
                Status.RACKED, Status.IMPRISONED, Status.TORTURED,
            ):
                # Confinement counts down per turn taken, and _intent_roll_dice
                # is what normally ticks it. A confined player who ends their
                # turn without rolling still burns the turn — otherwise they can
                # sit on the Rack forever by never pressing Roll.
                ender.status_turns_remaining = max(0, ender.status_turns_remaining - 1)
                if ender.status_turns_remaining == 0:
                    if ender.status == Status.RACKED:
                        release_evs = _release_from_rack(state, ender, board)
                    else:
                        ender.status = Status.NORMAL
                        release_evs = [_ev("confinement_expired", player=ender.username)]
                    _log(state, release_evs)
        except KeyError:
            pass
    # Extra turns queued by Tower Pass / Clerk's Tea / etc.
    if state.turn.extra_turns_queued > 0:
        state.turn.extra_turns_queued -= 1
        # Keep current player; reset per-turn context (but don't reset
        # consecutive_doubles — that's a per-roll counter).
        doubles = state.turn.consecutive_doubles
        state.turn = state.turn.model_copy(update={
            "roll": [],
            "cards_played_this_turn": [],
            "visited_this_turn": [],
            "pending_move": None,
            "pending_raven": None,
            "pending_jewel": None,
            "pending_split": None,
            "deferred_split_leg": None,
            "consecutive_doubles": doubles,
        })
        state.phase = Phase.TURN_START
        _log(state, [_ev("extra_turn_used", player=state.current_player().username)])
        return state, [_ev("extra_turn_used", player=state.current_player().username)]
    # Firecrackers resolution: the outgoing player had their "one turn" to
    # leave the White Tower. If they're still inside, off to the Rack.
    fc_events: list[dict[str, Any]] = []
    outgoing_name = state.turn_order[state.current_turn_index]
    outgoing = state.player(outgoing_name)
    if outgoing_name in state.firecrackers_affected:
        state.firecrackers_affected.remove(outgoing_name)
        if board.space(outgoing.position).region == "white_tower":
            src = outgoing.position
            outgoing.position = board.data.rack_space
            outgoing.status = Status.RACKED
            outgoing.status_turns_remaining = 3
            if outgoing.has_coin:
                outgoing.has_coin = False
                state.coins_available = min(state.coins_total, state.coins_available + 1)
                penalty = "coin"
                lost = 0
            else:
                lost = len(outgoing.hand)
                state.tower_discard.extend(outgoing.hand)
                outgoing.hand = []
                penalty = "hand"
            fc_events.append(_ev(
                "player_moved", player=outgoing_name,
                src=src, dst=outgoing.position, move_kind="firecrackers_rack",
            ))
            fc_events.append(_ev(
                "firecrackers_racked", player=outgoing_name,
                penalty=penalty, cards_discarded=lost,
            ))

    # Advance to the next player. Nobody ever leaves the table — using the exit
    # puts you back on the start square rather than out of the game — so this
    # is a plain rotation.
    n = len(state.turn_order)
    state.current_turn_index = (state.current_turn_index + 1) % n
    # No end-of-game test here. The game is decided by what has been *banked*,
    # and the only way to bank anything is to walk out of the Cradle Tower —
    # so ``_use_the_exit`` asks the question at the one moment the answer can
    # have changed. A jewel in somebody's pocket settles nothing: it can still
    # be taken off them in a fight.
    # The Rack allows no action at all: no roll, no cards, no decision — rolling
    # doesn't even shorten the sentence early. So don't stop on a racked player
    # and make them press "End turn" to confirm they can't do anything; tick the
    # sentence and pass play straight on. Reaching zero releases them but still
    # costs them this turn, matching the roll-while-racked branch above.
    skip_evs: list[dict[str, Any]] = []
    if any(p.status != Status.RACKED for p in state.players):
        for _ in range(len(state.turn_order)):
            racked = state.current_player()
            if racked.status != Status.RACKED:
                break
            racked.status_turns_remaining = max(0, racked.status_turns_remaining - 1)
            skip_evs.append(_ev(
                "rack_turn_skipped", player=racked.username,
                turns_remaining=racked.status_turns_remaining,
            ))
            if racked.status_turns_remaining == 0:
                skip_evs.extend(_release_from_rack(state, racked, board))
            state.current_turn_index = (
                state.current_turn_index + 1) % len(state.turn_order)

    # Reset per-turn context.
    state.turn = state.turn.__class__()
    state.phase = Phase.TURN_START
    cur = state.current_player()
    # Pay out any extra turns this player earned while somebody else was acting
    # (an extra-turn square they were pushed onto by a split-7, say). Consumed
    # at the *end* of the turn that is starting now, so they take this turn and
    # then go again.
    if cur.extra_turns_pending > 0:
        state.turn.extra_turns_queued += cur.extra_turns_pending
        cur.extra_turns_pending = 0
    evs = fc_events + skip_evs + [_ev("turn_start", player=cur.username)]
    _log(state, evs)
    return state, evs


JEWEL_VALUES = {
    "crown_st_edward": 5,
    "crown_prince_of_wales": 4,
    "sceptre": 3,
    "orb": 2,
    "sword": 1,
}


def _slow_ranking(state: GameState) -> list[dict[str, Any]]:
    """Return a sorted ranking of players for the end of a slow game.

    Scored on *banked* jewels only. A jewel still in somebody's pocket when the
    game ends never left the Tower, and the whole point of the hideout is that
    getting it out is the achievement.

    Sort order (each descending):
      1. Banked jewel count
      2. Top jewel value (tie-break for same count)
      3. Sum of jewel values (further tie-break)
    With a final ascending username tie-break for determinism.
    """
    scored: list[tuple[int, int, int, str, dict[str, Any]]] = []
    for p in state.players:
        count = len(p.banked_jewels)
        top = max((JEWEL_VALUES.get(j, 0) for j in p.banked_jewels), default=0)
        total = sum(JEWEL_VALUES.get(j, 0) for j in p.banked_jewels)
        scored.append((count, top, total, p.username, {
            "username": p.username,
            "jewel_count": count,
            "jewel_top_value": top,
            "jewel_total_value": total,
            "jewels": list(p.banked_jewels),
            "carrying": list(p.jewels),
        }))
    scored.sort(key=lambda r: (-r[0], -r[1], -r[2], r[3]))
    return [row[4] for row in scored]


def _slow_winner(state: GameState) -> Optional[str]:
    """Backwards-compatible wrapper around ``_slow_ranking``."""
    r = _slow_ranking(state)
    return r[0]["username"] if r else None


# =========================================================================
# Notification dismissal (any player can dismiss the public raven notice)
# =========================================================================


def _intent_end_game_draw(state, payload, *, board, rng):
    """Abandon the game and call it a draw.

    Any player may do this — it's how you start a fresh game without losing the
    result. The game goes to GAME_OVER with no winner so the results screen
    still shows everyone's haul and tallies; ``apply`` snapshots the stats.
    """
    if state.phase == Phase.GAME_OVER:
        return state, []
    state.phase = Phase.GAME_OVER
    state.winner = None
    evs = [_ev("game_over_draw", player=payload.get("username"))]
    _log(state, evs)
    return state, evs


def _intent_dismiss_raven_notice(state, payload, *, board, rng):
    """Clear the active raven notice; any player may invoke this.

    No-op (and no event) if the notice has already been cleared or the
    submitted ``card_id`` doesn't match — this avoids spurious clears when
    multiple players race to dismiss.
    """
    card_id = payload.get("card_id")
    if state.active_raven_notice is None:
        return state, []
    if card_id and state.active_raven_notice.card_id != card_id:
        return state, []
    if not state.active_raven_notice.revealed:
        # Clearing a face-down card would strand the turn in RAVEN_EFFECT with
        # nothing left to reveal, and nobody would ever see what it was.
        raise RuleError("The raven card hasn't been turned over yet")
    cleared = state.active_raven_notice.card_id
    state.active_raven_notice = None
    ev = _ev(
        "raven_notice_dismissed",
        card_id=cleared,
        by=payload.get("username"),
    )
    _log(state, [ev])
    return state, [ev]


def _intent_dismiss_confinement_notice(state, payload, *, board, rng):
    """Clear the red confinement banner. Only the player it happened to may.

    Everyone at the table sees the banner; letting anyone dismiss it would rob
    the victim of the moment. A no-op (and no event) when there's nothing to
    clear or somebody else asks, so a stale click from another client can't
    close it out from under them.
    """
    notice = state.active_confinement_notice
    if notice is None:
        return state, []
    if payload.get("username") != notice.username:
        return state, []
    state.active_confinement_notice = None
    ev = _ev(
        "confinement_notice_dismissed",
        player=notice.username, status=notice.status.value,
    )
    _log(state, [ev])
    return state, [ev]


# =========================================================================
# Global RNG bridge (used by auto-triggered landing raven-draw paths)
# =========================================================================


class _RngRef:
    def __init__(self):
        self._r: Optional[Rng] = None

    def set(self, r: Rng) -> None:
        self._r = r

    def get(self) -> Rng:
        if self._r is None:
            raise RuntimeError("Rng not set")
        return self._r


_GLOBAL_RNG = _RngRef()


# =========================================================================
# Dispatch table
# =========================================================================

_INTENTS: dict[str, Any] = {
    "start_game": _intent_start_game,
    "play_card_pre_roll": _intent_play_card_pre_roll,
    "roll_dice": _intent_roll_dice,
    "redraw_cards": _intent_redraw_cards,
    "choose_move_path": _intent_choose_move_path,
    "assign_split_seven": _intent_assign_split_seven,
    "initiate_combat": _intent_initiate_combat,
    "select_combat_cards": _intent_select_combat_cards,
    "play_combat_special": _intent_play_combat_special,
    "reveal_combat": _intent_reveal_combat,
    "attempt_jewel": _intent_attempt_jewel,
    "change_card": _intent_change_card,
    "attempt_accreditation": _intent_attempt_accreditation,
    "reveal_raven_notice": _intent_reveal_raven_notice,
    "dismiss_confinement_notice": _intent_dismiss_confinement_notice,
    "resolve_raven_effect": _intent_resolve_raven_effect,
    "dismiss_raven_notice": _intent_dismiss_raven_notice,
    "end_game_draw": _intent_end_game_draw,
    "end_turn": _intent_end_turn,
}
