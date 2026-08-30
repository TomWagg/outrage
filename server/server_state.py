"""Singleton app state: the one active game + connected sessions.

Holds the lobby (pre-game roster), the live :class:`GameState` once a game
has been started, the loaded :class:`Board`, and a seeded :class:`Rng`. Also
responsible for JSON auto-save/load of both lobby and game to ``saves/``.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from .game.board import Board
from .game.rng import Rng
from .game.rules import _GLOBAL_RNG
from .game.state import GameState
from .net.connection import Connection
from .persistence import load_game, save_game
from .stats import StatsStore, load_stats, save_stats

log = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parent.parent
SAVES_DIR = ROOT / "saves"
DATA_DIR = ROOT / "data"
GAME_FILE = SAVES_DIR / "current_game.json"
STATS_FILE = SAVES_DIR / "stats.json"
BOARD_FILE = DATA_DIR / "board.json"
TOWER_CARDS_FILE = DATA_DIR / "tower_cards.json"
RAVEN_CARDS_FILE = DATA_DIR / "raven_cards.json"


PLAYER_COLORS = ["#e74c3c", "#3498db", "#0f571f", "#e9e8e2", "#121213", "#ebe71c"]


class LobbyPlayer(BaseModel):
    username: str
    color: str
    connected: bool = True


class Lobby(BaseModel):
    """Pre-game lobby state. Persisted until a game starts."""

    players: list[LobbyPlayer] = Field(default_factory=list)
    mode: str = "fast"  # "fast" or "slow"
    started: bool = False

    def get(self, username: str) -> Optional[LobbyPlayer]:
        for p in self.players:
            if p.username == username:
                return p
        return None

    def next_color(self) -> str:
        used = {p.color for p in self.players}
        for c in PLAYER_COLORS:
            if c not in used:
                return c
        return PLAYER_COLORS[0]


@dataclass
class AppState:
    lobby: Lobby = field(default_factory=Lobby)
    stats: StatsStore = field(default_factory=StatsStore)
    connections: dict[str, Connection] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    board: Optional[Board] = None
    game: Optional[GameState] = None
    rng: Optional[Rng] = None

    # -------- persistence --------

    def snapshot_for_persistence(self) -> dict:
        out: dict = {"lobby": self.lobby.model_dump()}
        if self.game is not None:
            out["game"] = self.game.model_dump()
            if self.rng is not None:
                # random.Random.getstate returns a tuple; JSON-ise as nested list.
                out["rng"] = {
                    "seed": self.rng.seed,
                    "state": _rngstate_to_json(self.rng.getstate()),
                }
        return out

    def persist(self) -> None:
        save_game(self.snapshot_for_persistence(), GAME_FILE)
        save_stats(self.stats, STATS_FILE)

    # -------- game lifecycle --------

    def install_game(self, game: GameState, rng: Rng) -> None:
        self.game = game
        self.rng = rng
        _GLOBAL_RNG.set(rng)
        self.lobby.started = True

    def clear_game(self) -> None:
        self.game = None
        self.rng = None
        self.lobby.started = False


# ---------- loading a save written by an older build -----------------------


def _prune_unknown_fields(data: Any, error: ValidationError) -> tuple[Any, list[str]]:
    """Delete exactly the fields a validation error called ``extra_forbidden``.

    Every model in :mod:`server.game.state` forbids extra fields, which is what
    keeps a typo'd key from being silently accepted. The cost is that *removing*
    a field breaks every save that still carries it: the live game refuses to
    load and is thrown away on the next restart.

    So a save that fails is given one second chance with the offending keys
    dropped. This only ever runs after a normal load has already failed, and it
    only touches the exact paths Pydantic named, so a genuinely corrupt file
    still fails — it does not paper over a save that is wrong in some other way.

    Returns the pruned data and the dotted paths that were removed.
    """
    removed: list[str] = []
    for err in error.errors():
        if err.get("type") != "extra_forbidden":
            continue
        loc = err.get("loc") or ()
        if not loc:
            continue
        node = data
        try:
            for key in loc[:-1]:
                node = node[key]
            del node[loc[-1]]
        except (KeyError, IndexError, TypeError):
            continue
        removed.append(".".join(str(part) for part in loc))
    return data, removed


def _load_saved_game(raw: dict) -> GameState:
    """Validate a saved game, retrying once without fields the schema has dropped."""
    try:
        return GameState.model_validate(raw)
    except ValidationError as first:
        pruned, removed = _prune_unknown_fields(raw, first)
        if not removed:
            raise
        game = GameState.model_validate(pruned)
        log.warning(
            "Saved game carried %d field(s) this build no longer knows about; "
            "dropped and loaded anyway: %s",
            len(removed), ", ".join(removed),
        )
        return game


# ---------- persistence helpers for RNG state ------------------------------


def _rngstate_to_json(state: tuple) -> list:
    """Serialise ``random.Random.getstate()`` into a JSON-safe list."""
    # state is (version, internalstate_tuple, gauss_next)
    version, internal, gauss_next = state
    return [version, list(internal), gauss_next]


def _rngstate_from_json(data) -> tuple:
    version, internal, gauss_next = data
    return (version, tuple(internal), gauss_next)


# ---------- build / load ---------------------------------------------------


def build_app_state() -> AppState:
    state = AppState()
    state.stats = load_stats(STATS_FILE)
    # Load the board once at startup — it's immutable for the lifetime of the
    # process.
    state.board = Board.from_file(BOARD_FILE)

    saved = load_game(GAME_FILE)
    if saved:
        if "lobby" in saved:
            try:
                state.lobby = Lobby.model_validate(saved["lobby"])
                for p in state.lobby.players:
                    p.connected = False  # everyone is disconnected on cold start
            except Exception:
                log.exception("Corrupt lobby state; starting fresh")
                state.lobby = Lobby()
        if "game" in saved and saved["game"]:
            try:
                state.game = _load_saved_game(saved["game"])
                rng_info = saved.get("rng") or {}
                seed = int(rng_info.get("seed", 0))
                rng = Rng(seed=seed)
                if "state" in rng_info:
                    rng.setstate(_rngstate_from_json(rng_info["state"]))
                state.rng = rng
                _GLOBAL_RNG.set(rng)
                # Make sure lobby.started reflects reality.
                state.lobby.started = True
                # Mark game players disconnected on cold start.
                for p in state.game.players:
                    p.connected = False
            except Exception:
                log.exception("Corrupt game state; discarding saved game")
                state.game = None
                state.rng = None
                state.lobby.started = False
    return state


# ---------- singleton -------------------------------------------------------


APP: Optional[AppState] = None


def get_app() -> AppState:
    assert APP is not None, "App state not initialised"
    return APP


def set_app(state: AppState) -> None:
    global APP
    APP = state


# ---------- game construction ---------------------------------------------


def new_game_from_lobby(state: AppState, seed: Optional[int] = None) -> tuple[GameState, Rng]:
    """Build a fresh :class:`GameState` from ``state.lobby``.

    Caller is expected to then dispatch the ``start_game`` rule intent to deal
    hands + choose a turn order. The returned GameState has the players,
    decks, jewels, warders, and mode populated; everything else is default.
    """
    from .game.cards import load_tower_cards, load_raven_cards
    from .game.state import PlayerState

    assert state.board is not None
    board = state.board

    if seed is None:
        seed = random.randint(1, 2**31 - 1)
    rng = Rng(seed=seed)

    tower = load_tower_cards(TOWER_CARDS_FILE)
    raven = load_raven_cards(RAVEN_CARDS_FILE)
    rng.shuffle(tower)
    rng.shuffle(raven)

    players = [
        PlayerState(username=lp.username, color=lp.color, position=board.data.start_space, connected=lp.connected)
        for lp in state.lobby.players
    ]
    game = GameState(
        mode=state.lobby.mode,
        players=players,
        turn_order=[],  # rules.start_game will shuffle if empty
        tower_draw=tower,
        raven_draw=raven,
        seed=seed,
    )
    return game, rng
