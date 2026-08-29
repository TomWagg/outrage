"""Board graph with convenience helpers used by the engine.

A :class:`Board` is constructed from a :class:`BoardData` (validated Pydantic
model). It caches lookups and provides movement-adjacent helpers that don't
care about player state: raw neighbour sets, BFS reachability subject to
blocked spaces and direction constraints, shortest path, etc.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from .board_schema import BoardData, SpaceData, SpaceKind

log = logging.getLogger(__name__)


#: Cache key for :meth:`Board.reachable`: everything the answer depends on.
_ReachKey = tuple[str, int, bool, frozenset[str], frozenset[str]]


def _copy_paths(paths: dict[str, list[str]]) -> dict[str, list[str]]:
    """Copy a ``{destination: path}`` map so callers can't reach into the cache."""
    return {dest: list(path) for dest, path in paths.items()}


class Board:
    def __init__(self, data: BoardData):
        self.data = data
        self._by_id: dict[str, SpaceData] = {s.id: s for s in data.spaces}
        self._by_label: dict[str, SpaceData] = {s.label.lower(): s for s in data.spaces}
        # Neighbour set including slide targets.
        self._neighbors: dict[str, set[str]] = {s.id: set(s.neighbors) for s in data.spaces}
        # Slides are effectively edges that may be one-way.
        for sl in data.slides:
            self._neighbors.setdefault(sl.src, set()).add(sl.to)
            if sl.bidirectional:
                self._neighbors.setdefault(sl.to, set()).add(sl.src)
        # Built-in traversal edges (the Chapel Royal ↔ Salt Tower secret
        # passage) are part of the board itself, so they belong in the plain
        # neighbour graph. Card-gated edges (rope, ladder) deliberately are
        # not: whether they exist depends on the mover's hand, which the board
        # knows nothing about.
        for te in data.traversal_edges:
            if not te.built_in or te.requires_card:
                continue
            if te.movement_cost != 1:
                # The movement search is unweighted; a multi-step edge would
                # silently count as one. Better to leave it out and be loud.
                log.warning(
                    "Traversal edge %s has movement_cost=%s; only cost-1 edges "
                    "are supported, skipping", te.id or f"{te.src}->{te.to}", te.movement_cost,
                )
                continue
            if te.direction in ("bidirectional", "forward"):
                self._neighbors.setdefault(te.src, set()).add(te.to)
            if te.direction in ("bidirectional", "backward"):
                self._neighbors.setdefault(te.to, set()).add(te.src)
        # ...and the card-gated ones must not be reachable by accident. If a
        # card-gated edge is also listed in the ``neighbors`` array of either
        # square it joins, that second listing would make the shortcut free to
        # everybody. Strip it here, loudly.
        for te in data.traversal_edges:
            if not te.requires_card:
                continue
            for a, b in ((te.src, te.to), (te.to, te.src)):
                if b in self._neighbors.get(a, ()):
                    log.warning(
                        "Traversal edge %s is card-gated (%s) but %s lists %s as a "
                        "plain neighbour; dropping the free edge",
                        te.id or f"{te.src}->{te.to}", te.requires_card, a, b,
                    )
                    self._neighbors[a].discard(b)
        # Wall-walk order, start square first.
        walk = [s for s in data.spaces if s.region == "wall_walk" and s.wall_walk_order is not None]
        walk.sort(key=lambda s: s.wall_walk_order or 0)
        self._walk_order: list[str] = [s.id for s in walk]
        self._walk_index: dict[str, int] = {sid: i for i, sid in enumerate(self._walk_order)}

        # Sanity check neighbours refer to real spaces.
        for sid, nbrs in self._neighbors.items():
            for n in nbrs:
                if n not in self._by_id:
                    raise ValueError(f"Board: space {sid!r} references unknown neighbour {n!r}")

        # Memoised :meth:`reachable` results. The graph is fixed for the life of
        # the Board, so a query is a pure function of its arguments — and the
        # engine asks the same ones repeatedly (a split roll probes every leg
        # length for every player, and the planner re-runs on each re-render).
        self._reach_cache: dict[_ReachKey, dict[str, list[str]]] = {}

    # ---------- constructors -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Board":
        # The on-disk board may include ``_section`` separator dicts in the
        # ``spaces`` array purely for human readability. Filter those out
        # before validating so Pydantic doesn't see malformed SpaceData.
        if isinstance(data, dict) and isinstance(data.get("spaces"), list):
            data = dict(data)
            data["spaces"] = [s for s in data["spaces"] if not (isinstance(s, dict) and "_section" in s and "id" not in s)]
        return cls(BoardData.model_validate(data))

    @classmethod
    def from_file(cls, path: Path | str) -> "Board":
        return cls.from_dict(json.loads(Path(path).read_text()))

    # ---------- lookups ------------------------------------------------------

    @property
    def spaces(self) -> list[SpaceData]:
        return self.data.spaces

    def space(self, space_id: str) -> SpaceData:
        return self._by_id[space_id]

    def has_space(self, space_id: str) -> bool:
        return space_id in self._by_id

    def space_by_name(self, label: str) -> Optional[SpaceData]:
        return self._by_label.get(label.lower())

    def space_kind_of(self, space_id: str) -> SpaceKind:
        return self._by_id[space_id].kind

    def neighbors(self, space_id: str) -> set[str]:
        return self._neighbors.get(space_id, set())

    def tower_card_spaces(self) -> list[str]:
        """Every space that hands out a tower card when you land on it.

        The Summons that reads "go to any tower" is scoped to these: the Towers
        proper plus the Devereux, the Museum and the Royal Armouries, less the
        Broad Arrow Tower, which surrenders your weapons instead of dealing you
        a card. Driven off the same board rules the landing resolver uses, so
        the two can't drift.
        """
        kinds = set(getattr(self.data.rules, "tower_card_draw_kinds", []) or ["tower"])
        exceptions = set(
            getattr(self.data.rules, "tower_card_draw_exception_space_ids", []) or []
        )
        return [
            s.id for s in self.data.spaces
            if s.kind in kinds and s.id not in exceptions
        ]

    @property
    def rack_exit_space(self) -> Optional[str]:
        """The square a released prisoner steps out onto.

        Serving the sentence unlocks the door; it does not leave you standing
        in the cell. The Rack is a dead end with exactly one way out, so the
        board file need not spell it out — but ``rack_exit_space`` overrides
        the derivation if a board ever gives the Rack more than one neighbour.
        """
        explicit = self.data.rack_exit_space
        if explicit:
            return explicit
        rack = self.data.rack_space
        if not rack:
            return None
        nbrs = sorted(self._neighbors.get(rack, ()))
        return nbrs[0] if nbrs else None

    def wall_walk_order_of(self, space_id: str) -> Optional[int]:
        sp = self._by_id.get(space_id)
        return sp.wall_walk_order if sp else None

    def wall_walk_order(self) -> list[str]:
        """The wall-walk spaces in order, from the start square to Queen's House."""
        return list(self._walk_order)

    def next_wall_walk_space(self, space_id: str) -> Optional[str]:
        """Follow the wall-walk order forward by one step.

        The wall walk is a linear dead-end from ``ww00_start`` to Queen's
        House, so this returns ``None`` on the final wall-walk space.
        """
        idx = self._walk_index.get(space_id)
        if idx is None or not self._walk_order:
            return None
        nxt = idx + 1
        if nxt >= len(self._walk_order):
            return None
        return self._walk_order[nxt]

    def prev_wall_walk_space(self, space_id: str) -> Optional[str]:
        """Follow the wall-walk order backward by one step.

        Returns ``None`` at ``ww00_start`` (the walk is linear, so there is
        nothing before it) or for a space outside the wall walk.
        """
        idx = self._walk_index.get(space_id)
        if idx is None or idx <= 0:
            return None
        return self._walk_order[idx - 1]

    # ---------- reachability cache -------------------------------------------

    #: Entries to keep before dropping the oldest. A turn touches a handful of
    #: distinct queries; the cap only exists so a long game can't grow it without
    #: bound.
    _REACH_CACHE_MAX = 4096

    def _cache_get(self, key: "_ReachKey") -> Optional[dict[str, list[str]]]:
        hit = self._reach_cache.get(key)
        return None if hit is None else _copy_paths(hit)

    def _cache_put(
        self, key: "_ReachKey", value: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Store ``value`` and hand the caller its own copy to do as it likes with."""
        if len(self._reach_cache) >= self._REACH_CACHE_MAX:
            # Plain FIFO eviction: dicts keep insertion order, and the access
            # pattern is "the current turn", not a long tail worth ranking.
            for stale in list(self._reach_cache)[: self._REACH_CACHE_MAX // 4]:
                del self._reach_cache[stale]
        self._reach_cache[key] = value
        return _copy_paths(value)

    # ---------- traversal ----------------------------------------------------

    def path_between(self, src: str, dst: str, blocked: Optional[Iterable[str]] = None) -> Optional[list[str]]:
        """Shortest unweighted path from ``src`` to ``dst`` respecting ``blocked``.

        Returns a list of space ids starting with ``src`` and ending with
        ``dst`` (inclusive), or ``None`` if unreachable. Blocked spaces may not
        be entered even as intermediate stops (they are impassable). ``src``
        itself is allowed to be blocked (we start on it).
        """
        if src == dst:
            return [src]
        blocked_set: set[str] = set(blocked or ())
        parent: dict[str, str] = {}
        visited: set[str] = {src}
        q: deque[str] = deque([src])
        while q:
            cur = q.popleft()
            for nxt in self._neighbors.get(cur, ()):
                if nxt in visited or nxt in blocked_set:
                    continue
                visited.add(nxt)
                parent[nxt] = cur
                if nxt == dst:
                    # reconstruct
                    path = [nxt]
                    while path[-1] != src:
                        path.append(parent[path[-1]])
                    path.reverse()
                    return path
                q.append(nxt)
        return None

    def reachable(
        self,
        from_space: str,
        steps: int,
        *,
        forward_only: bool = False,
        blocked: Optional[Iterable[str]] = None,
        visited: Optional[Iterable[str]] = None,
    ) -> dict[str, list[str]]:
        """Return ``{destination_space_id: path}`` for spaces reachable in exactly ``steps``.

        - ``steps <= 0`` returns an empty dict.
        - ``forward_only`` walks the wall-walk order forward (pre-accreditation).
          Starting outside the wall walk, or reaching the dead-end before the
          step count is exhausted, yields no destinations.
        - ``blocked`` is a set of space ids that may not be entered.
        - ``visited`` is a set of space ids that cannot be re-entered this turn.
          The starting space is always implicitly excluded from being re-entered
          mid-path.

        The returned path includes the starting space as index 0 and the
        destination at index ``steps``. Free movement enumerates all *simple*
        paths of exactly ``steps`` and records one path per reachable
        destination (the first found by DFS). Alternate routes between the
        same pair can be probed via :meth:`path_between`.

        Results are memoised on the board (see :meth:`_cache_get`); callers get
        a private copy and may keep or mutate it freely.
        """
        if steps <= 0:
            return {}
        blocked_set: frozenset[str] = frozenset(blocked or ())
        visited_set: frozenset[str] = frozenset(visited or ())
        key = (from_space, steps, forward_only, blocked_set, visited_set)
        hit = self._cache_get(key)
        if hit is not None:
            return hit

        if forward_only:
            if from_space not in self._walk_index or not self._walk_order:
                return self._cache_put(key, {})
            path = [from_space]
            cur = from_space
            for _ in range(steps):
                nxt = self.next_wall_walk_space(cur)
                if nxt is None:
                    # Dead-end (Queen's House terminal): overshooting a roll
                    # simply lands the player on the final space. If we haven't
                    # advanced at all, there's no legal destination.
                    break
                if nxt in blocked_set or nxt in visited_set:
                    return self._cache_put(key, {})
                path.append(nxt)
                cur = nxt
            if cur == from_space:
                return self._cache_put(key, {})
            return self._cache_put(key, {cur: path})

        # Free movement: enumerate simple paths of exactly ``steps``, where
        # "simple" is relative to both this move *and* the turn's prior
        # visited-set.
        results: dict[str, list[str]] = {}
        path: list[str] = [from_space]
        on_path: set[str] = {from_space} | visited_set

        def dfs(depth: int) -> None:
            cur = path[-1]
            if depth == steps:
                # Don't register the starting space as a destination.
                if cur != from_space and cur not in results:
                    results[cur] = list(path)
                return
            for nxt in self._neighbors.get(cur, ()):
                if nxt in blocked_set or nxt in on_path:
                    continue
                path.append(nxt)
                on_path.add(nxt)
                dfs(depth + 1)
                path.pop()
                on_path.discard(nxt)

        dfs(0)
        return self._cache_put(key, results)

    def reachable_within(
        self,
        from_space: str,
        max_steps: int,
        *,
        blocked: Optional[Iterable[str]] = None,
    ) -> dict[str, int]:
        """Return {space_id: distance} for all spaces within ``max_steps`` (inclusive, >0)."""
        if max_steps <= 0:
            return {}
        blocked_set: set[str] = set(blocked or ())
        dist: dict[str, int] = {from_space: 0}
        q: deque[str] = deque([from_space])
        while q:
            cur = q.popleft()
            d = dist[cur]
            if d >= max_steps:
                continue
            for nxt in self._neighbors.get(cur, ()):
                if nxt in blocked_set or nxt in dist:
                    continue
                dist[nxt] = d + 1
                q.append(nxt)
        dist.pop(from_space, None)
        return dist
