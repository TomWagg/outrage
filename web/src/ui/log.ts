/**
 * Scrolling event log. Appends every game event broadcast by the server.
 *
 * Formatting is intentionally lightweight — a single line per event with
 * kind-specific one-liners for the common events, plus a safe fallback for
 * anything not explicitly handled.
 *
 * IMPORTANT: events arrive from the server with the same payload for every
 * connected player.  Any event that carries per-player secret information
 * (e.g. which tower card was drawn) must be redacted here for viewers who
 * are not the relevant player — we only show the full detail to state.you.
 */
import type { ClientState, LogEntry } from "../state.js";

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
  for (const e of entries) {
    const d = document.createElement("div");
    d.style.marginBottom = "0.2rem";
    d.textContent = formatEntry(e, state.you ?? null);
    list.appendChild(d);
  }
  list.scrollTop = list.scrollHeight;
}

/**
 * Format a log entry into a human-readable string.
 *
 * ``me`` is the viewing player's username — used to decide whether to reveal
 * secret information (e.g. which tower card was drawn) or redact it.
 */
function formatEntry(e: LogEntry, me: string | null): string {
  const p = e.payload || {};
  switch (e.kind) {

    // ---- turn flow ----------------------------------------------------------
    case "turn_start":
      return `— ${p.player}'s turn —`;
    case "turn_ended":            // legacy / unused
      return `${p.player} ended turn.`;
    case "missed_turn":
      return `${p.player} missed their turn.`;
    case "extra_turn_used":
      return `${p.player} uses an extra turn.`;
    case "extra_turn_granted":
      return `${p.player} earns an extra turn (at ${p.space ?? "?"}).`;
    case "extra_turn_queued":
      return `${p.player} gets another turn.`;

    // ---- dice & movement ----------------------------------------------------
    case "dice_rolled":
      return `${p.player} rolled ${(p.roll ?? []).join(" + ")} = ${sum(p.roll)}.`;
    case "player_moved": {
      const mv = p.move_kind ? ` [${p.move_kind}]` : "";
      return `${p.player} moved from ${p.src} → ${p.dst}${mv}.`;
    }
    case "sent_to_space":
      return `${p.player} is sent from ${p.src} to ${p.dst}` +
        (p.label ? ` (${p.label})` : "") +
        (p.misses_turn ? " — and misses their next turn." : ".");
    case "miss_turn_queued":
      return `${p.player}: ${p.label ?? "Miss a turn"} — they lose their next turn.`;
    case "space_action_failed":
      return `⚠ Space ${p.space ?? "?"} action ${p.key ?? "?"} failed (${p.reason ?? "?"}).`;
    case "landing_chain_truncated":
      return `⚠ Too many chained teleports at ${p.space ?? "?"} — stopped here.`;
    case "no_legal_move":
      return `${p.player} has no legal move (steps: ${p.steps ?? "?"}).`;
    case "choose_path":
      return `${p.player} is choosing a path (${(p.destinations ?? []).length} options).`;

    // ---- split 7 ------------------------------------------------------------
    case "split_assigned":
      return `Split ${p.self}/${p.other}` +
        (p.target ? ` with ${p.target}` : "") +
        (p.leg_order === "target_first" ? " (they move first)." : ".");
    case "split_assign_required":
      return `A split must be assigned for the roll of ${p.total ?? "?"}.`;
    case "binary_disruption_armed":
      return `${p.player} armed Binary Disruption — roll will be split.`;

    // ---- cards --------------------------------------------------------------
    case "tower_card_drawn":
      // SECRET: only the drawer knows which card they drew.
      if (p.player === me) {
        return `You drew: ${cardNameFromId(p.card ?? "")}.`;
      }
      return `${p.player} drew a tower card.`;

    case "raven_card_drawn":
      // Raven cards are drawn aloud — the effect is public.
      return `Raven (${p.player}): ${ravenEffectLabel(p.effect ?? p.card ?? "?")}.`;

    case "weapons_surrendered": {
      const n = p.count ?? (p.cards ?? []).length;
      if (n === 0) return `${p.player} reaches the Broad Arrow Tower unarmed.`;
      // The owner sees what they gave up; everyone else just sees how much.
      if (p.player === me) {
        const names = (p.cards ?? []).map((c: string) => cardNameFromId(c)).join(", ");
        return `Broad Arrow Tower: you surrendered ${names}.`;
      }
      return `Broad Arrow Tower: ${p.player} surrendered ${n} weapon${n === 1 ? "" : "s"}.`;
    }

    case "card_change_offered":
      return `${p.player} may change a card.`;
    case "card_changed":
      // SECRET: which card went out and which came in is the drawer's business.
      if (p.player === me) {
        return `You discarded ${cardNameFromId(p.discarded ?? "")}` +
          (p.drawn ? ` and drew ${cardNameFromId(p.drawn)}.` : " (deck empty).");
      }
      return `${p.player} changed a card.`;
    case "card_change_skipped":
      return `${p.player} had no card to change` +
        (p.reason === "empty_hand" ? " (empty hand) — drew instead." : ".");
    case "card_swap_offered":
      return `${p.player} may swap a card with ${(p.candidates ?? []).join(" or ")}.`;
    case "card_swapped":
      // Both sides know what they gave; neither should learn the other's hand
      // from the log, so only the two participants see the card names.
      if (p.player === me || p.target === me) {
        return `${p.player} gave ${cardNameFromId(p.given ?? "")} to ${p.target} ` +
          `and took ${cardNameFromId(p.received ?? "")}.`;
      }
      return `${p.player} swapped a card with ${p.target}.`;
    case "card_swap_skipped":
      return `${p.player} found nobody to swap with.`;

    case "raven_needs_input":
      return `Waiting for raven effect input (${p.effect ?? "?"}).`;
    case "raven_effect_failed":
      return `Raven effect failed: ${p.effect ?? "?"}.`;

    // ---- accreditation ------------------------------------------------------
    case "trying_accreditation":
      return `${p.player} approaches Queen's House to seek accreditation.`;
    case "accredited":
      return p.via === "odd_roll"
        ? `${p.player} rolled ODD — accredited! Free to roam the Inner Ward.`
        : p.via === "tower_pass"
          ? `${p.player} flashed a Tower Pass and strolled through accreditation.`
          : `${p.player} is now accredited (${p.via ?? "?"}).`;
    case "accreditation_failed":
      return `${p.player} rolled EVEN — the clerks are not convinced. Turn ends.`;
    // Legacy / unused
    case "accreditation_result":
      return `${p.player} accreditation: ${p.accredited ? "PASSED" : "failed"}.`;

    // ---- combat -------------------------------------------------------------
    case "combat_started":
      return `⚔ Combat: ${p.attacker} vs ${p.defender} at ${p.space ?? "?"}.`;
    case "combat_available":
      return `${p.player} can attack ${(p.targets ?? []).join(", ")} at ${p.space ?? "?"}.`;
    case "combat_cards_selected":
      return `${p.player} committed ${p.count ?? "?"} card(s) to combat.`;
    case "combat_special":
      // Special cards (Sanctuary, Mass Accretor) are played openly.
      return `${p.player} plays ${p.card ?? "?"} in combat.`;
    case "sanctuary_taken":
      return `${p.defender} claims Sanctuary — the fight is off, but ` +
        `${p.attacker} loses ${p.attacker_cards_lost ?? 0} card(s) and ` +
        `${p.defender} loses ${p.defender_cards_lost ?? 0}. Both redraw.`;
    case "combat_resolved": {
      const parts = [
        `⚔ ${p.attacker ?? "?"} ${p.attacker_total ?? 0} vs ` +
        `${p.defender ?? "?"} ${p.defender_total ?? 0} — ` +
        `${p.winner} wins${p.tie ? " (tie goes to the defender)" : ""}.`,
      ];
      const spoils: string[] = [];
      const jewels = Array.isArray(p.jewels_taken) ? p.jewels_taken : [];
      if (jewels.length) spoils.push(`${jewels.length} jewel(s): ${jewels.join(", ")}`);
      if (p.coin_taken) spoils.push(p.coin_overflowed ? "a coin (overflows to Devereux)" : "a coin");
      if (spoils.length) parts.push(`${p.winner} takes ${spoils.join(" and ")}.`);
      if (p.cards_drawn) parts.push(`${p.winner} draws ${p.cards_drawn} card(s).`);
      parts.push(`${p.loser} is carried to the Hospital and misses a turn.`);
      return parts.join(" ");
    }

    // ---- jewels & coin ------------------------------------------------------
    case "jewel_attempt_offered":
      return `Jewel attempt available: ${p.jewel ?? "?"} at ${p.space ?? "?"}.`;
    case "jewel_attempt":
      // Tools are committed face-up, so we can show them to everyone.
      return `${p.player} attempts ${p.jewel ?? "?"}: roll ${sum(p.roll)} vs threshold ${p.threshold ?? "?"} — ${p.success ? "SUCCESS ✓" : "failed ✗"}.`;
    case "jewel_attempt_retry_available":
      return `${p.player} stays put — they can try the ${p.jewel ?? "jewel"} again next turn.`;
    case "jewel_acquired":
      return `${p.player} took the ${p.jewel ?? "?"}.`;
    case "jewel_auto_acquired":
      return `${p.player} found the ${p.jewel ?? "?"} lying loose and pocketed it.`;
    case "jewel_already_taken":
      return `The ${p.jewel ?? "?"} has already been taken.`;
    case "coin_picked_up":
      return `${p.player} picked up a coin.`;

    // ---- confinement --------------------------------------------------------
    case "status_applied":
      return `${p.player} → ${p.status}${p.turns ? ` (${p.turns} turns)` : ""}.`;
    case "confinement_escaped":
      return `${p.player} escaped confinement early (doubles!).`;
    case "confinement_expired":
      return `${p.player}'s confinement has expired.`;
    case "rack_expired":
      return `${p.player} is released from the Rack.`;
    case "three_doubles_bloody_tower":
      return `${p.player} rolled three doubles — sent to the Bloody Tower!`;
    case "pardoned":
      return p.pardon_kind === "rack"
        ? `${p.player} produces a Rack Pardon — released!`
        : `${p.player} produces a Royal Pardon — released!`;
    case "disguise_played":
      return `${p.player} slips on a Disguise.`;
    case "escaped_beauchamp":
      return `${p.player} escaped Beauchamp Tower via rope/ladder.`;
    case "framed":
      return `${p.framer} framed ${p.framed} with Confession` +
        (p.remaining ? ` (${p.remaining} torture turn${p.remaining === 1 ? "" : "s"} inherited).` : ".");

    // ---- raven effects (common sub-events) ----------------------------------
    case "pecked_by_ravens":
      return `${p.player} is pecked by the ravens — off to hospital!`;
    case "resting_on_bench":
      return `${p.player} sits on a bench to rest — they miss their next turn.`;
    case "miss_turn_on_landing":
      return `${p.player} stops at the ${p.label ?? p.space_kind ?? "square"} — they miss their next turn.`;
    case "rack_sender_triggered":
      return `${p.player} trips the alarm — dragged off to the Rack!`;
    case "stopped_and_searched":
      return `${p.player} is stopped and searched` +
        (p.carried_jewels ? ` (carrying ${p.carried_jewels} jewel(s)).` : ".");
    case "disguise_shown":
      return `${p.player} shows a Disguise to the guard.`;
    case "stopped_forfeit":
      return `${p.player} forfeits their jewels and weapons — sent to Bloody Tower.`;
    case "ghost":
      return `${p.player} is haunted — flees to Chapel Royal.`;
    case "bowyer_questioning":
      return `${p.player} is taken for questioning at Bowyer Tower.`;
    case "governors_tea":
      return `${p.player} attends the Governor's tea at Queen's House.`;
    case "warder_moved":
      return `Yeoman Warder ${p.warder ?? "?"} moves to ${p.dst ?? "?"}.`;
    case "lassoed":
      return `${p.roper} lassos ${p.target} from ${p.src} to ${p.dst}.`;
    case "mass_accretor_queued":
      return `${p.player} readies Mass Accretor.`;

    // ---- firecrackers -------------------------------------------------------
    case "firecrackers_escaped":
      return `${p.player} slipped out of the White Tower — Firecrackers effect cleared.`;
    case "firecrackers":
      return `Firecrackers! ${p.player} rattled the White Tower` +
        (Array.isArray(p.affected) && p.affected.length
          ? ` — on notice: ${p.affected.join(", ")}.`
          : ".");
    case "firecrackers_racked":
      return `${p.player} stayed in the White Tower — sent to the Rack` +
        (p.penalty === "coin"
          ? " (coin forfeit)."
          : p.penalty === "hand"
            ? ` (hand of ${p.cards_discarded ?? 0} discarded).`
            : ".");

    // ---- win conditions -----------------------------------------------------
    case "fast_win":
      return `🏆 ${p.player} escapes with a jewel and a coin — WINS (fast mode)!`;
    case "slow_escaped":
      return `${p.player} escaped the Tower with their haul.`;
    case "game_over":
      return `GAME OVER — winner: ${p.winner ?? "—"}.`;
    case "slow_game_over": {
      const reason = p.reason === "jewels_exhausted"
        ? "all jewels claimed"
        : p.reason === "last_player"
          ? "last player standing"
          : "game over";
      const ranking = Array.isArray(p.ranking) ? p.ranking : [];
      const rankStr = ranking
        .map((r: any, i: number) =>
          `${i + 1}. ${r.username} (${r.jewel_count} jewel${r.jewel_count === 1 ? "" : "s"})`)
        .join("; ");
      return `🏆 GAME OVER (${reason}) — winner: ${p.winner ?? "—"}` +
        (rankStr ? ` — ${rankStr}.` : ".");
    }

    // ---- misc ---------------------------------------------------------------
    case "unhandled_space_action":
      // The server emits this whenever a space carries an ``action.key`` the
      // engine has no branch for. Surfaced loudly rather than silently — an
      // unimplemented action is otherwise indistinguishable from a square that
      // is simply meant to do nothing.
      return `⚠ Space ${p.space ?? "?"} has an unimplemented action: ${p.key ?? "?"}.`;
    case "game_started":
      return `Game started (${p.mode ?? "?"} mode), ${(p.order ?? []).join(", ")}.`;
    case "chat":
      return `${p.from}: ${p.text}`;
    case "game_reset":
      return `Game reset; back to lobby.`;

    default:
      // Safe fallback: show event kind but NO raw payload (it may contain
      // card names, hand contents, or other info that should be redacted).
      return e.kind.replace(/_/g, " ");
  }
}

// ---- helpers ----------------------------------------------------------------

function sum(arr: unknown): number {
  if (!Array.isArray(arr)) return 0;
  return arr.reduce((a: number, b) => a + (typeof b === "number" ? b : 0), 0);
}

/**
 * Derive a human-readable card name from a server card ID.
 * IDs have the form ``tower:<name_with_underscores>:<n>`` or
 * ``raven:<effect_key>:<n>``.
 */
function cardNameFromId(id: string): string {
  const parts = id.split(":");
  if (parts.length < 2) return id;
  // Title-case the name segment.
  return parts[1]
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Convert a raven effect_key to a brief human label.
 */
function ravenEffectLabel(key: string): string {
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
