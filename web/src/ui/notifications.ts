/**
 * Notifications overlay: card-shaped modals for tower/raven draws plus a
 * toast queue for transient story beats (accreditation, coin pickup, jewel
 * theft outcomes, combat results, raven landing effects, …).
 *
 * Two surfaces:
 *
 *   - **Modals** (centre-screen, blocking-feel): driven by ``tower_card_drawn``
 *     events for the local player, and by ``state.game.active_raven_notice``
 *     for raven cards. Tower modals are personal (only the drawer sees them);
 *     raven modals are shared (anyone may dismiss → the server clears the
 *     notice for everyone via ``dismiss_raven_notice``).
 *
 *   - **Toasts** (corner): short-lived strips for events that anyone watching
 *     should notice but don't warrant blocking interaction.
 *
 * Multiple tower modals queue locally so the player can dismiss them one at
 * a time.
 */
import type { WsClient } from "../net/ws.js";
import type { Card, ClientState, GameSnapshot, RavenNotice } from "../state.js";
import { ravenCardCopy, towerCardCopy } from "./card_descriptions.js";
import {
  ravenCardBack, ravenCardIcon, towerCardBack, towerCardIcon,
} from "./card_art.js";
import { hideBoardTooltip } from "../board/render.js";

interface TowerModal {
  cardId: string;
  cardName: string;
  /** Combat / burglary value, when the card has one. */
  value: number;
  category: string | null;
  /** Flipped face-up by the player. Lives here, not in the DOM, so a snapshot
   *  arriving mid-modal doesn't turn the card back over. */
  revealed: boolean;
}

interface ToastSpec {
  id: number;
  text: string;
  flavor: "info" | "good" | "bad" | "raven" | "tower";
  ttlMs: number;
}

const TOAST_DEFAULT_TTL = 4500;
const TOAST_LONG_TTL = 7000;

export function mountNotifications(
  ws: WsClient,
  state: ClientState,
  onChange: () => void,
): { update: () => void } {
  // ---- DOM scaffolding ----------------------------------------------------
  const overlay = document.createElement("div");
  overlay.id = "notifications-overlay";
  overlay.className = "notifications-overlay";
  overlay.innerHTML = `
    <div class="notif-modal-slot" id="notif-modal-slot"></div>
    <div class="notif-toast-stack" id="notif-toast-stack"></div>
  `;
  document.body.appendChild(overlay);
  const modalSlot = overlay.querySelector<HTMLElement>("#notif-modal-slot")!;
  const toastStack = overlay.querySelector<HTMLElement>("#notif-toast-stack")!;

  // ---- Tower modal queue (drawer only) -----------------------------------
  const towerQueue: TowerModal[] = [];
  // De-dupe by card id since a single event may fan out via multiple listeners
  // and we never want the same modal twice in the queue.
  const queuedIds = new Set<string>();

  function pushTowerModal(
    cardId: string, cardName: string, value: number, category: string | null,
  ): void {
    if (queuedIds.has(cardId)) return;
    queuedIds.add(cardId);
    towerQueue.push({ cardId, cardName, value, category, revealed: false });
    renderModal();
  }

  function popTowerModal(cardId: string): void {
    const idx = towerQueue.findIndex((m) => m.cardId === cardId);
    if (idx >= 0) towerQueue.splice(idx, 1);
    queuedIds.delete(cardId);
    renderModal();
  }

  // ---- Toast queue --------------------------------------------------------
  let toastSeq = 0;
  const toasts: ToastSpec[] = [];
  function pushToast(
    text: string,
    flavor: ToastSpec["flavor"] = "info",
    ttlMs = TOAST_DEFAULT_TTL,
  ): void {
    const t: ToastSpec = { id: ++toastSeq, text, flavor, ttlMs };
    toasts.push(t);
    if (toasts.length > 6) toasts.shift();
    renderToasts();
    window.setTimeout(() => {
      const i = toasts.findIndex((x) => x.id === t.id);
      if (i >= 0) {
        toasts.splice(i, 1);
        renderToasts();
      }
    }, ttlMs);
  }

  // ---- Render -------------------------------------------------------------
  function renderModal(): void {
    const hadModal = modalSlot.childElementCount > 0;
    modalSlot.innerHTML = "";
    const game = state.game;
    // A modal covering the board strands the last board tooltip on screen: the
    // pointer never moves off the square that spawned it, so nothing hides it.
    const opening = () => { if (!hadModal) hideBoardTooltip(); };

    // Raven modal takes precedence — public, blocking-style.
    const notice = game?.active_raven_notice ?? null;
    if (notice) {
      opening();
      // Reveal is server state, not a local flip: everyone turns the card over
      // together, and the effect doesn't fire until they do.
      modalSlot.appendChild(renderRavenModal(notice, ws, state.you));
      return;
    }
    // Then any queued tower modals (drawer-only).
    if (towerQueue.length > 0) {
      opening();
      const top = towerQueue[0];
      modalSlot.appendChild(renderTowerModal(
        top,
        () => popTowerModal(top.cardId),
        () => { top.revealed = true; renderModal(); },
      ));
    }
  }

  function renderToasts(): void {
    toastStack.innerHTML = "";
    for (const t of toasts) {
      const el = document.createElement("div");
      el.className = `notif-toast notif-toast-${t.flavor}`;
      el.textContent = t.text;
      toastStack.appendChild(el);
    }
  }

  // ---- Event subscriptions -----------------------------------------------
  ws.on("tower_card_drawn", (p: any) => {
    const drawer = p?.player as string | undefined;
    if (!drawer) return;
    if (drawer === state.you) {
      // Look up the card name from your hand (or recently-discarded if it was
      // a raven side-effect that auto-played). Fall back to the id.
      const cardId = String(p?.card ?? "");
      const card = cardFromId(state.game, cardId);
      const cardName = card?.name ?? slugToName(cardId);
      pushTowerModal(cardId, cardName, card?.value ?? 0, card?.category ?? null);
    } else {
      pushToast(`${drawer} drew a tower card.`, "tower");
    }
  });

  ws.on("coin_picked_up", (p: any) => {
    if (p?.player) pushToast(`${p.player} picked up the coin! 💰`, "good");
  });
  ws.on("accredited", (p: any) => {
    if (!p?.player) return;
    const via = p.via === "tower_pass"
      ? "with a Tower Pass"
      : p.via === "odd_roll"
        ? "(rolled odd)"
        : p.via === "space_action"
          ? "at Queen's House"
          : "";
    pushToast(`${p.player} is now accredited ${via}`.trim() + ".", "good");
  });
  ws.on("accreditation_failed", (p: any) => {
    if (p?.player) pushToast(`${p.player} failed accreditation — turn ends.`, "bad");
  });
  ws.on("trying_accreditation", (p: any) => {
    if (p?.player) pushToast(`${p.player} approaches Queen's House.`, "info");
  });

  ws.on("jewel_acquired", (p: any) => {
    if (p?.player && p?.jewel)
      pushToast(`${p.player} stole the ${prettyJewel(p.jewel)}! 💎`, "good", TOAST_LONG_TTL);
  });
  ws.on("jewel_attempt", (p: any) => {
    if (!p?.player) return;
    if (p.success) return; // covered by jewel_acquired
    pushToast(
      `${p.player} fumbled the ${prettyJewel(p.jewel)} (rolled ${p.roll}, needed ${p.threshold}).`,
      "bad",
    );
  });
  ws.on("jewel_auto_acquired", (p: any) => {
    if (p?.player && p?.jewel)
      pushToast(`${p.player} grabbed a loose ${prettyJewel(p.jewel)}! 💎`, "good", TOAST_LONG_TTL);
  });

  ws.on("combat_started", (p: any) => {
    if (p?.attacker && p?.defender)
      pushToast(`Combat: ${p.attacker} attacks ${p.defender}.`, "info");
  });
  ws.on("combat_resolved", (p: any) => {
    if (!p?.winner) return;
    const jewels = Array.isArray(p.jewels_taken) ? p.jewels_taken.length : 0;
    const spoils: string[] = [];
    if (jewels) spoils.push(`${jewels} jewel${jewels === 1 ? "" : "s"}`);
    if (p.coin_taken) spoils.push("a coin");
    pushToast(
      `${p.attacker ?? "?"} ${p.attacker_total ?? 0} vs ${p.defender ?? "?"} ${p.defender_total ?? 0}` +
        ` — ${p.winner} wins!` +
        (spoils.length ? ` Takes ${spoils.join(" and ")} from ${p.loser}.` : "") +
        ` ${p.loser} goes to the Hospital.`,
      "good",
      TOAST_LONG_TTL,
    );
  });
  ws.on("sanctuary_taken", (p: any) => {
    if (!p?.defender) return;
    pushToast(
      `${p.defender} flees to Sanctuary! Weapons spent all the same — ` +
        `${p.attacker} loses ${p.attacker_cards_lost ?? 0}, ` +
        `${p.defender} loses ${p.defender_cards_lost ?? 0}. Both redraw.`,
      "info",
      TOAST_LONG_TTL,
    );
  });

  ws.on("firecrackers", (p: any) => {
    const aff = Array.isArray(p?.affected) && p.affected.length
      ? ` On notice: ${p.affected.join(", ")}.`
      : "";
    pushToast(`Firecrackers in the White Tower!${aff}`, "raven", TOAST_LONG_TTL);
  });
  ws.on("firecrackers_racked", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} stayed in the White Tower — off to the Rack.`, "bad", TOAST_LONG_TTL);
  });
  ws.on("firecrackers_escaped", (p: any) => {
    if (p?.player) pushToast(`${p.player} slipped out of the White Tower.`, "good");
  });

  ws.on("lassoed", (p: any) => {
    if (p?.roper && p?.target)
      pushToast(`${p.roper} lassoed ${p.target}!`, "info");
  });
  ws.on("metallicity", () => {
    pushToast(`Metallicity! Jewels scattered across the Tower.`, "raven", TOAST_LONG_TTL);
  });
  ws.on("pecked_by_ravens", (p: any) => {
    if (p?.player) pushToast(`${p.player} was pecked by ravens — to the hospital.`, "bad");
  });
  ws.on("ghost", (p: any) => {
    if (p?.player) pushToast(`${p.player} was spooked by a ghost — Chapel Royal.`, "raven");
  });
  ws.on("bowyer_questioning", (p: any) => {
    if (p?.player) pushToast(`${p.player} hauled in for questioning at Bowyer Tower.`, "bad");
  });
  ws.on("governors_tea", (p: any) => {
    if (p?.player) pushToast(`${p.player} summoned to Governor's tea.`, "info");
  });
  ws.on("beauchamp_imprisonment", (p: any) => {
    if (p?.player) pushToast(`${p.player} imprisoned in Beauchamp Tower.`, "bad");
  });
  ws.on("rack_sender_triggered", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} is dragged off to the Rack!`, "bad", TOAST_LONG_TTL);
  });
  ws.on("resting_on_bench", (p: any) => {
    if (p?.player) pushToast(`${p.player} rests on a bench — misses a turn.`, "info");
  });
  ws.on("miss_turn_on_landing", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} stops at the ${p.label ?? "square"} — misses a turn.`, "info");
  });
  ws.on("rack_expired", (p: any) => {
    if (p?.player) pushToast(`${p.player} is released from the Rack.`, "good");
  });
  ws.on("rack_coin_lost", (p: any) => {
    if (p?.player) pushToast(`${p.player} forfeited a coin on the Rack.`, "bad");
  });
  ws.on("rack_hand_lost", (p: any) => {
    if (p?.player) pushToast(`${p.player} lost ${p.count ?? 0} card(s) on the Rack.`, "bad");
  });
  ws.on("stopped_forfeit", (p: any) => {
    if (p?.player) {
      const items: string[] = [];
      if (Array.isArray(p.jewels) && p.jewels.length) items.push(`${p.jewels.length} jewel(s)`);
      if (Array.isArray(p.weapons) && p.weapons.length) items.push(`${p.weapons.length} weapon(s)`);
      pushToast(
        `${p.player} stopped & searched — forfeited ${items.join(" + ") || "nothing"}.`,
        "bad",
      );
    }
  });
  ws.on("clerk_tea", () => {
    pushToast(`Clerk's tea exception — players sent to Queen's House.`, "raven");
  });
  ws.on("disguise_played", (p: any) => {
    if (p?.player) pushToast(`${p.player} slipped past in disguise.`, "info");
  });
  ws.on("pardoned", (p: any) => {
    if (p?.player) pushToast(`${p.player} pardoned (${p.pardon_kind ?? "?"}).`, "good");
  });
  ws.on("framed", (p: any) => {
    if (p?.framer && p?.framed)
      pushToast(`${p.framer} framed ${p.framed} with a Confession!`, "info");
  });
  ws.on("slow_escaped", (p: any) => {
    if (p?.player) pushToast(`${p.player} escaped the Tower with their haul!`, "good", TOAST_LONG_TTL);
  });
  ws.on("three_doubles_bloody_tower", (p: any) => {
    if (p?.player) pushToast(`${p.player} rolled three doubles — off to the Bloody Tower.`, "bad");
  });
  ws.on("weapons_surrendered", (p: any) => {
    const n = p?.count ?? (p?.cards ?? []).length;
    if (p?.player && n > 0)
      pushToast(
        `${p.player} surrendered ${n} weapon${n === 1 ? "" : "s"} at the Broad Arrow Tower.`,
        "bad",
      );
  });
  ws.on("card_swapped", (p: any) => {
    if (p?.player && p?.target)
      pushToast(`${p.player} swapped a card with ${p.target}.`, "info");
  });
  ws.on("sent_to_space", (p: any) => {
    if (!p?.player) return;
    const why = p.label ? `${p.label}: ` : "";
    pushToast(
      `${why}${p.player} is marched off to ${p.dst}` +
        (p.misses_turn ? " — and misses a turn." : "."),
      p.misses_turn ? "bad" : "info",
    );
  });
  ws.on("miss_turn_queued", (p: any) => {
    if (p?.player)
      pushToast(`${p.label ?? "Miss a turn"} — ${p.player} loses their next turn.`, "bad");
  });
  // Development aid: the engine emits this for any space ``action.key`` it has
  // no handler for. Without a toast an unimplemented action looks exactly like
  // a square that does nothing, which is how several gaps went unnoticed.
  ws.on("unhandled_space_action", (p: any) => {
    pushToast(
      `Unimplemented space action "${p?.key ?? "?"}" on ${p?.space ?? "?"}.`,
      "bad",
      TOAST_LONG_TTL,
    );
  });

  // Re-render modal whenever ws sends a snapshot (raven notice may have
  // appeared/cleared) or whenever the WS state changes.
  ws.on("__snapshot__", () => {
    queueMicrotask(() => {
      renderModal();
      onChange();
    });
  });
  // Also rerender on raven_card_drawn / dismissed for snappier feel — the
  // snapshot will follow but this avoids a flash.
  ws.on("raven_card_drawn", () => queueMicrotask(renderModal));
  ws.on("raven_notice_dismissed", () => queueMicrotask(renderModal));

  return {
    update: () => {
      renderModal();
    },
  };
}

// ---- Modal builders --------------------------------------------------------

/**
 * Wrap a card face in the flip scaffolding. The card lands face-down and stays
 * there until the player turns it over themselves — the reveal is the moment,
 * so it shouldn't happen while they're still looking at the board.
 *
 * ``revealed`` has to be passed in rather than tracked in the DOM: renderModal
 * rebuilds this markup on every snapshot, so a card revealed mid-turn would
 * flip back over the moment anything else happened in the game.
 *
 * The flip itself is a CSS keyframe gated on ``.is-revealed``, so there's no JS
 * timer to leak if the modal is dismissed mid-animation.
 */
function flipCard(
  faceHtml: string,
  flavor: "tower" | "raven",
  revealed: boolean,
  canReveal = true,
): string {
  const crest = flavor === "raven" ? ravenCardBack(72) : towerCardBack(72);
  const backLabel = flavor === "raven" ? "Raven" : "Tower";
  return `
    <div class="notif-card-flip">
      <div class="notif-card-inner${revealed ? " is-revealed" : ""}">
        <button type="button" class="notif-card-back notif-card-back-${flavor}"
                data-action="reveal" ${revealed || !canReveal ? "disabled" : ""}
                aria-label="Turn the card over">
          <span class="notif-card-crest">${crest}</span>
          <span class="notif-card-back-label">${backLabel}</span>
        </button>
        ${faceHtml}
      </div>
    </div>
  `;
}

/**
 * The card face: name boldly across the top, a large icon, then the value and
 * any explanatory text. Shared by both flavours so they stay in step.
 */
function cardFace(opts: {
  flavor: "tower" | "raven";
  title: string;
  icon: string;
  value?: string | null;
  description: string;
  meta?: string | null;
}): string {
  return `
    <div class="notif-card notif-card-${opts.flavor}">
      <h3 class="notif-card-title">${escapeHtml(opts.title)}</h3>
      <div class="notif-card-art">${opts.icon}</div>
      ${opts.value ? `<div class="notif-card-value">${escapeHtml(opts.value)}</div>` : ""}
      <p class="notif-card-body">${escapeHtml(opts.description)}</p>
      ${opts.meta ? `<div class="notif-card-meta">${escapeHtml(opts.meta)}</div>` : ""}
    </div>
  `;
}

/** "Weapon · 10" / "Burglary tool · 2" — empty for cards with no value. */
function valueLine(category: string | null, value: number): string | null {
  if (!value) return null;
  const label =
    category === "weapon" ? "Weapon"
    : category === "burglary" ? "Burglary tool"
    : "Value";
  return `${label} · ${value}`;
}

/**
 * The modal's footer. Before the card is turned over the only action offered is
 * "Reveal" — dismissing an unseen card would throw away the one moment the
 * modal exists for.
 */
function modalFoot(revealed: boolean, dismissLabel: string, hint: string): string {
  if (!revealed) {
    return `
      <div class="notif-modal-foot">
        <button class="notif-dismiss" data-action="reveal">Reveal</button>
        <span class="notif-modal-hint">Turn the card over.</span>
      </div>
    `;
  }
  return `
    <div class="notif-modal-foot">
      <button class="notif-dismiss" data-action="dismiss">${escapeHtml(dismissLabel)}</button>
      <span class="notif-modal-hint">${escapeHtml(hint)}</span>
    </div>
  `;
}

/** Wire both reveal affordances — the footer button and the card back itself. */
function bindReveal(wrap: HTMLElement, onReveal: () => void): void {
  for (const el of wrap.querySelectorAll<HTMLElement>('[data-action="reveal"]')) {
    el.addEventListener("click", onReveal);
  }
}

function renderRavenModal(
  notice: RavenNotice,
  ws: WsClient,
  you: string | null,
): HTMLElement {
  const copy = ravenCardCopy(notice.effect_key, notice.params);
  const revealed = notice.revealed;
  const isDrawer = you === notice.drawer;
  const wrap = document.createElement("div");
  wrap.className = "notif-modal-backdrop";

  // Only the drawer turns their own card over; everyone else waits and then
  // sees the same face at the same moment.
  const foot = revealed
    ? modalFoot(true, "Dismiss for everyone",
        isDrawer ? "You drew this." : "Anyone can dismiss when they're ready.")
    : isDrawer
      ? `<div class="notif-modal-foot">
           <button class="notif-dismiss" data-action="reveal">Reveal</button>
           <span class="notif-modal-hint">Turn it over to see what happens.</span>
         </div>`
      : `<div class="notif-modal-foot">
           <span class="notif-modal-hint" style="text-align:left">
             Waiting for ${escapeHtml(notice.drawer)} to turn the card over…
           </span>
         </div>`;

  wrap.innerHTML = `
    <div class="notif-modal notif-modal-raven" role="dialog" aria-modal="true">
      <div class="notif-modal-header">Raven card triggered</div>
      ${flipCard(cardFace({
        flavor: "raven",
        title: copy.title,
        icon: ravenCardIcon(notice.effect_key),
        description: copy.description,
        meta: `Drawn by ${notice.drawer}`,
      }), "raven", revealed, isDrawer)}
      ${foot}
    </div>
  `;
  if (isDrawer && !revealed) {
    bindReveal(wrap, () => {
      ws.send("reveal_raven_notice", { username: you }).catch(() => {});
    });
  }
  wrap.querySelector<HTMLButtonElement>('[data-action="dismiss"]')?.addEventListener("click", () => {
    ws.send("dismiss_raven_notice", { card_id: notice.card_id }).catch(() => {
      // Silently swallow — the snapshot will reflect reality regardless.
    });
  });
  return wrap;
}

function renderTowerModal(
  modal: TowerModal,
  onDismiss: () => void,
  onReveal: () => void,
): HTMLElement {
  const copy = towerCardCopy(modal.cardName);
  const wrap = document.createElement("div");
  wrap.className = "notif-modal-backdrop";
  wrap.innerHTML = `
    <div class="notif-modal notif-modal-tower" role="dialog" aria-modal="true">
      <div class="notif-modal-header">Tower card acquired!</div>
      ${flipCard(cardFace({
        flavor: "tower",
        title: copy.title,
        icon: towerCardIcon(modal.cardName),
        value: valueLine(modal.category, modal.value),
        description: copy.description,
      }), "tower", modal.revealed)}
      ${modalFoot(modal.revealed, "Dismiss", "")}
    </div>
  `;
  bindReveal(wrap, onReveal);
  wrap.querySelector<HTMLButtonElement>('[data-action="dismiss"]')?.addEventListener("click", onDismiss);
  return wrap;
}

// ---- Helpers ---------------------------------------------------------------

/** Find the full card in the viewer's hand — only they can see it. */
function cardFromId(game: GameSnapshot | null, cardId: string): Card | null {
  if (!game) return null;
  for (const p of game.players) {
    for (const c of p.hand as Card[]) {
      if (c.id === cardId) return c;
    }
  }
  return null;
}

function slugToName(cardId: string): string {
  const parts = cardId.split(":");
  const slug = parts.length >= 2 ? parts[1] : cardId;
  return slug
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function prettyJewel(j: string): string {
  switch (j) {
    case "sword": return "Sword";
    case "sceptre": return "Sceptre";
    case "orb": return "Orb";
    case "crown_prince_of_wales": return "Prince of Wales' Crown";
    case "crown_st_edward": return "St Edward's Crown";
    default: return j.replace(/_/g, " ");
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}
