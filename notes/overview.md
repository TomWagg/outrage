# Outrage — Implementation Overview

A reference for making your own edits. Assumes fluency with Python and familiarity with HTML/CSS/JS/TypeScript.

---

## High-level architecture

```
Browser ──WS──► FastAPI /ws ──► rules.apply() ──► GameState
                   │                                   │
                   │  ◄── Snapshot (per-player redacted)│
                   │  ◄── Events (broadcast all)        │
                   │                                    │
              /api/board ◄── data/board.json ──────────►Board
              /api/stats ◄── saves/stats.json
```

The server holds **one active game** at a time (singleton `AppState`). Every client action arrives as a JSON `Intent` over WebSocket; the server calls the pure `rules.apply()` function, then fans out events + per-player snapshots to all connections. There is no polling; the UI is purely reactive.

---

## Backend

### `server/main.py` — entry point & WebSocket router

FastAPI app with a single `/ws` endpoint. On connection a `Connection` object is created. Incoming messages are validated as `Intent` (Pydantic) then dispatched:

- **Lobby intents** (`join`, `set_mode`, `chat`, `start_game`, `reset_lobby`) are handled inline in `main.py`.
- **Game intents** (everything in `_INTENTS`) are forwarded to `rules.apply()`.

All state mutations are protected by an `asyncio.Lock` on `AppState`. After every successful game intent:
1. `state.game = new_game` — replace the live state.
2. `state.persist()` — write to `saves/current_game.json`.
3. `_broadcast_events()` — send each event as `{"type":"event","name":...}` to all clients.
4. `_broadcast_game_snapshots()` — send each client their own redacted snapshot.

The `lifespan` context manager loads `saves/current_game.json` on startup and saves on shutdown.

**HTTP routes:** `GET /api/board` returns `data/board.json` raw; `GET /api/stats` returns the stats store; `/` serves the Vite-built `web/dist/index.html`.

---

### `server/server_state.py` — singleton app state

`AppState` is a plain `dataclass` (not Pydantic) holding:

| Field | Type | Purpose |
|---|---|---|
| `lobby` | `Lobby` | Connected players + game mode, pre-game |
| `game` | `Optional[GameState]` | `None` until `start_game` is sent |
| `rng` | `Optional[Rng]` | Shared RNG instance |
| `board` | `Optional[Board]` | Loaded once at startup, immutable |
| `connections` | `dict[str, Connection]` | Username → live socket |
| `lock` | `asyncio.Lock` | Serialises all state mutations |
| `stats` | `StatsStore` | Lifetime stats, persisted separately |

`build_app_state()` does the cold-start load. `new_game_from_lobby()` constructs a fresh `GameState` from the lobby roster (but does not deal hands — that's `rules.apply(_, "start_game", _)`). The RNG state is serialised to JSON so a game survives a server restart exactly.

`_GLOBAL_RNG` is a module-level ref that lets `rules.py` access the RNG from auto-triggered code paths (e.g. raven card resolution) without passing it through every call.

---

### `server/game/state.py` — the data model

Everything the engine needs is in a single `GameState` Pydantic model. Key sub-models:

| Model | Purpose |
|---|---|
| `PlayerState` | Per-player: hand, position, status, accreditation, jewels, coin |
| `TurnContext` | Per-turn scratch-pad: roll, consecutive doubles, pending move/raven/jewel/split, visited spaces, extra turns queued. Reset at end of every turn via `state.turn = TurnContext()` |
| `PendingMove` | Live move decision: `destinations` dict `(space_id → path)`, split-7 deferred leg info. Cleared by `_commit_move` |
| `PendingRavenEffect` | Raven card waiting for player input — effect key, params, who drew it |
| `PendingJewelAttempt` | Queued jewel roll — which jewel, which space |
| `PendingSplitSeven` | Which roll triggered the split and why (`seven` or `binary_disruption`) |
| `Combat` | Full combat sub-state: attacker/defender, selected cards, phase, winner |
| `RavenNotice` | Public raven banner for all players. Survives turn resets; any player can dismiss it |
| `Warder` | Each warder: `id` + current `location` (barracks or a post space) |

`Phase` and `Status` are both `str` enums so they serialise transparently to JSON.

> **Important:** `rules.apply()` does **not** deep-copy state. Partial mutations persist if an exception is thrown mid-handler. The rule handlers are designed to validate first and mutate only on success.

---

### `server/game/rules.py` — the rule engine

The one public function:

```python
rules.apply(state, intent_name, payload, *, board, rng)
    → (new_state, events)   # or raises RuleError
```

`state` is mutated in place and returned as `new_state` (same object). `events` is a list of plain dicts `{"kind": ..., "payload": {...}}`.

#### `_INTENTS` dispatch table

| Intent | Handler | Valid phase(s) |
|---|---|---|
| `start_game` | `_intent_start_game` | `LOBBY` |
| `roll_dice` | `_intent_roll_dice` | `TURN_START`, `PRE_ROLL`, `ACCREDITATION_ATTEMPT` |
| `play_card_pre_roll` | `_intent_play_card_pre_roll` | Same three |
| `choose_move_path` | `_intent_choose_move_path` | `CHOOSING_PATH` |
| `assign_split_seven` | `_intent_assign_split_seven` | `SPLIT_SEVEN_ASSIGN` |
| `initiate_combat` | `_intent_initiate_combat` | any (checked manually) |
| `select_combat_cards` | `_intent_select_combat_cards` | `COMBAT` |
| `play_combat_special` | `_intent_play_combat_special` | `COMBAT` |
| `reveal_combat` | `_intent_reveal_combat` | `COMBAT` |
| `attempt_jewel` | `_intent_attempt_jewel` | `JEWEL_ATTEMPT` |
| `resolve_raven_effect` | `_intent_resolve_raven_effect` | `RAVEN_EFFECT` |
| `dismiss_raven_notice` | `_intent_dismiss_raven_notice` | any |
| `end_turn` | `_intent_end_turn` | `TURN_END`, `PRE_ROLL`, `ACCREDITATION_ATTEMPT`, `TURN_START` |

#### Movement flow

`_intent_roll_dice` → `_enter_movement_phase` → `_commit_move` → `_resolve_landing`

1. Roll dice. Check imprisonment/miss/Bloody Tower (3 consecutive doubles).
2. `_enter_movement_phase` calls `compute_destinations`. If there is exactly one reachable space and no pass-through combat choices, auto-commits; otherwise enters `CHOOSING_PATH`.
3. `_commit_move` updates `player.position`, records visited spaces, emits `player_moved`, calls `_resolve_landing`.
4. `_resolve_landing` fires all landing effects in order: loose jewels → Devereux coin → jewel attempt → Queen's House → raven trigger → tower card draw → space action → escape check → firecrackers escape → combat availability. Sets `state.phase` accordingly.

#### Space actions

Driven by the `action.key` field on a `SpaceData`. Handled in `_dispatch_space_action`:

| Key | Effect |
|---|---|
| `extra_turn` | Increment `extra_turns_queued` |
| `go_back_by_roll` | Teleport backward along wall walk by the dice total; call `_resolve_landing` recursively |
| `go_to_and_accredit` | Send player to Queen's House and grant accreditation |
| `surrender_weapons` | Discard all weapon cards from hand |

To add a new space action: add a branch in `_dispatch_space_action` and set `"action": {"key": "my_key", "params": {...}}` on the relevant space in `board.json`.

#### Adding a new intent

1. Write `def _intent_my_thing(state, payload, *, board, rng): ...`
2. Add it to `_INTENTS`.
3. Add the corresponding button/flow in `web/src/ui/controls.ts`.

---

### `server/game/cards_effects.py` — card effect registry

Every tower and raven card effect is a function registered under a string key:

```python
@register("my_effect")
def _my_effect(state, player, params, *, board, rng, **kw):
    # mutate state, return it
    return state, [_event("my_event", player=player.username)]
```

`dispatch(key, state, player, params, *, board, rng)` looks up and calls the handler. Raise `EffectError` to refuse the play (the rule engine converts this into a `RuleError`).

> **Footgun:** `_event(kind, **payload)` — do **not** pass `kind=...` as a keyword; it is the positional first parameter. This caused a real bug (the split-7 freeze) when `kind=` was accidentally used in `**payload`.

`_send_to(state, player, space_id, board)` is the canonical teleport helper. It moves the player and emits a `player_moved` event with `move_kind="teleport"`. It does **not** call `_resolve_landing`; callers that want landing effects must do so explicitly.

**Interactive effects** (those needing player input mid-raven-draw) return `[_event("raven_needs_input", kind="...")]` and leave `state.turn.pending_raven` set. The engine parks in `RAVEN_EFFECT`. After the player responds, `_intent_resolve_raven_effect` merges the player-supplied params and calls `dispatch` again.

---

### `server/game/board.py` + `board_schema.py` — the board graph

`Board.from_file(path)` parses `board.json` via Pydantic (`BoardData`) then builds:
- `_by_id: dict[str, SpaceData]` — fast lookup.
- `_neighbors: dict[str, set[str]]` — adjacency including slides.
- `_walk_order / _walk_index` — wall-walk sequence for forward-only movement.

#### Key methods

| Method | Purpose |
|---|---|
| `board.space(id)` | Look up a `SpaceData`; raises `KeyError` if missing |
| `board.neighbors(id)` | Return `set[str]` of adjacent space ids |
| `board.reachable(from, steps, ...)` | DFS for all simple paths of exactly `steps`; returns `{dest: path}` |
| `board.path_between(src, dst, blocked)` | BFS shortest path (used by teleport effects) |
| `board.reachable_within(from, max_steps)` | BFS up to `max_steps` (used for Lasso targeting) |

#### `SpaceData` fields

| Field | Notes |
|---|---|
| `id` | Canonical identifier, e.g. `ww21_miss`, `iw_05_03` |
| `kind` | One of the `SpaceKind` literals; drives engine logic |
| `region` | `wall_walk`, `inner_ward`, `white_tower`, `exterior_south`, `special` |
| `neighbors` | Explicit list of adjacent space ids |
| `wall_walk_order` | Integer position on the wall walk (0 = start) |
| `action` | Optional `SpaceAction(key, params)` for landed-on effects |
| `coords` / `coords_region` | Pixel coordinates for the renderer |
| `label` | Human-readable name; also shown in tooltips and the renderer |

`board_schema.py` uses `extra="ignore"` throughout, so you can freely add `_note`, `_shape`, `_section` etc. to `board.json` without breaking validation.

---

### `server/game/movement.py` — movement engine

`compute_destinations(board, from_space, steps, player, ...)` returns a `MoveOptions`:
- `destinations: dict[str, list[str]]` — destination → full path.
- `forced_single: bool` — True if the engine should auto-commit without asking the player.
- `intermediate_enemies` — enemies on any path (for pass-through combat stop choices).

Forward-only movement (pre-accreditation wall walk) is detected from `from_space.region == "wall_walk" and not player.accredited` and delegates to `board.reachable(forward_only=True)`. Accredited players use free graph DFS.

---

### `server/net/` — networking

| File | Purpose |
|---|---|
| `messages.py` | Pydantic models for `Intent`, `Ack`, `ErrorMsg`, `Event`, `Snapshot` |
| `connection.py` | `Connection` wraps a `WebSocket` and stores `username` |
| `broadcast.py` | `broadcast(conns, msg)` and `send_to(conn, msg)` — async fire-and-forget |
| `redact.py` | `redact_game_for_player(game, username)` — strips opponents' hands, hides unresolved combat cards, replaces deck lists with counts, trims `pending_move.destinations` for non-acting players |

---

### `server/persistence.py` + `server/stats.py`

`save_game` / `load_game` write/read `saves/current_game.json` (plain JSON, atomic write via temp file). `StatsStore` is a Pydantic model; `load_stats` / `save_stats` operate on `saves/stats.json`. Stats are updated by the rules engine when game-over events fire.

---

### `data/board.json`

Large JSON file. Top-level keys:

| Key | Type | Purpose |
|---|---|---|
| `spaces` | array | All `SpaceData` objects (plus `_section` sentinels the loader strips) |
| `slides` | array | `{from_space, to_space, bidirectional}` — extra graph edges |
| `warder_posts` | array | `{id, space_id, blocks_space_ids}` — post definitions |
| `initial_warders` | array | Starting warder locations |
| `initial_jewel_locations` | dict | `{jewel_id: space_id}` |
| `start_space` etc. | string | Named anchor spaces referenced by the engine |
| `rules` | dict | `BoardRules` flags, e.g. `white_tower_forward_only` |
| `display_regions` | array | Non-playable labelled rectangles drawn by the renderer |
| `metallicity_destination_ids` | array | Space ids that Metallicity can scatter jewels to |
| `bench_space_ids` | array | Space ids for the Bench/Rest raven effect |

### `data/tower_cards.json` and `data/raven_cards.json`

Arrays of card objects:

| Field | Notes |
|---|---|
| `id` | UUID — stable identity everywhere |
| `name` | Display name |
| `kind` | `"tower"` or `"raven"` |
| `category` | `"weapon"` \| `"burglary"` \| `"utility"` \| `"special"` |
| `value` | Integer; burglary tools add this to the jewel-roll threshold reduction |
| `effect_key` | Matches a `@register(...)` handler in `cards_effects.py`, or `null` |
| `params` | Base params merged with player-supplied params on play |
| `defender_only` | If true, only playable in combat by the defender |

---

## Frontend

### `web/src/main.ts` — top-level orchestration

Owns the `ClientState` object and the `WsClient`. Three views: `login`, `lobby`, `game`. `desiredView()` picks which to render based on `state.you` and `state.game`. When the view changes, the old DOM is torn down and a new one built; the returned `handle.update()` is called on every subsequent state change.

WS events routed here:

| Event | Action |
|---|---|
| `__snapshot__` | Replaces `state.lobby`, `state.game`, `state.stats` |
| `lobby_updated` | Replaces `state.lobby` |
| `chat` | Appends to `state.chat` |
| `game_reset` | Clears `state.game` |
| anything else | Appended to `state.log` |

`ws.emit` is monkey-patched to intercept all events and write them to `state.log` before listeners run.

`ensureBoard()` fetches `/api/board` once and caches it in `state.board`.

---

### `web/src/state.ts` — TypeScript types

Mirrors the server Pydantic models. Key interfaces:

| Interface | Mirror of |
|---|---|
| `GameSnapshot` | Redacted output of `redact_game_for_player` |
| `ClientState` | Top-level client state: connected, you, lobby, game, board, log, chat, stats |
| `BoardData` / `BoardSpace` | Matches Pydantic `BoardData` / `SpaceData` |
| `TurnContext`, `PendingMove`, `Combat`, `RavenNotice` | Mirror sub-models from `state.py` |

Helper functions: `currentTurnUsername(g)`, `playerByName(g, name)`.

---

### `web/src/net/ws.ts` — WsClient

Auto-reconnecting WebSocket wrapper.

- `send(name, payload)` returns a `Promise<void>` resolved by the server's `ack`. Uses a `request_id` (`r1`, `r2`, …) to correlate acks. 10 s timeout on unresolved requests.
- `on(event, fn)` registers listeners; `emit` dispatches to them.
- Snapshot messages (`type: "snapshot"`) are re-emitted as `__snapshot__`; errors as `__error__`.

---

### `web/src/ui/game.ts` — game layout

Builds the two-column DOM (`board-wrap` + `side`), then instantiates sub-panels (controls, hand, log, combat modal). Owns chat wiring and lifetime stats fetch. `update()` calls each panel's own update function. The board is re-rendered from scratch on every `update()` call — `renderBoard` wipes and rebuilds the SVG each time.

---

### `web/src/ui/controls.ts` — turn controls

`updateControls` reads `g.phase` and renders appropriate buttons into `#controls-row`. Key cases:

| Phase | Buttons shown |
|---|---|
| `TURN_START` / `PRE_ROLL` | Roll dice, End turn, pre-roll card buttons |
| `CHOOSING_PATH` | One button per destination (board squares also highlighted) |
| `JEWEL_ATTEMPT` | Attempt with all tools / without tools |
| `SPLIT_SEVEN_ASSIGN` | Dropdowns for n_self / target / leg order |
| `RAVEN_EFFECT` | `renderRavenEffect` handles each interactive `effect_key` individually |
| `COMBAT` | Deferred to `combat.ts`; controls panel just shows a status line |
| `TURN_END` | End turn |

`renderPreRollCardButtons` checks `PRE_ROLL_PLAYABLE_EFFECTS` and renders a sub-section per playable card. Lasso does a client-side BFS to filter obviously out-of-range targets.

**To add a new pre-roll-playable card:** add its `effect_key` to `PRE_ROLL_PLAYABLE_EFFECTS` and add a `case` in `renderPreRollCardButtons`.

---

### `web/src/board/render.ts` — SVG renderer

Called by `game.ts` on every update; wipes `container.innerHTML` and builds a fresh SVG. Layers in z-order:

1. **`regions`** — display-only labelled rectangles from `board.display_regions`.
2. **`spaces`** — one `<rect>` per space, coloured by `KIND_FILL[kind] ?? REGION_FILL[region]`. CSS variables applied via `el.style.fill` (not `setAttribute`) to allow CSS var resolution in SVG.
3. **Inner-ward safe circles** — centred `<circle>` on non-raven inner-ward spaces. Absence of a circle signals a raven trigger.
4. **Effect dots** — small `r=3` dot in the top-right corner of any space with a `SPACE_KIND_DESC` entry or a labelled `normal`-kind wall-walk space.
5. **`jewels`** — gold ring + glyph at each unclaimed jewel position.
6. **`pieces`** — coloured player pieces with rAF-tweened move animations (`lastPieceCoords` persists across renders).

#### Tooltips

`SPACE_KIND_DESC` maps every named `kind` to a description string. Labelled `kind: "normal"` wall-walk spaces use their label directly. A shared `div.board-tooltip` is positioned near the cursor via `mousemove` event delegation on the SVG.

#### Theming

To retheme the board, edit CSS custom properties in `:root` in `main.css` — `--sq-raven`, `--sq-white-tower`, `--sq-chapel-royal`, `--sq-chapel-st-john`, etc. No TypeScript changes needed.

To change a space's appearance, either change its `kind` in `board.json` (picks up an existing `KIND_FILL` entry) or add a new entry to `KIND_FILL` + a matching `--sq-*` CSS variable.

---

### `web/src/ui/notifications.ts`

Mounts a fixed overlay (`div.notifications-overlay`) once. Two notification types:

- **Tower modal** — shown only to the drawer on `tower_card_drawn`. Dismissible locally. Queued by card id.
- **Raven modal** — shown to all players when `state.game.active_raven_notice` is set. Any player can dismiss via `dismiss_raven_notice`, which clears it server-side and propagates via the next snapshot.
- **Toasts** — 20+ event kinds mapped to messages in `EVENT_TOASTS`. Auto-expire after 4.5–7 s.

---

### `web/src/ui/card_descriptions.ts`

Static copy for the notification system. `TOWER_CARDS` maps `card.name` to `{title, description}`. `ravenCardCopy(effectKey, params)` returns human-readable copy for every raven effect key, with location/jewel/post substitution and an April-21 Queen's Birthday branch.

---

## Quick-reference: where to make common edits

| I want to… | Edit here |
|---|---|
| Change a space's colour | CSS `--sq-*` variable in `web/src/styles/main.css` |
| Add a new space kind | `SpaceKind` Literal in `board_schema.py`; `KIND_FILL` entry + CSS var in `render.ts`; handle in `_resolve_landing` or `_dispatch_space_action` in `rules.py` |
| Add a new landing effect to an existing space | Set `"action": {"key": "...", "params": {...}}` in `board.json`; add branch in `_dispatch_space_action` |
| Add a new tower/raven card effect | `@register("my_key")` in `cards_effects.py`; copy in `card_descriptions.ts`; UI in `controls.ts` if it needs player input |
| Add a new pre-roll-playable card | Add `effect_key` to `PRE_ROLL_PLAYABLE_EFFECTS`; add `case` in `renderPreRollCardButtons` in `controls.ts` |
| Add a new game intent | Write handler, add to `_INTENTS` in `rules.py`; add button/flow in `controls.ts` |
| Change board topology | Edit `neighbors` arrays and/or `slides` in `board.json` |
| Change starting positions | Edit `initial_jewel_locations` / `initial_warders` / `start_space` in `board.json` |
| Add a new CSS theme variable | Add to `:root` in `main.css`; reference via `var(--name)` in `render.ts` or wherever needed |
| Add a new tooltip description | Add entry to `SPACE_KIND_DESC` in `render.ts` |
