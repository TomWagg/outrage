"""Seeded RNG wrapper for deterministic engine behaviour.

All randomness in the engine must flow through a single ``Rng`` instance whose
seed is stored in ``GameState.seed``. This keeps games reproducible from an
auto-save file and makes unit tests fully deterministic.
"""
from __future__ import annotations

import random
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")


class Rng:
    """Thin, serialisable wrapper around :class:`random.Random`.

    The entire internal state can be extracted and re-applied which lets us
    persist the RNG inside an auto-save without re-deriving from the seed.
    """

    __slots__ = ("_r", "seed")

    def __init__(self, seed: int):
        self.seed = seed
        self._r = random.Random(seed)

    def roll_die(self) -> int:
        return self._r.randint(1, 6)

    def roll_dice(self, n: int = 2) -> list[int]:
        """Return ``n`` independent d6 rolls as a list."""
        return [self._r.randint(1, 6) for _ in range(n)]

    def randint(self, lo: int, hi: int) -> int:
        return self._r.randint(lo, hi)

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise IndexError("Rng.choice from empty sequence")
        return self._r.choice(seq)

    def shuffle(self, seq: list[T]) -> None:
        self._r.shuffle(seq)

    def shuffled(self, seq: Iterable[T]) -> list[T]:
        items = list(seq)
        self._r.shuffle(items)
        return items

    # --- persistence helpers -------------------------------------------------

    def getstate(self):  # type: ignore[no-untyped-def]
        return self._r.getstate()

    def setstate(self, state) -> None:  # type: ignore[no-untyped-def]
        self._r.setstate(state)
