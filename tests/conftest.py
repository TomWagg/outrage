"""Test-wide safety net: no test may write to the real ``saves/`` directory.

``AppState.persist`` resolves ``GAME_FILE`` and ``STATS_FILE`` from
:mod:`server.server_state` at call time, so anything that builds an AppState —
including a ``TestClient`` lifespan, which persists on shutdown — writes to
whatever those names point at. A per-test monkeypatch covers the happy path,
but it unwinds at teardown, and a lifespan shutdown that runs after that (or a
test interrupted part-way) lands on the developer's live game instead.

Redirecting them once, for the whole session, removes the failure mode rather
than relying on every fixture to remember.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import server.server_state as server_state


@pytest.fixture(autouse=True, scope="session")
def _never_touch_real_saves(tmp_path_factory) -> None:
    saves = tmp_path_factory.mktemp("saves")
    server_state.SAVES_DIR = saves
    server_state.GAME_FILE = saves / "current_game.json"
    server_state.STATS_FILE = saves / "stats.json"


@pytest.fixture(autouse=True)
def _assert_real_saves_untouched() -> None:
    """Fail loudly if a test wrote to the real saves anyway.

    The session fixture above should make this unreachable; it is here so that a
    future change which reintroduces the hazard is caught by the suite rather
    than by somebody losing a game in progress.
    """
    real = Path(__file__).resolve().parent.parent / "saves"
    before = {p.name: p.stat().st_mtime_ns for p in real.glob("*.json")} if real.exists() else {}
    yield
    after = {p.name: p.stat().st_mtime_ns for p in real.glob("*.json")} if real.exists() else {}
    touched = [n for n, m in after.items() if before.get(n) != m]
    assert not touched, f"test wrote to the real saves/: {touched}"
