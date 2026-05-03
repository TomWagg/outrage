"""Auto-save/load for the live game state.

At this skeleton stage, the "state" is just the lobby and player list, stored as a
generic dict. Phase 2+ will attach the real GameState.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def load_game(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        path.rename(path.with_suffix(path.suffix + ".bak"))
        return None


def save_game(state: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)
