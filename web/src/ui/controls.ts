/**
 * Turn-action controls (roll, end turn, attempt jewel) rendered into a panel.
 *
 * The set of buttons surfaced is a pure function of the current game phase
 * and whether the viewer is the active player.
 */
import type { WsClient } from "../net/ws.js";
import type { ClientState } from "../state.js";
import { currentTurnUsername, playerByName } from "../state.js";
import { highlightSpace, spaceLabel } from "../board/render.js";
import { towerCardIcon } from "./card_art.js";
import { summonsLocationLabel } from "./card_descriptions.js";

// ---------------------------------------------------------------------------
// Card-trade picker state
// ---------------------------------------------------------------------------
//
// Which cards the player has ticked for a trade, and whether the picker is open
// at all. Local rather than server state — nobody else needs to see a selection
// that hasn't been committed — but module-level, because this panel is
// re-rendered wholesale on every snapshot and DOM state wouldn't survive.

let tradeOpen = false;
const tradePicked = new Set<string>();
/** Turn the picker was opened on, so a new turn closes it. */
let tradeTurnKey = "";

function closeTrade(): void {
  tradeOpen = false;
  tradePicked.clear();
}

export function renderControlsPanel(root: HTMLElement): { update: (state: ClientState, ws: WsClient) => void } {
  root.innerHTML = `
    <div class="panel" id="controls-panel">
      <h3>Turn</h3>
      <div id="turn-info" style="font-size:0.9rem;color:var(--muted);margin-bottom:0.5rem"></div>
      <div id="controls-row" style="display:flex;gap:0.4rem;flex-wrap:wrap"></div>
      <div id="pending-info" style="margin-top:0.5rem;font-size:0.85rem;color:var(--muted)"></div>
      <div id="newgame-row" class="newgame-row"></div>
    </div>
  `;
  return {
    update: (state, ws) => updateControls(root, state, ws),
  };
}

/**
 * "New game", behind a two-click confirm.
 *
 * Ending the game is destructive and the button sits next to ordinary turn
 * actions, so it asks first — inline rather than via window.confirm, which is
 * easy to dismiss on reflex and looks nothing like the rest of the UI.
 *
 * It ends the game as a *draw* rather than jumping straight to the lobby, so
 * the results screen still comes up and everyone gets to read the stats.
 */
function renderNewGameButton(root: HTMLElement, you: string | null, ws: WsClient): void {
  const slot = root.querySelector<HTMLElement>("#newgame-row")!;
  slot.innerHTML = "";
  if (!you) return;

  const ask = button("New game…", () => {
    slot.innerHTML = "";
    const warn = document.createElement("div");
    warn.className = "newgame-warn";
    warn.textContent = "End this game now? It'll be recorded as a draw, and everyone sees the final stats.";
    slot.appendChild(warn);
    const confirmRow = document.createElement("div");
    confirmRow.className = "newgame-confirm";
    confirmRow.appendChild(button("Yes, end it", () =>
      ws.send("end_game_draw", { username: you }).catch(noop),
    ));
    confirmRow.appendChild(button("Cancel", () => renderNewGameButton(root, you, ws)));
    slot.appendChild(confirmRow);
  });
  ask.className = "newgame-btn";
  slot.appendChild(ask);
}

function updateControls(root: HTMLElement, state: ClientState, ws: WsClient): void {
  const info = root.querySelector<HTMLElement>("#turn-info")!;
  const row = root.querySelector<HTMLElement>("#controls-row")!;
  const pending = root.querySelector<HTMLElement>("#pending-info")!;
  row.innerHTML = "";
  pending.textContent = "";
  // Buttons are thrown away and rebuilt here, so any square lit by hovering
  // one of them has nothing left to turn it off again.
  highlightSpace(null);

  const g = state.game;
  root.querySelector<HTMLElement>("#newgame-row")!.innerHTML = "";
  if (!g) {
    info.textContent = "No game in progress.";
    return;
  }

  const cur = currentTurnUsername(g);
  const you = state.you;
  const me = playerByName(g, you);
  const isMyTurn = cur === you;

  // A trade is only offered instead of rolling, so anything that moves the turn
  // on — or moves it past the roll, or locks you up mid-selection — abandons a
  // half-made selection.
  const turnKey = `${cur ?? ""}#${g.turn.roll.join(",")}`;
  const confinedNow = !!me && ["IMPRISONED", "TORTURED", "RACKED"].includes(me.status);
  const tradeable =
    isMyTurn && !confinedNow && (g.phase === "TURN_START" || g.phase === "PRE_ROLL");
  if (!tradeable || turnKey !== tradeTurnKey) {
    if (tradeOpen) closeTrade();
    tradeTurnKey = turnKey;
  }

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

  // Available to everyone, whosever turn it is — abandoning the game isn't a
  // turn action. Rendered last in the panel so it can't be hit by accident.
  renderNewGameButton(root, you, ws);

  if (!isMyTurn) {
    // The raven drawer isn't always the player whose turn it is: a split-7 leg
    // can shove an opponent onto a raven square, and the card that lands is
    // theirs to resolve. Without this the table waits on a prompt that never
    // appears for anyone.
    if (g.phase === "RAVEN_EFFECT" && g.turn.pending_raven?.drawer === you) {
      renderRavenEffect(pending, row, state, ws);
      return;
    }
    // Same again for a theft: a split-7 leg can shove you onto a jewel, or onto
    // a raven square whose card walks you to one. The attempt is yours. Without
    // this the roller was offered it — and took the jewel.
    if (g.phase === "JEWEL_ATTEMPT" && g.turn.pending_jewel?.player === you) {
      renderJewelAttempt(pending, row, me, you, ws);
      return;
    }
    pending.textContent = cur ? `Waiting for ${cur}…` : "";
    // A locked-up player may still buy their way out while somebody else is
    // acting. This is the only window a racked player has at all — their own
    // turn is skipped outright — so without it a Rack Pardon is unplayable.
    if (me && ["IMPRISONED", "TORTURED", "RACKED"].includes(me.status)) {
      renderPreRollCardButtons(pending, g, me, you, state, ws, SELF_RESCUE_EFFECTS);
    }
    return;
  }

  // If we share a square with any other player we may declare combat —
  // except inside the White Tower, where combat is forbidden.
  if (me && ["TURN_START", "PRE_ROLL", "TURN_END"].includes(g.phase)) {
    // Mirrors ``can_fight`` on the server: not in the White Tower, both sides
    // signed in, and something to fight with. Offering the button without all
    // three only produced a rejected intent.
    const mySpace = state.board?.spaces.find((s) => s.id === me.position);
    const canFightAtAll =
      mySpace?.region !== "white_tower" &&
      me.accredited &&
      me.hand.some((c) => c.category === "weapon");
    const coLocated = canFightAtAll
      ? g.players.filter((p) => p.username !== you && p.position === me.position && p.accredited)
      : [];
    for (const enemy of coLocated) {
      row.appendChild(button(`Attack ${enemy.username}`, () =>
        ws.send("initiate_combat", { username: you, target: enemy.username }).catch(noop),
      ));
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
      // On trial at Queen's House: this roll decides accreditation, so say so
      // and label the button for it. Driven off the player flag rather than a
      // phase — Phase.ACCREDITATION_ATTEMPT is never actually assigned.
      const onTrial = !missing && !!me?.trying_accreditation && !me?.accredited;
      if (missing) {
        const why = document.createElement("div");
        why.style.marginBottom = "0.4rem";
        why.style.color = "var(--muted)";
        why.style.fontSize = "0.85rem";
        why.textContent = "You're missing this turn.";
        pending.appendChild(why);
      } else {
        row.appendChild(button(
          onTrial ? "Roll for accreditation" : "Roll dice",
          () => ws.send("roll_dice", { username: you }).catch(noop),
        ));
      }
      row.appendChild(button("End turn", () => ws.send("end_turn", { username: you }).catch(noop)));
      if (onTrial) {
        const tip = document.createElement("div");
        tip.style.marginTop = "0.4rem";
        tip.style.fontSize = "0.85rem";
        tip.style.color = "var(--muted)";
        tip.innerHTML =
          `<strong style="color:var(--accent)">Accreditation trial at Queen's House.</strong><br>` +
          `<strong>Odd total</strong> → accredited; use the roll to move freely in the Inner Ward.<br>` +
          `<strong>Even total</strong> → the clerks send you away and your turn ends.`;
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
      // Trade cards instead of rolling: stay put, hand in n cards, take n - 1
      // back. Sits next to Roll because it is the alternative to it.
      // The server refuses a trade from a prisoner, and the trade costs a card
      // so it needs two to be worth anything. Neither is offered when it can't
      // be taken up.
      const canTrade = !!me && !missing && !confinedNow && (me.hand.length >= 2 || tradeOpen);
      if (canTrade) {
        row.appendChild(button(
          tradeOpen ? "Cancel trade" : "Trade cards…",
          () => {
            if (tradeOpen) closeTrade();
            else { tradeOpen = true; tradePicked.clear(); }
            updateControls(root, state, ws);
          },
        ));
      }
      if (tradeOpen && me) renderTradePicker(pending, me, you, state, ws, root);
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
          row.appendChild(spaceButton(d, named(state, d), () =>
            ws.send("choose_move_path", { username: you, destination: d }).catch(noop)));
        }
      } else {
        // Normal: roller picking their own destination.
        // Which destinations land on an enemy? (pass-through combat stops.)
        const enemyAt = new Map<string, string>();
        for (const p of g.players) {
          if (p.username !== you) enemyAt.set(p.position, p.username);
        }
        const combatStops = keys.filter((k) => enemyAt.has(k));
        // Routes the server will charge a Disguise for. They're offered
        // alongside the free ones precisely so the card can be held back until
        // the roll is known to be big enough to be worth spending it on.
        const needsDisguise = new Set(pm?.requires_disguise ?? []);
        // The Cradle Tower is not an ordinary square to a coin-holder: landing
        // on it banks your jewels but costs you the coin and your whole hand,
        // and there is no taking it back. Say so before they click.
        const exitId = state.board?.escape_space ?? null;
        const exitOffered = !!exitId && keys.includes(exitId) && !!me?.has_coin;
        pending.innerHTML =
          `<div>Click a highlighted square (${keys.length} option${keys.length === 1 ? "" : "s"}) or pick here:</div>` +
          (combatStops.length
            ? `<div style="margin-top:0.25rem;color:var(--accent);font-size:0.8rem">` +
              `Destinations marked <strong>[fight]</strong> stop at an enemy and end your turn after combat.</div>`
            : "") +
          (needsDisguise.size
            ? `<div style="margin-top:0.25rem;color:var(--accent);font-size:0.8rem">` +
              `Destinations marked <strong>[uses Disguise]</strong> slip past a Yeoman Warder ` +
              `and spend the card. Dashed outline on the board.</div>`
            : "") +
          (exitOffered
            ? `<div style="margin-top:0.25rem;color:var(--accent);font-size:0.8rem">` +
              `<strong>[bank &amp; restart]</strong> banks ` +
              `${me!.jewels.length ? `your ${me!.jewels.length} jewel${me!.jewels.length === 1 ? "" : "s"}` : "nothing"} ` +
              `for good, then takes your coin and all ${me!.hand.length} of your cards. ` +
              `You are dealt a fresh hand and start again from the Start square, still accredited.</div>`
            : "");
        for (const d of keys) {
          const enemy = enemyAt.get(d);
          const isExit = exitOffered && d === exitId;
          const tags =
            (enemy ? "[fight] " : "") + (needsDisguise.has(d) ? "[uses Disguise] " : "") +
            (isExit ? "[bank & restart] " : "");
          const label = `${tags}${named(state, d)}${enemy ? ` (vs ${enemy})` : ""}`;
          row.appendChild(spaceButton(d, label, () => ws.send("choose_move_path", { username: you, destination: d }).catch(noop)));
        }
      }
      break;
    }

    case "JEWEL_ATTEMPT": {
      renderJewelAttempt(pending, row, me, you, ws);
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
      // Some cards only become worth playing — or legal at all — once the move
      // is over: the extra turn you now know you need, the retreat from where
      // you actually landed, the Confession answering a Bowyer Tower sentence.
      renderPreRollCardButtons(pending, g, me, you, state, ws, POST_MOVE_PLAYABLE_EFFECTS);
      break;
  }
}

/**
 * The "attempt the jewel" panel.
 *
 * Rendered for whoever the pending attempt names, which is not always the
 * player whose turn it is.
 */
function renderJewelAttempt(
  pending: HTMLElement,
  row: HTMLElement,
  me: import("../state.js").GamePlayer | null,
  you: string | null,
  ws: WsClient,
): void {
  const tools = (me?.hand ?? []).filter((c) => c.category === "burglary");
  const bonus = tools.reduce((a, c) => a + (c.value ?? 0), 0);
  pending.innerHTML =
    `<div>Jewel attempt: ${tools.length} burglary tool(s) available (total +${bonus}).</div>` +
    `<div style="margin-top:0.25rem">Roll ${Math.max(2, 12 - bonus)}+ on two dice. ` +
    `Tools are never used up, and a failed attempt leaves you on the jewel — ` +
    `you can try again next turn. Roll a double and you take another turn.</div>`;
  if (tools.length) {
    row.appendChild(
      button("Attempt with all tools", () =>
        ws.send("attempt_jewel", { username: you, tool_card_ids: tools.map((c) => c.id) }).catch(noop),
      ),
    );
  }
  row.appendChild(
    button(tools.length ? "Attempt without tools" : "Attempt the theft", () =>
      ws.send("attempt_jewel", { username: you, tool_card_ids: [] }).catch(noop),
    ),
  );
}

/**
 * A destination as a player would name it.
 *
 * These buttons showed the raw space id — "iw_8_11" — which is a database key,
 * not a place. Falls back to the id only if the board hasn't loaded yet.
 */
function named(state: ClientState, spaceId: string): string {
  return state.board ? (spaceLabel(state.board, spaceId) || spaceId) : spaceId;
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

/**
 * Multi-select card picker for a trade.
 *
 * Same tiles as the swap/discard prompts, but toggling rather than committing on
 * click: the whole point of the rule is choosing a *set*, and the count you get
 * back (one fewer than you hand in) has to be visible while you choose.
 */
function renderTradePicker(
  pending: HTMLElement,
  me: import("../state.js").GamePlayer,
  you: string | null,
  state: ClientState,
  ws: WsClient,
  root: HTMLElement,
): void {
  const hand = me.hand ?? [];
  // Cards may have left the hand since the last click (a swap, a fight).
  for (const id of [...tradePicked]) {
    if (!hand.some((c) => c.id === id)) tradePicked.delete(id);
  }
  const n = tradePicked.size;
  const back = Math.max(0, n - 1);

  const box = document.createElement("div");
  box.style.marginTop = "0.5rem";
  box.style.paddingTop = "0.5rem";
  box.style.borderTop = "1px solid var(--border, #333)";
  box.innerHTML =
    `<div>Hand in any number of cards and draw back <strong>one fewer</strong>. ` +
    `You stay where you are and don't roll.</div>` +
    `<div style="margin-top:0.3rem;color:${n >= 2 ? "var(--accent)" : "var(--muted)"}">` +
    (n === 0
      ? "Pick the cards you want rid of."
      : n === 1
        ? "1 selected — a single card would buy you nothing. Pick another."
        : `${n} selected → you draw ${back} card${back === 1 ? "" : "s"}.`) +
    `</div>`;

  const grid = document.createElement("div");
  grid.className = "card-tile-grid";
  grid.style.marginTop = "0.6rem";
  for (const c of hand) {
    const picked = tradePicked.has(c.id);
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "card-tile" + (picked ? " is-selected" : "");
    tile.setAttribute("aria-pressed", picked ? "true" : "false");
    tile.title = picked ? `Keep ${c.name}` : `Trade away ${c.name}`;
    tile.innerHTML =
      (c.value ? `<span class="card-tile-value">${c.value}</span>` : "") +
      `<span class="card-tile-art">${towerCardIcon(c.name, 40)}</span>` +
      `<span class="card-tile-name">${escapeHtml(c.name)}</span>`;
    tile.addEventListener("click", () => {
      if (tradePicked.has(c.id)) tradePicked.delete(c.id);
      else tradePicked.add(c.id);
      updateControls(root, state, ws);
    });
    grid.appendChild(tile);
  }
  box.appendChild(grid);

  const confirm = button(
    n >= 2 ? `Trade ${n} for ${back}` : "Trade",
    () => {
      const ids = [...tradePicked];
      closeTrade();
      ws.send("redraw_cards", { username: you, card_ids: ids }).catch(noop);
    },
  );
  confirm.disabled = n < 2;
  confirm.style.marginTop = "0.5rem";
  box.appendChild(confirm);
  pending.appendChild(box);
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
  const split = g.turn.pending_split;
  const total = Number(split?.total ?? 7);
  // The server works out who a given leg size could actually move; anyone
  // boxed in (an un-accredited piece stuck on Queen's House, say) simply
  // isn't offered, and a leg they can't use isn't either.
  const movable = split?.movable_targets ?? {};
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
    <div id="split-note" style="margin-top:0.35rem;font-size:0.8rem;color:var(--muted)"></div>
  `;
  const selSelf = pending.querySelector<HTMLSelectElement>("#split-nself")!;
  const spanOther = pending.querySelector<HTMLElement>("#split-nother")!;
  const selTarget = pending.querySelector<HTMLSelectElement>("#split-target")!;
  const note = pending.querySelector<HTMLElement>("#split-note")!;

  // Leg sizes somebody could use. total (= keep it all) is always available.
  const usableOther = new Set<number>([0]);
  for (const legs of Object.values(movable)) for (const n of legs) usableOther.add(n);
  for (let i = 1; i <= total; i++) {
    if (!usableOther.has(total - i)) continue;
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = String(i);
    selSelf.appendChild(o);
  }
  selSelf.value = String(total);

  const refreshTargets = () => {
    const nother = total - Number(selSelf.value);
    spanOther.textContent = String(nother);
    const previous = selTarget.value;
    selTarget.innerHTML = "";
    if (nother === 0) {
      selTarget.disabled = true;
      note.textContent = "";
      return;
    }
    const eligible = g.players.filter(
      (p) => p.username !== you && (movable[p.username] ?? []).includes(nother),
    );
    // With exactly one player this leg could move there is nothing to decide,
    // so fill it in and lock the picker rather than making the roller confirm
    // the only legal answer. (Zero movable players never reaches this screen —
    // ``_intent_roll_dice`` hands the roller the whole roll instead.)
    const forced = eligible.length === 1;
    selTarget.disabled = forced;
    if (!forced) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "—";
      selTarget.appendChild(none);
    }
    for (const p of eligible) {
      const o = document.createElement("option");
      o.value = p.username;
      o.textContent = p.username;
      selTarget.appendChild(o);
    }
    if (forced) selTarget.value = eligible[0].username;
    else if (eligible.some((p) => p.username === previous)) selTarget.value = previous;
    const stuck = g.players.filter(
      (p) => p.username !== you && !(movable[p.username] ?? []).length,
    );
    note.textContent = stuck.length
      ? `${stuck.map((p) => p.username).join(", ")} can't be moved at all this roll.`
      : "";
  };
  selSelf.addEventListener("change", refreshTargets);
  refreshTargets();

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
      // A Summons can always be refused — the cost is your next turn.
      const loc = (pr.params as { location?: string }).location;
      const anywhere = loc === "player_choice";
      const destLabel = anywhere ? "a tower of your choice" : summonsLocationLabel(loc);
      pending.innerHTML =
        `<div>Raven — <strong>Summons</strong> to ${escapeHtml(destLabel)}.</div>` +
        `<div style="margin-top:0.25rem">Obey it, or refuse and miss your next turn.</div>`;
      if (anywhere) {
        // "Any tower you like" means a tower — the squares that deal you a
        // tower card — not any square on the board, which is what the old
        // dropdown offered (and the server used to accept).
        const towers = towerSpaces(state);
        if (towers.length === 0) {
          pending.innerHTML += `<div style="margin-top:0.25rem">No tower to go to.</div>`;
        }
        for (const t of towers) {
          row.appendChild(spaceButton(t.id, t.label, () =>
            sendResolve({ accept: true, chosen: t.id })));
        }
      } else {
        row.appendChild(button("Obey the summons", () => sendResolve({ accept: true })));
      }
      row.appendChild(button("Refuse (miss next turn)", () => sendResolve({ decline: true })));
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
        const sp = postSpace.get(p.id);
        row.appendChild(sp
          ? spaceButton(sp, p.label, () => sendResolve({ chosen_post: p.id }))
          : button(p.label, () => sendResolve({ chosen_post: p.id })));
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
        row.appendChild(spaceButton(w.location, `${w.id} (at ${spaceLabel})`, () =>
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
        row.appendChild(spaceButton(b, labelFor(state, b), () => sendResolve({ bench: b })));
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
        row.appendChild(spaceButton(c, labelFor(state, c), () => sendResolve({ destination: c })));
      }
      break;
    }

    case "stopped_and_searched": {
      // Match on effect_key, not the printed name: the server spends the card
      // by effect, so a rename would have left this offering a button the
      // server then refused.
      const me = g.players.find((p) => p.username === you);
      const hasDisguise = !!me?.hand.some((c) => c.effect_key === "disguise");
      pending.innerHTML = `
        <div>Raven — Stopped and Searched.</div>
        <div style="margin-top:0.25rem">
          You are carrying a jewel. Show a <strong>Disguise</strong> — it is spent —
          or forfeit every jewel and weapon and go to the Bloody Tower.
        </div>
      `;
      // With no Disguise in hand there is no choice to make, so don't dangle
      // one — the forfeit is the only button.
      if (hasDisguise) {
        row.appendChild(button("Show a Disguise", () => sendResolve({ play_disguise: true })));
      }
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

/**
 * The squares a Summons can send you to: everywhere that deals a tower card.
 *
 * Read off the same board rules the server uses (``tower_card_draw_kinds`` and
 * its exception list) so the two can't drift apart.
 */
function towerSpaces(state: ClientState): { id: string; label: string }[] {
  const rules = (state.board?.rules ?? {}) as {
    tower_card_draw_kinds?: string[];
    tower_card_draw_exception_space_ids?: string[];
  };
  const kinds = new Set(rules.tower_card_draw_kinds ?? ["tower"]);
  const skip = new Set(rules.tower_card_draw_exception_space_ids ?? []);
  return (state.board?.spaces ?? [])
    .filter((s) => kinds.has(s.kind) && !skip.has(s.id))
    .map((s) => ({ id: s.id, label: s.label || s.id }))
    .sort((a, b) => a.label.localeCompare(b.label));
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
  // Escape hatches. These were missing entirely, so a locked-up player holding
  // a Pardon or a Confession had no way to play it.
  "royal_pardon",
  "rack_pardon",
  "traversal_beauchamp_escape",
  "confession",
]);

// Mirrors SELF_RESCUE_EFFECTS in server/game/rules.py: cards a confined player
// may play at any moment, their turn or not.
const SELF_RESCUE_EFFECTS = new Set([
  "royal_pardon",
  "rack_pardon",
  "traversal_beauchamp_escape",
  "disguise",
]);

// Mirrors POST_MOVE_PLAYABLE_EFFECTS in server/game/rules.py: what's still
// legal at TURN_END, once the dice are down and the move is over. Keep the two
// in step — the server rejects anything outside its own set.
const POST_MOVE_PLAYABLE_EFFECTS = new Set([
  "tower_pass",
  "sanctuary",
  "confession",
  "royal_pardon",
  "rack_pardon",
  "traversal_beauchamp_escape",
  "disguise",
]);

function renderPreRollCardButtons(
  pending: HTMLElement,
  g: import("../state.js").GameSnapshot,
  me: import("../state.js").GamePlayer | null,
  you: string | null,
  state: ClientState,
  ws: WsClient,
  allowed: Set<string> = PRE_ROLL_PLAYABLE_EFFECTS,
): void {
  if (!me || !you) return;
  const playable = me.hand.filter(
    (c) => c.kind === "tower" && c.effect_key && allowed.has(c.effect_key),
  );
  if (playable.length === 0) return;

  const postMove = allowed === POST_MOVE_PLAYABLE_EFFECTS;
  // Cards whose conditions aren't met are left out of this list entirely
  // rather than greyed out. A row of dead buttons reads as "the game is
  // broken"; a shorter list reads as "not yet". The card itself is still in
  // your hand panel, where hovering its name says what it does.
  const section = document.createElement("div");
  section.style.marginTop = "0.5rem";
  section.style.paddingTop = "0.5rem";
  section.style.borderTop = "1px solid var(--border, #333)";
  section.innerHTML =
    `<div style="font-size:0.8rem;color:var(--muted);margin-bottom:0.3rem">` +
    (postMove ? "Play a card before you end your turn:" : "Play a card:") +
    `</div>`;
  const cardsRow = document.createElement("div");
  cardsRow.style.display = "flex";
  cardsRow.style.flexDirection = "column";
  cardsRow.style.gap = "0.3rem";
  section.appendChild(cardsRow);

  const atQueens = state.board && me.position === state.board.queens_house_space;
  const inWhiteTower =
    state.board?.spaces.find((s) => s.id === me.position)?.region === "white_tower";
  const alreadyArmed = !!g.turn.binary_disruption_armed;
  const status = me.status;
  const confined = status === "IMPRISONED" || status === "TORTURED" || status === "RACKED";
  const atBeauchamp = state.board && me.position === state.board.beauchamp_tower_space;
  // Confession swaps you with someone who is walking free — you can't hand your
  // sentence to a player already behind a different door, nor reach into the
  // White Tower, which nothing may drag a player out of.
  const whiteTowerSpaces = new Set(
    (state.board?.spaces ?? []).filter((s) => s.region === "white_tower").map((s) => s.id),
  );
  const frameable = g.players.filter(
    (p) =>
      p.username !== you &&
      !whiteTowerSpaces.has(p.position) &&
      !["IMPRISONED", "TORTURED", "RACKED"].includes(p.status),
  );

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
        // Nothing to buy if the clerks have already signed you in — the
        // button used to be offered anyway and burned the card for nothing.
        if (atQueens && !me.accredited) {
          wrap.appendChild(button("Accredit", () => send({ mode: "accredit" })));
        }
        wrap.appendChild(button("Extra turn", () => send({ mode: "extra_turn" })));
        break;
      }
      case "sanctuary": {
        // Chapel Royal is in the Inner Ward, which is closed to you until the
        // clerks have signed you in — and locked away entirely if you are.
        if (!me.accredited || confined) break;
        wrap.appendChild(button(`Play ${card.name} → Chapel Royal`, () => send({})));
        break;
      }
      case "royal_pardon": {
        if (status !== "IMPRISONED" && status !== "TORTURED") break;
        wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        break;
      }
      case "rack_pardon": {
        if (status !== "RACKED") break;
        wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        break;
      }
      case "traversal_beauchamp_escape": {
        if (status !== "IMPRISONED" || !atBeauchamp) break;
        wrap.appendChild(button(`Escape with the ${card.name}`, () => send({})));
        break;
      }
      case "confession": {
        if (status !== "TORTURED" || frameable.length === 0) break;
        const label = document.createElement("span");
        label.textContent = `${card.name} — frame:`;
        label.style.fontSize = "0.85rem";
        wrap.appendChild(label);
        for (const t of frameable) {
          wrap.appendChild(button(t.username, () => send({ target: t.username })));
        }
        break;
      }
      case "disguise": {
        // Two uses, and which one you get is decided by where you are: a
        // prisoner cannot move, so free passage past a post is worth nothing
        // to them and the card is their way out instead. It answers prison
        // only, so to anyone held on the Rack or under questioning it does
        // nothing at all.
        if (status === "IMPRISONED") {
          wrap.appendChild(button(`Walk out of prison with the ${card.name}`, () => send({})));
        } else if (!confined) {
          wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        }
        break;
      }
      case "firecrackers": {
        if (!inWhiteTower) break;
        wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        break;
      }
      case "binary_disruption": {
        if (alreadyArmed) break;
        wrap.appendChild(button(`Play ${card.name}`, () => send({})));
        break;
      }
      case "lasso": {
        const targets = lassoTargets(state, g, me);
        if (targets.length === 0) break;
        const label = document.createElement("span");
        label.textContent = `${card.name} →`;
        label.style.fontSize = "0.85rem";
        wrap.appendChild(label);
        for (const t of targets) {
          wrap.appendChild(button(t.username, () => send({ target: t.username })));
        }
        break;
      }
    }

    // Nothing playable about this card right now — drop the whole row, label
    // and all, rather than leaving a stub with no button on it.
    if (wrap.querySelector("button")) cardsRow.appendChild(wrap);
  }

  if (!cardsRow.querySelector("button")) return;
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
    (p) => p.username !== me.username && visited.has(p.position),
  );
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

/**
 * A button that names a board square, and lights that square up while pointed at.
 *
 * Several squares share a name — there are two benches, and a list reading
 * "Bench, Bench" tells you nothing about which is which. Rather than inventing
 * disambiguating labels, point at the board: hovering (or tabbing to) the
 * button flashes the square it refers to.
 */
function spaceButton(spaceId: string, label: string, onClick: () => void): HTMLButtonElement {
  const b = button(label, onClick);
  const on = () => highlightSpace(spaceId);
  const off = () => highlightSpace(null);
  b.addEventListener("mouseenter", on);
  b.addEventListener("mouseleave", off);
  b.addEventListener("focus", on);
  b.addEventListener("blur", off);
  // Clicking commits the move and re-renders the panel; the button goes away
  // without ever firing mouseleave, stranding the highlight on the board.
  b.addEventListener("click", off);
  return b;
}

function noop() { /* errors surface via __error__ */ }
