/**
 * Combat modal for attacker / defender card selection and reveal.
 *
 * The modal is mounted into a fixed-position overlay inside the game view.
 * Combat is driven entirely by ``state.game.combat`` + ``state.game.phase``.
 * A single update pass re-renders the whole modal on every state change —
 * the interaction is sparse enough that it's cheap and keeps the logic simple.
 */
import type { WsClient } from "../net/ws.js";
import type { Card, ClientState, Combat, GamePlayer } from "../state.js";
import { playerByName } from "../state.js";

export function renderCombatModal(root: HTMLElement): { update: (state: ClientState, ws: WsClient) => void } {
  root.innerHTML = `
    <div id="combat-overlay" style="
      position: fixed; inset: 0;
      background: rgba(0,0,0,0.55);
      display: none;
      align-items: center; justify-content: center;
      z-index: 10;
    ">
      <div id="combat-box" style="
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.25rem;
        min-width: 420px;
        max-width: 640px;
        max-height: 90vh;
        overflow-y: auto;
      "></div>
    </div>
  `;
  return { update: (state, ws) => update(root, state, ws) };
}

function update(root: HTMLElement, state: ClientState, ws: WsClient): void {
  const overlay = root.querySelector<HTMLElement>("#combat-overlay")!;
  const box = root.querySelector<HTMLElement>("#combat-box")!;

  const g = state.game;
  const combat = g?.combat ?? null;
  if (!combat || g?.phase !== "COMBAT") {
    overlay.style.display = "none";
    return;
  }

  const you = state.you;
  if (you !== combat.attacker && you !== combat.defender) {
    // Spectators see the chatter in the log; no modal for them.
    overlay.style.display = "none";
    return;
  }

  overlay.style.display = "flex";
  box.innerHTML = "";
  box.appendChild(header(combat));
  box.appendChild(body(state, combat, ws));
}

function header(combat: Combat): HTMLElement {
  const h = document.createElement("div");
  h.style.marginBottom = "0.75rem";
  h.innerHTML = `
    <h2 style="margin:0 0 0.25rem 0; color:var(--accent)">Combat</h2>
    <div style="font-size:0.9rem; color:var(--muted)">
      <strong>${combat.attacker}</strong> vs <strong>${combat.defender}</strong>
      · phase: <code>${combat.phase}</code>
    </div>
  `;
  return h;
}

function body(state: ClientState, combat: Combat, ws: WsClient): HTMLElement {
  const wrap = document.createElement("div");
  const you = state.you!;
  const me: GamePlayer | null = playerByName(state.game, you);
  const isAttacker = you === combat.attacker;
  const isDefender = you === combat.defender;
  const phase = combat.phase;

  // ---------- opponent summary ----------
  const opposer = document.createElement("div");
  opposer.style.marginBottom = "0.75rem";
  opposer.style.fontSize = "0.85rem";
  const oppName = isAttacker ? combat.defender : combat.attacker;
  const oppCount = isAttacker
    ? (combat.defender_cards_count ?? combat.defender_cards.length)
    : (combat.attacker_cards_count ?? combat.attacker_cards.length);
  const oppCommitted = isAttacker ? combat.defender_committed : combat.attacker_committed;
  opposer.innerHTML = `
    <div>Opponent: <strong>${oppName}</strong></div>
    <div>Cards committed: ${oppCount}${oppCommitted ? " (locked in)" : ""}</div>
  `;
  wrap.appendChild(opposer);

  // ---------- phase-specific content ----------
  if (phase === "attacker_selecting" && isAttacker) {
    wrap.appendChild(cardPicker(
      "Pick weapon cards to commit (hidden from defender until reveal):",
      weaponsIn(me),
      (ids) => ws.send("select_combat_cards", { username: you, card_ids: ids }).catch(alertErr),
    ));
  } else if (phase === "attacker_selecting" && isDefender) {
    wrap.appendChild(text("Waiting for the attacker to commit cards…"));
  } else if (phase === "defender_selecting" && isDefender) {
    wrap.appendChild(cardPicker(
      "Pick weapons (and/or Suit of Armour) to commit:",
      defenderPlayable(me),
      (ids) => ws.send("select_combat_cards", { username: you, card_ids: ids }).catch(alertErr),
    ));
  } else if (phase === "defender_selecting" && isAttacker) {
    wrap.appendChild(text("Waiting for the defender to commit cards…"));
  } else if (phase === "defender_specials" && isDefender) {
    wrap.appendChild(text("You may play a special (Sanctuary or Mass Accretor) before reveal, or reveal now."));
    const specials = (me?.hand ?? []).filter((c) => c.effect_key === "sanctuary" || c.effect_key === "mass_accretor");
    const row = document.createElement("div");
    row.style.display = "flex";
    row.style.gap = "0.4rem";
    row.style.flexWrap = "wrap";
    row.style.marginTop = "0.5rem";
    for (const c of specials) {
      row.appendChild(button(`Play ${c.name}`, () =>
        ws.send("play_combat_special", { username: you, card_id: c.id }).catch(alertErr),
      ));
    }
    row.appendChild(button("Reveal", () => ws.send("reveal_combat", { username: you }).catch(alertErr)));
    wrap.appendChild(row);
  } else if (phase === "defender_specials" && isAttacker) {
    wrap.appendChild(text("Defender is considering their final defences…"));
  } else if (phase === "revealed" || phase === "resolved") {
    wrap.appendChild(revealDisplay(combat));
  }

  // ---------- your committed cards (readback) ----------
  const mine = isAttacker ? combat.attacker_cards : combat.defender_cards;
  if (mine.length) {
    const d = document.createElement("div");
    d.style.marginTop = "0.75rem";
    d.style.fontSize = "0.85rem";
    d.style.color = "var(--muted)";
    d.innerHTML = `Your committed cards: ${mine.map((c) => `${c.name} (+${c.value ?? 0})`).join(", ")}`;
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

function cardPicker(
  labelText: string,
  cards: Card[],
  onCommit: (ids: string[]) => void,
): HTMLElement {
  const wrap = document.createElement("div");
  const label = document.createElement("div");
  label.textContent = labelText;
  label.style.fontSize = "0.85rem";
  label.style.marginBottom = "0.4rem";
  wrap.appendChild(label);

  if (cards.length === 0) {
    const empty = document.createElement("div");
    empty.textContent = "No eligible cards in hand.";
    empty.style.color = "var(--muted)";
    empty.style.fontStyle = "italic";
    empty.style.marginBottom = "0.5rem";
    wrap.appendChild(empty);
  }

  const list = document.createElement("div");
  list.style.display = "flex";
  list.style.flexDirection = "column";
  list.style.gap = "0.25rem";
  list.style.marginBottom = "0.6rem";
  const checks: HTMLInputElement[] = [];
  for (const c of cards) {
    const row = document.createElement("label");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "0.5rem";
    row.style.padding = "0.25rem 0.4rem";
    row.style.background = "var(--panel-2)";
    row.style.border = "1px solid var(--border)";
    row.style.borderRadius = "4px";
    row.style.fontSize = "0.85rem";
    row.style.cursor = "pointer";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.cardId = c.id;
    checks.push(box);
    row.appendChild(box);
    const text = document.createElement("span");
    text.style.flex = "1";
    text.textContent = c.name;
    row.appendChild(text);
    const val = document.createElement("span");
    val.style.color = "var(--accent)";
    val.textContent = `+${c.value ?? 0}`;
    row.appendChild(val);
    list.appendChild(row);
  }
  wrap.appendChild(list);

  const actions = document.createElement("div");
  actions.style.display = "flex";
  actions.style.gap = "0.4rem";
  actions.appendChild(button("Commit selection", () => {
    const ids = checks.filter((c) => c.checked).map((c) => c.dataset.cardId!).filter(Boolean);
    onCommit(ids);
  }));
  actions.appendChild(button("Commit none", () => onCommit([])));
  wrap.appendChild(actions);
  return wrap;
}

function revealDisplay(combat: Combat): HTMLElement {
  const d = document.createElement("div");
  const aTotal = combat.attacker_cards.reduce((a, c) => a + (c.value ?? 0), 0);
  const dTotal = combat.defender_cards.reduce((a, c) => a + (c.value ?? 0), 0);
  const line = (name: string, cards: Card[], total: number): string => `
    <div style="margin-top:0.4rem">
      <strong>${name}:</strong> ${cards.length ? cards.map((c) => `${c.name} (+${c.value ?? 0})`).join(", ") : "—"}
      <span style="float:right; color:var(--accent)">total ${total}</span>
    </div>`;
  d.innerHTML = `
    ${line(combat.attacker + " (attacker)", combat.attacker_cards, aTotal)}
    ${line(combat.defender + " (defender)", combat.defender_cards, dTotal)}
    <div style="margin-top:0.75rem; font-size:1rem">
      Winner: <strong style="color:var(--accent)">${combat.winner ?? "—"}</strong>
    </div>
  `;
  return d;
}

function text(s: string): HTMLElement {
  const d = document.createElement("div");
  d.textContent = s;
  d.style.color = "var(--muted)";
  d.style.fontStyle = "italic";
  return d;
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function alertErr(e: Error): void {
  // Non-blocking — surface via the global status/error banner.
  console.error("combat intent failed:", e.message);
}
