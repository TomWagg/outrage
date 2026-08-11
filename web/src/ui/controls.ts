/**
 * Turn-action controls (roll, end turn, attempt jewel) rendered into a panel.
 *
 * The set of buttons surfaced is a pure function of the current game phase
 * and whether the viewer is the active player.
 */
import type { WsClient } from "../net/ws.js";
import type { ClientState } from "../state.js";
import { currentTurnUsername, playerByName } from "../state.js";
import { towerCardIcon } from "./card_art.js";

export function renderControlsPanel(root: HTMLElement): { update: (state: ClientState, ws: WsClient) => void } {
  root.innerHTML = `
    <div class="panel" id="controls-panel">
      <h3>Turn</h3>
      <div id="turn-info" style="font-size:0.9rem;color:var(--muted);margin-bottom:0.5rem"></div>
      <div id="controls-row" style="display:flex;gap:0.4rem;flex-wrap:wrap"></div>
      <div id="pending-info" style="margin-top:0.5rem;font-size:0.85rem;color:var(--muted)"></div>
    </div>
  `;
  return {
    update: (state, ws) => updateControls(root, state, ws),
  };
}

function updateControls(root: HTMLElement, state: ClientState, ws: WsClient): void {
  const info = root.querySelector<HTMLElement>("#turn-info")!;
  const row = root.querySelector<HTMLElement>("#controls-row")!;
  const pending = root.querySelector<HTMLElement>("#pending-info")!;
  row.innerHTML = "";
  pending.textContent = "";

  const g = state.game;
  if (!g) {
    info.textContent = "No game in progress.";
    return;
  }

  const cur = currentTurnUsername(g);
  const you = state.you;
  const me = playerByName(g, you);
  const isMyTurn = cur === you;

  info.innerHTML = `
    <div>Phase: <strong>${g.phase}</strong></div>
    <div>Turn: <strong>${cur ?? "—"}</strong>${isMyTurn ? " (you)" : ""}</div>
    ${g.turn.roll.length ? `<div>Last roll: ${g.turn.roll.join(" + ")} = ${g.turn.roll.reduce((a, b) => a + b, 0)}</div>` : ""}
  `;

  if (g.phase === "GAME_OVER") {
    // The results screen (gameover.ts) is the real end-of-game surface; this
    // panel just leaves a way back in case it's been dismissed.
    info.innerHTML += `<div style="color:var(--accent);margin-top:0.4rem">Winner: ${g.winner ?? "—"}</div>`;
    if (you) row.appendChild(button("Return to lobby", () => ws.send("reset_lobby", {}).catch(noop)));
    return;
  }

  // Combat is the one phase where non-turn players (the defender) still need
  // to act — but that interaction lives in the combat modal, not this panel.
  if (g.phase === "COMBAT" && g.combat) {
    const c = g.combat;
    if (you === c.attacker || you === c.defender) {
      pending.textContent = "Combat in progress — see the dialog.";
    } else {
      pending.textContent = `Combat: ${c.attacker} vs ${c.defender}.`;
    }
    return;
  }

  if (!isMyTurn) {
    pending.textContent = cur ? `Waiting for ${cur}…` : "";
    return;
  }

  // If we share a square with any other player we may declare combat —
  // except inside the White Tower, where combat is forbidden.
  if (me && ["TURN_START", "PRE_ROLL", "TURN_END"].includes(g.phase)) {
    const mySpace = state.board?.spaces.find((s) => s.id === me.position);
    const inWhiteTower = mySpace?.region === "white_tower";
    const coLocated = g.players.filter((p) => p.username !== you && !p.escaped && p.position === me.position);
    for (const enemy of coLocated) {
      const b = button(`Attack ${enemy.username}`, () =>
        ws.send("initiate_combat", { username: you, target: enemy.username }).catch(noop),
      );
      if (inWhiteTower) {
        b.disabled = true;
        b.title = "No combat inside the White Tower";
      }
      row.appendChild(b);
    }
  }

  // Phase-specific action set (we only surface what the engine allows now).
  switch (g.phase) {
    case "TURN_START":
    case "PRE_ROLL":
    case "ACCREDITATION_ATTEMPT": {
      // Sitting out this turn: there's nothing to roll for, so don't offer a
      // roll. A Tower Pass can still buy an extra turn, hence the card buttons
      // below stay.
      const missing = !!me?.miss_next_turn;
      if (missing) {
        const why = document.createElement("div");
        why.style.marginBottom = "0.4rem";
        why.style.color = "var(--muted)";
        why.style.fontSize = "0.85rem";
        why.textContent = "You're missing this turn.";
        pending.appendChild(why);
      } else {
        row.appendChild(button("Roll dice", () => ws.send("roll_dice", { username: you }).catch(noop)));
      }
      row.appendChild(button("End turn", () => ws.send("end_turn", { username: you }).catch(noop)));
      if (g.phase === "ACCREDITATION_ATTEMPT" && me?.trying_accreditation) {
        const tip = document.createElement("div");
        tip.style.marginTop = "0.4rem";
        tip.style.fontSize = "0.85rem";
        tip.style.color = "var(--muted)";
        tip.innerHTML =
          `Accreditation trial: roll both dice.<br>` +
          `<strong>Odd total</strong> → accredited (use the roll to move in the Inner Ward).<br>` +
          `<strong>Even total</strong> → clerks send you away; turn ends.`;
        pending.appendChild(tip);
      }
      // A failed theft leaves you standing on the jewel — offer another go
      // before you roll and walk away.
      if (me && !missing && standingJewel(g, me)) {
        row.appendChild(
          button("Attempt the jewel again", () =>
            ws.send("attempt_jewel", {
              username: you,
              tool_card_ids: (me.hand ?? [])
                .filter((c) => c.category === "burglary")
                .map((c) => c.id),
            }).catch(noop),
          ),
        );
      }
      renderPreRollCardButtons(pending, g, me, you, state, ws);
      break;
    }

    case "CHOOSING_PATH": {
      const pm = g.turn.pending_move;
      const dests = pm?.destinations ?? {};
      const keys = Object.keys(dests);
      const forTarget = pm?.is_for_target === true;
      const targetName = pm?.target_for_split ?? "target";
      if (forTarget) {
        // Roller is picking where the split-7 target player moves.
        pending.innerHTML =
          `<div>Choose where to send <strong>${targetName}</strong> ` +
          `(${keys.length} option${keys.length === 1 ? "" : "s"}):</div>`;
        for (const d of keys) {
          row.appendChild(button(d, () => ws.send("choose_move_path", { username: you, destination: d }).catch(noop)));
        }
      } else {
        // Normal: roller picking their own destination.
        // Which destinations land on an enemy? (pass-through combat stops.)
        const enemyAt = new Map<string, string>();
        for (const p of g.players) {
          if (p.username !== you && !p.escaped) enemyAt.set(p.position, p.username);
        }
        const combatStops = keys.filter((k) => enemyAt.has(k));
        pending.innerHTML =
          `<div>Click a highlighted square (${keys.length} option${keys.length === 1 ? "" : "s"}) or pick here:</div>` +
          (combatStops.length
            ? `<div style="margin-top:0.25rem;color:var(--accent);font-size:0.8rem">` +
              `Destinations marked <strong>[fight]</strong> stop at an enemy and end your turn after combat.</div>`
            : "");
        for (const d of keys) {
          const enemy = enemyAt.get(d);
          const label = enemy ? `[fight] ${d} (vs ${enemy})` : d;
          row.appendChild(button(label, () => ws.send("choose_move_path", { username: you, destination: d }).catch(noop)));
        }
      }
      break;
    }

    case "JEWEL_ATTEMPT": {
      // Offer a simple "attempt with all burglary tools" button for the skeleton.
      const tools = (me?.hand ?? []).filter((c) => c.category === "burglary");
      const bonus = tools.reduce((a, c) => a + (c.value ?? 0), 0);
      pending.innerHTML =
        `<div>Jewel attempt: ${tools.length} burglary tool(s) available (total +${bonus}).</div>` +
        `<div style="margin-top:0.25rem">Roll ${Math.max(2, 12 - bonus)}+ on two dice. ` +
        `Tools are never used up, and a failed attempt leaves you on the jewel — ` +
        `you can try again next turn.</div>`;
      row.appendChild(
        button("Attempt with all tools", () =>
          ws.send("attempt_jewel", { username: you, tool_card_ids: tools.map((c) => c.id) }).catch(noop),
        ),
      );
      row.appendChild(
        button("Attempt without tools", () =>
          ws.send("attempt_jewel", { username: you, tool_card_ids: [] }).catch(noop),
        ),
      );
      break;
    }

    case "CARD_CHANGE": {
      renderCardChange(pending, g, me, you, ws);
      break;
    }

    case "SPLIT_SEVEN_ASSIGN": {
      renderSplitSeven(pending, row, g, you, ws);
      break;
    }

    case "RAVEN_EFFECT":
      renderRavenEffect(pending, row, state, ws);
      break;

    case "TURN_END":
      row.appendChild(button("End turn", () => ws.send("end_turn", { username: you }).catch(noop)));
      break;
  }
}

/**
 * The unclaimed jewel the player is currently standing on, if any.
 *
 * ``jewels_available`` maps jewel id → the space it sits on, so a match means
 * the jewel is still there to be stolen.
 */
function standingJewel(
  g: import("../state.js").GameSnapshot,
  me: import("../state.js").GamePlayer,
): string | null {
  for (const [jewelId, spaceId] of Object.entries(g.jewels_available ?? {})) {
    if (spaceId === me.position) return jewelId;
  }
  return null;
}

/**
 * Prompt for the "Change a card" squares and for ww75's swap.
 *
 * Both use the same pending state: the player picks a card to give up. A swap
 * additionally picks the opponent to trade with — what comes back is random,
 * so there's nothing else to choose.
 */
function renderCardChange(
  pending: HTMLElement,
  g: import("../state.js").GameSnapshot,
  me: import("../state.js").GamePlayer | null,
  you: string | null,
  ws: WsClient,
): void {
  const pcc = g.turn.pending_card_change;
  if (!pcc) return;
  const isSwap = pcc.kind === "swap";
  const hand = me?.hand ?? [];

  if (currentTurnUsername(g) !== you) {
    pending.textContent = isSwap
      ? `Waiting for ${currentTurnUsername(g) ?? "them"} to choose a card to swap…`
      : `Waiting for ${currentTurnUsername(g) ?? "them"} to change a card…`;
    return;
  }

  const targetPicker = isSwap
    ? `<label style="display:block;margin-top:0.4rem">Swap with:
         <select id="cc-target" style="margin-left:0.25rem">
           ${pcc.candidates.map((c) => `<option value="${c}">${c}</option>`).join("")}
         </select>
       </label>`
    : "";
  pending.innerHTML =
    (isSwap
      ? `<div>Pick a card to give away — you'll get a <strong>random</strong> one back.</div>`
      : `<div>Pick a card to discard; you'll draw the top of the tower deck.</div>`) +
    targetPicker;

  const targetSel = pending.querySelector<HTMLSelectElement>("#cc-target");

  // Same tiles as the combat picker: pick the card by its picture. This goes
  // in `pending` (a block) rather than `row` (a flex line) — inside the flex
  // row the grid has no definite width and collapses to a single column.
  const grid = document.createElement("div");
  grid.className = "card-tile-grid";
  grid.style.marginTop = "0.6rem";
  for (const c of hand) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "card-tile";
    tile.title = `${isSwap ? "Give away" : "Discard"} ${c.name}`;
    tile.innerHTML =
      (c.value ? `<span class="card-tile-value">${c.value}</span>` : "") +
      `<span class="card-tile-art">${towerCardIcon(c.name, 40)}</span>` +
      `<span class="card-tile-name">${escapeHtml(c.name)}</span>`;
    tile.addEventListener("click", () =>
      ws.send("change_card", {
        username: you,
        card_id: c.id,
        ...(isSwap ? { target: targetSel?.value } : {}),
      }).catch(noop),
    );
    grid.appendChild(tile);
  }
  pending.appendChild(grid);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!
  ));
}

function renderSplitSeven(
  pending: HTMLElement,
  row: HTMLElement,
  g: import("../state.js").GameSnapshot,
  you: string | null,
  ws: WsClient,
): void {
  const total = Number((g.turn.pending_split as { total?: number } | null)?.total ?? 7);
  pending.innerHTML = `
    <div>Split the roll of <strong>${total}</strong> between yourself and another player.</div>
    <div style="margin-top:0.4rem;display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
      <label>You take:
        <select id="split-nself" style="margin-left:0.25rem"></select>
      </label>
      <label>They take:
        <span id="split-nother">${total}</span>
      </label>
      <label>Target:
        <select id="split-target" style="margin-left:0.25rem"></select>
      </label>
      <label>Leg order:
        <select id="split-order" style="margin-left:0.25rem">
          <option value="self_first" selected>Me first</option>
          <option value="target_first">Them first</option>
        </select>
      </label>
    </div>
  `;
  const selSelf = pending.querySelector<HTMLSelectElement>("#split-nself")!;
  const spanOther = pending.querySelector<HTMLElement>("#split-nother")!;
  const selTarget = pending.querySelector<HTMLSelectElement>("#split-target")!;
  // n_self: 1..total (must take at least 1 per Outrage rules).
  for (let i = 1; i <= total; i++) {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = String(i);
    selSelf.appendChild(o);
  }
  selSelf.value = String(total);
  const updateOther = () => {
    const nself = Number(selSelf.value);
    spanOther.textContent = String(total - nself);
    selTarget.disabled = total - nself === 0;
  };
  selSelf.addEventListener("change", updateOther);

  const none = document.createElement("option");
  none.value = "";
  none.textContent = "—";
  selTarget.appendChild(none);
  for (const p of g.players) {
    if (p.username === you || p.escaped) continue;
    const o = document.createElement("option");
    o.value = p.username;
    o.textContent = p.username;
    selTarget.appendChild(o);
  }
  updateOther();

  row.appendChild(button("Commit split", () => {
    const nself = Number(selSelf.value);
    const nother = total - nself;
    const target = selTarget.value || undefined;
    const selOrder = pending.querySelector<HTMLSelectElement>("#split-order")!;
    const legOrder = selOrder.value;
    if (nother > 0 && !target) {
      pending.querySelector<HTMLElement>("#split-nother")!.style.color = "var(--danger)";
      return;
    }
    ws.send("assign_split_seven", {
      username: you, n_self: nself, n_other: nother, target, leg_order: legOrder,
    }).catch(noop);
  }));
}

// ---------------------------------------------------------------------------
// Raven effect resolution
// ---------------------------------------------------------------------------
//
// When the server enters ``RAVEN_EFFECT``, ``state.game.turn.pending_raven``
// carries ``{ effect_key, card_id, params, drawer }``. The drawer is always
// the current-turn player. Each interactive effect takes a specific set of
// params that we forward via the ``resolve_raven_effect`` intent under
// ``{ username, params: { ... } }``. See ``server/game/cards_effects.py``
// for the canonical param keys.

const WARDER_POSTS: { id: string; label: string }[] = [
  { id: "scaffold", label: "Scaffold" },
  { id: "lanthorn", label: "Lanthorn" },
  { id: "waterloo", label: "Waterloo" },
  { id: "chapel", label: "Chapel" },
];

function renderRavenEffect(
  pending: HTMLElement,
  row: HTMLElement,
  state: ClientState,
  ws: WsClient,
): void {
  const g = state.game!;
  const pr = g.turn.pending_raven;
  const you = state.you;
  if (!pr) {
    pending.textContent = "Raven effect pending…";
    return;
  }
  if (pr.drawer !== you) {
    pending.textContent = `Waiting for ${pr.drawer} to resolve the raven effect.`;
    return;
  }
  const sendResolve = (params: Record<string, unknown>) =>
    ws.send("resolve_raven_effect", { username: you, params }).catch(noop);

  switch (pr.effect_key) {
    case "go_to_location": {
      if ((pr.params as { location?: string }).location !== "player_choice") {
        pending.textContent = "Resolving raven card…";
        return;
      }
      pending.innerHTML = `<div>Raven — choose any space to move to.</div>`;
      const sel = document.createElement("select");
      sel.style.marginTop = "0.4rem";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "—";
      sel.appendChild(placeholder);
      const spaces = [...(state.board?.spaces ?? [])].sort((a, b) => a.label.localeCompare(b.label));
      for (const s of spaces) {
        const o = document.createElement("option");
        o.value = s.id;
        o.textContent = s.label || s.id;
        sel.appendChild(o);
      }
      pending.appendChild(sel);
      row.appendChild(button("Go", () => {
        if (!sel.value) return;
        sendResolve({ chosen: sel.value });
      }));
      break;
    }

    case "call_warder_to_post": {
      if ((pr.params as { post?: string }).post !== "chooser") {
        pending.textContent = "Resolving raven card…";
        return;
      }
      // A post holds one warder, so only offer the empty ones — the server
      // rejects an occupied choice anyway.
      const postSpace = new Map(
        (state.board?.warder_posts ?? []).map((wp) => [wp.id, wp.space_id]),
      );
      const barracks = state.board?.barracks_space;
      const manned = new Set(
        g.warders.filter((w) => w.location !== barracks).map((w) => w.location),
      );
      const free = WARDER_POSTS.filter((p) => {
        const sp = postSpace.get(p.id);
        return sp ? !manned.has(sp) : true;
      });
      if (free.length === 0) {
        pending.textContent = "Every post is already manned — nothing to do.";
        row.appendChild(button("Continue", () => sendResolve({})));
        break;
      }
      pending.textContent = "Raven — pick which post to call a warder to:";
      for (const p of free) {
        row.appendChild(button(p.label, () => sendResolve({ chosen_post: p.id })));
      }
      break;
    }

    case "return_warder_to_barracks": {
      const barracks = state.board?.barracks_space;
      const out = g.warders.filter((w) => w.location !== barracks);
      if (out.length === 0) {
        pending.textContent = "No warders to return; resolving…";
        row.appendChild(button("Dismiss", () => sendResolve({})));
        break;
      }
      pending.textContent = "Raven — pick a warder to send to the Barracks:";
      for (const w of out) {
        const spaceLabel = labelFor(state, w.location);
        row.appendChild(button(`${w.id} (at ${spaceLabel})`, () =>
          sendResolve({ warder_id: w.id }),
        ));
      }
      break;
    }

    case "rest_on_bench": {
      const benches = state.board?.bench_space_ids ?? [];
      if (benches.length <= 1) {
        pending.textContent = "Resolving…";
        row.appendChild(button("Dismiss", () => sendResolve({})));
        break;
      }
      pending.textContent = "Raven — pick a bench to rest on (miss next turn):";
      for (const b of benches) {
        row.appendChild(button(labelFor(state, b), () => sendResolve({ bench: b })));
      }
      break;
    }

    case "photo_with_warder": {
      const barracks = state.board?.barracks_space;
      const occupied = g.warders
        .filter((w) => w.location !== barracks)
        .map((w) => w.location);
      const bySpace = new Map<string, { id: string; label: string; neighbors: string[] }>();
      for (const sp of state.board?.spaces ?? []) bySpace.set(sp.id, sp);
      const candidates = new Set<string>();
      for (const ps of occupied) {
        for (const n of bySpace.get(ps)?.neighbors ?? []) candidates.add(n);
      }
      if (candidates.size === 0) {
        pending.textContent = "No post has a warder; dismissing…";
        row.appendChild(button("Dismiss", () => sendResolve({})));
        break;
      }
      pending.textContent = "Raven — pick a square adjacent to a manned post:";
      for (const c of [...candidates].sort()) {
        row.appendChild(button(labelFor(state, c), () => sendResolve({ destination: c })));
      }
      break;
    }

    case "stopped_and_searched": {
      const me = g.players.find((p) => p.username === you);
      const hasDisguise = !!me?.hand.some((c) => c.name?.toLowerCase() === "disguise");
      pending.innerHTML = `
        <div>Raven — Stopped and Searched.</div>
        <div style="margin-top:0.25rem">
          You are carrying a jewel. Play a <strong>Disguise</strong> or forfeit all
          jewels + weapons and go to the Bloody Tower.
        </div>
      `;
      const dBtn = button("Play Disguise", () => sendResolve({ play_disguise: true }));
      if (!hasDisguise) {
        dBtn.disabled = true;
        dBtn.title = "No Disguise card in hand";
      }
      row.appendChild(dBtn);
      row.appendChild(button("Forfeit (go to Bloody Tower)", () => sendResolve({ play_disguise: false })));
      break;
    }

    default:
      // Non-interactive or already-resolved effect. Most of the 19 effect
      // keys are fire-and-forget; the engine only pauses for the handful
      // above. Offer a dismiss button as a safety valve.
      pending.textContent = `Raven effect (${pr.effect_key}) — nothing to choose.`;
      row.appendChild(button("Dismiss", () => sendResolve({})));
      break;
  }
}

function labelFor(state: ClientState, spaceId: string): string {
  const sp = state.board?.spaces.find((s) => s.id === spaceId);
  return sp?.label || spaceId;
}

// ---------------------------------------------------------------------------
// Pre-roll card play
// ---------------------------------------------------------------------------
//
// The engine accepts ``play_card_pre_roll`` in TURN_START / PRE_ROLL /
// ACCREDITATION_ATTEMPT for tower cards whose category isn't weapon/burglary.
// Param shapes (see ``server/game/cards_effects.py``):
//   tower_pass        -> { mode: "accredit" | "extra_turn" }
//   sanctuary         -> {}
//   disguise          -> {} (explicit play; implicit consumption elsewhere)
//   firecrackers      -> {} (must be in white_tower)
//   lasso             -> { target: <username> }
//   binary_disruption -> {}

const PRE_ROLL_PLAYABLE_EFFECTS = new Set([
  "tower_pass",
  "sanctuary",
  "disguise",
  "firecrackers",
  "lasso",
  "binary_disruption",
]);

function renderPreRollCardButtons(
  pending: HTMLElement,
  g: import("../state.js").GameSnapshot,
  me: import("../state.js").GamePlayer | null,
  you: string | null,
  state: ClientState,
  ws: WsClient,
): void {
  if (!me || !you) return;
  const playable = me.hand.filter(
    (c) => c.kind === "tower" && c.effect_key && PRE_ROLL_PLAYABLE_EFFECTS.has(c.effect_key),
  );
  if (playable.length === 0) return;

  const section = document.createElement("div");
  section.style.marginTop = "0.5rem";
  section.style.paddingTop = "0.5rem";
  section.style.borderTop = "1px solid var(--border, #333)";
  section.innerHTML = `<div style="font-size:0.8rem;color:var(--muted);margin-bottom:0.3rem">Play a card:</div>`;
  const cardsRow = document.createElement("div");
  cardsRow.style.display = "flex";
  cardsRow.style.flexDirection = "column";
  cardsRow.style.gap = "0.3rem";
  section.appendChild(cardsRow);

  const atQueens = state.board && me.position === state.board.queens_house_space;
  const inWhiteTower =
    state.board?.spaces.find((s) => s.id === me.position)?.region === "white_tower";
  const alreadyArmed = !!g.turn.binary_disruption_armed;

  const playedIds = new Set<string>();
  for (const card of playable) {
    const wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.gap = "0.3rem";
    wrap.style.alignItems = "center";
    wrap.style.flexWrap = "wrap";

    const send = (params: Record<string, unknown>) => {
      if (playedIds.has(card.id)) return;
      playedIds.add(card.id);
      ws.send("play_card_pre_roll", { username: you, card_id: card.id, params }).catch(noop);
    };

    switch (card.effect_key) {
      case "tower_pass": {
        const label = document.createElement("span");
        label.textContent = `${card.name}:`;
        label.style.fontSize = "0.85rem";
        wrap.appendChild(label);
        const bAcc = button("Accredit", () => send({ mode: "accredit" }));
        if (!atQueens) {
          bAcc.disabled = true;
          bAcc.title = "Must be at Queen's House";
        }
        wrap.appendChild(bAcc);
        wrap.appendChild(button("Extra turn", () => send({ mode: "extra_turn" })));
        break;
      }
      case "sanctuary":
        wrap.appendChild(button(`Play ${card.name} → Chapel Royal`, () => send({})));
        break;
      case "disguise":
        wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        break;
      case "firecrackers": {
        const b = button(`Play ${card.name}`, () => send({}));
        if (!inWhiteTower) {
          b.disabled = true;
          b.title = "Must be in the White Tower";
        }
        wrap.appendChild(b);
        break;
      }
      case "binary_disruption": {
        const b = button(`Play ${card.name}`, () => send({}));
        if (alreadyArmed) {
          b.disabled = true;
          b.title = "Already armed";
        }
        wrap.appendChild(b);
        break;
      }
      case "lasso": {
        const label = document.createElement("span");
        label.textContent = `${card.name} →`;
        label.style.fontSize = "0.85rem";
        wrap.appendChild(label);
        const targets = lassoTargets(state, g, me);
        if (targets.length === 0) {
          const n = document.createElement("span");
          n.textContent = "no targets in range";
          n.style.color = "var(--muted)";
          n.style.fontSize = "0.8rem";
          wrap.appendChild(n);
        } else {
          for (const t of targets) {
            wrap.appendChild(button(t.username, () => send({ target: t.username })));
          }
        }
        break;
      }
    }

    cardsRow.appendChild(wrap);
  }

  pending.appendChild(section);
}

function lassoTargets(
  state: ClientState,
  g: import("../state.js").GameSnapshot,
  me: import("../state.js").GamePlayer,
): import("../state.js").GamePlayer[] {
  // BFS up to 5 hops on the space graph. Server is authoritative; this is
  // a UX filter to avoid showing obviously-out-of-range players.
  const board = state.board;
  if (!board) return [];
  const adj = new Map<string, string[]>();
  for (const sp of board.spaces) adj.set(sp.id, sp.neighbors);
  const visited = new Set<string>([me.position]);
  let frontier = [me.position];
  for (let d = 0; d < 5 && frontier.length; d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const n of adj.get(id) ?? []) {
        if (!visited.has(n)) {
          visited.add(n);
          next.push(n);
        }
      }
    }
    frontier = next;
  }
  return g.players.filter(
    (p) => p.username !== me.username && !p.escaped && visited.has(p.position),
  );
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function noop() { /* errors surface via __error__ */ }
