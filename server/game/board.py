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
        # Wall-walk order cycle.
        walk = [s for s in data.spaces if s.region == "wall_walk" and s.wall_walk_order is not None]
        walk.sort(key=lambda s: s.wall_walk_order or 0)
        self._walk_order: list[str] = [s.id for s in walk]
        self._walk_index: dict[str, int] = {sid: i for i, sid in enumerate(self._walk_order)}

        # Sanity check neighbours refer to real spaces.
        for sid, nbrs in self._neighbors.items():
            for n in nbrs:
                if n not in self._by_id:
                    raise ValueError(f"Board: space {sid!r} references unknown neighbour {n!r}")

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

    def wall_walk_order_of(self, space_id: str) -> Optional[int]:
        sp = self._by_id.get(space_id)
        return sp.wall_walk_order if sp else None

    def wall_walk_cycle(self) -> list[str]:
        return list(self._walk_order)

    def next_wall_walk_space(self, space_id: str) -> Optional[str]:
        """Follow the wall-walk order forward by one step.

        The wall walk is a linear dead-end from ``ww00_start`` to
        Queen's House (``rules.wall_walk_is_closed_loop`` is False), so this
        returns ``None`` when called on the final wall-walk space.
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
        - ``visited`` is a set of space ids that cannot be re-entered this turn
          (per ``rules.no_revisit_during_turn``). The starting space is always
          implicitly excluded from being re-entered mid-path.

        The returned path includes the starting space as index 0 and the
        destination at index ``steps``. Free movement enumerates all *simple*
        paths of exactly ``steps`` and records one path per reachable
        destination (the first found by DFS). Alternate routes between the
        same pair can be probed via :meth:`path_between`.
        """
        if steps <= 0:
            return {}
        blocked_set: set[str] = set(blocked or ())
        visited_set: set[str] = set(visited or ())

        if forward_only:
            if from_space not in self._walk_index or not self._walk_order:
                return {}
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
                    return {}
                path.append(nxt)
                cur = nxt
            if cur == from_space:
                return {}
            return {cur: path}

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
        return results

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
