import { WsClient, wsUrl } from "./net/ws.js";
import { initialState } from "./state.js";
import type { BoardData, ClientState, GameSnapshot, LogEntry } from "./state.js";
import { renderLogin } from "./ui/login.js";
import { renderLobbyLayout } from "./ui/lobby.js";
import { renderGameLayout } from "./ui/game.js";
import { mountNotifications } from "./ui/notifications.js";

const root = document.getElementById("app")!;
const state: ClientState = initialState();
const ws = new WsClient({
  url: wsUrl("/ws"),
  onOpen: () => {
    state.connected = true;
    if (state.you) {
      ws.send("join", { username: state.you }).catch((e) => {
        state.lastError = e?.message ?? String(e);
        renderCurrent();
      });
    } else {
      renderCurrent();
    }
  },
  onClose: () => {
    state.connected = false;
    renderCurrent();
  },
});

type ViewName = "login" | "lobby" | "game";
let currentView: ViewName | null = null;
let lobbyHandle: { update: () => void } | null = null;
let gameHandle: { update: () => void } | null = null;

function desiredView(): ViewName {
  if (!state.you) return "login";
  if (state.game) return "game";
  return "lobby";
}

function renderCurrent(): void {
  const want = desiredView();
  if (want !== currentView) {
    currentView = want;
    lobbyHandle = null;
    gameHandle = null;
    if (want === "login") {
      renderLogin(root, ws, (username) => {
        state.you = username;
        renderCurrent();
      });
      return;
    }
    if (want === "lobby") {
      lobbyHandle = renderLobbyLayout(root, ws, state);
    } else {
      gameHandle = renderGameLayout(root, ws, state);
    }
  }
  lobbyHandle?.update();
  gameHandle?.update();
}

// ---------------- Board fetch (once) ----------------
let boardFetchInFlight = false;
function ensureBoard(): void {
  if (state.board || boardFetchInFlight) return;
  boardFetchInFlight = true;
  fetch("/api/board")
    .then((r) => r.json())
    .then((data: BoardData) => {
      state.board = data;
      renderCurrent();
    })
    .catch((e) => {
      state.lastError = `Failed to load board: ${e?.message ?? e}`;
      renderCurrent();
    })
    .finally(() => {
      boardFetchInFlight = false;
    });
}

// ---------------- WS event routing ----------------
ws.on("__snapshot__", (snap: any) => {
  if (snap.you) state.you = snap.you;
  if (snap.lobby) state.lobby = snap.lobby;
  if (snap.stats) state.stats = snap.stats;
  if (snap.game) {
    state.game = snap.game as GameSnapshot;
    ensureBoard();
  } else {
    state.game = null;
  }
  renderCurrent();
});
ws.on("lobby_updated", (p: any) => {
  if (p.lobby) state.lobby = p.lobby;
  renderCurrent();
});
ws.on("chat", (p: any) => {
  state.chat.push({ from: p.from, text: p.text });
  renderCurrent();
});
ws.on("game_reset", () => {
  state.game = null;
  state.log = [];
  renderCurrent();
});
ws.on("__error__", (e: any) => {
  state.lastError = e?.message ?? "error";
  renderCurrent();
});

// Append every non-chat/non-lobby game event to the log (and trigger a rerender).
const UI_ONLY_EVENTS = new Set(["lobby_updated", "chat", "game_reset"]);
const originalEmit = (ws as any).emit.bind(ws);
(ws as any).emit = function (event: string, payload: any) {
  if (!UI_ONLY_EVENTS.has(event) && !event.startsWith("__")) {
    const entry: LogEntry = { kind: event, payload: payload ?? {}, ts: Date.now() };
    state.log.push(entry);
    if (state.log.length > 500) state.log.splice(0, state.log.length - 500);
    // Defer the render so listeners run first (they might update state.game).
    queueMicrotask(renderCurrent);
  }
  return originalEmit(event, payload);
};

// Mount the global notifications overlay (modals + toasts). It self-updates
// on ws snapshots/events; ``onChange`` lets it nudge a re-render of the rest
// of the UI when (e.g.) a modal opens after a snapshot already arrived.
mountNotifications(ws, state, renderCurrent);

ws.connect();
renderCurrent();
