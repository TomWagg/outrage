"""Pydantic v2 models describing the on-disk ``data/board.json`` schema.

The engine consumes these models through :mod:`server.game.board` which adds
convenient graph helpers. The schema is intentionally *board-agnostic* — the
engine should work for the real Tower of London board or the toy board used in
tests, so long as both validate against :class:`BoardData`.

All Pydantic models use ``extra="ignore"`` (rather than ``forbid``) so the
on-disk board may carry free-form metadata (``_note``, ``_shape``, ``_draft``,
``_open_questions``, ``_section``, ...) without the loader rejecting it.
Unknown top-level keys are silently dropped; fields the engine actually uses
must be declared here.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


Region = Literal[
    "wall_walk",
    "inner_ward",
    "white_tower",
    "exterior_south",
    "special",
]


SpaceKind = Literal[
    "start",
    "normal",
    "raven_trigger",
    "tower",
    "queens_house",
    "rack",
    "rack_sender",
    "devereux",
    "jewel",
    "escape",
    "bloody_tower",
    "beauchamp_tower",
    "bowyer_tower",
    "chapel_royal",
    "chapel_st_john",
    "hospital",
    "museum",
    "royal_armouries",
    "shop",
    "bench",
    "barracks",
    "warder_post",
    "yeoman_red_square",
]


JewelId = Literal[
    "sword",
    "sceptre",
    "orb",
    "crown_prince_of_wales",
    "crown_st_edward",
]


WarderPostId = Literal["scaffold", "lanthorn", "waterloo", "chapel"]


Coord = tuple[float, float]
CoordRegion = tuple[Coord, Coord]  # [(x1, y1), (x2, y2)] rectangle


class SpaceAction(BaseModel):
    """Optional custom side effect when a space is landed on."""

    model_config = ConfigDict(extra="allow")
    key: str
    params: dict[str, Any] = Field(default_factory=dict)


class SpaceData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str = ""
    region: Region
    kind: SpaceKind
    # ``coords`` is a single point; multi-cell display spaces use
    # ``coords_region`` instead (e.g. the Rack, the Chapel of St John, the
    # Raven deck display strip). Either, both, or neither may be set.
    coords: Optional[Coord] = None
    coords_region: Optional[CoordRegion] = None
    neighbors: list[str] = Field(default_factory=list)
    wall_walk_order: Optional[int] = None
    # If True, raven cards / other "send player here" effects cannot force a
    # player onto this space (e.g. White Tower pink path cells).
    immune_to_forced_moves: bool = False
    action: Optional[SpaceAction] = None


class SlideData(BaseModel):
    """A slide is a directed (optionally bidirectional) edge between two spaces."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: Optional[str] = None
    src: str = Field(alias="from_space")
    to: str = Field(alias="to_space")
    bidirectional: bool = True
    path_coords: list[Coord] = Field(default_factory=list)


class WarderPostData(BaseModel):
    """A named post. A warder standing on ``space_id`` makes that square
    impassable to anyone without a Disguise."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: WarderPostId
    space_id: str


class InitialWarder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    location: str  # space_id, e.g. the barracks id


class DisplayRegion(BaseModel):
    """A non-playable rectangular region used purely for UI display
    (e.g. the Raven-deck strip or the Barracks)."""

    model_config = ConfigDict(extra="ignore")
    id: str
    label: str = ""
    coords_region: CoordRegion


class TraversalEdge(BaseModel):
    """A non-adjacency edge requiring a traversal item (rope, ladder, etc.)
    or a built-in board feature (e.g. secret passage)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: Optional[str] = None
    src: str = Field(alias="from_space")
    to: str = Field(alias="to_space")
    item: Optional[str] = None  # "rope", "ladder", "secret_passage", ... — drives the board art
    built_in: bool = False
    direction: Literal["bidirectional", "forward", "backward"] = "bidirectional"
    # If set, the player must hold a card of this kind to traverse, and the
    # edge is kept out of the plain neighbour graph so it can't be walked free.
    requires_card: Optional[str] = None
    # Number of movement-points the traversal costs. The movement search is
    # unweighted, so :class:`~server.game.board.Board` skips anything but 1 and
    # says so.
    movement_cost: int = 1


class JewelDisplayOffset(BaseModel):
    """Per-player rendering offset so held jewels stack neatly next to a piece."""

    model_config = ConfigDict(extra="ignore")
    x: float = 0
    y: float = 0


class BoardRules(BaseModel):
    """Board-level switches the engine actually consults.

    ``extra="allow"`` keeps free-form annotations in the JSON from failing
    validation — but an unknown key is inert, so anything meant to change how
    the game plays has to be declared here *and* read somewhere.
    """

    model_config = ConfigDict(extra="allow")
    #: The White Tower is walked out of under your own steam or not at all.
    white_tower_immune_to_forced_moves: bool = True
    #: An escapee's surrendered hand goes back into the draw pile (else discard).
    escape_reshuffles_old_hand_into_deck: bool = True
    #: Space kinds that deal a tower card on landing, less the exceptions.
    tower_card_draw_kinds: list[str] = Field(default_factory=lambda: ["tower"])
    tower_card_draw_exception_space_ids: list[str] = Field(default_factory=list)
    #: Space kind -> the ``Status`` walking onto it imposes.
    confine_on_landing_kinds: dict[str, str] = Field(default_factory=dict)
    #: How many turns a confinement lasts.
    confinement_turns: int = 3
    #: Landing on the Devereux Tower hands out a coin.
    devereux_grants_coin: bool = True
    #: Space kinds that simply cost you your next turn.
    miss_turn_on_landing_kinds: list[str] = Field(default_factory=list)
    #: Space id -> the square you are stepped out onto once that missed turn has
    #: been served. Only needed where staying put would trap the player, i.e.
    #: where the square's only way out leads straight back onto it.
    miss_turn_exit_spaces: dict[str, str] = Field(default_factory=dict)


class BoardData(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    spaces: list[SpaceData]
    slides: list[SlideData] = Field(default_factory=list)
    warder_posts: list[WarderPostData] = Field(default_factory=list)
    initial_warders: list[InitialWarder] = Field(default_factory=list)
    initial_jewel_locations: dict[JewelId, str] = Field(default_factory=dict)
    display_regions: list[DisplayRegion] = Field(default_factory=list)
    traversal_edges: list[TraversalEdge] = Field(default_factory=list)
    jewel_display_offset: Optional[JewelDisplayOffset] = None
    rules: BoardRules = Field(default_factory=BoardRules)

    # Anchor spaces. A real game needs most of them, but the toy boards used in
    # tests omit several, so they are all Optional. Nothing validates them up
    # front: a missing anchor surfaces when the rule that wants it runs.
    start_space: Optional[str] = None
    escape_space: Optional[str] = None
    queens_house_space: Optional[str] = None
    devereux_space: Optional[str] = None
    rack_space: Optional[str] = None
    # Where a released prisoner steps out of the Rack. Defaults to the
    # Rack's sole neighbour (see ``Board.rack_exit_space``) — the Rack is a
    # dead end, so leaving it and staying put are not the same thing.
    rack_exit_space: Optional[str] = None
    bloody_tower_space: Optional[str] = None
    beauchamp_tower_space: Optional[str] = None
    bowyer_tower_space: Optional[str] = None
    chapel_royal_space: Optional[str] = None
    chapel_st_john_space: Optional[str] = None
    hospital_space: Optional[str] = None
    museum_space: Optional[str] = None
    royal_armouries_space: Optional[str] = None
    shop_space: Optional[str] = None
    barracks_space: Optional[str] = None
    raven_deck_space: Optional[str] = None

    metallicity_destination_ids: list[str] = Field(default_factory=list)
    bench_space_ids: list[str] = Field(default_factory=list)
