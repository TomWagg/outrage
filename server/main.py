"""FastAPI entry point: WebSocket endpoint + static file serving.

Run locally:
    uvicorn server.main:app --reload --host 0.0.0.0 --port 8000

The `/ws` endpoint handles two broad categories of messages:

  Lobby intents      — ``join``, ``set_mode``, ``chat``, ``start_game``,
                        ``reset_lobby`` (end the current game)
  Game intents       — everything in :data:`server.game.rules._INTENTS`
                        (``roll_dice``, ``choose_move_path``, ``end_turn`` …)
                        forwarded to :func:`server.game.rules.apply`.

Every successful game intent:
  * is persisted via :meth:`AppState.persist`,
  * produces a fan-out of :class:`Event` messages (one per log event),
  * pushes a per-player redacted :class:`Snapshot` so the UI stays in sync.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .game import rules
from .net.broadcast import broadcast, send_to
from .net.connection import Connection
from .net.messages import Ack, ErrorMsg, Event, Intent, Snapshot
from .net.redact import redact_game_for_player
from .server_state import (
    AppState,
    LobbyPlayer,
    build_app_state,
    get_app,
    new_game_from_lobby,
    set_app,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("outrage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = build_app_state()
    set_app(state)
    log.info(
        "App state ready; %d lobby players, game=%s",
        len(state.lobby.players),
        "in-progress" if state.game is not None else "none",
    )
    yield
    state.persist()
    log.info("App state persisted on shutdown")


app = FastAPI(title="Outrage! server", lifespan=lifespan)

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


# ============================================================================
# Snapshot / broadcast helpers
# ============================================================================


def _snapshot_payload_for(state: AppState, username: str | None) -> dict:
    payload: dict = {
        "lobby": state.lobby.model_dump(),
        "you": username,
        "phase": state.game.phase.value if state.game else "LOBBY",
    }
    if username:
        payload["stats"] = state.stats.get(username).model_dump()
    if state.game is not None:
        payload["game"] = redact_game_for_player(state.game, username)
    return payload


async def _broadcast_lobby(state: AppState) -> None:
    msg = Event(name="lobby_updated", payload={"lobby": state.lobby.model_dump()})
    await broadcast(state.connections.values(), msg)


async def _broadcast_game_snapshots(state: AppState) -> None:
    """Send every connected client a fresh per-player snapshot."""
    for username, conn in list(state.connections.items()):
        await send_to(conn, Snapshot(state=_snapshot_payload_for(state, username)))


async def _broadcast_events(state: AppState, events: list[dict]) -> None:
    for ev in events:
        await broadcast(
            state.connections.values(),
            Event(name=ev["kind"], payload=ev.get("payload", {})),
        )


# ============================================================================
# Lobby handlers
# ============================================================================


async def _handle_join(state: AppState, conn: Connection, payload: dict, request_id: str | None) -> None:
    username = (payload.get("username") or "").strip()
    if not username:
        await send_to(conn, ErrorMsg(request_id=request_id, code="bad_username", message="Username required"))
        return
    if len(username) > 24 or not all(c.isalnum() or c in "-_ " for c in username):
        await send_to(
            conn,
            ErrorMsg(request_id=request_id, code="bad_username", message="Alphanumerics, space, -, _ only (max 24)"),
        )
        return

    # Kick any prior socket for this username.
    old = state.connections.get(username)
    if old is not None and old is not conn:
        await old.close()
        state.connections.pop(username, None)

    conn.username = username
    state.connections[username] = conn

    # If a game is already in progress, only let players who are already in
    # the game reconnect. Brand-new usernames are rejected.
    if state.game is not None:
        game_player = next((p for p in state.game.players if p.username == username), None)
        if game_player is None:
            await send_to(
                conn,
                ErrorMsg(
                    request_id=request_id,
                    code="game_in_progress",
                    message="A game is already in progress; can't join as a new player",
                ),
            )
            conn.username = None
            state.connections.pop(username, None)
            return
        game_player.connected = True
        lobby_p = state.lobby.get(username)
        if lobby_p is not None:
            lobby_p.connected = True
    else:
        # Lobby-only join.
        existing = state.lobby.get(username)
        if existing is None:
            color = state.lobby.next_color()
            state.lobby.players.append(LobbyPlayer(username=username, color=color, connected=True))
        else:
            existing.connected = True

    state.persist()
    await send_to(conn, Ack(request_id=request_id, detail="joined"))
    await send_to(conn, Snapshot(state=_snapshot_payload_for(state, username)))
    await _broadcast_lobby(state)
    if state.game is not None:
        # Also push a game snapshot to everyone so they see this player is back.
        await _broadcast_game_snapshots(state)


async def _handle_leave(state: AppState, conn: Connection) -> None:
    username = conn.username
    if not username:
        return
    state.connections.pop(username, None)
    if state.game is not None:
        game_player = next((p for p in state.game.players if p.username == username), None)
        if game_player is not None:
            game_player.connected = False
    lobby_p = state.lobby.get(username)
    if lobby_p is not None:
        if state.lobby.started:
            lobby_p.connected = False
        else:
            state.lobby.players = [p for p in state.lobby.players if p.username != username]
    state.persist()
    await _broadcast_lobby(state)
    if state.game is not None:
        await _broadcast_game_snapshots(state)


async def _handle_set_mode(state: AppState, conn: Connection, payload: dict, request_id: str | None) -> None:
    if state.lobby.started or state.game is not None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="game_started", message="Game already started"))
        return
    mode = payload.get("mode")
    if mode not in ("fast", "slow"):
        await send_to(conn, ErrorMsg(request_id=request_id, code="bad_mode", message="mode must be 'fast' or 'slow'"))
        return
    state.lobby.mode = mode
    state.persist()
    await send_to(conn, Ack(request_id=request_id))
    await _broadcast_lobby(state)


async def _handle_chat(state: AppState, conn: Connection, payload: dict) -> None:
    text = (payload.get("text") or "").strip()
    if not text or not conn.username:
        return
    text = text[:500]
    msg = Event(name="chat", payload={"from": conn.username, "text": text})
    await broadcast(state.connections.values(), msg)


async def _handle_start_game(state: AppState, conn: Connection, payload: dict, request_id: str | None) -> None:
    if state.game is not None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="already_started", message="Game already running"))
        return
    if len(state.lobby.players) < 2:
        await send_to(conn, ErrorMsg(request_id=request_id, code="need_players", message="Need at least 2 players"))
        return
    if conn.username is None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="not_joined", message="Join the lobby first"))
        return

    seed = payload.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            await send_to(conn, ErrorMsg(request_id=request_id, code="bad_seed", message="seed must be an integer"))
            return

    try:
        game, rng = new_game_from_lobby(state, seed=seed)
        state.install_game(game, rng)
        assert state.board is not None
        new_game, events = rules.apply(game, "start_game", {}, board=state.board, rng=rng)
        state.game = new_game
    except rules.RuleError as exc:
        state.clear_game()
        await send_to(conn, ErrorMsg(request_id=request_id, code="rule_error", message=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001
        state.clear_game()
        log.exception("start_game failed")
        await send_to(conn, ErrorMsg(request_id=request_id, code="internal", message=str(exc)))
        return

    state.persist()
    await send_to(conn, Ack(request_id=request_id, detail="game_started"))
    await _broadcast_events(state, events)
    await _broadcast_lobby(state)
    await _broadcast_game_snapshots(state)


async def _handle_reset_lobby(state: AppState, conn: Connection, payload: dict, request_id: str | None) -> None:
    """Forcibly end the current game and return to lobby (admin / debug)."""
    if state.game is None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="no_game", message="No game in progress"))
        return
    state.clear_game()
    # Restore lobby.connected flags from current connections.
    for p in state.lobby.players:
        p.connected = p.username in state.connections
    state.persist()
    await send_to(conn, Ack(request_id=request_id, detail="lobby_reset"))
    await _broadcast_lobby(state)
    await broadcast(state.connections.values(), Event(name="game_reset", payload={}))


# ============================================================================
# Game-intent dispatch (forwards to rules.apply)
# ============================================================================


async def _handle_game_intent(
    state: AppState,
    conn: Connection,
    intent_name: str,
    payload: dict,
    request_id: str | None,
) -> None:
    if state.game is None or state.board is None or state.rng is None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="no_game", message="No game in progress"))
        return
    if conn.username is None:
        await send_to(conn, ErrorMsg(request_id=request_id, code="not_joined", message="Join first"))
        return

    # Inject caller's username if not explicitly supplied — the rule engine
    # always checks "current_player" against payload["username"].
    pay = dict(payload)
    pay.setdefault("username", conn.username)

    # Authorisation: payload username must match the socket's username, unless
    # it's the target of an intent explicitly initiated by someone else
    # (currently none in _INTENTS — every intent is driven by the acting
    # player, including split-7 which is driven by the roller).
    if pay["username"] != conn.username:
        await send_to(
            conn,
            ErrorMsg(
                request_id=request_id,
                code="auth",
                message="You can only act as yourself",
            ),
        )
        return

    try:
        new_game, events = rules.apply(
            state.game,
            intent_name,
            pay,
            board=state.board,
            rng=state.rng,
        )
    except rules.RuleError as exc:
        await send_to(conn, ErrorMsg(request_id=request_id, code="rule_error", message=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("Engine error on %s", intent_name)
        await send_to(conn, ErrorMsg(request_id=request_id, code="internal", message=str(exc)))
        return

    state.game = new_game
    state.persist()
    await send_to(conn, Ack(request_id=request_id))
    await _broadcast_events(state, events)
    await _broadcast_game_snapshots(state)

    # Auto-wrap game-over: when the rule engine flips phase to GAME_OVER we
    # push a single final lobby update so clients can drop back to the lobby
    # if they want. We don't tear the game down automatically (so players can
    # review the final state); a ``reset_lobby`` intent will.


# ============================================================================
# Intent router
# ============================================================================


LOBBY_INTENTS = {
    "join": _handle_join,
    "set_mode": _handle_set_mode,
    "chat": _handle_chat,
    "start_game": _handle_start_game,
    "reset_lobby": _handle_reset_lobby,
}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    state = get_app()
    conn = Connection(ws)
    try:
        while True:
            raw = await ws.receive_json()
            try:
                intent = Intent.model_validate(raw)
            except ValidationError as ve:
                await send_to(conn, ErrorMsg(code="bad_intent", message=str(ve)))
                continue

            async with state.lock:
                name = intent.name
                if name == "chat":
                    await _handle_chat(state, conn, intent.payload)
                    continue
                lobby_handler = LOBBY_INTENTS.get(name)
                if lobby_handler is not None:
                    await lobby_handler(state, conn, intent.payload, intent.request_id)
                    continue
                if name in rules._INTENTS:
                    await _handle_game_intent(state, conn, name, intent.payload, intent.request_id)
                    continue
                await send_to(
                    conn,
                    ErrorMsg(
                        request_id=intent.request_id,
                        code="unknown_intent",
                        message=f"Unknown intent: {name}",
                    ),
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("ws error: %s", exc)
    finally:
        async with state.lock:
            await _handle_leave(state, conn)


# ============================================================================
# HTTP: stats + static file serving
# ============================================================================


@app.get("/api/board")
def get_board() -> dict:
    """Return the raw board JSON (used by the frontend renderer)."""
    import json as _json
    from .server_state import BOARD_FILE
    return _json.loads(BOARD_FILE.read_text())


@app.get("/api/stats")
def get_stats() -> dict:
    return get_app().stats.model_dump()


@app.get("/api/stats/{username}")
def get_stats_for(username: str) -> dict:
    return get_app().stats.get(username).model_dump()


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")
else:
    @app.get("/")
    def index_placeholder() -> dict:
        return {
            "status": "backend running",
            "note": "frontend not built — run `npm run dev` in web/ or `npm run build`",
        }
