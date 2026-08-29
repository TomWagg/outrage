/**
 * Display the viewer's own hand of tower cards, plus coin / jewels summary.
 *
 * Opponents' hands are only known by ``hand_size`` on the server snapshot;
 * for our own player we list the actual cards so the player can plan combat,
 * jewel attempts, etc.
 */
import type { ClientState, Card, GamePlayer } from "../state.js";
import { playerByName } from "../state.js";
import { towerCardIcon } from "./card_art.js";
import { towerCardCopy } from "./card_descriptions.js";

export function renderHandPanel(root: HTMLElement): { update: (state: ClientState) => void } {
  root.innerHTML = `
    <div class="panel" id="hand-panel">
      <h3>Your hand</h3>
      <div id="hand-summary" style="font-size:0.85rem;color:var(--muted);margin-bottom:0.4rem"></div>
      <ul id="hand-list" style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:0.25rem"></ul>
      <div id="hand-empty" style="color:var(--muted);font-style:italic;display:none">No cards.</div>
    </div>
  `;
  return { update: (state) => update(root, state) };
}

function update(root: HTMLElement, state: ClientState): void {
  const summary = root.querySelector<HTMLElement>("#hand-summary")!;
  const list = root.querySelector<HTMLElement>("#hand-list")!;
  const empty = root.querySelector<HTMLElement>("#hand-empty")!;
  list.innerHTML = "";
  summary.textContent = "";
  empty.style.display = "none";

  const me: GamePlayer | null = playerByName(state.game, state.you);
  if (!me) {
    summary.textContent = "Not in game.";
    return;
  }

  const bits: string[] = [];
  if (me.has_coin) bits.push("💰 coin");
  if (me.accredited) bits.push("★ accredited");
  else if (me.trying_accreditation) bits.push("trying for accreditation");
  if (me.jewels.length) bits.push(`jewels: ${me.jewels.join(", ")}`);
  if (me.status && me.status !== "normal") {
    bits.push(`${me.status} (${me.status_turns_remaining} turns left)`);
  }
  summary.textContent = bits.join(" · ");

  const hand = me.hand ?? [];
  if (hand.length === 0) {
    empty.style.display = "block";
    return;
  }
  // Sort by category then name for stable display.
  const sorted = [...hand].sort((a, b) => {
    const ca = a.category ?? "";
    const cb = b.category ?? "";
    if (ca !== cb) return ca.localeCompare(cb);
    return a.name.localeCompare(b.name);
  });
  for (const c of sorted) {
    list.appendChild(cardRow(c));
  }
}

function cardRow(c: Card): HTMLElement {
  const li = document.createElement("li");
  li.style.display = "flex";
  li.style.alignItems = "center";
  li.style.gap = "0.4rem";
  li.style.fontSize = "0.85rem";
  li.style.padding = "0.25rem 0.4rem";
  li.style.background = "var(--panel-2)";
  li.style.border = "1px solid var(--border)";
  li.style.borderRadius = "4px";

  // The card's own art, at row scale — the same icon the reveal modal shows,
  // so a card is recognisable in the hand without reading the name.
  const icon = document.createElement("span");
  icon.className = "hand-card-icon";
  icon.innerHTML = towerCardIcon(c.name, 18);
  icon.title = shortCategoryLabel(c.category);
  li.appendChild(icon);

  const tag = document.createElement("span");
  tag.textContent = shortCategory(c.category);
  tag.style.fontSize = "0.65rem";
  tag.style.color = "var(--muted)";
  tag.style.minWidth = "2.6em";
  li.appendChild(tag);

  const name = document.createElement("span");
  name.textContent = c.name;
  name.style.flex = "1";
  // What the card does, on hover. The turn panel only lists cards you can play
  // *now*, so this is where you find out what a card is waiting for.
  const copy = towerCardCopy(c.name);
  if (copy.description) name.title = copy.description;
  li.appendChild(name);

  if (typeof c.value === "number" && c.value != 0) {
    const val = document.createElement("span");
    val.textContent = `${c.value}`;
    val.style.color = "var(--accent)";
    li.appendChild(val);
  }
  return li;
}

function shortCategory(cat: string | null | undefined): string {
  switch (cat) {
    case "weapon": return "WPN";
    case "burglary": return "BRG";
    case "traversal": return "TRV";
    case "utility": return "UTL";
    case "custom": return "CST";
    default: return "—";
  }
}

/** Spelled-out category, used as the icon's hover title. */
function shortCategoryLabel(cat: string | null | undefined): string {
  switch (cat) {
    case "weapon": return "Weapon";
    case "burglary": return "Burglary tool";
    case "traversal": return "Traversal";
    case "utility": return "Utility";
    case "custom": return "Special";
    default: return "Card";
  }
}
