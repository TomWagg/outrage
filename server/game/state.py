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


#: Statuses in which a player's piece is locked in place: they take no turn of
#: their own and nobody else may move them either. The Hospital is deliberately
#: not one of these — it costs a turn but the piece is free.
CONFINED_STATUSES: tuple["Status", ...] = (Status.IMPRISONED, Status.TORTURED, Status.RACKED)


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
    # Extra turns owed to this player but not yet taken. Set when something
    # grants an extra turn to somebody who is *not* the acting player — a
    # split-7 leg that shoves an opponent onto the extra-turn square, say.
    # ``_intent_end_turn`` moves it into ``turn.extra_turns_queued`` when play
    # reaches them, so they get the bonus on their own turn, not the roller's.
    extra_turns_pending: int = 0
    connected: bool = True
    #: Jewels carried out through the Cradle Tower and stashed in the hideout.
    #: Safe forever — no fight or pickpocket can touch them — and the only
    #: jewels that score. ``jewels`` above is what is still in your pockets and
    #: still at risk. Using the exit moves the one list into the other.
    banked_jewels: list[JewelId] = Field(default_factory=list)

    # --- derived ----------------------------------------------------------

    @property
    def confined(self) -> bool:
        """Locked up: in the Bloody/Beauchamp/Bowyer Tower or on the Rack.

        A confined piece cannot be moved by anything — not a split 7, not a
        Lasso, not a Sanctuary played by its own owner. Serving the sentence is
        the only way out (bar a Pardon or a Confession).
        """
        return self.status in CONFINED_STATUSES

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
    # username -> the leg sizes that would actually move them somewhere. A
    # player who is boxed in (an un-accredited piece parked on Queen's House,
    # say) can't be given any of the roll, so the client must not offer them —
    # and if nobody is movable the split never happens at all.
    movable_targets: dict[str, list[int]] = Field(default_factory=dict)


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


class ConfinementNotice(BaseModel):
    """Public banner announcing that somebody has just been locked up.

    The table's counterpart to :class:`RavenNotice`: everyone sees it, so the
    imprisonment reads as an event at the table rather than a quiet status
    change in a side panel. Only the player it happened to may dismiss it.

    Lives on :class:`GameState` so it survives the per-turn ``TurnContext``
    reset and reaches reconnecting clients. Purely informational — it gates no
    phase, so a disconnected victim can't wedge the game by never dismissing.
    """

    model_config = ConfigDict(extra="forbid")

    username: str
    status: Status
    space_id: str
    turns: int = 0
    #: Best-effort description of what put them there, for the banner copy.
    #: Derived from the event log, so an unrecognised route yields "".
    cause: str = ""


class PendingMove(BaseModel):
    """Pre-commit move decision after a roll."""

    model_config = ConfigDict(extra="forbid")

    steps: int
    destinations: dict[str, list[str]] = Field(default_factory=dict)
    # Destinations whose only route runs through an occupied Yeoman Warder post.
    # Reaching one spends a Disguise from hand, so the client labels them and
    # the engine never auto-commits one. Display-only: the charge is worked out
    # from the committed path, not from this list.
    requires_disguise: list[str] = Field(default_factory=list)
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


class DeferredSplitLeg(BaseModel):
    """Half of a split-7 that couldn't run when its turn came round.

    Whichever leg goes first can land somewhere that opens a prompt — a raven
    card, a jewel attempt, a "change a card" square. Running the other leg then
    and there would overwrite that phase and strand whoever owes the answer, so
    it's parked here and picked up once the prompt is closed. Dropping it
    instead would silently cost somebody part of a roll that was legitimately
    made.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["roller", "target"]
    steps: int
    target: Optional[str] = None
    target_destination: Optional[str] = None


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
    deferred_split_leg: Optional[DeferredSplitLeg] = None
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
    # The Devereux Tower holds one coin more than there are players, so exactly
    # one player can always be left short. ``coins_total`` is the size of that
    # pile (set at ``start_game``); ``coins_available`` is how many are still
    # sitting in the tower. Coins handed back — the Rack toll, a fight won by
    # someone already holding one — go back onto the pile, never above the cap.
    coins_total: int = 5
    coins_available: int = 5

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

    # Confinement notice: the red counterpart to the raven notice. Raised
    # automatically by ``rules.apply`` whenever a player's status crosses into
    # confinement, from whichever route; cleared by the victim, by a later
    # confinement, or by their release.
    active_confinement_notice: Optional[ConfinementNotice] = None

    # Win results
    winner: Optional[str] = None
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
        return [p for p in self.players if p.position == space_id]

    def jewel_at_space(self, space_id: str) -> Optional[JewelId]:
        for jid, sid in self.jewels_available.items():
            if sid == space_id:
                return jid
        return None
