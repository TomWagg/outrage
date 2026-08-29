"""Movement engine.

The movement engine is a pure function of ``(board, from_space, steps,
player, ...)`` — it does not mutate state. Its caller (the rule engine)
decides whether to auto-resolve (single-path / forward-only) or ask the
player for a decision via the ``CHOOSING_PATH`` phase.

Wall-walk movement is forward-only along the linear wall-walk sequence until
the player has been accredited. The wall walk is a dead-end that terminates at
Queen's House (``ww77_queens_house``); overshooting simply lands the player on
the final space. After accreditation, the wall walk behaves as an arbitrary
graph just like the inner ward.

Warder blocking is represented by an opt-in ``warder_blocking_spaces``
parameter — those spaces are treated as impassable unless the player holds a
``Disguise`` card (which is handled and consumed in the rule layer, not here).
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .board import Board
from .state import PlayerState

log = logging.getLogger(__name__)


class MoveOptions:
    """Result of :func:`compute_destinations`.

    - ``destinations`` maps destination space id → path from current position
      (inclusive). For single-path movement this dict has exactly one entry.
    - ``intermediate_enemies`` lists spaces on any path that contain other
      players — potential combat-initiation or Lasso trigger points.
    - ``requires_disguise`` is the subset of ``destinations`` whose route runs
      through an occupied Yeoman Warder post. Reaching them costs the mover a
      Disguise card, spent at commit time, so the caller must not auto-commit
      one of these.
    - ``forced_single`` indicates movement cannot branch (wall-walk forward-
      only, or there is literally one valid destination) and can be auto-
      committed by the caller.
    """

    __slots__ = ("destinations", "intermediate_enemies", "requires_disguise", "forced_single")

    def __init__(
        self,
        destinations: dict[str, list[str]],
        intermediate_enemies: dict[str, list[str]],
        forced_single: bool,
        requires_disguise: Optional[set[str]] = None,
    ):
        self.destinations = destinations
        self.intermediate_enemies = intermediate_enemies
        self.forced_single = forced_single
        self.requires_disguise = requires_disguise or set()

    def only_destination(self) -> Optional[str]:
        if len(self.destinations) == 1:
            return next(iter(self.destinations))
        return None


def compute_destinations(
    board: Board,
    from_space: str,
    steps: int,
    player: PlayerState,
    warder_blocking_spaces: Optional[Iterable[str]] = None,
    other_player_positions: Optional[Iterable[str]] = None,
    visited_this_turn: Optional[Iterable[str]] = None,
    allow_combat_stops: bool = True,
    disguise_available: bool = False,
) -> MoveOptions:
    """Enumerate legal terminal squares for a move of ``steps``.

    Players may end their move on another player's square — choosing to do so
    is the pass-through combat rule: the mover may stop at or past any enemy
    they reach, ending their turn there after combat. Every path that passes
    through an enemy also surfaces truncated-path entries keyed by each enemy
    along the route so the client can "stop at" them explicitly.

    ``visited_this_turn`` supplies the squares the player has already stood on
    this turn — under ``rules.no_revisit_during_turn`` those cannot be re-entered
    by any path of this move.

    ``allow_combat_stops=False`` drops those truncated entries: the move must
    be walked in full. Split-7 legs use this. There the step count is chosen
    deliberately rather than rolled, so a player who wants a fight picks the
    exact leg that reaches their opponent — letting them also stop short would
    hand out a second, free choice of distance and let a leg of 4 be spent as a
    leg of 1. Landing *exactly* on an enemy still starts a fight; only stopping
    early is refused.

    The escape square is a special case in the other direction: it is added
    whenever it is *within* ``steps``, not exactly on it. See the comment at the
    end of the body.

    ``disguise_available=True`` says the mover is holding a Disguise but has not
    played it. Routes through an occupied post are then enumerated *as well*, and
    reported in ``requires_disguise`` for the caller to charge the card for. A
    Disguise is worth nothing until you know you rolled far enough to use it, so
    the decision belongs here — at the point the destinations are chosen — rather
    than before the dice leave the hand.
    """
    if steps <= 0:
        return MoveOptions({}, {}, True)

    blocked: set[str] = set(warder_blocking_spaces or ())
    visited_set: set[str] = set(visited_this_turn or ())

    # Forward-only applies while on the wall walk and un-accredited.
    cur_space = board.space(from_space)
    forward_only = cur_space.region == "wall_walk" and not player.accredited

    raw = board.reachable(
        from_space,
        steps,
        forward_only=forward_only,
        blocked=blocked,
        visited=visited_this_turn,
    )
    # Second sweep with the posts open, for a mover carrying a Disguise. Run
    # after the free sweep and only for destinations it didn't reach, so a
    # square with a warder-free route is never billed for the card.
    raw_via_post: dict[str, list[str]] = {}
    if disguise_available and blocked:
        raw_via_post = board.reachable(
            from_space,
            steps,
            forward_only=forward_only,
            visited=visited_this_turn,
        )

    others = set(other_player_positions or ())
    destinations: dict[str, list[str]] = {}
    intermediate_enemies: dict[str, list[str]] = {}
    requires_disguise: set[str] = set()

    def record(dest: str, path: list[str]) -> None:
        if dest not in destinations:
            destinations[dest] = path
            if any(sid in blocked for sid in path[1:]):
                requires_disguise.add(dest)
        enemies_on_path = [sid for sid in path[1:] if sid in others]
        if not enemies_on_path:
            return
        intermediate_enemies[dest] = enemies_on_path
        if not allow_combat_stops:
            return
        # Also surface each pass-through enemy as its own early-stop
        # destination so the client can show it as a clickable square.
        for sid in enemies_on_path:
            if sid == dest or sid in destinations:
                continue
            prefix = path[: path.index(sid) + 1]
            destinations[sid] = prefix
            # Stopping short of the post is free even on a disguise route.
            if any(x in blocked for x in prefix[1:]):
                requires_disguise.add(sid)

    for dest, path in raw.items():
        record(dest, path)
    for dest, path in raw_via_post.items():
        if dest in destinations:
            continue
        record(dest, path)

    # The exit is a stop, not a precise landing. Reaching the Cradle Tower at all
    # is enough to walk out of it, so a roll bigger than the distance must not
    # strand a player one square short of the door — the same overshoot courtesy
    # the wall walk's dead end at Queen's House already gets. Offered whether or
    # not they can cash in: it's an ordinary square to anyone without a jewel and
    # a coin, and the engine decides that on arrival, not here.
    escape_added = False
    escape_id = board.data.escape_space
    if (
        not forward_only          # un-accredited players can't cross the wards
        and escape_id
        and escape_id != from_space
        and escape_id not in destinations
        and escape_id not in blocked
        and escape_id not in visited_set
    ):
        # ``src`` may be in the blocked set (we start on it); path_between allows
        # that, so the visited squares can go straight in.
        route = board.path_between(from_space, escape_id, blocked=blocked | visited_set)
        if route is not None and len(route) - 1 <= steps:
            destinations[escape_id] = route
            escape_added = True

    # If the move forces the player through an enemy they can choose to stop
    # at for combat, that is a real decision — don't auto-commit. Nor when a
    # destination would cost the mover their Disguise: spending a card is never
    # something to do on the player's behalf.
    has_combat_choice = allow_combat_stops and bool(intermediate_enemies)
    forced_single = (
        (not has_combat_choice)
        and not requires_disguise
        and not escape_added
        and (forward_only or (len(destinations) == 1))
    )
    return MoveOptions(destinations, intermediate_enemies, forced_single, requires_disguise)


def split_movement(
    board: Board,
    from_space: str,
    total_steps: int,
    split_first: int,
    player: PlayerState,
    warder_blocking_spaces: Optional[Iterable[str]] = None,
    other_player_positions: Optional[Iterable[str]] = None,
    visited_this_turn: Optional[Iterable[str]] = None,
    allow_combat_stops: bool = True,
) -> tuple[MoveOptions, int]:
    """Helper for split-7-style segmented movement.

    Returns ``(first_segment_options, remaining_steps)``. The caller resolves
    the first segment (with landing effects) and then invokes
    ``compute_destinations`` again from the new position with
    ``remaining_steps`` for the second segment.
    """
    if split_first < 0 or split_first > total_steps:
        raise ValueError("split_first out of range")
    opts = compute_destinations(
        board,
        from_space,
        split_first,
        player,
        warder_blocking_spaces=warder_blocking_spaces,
        other_player_positions=other_player_positions,
        visited_this_turn=visited_this_turn,
        allow_combat_stops=allow_combat_stops,
    )
    return opts, total_steps - split_first
