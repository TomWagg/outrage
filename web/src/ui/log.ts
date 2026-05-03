/**
 * Scrolling event log. Appends every game event broadcast by the server.
 *
 * Formatting is intentionally lightweight — a single line per event with
 * kind-specific one-liners for the common events, plus a JSON fallback for
 * anything we haven't styled yet.
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
    d.textContent = formatEntry(e);
    list.appendChild(d);
  }
  list.scrollTop = list.scrollHeight;
}

function formatEntry(e: LogEntry): string {
  const p = e.payload || {};
  switch (e.kind) {
    case "game_started":
      return `Game started (${p.mode ?? "?"} mode).`;
    case "turn_started":
      return `— ${p.username}'s turn —`;
    case "turn_ended":
      return `${p.username} ended turn.`;
    case "dice_rolled":
      return `${p.username} rolled ${(p.roll ?? []).join(" + ")} = ${sum(p.roll)}.`;
    case "player_moved":
      return `${p.username} → ${p.to} (from ${p.from}).`;
    case "card_drawn":
      return `${p.username} drew a ${p.deck ?? "card"}${p.card ? `: ${p.card.name}` : ""}.`;
    case "raven_effect":
      return `Raven: ${p.effect_key ?? "?"}${p.username ? ` (${p.username})` : ""}.`;
    case "jewel_attempted":
      return `${p.username} attempted a jewel: ${p.success ? "SUCCESS" : "failed"}.`;
    case "jewel_taken":
      return `${p.username} took the ${p.jewel_id}.`;
    case "accreditation_result":
      return `${p.username} accreditation: ${p.accredited ? "PASSED" : "failed"}.`;
    case "trying_accreditation":
      return `${p.username} approaches Queen's House to seek accreditation.`;
    case "accredited":
      return p.via === "odd_roll"
        ? `${p.username} rolled ODD and is now accredited — free to roam the Inner Ward.`
        : p.via === "tower_pass"
          ? `${p.username} flashed a Tower Pass and strolled through accreditation.`
          : `${p.username} is now accredited (${p.via ?? "?"}).`;
    case "accreditation_failed":
      return `${p.username} rolled EVEN — the clerks are not convinced. Turn ends.`;
    case "combat_started":
      return `Combat: ${p.attacker} vs ${p.defender}.`;
    case "combat_resolved":
      return `Combat won by ${p.winner}.`;
    case "combat_available":
      return `${p.player} shares ${p.space} with ${(p.targets ?? []).join(", ")} — may attack.`;
    case "status_applied":
      return `${p.username} → ${p.status}${p.turns ? ` (${p.turns} turns)` : ""}.`;
    case "framed":
      return `${p.framer} framed ${p.framed} with Confession` +
        (p.remaining ? ` (${p.remaining} torture turn${p.remaining === 1 ? "" : "s"} inherited).` : ".");
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
    case "split_assigned":
      return `Split ${p.self}/${p.other}` +
        (p.target ? ` with ${p.target}` : "") +
        (p.leg_order === "target_first" ? " (they move first)." : ".");
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
          `${i + 1}. ${r.username} (${r.jewel_count} jewel${r.jewel_count === 1 ? "" : "s"}, top ${r.jewel_top_value})`)
        .join("; ");
      return `SLOW GAME OVER (${reason}) — winner: ${p.winner ?? "—"}` +
        (rankStr ? ` — ${rankStr}.` : ".");
    }
    case "slow_escaped":
      return `${p.player} escaped the Tower with their haul.`;
    case "chat":
      return `${p.from}: ${p.text}`;
    case "game_reset":
      return `Game reset; back to lobby.`;
    default:
      return `${e.kind}: ${safeJson(p)}`;
  }
}

function sum(arr: unknown): number {
  if (!Array.isArray(arr)) return 0;
  return arr.reduce((a: number, b) => a + (typeof b === "number" ? b : 0), 0);
}

function safeJson(p: unknown): string {
  try {
    const s = JSON.stringify(p);
    return s.length > 120 ? s.slice(0, 117) + "…" : s;
  } catch {
    return "(?)";
  }
}
