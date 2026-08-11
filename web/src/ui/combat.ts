/**
 * Combat modal: card selection, then a played-out reveal.
 *
 * Two distinct sources drive this, and the split matters:
 *
 *   - **Selection** comes from ``state.game.combat`` on the snapshot. It's
 *     re-rendered on every state change; the interaction is sparse enough that
 *     rebuilding the whole modal is cheap and keeps the logic simple.
 *
 *   - **The reveal** comes from the ``combat_resolved`` *event*, not the
 *     snapshot — the server clears ``state.combat`` the moment combat resolves,
 *     so by the time anyone could animate it the snapshot has nothing left to
 *     animate. The event is broadcast identically to everyone, so the whole
 *     table watches the same sequence at the same pace.
 *
 * Everyone sees the modal, not just the two fighters: a combat where the rest
 * of the table can't tell what's happening (or who everyone is waiting on) is
 * the thing this replaces.
 */
import type { WsClient } from "../net/ws.js";
import type { Card, ClientState, Combat, GamePlayer } from "../state.js";
import { playerByName } from "../state.js";
import { towerCardIcon } from "./card_art.js";
import { hideBoardTooltip } from "../board/render.js";

/** One card as it appears in the reveal payload. */
interface RevealCard { id: string; name: string; value: number; }

/** The `combat_resolved` payload, plus our position in playing it back. */
interface Cinematic {
  attacker: string;
  defender: string;
  winner: string;
  loser: string;
  attackerCards: RevealCard[];
  defenderCards: RevealCard[];
  attackerTotal: number;
  defenderTotal: number;
  tie: boolean;
  jewelsTaken: string[];
  coinTaken: boolean;
  coinOverflowed: boolean;
  winnerDrew: string[];
  /** Interleaved play order: which side each step turns over. */
  order: ("attacker" | "defender")[];
  /** How many steps of ``order`` have been played. */
  step: number;
  /** Verdict shown; waiting on the winner's new-cards view (or a dismiss). */
  finished: boolean;
  /** The winner dismissed the spoils view — tear the whole thing down. */
  dismissed: boolean;
}

const REVEAL_INTERVAL_MS = 1000;

export function renderCombatModal(
  root: HTMLElement,
  ws: WsClient,
  requestUpdate: () => void,
): { update: (state: ClientState, ws: WsClient) => void } {
  root.innerHTML = `
    <div id="combat-overlay" class="combat-overlay">
      <div id="combat-box" class="combat-box"></div>
    </div>
  `;

  let cine: Cinematic | null = null;
  let timer: number | null = null;

  function clearTimer(): void {
    if (timer !== null) { window.clearTimeout(timer); timer = null; }
  }

  /** Turn over the next card, then queue the one after it. */
  function tick(): void {
    timer = null;
    if (!cine) return;
    if (cine.step < cine.order.length) {
      cine.step++;
      requestUpdate();
      timer = window.setTimeout(tick, REVEAL_INTERVAL_MS);
      return;
    }
    // All cards down — call it.
    cine.finished = true;
    requestUpdate();
  }

  ws.on("combat_resolved", (p: any) => {
    clearTimer();
    cine = buildCinematic(p);
    requestUpdate();
    // A beat before the first card, so the reveal doesn't start mid-blink.
    timer = window.setTimeout(tick, REVEAL_INTERVAL_MS / 2);
  });

  // A fresh game (or a reset) must not leave a stale reveal on screen.
  ws.on("game_started", () => { clearTimer(); cine = null; });
  ws.on("game_reset", () => { clearTimer(); cine = null; });

  function dismissCinematic(): void {
    clearTimer();
    cine = null;
    requestUpdate();
  }

  return {
    update: (state, wsc) => update(root, state, wsc, cine, dismissCinematic),
  };
}

function buildCinematic(p: any): Cinematic {
  const attackerCards: RevealCard[] = p.attacker_cards ?? [];
  const defenderCards: RevealCard[] = p.defender_cards ?? [];
  return {
    attacker: p.attacker,
    defender: p.defender,
    winner: p.winner,
    loser: p.loser,
    attackerCards,
    defenderCards,
    attackerTotal: p.attacker_total ?? 0,
    defenderTotal: p.defender_total ?? 0,
    tie: !!p.tie,
    jewelsTaken: Array.isArray(p.jewels_taken) ? p.jewels_taken : [],
    coinTaken: !!p.coin_taken,
    coinOverflowed: !!p.coin_overflowed,
    winnerDrew: Array.isArray(p.winner_drew) ? p.winner_drew : [],
    order: interleave(attackerCards.length, defenderCards.length),
    step: 0,
    finished: attackerCards.length + defenderCards.length === 0,
    dismissed: false,
  };
}

/**
 * Attacker, defender, attacker, … — and once one side runs out, the other
 * plays out its remainder rather than the sequence stopping short.
 */
function interleave(nAttacker: number, nDefender: number): ("attacker" | "defender")[] {
  const out: ("attacker" | "defender")[] = [];
  let a = 0, d = 0;
  while (a < nAttacker || d < nDefender) {
    if (a < nAttacker) { out.push("attacker"); a++; }
    if (d < nDefender) { out.push("defender"); d++; }
  }
  return out;
}

function update(
  root: HTMLElement,
  state: ClientState,
  ws: WsClient,
  cine: Cinematic | null,
  onDismissCinematic: () => void,
): void {
  const overlay = root.querySelector<HTMLElement>("#combat-overlay")!;
  const box = root.querySelector<HTMLElement>("#combat-box")!;
  const g = state.game;
  const combat = g?.combat ?? null;
  const inSelection = !!combat && g?.phase === "COMBAT";

  if (!cine && !inSelection) {
    overlay.style.display = "none";
    return;
  }

  // Same reason as the card modals: this covers the board, so any tooltip the
  // pointer left behind would sit there with nothing to dismiss it.
  if (overlay.style.display !== "flex") hideBoardTooltip();
  overlay.style.display = "flex";
  box.innerHTML = "";
  if (cine) {
    box.appendChild(revealView(state, cine, onDismissCinematic));
  } else {
    box.appendChild(header(combat!.attacker, combat!.defender));
    box.appendChild(statusLine(combat!, state.you));
    box.appendChild(selectionBody(state, combat!, ws));
  }
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------

function header(attacker: string, defender: string): HTMLElement {
  const h = document.createElement("div");
  h.className = "combat-header";
  h.innerHTML = `
    <h2>⚔ Combat</h2>
    <div class="combat-versus">
      <span class="combat-side">${escapeHtml(attacker)}</span>
      <span class="combat-vs">vs</span>
      <span class="combat-side">${escapeHtml(defender)}</span>
    </div>
  `;
  return h;
}

/**
 * What the table is waiting on, in plain words. Shown to everyone — a fight
 * stalled on a slow player should look stalled, not broken.
 */
function statusLine(combat: Combat, you: string | null): HTMLElement {
  const d = document.createElement("div");
  d.className = "combat-status";
  const who = (name: string) => (name === you ? "you" : name);
  let msg: string;
  switch (combat.phase) {
    case "attacker_selecting":
      msg = `Waiting for ${who(combat.attacker)} to choose cards…`;
      break;
    case "defender_selecting":
      msg = `${cap(who(combat.attacker))} committed ` +
        `${combat.attacker_cards_count ?? combat.attacker_cards.length} card(s). ` +
        `Waiting for ${who(combat.defender)} to choose…`;
      break;
    case "defender_specials":
      msg = `Both sides are committed. ` +
        `${cap(who(combat.defender))} may play a last-moment defence…`;
      break;
    default:
      msg = "Resolving…";
  }
  d.textContent = msg;
  return d;
}

function selectionBody(state: ClientState, combat: Combat, ws: WsClient): HTMLElement {
  const wrap = document.createElement("div");
  const you = state.you;
  const me: GamePlayer | null = playerByName(state.game, you);
  const isAttacker = you === combat.attacker;
  const isDefender = you === combat.defender;
  const phase = combat.phase;

  if (phase === "attacker_selecting" && isAttacker) {
    wrap.appendChild(cardPicker(
      "Choose the weapons you'll fight with — hidden from the defender until the reveal.",
      weaponsIn(me),
      (ids) => ws.send("select_combat_cards", { username: you, card_ids: ids }).catch(logErr),
    ));
  } else if (phase === "defender_selecting" && isDefender) {
    wrap.appendChild(cardPicker(
      "Choose your defence — any weapon, including the Suit of Armour.",
      defenderPlayable(me),
      (ids) => ws.send("select_combat_cards", { username: you, card_ids: ids }).catch(logErr),
    ));
  } else if (phase === "defender_specials" && isDefender) {
    const specials = (me?.hand ?? []).filter(
      (c) => c.effect_key === "sanctuary" || c.effect_key === "mass_accretor",
    );
    const row = document.createElement("div");
    row.className = "combat-actions";
    for (const c of specials) {
      row.appendChild(button(`Play ${c.name}`, () =>
        ws.send("play_combat_special", { username: you, card_id: c.id }).catch(logErr),
      ));
    }
    row.appendChild(button("Reveal!", () =>
      ws.send("reveal_combat", { username: you }).catch(logErr),
    ));
    wrap.appendChild(row);
  }

  // Read-back of what you've already committed.
  const mine = isAttacker ? combat.attacker_cards : isDefender ? combat.defender_cards : [];
  if (mine.length) {
    const d = document.createElement("div");
    d.className = "combat-committed";
    d.innerHTML = `<span>Committed:</span> ` + mine
      .map((c) => `<span class="combat-committed-card">${escapeHtml(c.name)} ` +
        `<b>${c.value ?? 0}</b></span>`)
      .join("");
    wrap.appendChild(d);
  }
  return wrap;
}

function weaponsIn(p: GamePlayer | null): Card[] {
  return (p?.hand ?? []).filter((c) => c.category === "weapon" && !c.defender_only);
}

function defenderPlayable(p: GamePlayer | null): Card[] {
  // Defender may play any weapon (including Suit of Armour).
  return (p?.hand ?? []).filter((c) => c.category === "weapon");
}

/** A grid of toggleable card tiles; the commit button carries the total. */
function cardPicker(
  labelText: string,
  cards: Card[],
  onCommit: (ids: string[]) => void,
): HTMLElement {
  const wrap = document.createElement("div");
  const label = document.createElement("div");
  label.className = "combat-picker-label";
  label.textContent = labelText;
  wrap.appendChild(label);

  if (cards.length === 0) {
    const empty = document.createElement("div");
    empty.className = "combat-empty";
    empty.textContent = "No eligible cards in hand — you'll fight bare-handed.";
    wrap.appendChild(empty);
  }

  const selected = new Set<string>();
  const grid = document.createElement("div");
  grid.className = "card-tile-grid";
  const commit = button("Commit", () => onCommit([...selected]));

  const refreshCommit = () => {
    const total = cards
      .filter((c) => selected.has(c.id))
      .reduce((a, c) => a + (c.value ?? 0), 0);
    commit.textContent = selected.size
      ? `Commit ${selected.size} card${selected.size === 1 ? "" : "s"} · ${total}`
      : "Commit";
    commit.disabled = selected.size === 0;
  };

  for (const c of cards) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "card-tile";
    tile.setAttribute("aria-pressed", "false");
    tile.innerHTML = `
      <span class="card-tile-value">${c.value ?? 0}</span>
      <span class="card-tile-art">${towerCardIcon(c.name, 40)}</span>
      <span class="card-tile-name">${escapeHtml(c.name)}</span>
    `;
    tile.addEventListener("click", () => {
      if (selected.has(c.id)) selected.delete(c.id);
      else selected.add(c.id);
      const on = selected.has(c.id);
      tile.classList.toggle("is-selected", on);
      tile.setAttribute("aria-pressed", String(on));
      refreshCommit();
    });
    grid.appendChild(tile);
  }
  wrap.appendChild(grid);

  const actions = document.createElement("div");
  actions.className = "combat-actions";
  refreshCommit();
  actions.appendChild(commit);
  actions.appendChild(button("Fight with none", () => onCommit([])));
  wrap.appendChild(actions);
  return wrap;
}

// ---------------------------------------------------------------------------
// Reveal
// ---------------------------------------------------------------------------

function revealView(
  state: ClientState,
  cine: Cinematic,
  onDismiss: () => void,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.appendChild(header(cine.attacker, cine.defender));

  // How many of each side's cards have been turned over so far.
  const played = cine.order.slice(0, cine.step);
  const nAttacker = played.filter((s) => s === "attacker").length;
  const nDefender = played.filter((s) => s === "defender").length;

  const rows = document.createElement("div");
  rows.className = "combat-reveal";
  rows.appendChild(revealSide(cine.attacker, "attacker", cine.attackerCards, nAttacker));
  rows.appendChild(revealSide(cine.defender, "defender", cine.defenderCards, nDefender));
  wrap.appendChild(rows);

  if (!cine.finished) {
    const s = document.createElement("div");
    s.className = "combat-status";
    s.textContent = "Revealing…";
    wrap.appendChild(s);
    return wrap;
  }

  wrap.appendChild(verdict(cine));

  // The winner alone can turn their new card ids into faces — every other
  // client sees an empty hand for them.
  const you = state.you;
  const drew = you === cine.winner ? resolveCards(state, cine.winnerDrew) : [];
  if (drew.length) wrap.appendChild(spoilsView(drew));

  const actions = document.createElement("div");
  actions.className = "combat-actions";
  actions.appendChild(button(drew.length ? "Take them" : "Close", onDismiss));
  wrap.appendChild(actions);
  return wrap;
}

function revealSide(
  name: string,
  side: "attacker" | "defender",
  cards: RevealCard[],
  shown: number,
): HTMLElement {
  const d = document.createElement("div");
  d.className = `combat-reveal-side combat-reveal-${side}`;
  const runningTotal = cards.slice(0, shown).reduce((a, c) => a + (c.value ?? 0), 0);
  const slots = cards.map((c, i) => i < shown
    ? `<span class="combat-reveal-card is-shown">
         ${towerCardIcon(c.name, 34)}
         <span class="combat-reveal-card-value">${c.value ?? 0}</span>
       </span>`
    : `<span class="combat-reveal-card"></span>`,
  ).join("");
  d.innerHTML = `
    <div class="combat-reveal-head">
      <span class="combat-reveal-name">${escapeHtml(name)}
        <em>${side}</em></span>
      <span class="combat-reveal-total">${runningTotal}</span>
    </div>
    <div class="combat-reveal-cards">${slots || `<span class="combat-empty">no cards</span>`}</div>
  `;
  return d;
}

function verdict(cine: Cinematic): HTMLElement {
  const d = document.createElement("div");
  d.className = "combat-verdict";
  const spoils: string[] = [];
  if (cine.jewelsTaken.length) {
    spoils.push(`${cine.jewelsTaken.length} jewel${cine.jewelsTaken.length === 1 ? "" : "s"}`);
  }
  if (cine.coinTaken) spoils.push("a coin");
  d.innerHTML = `
    <div class="combat-verdict-winner">${escapeHtml(cine.winner)} wins!</div>
    ${cine.tie ? `<div class="combat-verdict-note">A tie — the defender holds.</div>` : ""}
    ${spoils.length
      ? `<div class="combat-verdict-note">Takes ${spoils.join(" and ")} from ${escapeHtml(cine.loser)}.` +
        (cine.coinOverflowed ? ` The spare coin returns to Devereux.` : "") + `</div>`
      : ""}
    <div class="combat-verdict-note">
      ${escapeHtml(cine.loser)} is carried to the Hospital and misses a turn.
    </div>
  `;
  return d;
}

function spoilsView(cards: Card[]): HTMLElement {
  const d = document.createElement("div");
  d.className = "combat-spoils";
  d.innerHTML = `
    <div class="combat-spoils-label">You draw ${cards.length}
      replacement${cards.length === 1 ? "" : "s"}:</div>
    <div class="combat-spoils-cards">
      ${cards.map((c) => `
        <div class="combat-spoils-card">
          <div class="combat-spoils-name">${escapeHtml(c.name)}</div>
          <div class="combat-spoils-art">${towerCardIcon(c.name, 44)}</div>
          <div class="combat-spoils-value">${
            c.value ? escapeHtml(String(c.value)) : escapeHtml(c.category ?? "card")
          }</div>
        </div>`).join("")}
    </div>
  `;
  return d;
}

/** Card objects for ids that are in the viewer's own hand. */
function resolveCards(state: ClientState, ids: string[]): Card[] {
  const me = playerByName(state.game, state.you);
  const byId = new Map((me?.hand ?? []).map((c) => [c.id, c]));
  return ids.map((id) => byId.get(id)).filter((c): c is Card => !!c);
}

// ---------------------------------------------------------------------------

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!
  ));
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function logErr(e: Error): void {
  // Non-blocking — surface via the global status/error banner.
  console.error("combat intent failed:", e.message);
}
