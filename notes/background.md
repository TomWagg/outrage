# Background — frameworks and key patterns

A primer on the technologies used in the backend and how they show up in this codebase.

---

## FastAPI

[FastAPI](https://fastapi.tiangolo.com) is a Python web framework for building HTTP and WebSocket APIs. It is built on top of **Starlette** (the async web toolkit) and **Uvicorn** (the ASGI server). You run it with:

```bash
uvicorn server.main:app --reload
```

`server.main:app` tells uvicorn where to find the FastAPI application object (`app = FastAPI(...)` in `server/main.py`). `--reload` restarts the server automatically when you save a Python file.

### Routes

HTTP endpoints are defined with decorators:

```python
@app.get("/api/stats")
def get_stats() -> dict:
    return get_app().stats.model_dump()
```

The return type hint is optional but FastAPI uses it to auto-generate an OpenAPI schema (viewable at `/docs` while the server is running — useful for poking at the API).

### WebSockets

The game's entire real-time communication goes through a single WebSocket endpoint:

```python
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    ...
    while True:
        raw = await ws.receive_json()
        ...
```

A WebSocket is a persistent two-way connection. Unlike HTTP (one request → one response), the server and client can each send messages at any time. This is how the server pushes game state updates to all players immediately when something happens, rather than clients having to poll.

### Async / await

FastAPI is **async-first**. Functions marked `async def` are coroutines — they can suspend execution with `await` while waiting for I/O (like receiving a message), letting other connections run in the meantime. For this game it means the server can handle all connected players simultaneously on a single thread.

The `asyncio.Lock` on `AppState` ensures that only one player's intent is processed at a time — without it, two players submitting moves simultaneously could corrupt the shared game state.

### Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    state = build_app_state()   # runs on startup
    set_app(state)
    yield                        # server runs here
    state.persist()              # runs on shutdown
```

This is where the saved game is loaded on startup and flushed to disk on shutdown.

---

## Pydantic v2

[Pydantic](https://docs.pydantic.dev/latest/) is a data validation library. You declare a class that inherits from `BaseModel`, annotate its fields with types, and Pydantic automatically validates and coerces incoming data — rejecting bad input with a clear error rather than blowing up deep inside your own code.

### Defining a model

```python
from pydantic import BaseModel, Field

class PlayerState(BaseModel):
    username: str
    position: str
    has_coin: bool = False
    hand: list[Card] = Field(default_factory=list)
```

- Fields with defaults are optional on construction; fields without are required.
- `Field(default_factory=list)` is used for mutable defaults (you can't write `hand: list = []` because that would share one list across all instances — the same Python gotcha as in regular dataclasses).

### Validation

```python
player = PlayerState(username="alice", position="ww00_start")
# has_coin defaults to False, hand defaults to []
```

If you pass the wrong type — e.g. `has_coin="yes"` — Pydantic coerces it if it can (`"true"` → `True`) or raises a `ValidationError` if it cannot. This is how the server validates incoming WebSocket messages (`Intent.model_validate(raw)`).

### `model_dump()` and `model_validate()`

```python
data: dict = player.model_dump()          # → plain dict, ready for JSON
player2 = PlayerState.model_validate(data) # → reconstruct from dict
```

These two are used everywhere for persistence (save to JSON) and loading (restore from JSON).

### `ConfigDict(extra="forbid")`

Most models in this codebase use:

```python
class PlayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

This means passing an unknown field raises an error immediately, catching typos like `player = PlayerState(usernme="alice")` at construction time rather than silently ignoring it.

`board_schema.py` uses `extra="ignore"` instead so that editorial annotations (`_note`, `_section`, etc.) in `board.json` are silently dropped rather than rejected.

### `model_copy(update={...})`

Because Pydantic models are mutable (`state.phase = Phase.TURN_END` works fine), `model_copy` is mainly used when you want a partial update while preserving everything else:

```python
state.turn = state.turn.model_copy(update={
    "roll": [],
    "cards_played_this_turn": [],
    "consecutive_doubles": doubles,
})
```

This is how `_intent_end_turn` resets most of the `TurnContext` between turns while keeping `consecutive_doubles`.

---

## Key patterns in this game

### The intent pattern

Every player action is an **intent** — a `(name, payload)` pair sent over WebSocket. The server routes it through a single function:

```python
new_state, events = rules.apply(state, intent_name, payload, board=board, rng=rng)
```

`apply` looks up the handler in `_INTENTS`, runs it, and returns the updated state plus a list of events. Handlers raise `RuleError` for illegal moves (e.g. rolling out of turn); the server catches this and sends an error back to the client without touching the live state.

This pattern keeps game logic pure and testable — you can drive the entire game engine from a test without a running server:

```python
state, events = rules.apply(state, "roll_dice", {"username": "alice"}, board=board, rng=rng)
assert state.phase == Phase.CHOOSING_PATH
```

### The effect registry

Card effects use a decorator registry in `cards_effects.py`:

```python
@register("sanctuary")
def _sanctuary(state, player, params, *, board, rng, **kw):
    ...
    return state, [_event("player_moved", ...)]
```

`dispatch("sanctuary", state, player, params, board=board, rng=rng)` looks up and calls the right handler. Adding a new card effect means adding one decorated function and giving the card that `effect_key` in `tower_cards.json` or `raven_cards.json` — no wiring elsewhere in the engine.

### Events vs snapshots

The server sends two kinds of messages to clients after every intent:

- **Events** (broadcast to everyone): `{"type": "event", "name": "player_moved", "payload": {...}}`. These are logged in the event panel and drive toast notifications.
- **Snapshots** (per-player, individualised): `{"type": "snapshot", "state": {...}}`. This is the full current game state, redacted so each player only sees their own hand. The UI replaces its internal state wholesale on receipt.

The separation means the event log can record what happened ("Alice moved to Martin Tower") while the snapshot is always authoritative about the current state (you can reconnect mid-game and get a full snapshot to catch up).

### Redaction

`server/net/redact.py` is called just before each snapshot is sent. It replaces every opponent's `hand` with an empty list (adding `hand_size` so the UI can show card-count badges), hides combat card selections until both sides have committed, and removes deck contents (sending only counts). The acting player gets their `pending_move.destinations` in full; everyone else gets a summary.

---

## Running the game locally

```bash
# Terminal 1 — backend
source activate outrage
uvicorn server.main:app --reload

# Terminal 2 — frontend dev server (hot reload)
cd web
npm run dev
```

The Vite dev server proxies `/ws` and `/api/*` to the backend (configured in `web/vite.config.ts`), so you only need to open `http://localhost:5173`.

For production (or playing with friends over the internet), build the frontend first:

```bash
cd web && npm run build
```

Then `uvicorn server.main:app` alone serves everything from `web/dist/`.

### Running tests

```bash
source activate outrage
python -m pytest tests/ -q
```

Tests drive the rule engine directly — no server, no browser, no WebSocket. Each test builds a minimal `GameState`, applies intents, and asserts on the resulting state and events.
