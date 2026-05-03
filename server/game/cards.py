"""Tower and raven card models + a small deck helper.

The canonical card JSON uses a ``count:`` field to represent multiples; we
expand each template into individual :class:`Card` instances with unique ids
so the engine and UI can address a specific card at any time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .rng import Rng

log = logging.getLogger(__name__)


CardKind = Literal["tower", "raven"]
TowerCategory = Literal["burglary", "weapon", "traversal", "utility", "custom"]


class Card(BaseModel):
    """A single materialised card, as it lives in decks/hands/discards."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: CardKind
    name: str
    category: Optional[TowerCategory] = None  # only for tower cards
    value: int = 0
    defender_only: bool = False
    effect_key: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)


class Deck(BaseModel):
    """A simple draw/discard pair with reshuffle-on-empty semantics."""

    model_config = ConfigDict(extra="forbid")

    draw_pile: list[Card] = Field(default_factory=list)
    discard_pile: list[Card] = Field(default_factory=list)

    def draw(self, rng: Rng) -> Optional[Card]:
        if not self.draw_pile:
            self.reshuffle_if_empty(rng)
        if not self.draw_pile:
            return None
        return self.draw_pile.pop()

    def discard(self, card: Card) -> None:
        self.discard_pile.append(card)

    def reshuffle_if_empty(self, rng: Rng) -> bool:
        if self.draw_pile or not self.discard_pile:
            return False
        self.draw_pile = self.discard_pile
        self.discard_pile = []
        rng.shuffle(self.draw_pile)
        return True

    def __len__(self) -> int:
        return len(self.draw_pile) + len(self.discard_pile)

    def size_draw(self) -> int:
        return len(self.draw_pile)


# ---------- loaders ---------------------------------------------------------


def _expand_tower(templates: list[dict[str, Any]]) -> list[Card]:
    cards: list[Card] = []
    counter: dict[str, int] = {}
    for t in templates:
        name = t["name"]
        count = int(t.get("count", 1))
        for _ in range(count):
            counter[name] = counter.get(name, 0) + 1
            cid = f"tower:{name.replace(' ', '_').lower()}:{counter[name]}"
            cards.append(
                Card(
                    id=cid,
                    kind="tower",
                    name=name,
                    category=t.get("category"),
                    value=int(t.get("value", 0)),
                    defender_only=bool(t.get("defender_only", False)),
                    effect_key=t.get("effect_key"),
                    params=t.get("params", {}),
                )
            )
    return cards


def _expand_raven(templates: list[dict[str, Any]]) -> list[Card]:
    cards: list[Card] = []
    counter: dict[str, int] = {}
    for t in templates:
        effect = t["effect_key"]
        count = int(t.get("count", 1))
        for _ in range(count):
            counter[effect] = counter.get(effect, 0) + 1
            cid = f"raven:{effect}:{counter[effect]}"
            cards.append(
                Card(
                    id=cid,
                    kind="raven",
                    name=effect,
                    effect_key=effect,
                    params=t.get("params", {}),
                )
            )
    return cards


def load_tower_cards(path: Path | str) -> list[Card]:
    data = json.loads(Path(path).read_text())
    return _expand_tower(data["cards"])


def load_raven_cards(path: Path | str) -> list[Card]:
    data = json.loads(Path(path).read_text())
    return _expand_raven(data["cards"])


def build_tower_deck(path: Path | str, rng: Rng) -> Deck:
    cards = load_tower_cards(path)
    rng.shuffle(cards)
    return Deck(draw_pile=cards)


def build_raven_deck(path: Path | str, rng: Rng) -> Deck:
    cards = load_raven_cards(path)
    rng.shuffle(cards)
    return Deck(draw_pile=cards)
