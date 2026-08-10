/**
 * Client-side mirror of server state.
 *
 * The server sends two top-level updates:
 *   - Snapshot  ``{lobby, you, stats, phase, game?}`` — replace-on-receipt.
 *   - Event     individual log entries; append to ``log``.
 *
 * Board layout is fetched once from ``/api/board`` and cached in ``board``.
 */

export interface LobbyPlayer {
  username: string;
  color: string;
  connected: boolean;
}

export interface LobbyState {
  players: LobbyPlayer[];
  mode: "fast" | "slow";
  started: boolean;
}

export interface PlayerStats {
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

// --- Game ------------------------------------------------------------------

export interface Card {
  id: string;
  kind: string;
  name: string;
  category?: string | null;
  value?: number;
  defender_only?: boolean;
  effect_key?: string | null;
  params?: Record<string, unknown>;
}

export interface GamePlayer {
  username: string;
  color: string;
  position: string;
  hand: Card[];        // own hand (empty array for opponents)
  hand_size: number;
  has_coin: boolean;
  jewels: string[];
  accredited: boolean;
  trying_accreditation: boolean;
  status: string;
  status_turns_remaining: number;
  miss_next_turn: boolean;
  connected: boolean;
  escaped: boolean;
}

export interface PendingMove {
  steps: number;
  destinations?: Record<string, string[]>;
  remaining_steps?: number;
  has_destinations?: boolean;
  /** True when the roller is choosing where the split-7 *target* moves. */
  is_for_target?: boolean;
  /** The target player's username when is_for_target is set. */
  target_for_split?: string;
}

export interface Combat {
  attacker: string;
  defender: string;
  space_id: string;
  attacker_cards: Card[];
  defender_cards: Card[];
  attacker_cards_count?: number;
  defender_cards_count?: number;
  attacker_committed: boolean;
  defender_committed: boolean;
  phase: "attacker_selecting" | "defender_selecting" | "defender_specials" | "revealed" | "resolved";
  sanctuary_cancelled: boolean;
  mass_accretor_played: boolean;
  winner: string | null;
  resolved_events: string[];
}

export interface PendingRavenEffect {
  effect_key: string;
  card_id: string;
  params: Record<string, unknown>;
  drawer: string;
}

export interface RavenNotice {
  card_id: string;
  effect_key: string;
  drawer: string;
  params: Record<string, unknown>;
}

export interface PendingCardChange {
  /** "change" = discard one, draw the top of the tower deck (ww41/58/69).
   *  "swap"   = give a chosen card to a chosen opponent, get a random one back. */
  kind: "change" | "swap";
  space_id: string;
  /** Opponents eligible for a swap; empty for a plain change. */
  candidates: string[];
}

export interface TurnContext {
  roll: number[];
  consecutive_doubles: number;
  extra_turns_queued: number;
  visited_this_turn: string[];
  cards_played_this_turn: string[];
  pending_move: PendingMove | null;
  pending_raven: PendingRavenEffect | null;
  pending_jewel: Record<string, unknown> | null;
  pending_split: Record<string, unknown> | null;
  pending_card_change: PendingCardChange | null;
  binary_disruption_armed?: boolean;
}

export interface GameSnapshot {
  mode: "fast" | "slow";
  phase: string;
  players: GamePlayer[];
  turn_order: string[];
  current_turn_index: number;
  turn: TurnContext;
  jewels_available: Record<string, string>;
  loose_jewels: Record<string, string[]>;
  coins_available: number;
  warders: { id: string; location: string }[];
  combat: Combat | null;
  tower_draw_count: number;
  tower_discard_count: number;
  raven_draw_count: number;
  raven_discard_count: number;
  winner: string | null;
  finished_slow_order: string[];
  firecrackers_affected?: string[];
  active_raven_notice?: RavenNotice | null;
  seed: number;
}

// --- Board -----------------------------------------------------------------

export interface BoardSpace {
  id: string;
  label: string;
  region: string;
  kind: string;
  coords?: [number, number];
  coords_region?: [number, number][];
  neighbors: string[];
  wall_walk_order?: number | null;
  action?: { key: string; params?: Record<string, unknown> } | null;
}

export interface BoardData {
  spaces: BoardSpace[];
  // NOTE: /api/board serves the raw board.json, so these arrive with the JSON
  // key names (from_space / to_space), not the Pydantic field aliases.
  slides: {
    id?: string;
    from_space: string;
    to_space: string;
    bidirectional?: boolean;
    path_coords?: [number, number][];
  }[];
  traversal_edges: { src: string; to: string; requires_card?: string }[];
  start_space: string;
  escape_space: string;
  queens_house_space: string;
  devereux_space: string;
  rack_space: string;
  bloody_tower_space: string;
  beauchamp_tower_space: string;
  bowyer_tower_space: string;
  chapel_royal_space: string;
  chapel_st_john_space: string;
  hospital_space: string;
  museum_space: string;
  royal_armouries_space: string;
  shop_space: string;
  barracks_space: string;
  raven_deck_space: string;
  metallicity_destination_ids: string[];
  bench_space_ids: string[];
  display_regions: { id: string; label: string; coords_region: [number, number][] }[];
  jewel_display_offset: { x: number; y: number };
  rules: Record<string, unknown>;
}

// --- Top-level client state -----------------------------------------------

export interface LogEntry {
  kind: string;
  payload: Record<string, any>;
  ts: number;
}

export interface ClientState {
  connected: boolean;
  you: string | null;
  lobby: LobbyState | null;
  stats: PlayerStats | null;
  lastError: string | null;
  chat: Array<{ from: string; text: string }>;

  board: BoardData | null;   // fetched once
  game: GameSnapshot | null; // null while in the lobby
  log: LogEntry[];
}

export function initialState(): ClientState {
  return {
    connected: false,
    you: null,
    lobby: null,
    stats: null,
    lastError: null,
    chat: [],
    board: null,
    game: null,
    log: [],
  };
}

export function currentTurnUsername(g: GameSnapshot | null): string | null {
  if (!g || !g.turn_order.length) return null;
  return g.turn_order[g.current_turn_index] ?? null;
}

export function playerByName(g: GameSnapshot | null, name: string | null): GamePlayer | null {
  if (!g || !name) return null;
  return g.players.find((p) => p.username === name) ?? null;
}
