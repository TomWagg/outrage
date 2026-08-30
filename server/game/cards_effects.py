"""Effect dispatch registry for tower and raven cards.

Each handler has the signature::

    handler(state, player, params, *, board, rng, **kwargs) -> (state, events)

``events`` is a list of ``LogEntry``-compatible dicts. Handlers mutate state
in place and return it (Pydantic models are mutable). The rule engine does
**not** deep-copy state around effect dispatch — handlers are expected to
validate before mutating so that a raised :class:`EffectError` leaves the
state consistent.

Several raven effects can't be fully resolved without player input (e.g.
``go_to_location`` with ``player_choice``, ``call_warder_to_post`` with
``chooser``, bench selection, warder selection, etc.). Those handlers set
``state.turn.pending_raven`` and leave the engine in
:attr:`Phase.RAVEN_EFFECT`; the rule engine later accepts a
``resolve_raven_effect`` intent and calls back into the handler.
"""
from __future__ import annotations

import logging
from datetime import date
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
    RackEscrow,
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


def _summon_to(state: GameState, player: PlayerState, space_id: str, board: Board) -> EventList:
    """Teleport, then run the destination square's own landing effects.

    Cards that *summon* a player drop them onto a live square: the Museum still
    hands out a tower card, Queen's House still starts the accreditation trial,
    "Go to Shop" squares still fire. Cards that *punish* deliberately use plain
    ``_send_to`` instead — the hospital, the Rack and the prison towers are the
    whole effect, and re-resolving them would stack a second penalty on top.
    """
    evs = _send_to(state, player, space_id, board)
    # Deferred import: rules imports this module at module level.
    from .rules import resolve_landing_after_summons
    evs.extend(resolve_landing_after_summons(state, board, player))
    return evs


def _draw_tower_card(state: GameState) -> Optional[Card]:
    """Draw one tower card, recycling the discard pile if the deck has run out.

    Thin wrapper over ``rules._draw_tower`` (deferred import — rules imports this
    module at module level) so every draw site recycles and reshuffles the same
    way.
    """
    from .rules import _draw_tower
    return _draw_tower(state)


def _clear_confinement(player: PlayerState) -> None:
    player.status = Status.NORMAL
    player.status_turns_remaining = 0


#: How many turns a Rack sentence runs for.
RACK_TURNS = 3


def send_to_rack(
    state: GameState, player: PlayerState, board: Board, cause: str = "rack_sender",
) -> EventList:
    """Put ``player`` on the Rack for three turns and take the entry toll.

    The toll is every jewel they are carrying, plus *either* the coin, if they
    hold one, *or* their whole hand. The single entry point for all three routes
    onto the Rack — the ``rack_of_torment`` raven card, landing on a
    ``rack_sender`` square, and Firecrackers — so they can't drift apart.

    Nothing is destroyed here. The confiscated goods go into
    :class:`~server.game.state.RackEscrow`, because a Rack Pardon reverses the
    whole sentence: see :func:`~server.game.rules._release_from_rack` for the
    point at which the loss becomes permanent instead.

    A Rack Pardon in the hand is the one thing the toll leaves behind. Taking it
    would confiscate the only answer to the very sentence being handed down, and
    a card that cannot be played at the one moment it is for is not a card.
    """
    evs = _send_to(state, player, board.data.rack_space, board)
    player.status = Status.RACKED
    player.status_turns_remaining = RACK_TURNS
    # A second sentence before the first is served adds to the same pile rather
    # than replacing it, or the earlier haul would be lost with no way back.
    escrow = player.rack_escrow or RackEscrow()

    jewels = list(player.jewels)
    escrow.jewels.extend(jewels)
    player.jewels = []

    if player.has_coin:
        player.has_coin = False
        escrow.coin = True
        penalty, lost_cards = "coin", 0
    else:
        kept = [c for c in player.hand if c.effect_key == "rack_pardon"]
        taken = [c for c in player.hand if c.effect_key != "rack_pardon"]
        lost_cards = len(taken)
        escrow.cards.extend(taken)
        player.hand = kept
        penalty = "hand"

    player.rack_escrow = escrow
    evs.append(_event(
        "sent_to_rack", player=player.username, cause=cause,
        penalty=penalty, jewels=jewels, cards_taken=lost_cards,
        turns=RACK_TURNS,
    ))
    return evs


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
        if player.accredited:
            # Nothing to buy; refuse rather than burn the card for no effect.
            raise EffectError("You are already accredited")
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
    if player.confined:
        # The Chapel is on the other side of a locked door.
        raise EffectError("You cannot claim Sanctuary while locked up")
    evs = _send_to(state, player, board.data.chapel_royal_space, board)
    return state, evs


@register("disguise")
def _disguise(state, player, params, *, board, rng, **kw):
    """Walk out of prison, or slip past a Yeoman Warder post.

    Two uses, and which one you get is never a real choice: a player locked in
    the Bloody or Beauchamp Tower cannot move at all, so free passage past a
    post is worth nothing to them and the card must be the way out. Only prison
    — the Rack and the Bowyer Tower's questioning both hold you regardless.

    Otherwise it sets ``turn.disguise_used`` and movement blocking is lifted;
    the card is already removed from hand by the pre-roll intent handler.
    """
    if player.status == Status.IMPRISONED:
        _clear_confinement(player)
        return state, [_event("disguise_played", player=player.username, via="prison")]
    state.turn.disguise_used = True
    return state, [_event("disguise_played", player=player.username)]


@register("royal_pardon")
def _royal_pardon(state, player, params, *, board, rng, **kw):
    if player.status in (Status.IMPRISONED, Status.TORTURED):
        _clear_confinement(player)
        return state, [_event("pardoned", player=player.username, pardon_kind="royal")]
    raise EffectError("Royal Pardon only works for prison or torture")


@register("rack_pardon")
def _rack_pardon(state, player, params, *, board, rng, **kw):
    """Tear up a Rack sentence: everything confiscated comes back, and you walk.

    The whole point of the card is that the Rack costs you nothing, so the
    escrow is handed back before the status is cleared — the jewels, and the
    coin or the hand, whichever the toll took.
    """
    if player.status != Status.RACKED:
        raise EffectError("Rack Pardon only works for the Rack")
    escrow = player.rack_escrow
    returned_jewels: list[JewelId] = []
    returned_cards = 0
    returned_coin = False
    if escrow is not None:
        returned_jewels = list(escrow.jewels)
        player.jewels.extend(returned_jewels)
        returned_cards = len(escrow.cards)
        for card in escrow.cards:
            player.add_card(card)
        returned_coin = escrow.coin
        if returned_coin:
            player.has_coin = True
        player.rack_escrow = None
    _clear_confinement(player)
    evs = [_event(
        "pardoned", player=player.username, pardon_kind="rack",
        jewels_returned=returned_jewels, cards_returned=returned_cards,
        coin_returned=returned_coin,
    )]
    # Walk them out of the cell as well — the Rack is a dead end and being
    # left standing on it is one forced step from being sent back down.
    exit_space = board.rack_exit_space
    if exit_space and player.position == board.data.rack_space:
        evs.extend(_send_to(state, player, exit_space, board))
    return state, evs


@register("confession")
def _confession(state, player, params, *, board, rng, **kw):
    """Frame another player: swap positions, move torture status to them."""
    if player.status != Status.TORTURED:
        raise EffectError("Confession only playable while Tortured")
    target_name = params.get("target")
    if not target_name:
        raise EffectError("Confession requires a target player")
    if target_name == player.username:
        raise EffectError("You cannot confess against yourself")
    target = state.player(target_name)
    if target.confined:
        # They're already behind a different door; there's no swap to make.
        raise EffectError(f"{target_name} is already locked up")
    from .rules import immune_to_forced_moves as _immune
    if _immune(board, target):
        raise EffectError(f"{target_name} cannot be dragged out of there")
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
        if board.space(p.position).region == "white_tower"
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
    if target.confined:
        raise EffectError(f"{target_name} is locked up and cannot be moved")
    from .rules import immune_to_forced_moves as _immune
    if _immune(board, target):
        raise EffectError(f"{target_name} cannot be dragged out of there")
    if player.confined:
        raise EffectError("You cannot throw a Lasso while locked up")
    dist_map = board.reachable_within(player.position, 5)
    if target.position not in dist_map:
        raise EffectError(f"Target {target_name!r} not within 5 spaces of you")
    old = target.position
    target.position = player.position
    from .rules import cancel_rest_if_moved_off
    evs = [
        _event("lassoed", roper=player.username, target=target.username, src=old, dst=player.position),
    ]
    evs.extend(cancel_rest_if_moved_off(state, board, target, old))
    return state, evs


@register("binary_disruption")
def _binary_disruption(state, player, params, *, board, rng, **kw):
    """Deal the roll you have just thrown out between yourself and an opponent.

    Played after the dice are down but before they are spent, which is the only
    moment it means anything — you play it because of the numbers you can see.

    It rearranges the roll rather than re-rolling it, so the two dice go one
    each: a 5 and a 3 send one player 5 and the other 3. That is what separates
    it from a natural seven, which may be cut anywhere.

    Refuses when nobody can be moved by either die — the card would be spent for
    nothing, so it stays in hand instead.
    """
    from .rules import arm_binary_disruption
    return state, arm_binary_disruption(state, player, board=board)


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
    """A Summons: obey it, or refuse and forfeit your next turn instead.

    The choice is always the player's, so the card parks itself for input even
    when the destination is fixed. ``params`` comes back as ``{"decline": True}``
    to refuse, or ``{"accept": True}`` (plus ``chosen`` for the "any tower you
    like" variant) to go.
    """
    if params.get("decline"):
        player.miss_next_turn = True
        return state, [_event(
            "summons_declined", player=player.username,
            location=params.get("location"),
        )]
    player_choice = params.get("location") == "player_choice"
    if not params.get("accept") and not (player_choice and params.get("chosen")):
        return state, [_event(
            "raven_needs_input",
            input_kind="choose_location" if player_choice else "summons",
            choices=board.tower_card_spaces() if player_choice else None,
        )]
    loc = _location_from_params(params, board)
    if loc is None:
        # Resolve from chosen parameter. "Any tower you like" means a tower —
        # any square that deals you a tower card — not any square on the board.
        loc = params.get("chosen")
        allowed = board.tower_card_spaces()
        if loc is None or loc not in allowed:
            raise EffectError(f"The Summons only reaches a tower, not {loc!r}")
    return state, _summon_to(state, player, loc, board)


@register("go_to_jewel_view")
def _go_to_jewel_view(state, player, params, *, board, rng, **kw):
    """Move to the jewel's current space and queue an optional attempt."""
    jewel_id: JewelId = params["jewel"]
    # If the jewel is already taken, nothing happens (beyond noting it).
    if jewel_id not in state.jewels_available:
        return state, [_event("jewel_already_taken", jewel=jewel_id)]
    space_id = state.jewels_available[jewel_id]
    evs = _send_to(state, player, space_id, board)
    state.turn.pending_jewel = PendingJewelAttempt(
        jewel_id=jewel_id, space_id=space_id, player=player.username,
        source="raven_view",
    )
    evs.append(_event("jewel_attempt_offered", jewel=jewel_id))
    return state, evs


def free_warder_posts(state: GameState, board: Board) -> list[str]:
    """Post ids with no warder standing on them.

    A post holds one warder; calling a second to the same post would stack them
    invisibly and make the block un-clearable, so callers must pick from here.
    """
    taken = {w.location for w in state.warders if w.location != board.data.barracks_space}
    return [wp.id for wp in board.data.warder_posts if wp.space_id not in taken]


@register("call_warder_to_post")
def _call_warder(state, player, params, *, board, rng, **kw):
    post = params.get("post")
    free = free_warder_posts(state, board)
    if post == "chooser" and not params.get("chosen_post"):
        if not free:
            return state, [_event("no_free_warder_posts")]
        if len(free) == 1:
            post = free[0]          # nothing to choose between
        else:
            return state, [_event("raven_needs_input", input_kind="choose_post", posts=free)]
    elif post == "chooser":
        post = params["chosen_post"]
        if post not in free:
            raise EffectError(f"That post already has a warder: {post}")
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
    if post not in free:
        # A fixed-post card whose post is already manned: nothing to do.
        return state, [_event("warder_post_occupied", post=post, space=post_space)]
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
            return state, [_event("raven_needs_input", input_kind="choose_warder")]
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
            return state, [_event("raven_needs_input", input_kind="choose_bench")]
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
            return state, [_event("raven_needs_input", input_kind="choose_photo_space", candidates=sorted(candidates))]
    if chosen not in candidates:
        raise EffectError(f"Not adjacent to an occupied post: {chosen}")
    return state, _summon_to(state, player, chosen, board)


def _disguise_in_hand(player: PlayerState) -> Optional[Card]:
    return next(
        (c for c in player.hand if c.kind == "tower" and c.effect_key == "disguise"),
        None,
    )


@register("stopped_and_searched")
def _stopped(state, player, params, *, board, rng, **kw):
    if not player.jewels:
        return state, [_event("stopped_and_searched", player=player.username, carried_jewels=0)]

    # Carrying a jewel, so there is a decision to make: show a Disguise, or
    # forfeit. Park for input until they answer.
    play_disguise = params.get("play_disguise")
    if play_disguise is None:
        return state, [_event(
            "raven_needs_input", input_kind="stopped_and_searched",
            carried_jewels=len(player.jewels),
            has_disguise=_disguise_in_hand(player) is not None,
        )]

    if play_disguise:
        # Spent here — nothing upstream consumes the card for this path.
        card = _disguise_in_hand(player)
        if card is None:
            raise EffectError("You have no Disguise to show")
        player.remove_card(card.id)
        state.tower_discard.append(card)
        return state, [_event("disguise_shown", player=player.username, card=card.id)]
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
    from .rules import cancel_rest_if_moved_off, immune_to_forced_moves as _immune
    evs: EventList = []
    for p in state.players:
        if p.status in (Status.RACKED, Status.IMPRISONED, Status.TORTURED):
            continue
        # The summons doesn't reach inside the White Tower either.
        if _immune(board, p):
            continue
        if p.position != board.data.queens_house_space:
            old = p.position
            p.position = board.data.queens_house_space
            evs.append(_event("player_moved", player=p.username, src=old, dst=p.position, move_kind="clerk_tea"))
            evs.extend(cancel_rest_if_moved_off(state, board, p, old))
    state.turn.extra_turns_queued += 1
    evs.append(_event("extra_turn_queued", player=player.username))
    return state, evs


@register("ghost")
def _ghost(state, player, params, *, board, rng, **kw):
    evs = _send_to(state, player, board.data.chapel_royal_space, board)
    player.miss_next_turn = True
    evs.append(_event("ghost", player=player.username))
    return state, evs


#: The Queen's official birthday. Drawing this card on the day is worth double.
QUEENS_BIRTHDAY = (4, 21)


def _is_queens_birthday(params: dict[str, Any]) -> bool:
    """Is it the 21st of April?

    Reads the wall clock, which is the one piece of engine behaviour that
    legitimately depends on something outside :class:`GameState`. Tests (and a
    replay of a saved game) pin it by passing ``today`` as ``"YYYY-MM-DD"``.
    """
    stamp = params.get("today")
    if stamp:
        try:
            when = date.fromisoformat(str(stamp))
        except ValueError as exc:
            raise EffectError(f"Bad 'today' value: {stamp!r}") from exc
    else:
        when = date.today()
    return (when.month, when.day) == QUEENS_BIRTHDAY


@register("queens_birthday")
def _birthday(state, player, params, *, board, rng, **kw):
    """Everyone draws a tower card — two each on the Queen's birthday itself."""
    draws_per_player = 2 if _is_queens_birthday(params) else 1
    evs: EventList = []
    for p in state.players:
        for _ in range(draws_per_player):
            card = _draw_tower_card(state)
            if card is None:
                break
            p.add_card(card)
            evs.append(_event("tower_card_drawn", player=p.username, card=card.id))
    return state, evs


@register("lost")
def _lost(state, player, params, *, board, rng, **kw):
    return state, _summon_to(state, player, board.data.queens_house_space, board)


@register("chief_yeoman_passes")
def _chief(state, player, params, *, board, rng, **kw):
    card = _draw_tower_card(state)
    if card is None:
        return state, [_event("tower_deck_empty")]
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
    return state, _summon_to(state, player, board.data.shop_space, board)


@register("governors_tea")
def _gov_tea(state, player, params, *, board, rng, **kw):
    evs = _summon_to(state, player, board.data.queens_house_space, board)
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
    return state, send_to_rack(state, player, board, cause="raven")


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
