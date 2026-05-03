#!/usr/bin/env python3
"""Populate board.json with the generic inner-ward walkable grid.

Rules (from user):
- Inner ward grid runs x=1..20, y=1..18.
- Every cell is a white walkable square UNLESS it's occupied by a named
  structure, a warder post, a non-walkable display region (barracks /
  raven-deck), a white-tower pink-path cell, a slide cell, or an exterior_south
  space.
- All orthogonally-adjacent white cells are connected (up/down/left/right).
- Named structures have explicit entry cells defined in SPECIAL_NBRS.
- Generic cells default to kind=raven_trigger; circled (non-raven) cells are
  TBD — flagged as an open question.

Run: python tools/gen_iw_grid.py
Re-runnable: regenerates the iw_X_Y entries, preserving everything else.
"""
from __future__ import annotations

import json
from pathlib import Path

BOARD = Path(__file__).resolve().parent.parent / "data" / "board.json"


def main() -> None:
    data = json.loads(BOARD.read_text())

    occupied: set[tuple[int, int]] = set()

    def add_rect(x1: int, y1: int, x2: int, y2: int) -> None:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            for y in range(min(y1, y2), max(y1, y2) + 1):
                occupied.add((x, y))

    # Multi-cell named structures (single logical space each).
    add_rect(2, 1, 6, 2)     # Queen's House
    add_rect(3, 16, 7, 17)   # Chapel Royal
    add_rect(19, 11, 20, 13) # Museum
    add_rect(19, 7, 20, 9)   # Hospital
    add_rect(19, 3, 20, 5)   # Royal Armouries
    add_rect(13, 5, 15, 7)   # Chapel of St John (inside White Tower)
    add_rect(7, 10, 8, 10)   # Scaffold warder post
    add_rect(1, 14, 2, 14)   # Chapel warder post
    add_rect(12, 13, 12, 14) # Waterloo warder post
    add_rect(16, 2, 17, 2)   # Lanthorn warder post
    add_rect(13, 8, 13, 10)  # The Rack

    # Non-walkable display regions (green grass).
    add_rect(3, 9, 6, 13)    # Raven-deck display strip
    add_rect(10, 15, 17, 15) # Barracks display strip

    # White Tower footprint: cells inside the envelope x=10..14, y=2..11 that
    # aren't on the pink path are solid tower structure (not walkable). The
    # Chapel of St John extends slightly east to x=15, y=5..7 (already occupied
    # by add_rect above).
    add_rect(10, 2, 14, 11)

    # Exterior_south anchor cells that live on the grid boundary.
    occupied.add((7, 2))   # out_7_2
    occupied.add((18, 2))  # out_18_2
    occupied.add((7, 1))   # slide_south_west pass-through
    occupied.add((18, 1))  # slide_south_east pass-through

    iw_cells: set[tuple[int, int]] = set()
    for x in range(1, 21):
        for y in range(1, 19):
            if (x, y) not in occupied:
                iw_cells.add((x, y))

    # Explicit non-grid neighbours for entry cells.
    special_nbrs: dict[tuple[int, int], list[str]] = {
        (5, 3): ["ww76_queens_house"],
        (1, 8): ["ww61"],
        (2, 18): ["ww54_devereux"],
        (8, 18): ["ww48"],
        (20, 18): ["ww37_martin"],
        (20, 10): ["ww32"],
        (2, 16): ["iw_chapel_royal"],
        (18, 12): ["iw_museum"],
        (18, 8): ["iw_hospital"],
        (18, 4): ["iw_royal_armouries"],
        (14, 4): ["wt_chapel_st_john"],
        (16, 4): ["wt_chapel_st_john"],
        (13, 13): ["wt_13_11_rack_sender"],
    }

    # Cells adjacent to exterior_south anchors should link to out_7_2 / out_18_2.
    adj_ext: dict[tuple[int, int], list[str]] = {
        (6, 2): ["out_7_2"],  # inside QH, skipped anyway
        (8, 2): ["out_7_2"],
        (7, 3): ["out_7_2"],
        (19, 2): ["out_18_2"],
        (18, 3): ["out_18_2"],
    }

    def cid(x: int, y: int) -> str:
        return f"iw_{x}_{y}"

    generated: list[dict] = []
    for (x, y) in sorted(iw_cells):
        nbrs: list[str] = []
        for (dx, dy) in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            if (x + dx, y + dy) in iw_cells:
                nbrs.append(cid(x + dx, y + dy))
        nbrs.extend(special_nbrs.get((x, y), []))
        nbrs.extend(adj_ext.get((x, y), []))
        space = {
            "id": cid(x, y),
            "label": "",
            "region": "inner_ward",
            "kind": "raven_trigger",
            "coords": [x, y],
            "neighbors": sorted(set(nbrs)),
        }
        generated.append(space)

    # Strip existing generic iw_X_Y cells (kept: named structures iw_shop,
    # iw_rack, iw_bloody_tower, iw_chapel_royal, iw_museum, iw_hospital,
    # iw_royal_armouries, iw_bench_*, barracks, and section markers).
    preserved: list[dict] = []
    stripped_ids: set[str] = set()
    for sp in data["spaces"]:
        if not isinstance(sp, dict):
            preserved.append(sp)
            continue
        sid = sp.get("id")
        if sid and sid.startswith("iw_") and "coords" in sp and sp.get("region") == "inner_ward" and sp.get("kind") in {"normal", "raven_trigger"}:
            stripped_ids.add(sid)
            continue
        preserved.append(sp)

    # Insert generated cells just before the "cells referenced by the White
    # Tower" section marker for readability.
    new_spaces: list[dict] = []
    inserted = False
    for sp in preserved:
        if not inserted and isinstance(sp, dict) and sp.get("_section", "").startswith("INNER WARD — cells referenced"):
            new_spaces.append({
                "_section": "INNER WARD — generic walkable grid (generated by tools/gen_iw_grid.py). "
                            "All cells default to kind=raven_trigger; circled (non-raven) cells TBD."
            })
            new_spaces.extend(generated)
            inserted = True
        new_spaces.append(sp)
    if not inserted:
        new_spaces.extend(generated)

    data["spaces"] = new_spaces

    # Patch out_7_2 and out_18_2 to include their boundary iw neighbours.
    for sp in data["spaces"]:
        if not isinstance(sp, dict):
            continue
        if sp.get("id") == "out_7_2":
            extra = [c for c in ["iw_8_2", "iw_7_3"] if (int(c.split("_")[1]), int(c.split("_")[2])) in iw_cells]
            sp["neighbors"] = sorted(set(list(sp.get("neighbors", [])) + extra))
        elif sp.get("id") == "out_18_2":
            extra = [c for c in ["iw_19_2", "iw_18_3"] if (int(c.split("_")[1]), int(c.split("_")[2])) in iw_cells]
            sp["neighbors"] = sorted(set(list(sp.get("neighbors", [])) + extra))

    BOARD.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Stripped {len(stripped_ids)} old iw cells; added {len(generated)} generic iw cells.")
    print(f"Total spaces now: {len(data['spaces'])}.")


if __name__ == "__main__":
    main()
