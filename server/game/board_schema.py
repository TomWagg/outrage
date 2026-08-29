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
    white_tower_order: Optional[int] = None
    jewel_id: Optional[JewelId] = None
    warder_post_id: Optional[WarderPostId] = None
    # If True, raven cards / other "send player here" effects cannot force a
    # player onto this space (e.g. White Tower pink path cells).
    immune_to_forced_moves: bool = False
    # True for circled inner-ward cells that are walkable but do NOT trigger a
    # raven-card draw on landing.
    non_raven: bool = False
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
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: WarderPostId
    space_id: str
    blocks: list[str] = Field(default_factory=list, alias="blocks_space_ids")


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
    purpose: str = ""


class TraversalEdge(BaseModel):
    """A non-adjacency edge requiring a traversal item (rope, ladder, etc.)
    or a built-in board feature (e.g. secret passage)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: Optional[str] = None
    src: str = Field(alias="from_space")
    to: str = Field(alias="to_space")
    item: Optional[str] = None  # "rope", "ladder", "secret_passage", ...
    consumes_card: bool = True
    built_in: bool = False
    direction: Literal["bidirectional", "forward", "backward"] = "bidirectional"
    # If set, the player must hold a card of this kind to traverse. The card
    # is kept in hand unless ``consumes_card`` is also true.
    requires_card: Optional[str] = None
    # Number of movement-points the traversal costs (default 1, same as a
    # normal neighbour edge).
    movement_cost: int = 1
    notes: Optional[str] = None


class JewelDisplayOffset(BaseModel):
    """Per-player rendering offset so held jewels stack neatly next to a piece."""

    model_config = ConfigDict(extra="ignore")
    x: float = 0
    y: float = 0


class BoardRules(BaseModel):
    model_config = ConfigDict(extra="allow")
    white_tower_forward_only: bool = True
    white_tower_immune_to_forced_moves: bool = True
    escape_banks_jewel_returns_coin_redraws_hand: bool = True
    escape_reshuffles_old_hand_into_deck: bool = True


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

    # Anchor spaces. Most are required to start a real game, but the toy
    # board used in tests may omit several — so they are all Optional and the
    # engine validates presence at game-start time for the pieces it needs.
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
    raven_deck_display_region_id: Optional[str] = None

    metallicity_destination_ids: list[str] = Field(default_factory=list)
    bench_space_ids: list[str] = Field(default_factory=list)
