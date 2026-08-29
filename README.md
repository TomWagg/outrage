# Outrage! — online

Online adaptation of the board game *Outrage! Steal the Crown Jewels*. Self-hosted:
run the server on your laptop, share the URL with friends, play live together.

The server is authoritative: clients send intents over a WebSocket, the rule
engine validates them, and every client gets a redacted snapshot back. One game
runs at a time, auto-saved to disk so the server can be restarted mid-game.

## Project layout

```
server/     FastAPI backend (WebSocket game server, Pydantic models, persistence)
web/        Vite + TypeScript frontend (vanilla TS, SVG board, WS client)
data/       Card and board data (tower_cards.json, raven_cards.json, board.json)
saves/      Auto-save of live game state + per-username stats (gitignored in practice)
tests/      Python unit tests for the rule engine
tools/      One-off board-authoring scripts
```

Run the tests with `python -m pytest tests/ -q` from the repo root.

## Development

### Prerequisites
- Python 3.11+
- Node 18+

### Backend (conda)

One-time setup:

```bash
conda create -n outrage python=3.12 -y
conda activate outrage
cd /Users/twagg/codes/outrage
pip install -e .
```

Run the server:

```bash
conda activate outrage
cd /Users/twagg/codes/outrage
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (separate terminal)

```bash
cd /Users/twagg/codes/outrage/web
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/ws` and `/api` to the backend at :8000.

### Production-ish (single port)

```bash
cd /Users/twagg/codes/outrage/web && npm run build
conda activate outrage
cd /Users/twagg/codes/outrage
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Now everything is served from :8000 — share your LAN/tunnel URL with friends.

## Data files

- `data/tower_cards.json` — tower deck (burglary / weapons / traversal / utility / custom)
- `data/raven_cards.json` — raven deck with effect_keys
- `data/board.json` — board graph: spaces, neighbours, slides, traversal edges,
  anchor spaces and the `rules` block

## Saves

- `saves/current_game.json` — live game; loaded on startup, kept up to date on every change
- `saves/stats.json` — per-username aggregates across games

Delete these files to reset.
