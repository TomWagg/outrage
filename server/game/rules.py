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
    Combat,
    GameState,
    LogEntry,
    JewelId,
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

# Max coins the devereux Tower "holds" — equals the player cap for this
# implementation (5 per scoping notes).
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


def _warder_blocked_spaces(state: GameState, board: "Board") -> set[str]:
    """Return post space ids that are occupied by a warder this turn.

    Returns an empty set when the current player has a Disguise armed
    (``turn.disguise_used``), allowing them to pass freely.
    """
    if state.turn.disguise_used:
        return set()
    barracks = board.data.barracks_space
    return {w.location for w in state.warders if w.location != barracks}


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
    return handler(state, payload, board=board, rng=rng)


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
    evs = [_ev("game_started", order=state.turn_order, hand_size=per_player)]
    _log(state, evs)
    return state, evs


# =========================================================================
# Turn start / pre-roll
# =========================================================================


def _intent_play_card_pre_roll(state, payload, *, board, rng):
    """Play a utility/custom card from hand before rolling."""
    _require_phase(state, Phase.TURN_START, Phase.PRE_ROLL, Phase.ACCREDITATION_ATTEMPT)
    username = payload["username"]
    player = _require_current_player(state, username)
    card_id = payload["card_id"]
    card = next((c for c in player.hand if c.id == card_id), None)
    if card is None:
        raise RuleError(f"No such card in hand: {card_id}")
    # Only certain categories are playable pre-roll.
    if card.kind != "tower":
        raise RuleError("Only tower cards are played pre-roll")
    if card.category in ("weapon", "burglary"):
        raise RuleError(f"Cannot play {card.name} pre-roll")
    if card.effect_key is None:
        raise RuleError(f"Card {card.name} has no effect")
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
    state.turn.cards_played_this_turn.append(card_id)
    if state.phase == Phase.TURN_START:
        state.phase = Phase.PRE_ROLL
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
                player.status = Status.NORMAL
                evs.append(_ev("rack_expired", player=player.username))
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

    # Accreditation trial.
    if player.trying_accreditation and not player.accredited:
        if total % 2 == 1:
            player.accredited = True
            player.trying_accreditation = False
            evs.append(_ev("accredited", player=player.username, via="odd_roll"))
            # Use roll to move in the inner ward (free graph movement).
        else:
            state.phase = Phase.TURN_END
            evs.append(_ev("accreditation_failed", player=player.username))
            _log(state, evs)
            return state, evs

    # Doubles tracking.
    if is_double:
        state.turn.consecutive_doubles += 1
        if state.turn.consecutive_doubles >= 3:
            # Cancel movement, go to Bloody Tower.
            player.position = board.data.bloody_tower_space
            player.status = Status.IMPRISONED
            player.status_turns_remaining = 3
            state.turn.consecutive_doubles = 0
            state.phase = Phase.TURN_END
            evs.append(_ev("three_doubles_bloody_tower", player=player.username))
            _log(state, evs)
            return state, evs
        # Regular double: grant an extra roll at the end of this turn.
        state.turn.extra_turns_queued += 1

    # Binary disruption or split-7?
    if state.turn.binary_disruption_armed or total == 7:
        state.phase = Phase.SPLIT_SEVEN_ASSIGN
        state.turn.pending_split = PendingSplitSeven(
            total=total,
            source="binary_disruption" if state.turn.binary_disruption_armed else "seven",
        )
        state.turn.binary_disruption_armed = False
        evs.append(_ev("split_assign_required", total=total))
        _log(state, evs)
        return state, evs

    # Normal movement path.
    evs.extend(_enter_movement_phase(state, board, player, total))
    _log(state, evs)
    return state, evs


def _enter_movement_phase(state: GameState, board: Board, player: PlayerState, steps: int) -> list[dict[str, Any]]:
    others = [p.position for p in state.players if p.username != player.username and not p.escaped]
    # Seed the visited-this-turn list with the player's current position on
    # first use so the pathfinder excludes it consistently.
    if not state.turn.visited_this_turn:
        state.turn.visited_this_turn = [player.position]
    opts = compute_destinations(
        board, player.position, steps, player,
        other_player_positions=others,
        visited_this_turn=state.turn.visited_this_turn,
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
    )
    if not opts.destinations:
        # No legal move — just end turn.
        state.phase = Phase.TURN_END
        return [_ev("no_legal_move", player=player.username, steps=steps)]
    state.turn.pending_move = PendingMove(steps=steps, destinations=opts.destinations)
    if opts.forced_single and (only := opts.only_destination()):
        # Auto-commit.
        return _commit_move(state, board, player, only, opts.destinations[only])
    state.phase = Phase.CHOOSING_PATH
    return [_ev("choose_path", player=player.username, destinations=list(opts.destinations.keys()))]


def _commit_move(state: GameState, board: Board, player: PlayerState, dest: str, path: list[str]) -> list[dict[str, Any]]:
    old = player.position
    # Escape rule: if the path crosses (or lands on) an escape space and the
    # player has their jewels + coin, they escape — moving >= the number of
    # steps needed to reach the escape square is enough (overshoot is OK).
    if player.jewels and player.has_coin:
        for sid in path[1:]:
            if board.space(sid).kind == "escape":
                idx = path.index(sid)
                path = path[: idx + 1]
                dest = sid
                break
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


def _resolve_landing(state: GameState, board: Board, player: PlayerState) -> list[dict[str, Any]]:
    """Trigger landing effects for the current space, updating the phase."""
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
            evs.append(_ev("coin_picked_up", player=player.username))

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
    if space.kind == "raven_trigger":
        evs.extend(_draw_raven_and_resolve(state, board, player))
        if state.phase == Phase.RAVEN_EFFECT:
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
        action_evs, handled_terminal = _dispatch_space_action(state, board, player, space)
        evs.extend(action_evs)
        if handled_terminal:
            return evs

    # Escape in fast mode.
    if space.kind == "escape" and state.mode == "fast":
        if player.jewels and player.has_coin:
            player.escaped = True
            state.phase = Phase.GAME_OVER
            state.winner = player.username
            evs.append(_ev("fast_win", player=player.username))
            return evs

    # Escape in slow mode.
    if space.kind == "escape" and state.mode == "slow":
        if player.jewels and player.has_coin:
            player.escaped = True
            state.finished_slow_order.append(player.username)
            evs.append(_ev("slow_escaped", player=player.username))

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
            if p.username != player.username and not p.escaped and p.position == player.position
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


def _dispatch_space_action(
    state: GameState, board: Board, player: PlayerState, space
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
        state.turn.extra_turns_queued += 1
        if params.get("resets_consecutive_doubles"):
            state.turn.consecutive_doubles = 0
        evs.append(_ev("extra_turn_granted", player=player.username, space=space.id))
        return evs, False

    if key == "go_back_by_roll":
        # Move the player backward along the wall walk by the dice total that
        # put them on this square (per ``uses_landing_roll``).
        total = sum(state.turn.roll) if state.turn.roll else 0
        if total <= 0 or space.wall_walk_order is None:
            return evs, False
        target_order = space.wall_walk_order - total
        if target_order < 0:
            return evs, False
        target = next(
            (s for s in board.data.spaces
             if s.region == "wall_walk" and s.wall_walk_order == target_order),
            None,
        )
        if target is None:
            return evs, False
        old = player.position
        player.position = target.id
        # Visited tracking: the teleport destination counts as visited too.
        if target.id not in state.turn.visited_this_turn:
            state.turn.visited_this_turn.append(target.id)
        evs.append(_ev(
            "go_back_by_roll", player=player.username,
            src=old, dst=target.id, steps=total,
        ))
        # Resolve landing on the new square (but guard against recursion
        # through another go_back_by_roll — wall walk is linear so a backward
        # jump cannot land on another go_back_by_roll square).
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
    # If the effect needs input, park pending state.
    ek = card.effect_key
    params = dict(card.params)
    # Effects that need input: go_to_location(player_choice), call_warder_to_post(chooser),
    # return_warder_to_barracks (multiple), rest_on_bench (multiple), photo_with_warder,
    # stopped_and_searched.
    if _raven_needs_input(ek, params, state, board, player):
        state.turn.pending_raven = PendingRavenEffect(
            effect_key=ek,
            card_id=card.id,
            params=params,
            drawer=player.username,
        )
        state.phase = Phase.RAVEN_EFFECT
        evs.append(_ev("raven_needs_input", effect=ek))
        return evs
    # Auto-resolve.
    from .cards_effects import dispatch as _dispatch
    try:
        _, sub_evs = _dispatch(ek, state, player, params, board=board, rng=_GLOBAL_RNG.get())
    except EffectError as exc:
        evs.append(_ev("raven_effect_failed", effect=ek, error=str(exc)))
        return evs
    evs.extend(sub_evs)
    return evs


def _raven_needs_input(ek: str, params: dict, state: GameState, board: Board, player: PlayerState) -> bool:
    if ek == "go_to_location" and params.get("location") == "player_choice":
        return True
    if ek == "call_warder_to_post" and params.get("post") == "chooser":
        # And there is actually a warder in the barracks.
        if any(w.location == board.data.barracks_space for w in state.warders):
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
        # Clear CHOOSING_PATH now that the selection is committed; _resolve_landing
        # may override to RAVEN_EFFECT / JEWEL_ATTEMPT etc., and the guard below
        # will set TURN_END if nothing else grabs the phase.
        state.phase = Phase.MOVING
        evs: list[dict[str, Any]] = [_ev(
            "player_moved", player=target.username,
            src=old_pos, dst=dest, path=path, move_kind="split_seven",
        )]
        evs.extend(_resolve_landing(state, board, target))
        # target_first: run the roller's deferred leg now (if still no active sub-phase).
        if roller_steps > 0 and state.phase not in (
            Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT,
            Phase.GAME_OVER, Phase.CHOOSING_PATH, Phase.COMBAT,
        ):
            evs.extend(_run_roller_split_leg(state, board, player, roller_steps, target=None, target_destination=None))
        if state.phase not in (
            Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT,
            Phase.GAME_OVER, Phase.CHOOSING_PATH, Phase.COMBAT,
        ):
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
    # Snapshot the split-7 continuation *before* _commit_move clears pending_move.
    split_target = pm.split_target
    split_n_other = pm.remaining_steps
    split_tdest_override = payload.get("target_destination") or pm.split_target_destination
    evs = _commit_move(state, board, player, dest, path)
    if split_target is not None and split_n_other > 0:
        evs.extend(_resolve_split_target_leg(state, board, split_target, split_n_other, split_tdest_override))
        # Landing effects of either leg may have forced a new phase; only
        # fall through to TURN_END when nothing else needs the player's
        # attention.
        if state.phase not in (
            Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT,
            Phase.GAME_OVER, Phase.CHOOSING_PATH, Phase.COMBAT,
        ):
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
    evs: list[dict[str, Any]] = [_ev(
        "split_assigned", self=n_self, other=n_other,
        target=target_name, leg_order=leg_order,
    )]

    target_first = leg_order == "target_first" and n_other > 0 and target_name
    if target_first:
        # Resolve the target's leg before the roller moves.
        state.turn.pending_split = None
        evs.extend(_resolve_split_target_leg(state, board, target_name, n_other, target_destination))
        if state.phase == Phase.CHOOSING_PATH:
            # Target has multiple destinations; roller must pick one via
            # choose_move_path.  Stash the roller's pending steps so we can
            # resume them after the target is placed.
            if n_self > 0 and state.turn.pending_move is not None:
                state.turn.pending_move.roller_steps_after_target = n_self
            _log(state, evs)
            return state, evs
        # Now the roller's leg. No deferred-target leg afterwards.
        if n_self > 0:
            evs.extend(_run_roller_split_leg(state, board, player, n_self, target=None, target_destination=None))
        if state.phase not in (Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT, Phase.GAME_OVER, Phase.CHOOSING_PATH, Phase.COMBAT):
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
            evs.extend(_resolve_split_target_leg(state, board, target_name, n_other, target_destination))
    if state.phase not in (Phase.JEWEL_ATTEMPT, Phase.RAVEN_EFFECT, Phase.GAME_OVER, Phase.CHOOSING_PATH, Phase.COMBAT):
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
    others = [p.position for p in state.players if p.username != player.username and not p.escaped]
    if not state.turn.visited_this_turn:
        state.turn.visited_this_turn = [player.position]
    opts = compute_destinations(
        board, player.position, n_self, player,
        other_player_positions=others,
        visited_this_turn=state.turn.visited_this_turn,
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
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
    others = [p.position for p in state.players if p.username != target.username and not p.escaped]
    # The no-revisit rule applies to the target's own movement this turn
    # too, but since the target was not the acting player, they have no
    # visited_this_turn trail — their current space is the only exclusion.
    opts = compute_destinations(
        board, target.position, n_other, target,
        other_player_positions=others,
        visited_this_turn=[target.position],
        warder_blocking_spaces=_warder_blocked_spaces(state, board),
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
    # Resolve target's landing effect fully.
    evs.extend(_resolve_landing(state, board, target))
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
    combat = combat_mod.play_defender_special(state, card_id, board.data.chapel_royal_space, rng)
    # Discard the special card.
    state.tower_discard.append(card)
    evs = [_ev("combat_special", player=username, card=card.name)]
    if combat.sanctuary_cancelled:
        state.phase = Phase.TURN_END
    _log(state, evs)
    return state, evs


def _intent_reveal_combat(state, payload, *, board, rng):
    _require_phase(state, Phase.COMBAT)
    combat = combat_mod.reveal(state)
    # Auto-resolve right away — there are no more decisions to make.
    tower_deck = _deck_view(state, "tower")
    combat_mod.resolve(
        state,
        tower_deck,
        hospital_space=board.data.hospital_space,
        devereux_max_coins=MAX_COINS,
        rng=rng,
    )
    _sync_deck(state, "tower", tower_deck)
    loser = combat.defender if combat.winner == combat.attacker else combat.attacker
    evs = [_ev("combat_resolved", winner=combat.winner, loser=loser)]
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
    _require_phase(state, Phase.JEWEL_ATTEMPT)
    username = payload["username"]
    player = _require_current_player(state, username)
    pj = state.turn.pending_jewel
    if pj is None:
        raise RuleError("No pending jewel attempt")
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
    # On success the burglary cards are spent (→ tower_discard). On failure
    # they're kept in the player's hand for another attempt later.
    if success:
        for c in tools:
            player.remove_card(c.id)
            state.tower_discard.append(c)
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
    state.turn.pending_jewel = None
    state.phase = Phase.TURN_END
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


def _intent_resolve_raven_effect(state, payload, *, board, rng):
    _require_phase(state, Phase.RAVEN_EFFECT)
    pr = state.turn.pending_raven
    if pr is None:
        raise RuleError("No pending raven effect")
    username = payload["username"]
    if username != pr.drawer:
        raise RuleError("Only the drawer can resolve their raven effect")
    player = state.player(username)
    # Merge player-supplied params over the card's base params.
    merged = dict(pr.params) | dict(payload.get("params") or {})
    from .cards_effects import dispatch as _dispatch
    _, evs = _dispatch(pr.effect_key, state, player, merged, board=board, rng=rng)
    state.turn.pending_raven = None
    if state.phase == Phase.RAVEN_EFFECT:
        # go_to_jewel_view sets pending_jewel; honour it before ending.
        if state.turn.pending_jewel is not None:
            state.phase = Phase.JEWEL_ATTEMPT
        else:
            state.phase = Phase.TURN_END
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
                state.coins_available = min(5, state.coins_available + 1)
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

    # Advance to next connected, non-escaped player.
    n = len(state.turn_order)
    for offset in range(1, n + 1):
        idx = (state.current_turn_index + offset) % n
        username = state.turn_order[idx]
        p = state.player(username)
        if not p.escaped:
            state.current_turn_index = idx
            break
    else:
        state.phase = Phase.GAME_OVER
        _log(state, fc_events)
        return state, fc_events + [_ev("game_over")]
    # Slow mode: game is over if only one non-escaped player remains, or if
    # every jewel has been claimed (nothing left in the White Tower or loose
    # on the board).
    if state.mode == "slow":
        remaining = [p for p in state.players if not p.escaped]
        jewels_out = (
            len(state.jewels_available) == 0
            and sum(len(v) for v in state.loose_jewels.values()) == 0
        )
        if len(remaining) <= 1 or jewels_out:
            state.phase = Phase.GAME_OVER
            ranking = _slow_ranking(state)
            state.winner = ranking[0]["username"] if ranking else None
            reason = "last_player" if len(remaining) <= 1 else "jewels_exhausted"
            end_ev = _ev(
                "slow_game_over", winner=state.winner,
                ranking=ranking, reason=reason,
            )
            _log(state, fc_events + [end_ev])
            return state, fc_events + [end_ev]
    # Reset per-turn context.
    state.turn = state.turn.__class__()
    state.phase = Phase.TURN_START
    cur = state.current_player()
    evs = fc_events + [_ev("turn_start", player=cur.username)]
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
    """Return a sorted ranking of players for slow-mode end-of-game.

    Sort order (each descending):
      1. Jewel count
      2. Top jewel value (tie-break for same count)
      3. Sum of jewel values (further tie-break)
    With a final ascending username tie-break for determinism.
    """
    scored: list[tuple[int, int, int, str, dict[str, Any]]] = []
    for p in state.players:
        count = len(p.jewels)
        top = max((JEWEL_VALUES.get(j, 0) for j in p.jewels), default=0)
        total = sum(JEWEL_VALUES.get(j, 0) for j in p.jewels)
        scored.append((count, top, total, p.username, {
            "username": p.username,
            "jewel_count": count,
            "jewel_top_value": top,
            "jewel_total_value": total,
            "jewels": list(p.jewels),
            "escaped": p.escaped,
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
    cleared = state.active_raven_notice.card_id
    state.active_raven_notice = None
    ev = _ev(
        "raven_notice_dismissed",
        card_id=cleared,
        by=payload.get("username"),
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
    "choose_move_path": _intent_choose_move_path,
    "assign_split_seven": _intent_assign_split_seven,
    "initiate_combat": _intent_initiate_combat,
    "select_combat_cards": _intent_select_combat_cards,
    "play_combat_special": _intent_play_combat_special,
    "reveal_combat": _intent_reveal_combat,
    "attempt_jewel": _intent_attempt_jewel,
    "attempt_accreditation": _intent_attempt_accreditation,
    "resolve_raven_effect": _intent_resolve_raven_effect,
    "dismiss_raven_notice": _intent_dismiss_raven_notice,
    "end_turn": _intent_end_turn,
}
