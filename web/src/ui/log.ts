/**
 * Scrolling event log. Appends every game event broadcast by the server.
 *
 * One line per event, in a consistent register: past tense, whole sentences,
 * places named the way a player would name them. Two rules earn their keep here.
 *
 * **Never print a placeholder.** A missing payload field means the clause it
 * belongs to is dropped, not filled with "?" — a log peppered with question
 * marks reads as a broken client, and it hides the events that really are
 * malformed. Hence the conditional-concatenation style below, and the helpers
 * (:func:`place`, :func:`jewel`, :func:`cardNameFromId`) that return "" rather
 * than a placeholder when handed nothing.
 *
 * **Never print a raw space id.** ``ww47_bowyer`` is a database key; the player
 * knows the place as the Bowyer Tower. :func:`place` resolves ids against the
 * board, so every message that mentions a square goes through it.
 *
 * IMPORTANT: events arrive from the server with the same payload for every
 * connected player. Any event that carries per-player secret information
 * (e.g. which tower card was drawn) must be redacted here for viewers who
 * are not the relevant player — we only show the full detail to state.you.
 */
import type { BoardData, BoardSpace, ClientState, LogEntry } from "../state.js";

export function renderLogPanel(root: HTMLElement): { update: (state: ClientState) => void } {
  root.innerHTML = `
    <div class="panel" id="log-panel">
      <h3>Log</h3>
      <div id="log-list" style="max-height:260px;overflow-y:auto;font-size:0.8rem;line-height:1.35"></div>
    </div>
  `;
  return { update: (state) => update(root, state) };
}

function update(root: HTMLElement, state: ClientState): void {
  const list = root.querySelector<HTMLElement>("#log-list")!;
  // For simplicity, re-render the entire list (the log is bounded in practice).
  list.innerHTML = "";
  const entries = state.log.slice(-200);
  const ctx: Ctx = { me: state.you ?? null, board: state.board ?? null };
  for (const e of entries) {
    const text = formatEntry(e, ctx);
    if (!text) continue;
    const d = document.createElement("div");
    d.style.marginBottom = "0.2rem";
    d.textContent = text;
    list.appendChild(d);
  }
  list.scrollTop = list.scrollHeight;
}

interface Ctx {
  /** The viewing player, used to decide what to reveal and to say "you". */
  me: string | null;
  board: BoardData | null;
}

// ---- place names ------------------------------------------------------------

const REGION_NAMES: Record<string, string> = {
  wall_walk: "the Wall Walk",
  inner_ward: "the Inner Ward",
  white_tower: "the White Tower",
};

// One index per board object. The board is fetched once and never mutated, so
// a WeakMap keyed on it costs one build and keeps the log's many lookups off a
// linear scan of ~300 spaces.
const spaceIndex = new WeakMap<BoardData, Map<string, BoardSpace>>();

function spaceById(board: BoardData | null, id: string): BoardSpace | null {
  if (!board) return null;
  let idx = spaceIndex.get(board);
  if (!idx) {
    idx = new Map(board.spaces.map((s) => [s.id, s]));
    spaceIndex.set(board, idx);
  }
  return idx.get(id) ?? null;
}

/**
 * A space id as a player would say it.
 *
 * Most named squares carry a label. The plain ones don't, so they fall back to
 * something locating rather than to the id: a numbered step on the Wall Walk,
 * or a region and grid reference in the wards.
 */
function place(board: BoardData | null, id: unknown): string {
  if (typeof id !== "string" || !id) return "";
  const sp = spaceById(board, id);
  if (!sp) {
    // No board yet (or an id the board doesn't know): make the key readable
    // rather than printing it raw.
    return id.replace(/^(ww|iw|wt|out)_?/, "").replace(/_/g, " ").trim() || id;
  }
  if (sp.label) return sp.label;
  if (sp.wall_walk_order != null) return `the Wall Walk (step ${sp.wall_walk_order})`;
  const region = REGION_NAMES[sp.region] ?? "the board";
  return sp.coords ? `${region} at ${sp.coords[0]},${sp.coords[1]}` : region;
}

// ---- small formatting helpers ----------------------------------------------

/** ``n`` with a plural ``s`` where English wants one. */
function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function sum(arr: unknown): number {
  if (!Array.isArray(arr)) return 0;
  return arr.reduce((a: number, b) => a + (typeof b === "number" ? b : 0), 0);
}

/** Join a list the way prose does: "a, b and c". */
function list(items: unknown): string {
  const arr = Array.isArray(items) ? items.map(String).filter(Boolean) : [];
  if (arr.length === 0) return "";
  if (arr.length === 1) return arr[0];
  return `${arr.slice(0, -1).join(", ")} and ${arr[arr.length - 1]}`;
}

/**
 * Derive a human-readable card name from a server card ID.
 * IDs have the form ``tower:<name_with_underscores>:<n>`` or
 * ``raven:<effect_key>:<n>``.
 */
function cardNameFromId(id: unknown): string {
  if (typeof id !== "string" || !id) return "";
  const parts = id.split(":");
  if (parts.length < 2) return id;
  return parts[1]
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function cardNames(ids: unknown): string {
  return list(Array.isArray(ids) ? ids.map(cardNameFromId) : []);
}

const JEWEL_NAMES: Record<string, string> = {
  sword: "the Sword",
  sceptre: "the Sceptre",
  orb: "the Orb",
  crown_prince_of_wales: "the Prince of Wales's Crown",
  crown_st_edward: "St Edward's Crown",
};

function jewel(id: unknown): string {
  if (typeof id !== "string" || !id) return "";
  return JEWEL_NAMES[id] ?? id.replace(/_/g, " ");
}

// ---- the formatter ----------------------------------------------------------

/**
 * Format a log entry into a human-readable line.
 *
 * Returns "" for events with nothing worth saying to a player, which the
 * caller skips — a few server events exist only to be folded into statistics
 * or to acknowledge a click.
 */
function formatEntry(e: LogEntry, ctx: Ctx): string {
  const p: Record<string, any> = e.payload || {};
  const b = ctx.board;
  const at = (id: unknown) => place(b, id);

  switch (e.kind) {

    // ---- turn flow ----------------------------------------------------------
    case "game_started": {
      const order = list(p.order);
      const mode = p.mode === "slow" ? "the long game" : "a race for the first escape";
      return `The game begins — ${mode}.` +
        (order ? ` Order of play: ${order}.` : "") +
        (p.hand_size ? ` ${plural(Number(p.hand_size), "card")} dealt to each player.` : "");
    }
    case "turn_start":
      return `— ${p.player}'s turn —`;
    case "missed_turn":
      return `${p.player} forfeits this turn.`;
    case "extra_turn_used":
      return `${p.player} takes another turn.`;
    case "extra_turn_granted":
      return p.space
        ? `${p.player} reaches ${at(p.space)} and earns an extra turn.`
        : `${p.player} earns an extra turn.`;
    case "extra_turn_queued":
      return `${p.player} is owed another turn.`;

    // ---- dice & movement ----------------------------------------------------
    case "dice_rolled":
      return `${p.player} rolls ${(p.roll ?? []).join(" and ")} — ${sum(p.roll)}.`;
    case "player_moved": {
      const how = p.move_kind === "split_seven"
        ? " (moved by another player's seven)"
        : p.move_kind === "firecrackers_rack"
          ? " (dragged off by the guards)"
          : "";
      return `${p.player} moves from ${at(p.src)} to ${at(p.dst)}${how}.`;
    }
    case "sent_to_space":
      return `${p.player} is sent from ${at(p.src)} to ${at(p.dst)}` +
        (p.label ? ` — ${p.label}` : "") +
        (p.misses_turn ? ", and will miss their next turn." : ".");
    case "go_back_by_roll":
      return `${p.player} is turned back ${plural(Number(p.steps ?? 0), "step")} ` +
        `to ${at(p.dst)}.`;
    case "go_to_and_accredit":
      return `${p.player} is escorted to ${at(p.dst)} and signed in.`;
    case "miss_turn_queued":
      return `${p.player} is delayed${p.label ? ` — ${p.label}` : ""}, and will miss ` +
        `their next turn.`;
    case "no_legal_move":
      return `${p.player} has nowhere legal to move` +
        (p.steps ? ` with ${plural(Number(p.steps), "step")}` : "") + `.`;
    case "choose_path": {
      const n = (p.destinations ?? []).length;
      return p.for_target
        ? `${p.player} is choosing where to send ${p.for_target}.`
        : `${p.player} is choosing between ${plural(n, "route")}.`;
    }
    case "space_action_failed":
      return `The square at ${at(p.space)} could not resolve` +
        (p.reason ? ` (${String(p.reason).replace(/_/g, " ")})` : "") + `.`;
    case "landing_chain_truncated":
      return `Too many squares sent ${at(p.space)} onward in one move — ` +
        `the move stops here.`;

    // ---- split 7 ------------------------------------------------------------
    case "split_assigned": {
      const self = Number(p.self ?? 0);
      const other = Number(p.other ?? 0);
      if (!other || !p.target) return `The whole roll is kept.`;
      return `The roll is split: ${plural(self, "step")} kept, ` +
        `${plural(other, "step")} given to ${p.target}` +
        (p.leg_order === "target_first" ? ", who moves first." : ".");
    }
    case "split_assign_required":
      return p.total
        ? `A roll of ${p.total} must be split between two players.`
        : `The roll must be split between two players.`;
    case "split_unavailable":
      return `No other player can be moved, so ${p.player} keeps all ` +
        `${p.total ?? 7} steps.`;
    case "binary_disruption_armed":
      return `${p.player} plays Binary Disruption — the next roll will be split.`;

    // ---- cards --------------------------------------------------------------
    case "tower_card_drawn":
      // SECRET: only the drawer knows which card they drew.
      return p.player === ctx.me
        ? `You draw ${cardNameFromId(p.card) || "a tower card"}.`
        : `${p.player} draws a tower card.`;

    case "raven_card_drawn":
      // Raven cards are drawn aloud — the effect is public.
      return `${p.player} draws a raven card: ` +
        `${ravenEffectLabel(p.effect ?? p.card)}.`;
    case "raven_notice_revealed":
    case "raven_notice_dismissed":
    case "confinement_notice_dismissed":
      // Acknowledgements of a click, not events in the game.
      return "";
    case "raven_deck_empty":
      return `The raven deck is exhausted.`;
    case "tower_deck_empty":
      return `The tower deck is exhausted.`;

    case "cards_redrawn": {
      const given = Number(p.given_count ?? 0);
      const got = Number(p.received_count ?? 0);
      // SECRET: the counts are public, the cards themselves are not.
      const tail = p.short_by ? ` The deck fell ${p.short_by} short.` : "";
      if (p.player === ctx.me) {
        const names = cardNames(p.received);
        return `You trade in ${plural(given, "card")} and draw ` +
          (names ? `${names}.` : `${plural(got, "card")}.`) + tail;
      }
      return `${p.player} stays put and trades ${plural(given, "card")} for ` +
        `${plural(got, "card")}.${tail}`;
    }

    case "weapons_surrendered": {
      const n = Number(p.count ?? (p.cards ?? []).length);
      if (n === 0) return `${p.player} arrives at the Broad Arrow Tower unarmed.`;
      // The owner sees what they gave up; everyone else just sees how much.
      if (p.player === ctx.me) {
        return `You surrender ${cardNames(p.cards)} at the Broad Arrow Tower.`;
      }
      return `${p.player} surrenders ${plural(n, "weapon")} at the Broad Arrow Tower.`;
    }

    case "card_change_offered":
      return `${p.player} may exchange a card.`;
    case "card_changed":
      // SECRET: which card went out and which came in is the drawer's business.
      if (p.player === ctx.me) {
        const out = cardNameFromId(p.discarded);
        return p.drawn
          ? `You discard ${out} and draw ${cardNameFromId(p.drawn)}.`
          : `You discard ${out}; the deck is empty.`;
      }
      return `${p.player} exchanges a card.`;
    case "card_change_skipped":
      return p.reason === "empty_hand"
        ? `${p.player} has no card to exchange, and draws instead.`
        : `${p.player} has no card to exchange.`;
    case "card_swap_offered":
      return `${p.player} may trade a card with ${list(p.candidates)}.`;
    case "card_swapped":
      // Both sides know what they gave; neither should learn the other's hand
      // from the log, so only the two participants see the card names.
      if (p.player === ctx.me || p.target === ctx.me) {
        return `${p.player} gives ${cardNameFromId(p.given)} to ${p.target} ` +
          `and takes ${cardNameFromId(p.received)} in return.`;
      }
      return `${p.player} trades a card with ${p.target}.`;
    case "card_swap_skipped":
      return `${p.player} finds nobody to trade with.`;

    case "raven_needs_input":
      return `The raven card is waiting on a decision.`;
    case "raven_effect_failed":
      return `The raven card had no effect` +
        (p.effect ? ` (${ravenEffectLabel(p.effect)})` : "") + `.`;

    // ---- accreditation ------------------------------------------------------
    case "trying_accreditation":
      return `${p.player} presents themselves at Queen's House for accreditation.`;
    case "accredited":
      switch (p.via) {
        case "odd_roll":
          return `${p.player} rolls odd and is accredited — the Inner Ward is open ` +
            `to them.`;
        case "tower_pass":
          return `${p.player} produces a Tower Pass and is accredited on the spot.`;
        case "space_action":
          return `${p.player} is accredited on arrival at Queen's House.`;
        default:
          return `${p.player} is accredited.`;
      }
    case "accreditation_failed":
      return `${p.player} rolls even — the clerks are unconvinced, and the turn ends.`;
    case "accreditation_retry":
      return `${p.player} rolls a double at Queen's House — the clerks allow ` +
        `another attempt.`;

    // ---- combat -------------------------------------------------------------
    case "combat_started":
      return `${p.attacker} attacks ${p.defender} at ${at(p.space)}.`;
    case "combat_available":
      return `${p.player} may attack ${list(p.targets)} at ${at(p.space)}.`;
    case "combat_cards_selected":
      return `${p.player} commits ${plural(Number(p.count ?? 0), "card")} to the fight.`;
    case "combat_special":
      // Special cards (Sanctuary, Mass Accretor) are played openly.
      return `${p.player} plays ${cardNameFromId(p.card) || p.card || "a card"} ` +
        `in the fight.`;
    case "sanctuary_taken":
      return `${p.defender} claims Sanctuary and the fight is called off. ` +
        `${p.attacker} loses ${plural(Number(p.attacker_cards_lost ?? 0), "card")} ` +
        `and ${p.defender} loses ` +
        `${plural(Number(p.defender_cards_lost ?? 0), "card")}; both redraw.`;
    case "combat_resolved": {
      const parts: string[] = [];
      parts.push(
        `${p.attacker} ${p.attacker_total ?? 0} against ` +
        `${p.defender} ${p.defender_total ?? 0} — ${p.winner} wins` +
        `${p.tie ? ", the tie going to the defender" : ""}.`,
      );
      const spoils: string[] = [];
      const jewels = Array.isArray(p.jewels_taken) ? p.jewels_taken : [];
      if (jewels.length) spoils.push(list(jewels.map(jewel)));
      if (p.coin_taken) {
        spoils.push(p.coin_overflowed
          ? "a coin, which overflows back to the Devereux Tower"
          : "a coin");
      }
      if (spoils.length) parts.push(`${p.winner} takes ${list(spoils)}.`);
      if (p.cards_drawn) {
        parts.push(`${p.winner} draws ${plural(Number(p.cards_drawn), "card")}.`);
      }
      parts.push(`${p.loser} is carried to the Hospital and misses a turn.`);
      return parts.join(" ");
    }

    // ---- jewels & coin ------------------------------------------------------
    case "jewel_attempt_offered":
      return `${jewel(p.jewel)} is within reach at ${at(p.space)}.`;
    case "jewel_attempt":
      // Tools are committed face-up, so we can show them to everyone.
      return `${p.player} attempts ${jewel(p.jewel)}: rolls ${sum(p.roll)} ` +
        `against a threshold of ${p.threshold} — ` +
        (p.success ? "success." : "and fails.");
    case "jewel_attempt_retry_available":
      return `${p.player} remains beside ${jewel(p.jewel) || "the jewel"} and may ` +
        `try again next turn.`;
    case "jewel_acquired":
      return `${p.player} takes ${jewel(p.jewel)}.`;
    case "jewel_auto_acquired":
      return `${p.player} finds ${jewel(p.jewel)} lying loose and pockets it.`;
    case "jewel_already_taken":
      return `${jewel(p.jewel)} has already been taken.`;
    case "metallicity":
      return `Metallicity — jewels are scattered loose across the board.`;
    case "coin_picked_up":
      return `${p.player} collects a coin` +
        (typeof p.remaining === "number"
          ? `; ${p.remaining} left in the Devereux Tower.`
          : ".");

    // ---- confinement --------------------------------------------------------
    case "confined_on_landing":
      return `${p.player} walks into ${p.label ?? at(p.space)} and the door closes ` +
        `behind them — ` +
        `${p.status === "TORTURED" ? "held for questioning" : "imprisoned"} for ` +
        `${plural(Number(p.turns ?? 3), "turn")}.`;
    case "beauchamp_imprisonment":
      return `${p.player} is imprisoned in the Beauchamp Tower.`;
    case "rack_sender_triggered":
      return `${p.player} trips an alarm at ${at(p.space)} and is taken to the Rack.`;
    case "rack_turn_skipped":
      return `${p.player} is on the Rack and takes no turn` +
        (typeof p.turns_remaining === "number" && p.turns_remaining > 0
          ? `; ${plural(p.turns_remaining, "turn")} still to serve.`
          : ".");
    case "rack_coin_lost":
      return `${p.player} forfeits a coin to the Rack.`;
    case "rack_hand_lost":
      return `${p.player} forfeits their hand to the Rack.`;
    case "confinement_escaped":
      return `${p.player} rolls a double and walks free.`;
    case "confinement_expired":
      return `${p.player} has served their sentence and is released.`;
    case "rack_expired":
      return `${p.player} is released from the Rack.`;
    case "three_doubles_bloody_tower":
      return `${p.player} rolls a third double in a row and is taken to the ` +
        `Bloody Tower.`;
    case "pardoned":
      return p.pardon_kind === "rack"
        ? `${p.player} produces a Rack Pardon and is released.`
        : `${p.player} produces a Royal Pardon and is released.`;
    case "disguise_played":
      return p.via === "move"
        ? `${p.player} puts on a Disguise and slips past the Yeoman Warder.`
        : `${p.player} puts on a Disguise.`;
    case "escaped_beauchamp":
      return `${p.player} escapes the Beauchamp Tower by rope.`;
    case "framed":
      return `${p.framer} signs a confession naming ${p.framed}, ` +
        `who inherits ` +
        (p.remaining
          ? `${plural(Number(p.remaining), "turn")} of questioning.`
          : `the questioning.`);

    // ---- raven effects (common sub-events) ----------------------------------
    case "pecked_by_ravens":
      return `${p.player} is set upon by the ravens and taken to the Hospital.`;
    case "resting_on_bench":
      return `${p.player} stops to rest on a bench and misses their next turn.`;
    case "miss_turn_on_landing":
      return `${p.player} is held up at ${p.label ?? at(p.space)} and misses ` +
        `their next turn.`;
    case "stopped_and_searched":
      return `${p.player} is stopped and searched` +
        (p.carried_jewels
          ? `, carrying ${plural(Number(p.carried_jewels), "jewel")}.`
          : ".");
    case "disguise_shown":
      return `${p.player} shows a Disguise to the guard and is waved through.`;
    case "stopped_forfeit":
      return `${p.player} forfeits their jewels and weapons, and is taken to the ` +
        `Bloody Tower.`;
    case "summons_declined":
      return `${p.player} ignores the summons and misses their next turn instead.`;
    case "ghost":
      return `${p.player} meets the ghost and flees to the Chapel Royal.`;
    case "bowyer_questioning":
      return `${p.player} is taken to the Bowyer Tower for questioning.`;
    case "governors_tea":
      return `${p.player} is invited to the Governor's tea at Queen's House.`;
    case "warder_moved":
      return `A Yeoman Warder takes up post at ${at(p.dst)}.`;
    case "warder_post_occupied":
      return `That post is already manned.`;
    case "no_free_warder_posts":
      return `Every Yeoman Warder post is already manned.`;
    case "no_occupied_posts":
      return `No Yeoman Warder is at a post.`;
    case "no_warders_in_barracks":
      return `No Yeoman Warder is off duty.`;
    case "no_warders_out_of_barracks":
      return `Every Yeoman Warder is off duty.`;
    case "lassoed":
      return `${p.roper} lassos ${p.target} from ${at(p.src)} to ${at(p.dst)}.`;
    case "mass_accretor_queued":
      return `${p.player} readies the Mass Accretor for the next fight.`;

    // ---- firecrackers -------------------------------------------------------
    case "firecrackers":
      return `Firecrackers go off in the White Tower` +
        (Array.isArray(p.affected) && p.affected.length
          ? ` — ${list(p.affected)} must get out before the turn is over.`
          : ".");
    case "firecrackers_escaped":
      return `${p.player} gets clear of the White Tower in time.`;
    case "firecrackers_racked":
      return `${p.player} is still in the White Tower and is taken to the Rack` +
        (p.penalty === "coin"
          ? ", forfeiting a coin."
          : p.penalty === "hand"
            ? `, forfeiting ${plural(Number(p.cards_discarded ?? 0), "card")}.`
            : ".");

    // ---- win conditions -----------------------------------------------------
    case "fast_win":
      return `${p.player} escapes through the Cradle Tower with a jewel and a ` +
        `coin, and wins.`;
    case "slow_escaped":
      return `${p.player} escapes the Tower with their haul.`;
    case "game_over":
      return p.winner ? `Game over. ${p.winner} wins.` : `Game over.`;
    case "game_over_draw":
      return `The game is abandoned and recorded as a draw.`;
    case "slow_game_over": {
      const reason = p.reason === "jewels_exhausted"
        ? "every jewel has been claimed"
        : p.reason === "last_player"
          ? "only one player is left"
          : "";
      const ranking = Array.isArray(p.ranking) ? p.ranking : [];
      const rankStr = ranking
        .map((r: any, i: number) =>
          `${i + 1}. ${r.username} (${plural(Number(r.jewel_count ?? 0), "jewel")})`)
        .join("; ");
      return `Game over${reason ? ` — ${reason}` : ""}.` +
        (p.winner ? ` ${p.winner} wins.` : "") +
        (rankStr ? ` Final standings: ${rankStr}.` : "");
    }

    // ---- misc ---------------------------------------------------------------
    case "unhandled_space_action":
      // The server emits this whenever a space carries an ``action.key`` the
      // engine has no branch for. Surfaced loudly rather than silently — an
      // unimplemented action is otherwise indistinguishable from a square that
      // is simply meant to do nothing.
      return `⚠ ${at(p.space)} has an action the engine does not implement` +
        (p.key ? ` (${p.key})` : "") + `.`;
    case "chat":
      return `${p.from}: ${p.text}`;
    case "game_reset":
      return `The game is reset; back to the lobby.`;

    default:
      // Safe fallback: show the event kind but NO raw payload (it may contain
      // card names, hand contents, or other info that should be redacted).
      return e.kind.replace(/_/g, " ");
  }
}

/**
 * Convert a raven effect_key to a brief human label.
 */
function ravenEffectLabel(key: unknown): string {
  if (typeof key !== "string" || !key) return "an unknown card";
  const labels: Record<string, string> = {
    go_to_location:          "Go to a Tower location",
    go_to_jewel_view:        "Go to Jewel View",
    call_warder_to_post:     "Yeoman Warder called to post",
    return_warder_to_barracks: "Yeoman Warder returns to barracks",
    pecked_by_ravens:        "Pecked by Ravens",
    rest_on_bench:           "Rest on a Bench",
    photo_with_warder:       "Photo with a Warder",
    stopped_and_searched:    "Stopped and Searched",
    clerk_tea_exception:     "Clerk's Tea Exception",
    ghost:                   "The Ghost",
    queens_birthday:         "Queen's Birthday",
    lost:                    "Lost",
    chief_yeoman_passes:     "Chief Yeoman Passes",
    bowyer_questioning:      "Bowyer Tower Questioning",
    shop_for_film:           "Shop for Film",
    governors_tea:           "Governor's Tea",
    beauchamp_imprisonment:  "Beauchamp Tower Imprisonment",
    rack_of_torment:         "Rack of Torment",
    metallicity:             "Metallicity",
  };
  return labels[key] ?? key.replace(/_/g, " ");
}
