"""Pydantic v2 state models for the Outrage engine.

All state lives in a single :class:`GameState`. Sub-phase machinery (combat,
turn context, pending raven/jewel prompts) is expressed as nested models so a
snapshot of ``GameState`` is sufficient to resume a game.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .cards import Card


# ---------- enums ----------------------------------------------------------


class Phase(str, Enum):
    LOBBY = "LOBBY"
    TURN_START = "TURN_START"
    PRE_ROLL = "PRE_ROLL"
    ROLLING = "ROLLING"
    MOVING = "MOVING"
    CHOOSING_PATH = "CHOOSING_PATH"
    COMBAT = "COMBAT"
    JEWEL_ATTEMPT = "JEWEL_ATTEMPT"
    CARD_CHANGE = "CARD_CHANGE"
    ACCREDITATION_ATTEMPT = "ACCREDITATION_ATTEMPT"
    RAVEN_EFFECT = "RAVEN_EFFECT"
    SPLIT_SEVEN_ASSIGN = "SPLIT_SEVEN_ASSIGN"
    TURN_END = "TURN_END"
    GAME_OVER = "GAME_OVER"


class Status(str, Enum):
    NORMAL = "NORMAL"
    HOSPITAL = "HOSPITAL"
    RACKED = "RACKED"
    IMPRISONED = "IMPRISONED"
    TORTURED = "TORTURED"


CombatPhase = Literal[
    "attacker_selecting",
    "defender_selecting",
    "defender_specials",
    "revealed",
    "resolved",
]


JewelId = Literal[
    "sword",
    "sceptre",
    "orb",
    "crown_prince_of_wales",
    "crown_st_edward",
]


# ---------- per-player ------------------------------------------------------


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    color: str
    position: str
    hand: list[Card] = Field(default_factory=list)
    has_coin: bool = False
    jewels: list[JewelId] = Field(default_factory=list)
    accredited: bool = False
    trying_accreditation: bool = False
    status: Status = Status.NORMAL
    status_turns_remaining: int = 0
    miss_next_turn: bool = False
    connected: bool = True
    escaped: bool = False  # slow mode: player is out but game continues

    # --- convenience mutators ---------------------------------------------

    def add_card(self, card: Card) -> None:
        self.hand.append(card)

    def remove_card(self, card_id: str) -> Optional[Card]:
        for i, c in enumerate(self.hand):
            if c.id == card_id:
                return self.hand.pop(i)
        return None


# ---------- warders ---------------------------------------------------------


class Warder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    location: str  # barracks id or post space id


# ---------- combat ---------------------------------------------------------


class Combat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attacker: str
    defender: str
    space_id: str
    attacker_cards: list[Card] = Field(default_factory=list)
    defender_cards: list[Card] = Field(default_factory=list)
    attacker_committed: bool = False
    defender_committed: bool = False
    phase: CombatPhase = "attacker_selecting"
    # Transient effects:
    sanctuary_cancelled: bool = False
    mass_accretor_played: bool = False
    winner: Optional[str] = None  # set after reveal
    resolved_events: list[str] = Field(default_factory=list)


# ---------- turn context ---------------------------------------------------


class PendingRavenEffect(BaseModel):
    """Tracks a raven card awaiting player input."""

    model_config = ConfigDict(extra="forbid")

    effect_key: str
    card_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    drawer: str


class PendingJewelAttempt(BaseModel):
    """Player must choose which burglary cards to play before we roll."""

    model_config = ConfigDict(extra="forbid")

    jewel_id: JewelId
    space_id: str
    source: Literal["landing", "raven_view"] = "landing"


class PendingCardChange(BaseModel):
    """A "Change a card" square is waiting for the player to pick a discard.

    ``kind`` distinguishes the two prompts that share this shape: the
    wall-walk *change* squares (discard one, draw the top of the tower deck)
    and ww75's *swap* (give one card to a chosen opponent, receive a random one
    back). For a swap, ``candidates`` lists the opponents who can be picked.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["change", "swap"] = "change"
    space_id: str
    candidates: list[str] = Field(default_factory=list)


class PendingSplitSeven(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 7  # usually 7, but Binary Disruption can reuse this struct
    source: Literal["seven", "binary_disruption"] = "seven"


class RavenNotice(BaseModel):
    """Public banner announcing a freshly drawn raven card.

    Held on :class:`GameState` (not :class:`TurnContext`) so reconnecting
    clients see the active notice and any player can dismiss it via the
    ``dismiss_raven_notice`` intent.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: str
    effect_key: str
    drawer: str
    params: dict[str, Any] = Field(default_factory=dict)
    # The card lands face-down. Only the drawer may turn it over, and the
    # effect does not fire until they do — so the table sees the card before
    # anyone's piece moves. Shared state, so everyone flips together.
    revealed: bool = False


class PendingMove(BaseModel):
    """Pre-commit move decision after a roll."""

    model_config = ConfigDict(extra="forbid")

    steps: int
    destinations: dict[str, list[str]] = Field(default_factory=dict)
    # For split movement: if we've already partially moved, remember what's left.
    remaining_steps: int = 0
    # Segmented moves carry a target (who to stop at) during split-7 second leg.
    forced_stop_space: Optional[str] = None
    # Split-7 continuation: if the self-leg entered ``CHOOSING_PATH``, the
    # target's leg is deferred until the player picks a destination. These
    # fields let ``_intent_choose_move_path`` resume the target's movement.
    split_target: Optional[str] = None
    split_target_destination: Optional[str] = None
    # Target-destination selection: the current player is choosing where the
    # split-7 target moves (not where they themselves move).
    is_for_target: bool = False
    target_for_split: Optional[str] = None
    # target_first: after the roller picks the target's destination, how many
    # steps does the roller still need to take (0 = already done / self_first).
    roller_steps_after_target: int = 0


class TurnContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roll: list[int] = Field(default_factory=list)
    consecutive_doubles: int = 0
    cards_played_this_turn: list[str] = Field(default_factory=list)
    extra_turns_queued: int = 0
    # Every space the current player has stood on this turn, in visit order —
    # used to enforce the "no revisit during a single turn" rule across
    # doubles re-rolls, split-7 legs, and forced / teleport moves.
    visited_this_turn: list[str] = Field(default_factory=list)
    pending_move: Optional[PendingMove] = None
    pending_raven: Optional[PendingRavenEffect] = None
    pending_jewel: Optional[PendingJewelAttempt] = None
    pending_split: Optional[PendingSplitSeven] = None
    pending_card_change: Optional[PendingCardChange] = None
    # For ``binary_disruption`` and split-7: allow the roller to choose splits
    # on an arbitrary roll; we record the effective total here.
    binary_disruption_armed: bool = False
    # Set when a Disguise card is played pre-roll; allows the player to pass
    # through occupied Yeoman Warder posts for the remainder of this turn.
    disguise_used: bool = False


# ---------- log entries ----------------------------------------------------


class LogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GameStats(BaseModel):
    """Per-player tallies for a single game, for the end-of-game screen.

    Derived by folding the event log at game end rather than by incrementing
    counters all over the rule engine — the log is already the authoritative
    record of everything that happened, and one fold is far easier to keep
    honest than twenty scattered ``+= 1``s.
    """

    model_config = ConfigDict(extra="forbid")

    turns_taken: int = 0
    steps_taken: int = 0
    doubles_rolled: int = 0
    tower_cards_drawn: int = 0
    raven_cards_drawn: int = 0
    jewel_attempts: int = 0
    jewels_collected: int = 0
    coins_picked_up: int = 0
    fights_won: int = 0
    fights_lost: int = 0
    turns_lost: int = 0
    times_locked_up: int = 0


# ---------- GameState ------------------------------------------------------


class GameState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["fast", "slow"] = "fast"
    players: list[PlayerState] = Field(default_factory=list)
    turn_order: list[str] = Field(default_factory=list)
    current_turn_index: int = 0
    phase: Phase = Phase.LOBBY

    # Decks
    tower_draw: list[Card] = Field(default_factory=list)
    tower_discard: list[Card] = Field(default_factory=list)
    raven_draw: list[Card] = Field(default_factory=list)
    raven_discard: list[Card] = Field(default_factory=list)

    # Jewels + coins
    jewels_available: dict[JewelId, str] = Field(default_factory=dict)  # jewel -> space_id
    loose_jewels: dict[str, list[JewelId]] = Field(default_factory=dict)  # space_id -> jewels
    coins_available: int = 5  # enough for 5 players; the board sits at devereux

    # Warders
    warders: list[Warder] = Field(default_factory=list)

    # Sub-phase
    combat: Optional[Combat] = None
    turn: TurnContext = Field(default_factory=TurnContext)

    # RNG + log
    seed: int = 0
    rng_state: Optional[list] = None  # persisted Rng internal state
    log: list[LogEntry] = Field(default_factory=list)

    # Firecrackers: usernames currently "on notice" — if they end their next
    # turn still inside the White Tower they are sent to the Rack; if their
    # movement takes them outside the White Tower they escape the effect.
    firecrackers_affected: list[str] = Field(default_factory=list)

    # Raven notice: a public modal that lingers until any player dismisses
    # it. Survives the per-turn TurnContext reset so late-arriving clients
    # still see it.
    active_raven_notice: Optional[RavenNotice] = None

    # Win results
    winner: Optional[str] = None
    finished_slow_order: list[str] = Field(default_factory=list)
    # Filled in once, when the game ends. Sent in the snapshot so a player who
    # reconnects to a finished game still sees the full result.
    final_stats: dict[str, GameStats] = Field(default_factory=dict)

    # --- helpers ---------------------------------------------------------

    def player(self, username: str) -> PlayerState:
        for p in self.players:
            if p.username == username:
                return p
        raise KeyError(username)

    def current_player(self) -> PlayerState:
        if not self.turn_order:
            raise RuntimeError("No turn order set")
        return self.player(self.turn_order[self.current_turn_index])

    def player_at(self, space_id: str) -> list[PlayerState]:
        return [p for p in self.players if p.position == space_id and not p.escaped]

    def jewel_at_space(self, space_id: str) -> Optional[JewelId]:
        for jid, sid in self.jewels_available.items():
            if sid == space_id:
                return jid
        return None
