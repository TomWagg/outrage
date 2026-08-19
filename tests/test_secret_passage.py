"""The Chapel Royal ↔ Salt Tower secret passage is part of the board graph.

``traversal_edges`` were parsed but never joined to the movement graph, so the
passage did nothing: standing two squares from the Chapel Royal and rolling 8
offered no route through it. Card-gated edges (rope, ladder) stay out — whether
they exist depends on the mover's hand, which the board doesn't know about.
"""
from __future__ import annotations

from pathlib import Path

from server.game.board import Board
from server.game.movement import compute_destinations
from server.game.state import PlayerState


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")
CHAPEL = "iw_chapel_royal"
SALT = "ww23_salt"


def test_the_passage_is_a_two_way_edge():
    assert SALT in BOARD.neighbors(CHAPEL)
    assert CHAPEL in BOARD.neighbors(SALT)


def test_card_gated_edges_stay_out_of_the_plain_graph():
    """A rope/ladder edge only exists for a player holding the card, which the
    board can't know — so joining it here would hand it to everyone.

    Checked on the ladder's upward direction specifically: the rope edge and
    the ladder's downward direction are *also* written into ``neighbors`` in
    board.json, so they're already free for everyone regardless of this rule.
    """
    ladder = next(te for te in BOARD.data.traversal_edges if te.requires_card == "ladder")
    assert ladder.to not in BOARD.neighbors(ladder.src)


def test_a_roll_from_near_the_chapel_offers_routes_through_the_passage():
    # iw_2_17 -> iw_2_16 -> chapel -> salt tower, then five more along the wall.
    player = PlayerState(username="p1", color="red", position="iw_2_17",
                         accredited=True)
    opts = compute_destinations(
        BOARD, "iw_2_17", 8, player, visited_this_turn=["iw_2_17"],
    )

    through = {d: p for d, p in opts.destinations.items() if SALT in p}
    assert through, f"no destination routed via the passage: {sorted(opts.destinations)}"
    for path in through.values():
        # The passage costs exactly one step, like any other edge.
        assert path[path.index(SALT) - 1] == CHAPEL
        assert len(path) == 9
