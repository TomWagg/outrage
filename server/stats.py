"""Persistent per-username stats."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PlayerStats(BaseModel):
    username: str
    games_played: int = 0
    wins: int = 0
    jewels_stolen: int = 0
    coins_stolen: int = 0
    combat_wins: int = 0
    combat_losses: int = 0
    racked_count: int = 0
    imprisoned_count: int = 0
    tower_cards_gained: int = 0
    raven_cards_triggered: int = 0
    doubles_rolled: int = 0
    total_dice_rolls: int = 0


class StatsStore(BaseModel):
    by_username: dict[str, PlayerStats] = Field(default_factory=dict)

    def get(self, username: str) -> PlayerStats:
        if username not in self.by_username:
            self.by_username[username] = PlayerStats(username=username)
        return self.by_username[username]

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


def load_stats(path: Path) -> StatsStore:
    if not path.exists():
        return StatsStore()
    try:
        data = json.loads(path.read_text())
        return StatsStore.model_validate(data)
    except Exception:
        # Corrupt file: start fresh but back up the old one.
        path.rename(path.with_suffix(path.suffix + ".bak"))
        return StatsStore()


def save_stats(store: StatsStore, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.model_dump(), indent=2))
