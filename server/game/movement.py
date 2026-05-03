"""Movement engine.

The movement engine is a pure function of ``(board, from_space, steps,
player, warder_blocks)`` — it does not mutate state. Its caller (the rule
engine) decides whether to auto-resolve (single-path / forward-only) or ask
the player for a decision via the ``CHOOSING_PATH`` phase.

Wall-walk movement is forward-only along the cyclic wall-walk order until the
player has been accredited. After accreditation, the wall walk behaves as an
arbitrary graph just like the inner ward.

Warder blocking support (Phase 8) is represented by an opt-in
``warder_blocking_spaces`` parameter — we treat those spaces as impassable
unless the player intends to play a ``Disguise`` (which is handled in the
rule layer, not here).
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
    - ``forced_single`` indicates movement cannot branch (wall-walk forward-
      only, or there is literally one valid destination) and can be auto-
      committed by the caller.
    """

    __slots__ = ("destinations", "intermediate_enemies", "forced_single")

    def __init__(
        self,
        destinations: dict[str, list[str]],
        intermediate_enemies: dict[str, list[str]],
        forced_single: bool,
    ):
        self.destinations = destinations
        self.intermediate_enemies = intermediate_enemies
        self.forced_single = forced_single

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
    """
    if steps <= 0:
        return MoveOptions({}, {}, True)

    blocked: set[str] = set(warder_blocking_spaces or ())

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

    others = set(other_player_positions or ())
    destinations: dict[str, list[str]] = {}
    intermediate_enemies: dict[str, list[str]] = {}
    for dest, path in raw.items():
        destinations[dest] = path
        enemies_on_path = [sid for sid in path[1:] if sid in others]
        if enemies_on_path:
            intermediate_enemies[dest] = enemies_on_path
            # Also surface each pass-through enemy as its own early-stop
            # destination so the client can show it as a clickable square.
            for sid in enemies_on_path:
                if sid == dest:
                    continue
                idx = path.index(sid)
                if sid not in destinations:
                    destinations[sid] = path[: idx + 1]

    # If the move forces the player through an enemy they can choose to stop
    # at for combat, that is a real decision — don't auto-commit.
    has_combat_choice = bool(intermediate_enemies)
    forced_single = (not has_combat_choice) and (
        forward_only or (len(destinations) == 1)
    )
    return MoveOptions(destinations, intermediate_enemies, forced_single)


def split_movement(
    board: Board,
    from_space: str,
    total_steps: int,
    split_first: int,
    player: PlayerState,
    warder_blocking_spaces: Optional[Iterable[str]] = None,
    other_player_positions: Optional[Iterable[str]] = None,
    visited_this_turn: Optional[Iterable[str]] = None,
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
    )
    return opts, total_steps - split_first
