"""Auto-save/load for the live game state.

The full ``AppState`` snapshot (lobby + ``GameState`` + RNG internal state) is
serialised to a single JSON file in ``saves/``. On startup
:func:`server.server_state.build_app_state` calls :func:`load_game` to restore
an in-progress game; on shutdown (and after every intent) :func:`save_game`
atomically replaces the file via a ``.tmp`` → rename so a crash mid-write
never leaves a half-written save.

A corrupt or unreadable file is renamed to ``.bak`` and the server starts
fresh rather than refusing to boot.
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
