/**
 * Game view layout: board + side panels (controls, hand, opponents, decks, log, chat).
 *
 * Mounted by ``main.ts`` when ``state.game`` becomes non-null.
 */
import type { WsClient } from "../net/ws.js";
import type { ClientState } from "../state.js";
import { renderBoard } from "../board/render.js";
import { renderControlsPanel } from "./controls.js";
import { renderHandPanel } from "./hand.js";
import { renderLogPanel } from "./log.js";
import { renderCombatModal } from "./combat.js";

export function renderGameLayout(
  root: HTMLElement,
  ws: WsClient,
  state: ClientState,
): { update: () => void } {
  root.innerHTML = `
    <div class="status">
      <span><span class="dot" id="conn-dot"></span><span id="conn-text">connecting…</span></span>
      <span id="you-label"></span>
    </div>
    <div class="main">
      <div class="board-wrap" id="board-wrap"></div>
      <div class="side">
        <div id="controls-slot"></div>
        <div id="hand-slot"></div>
        <div id="opponents-slot" class="panel">
          <h3>Players</h3>
          <ul class="player-list" id="opponents-list"></ul>
        </div>
        <div id="decks-slot" class="panel">
          <h3>Decks &amp; jewels</h3>
          <div id="decks-info" style="font-size:0.85rem;color:var(--muted)"></div>
        </div>
        <div id="stats-slot" class="panel">
          <h3>Lifetime stats</h3>
          <div id="stats-info" style="font-size:0.8rem;color:var(--muted)">Loading…</div>
        </div>
        <div id="log-slot"></div>
        <div class="panel" id="chat-slot">
          <h3>Chat</h3>
          <div class="chat-log" id="chat-log"></div>
          <div class="chat-row">
            <input id="chat-input" placeholder="Say something…" maxlength="500" />
            <button id="chat-send">Send</button>
          </div>
        </div>
      </div>
    </div>
    <div id="combat-modal-slot"></div>
  `;

  const controls = renderControlsPanel(root.querySelector<HTMLElement>("#controls-slot")!);
  const hand = renderHandPanel(root.querySelector<HTMLElement>("#hand-slot")!);
  const logPanel = renderLogPanel(root.querySelector<HTMLElement>("#log-slot")!);
  const combat = renderCombatModal(root.querySelector<HTMLElement>("#combat-modal-slot")!);

  // Lifetime stats: fetched from ``/api/stats``. Re-fetch on game_over so
  // end-of-game bumps are visible without refreshing the page. Cached in a
  // closure so ``update()`` can re-render without re-fetching every frame.
  let statsCache: Record<string, LifetimeStats> | null = null;
  const refreshStats = async () => {
    try {
      const resp = await fetch("/api/stats");
      const json = await resp.json();
      statsCache = (json?.by_username ?? {}) as Record<string, LifetimeStats>;
      renderStats(root, state, statsCache);
    } catch {
      renderStats(root, state, null);
    }
  };
  void refreshStats();
  let lastSeenGameOver = false;

  // Chat wiring.
  const chatInput = root.querySelector<HTMLInputElement>("#chat-input")!;
  const chatSend = root.querySelector<HTMLButtonElement>("#chat-send")!;
  const sendChat = () => {
    const text = chatInput.value.trim();
    if (!text) return;
    ws.send("chat", { text }).catch(() => {});
    chatInput.value = "";
  };
  chatSend.addEventListener("click", sendChat);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChat();
  });

  return {
    update: () => {
      updateStatus(root, state);
      updateBoard(root, ws, state);
      controls.update(state, ws);
      hand.update(state);
      updateOpponents(root, state);
      updateDecks(root, state);
      logPanel.update(state);
      updateChat(root, state);
      combat.update(state, ws);
      renderStats(root, state, statsCache);
      // Refresh stats on fresh game_over transitions.
      const nowOver = state.game?.phase === "GAME_OVER";
      if (nowOver && !lastSeenGameOver) void refreshStats();
      lastSeenGameOver = nowOver;
    },
  };
}

interface LifetimeStats {
  username: string;
  games_played: number;
  wins_fast: number;
  wins_slow: number;
  jewels_stolen: number;
  coins_stolen: number;
  combat_wins: number;
  combat_losses: number;
  racked_count: number;
  imprisoned_count: number;
}

function renderStats(
  root: HTMLElement,
  state: ClientState,
  cache: Record<string, LifetimeStats> | null,
): void {
  const el = root.querySelector<HTMLElement>("#stats-info");
  if (!el) return;
  if (cache === null) { el.textContent = "(unable to load)"; return; }
  const g = state.game;
  // Scope to players in the current game; if the lobby hasn't started, show
  // the viewer's own row.
  const names = g ? g.players.map((p) => p.username) : state.you ? [state.you] : [];
  if (names.length === 0) { el.textContent = "—"; return; }
  const rows = names
    .map((n) => cache[n] ?? emptyStats(n))
    .sort((a, b) => (b.wins_fast + b.wins_slow) - (a.wins_fast + a.wins_slow));
  const tbl = [
    `<table style="width:100%;border-collapse:collapse">`,
    `<thead><tr style="color:var(--muted);text-align:left">` +
      `<th>Player</th><th>Games</th><th>Wins</th><th>💎</th><th>⚔ W/L</th><th>Rack</th><th>Prison</th>` +
      `</tr></thead>`,
    `<tbody>`,
    ...rows.map((s) => {
      const wins = s.wins_fast + s.wins_slow;
      const mine = s.username === state.you ? ` style="color:var(--accent)"` : "";
      return (
        `<tr${mine}>` +
        `<td>${escapeHtml(s.username)}</td>` +
        `<td>${s.games_played}</td>` +
        `<td>${wins}${wins ? ` (F${s.wins_fast}/S${s.wins_slow})` : ""}</td>` +
        `<td>${s.jewels_stolen}</td>` +
        `<td>${s.combat_wins}/${s.combat_losses}</td>` +
        `<td>${s.racked_count}</td>` +
        `<td>${s.imprisoned_count}</td>` +
        `</tr>`
      );
    }),
    `</tbody></table>`,
  ].join("");
  el.innerHTML = tbl;
}

function emptyStats(username: string): LifetimeStats {
  return {
    username, games_played: 0, wins_fast: 0, wins_slow: 0,
    jewels_stolen: 0, coins_stolen: 0, combat_wins: 0, combat_losses: 0,
    racked_count: 0, imprisoned_count: 0,
  };
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}

function updateStatus(root: HTMLElement, state: ClientState): void {
  const dot = root.querySelector<HTMLElement>("#conn-dot")!;
  const text = root.querySelector<HTMLElement>("#conn-text")!;
  dot.classList.remove("ok", "bad");
  if (state.connected) {
    dot.classList.add("ok");
    text.textContent = "connected";
  } else {
    dot.classList.add("bad");
    text.textContent = "disconnected — retrying…";
  }
  const youLabel = root.querySelector<HTMLElement>("#you-label")!;
  youLabel.textContent = state.you ? `you are ${state.you}` : "";
}

function updateBoard(root: HTMLElement, ws: WsClient, state: ClientState): void {
  const container = root.querySelector<HTMLElement>("#board-wrap")!;
  if (!state.board) {
    container.innerHTML = `<div class="board-placeholder">Loading board…</div>`;
    return;
  }
  renderBoard(container, {
    board: state.board,
    game: state.game,
    youUsername: state.you,
    onChooseDestination: (spaceId) => {
      ws.send("choose_move_path", { username: state.you, destination: spaceId }).catch(() => {});
    },
  });
}

function updateOpponents(root: HTMLElement, state: ClientState): void {
  const ul = root.querySelector<HTMLElement>("#opponents-list")!;
  ul.innerHTML = "";
  const g = state.game;
  if (!g) return;
  const curIdx = g.current_turn_index;
  const curName = g.turn_order[curIdx] ?? null;
  for (const p of g.players) {
    const li = document.createElement("li");
    if (!p.connected) li.classList.add("disconnected");

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = p.color;
    li.appendChild(swatch);

    const name = document.createElement("span");
    name.textContent = p.username;
    if (p.username === state.you) name.classList.add("you");
    if (p.username === curName) {
      name.textContent = `▶ ${name.textContent}`;
      name.style.color = "var(--accent)";
    }
    li.appendChild(name);

    const info = document.createElement("span");
    info.className = "badge";
    const bits: string[] = [];
    bits.push(`🂠${p.hand_size}`);
    if (p.jewels.length) bits.push(`💎${p.jewels.length}`);
    if (p.has_coin) bits.push("💰");
    if (p.escaped) bits.push("ESCAPED");
    if (p.status && p.status !== "normal") bits.push(p.status);
    info.textContent = bits.join(" ");
    li.appendChild(info);
    ul.appendChild(li);
  }
}

function updateDecks(root: HTMLElement, state: ClientState): void {
  const el = root.querySelector<HTMLElement>("#decks-info")!;
  const g = state.game;
  if (!g) { el.textContent = "—"; return; }
  const jewelsOut = Object.keys(g.jewels_available).length;
  const looseCount = Object.values(g.loose_jewels ?? {}).reduce((a, l) => a + l.length, 0);
  const tower = deckBucket(g.tower_draw_count, g.tower_discard_count);
  const raven = deckBucket(g.raven_draw_count, g.raven_discard_count);
  el.innerHTML = `
    <div>Tower: ${tower}</div>
    <div>Raven: ${raven}</div>
    <div>Jewels in the Tower: ${jewelsOut}${looseCount ? ` (+${looseCount} loose)` : ""}</div>
    <div>Coins in Deveraux: ${g.coins_available}</div>
  `;
}

function deckBucket(draw: number, discard: number): string {
  // Hide exact counts so deck-counting isn't a strategy; opponents can still
  // infer roughly where the pile is. The draw pile reshuffles from discard
  // once it hits zero, so callers who want to know "how much is left in the
  // round" should think of ``draw + discard``.
  if (draw === 0 && discard === 0) return "empty";
  if (draw === 0) return `reshuffling soon (${discard} in discard)`;
  if (draw <= 5) return `~${draw} left`;
  if (draw <= 15) return "running low";
  if (draw <= 30) return "plenty";
  return "full deck";
}

function updateChat(root: HTMLElement, state: ClientState): void {
  const log = root.querySelector<HTMLElement>("#chat-log")!;
  log.innerHTML = "";
  for (const m of state.chat.slice(-100)) {
    const d = document.createElement("div");
    d.className = "msg";
    const from = document.createElement("span");
    from.className = "from";
    from.textContent = `${m.from}:`;
    d.appendChild(from);
    d.appendChild(document.createTextNode(m.text));
    log.appendChild(d);
  }
  log.scrollTop = log.scrollHeight;
}
