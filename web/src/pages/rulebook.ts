/**
 * Interactive bits of the rulebook: collapsible sections and the card browser.
 *
 * The prose lives in ``rules.html`` so it can be read and edited as text. This
 * file adds the two things prose can't do on its own — letting a reader open
 * only the section they came for, and building the deck lists from the decks
 * the server actually deals from.
 */
import { jewelLabel, ravenCardIcon, towerCardIcon } from "../ui/card_art.js";
import {
  ravenCardCopy, summonsLocationLabel, towerCardCopy, type CardCopy,
} from "../ui/card_descriptions.js";
import { renderPageNav } from "./nav.js";
import { mountToc } from "./toc.js";

// ---------------------------------------------------------------------------
// Card data, as served from data/*.json
// ---------------------------------------------------------------------------

interface TowerEntry {
  name: string;
  category: string;
  count?: number;
  value?: number;
  defender_only?: boolean;
  effect_key?: string;
}

interface RavenEntry {
  effect_key: string;
  count?: number;
  params?: Record<string, unknown>;
}

/** One row of the browser: a card, however many copies of it there are. */
interface Entry {
  key: string;
  title: string;
  description: string;
  icon: string;
  count: number;
  /** Extra lines under the description — value, defender-only, and so on. */
  facts: string[];
}

const TOWER_CATEGORIES: { key: string; label: string; blurb: string }[] = [
  {
    key: "burglary",
    label: "Burglary tools",
    blurb: "Played on a jewel attempt to lower the roll you need. Never used up.",
  },
  {
    key: "weapon",
    label: "Weapons",
    blurb: "Committed face-down in a fight. Highest total wins; the cards are spent either way.",
  },
  {
    key: "traversal",
    label: "Rope & ladder",
    blurb: "Your way out of the Beauchamp Tower.",
  },
  {
    key: "utility",
    label: "Utility",
    blurb: "Played from hand at the moment each one calls for.",
  },
  {
    key: "custom",
    label: "Oddities",
    blurb: "One-off cards that bend a rule rather than following one.",
  },
];

/**
 * A short label distinguishing one raven card from its siblings.
 *
 * Ten cards share the title "Summons" and five share "Jewel Glimpse", so the
 * chip has to say *which* — otherwise the grid is a wall of identical tiles.
 */
function ravenVariant(effectKey: string, params: Record<string, unknown>): string {
  switch (effectKey) {
    case "go_to_location": {
      const loc = String(params.location ?? "");
      return loc === "player_choice" ? "Anywhere" : summonsLocationLabel(loc);
    }
    case "go_to_jewel_view":
      return jewelLabel(String(params.jewel ?? "")) || "a jewel";
    case "call_warder_to_post": {
      const post = String(params.post ?? "");
      if (post === "chooser") return "Your choice";
      return post.charAt(0).toUpperCase() + post.slice(1);
    }
    default:
      return "";
  }
}

/** Order the raven families so the ones you'll meet most sit at the top. */
function ravenFamilyLabel(effectKey: string): string {
  const copy = ravenCardCopy(effectKey, {});
  return copy.title;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * One group of cards: a heading, a grid of chips, and a shared detail panel.
 *
 * The detail panel sits below the grid rather than inside a chip so that
 * opening a long description doesn't stretch one grid column and reflow every
 * other tile on the row.
 */
function renderGroup(
  host: HTMLElement, label: string, blurb: string, entries: Entry[], deckSize: number,
): void {
  if (entries.length === 0) return;
  const group = el("div", "card-browser-group");
  group.appendChild(el("h4", undefined, label));
  const copies = entries.reduce((n, e) => n + e.count, 0);
  const note = el("p", "card-group-note");
  note.textContent =
    `${entries.length} ${entries.length === 1 ? "card" : "different cards"}, ` +
    `${copies} of the ${deckSize} in the deck. ${blurb}`;
  group.appendChild(note);

  const grid = el("div", "card-grid");
  const detail = el("div", "card-detail");
  detail.hidden = true;

  let openChip: HTMLButtonElement | null = null;

  for (const entry of entries) {
    const chip = el("button", "card-chip") as HTMLButtonElement;
    chip.type = "button";
    chip.setAttribute("aria-expanded", "false");

    const art = el("span", "card-chip-art");
    art.innerHTML = entry.icon;
    chip.appendChild(art);
    chip.appendChild(el("span", "card-chip-name", entry.title));
    chip.appendChild(el("span", "card-chip-count", `×${entry.count}`));

    chip.addEventListener("click", () => {
      // Clicking the open chip closes it, so the panel is dismissible without
      // hunting for a close button.
      if (openChip === chip) {
        chip.setAttribute("aria-expanded", "false");
        openChip = null;
        detail.hidden = true;
        return;
      }
      openChip?.setAttribute("aria-expanded", "false");
      chip.setAttribute("aria-expanded", "true");
      openChip = chip;
      showDetail(detail, entry, deckSize);
    });

    grid.appendChild(chip);
  }

  group.appendChild(grid);
  group.appendChild(detail);
  host.appendChild(group);
}

function showDetail(detail: HTMLElement, entry: Entry, deckSize: number): void {
  detail.hidden = false;
  detail.innerHTML = "";

  const head = el("div", "card-detail-head");
  const art = el("span", "card-chip-art");
  art.innerHTML = entry.icon;
  head.appendChild(art);
  head.appendChild(el("span", "card-detail-title", entry.title));

  const share = ((entry.count / deckSize) * 100).toFixed(1);
  const meta = el("div", "card-detail-meta");
  meta.innerHTML =
    `${entry.count} ${entry.count === 1 ? "copy" : "copies"}<br>` +
    `${share}% of the deck`;
  head.appendChild(meta);
  detail.appendChild(head);

  detail.appendChild(el("p", "card-detail-body", entry.description));
  for (const fact of entry.facts) {
    detail.appendChild(el("p", "card-detail-stat", fact));
  }
}

function towerEntries(cards: TowerEntry[], category: string): Entry[] {
  return cards
    .filter((c) => c.category === category)
    .map((c) => {
      const copy: CardCopy = towerCardCopy(c.name);
      const facts: string[] = [];
      if (c.category === "weapon") {
        facts.push(`Strength ${c.value ?? 0} in a fight.`);
        if (c.defender_only) facts.push("Defence only — you cannot attack with it.");
      } else if (c.category === "burglary") {
        facts.push(`Takes ${c.value ?? 0} off the roll you need to steal a jewel.`);
      }
      return {
        key: c.name,
        title: copy.title || c.name,
        description: copy.description,
        icon: towerCardIcon(c.name, 44),
        count: Number(c.count ?? 1),
        facts,
      };
    })
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title));
}

function ravenGroups(cards: RavenEntry[]): { label: string; entries: Entry[] }[] {
  const families = new Map<string, RavenEntry[]>();
  for (const c of cards) {
    const list = families.get(c.effect_key) ?? [];
    list.push(c);
    families.set(c.effect_key, list);
  }

  const groups: { label: string; entries: Entry[]; copies: number }[] = [];
  for (const [effectKey, list] of families) {
    const entries: Entry[] = list.map((c) => {
      const params = c.params ?? {};
      const copy = ravenCardCopy(effectKey, params);
      const variant = ravenVariant(effectKey, params);
      return {
        key: `${effectKey}:${JSON.stringify(params)}`,
        // Where a family has variants, the chip is labelled by the variant —
        // ten tiles all reading "Summons" would tell a reader nothing.
        title: variant || copy.title,
        description: copy.description,
        icon: ravenCardIcon(effectKey, 44),
        count: Number(c.count ?? 1),
        facts: variant ? [`One of the ${familyCopies(list)} “${copy.title}” cards.`] : [],
      };
    });
    entries.sort((a, b) => b.count - a.count || a.title.localeCompare(b.title));
    groups.push({
      label: ravenFamilyLabel(effectKey),
      entries,
      copies: familyCopies(list),
    });
  }
  // Commonest families first: that ordering is itself information about what
  // the deck is mostly made of.
  groups.sort((a, b) => b.copies - a.copies || a.label.localeCompare(b.label));
  return groups;
}

function familyCopies(list: RavenEntry[]): number {
  return list.reduce((n, c) => n + Number(c.count ?? 1), 0);
}

// ---------------------------------------------------------------------------
// Collapsible sections
// ---------------------------------------------------------------------------

function wireSections(): void {
  const sections = [...document.querySelectorAll<HTMLDetailsElement>("details.rule-section")];
  const toolbar = document.getElementById("rule-toolbar");
  if (toolbar) {
    const expand = el("button", undefined, "Open everything") as HTMLButtonElement;
    expand.type = "button";
    expand.addEventListener("click", () => sections.forEach((s) => (s.open = true)));
    const collapse = el("button", undefined, "Close everything") as HTMLButtonElement;
    collapse.type = "button";
    collapse.addEventListener("click", () => sections.forEach((s) => (s.open = false)));
    toolbar.appendChild(expand);
    toolbar.appendChild(collapse);
  }

  // Opening a section rewrites the address bar so a reader can link somebody
  // straight to the rule they're arguing about. replaceState, not pushState:
  // toggling sections should not fill up the back button.
  for (const section of sections) {
    section.addEventListener("toggle", () => {
      if (section.open && section.id) {
        history.replaceState(null, "", `#${section.id}`);
      }
    });
  }

  // On first load the deck sections are still empty, so opening the target is
  // safe but scrolling to it is not — the page is about to grow by more than a
  // screenful underneath us. The scroll is done once, after the cards land.
  openFromHash({ scroll: false });
  // By the time a hash *changes*, the page is fully built, so that path can
  // scroll immediately. (The listener is handed an Event, hence the wrapper.)
  window.addEventListener("hashchange", () => openFromHash({ scroll: true }));
}

function openFromHash(
  { scroll = true, behavior = "smooth" }: { scroll?: boolean; behavior?: ScrollBehavior } = {},
): void {
  const id = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (!id) return;
  const target = document.getElementById(id);
  if (!(target instanceof HTMLDetailsElement)) return;
  target.open = true;
  if (scroll) target.scrollIntoView({ behavior, block: "start" });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

async function renderCardBrowser(): Promise<void> {
  const towerHost = document.getElementById("tower-deck");
  const ravenHost = document.getElementById("raven-deck");
  if (!towerHost || !ravenHost) return;

  let data: { tower: TowerEntry[]; raven: RavenEntry[] };
  try {
    const res = await fetch("/api/cards");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    // The page is served by the same process that serves this endpoint, so a
    // failure here means the server is down — say so rather than leaving two
    // empty headings and no explanation.
    const message = `Couldn't load the deck lists (${err instanceof Error ? err.message : err}). ` +
      `They're read from the running server, so this needs the game server up.`;
    towerHost.appendChild(el("p", "page-note is-warning", message));
    return;
  }

  const towerSize = data.tower.reduce((n, c) => n + Number(c.count ?? 1), 0);
  const ravenSize = data.raven.reduce((n, c) => n + Number(c.count ?? 1), 0);

  for (const el2 of document.querySelectorAll<HTMLElement>("[data-deck-size='tower']")) {
    el2.textContent = String(towerSize);
  }
  for (const el2 of document.querySelectorAll<HTMLElement>("[data-deck-size='raven']")) {
    el2.textContent = String(ravenSize);
  }

  for (const cat of TOWER_CATEGORIES) {
    renderGroup(towerHost, cat.label, cat.blurb, towerEntries(data.tower, cat.key), towerSize);
  }
  for (const group of ravenGroups(data.raven)) {
    renderGroup(ravenHost, group.label, "", group.entries, ravenSize);
  }
}

renderPageNav("rules");
wireSections();
// After wireSections, so the contents panel sees the sections in the state the
// hash has already put them in.
mountToc();
void renderCardBrowser().then(() => {
  // Now the page is its final height, so a link straight to a deck section
  // lands where it should. An instant jump, not a smooth one: this is the
  // page arriving at its destination, not travelling to it.
  openFromHash({ behavior: "auto" });
});
