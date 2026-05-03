"""Effect dispatch registry for tower and raven cards.

Each handler has the signature::

    handler(state, player, params, *, board, rng, **kwargs) -> (state, events)

``events`` is a list of ``LogEntry``-compatible dicts. Handlers mutate state
in place and return it (Pydantic models are mutable); this is fine because
the rule engine snapshots via ``model_copy(deep=True)`` when it wants
immutability.

Several raven effects can't be fully resolved without player input (e.g.
``go_to_location`` with ``player_choice``, ``call_warder_to_post`` with
``chooser``, bench selection, warder selection, etc.). Those handlers set
``state.turn.pending_raven`` and leave the engine in
:attr:`Phase.RAVEN_EFFECT`; the rule engine later accepts a
``resolve_raven_effect`` intent and calls back into the handler.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .board import Board
from .cards import Card
from .rng import Rng
from .state import (
    GameState,
    JewelId,
    LogEntry,
    PendingJewelAttempt,
    PendingRavenEffect,
    PendingSplitSeven,
    PlayerState,
    Phase,
    Status,
)

log = logging.getLogger(__name__)


EventList = list[dict[str, Any]]


class EffectError(Exception):
    """Raised when an effect cannot be applied as requested."""


EffectHandler = Callable[..., tuple[GameState, EventList]]


REGISTRY: dict[str, EffectHandler] = {}


def register(key: str):
    def _wrap(fn: EffectHandler) -> EffectHandler:
        REGISTRY[key] = fn
        return fn

    return _wrap


def dispatch(
    key: str,
    state: GameState,
    player: PlayerState,
    params: dict[str, Any],
    *,
    board: Board,
    rng: Rng,
    **kwargs: Any,
) -> tuple[GameState, EventList]:
    handler = REGISTRY.get(key)
    if handler is None:
        raise EffectError(f"No handler registered for effect {key!r}")
    return handler(state, player, params, board=board, rng=rng, **kwargs)


# ---------- small helpers --------------------------------------------------


def _event(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "payload": payload}


def _send_to(state: GameState, player: PlayerState, space_id: str, board: Board) -> EventList:
    """Teleport helper. Returns an event list describing the move."""
    if not board.has_space(space_id):
        raise EffectError(f"Unknown destination space: {space_id}")
    old = player.position
    player.position = space_id
    return [_event("player_moved", player=player.username, src=old, dst=space_id, move_kind="teleport")]


def _clear_confinement(player: PlayerState) -> None:
    player.status = Status.NORMAL
    player.status_turns_remaining = 0


# ---------- tower effects --------------------------------------------------


@register("tower_pass")
def _tower_pass(state, player, params, *, board, rng, **kw):
    """Either auto-accredit at Queen's House or queue an extra turn.

    The card's user picks the mode by passing ``params={"mode": "accredit"}``
    or ``params={"mode": "extra_turn"}``. If they pick accredit without being
    at Queen's House the engine raises.
    """
    mode = params.get("mode", "extra_turn")
    if mode == "accredit":
        if player.position != board.data.queens_house_space:
            raise EffectError("Tower Pass accredit requires being at Queen's House")
        player.accredited = True
        player.trying_accreditation = False
        return state, [_event("accredited", player=player.username, via="tower_pass")]
    if mode == "extra_turn":
        state.turn.extra_turns_queued += 1
        return state, [_event("extra_turn_queued", player=player.username)]
    raise EffectError(f"Unknown Tower Pass mode: {mode}")


@register("sanctuary")
def _sanctuary(state, player, params, *, board, rng, **kw):
    """Out-of-combat Sanctuary — teleport to Chapel Royal.

    Requires accreditation: only accredited players can navigate to the Inner
    Ward (where Chapel Royal sits). Combat-context Sanctuary is handled
    separately by the combat sub-system before this handler is called.
    """
    if not player.accredited:
        raise EffectError("Sanctuary can only be used by an accredited player")
    evs = _send_to(state, player, board.data.chapel_royal_space, board)
    return state, evs


@register("disguise")
def _disguise(state, player, params, *, board, rng, **kw):
    """No-op at this layer; consumed by movement / confinement helpers.

    The rule engine consumes Disguise implicitly when the player needs to
    pass a warder or escape prison; when played explicitly via this handler
    we simply emit an event so the UI can show the card was played.
    """
    return state, [_event("disguise_played", player=player.username)]


@register("royal_pardon")
def _royal_pardon(state, player, params, *, board, rng, **kw):
    if player.status in (Status.IMPRISONED, Status.TORTURED):
        _clear_confinement(player)
        return state, [_event("pardoned", player=player.username, kind="royal")]
    raise EffectError("Royal Pardon only works for prison or torture")


@register("rack_pardon")
def _rack_pardon(state, player, params, *, board, rng, **kw):
    if player.status == Status.RACKED:
        _clear_confinement(player)
        return state, [_event("pardoned", player=player.username, kind="rack")]
    raise EffectError("Rack Pardon only works for the Rack")


@register("confession")
def _confession(state, player, params, *, board, rng, **kw):
    """Frame another player: swap positions, move torture status to them."""
    if player.status != Status.TORTURED:
        raise EffectError("Confession only playable while Tortured")
    target_name = params.get("target")
    if not target_name:
        raise EffectError("Confession requires a target player")
    target = state.player(target_name)
    # The framed player inherits the framer's remaining torture counter,
    # not a fresh 3 turns.
    remaining = max(0, int(player.status_turns_remaining))
    # Swap positions.
    player.position, target.position = target.position, player.position
    # Player walks free.
    _clear_confinement(player)
    # Target takes on the torture status with the inherited counter.
    target.status = Status.TORTURED
    target.status_turns_remaining = remaining
    return state, [_event(
        "framed", framer=player.username, framed=target.username,
        remaining=remaining,
    )]


@register("traversal_beauchamp_escape")
def _traversal_escape(state, player, params, *, board, rng, **kw):
    """Rope / Ladder: escape Beauchamp Tower imprisonment."""
    if player.status != Status.IMPRISONED or player.position != board.data.beauchamp_tower_space:
        raise EffectError("Rope/Ladder only works when imprisoned in Beauchamp Tower")
    _clear_confinement(player)
    return state, [_event("escaped_beauchamp", player=player.username)]


@register("firecrackers")
def _firecrackers(state, player, params, *, board, rng, **kw):
    """Mark every White Tower player as subject to the Firecrackers effect.

    Each marked player has until the end of their next turn to move outside
    the White Tower; if they haven't, they go to the Rack (see the end-turn
    handler in ``rules.py``). Moving outside clears the flag via
    ``_resolve_landing``.
    """
    if board.space(player.position).region != "white_tower":
        raise EffectError("Firecrackers can only be played from the White Tower")
    affected = [
        p.username for p in state.players
        if not p.escaped and board.space(p.position).region == "white_tower"
    ]
    # Replace any stale list; a fresh Firecrackers supersedes a prior one.
    state.firecrackers_affected = list(dict.fromkeys(affected))
    return state, [_event(
        "firecrackers", player=player.username,
        affected=list(state.firecrackers_affected),
    )]


@register("lasso")
def _lasso(state, player, params, *, board, rng, **kw):
    """Pull a player from ≤5 spaces away onto the roper's space.

    Per the house ruling, the pulled player does NOT trigger the landing
    effect of the destination square — they simply materialise there. Only
    the roper's own landing effect (resolved separately when they first
    arrived) counts. We therefore return the teleport event and do not
    invoke ``_resolve_landing``.
    """
    target_name = params.get("target")
    if not target_name:
        raise EffectError("Lasso requires a target")
    target = state.player(target_name)
    if target.username == player.username:
        raise EffectError("Cannot Lasso yourself")
    dist_map = board.reachable_within(player.position, 5)
    if target.position not in dist_map:
        raise EffectError(f"Target {target_name!r} not within 5 spaces of you")
    old = target.position
    target.position = player.position
    return state, [
        _event("lassoed", roper=player.username, target=target.username, src=old, dst=player.position),
    ]


@register("binary_disruption")
def _binary_disruption(state, player, params, *, board, rng, **kw):
    """Arms the next roll to be split like a 7.

    The player plays this pre-roll; the rule engine's ``roll_dice`` handler
    reads ``state.turn.binary_disruption_armed`` and jumps to the split
    assignment phase regardless of the actual roll total.
    """
    state.turn.binary_disruption_armed = True
    return state, [_event("binary_disruption_armed", player=player.username)]


@register("mass_accretor")
def _mass_accretor(state, player, params, *, board, rng, **kw):
    """No-op outside combat; the combat sub-state machine handles it."""
    return state, [_event("mass_accretor_queued", player=player.username)]


# ---------- raven effects --------------------------------------------------


def _location_from_params(params: dict[str, Any], board: Board) -> Optional[str]:
    loc = params.get("location")
    if not loc:
        return None
    if loc == "player_choice":
        return None
    if board.has_space(loc):
        return loc
    sp = board.space_by_name(loc.replace("_", " "))
    return sp.id if sp else None


@register("go_to_location")
def _go_to_location(state, player, params, *, board, rng, **kw):
    """Move to a fixed location, or ask the player if ``player_choice``."""
    loc = _location_from_params(params, board)
    if params.get("location") == "player_choice" and not params.get("chosen"):
        # Wait for input.
        return state, [_event("raven_needs_input", kind="choose_location")]
    if loc is None:
        # Resolve from chosen parameter.
        loc = params.get("chosen")
        if loc is None or not board.has_space(loc):
            raise EffectError(f"Unknown chosen location: {loc}")
    return state, _send_to(state, player, loc, board)


@register("go_to_jewel_view")
def _go_to_jewel_view(state, player, params, *, board, rng, **kw):
    """Move to the jewel's current space and queue an optional attempt."""
    jewel_id: JewelId = params["jewel"]
    # If the jewel is already taken, nothing happens (beyond noting it).
    if jewel_id not in state.jewels_available:
        return state, [_event("jewel_already_taken", jewel=jewel_id)]
    space_id = state.jewels_available[jewel_id]
    evs = _send_to(state, player, space_id, board)
    state.turn.pending_jewel = PendingJewelAttempt(jewel_id=jewel_id, space_id=space_id, source="raven_view")
    evs.append(_event("jewel_attempt_offered", jewel=jewel_id))
    return state, evs


@register("call_warder_to_post")
def _call_warder(state, player, params, *, board, rng, **kw):
    post = params.get("post")
    if post == "chooser" and not params.get("chosen_post"):
        return state, [_event("raven_needs_input", kind="choose_post")]
    if post == "chooser":
        post = params["chosen_post"]
    # Find warder in barracks; require input if multiple choices and no ``warder_id`` supplied.
    in_barracks = [w for w in state.warders if w.location == board.data.barracks_space]
    if not in_barracks:
        return state, [_event("no_warders_in_barracks")]
    warder = in_barracks[0]
    # Find the post space.
    post_space: Optional[str] = None
    for wp in board.data.warder_posts:
        if wp.id == post:
            post_space = wp.space_id
            break
    if post_space is None:
        raise EffectError(f"Unknown warder post: {post}")
    warder.location = post_space
    return state, [_event("warder_moved", warder=warder.id, dst=post_space)]


@register("return_warder_to_barracks")
def _return_warder(state, player, params, *, board, rng, **kw):
    chosen = params.get("warder_id")
    out_of_barracks = [w for w in state.warders if w.location != board.data.barracks_space]
    if not out_of_barracks:
        return state, [_event("no_warders_out_of_barracks")]
    if not chosen:
        if len(out_of_barracks) == 1:
            chosen = out_of_barracks[0].id
        else:
            return state, [_event("raven_needs_input", kind="choose_warder")]
    for w in state.warders:
        if w.id == chosen:
            w.location = board.data.barracks_space
            return state, [_event("warder_moved", warder=w.id, dst=board.data.barracks_space)]
    raise EffectError(f"Unknown warder id: {chosen}")


@register("pecked_by_ravens")
def _pecked(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.hospital_space, board)
    player.miss_next_turn = True
    player.status = Status.HOSPITAL
    evs.append(_event("pecked_by_ravens", player=player.username))
    return state, evs


@register("rest_on_bench")
def _bench(state, player, params, *, board, rng, **kw):
    chosen = params.get("bench")
    if chosen is None:
        if len(board.data.bench_space_ids) == 1:
            chosen = board.data.bench_space_ids[0]
        else:
            return state, [_event("raven_needs_input", kind="choose_bench")]
    if chosen not in board.data.bench_space_ids:
        raise EffectError(f"Not a valid bench: {chosen}")
    evs = _send_to(state, player, chosen, board)
    player.miss_next_turn = True
    evs.append(_event("resting_on_bench", player=player.username))
    return state, evs


@register("photo_with_warder")
def _photo(state, player, params, *, board, rng, **kw):
    occupied_posts = [w.location for w in state.warders if w.location != board.data.barracks_space]
    if not occupied_posts:
        return state, [_event("no_occupied_posts")]
    # Spaces adjacent to an occupied post.
    candidates: set[str] = set()
    for post_space in occupied_posts:
        candidates.update(board.neighbors(post_space))
    chosen = params.get("destination")
    if chosen is None:
        if len(candidates) == 1:
            chosen = next(iter(candidates))
        else:
            return state, [_event("raven_needs_input", kind="choose_photo_space", candidates=sorted(candidates))]
    if chosen not in candidates:
        raise EffectError(f"Not adjacent to an occupied post: {chosen}")
    return state, _send_to(state, player, chosen, board)


@register("stopped_and_searched")
def _stopped(state, player, params, *, board, rng, **kw):
    if not player.jewels:
        return state, [_event("stopped_and_searched", player=player.username, carried_jewels=0)]
    # Requires either playing a Disguise or forfeit.
    play_disguise = params.get("play_disguise", False)
    if play_disguise:
        # Rule engine should have already consumed the Disguise card before
        # dispatching (we just emit an event).
        return state, [_event("disguise_shown", player=player.username)]
    # Forfeit all jewels + weapons, go to Bloody Tower (sent).
    dropped_jewels = list(player.jewels)
    player.jewels = []
    # Jewels return to their initial spaces (or White Tower defaults).
    for j in dropped_jewels:
        orig = board.data.initial_jewel_locations.get(j)
        if orig:
            state.jewels_available[j] = orig
    lost_weapons: list[Card] = []
    remaining: list[Card] = []
    for c in player.hand:
        if c.category == "weapon":
            lost_weapons.append(c)
        else:
            remaining.append(c)
    player.hand = remaining
    # Send discarded weapons back to tower_discard list.
    state.tower_discard.extend(lost_weapons)
    # Move to Bloody Tower as sent.
    evs = _send_to(state, player, board.data.bloody_tower_space, board)
    player.status = Status.IMPRISONED
    player.status_turns_remaining = 3
    evs.append(_event("stopped_forfeit", player=player.username, jewels=dropped_jewels, weapons=[c.name for c in lost_weapons]))
    return state, evs


@register("clerk_tea_exception")
def _clerk(state, player, params, *, board, rng, **kw):
    evs: EventList = []
    for p in state.players:
        if p.escaped:
            continue
        if p.status in (Status.RACKED, Status.IMPRISONED, Status.TORTURED):
            continue
        if p.position != board.data.queens_house_space:
            old = p.position
            p.position = board.data.queens_house_space
            evs.append(_event("player_moved", player=p.username, src=old, dst=p.position, move_kind="clerk_tea"))
    state.turn.extra_turns_queued += 1
    evs.append(_event("extra_turn_queued", player=player.username))
    return state, evs


@register("ghost")
def _ghost(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.chapel_royal_space, board)
    player.miss_next_turn = True
    evs.append(_event("ghost", player=player.username))
    return state, evs


@register("queens_birthday")
def _birthday(state, player, params, *, board, rng, **kw):
    # The engine's clock is injected via params['today'] = "YYYY-MM-DD".
    today = params.get("today", "")
    is_april_21 = today.endswith("-04-21")
    draws_per_player = 2 if is_april_21 else 1
    evs: EventList = []
    for p in state.players:
        if p.escaped:
            continue
        for _ in range(draws_per_player):
            if not state.tower_draw:
                state.tower_draw = state.tower_discard
                state.tower_discard = []
                rng.shuffle(state.tower_draw)
            if not state.tower_draw:
                break
            card = state.tower_draw.pop()
            p.add_card(card)
            evs.append(_event("tower_card_drawn", player=p.username, card=card.id))
    return state, evs


@register("lost")
def _lost(state, player, params, *, board, rng, **kw):
    return state, _send_to(state, player, board.data.queens_house_space, board)


@register("chief_yeoman_passes")
def _chief(state, player, params, *, board, rng, **kw):
    if not state.tower_draw:
        state.tower_draw = state.tower_discard
        state.tower_discard = []
        rng.shuffle(state.tower_draw)
    if not state.tower_draw:
        return state, [_event("tower_deck_empty")]
    card = state.tower_draw.pop()
    player.add_card(card)
    return state, [_event("tower_card_drawn", player=player.username, card=card.id)]


@register("bowyer_questioning")
def _bowyer(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.bowyer_tower_space, board)
    player.status = Status.TORTURED
    player.status_turns_remaining = 3
    evs.append(_event("bowyer_questioning", player=player.username))
    return state, evs


@register("shop_for_film")
def _shop(state, player, params, *, board, rng, **kw):
    return state, _send_to(state, player, board.data.shop_space, board)


@register("governors_tea")
def _gov_tea(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.queens_house_space, board)
    player.miss_next_turn = True
    evs.append(_event("governors_tea", player=player.username))
    return state, evs


@register("beauchamp_imprisonment")
def _beauchamp(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.beauchamp_tower_space, board)
    player.status = Status.IMPRISONED
    player.status_turns_remaining = 3
    evs.append(_event("beauchamp_imprisonment", player=player.username))
    return state, evs


@register("rack_of_torment")
def _rack_raven(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.rack_space, board)
    player.status = Status.RACKED
    player.status_turns_remaining = 3
    # On Rack entry: if coin held, lose it; else lose entire hand.
    if player.has_coin:
        player.has_coin = False
        state.coins_available = min(5, state.coins_available + 1)
        evs.append(_event("rack_coin_lost", player=player.username))
    else:
        lost = player.hand
        player.hand = []
        state.tower_discard.extend(lost)
        evs.append(_event("rack_hand_lost", player=player.username, count=len(lost)))
    return state, evs


@register("metallicity")
def _metallicity(state, player, params, *, board, rng, **kw):
    """For each jewel still in the White Tower: relocate + mark loose."""
    dests = board.data.metallicity_destination_ids
    if not dests:
        raise EffectError("Board has no metallicity destinations configured")
    moved: list[tuple[str, str]] = []
    for jewel_id, space_id in list(state.jewels_available.items()):
        if board.space(space_id).region != "white_tower":
            continue
        new_space = rng.choice(dests)
        state.jewels_available.pop(jewel_id)
        state.loose_jewels.setdefault(new_space, []).append(jewel_id)
        moved.append((jewel_id, new_space))
    return state, [_event("metallicity", moved=[{"jewel": j, "space": s} for j, s in moved])]
