import type { WsClient } from "../net/ws.js";
import type { ClientState } from "../state.js";

export function renderLobbyLayout(root: HTMLElement, ws: WsClient, state: ClientState): {
  update: () => void;
} {
  root.innerHTML = `
    <div class="status">
      <span><span class="dot" id="conn-dot"></span><span id="conn-text">connecting…</span></span>
      <span id="you-label"></span>
    </div>
    <div class="main">
      <div class="board-wrap">
        <div class="board-placeholder">
          <div>
            <div style="font-size:1.2em; color: var(--accent);">Board will appear here</div>
            <div style="margin-top:0.5rem;">Waiting for a board layout to be authored.<br/>
              In the meantime, join players in the lobby and start a game from here.
            </div>
          </div>
        </div>
      </div>
      <div class="side">
        <div class="panel">
          <h3>Lobby</h3>
          <ul class="player-list" id="players"></ul>
          <div style="margin-top: 0.75rem; display:flex; gap:0.5rem; align-items:center;">
            <label for="mode" style="color: var(--muted);">Mode:</label>
            <select id="mode">
              <option value="fast">Fast (first jewel wins)</option>
              <option value="slow">Slow (most jewels wins)</option>
            </select>
          </div>
          <div style="margin-top: 0.75rem;">
            <button id="start">Start game</button>
            <span id="start-error" class="error" style="display:inline-block;margin-left:0.5rem"></span>
          </div>
        </div>
        <div class="panel">
          <h3>Stats</h3>
          <div id="stats" style="font-size:0.9rem; color: var(--muted);"></div>
        </div>
        <div class="panel">
          <h3>Chat</h3>
          <div class="chat-log" id="chat-log"></div>
          <div class="chat-row">
            <input id="chat-input" placeholder="Say something…" maxlength="500" />
            <button id="chat-send">Send</button>
          </div>
        </div>
      </div>
    </div>
  `;

  const modeSelect = root.querySelector<HTMLSelectElement>("#mode")!;
  modeSelect.addEventListener("change", () => {
    ws.send("set_mode", { mode: modeSelect.value }).catch(() => {
      /* error shown in status line */
    });
  });

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

  const startBtn = root.querySelector<HTMLButtonElement>("#start")!;
  const startErr = root.querySelector<HTMLElement>("#start-error")!;
  startBtn.addEventListener("click", () => {
    startErr.textContent = "";
    ws.send("start_game", {}).catch((e: Error) => {
      startErr.textContent = e.message;
    });
  });

  return {
    update: () => update(root, state),
  };
}

function update(root: HTMLElement, state: ClientState): void {
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

  const ul = root.querySelector<HTMLElement>("#players")!;
  ul.innerHTML = "";
  const players = state.lobby?.players ?? [];
  if (players.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No players yet";
    li.style.color = "var(--muted)";
    ul.appendChild(li);
  }
  for (const p of players) {
    const li = document.createElement("li");
    if (!p.connected) li.classList.add("disconnected");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = p.color;
    li.appendChild(swatch);
    const name = document.createElement("span");
    name.textContent = p.username;
    if (p.username === state.you) name.classList.add("you");
    li.appendChild(name);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = p.connected ? "online" : "offline";
    li.appendChild(badge);
    ul.appendChild(li);
  }

  const mode = root.querySelector<HTMLSelectElement>("#mode")!;
  if (state.lobby) mode.value = state.lobby.mode;

  const startBtn = root.querySelector<HTMLButtonElement>("#start");
  if (startBtn) {
    const n = state.lobby?.players.length ?? 0;
    startBtn.disabled = n < 2;
    startBtn.title = n < 2 ? "Need at least 2 players" : "";
  }

  // Stats panel
  const statsEl = root.querySelector<HTMLElement>("#stats")!;
  if (state.stats) {
    const s = state.stats;
    statsEl.innerHTML = `
      <div>Games played: <strong>${s.games_played}</strong></div>
      <div>Wins (fast / slow): <strong>${s.wins_fast} / ${s.wins_slow}</strong></div>
      <div>Jewels stolen: <strong>${s.jewels_stolen}</strong></div>
      <div>Combat W/L: <strong>${s.combat_wins} / ${s.combat_losses}</strong></div>
      <div>Racked: <strong>${s.racked_count}</strong> &nbsp; Imprisoned: <strong>${s.imprisoned_count}</strong></div>
    `;
  } else {
    statsEl.textContent = "—";
  }

  // Chat
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
