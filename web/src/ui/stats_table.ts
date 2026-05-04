/**
 * Shared stats-table renderer used by both the lobby and login screens.
 *
 * Fetches ``/api/stats`` once and renders a compact table into the supplied
 * element. Pass ``youUsername`` to highlight the current player's row.
 */

export interface LifetimeStats {
  username: string;
  games_played: number;
  wins: number;
  jewels_stolen: number;
  coins_stolen: number;
  combat_wins: number;
  combat_losses: number;
  racked_count: number;
  imprisoned_count: number;
  tower_cards_gained: number;
  raven_cards_triggered: number;
  doubles_rolled: number;
  total_dice_rolls: number;
}

export function emptyStats(username: string): LifetimeStats {
  return {
    username, games_played: 0, wins: 0,
    jewels_stolen: 0, coins_stolen: 0, combat_wins: 0, combat_losses: 0,
    racked_count: 0, imprisoned_count: 0,
    tower_cards_gained: 0, raven_cards_triggered: 0, doubles_rolled: 0, total_dice_rolls: 0,
  };
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}

/**
 * Render ``cache`` into ``el``. If ``cache`` is null, shows a loading
 * placeholder. ``youUsername`` highlights that row in accent colour.
 */
export function renderStatsTable(
  el: HTMLElement,
  cache: Record<string, LifetimeStats> | null,
  youUsername?: string | null,
): void {
  if (cache === null) { el.textContent = "Loading…"; return; }

  const allNames = Object.keys(cache);
  if (youUsername && !allNames.includes(youUsername)) allNames.push(youUsername);
  if (allNames.length === 0) { el.textContent = "No stats yet."; return; }

  const rows = allNames
    .map((n) => cache[n] ?? emptyStats(n))
    .sort((a, b) =>
      b.wins - a.wins ||
      b.games_played - a.games_played ||
      a.username.localeCompare(b.username),
    );

  el.innerHTML = [
    `<table style="width:100%;border-collapse:collapse;font-size:0.8rem">`,
    `<thead><tr style="color:var(--muted);text-align:left;border-bottom:1px solid var(--border)">` +
      `<th style="padding:2px 4px">Player</th>` +
      `<th style="padding:2px 4px" title="Games played">G</th>` +
      `<th style="padding:2px 4px" title="Wins">W</th>` +
      `<th style="padding:2px 4px" title="Jewels stolen">💎</th>` +
      `<th style="padding:2px 4px" title="Combat wins / losses">⚔</th>` +
      `<th style="padding:2px 4px" title="Times racked / imprisoned">🔒</th>` +
      `<th style="padding:2px 4px" title="Tower cards gained">🗼</th>` +
      `<th style="padding:2px 4px" title="Raven cards triggered">🐦</th>` +
      `<th style="padding:2px 4px" title="Doubles rolled / total pips rolled">🎲</th>` +
      `</tr></thead>`,
    `<tbody>`,
    ...rows.map((s) => {
      const mine = s.username === youUsername ? ` style="color:var(--accent)"` : "";
      return (
        `<tr${mine}>` +
        `<td style="padding:2px 4px">${escapeHtml(s.username)}</td>` +
        `<td style="padding:2px 4px">${s.games_played}</td>` +
        `<td style="padding:2px 4px">${s.wins}</td>` +
        `<td style="padding:2px 4px">${s.jewels_stolen}</td>` +
        `<td style="padding:2px 4px">${s.combat_wins}/${s.combat_losses}</td>` +
        `<td style="padding:2px 4px">${s.racked_count}/${s.imprisoned_count}</td>` +
        `<td style="padding:2px 4px">${s.tower_cards_gained}</td>` +
        `<td style="padding:2px 4px">${s.raven_cards_triggered}</td>` +
        `<td style="padding:2px 4px">${s.doubles_rolled}/${s.total_dice_rolls}</td>` +
        `</tr>`
      );
    }),
    `</tbody></table>`,
  ].join("");
}

/**
 * Fetch ``/api/stats`` and render the result into ``el``. Resolves once
 * rendered; never rejects (shows "Unable to load" on fetch failure).
 */
export async function fetchAndRenderStats(
  el: HTMLElement,
  youUsername?: string | null,
): Promise<Record<string, LifetimeStats>> {
  el.textContent = "Loading…";
  try {
    const resp = await fetch("/api/stats");
    const json = await resp.json();
    const cache = (json?.by_username ?? {}) as Record<string, LifetimeStats>;
    renderStatsTable(el, cache, youUsername);
    return cache;
  } catch {
    el.textContent = "(unable to load stats)";
    return {};
  }
}
