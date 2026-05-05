/**
 * Game view layout: board + collapsible sidebar (controls, hand, opponents, decks, log, chat).
 *
 * The sidebar slides in/out via a toggle tab.  Each panel in the sidebar can
 * be independently collapsed; their open/closed states persist in
 * ``localStorage``.
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
import { createDiceDisplay } from "./dice.js";

export function renderGameLayout(
  root: HTMLElement,
  ws: WsClient,
  state: ClientState,
): { update: () => void } {
  root.innerHTML = `
    <div class="main" id="game-main">
      <div class="board-wrap" id="board-wrap"></div>
      <div class="sidebar-outer" id="sidebar-outer">
        <div class="side">

          <!-- Connection status — always visible, not collapsible -->
          <div class="side-status">
            <span class="dot" id="conn-dot"></span>
            <span id="conn-text">connecting…</span>
            <span class="side-you" id="you-label"></span>
            <button class="sidebar-hide-btn" id="sidebar-hide-btn" title="Hide sidebar">‹</button>
          </div>

          <!-- Turn controls -->
          <div id="controls-slot"></div>

          <!-- Dice -->
          <div class="panel" id="dice-panel">
            <h3>Dice</h3>
            <div id="dice-slot"></div>
          </div>

          <!-- Your hand -->
          <div id="hand-slot"></div>

          <!-- Players list -->
          <div id="opponents-slot" class="panel">
            <h3>Players</h3>
            <ul class="player-list" id="opponents-list"></ul>
          </div>

          <!-- Decks & jewels (hidden until game starts) -->
          <div id="decks-slot" class="panel" style="display:none">
            <h3>Decks &amp; jewels</h3>
            <div id="decks-info" style="font-size:0.85rem;color:var(--muted)"></div>
          </div>

          <!-- Event log -->
          <div id="log-slot"></div>

          <!-- Chat -->
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
    </div>

    <!-- Tab that appears at the screen edge when the sidebar is hidden -->
    <button class="sidebar-reveal-tab" id="sidebar-reveal-tab" title="Show sidebar">›</button>

    <div id="combat-modal-slot"></div>
  `;

  // ---- sub-panel renderers ---------------------------------------------------
  const controls = renderControlsPanel(root.querySelector<HTMLElement>("#controls-slot")!);
  const hand     = renderHandPanel(root.querySelector<HTMLElement>("#hand-slot")!);
  const logPanel = renderLogPanel(root.querySelector<HTMLElement>("#log-slot")!);
  const combat   = renderCombatModal(root.querySelector<HTMLElement>("#combat-modal-slot")!);

  const dice = createDiceDisplay();
  root.querySelector<HTMLElement>("#dice-slot")!.appendChild(dice.el);

  // ---- make all panels collapsible ------------------------------------------
  // Panels rendered by external functions live inside their slot divs.
  const controlsPanel  = root.querySelector<HTMLElement>("#controls-panel")!;
  const handPanel      = root.querySelector<HTMLElement>("#hand-panel")!;
  const logPanelEl     = root.querySelector<HTMLElement>("#log-panel")!;

  makeCollapsible(controlsPanel,  "turn");
  makeCollapsible(root.querySelector<HTMLElement>("#dice-panel")!,      "dice");
  makeCollapsible(handPanel,      "hand");
  makeCollapsible(root.querySelector<HTMLElement>("#opponents-slot")!,  "players");
  makeCollapsible(root.querySelector<HTMLElement>("#decks-slot")!,      "decks");
  makeCollapsible(logPanelEl,     "log");
  makeCollapsible(root.querySelector<HTMLElement>("#chat-slot")!,       "chat");

  // ---- sidebar toggle --------------------------------------------------------
  const sidebarOuter = root.querySelector<HTMLElement>("#sidebar-outer")!;
  const revealTab    = root.querySelector<HTMLElement>("#sidebar-reveal-tab")!;
  const hideBtn      = root.querySelector<HTMLElement>("#sidebar-hide-btn")!;

  const SIDEBAR_KEY = "sidebar:open";
  const sidebarOpen = localStorage.getItem(SIDEBAR_KEY) !== "false";
  if (!sidebarOpen) openSidebar(false);

  hideBtn.addEventListener("click",   () => closeSidebar(true));
  revealTab.addEventListener("click", () => openSidebar(true));

  function closeSidebar(animate: boolean): void {
    if (!animate) sidebarOuter.style.transition = "none";
    sidebarOuter.classList.add("sidebar-hidden");
    revealTab.classList.add("visible");
    if (!animate) requestAnimationFrame(() => sidebarOuter.style.transition = "");
    localStorage.setItem(SIDEBAR_KEY, "false");
  }
  function openSidebar(animate: boolean): void {
    if (!animate) sidebarOuter.style.transition = "none";
    sidebarOuter.classList.remove("sidebar-hidden");
    revealTab.classList.remove("visible");
    if (!animate) requestAnimationFrame(() => sidebarOuter.style.transition = "");
    localStorage.setItem(SIDEBAR_KEY, "true");
  }

  // ---- chat wiring -----------------------------------------------------------
  const chatInput = root.querySelector<HTMLInputElement>("#chat-input")!;
  const chatSend  = root.querySelector<HTMLButtonElement>("#chat-send")!;
  const sendChat = () => {
    const text = chatInput.value.trim();
    if (!text) return;
    ws.send("chat", { text }).catch(() => {});
    chatInput.value = "";
  };
  chatSend.addEventListener("click", sendChat);
  chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

  // ---- roll-key tracking (unchanged from before) ----------------------------
  let prevRollKey = "";

  return {
    update: () => {
      updateStatus(root, state);

      const roll   = state.game?.turn.roll ?? [];
      const rollKey = `${state.game?.current_turn_index ?? -1}:${roll.join(",")}`;
      const newRoll = rollKey !== prevRollKey && roll.length === 2;
      if (newRoll) prevRollKey = rollKey;

      if (!dice.animating && !newRoll) {
        updateBoard(root, ws, state);
      }

      controls.update(state, ws);
      hand.update(state);
      updateOpponents(root, state);
      updateDecks(root, state);
      logPanel.update(state);
      updateChat(root, state);
      combat.update(state, ws);

      if (newRoll) {
        dice.roll(roll[0], roll[1], () => {
          updateBoard(root, ws, state);
        });
      }
    },
  };
}

// =============================================================================
// Collapsible panels
// =============================================================================

/**
 * Turn a ``.panel`` element into a collapsible section.
 *
 * Wraps all children after the first ``<h3>`` in a ``.panel-body`` div,
 * appends a chevron arrow to the heading, and saves the open/closed state
 * in ``localStorage`` under ``panel:<id>``.
 */
function makeCollapsible(panel: HTMLElement | null, id: string): void {
  if (!panel) return;
  const h3 = panel.querySelector<HTMLElement>(":scope > h3");
  if (!h3) return;

  // Wrap non-h3 direct children in .panel-body
  const body = document.createElement("div");
  body.className = "panel-body";
  const toMove = Array.from(panel.childNodes).filter(n => n !== h3);
  for (const child of toMove) body.appendChild(child);
  panel.appendChild(body);

  // Add chevron
  const arrow = document.createElement("span");
  arrow.className = "collapse-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "▾";
  h3.appendChild(arrow);

  // Restore saved state
  if (localStorage.getItem(`panel:${id}`) === "closed") {
    panel.classList.add("collapsed");
  }

  // Toggle on click
  h3.addEventListener("click", () => {
    const nowCollapsed = panel.classList.toggle("collapsed");
    localStorage.setItem(`panel:${id}`, nowCollapsed ? "closed" : "open");
  });
}

// =============================================================================
// Status / update helpers
// =============================================================================

function updateStatus(root: HTMLElement, state: ClientState): void {
  const dot  = root.querySelector<HTMLElement>("#conn-dot")!;
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
  const curIdx  = g.current_turn_index;
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
  const slot = root.querySelector<HTMLElement>("#decks-slot")!;
  const el   = root.querySelector<HTMLElement>("#decks-info")!;
  const g = state.game;
  if (!g) {
    slot.style.display = "none";
    return;
  }
  slot.style.removeProperty("display");
  const jewelsOut  = Object.keys(g.jewels_available).length;
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
  if (draw === 0 && discard === 0) return "empty";
  if (draw === 0) return `reshuffling soon (${discard} in discard)`;
  if (draw <= 5)  return `~${draw} left`;
  if (draw <= 15) return "running low";
  if (draw <= 30) return "plenty";
  return "full deck";
}

function updateChat(root: HTMLElement, state: ClientState): void {
  const log = root.querySelector<HTMLElement>("#chat-log")!;
  log.innerHTML = "";
  for (const m of state.chat.slice(-100)) {
    const d    = document.createElement("div");
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
