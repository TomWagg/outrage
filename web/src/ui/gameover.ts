/**
 * End-of-game results screen.
 *
 * A full-screen banner everyone sees, plus a card per player showing what they
 * finished with — jewels, coin, their whole hand (hands stop being secret once
 * the game is over, so the server sends them all) — and the per-game tallies
 * the server folded out of the event log.
 *
 * Driven purely from the snapshot, so a player who reconnects to a finished
 * game gets the same screen rather than an empty board.
 */
import type { WsClient } from "../net/ws.js";
import type { ClientState, GamePlayer } from "../state.js";
import { coinDisc, jewelDisc, jewelEmblem, jewelLabel, towerCardIcon } from "./card_art.js";

/** Jewel values, for the slow-mode ranking. */
const JEWEL_VALUE: Record<string, number> = {
  crown_st_edward: 5, crown_prince_of_wales: 4, sceptre: 3, orb: 2, sword: 1,
};

/**
 * Every player shows every stat, zeros included — the cards sit side by side,
 * so hiding empty tiles made the grids ragged and the columns impossible to
 * compare at a glance.
 *
 * Grouped by theme: the haul, then the legwork, then combat, then cards, then
 * misfortune. ``turns_taken`` is still recorded server-side but isn't shown —
 * it's near-identical for everyone and told you nothing.
 */
const STATS: { key: string; label: string }[] = [
  { key: "jewels_collected", label: "Jewels" },
  { key: "coins_picked_up", label: "Coins" },
  { key: "steps_taken", label: "Steps" },
  { key: "jewel_attempts", label: "Theft attempts" },
  { key: "fights_won", label: "Fights won" },
  { key: "fights_lost", label: "Fights lost" },
  { key: "tower_cards_drawn", label: "Tower cards" },
  { key: "raven_cards_drawn", label: "Raven cards" },
  { key: "doubles_rolled", label: "Doubles" },
  { key: "turns_lost", label: "Turns lost" },
  { key: "times_locked_up", label: "Locked up" },
];

export function renderGameOverScreen(root: HTMLElement): {
  update: (state: ClientState, ws: WsClient) => void;
} {
  root.innerHTML = `<div id="gameover-overlay" class="gameover-overlay"></div>`;
  return { update: (state, ws) => update(root, state, ws) };
}

function update(root: HTMLElement, state: ClientState, ws: WsClient): void {
  const overlay = root.querySelector<HTMLElement>("#gameover-overlay")!;
  const g = state.game;
  if (!g || g.phase !== "GAME_OVER") {
    overlay.style.display = "none";
    overlay.innerHTML = "";
    return;
  }
  if (overlay.style.display === "block") return;  // already built

  overlay.style.display = "block";
  overlay.innerHTML = `
    <div class="gameover-sheet">
      ${banner(state)}
      ${g.mode === "slow" ? podium(g.players) : ""}
      <div class="gameover-players">
        ${[...g.players]
          .sort(orderForDisplay(g.winner))
          .map((p) => playerCard(p, state))
          .join("")}
      </div>
      <div class="gameover-actions">
        <button class="gameover-again" data-action="lobby">Return to lobby</button>
      </div>
    </div>
  `;
  overlay.querySelector<HTMLButtonElement>('[data-action="lobby"]')
    ?.addEventListener("click", () => { ws.send("reset_lobby", {}).catch(() => {}); });
}

/** Winner first, then everyone else alphabetically. */
function orderForDisplay(winner: string | null) {
  return (a: GamePlayer, b: GamePlayer) => {
    if (a.username === winner) return -1;
    if (b.username === winner) return 1;
    return a.username.localeCompare(b.username);
  };
}

function banner(state: ClientState): string {
  const g = state.game!;
  const winner = g.winner;
  const modeTag = `<span class="gameover-mode">${
    g.mode === "fast" ? "Fast game" : "Slow game"}</span>`;

  // No winner means the game was ended early — a draw, not a defeat.
  if (!winner) {
    return `
      <div class="gameover-banner is-draw">
        <div class="gameover-crown">${jewelEmblem("orb", 56)}</div>
        <h1 class="gameover-title">A draw</h1>
        <div class="gameover-sub">
          The game was called before anyone got away with the Crown Jewels.
          ${modeTag}
        </div>
      </div>
    `;
  }

  const youWon = winner === state.you;
  const reason = g.mode === "fast"
    ? "was first to bank a jewel at the hideout"
    : "banked a haul nobody can catch";
  return `
    <div class="gameover-banner${youWon ? " is-you" : ""}">
      <div class="gameover-crown">${jewelEmblem("crown_st_edward", 60)}</div>
      <h1 class="gameover-title">${escapeHtml(winner)} wins!</h1>
      <div class="gameover-sub">
        ${youWon ? "That's you — " : ""}${escapeHtml(reason)}.
        ${modeTag}
      </div>
    </div>
  `;
}

function podium(players: GamePlayer[]): string {
  // Scored on banked jewels only — mirrors ``_slow_ranking`` on the server.
  // Anything still in a pocket when the game ended never left the Tower.
  const rows = [...players]
    .map((p) => ({
      username: p.username,
      count: p.banked_jewels.length,
      top: p.banked_jewels.reduce((m, j) => Math.max(m, JEWEL_VALUE[j] ?? 0), 0),
      total: p.banked_jewels.reduce((s, j) => s + (JEWEL_VALUE[j] ?? 0), 0),
      carrying: p.jewels.length,
    }))
    .sort((a, b) =>
      b.count - a.count || b.top - a.top || b.total - a.total
      || a.username.localeCompare(b.username));
  return `
    <ol class="gameover-podium">
      ${rows.map((r) => `
        <li>
          <span class="gameover-podium-name">${escapeHtml(r.username)}</span>
          <span class="gameover-podium-score">
            ${r.count} banked
            ${r.total ? `· ${r.total} pts` : ""}
            ${r.carrying ? `<em>${r.carrying} left in pocket</em>` : ""}
          </span>
        </li>`).join("")}
    </ol>
  `;
}

function playerCard(p: GamePlayer, state: ClientState): string {
  const g = state.game!;
  const isWinner = p.username === g.winner;
  const stats = (g.final_stats ?? {})[p.username] as Record<string, number> | undefined;
  const hand = p.hand ?? [];

  return `
    <div class="gameover-player${isWinner ? " is-winner" : ""}">
      <div class="gameover-player-head">
        <span class="gameover-swatch" style="background:${escapeHtml(p.color)}"></span>
        <span class="gameover-name">${escapeHtml(p.username)}</span>
        ${isWinner ? `<span class="gameover-badge">winner</span>` : ""}
        ${p.username === state.you ? `<span class="gameover-badge muted">you</span>` : ""}
      </div>

      <div class="gameover-haul">
        ${p.banked_jewels.length
          ? p.banked_jewels.map((j) =>
              `<span class="gameover-jewel" title="${escapeHtml(jewelLabel(j))} — banked">
                 ${jewelDisc(j, 30)}</span>`).join("")
          : `<span class="gameover-none">nothing banked</span>`}
        ${p.jewels.map((j) =>
            `<span class="gameover-jewel is-unbanked"
                   title="${escapeHtml(jewelLabel(j))} — never made it out of the Tower">
               ${jewelDisc(j, 30)}</span>`).join("")}
        ${p.has_coin ? `<span class="gameover-coin" title="Holding a coin">${coinDisc(30)}</span>` : ""}
      </div>

      ${statGrid(stats)}

      <div class="gameover-hand-label">
        Final hand${hand.length ? ` (${hand.length})` : ""}
      </div>
      <div class="gameover-hand">
        ${hand.length
          ? hand.map((c) => `
              <span class="gameover-card" title="${escapeHtml(c.name)}">
                ${c.value ? `<span class="gameover-card-value">${c.value}</span>` : ""}
                ${towerCardIcon(c.name, 26)}
              </span>`).join("")
          : `<span class="gameover-none">empty-handed</span>`}
      </div>
    </div>
  `;
}

function statGrid(stats: Record<string, number> | undefined): string {
  if (!stats) return "";
  return `
    <div class="gameover-stats">
      ${STATS.map((s) => `
        <div class="gameover-stat">
          <span class="gameover-stat-value">${stats[s.key] ?? 0}</span>
          <span class="gameover-stat-label">${escapeHtml(s.label)}</span>
        </div>`).join("")}
    </div>
  `;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!
  ));
}
