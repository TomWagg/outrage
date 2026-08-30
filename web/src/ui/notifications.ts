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
import type {
  Card, ClientState, ConfinementNotice, GameSnapshot, RavenNotice,
} from "../state.js";
import { confinementCopy, ravenCardCopy, towerCardCopy } from "./card_descriptions.js";
import {
  confinementIcon, ravenCardBack, ravenCardIcon, towerCardBack, towerCardIcon,
} from "./card_art.js";

/** A completed card trade, waiting to be shown to the player who made it. */
interface TradeModal {
  given: number;
  /** Card ids drawn back. Resolved to names at render time — the event beats
   *  the snapshot, so they aren't in our hand yet when this arrives. */
  received: string[];
  shortBy: number;
}
import { hideBoardTooltip } from "../board/render.js";
import { afterReveals, revealsHeld } from "./reveal_gate.js";

interface TowerModal {
  cardId: string;
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

  /**
   * Subscribe to a server event, but never fire ahead of the dice.
   *
   * Every notification in here narrates something the roll caused, so each one
   * would otherwise announce the outcome while the dice are still tumbling.
   * Routing them all through the reveal gate keeps the story in order without
   * each handler having to remember.
   */
  function on(event: string, handler: (payload: any) => void): void {
    ws.on(event, (p: any) => afterReveals(() => handler(p)));
  }

  // ---- Tower modal queue (drawer only) -----------------------------------
  const towerQueue: TowerModal[] = [];
  // De-dupe by card id since a single event may fan out via multiple listeners
  // and we never want the same modal twice in the queue.
  const queuedIds = new Set<string>();

  function pushTowerModal(cardId: string): void {
    if (queuedIds.has(cardId)) return;
    queuedIds.add(cardId);
    towerQueue.push({ cardId, revealed: false });
    renderModal();
  }

  function popTowerModal(cardId: string): void {
    const idx = towerQueue.findIndex((m) => m.cardId === cardId);
    if (idx >= 0) towerQueue.splice(idx, 1);
    queuedIds.delete(cardId);
    renderModal();
  }

  // ---- Card-trade result (the trader only) --------------------------------
  let tradeModal: TradeModal | null = null;

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
    // Then the confinement banner — after the raven, so a raven card that put
    // somebody away is read first and the sentence lands second.
    const confinement = game?.active_confinement_notice ?? null;
    if (confinement) {
      opening();
      modalSlot.appendChild(renderConfinementModal(confinement, ws, state.you));
      return;
    }
    // A trade can hand back several cards at once, so it gets one modal listing
    // them rather than a queue of single-card flips to click through.
    if (tradeModal) {
      opening();
      const spec = tradeModal;
      modalSlot.appendChild(renderTradeModal(
        spec, state.game, () => { tradeModal = null; renderModal(); },
      ));
      return;
    }
    // Then any queued tower modals (drawer-only).
    if (towerQueue.length > 0) {
      opening();
      const top = towerQueue[0];
      modalSlot.appendChild(renderTowerModal(
        top,
        cardFromId(state.game, top.cardId),
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
  on("tower_card_drawn", (p: any) => {
    const drawer = p?.player as string | undefined;
    if (!drawer) return;
    if (drawer === state.you) {
      // Only the id: the card's details are resolved at render time, because
      // events arrive *before* the snapshot and the card isn't in our hand yet.
      pushTowerModal(String(p?.card ?? ""));
    } else {
      pushToast(`${drawer} drew a tower card.`, "tower");
    }
  });

  // "Change a card" hands you a fresh tower card without a tower_card_drawn
  // event, so give it the same face-down-then-flip modal as any other draw.
  on("card_changed", (p: any) => {
    if (p?.player === state.you && p?.drawn) pushTowerModal(String(p.drawn));
  });

  on("cards_redrawn", (p: any) => {
    const who = p?.player as string | undefined;
    if (!who) return;
    const givenCount = Number(p?.given_count ?? 0);
    const received: string[] = Array.isArray(p?.received) ? p.received.map(String) : [];
    if (who === state.you) {
      tradeModal = {
        given: givenCount,
        received,
        shortBy: Number(p?.short_by ?? 0),
      };
      renderModal();
    } else {
      // SECRET: what came back is the trader's business; the counts are public.
      pushToast(
        `${who} traded ${givenCount} card${givenCount === 1 ? "" : "s"} for ` +
        `${received.length} — and stayed put.`,
        "tower",
      );
    }
  });

  on("mass_accretor_stole", (p: any) => {
    if (!p?.player) return;
    // Weapons are committed face-up, so naming the card is fair game.
    const name = String(p.card ?? "").split(":")[1]?.replace(/_/g, " ") || "a weapon";
    pushToast(
      `${p.player} rips ${name} from ${p.attacker} and swings it back!`,
      "good",
      TOAST_LONG_TTL,
    );
  });

  on("coin_picked_up", (p: any) => {
    if (!p?.player) return;
    const left = typeof p.remaining === "number" && typeof p.total === "number"
      ? ` (${p.remaining} of ${p.total} left)`
      : "";
    pushToast(`${p.player} picked up a coin! 💰${left}`, "good");
  });
  on("accreditation_retry", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} rolled a double at Queen's House — another go.`, "info");
  });
  on("split_unavailable", (p: any) => {
    if (p?.player)
      pushToast(`Nobody else can be moved — ${p.player} takes all ${p.total ?? 7}.`, "info");
  });
  on("summons_declined", (p: any) => {
    if (p?.player) pushToast(`${p.player} refused the summons — misses a turn.`, "bad");
  });
  on("accredited", (p: any) => {
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
  on("accreditation_failed", (p: any) => {
    if (p?.player) pushToast(`${p.player} failed accreditation — turn ends.`, "bad");
  });
  on("trying_accreditation", (p: any) => {
    if (p?.player) pushToast(`${p.player} approaches Queen's House.`, "info");
  });

  on("jewel_acquired", (p: any) => {
    if (p?.player && p?.jewel)
      pushToast(`${p.player} stole the ${prettyJewel(p.jewel)}! 💎`, "good", TOAST_LONG_TTL);
  });
  on("jewel_attempt", (p: any) => {
    if (!p?.player) return;
    if (p.success) return; // covered by jewel_acquired
    pushToast(
      `${p.player} fumbled the ${prettyJewel(p.jewel)} (rolled ${p.roll}, needed ${p.threshold}).`,
      "bad",
    );
  });
  on("jewel_auto_acquired", (p: any) => {
    if (p?.player && p?.jewel)
      pushToast(`${p.player} grabbed a loose ${prettyJewel(p.jewel)}! 💎`, "good", TOAST_LONG_TTL);
  });

  on("combat_started", (p: any) => {
    if (p?.attacker && p?.defender)
      pushToast(`Combat: ${p.attacker} attacks ${p.defender}.`, "info");
  });
  on("combat_resolved", (p: any) => {
    if (!p?.winner) return;
    const jewels = Array.isArray(p.jewels_taken) ? p.jewels_taken.length : 0;
    const spoils: string[] = [];
    if (jewels) spoils.push(`${jewels} jewel${jewels === 1 ? "" : "s"}`);
    if (p.coin_taken) spoils.push("a coin");
    // No "?" placeholders: a missing name drops the score line rather than
    // printing punctuation at the player.
    const score = p.attacker && p.defender
      ? `${p.attacker} ${p.attacker_total ?? 0} vs ${p.defender} ${p.defender_total ?? 0} — `
      : "";
    pushToast(
      `${score}${p.winner} wins!` +
        (spoils.length ? ` Takes ${spoils.join(" and ")} from ${p.loser}.` : "") +
        (p.loser ? ` ${p.loser} goes to the Hospital.` : ""),
      "good",
      TOAST_LONG_TTL,
    );
  });
  on("sanctuary_taken", (p: any) => {
    if (!p?.defender) return;
    pushToast(
      `${p.defender} flees to Sanctuary! Weapons spent all the same — ` +
        `${p.attacker} loses ${p.attacker_cards_lost ?? 0}, ` +
        `${p.defender} loses ${p.defender_cards_lost ?? 0}. Both redraw.`,
      "info",
      TOAST_LONG_TTL,
    );
  });

  on("firecrackers", (p: any) => {
    const aff = Array.isArray(p?.affected) && p.affected.length
      ? ` On notice: ${p.affected.join(", ")}.`
      : "";
    pushToast(`Firecrackers in the White Tower!${aff}`, "raven", TOAST_LONG_TTL);
  });
  on("firecrackers_racked", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} stayed in the White Tower — off to the Rack.`, "bad", TOAST_LONG_TTL);
  });
  on("firecrackers_escaped", (p: any) => {
    if (p?.player) pushToast(`${p.player} slipped out of the White Tower.`, "good");
  });

  on("lassoed", (p: any) => {
    if (p?.roper && p?.target)
      pushToast(`${p.roper} lassoed ${p.target}!`, "info");
  });
  on("metallicity", () => {
    pushToast(`Metallicity! Jewels scattered across the Tower.`, "raven", TOAST_LONG_TTL);
  });
  on("pecked_by_ravens", (p: any) => {
    if (p?.player) pushToast(`${p.player} was pecked by ravens — to the hospital.`, "bad");
  });
  on("ghost", (p: any) => {
    if (p?.player) pushToast(`${p.player} was spooked by a ghost — Chapel Royal.`, "raven");
  });
  on("bowyer_questioning", (p: any) => {
    if (p?.player) pushToast(`${p.player} hauled in for questioning at Bowyer Tower.`, "bad");
  });
  on("governors_tea", (p: any) => {
    if (p?.player) pushToast(`${p.player} summoned to Governor's tea.`, "info");
  });
  on("beauchamp_imprisonment", (p: any) => {
    if (p?.player) pushToast(`${p.player} imprisoned in Beauchamp Tower.`, "bad");
  });
  on("confined_on_landing", (p: any) => {
    if (!p?.player) return;
    const verb = p.status === "TORTURED" ? "hauled in for questioning at" : "locked up in";
    pushToast(
      `${p.player} walked into the ${p.label ?? "tower"} — ${verb} it for ${p.turns ?? 3} turns.`,
      "bad",
      TOAST_LONG_TTL,
    );
  });
  on("rack_sender_triggered", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} is dragged off to the Rack!`, "bad", TOAST_LONG_TTL);
  });
  on("resting_on_bench", (p: any) => {
    if (p?.player) pushToast(`${p.player} rests on a bench — misses a turn.`, "info");
  });
  on("miss_turn_on_landing", (p: any) => {
    if (p?.player)
      pushToast(`${p.player} stops at the ${p.label ?? "square"} — misses a turn.`, "info");
  });
  on("rack_expired", (p: any) => {
    if (p?.player) pushToast(`${p.player} is released from the Rack.`, "good");
  });
  on("sent_to_rack", (p: any) => {
    if (!p?.player) return;
    const took: string[] = [];
    if (Array.isArray(p.jewels) && p.jewels.length) took.push(`${p.jewels.length} jewel(s)`);
    if (p.penalty === "coin") took.push("a coin");
    else if (Number(p.cards_taken) > 0) took.push(`${p.cards_taken} card(s)`);
    pushToast(
      `${p.player} is taken to the Rack` + (took.length ? ` — ${took.join(" and ")} confiscated.` : "."),
      "bad",
    );
  });
  on("stopped_forfeit", (p: any) => {
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
  on("clerk_tea", () => {
    pushToast(`Clerk's tea exception — players sent to Queen's House.`, "raven");
  });
  on("disguise_played", (p: any) => {
    if (p?.player) pushToast(`${p.player} slipped past in disguise.`, "info");
  });
  on("pardoned", (p: any) => {
    if (!p?.player) return;
    pushToast(
      p.pardon_kind === "rack"
        ? `${p.player} produced a Rack Pardon — released!`
        : p.pardon_kind === "royal"
          ? `${p.player} produced a Royal Pardon — released!`
          : `${p.player} was pardoned — released!`,
      "good",
    );
  });
  on("framed", (p: any) => {
    if (p?.framer && p?.framed)
      pushToast(`${p.framer} framed ${p.framed} with a Confession!`, "info");
  });
  on("jewels_banked", (p: any) => {
    const n = Array.isArray(p?.jewels) ? p.jewels.length : 0;
    if (p?.player) pushToast(
      n
        ? `${p.player} banked ${n} jewel${n === 1 ? "" : "s"} at the hideout!`
        : `${p.player} slipped out for a fresh hand.`,
      n ? "good" : "info", TOAST_LONG_TTL);
  });
  on("three_doubles_bloody_tower", (p: any) => {
    if (p?.player) pushToast(`${p.player} rolled three doubles — off to the Bloody Tower.`, "bad");
  });
  on("weapons_surrendered", (p: any) => {
    const n = p?.count ?? (p?.cards ?? []).length;
    if (p?.player && n > 0)
      pushToast(
        `${p.player} surrendered ${n} weapon${n === 1 ? "" : "s"} at the Broad Arrow Tower.`,
        "bad",
      );
  });
  // A swap deals a card to *both* sides: the swapper takes a random one from
  // the opponent, the opponent keeps the card they were handed. Either way the
  // card is new to you, so it gets the same face-down-then-flip reveal as a
  // draw. Everyone else just gets the toast.
  on("card_swapped", (p: any) => {
    if (p?.player === state.you) {
      if (p.received) pushTowerModal(String(p.received));
    } else if (p?.target === state.you) {
      if (p.given) pushTowerModal(String(p.given));
    } else if (p?.player && p?.target) {
      pushToast(`${p.player} swapped a card with ${p.target}.`, "info");
    }
  });
  on("sent_to_space", (p: any) => {
    if (!p?.player) return;
    const why = p.label ? `${p.label}: ` : "";
    pushToast(
      `${why}${p.player} is marched off to ${p.dst}` +
        (p.misses_turn ? " — and misses a turn." : "."),
      p.misses_turn ? "bad" : "info",
    );
  });
  on("miss_turn_queued", (p: any) => {
    if (p?.player)
      pushToast(`${p.label ?? "Miss a turn"} — ${p.player} loses their next turn.`, "bad");
  });
  // Development aid: the engine emits this for any space ``action.key`` it has
  // no handler for. Without a toast an unimplemented action looks exactly like
  // a square that does nothing, which is how several gaps went unnoticed.
  on("unhandled_space_action", (p: any) => {
    const which = p?.key ? `"${p.key}"` : "an action";
    pushToast(
      `Unimplemented space action: ${which}` + (p?.space ? ` on ${p.space}` : "") + `.`,
      "bad",
      TOAST_LONG_TTL,
    );
  });

  // Re-render modal whenever ws sends a snapshot (raven notice may have
  // appeared/cleared) or whenever the WS state changes.
  on("__snapshot__", () => {
    queueMicrotask(() => {
      renderModal();
      onChange();
    });
  });
  // Also rerender on raven_card_drawn / dismissed for snappier feel — the
  // snapshot will follow but this avoids a flash.
  on("raven_card_drawn", () => queueMicrotask(renderModal));
  on("raven_notice_dismissed", () => queueMicrotask(renderModal));

  return {
    update: () => {
      // The raven and confinement banners come from snapshot state rather than
      // from an event, so they need the same hold — a raven card raised by the
      // square you just landed on would otherwise appear before the dice stop.
      if (revealsHeld()) {
        afterReveals(renderModal);
        return;
      }
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
  flavor: "tower" | "raven" | "prison";
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

/**
 * The red banner. Public like a raven notice, but dismissable only by the
 * player it happened to — the rest of the table watches it land.
 */
function renderConfinementModal(
  notice: ConfinementNotice,
  ws: WsClient,
  you: string | null,
): HTMLElement {
  const isVictim = you === notice.username;
  const copy = confinementCopy(notice.status, {
    turns: notice.turns,
    cause: notice.cause,
    you: isVictim,
    username: notice.username,
  });
  const wrap = document.createElement("div");
  wrap.className = "notif-modal-backdrop";
  // No flip: this isn't a card being turned over, it's a door closing.
  const foot = isVictim
    ? `<div class="notif-modal-foot">
         <button class="notif-dismiss" data-action="dismiss">Accept your fate</button>
         <span class="notif-modal-hint">Only you can clear this.</span>
       </div>`
    : `<div class="notif-modal-foot">
         <span class="notif-modal-hint" style="text-align:left">
           Waiting for ${escapeHtml(notice.username)} to take it in…
         </span>
       </div>`;
  wrap.innerHTML = `
    <div class="notif-modal notif-modal-prison" role="dialog" aria-modal="true">
      <div class="notif-modal-header">Locked up</div>
      ${cardFace({
        flavor: "prison",
        title: copy.title,
        icon: confinementIcon(notice.status),
        description: copy.description,
        meta: isVictim ? undefined : `${notice.username} is going nowhere`,
      })}
      ${foot}
    </div>
  `;
  wrap.querySelector<HTMLButtonElement>('[data-action="dismiss"]')?.addEventListener("click", () => {
    ws.send("dismiss_confinement_notice", { username: you }).catch(() => {});
  });
  return wrap;
}

/**
 * What a card trade brought back, all in one modal.
 *
 * No flip animation here: with several cards at once a per-card reveal would be
 * a chore, and the interesting information is the set, not the suspense.
 */
function renderTradeModal(
  spec: TradeModal,
  game: GameSnapshot | null,
  onDismiss: () => void,
): HTMLElement {
  const cards = spec.received.map((id) => ({ id, card: cardFromId(game, id) }));
  const wrap = document.createElement("div");
  wrap.className = "notif-modal-backdrop";
  const tiles = cards
    .map(({ id, card }) => {
      const name = card?.name ?? slugToName(id);
      return `<div class="card-tile" style="cursor:default">` +
        (card?.value ? `<span class="card-tile-value">${card.value}</span>` : "") +
        `<span class="card-tile-art">${towerCardIcon(name, 40)}</span>` +
        `<span class="card-tile-name">${escapeHtml(name)}</span>` +
        `</div>`;
    })
    .join("");
  const shortNote = spec.shortBy
    ? `<p class="notif-card-body">The tower deck ran dry — you're ` +
      `${spec.shortBy} card${spec.shortBy === 1 ? "" : "s"} short.</p>`
    : "";
  wrap.innerHTML = `
    <div class="notif-modal notif-modal-tower" role="dialog" aria-modal="true">
      <div class="notif-modal-header">Cards traded</div>
      <p class="notif-card-body">
        You handed in ${spec.given} and drew ${cards.length} back.
      </p>
      ${cards.length
        ? `<div class="card-tile-grid" style="margin-top:0.8rem">${tiles}</div>`
        : `<p class="notif-card-body">Nothing came back.</p>`}
      ${shortNote}
      <div class="notif-modal-foot">
        <button class="notif-dismiss" data-action="dismiss">Right then</button>
        <span class="notif-modal-hint"></span>
      </div>
    </div>
  `;
  wrap.querySelector<HTMLButtonElement>('[data-action="dismiss"]')?.addEventListener("click", onDismiss);
  return wrap;
}

function renderTowerModal(
  modal: TowerModal,
  /** The card from the viewer's hand, once the snapshot has caught up. */
  card: Card | null,
  onDismiss: () => void,
  onReveal: () => void,
): HTMLElement {
  // Resolved per render, not once at queue time: the draw event beats the
  // snapshot, so early on the card isn't in our hand and only the id is known.
  const cardName = card?.name ?? slugToName(modal.cardId);
  const copy = towerCardCopy(cardName);
  const wrap = document.createElement("div");
  wrap.className = "notif-modal-backdrop";
  wrap.innerHTML = `
    <div class="notif-modal notif-modal-tower" role="dialog" aria-modal="true">
      <div class="notif-modal-header">Tower card acquired!</div>
      ${flipCard(cardFace({
        flavor: "tower",
        title: copy.title,
        icon: towerCardIcon(cardName),
        value: valueLine(card?.category ?? null, card?.value ?? 0),
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
