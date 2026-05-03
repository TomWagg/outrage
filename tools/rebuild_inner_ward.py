"""Rebuild data/board.json's inner-ward cells from the user's exact dictation.

Design:
  1. Drop every existing iw_X_Y space.
  2. Enumerate the exact cells the user dictated (single cells, lines, and
     rectangles), remembering which ones are circled.
  3. Emit one iw_X_Y space per cell with neighbours derived from orthogonal
     adjacency between cells that are BOTH in the dictated set.
  4. Patch named structures' neighbour lists to match the dictation:
       - Museum connects only to (18,12)
       - Hospital connects only to (18,8)
       - Royal Armouries connects only to (18,4)
       - Chapel Royal connects to (2,16) + secret passage (handled elsewhere)
       - Chapel of St John connects to wt_14_8 + (14,4) + ladder to (16,4)
       - Benches: (10,13) → (10,14) only; (16,9) → (17,9) only
       - Warder posts: scaffold/chapel/waterloo/lanthorn neighbours as
         orthogonally implied by their coords and the dictated cell list.
  5. Convert legacy out_7_2 → iw_7_2 (user says (7,2) is inner ward).
  6. Strip any stale references to removed cells.

The script is idempotent and preserves wall walk, white tower, exterior
south, and named spaces untouched except for the neighbour-patching above.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

BOARD = Path(__file__).resolve().parent.parent / "data" / "board.json"
data = json.loads(BOARD.read_text())


# ---------- 1. collect the user's dictated cell list -----------------------

def rect(x1, y1, x2, y2, circled_coords: Iterable[tuple[int, int]] = ()):
    circled_set = set(circled_coords)
    xs = range(min(x1, x2), max(x1, x2) + 1)
    ys = range(min(y1, y2), max(y1, y2) + 1)
    for x in xs:
        for y in ys:
            yield (x, y, (x, y) in circled_set)


def line(points_circled: Iterable[tuple[int, int, bool]]):
    yield from points_circled


CELLS: dict[tuple[int, int], bool] = {}  # (x,y) -> circled?


def add(x: int, y: int, circled: bool = False) -> None:
    # Dedupe; OR the circled flag so any "mentioned circled" wins.
    CELLS[(x, y)] = CELLS.get((x, y), False) or circled


def add_many(iterable):
    for x, y, c in iterable:
        add(x, y, c)


# --- inner-ward cells from the dictation ---
# Row y=3: (2,3)-(8,3); (7,3) circled
add_many(rect(2, 3, 8, 3, [(7, 3)]))
# Rect (7,4)-(8,9) all white, no circles
add_many(rect(7, 4, 8, 9))
# Col (2,4)-(2,13); circles at (2,5) and (2,12)
add_many(rect(2, 4, 2, 13, [(2, 5), (2, 12)]))
# Row (1,8)-(8,8); (3,8) circled
add_many(rect(1, 8, 8, 8, [(3, 8)]))
# Col (2,15)-(2,18); (2,17) circled
add_many(rect(2, 15, 2, 18, [(2, 17)]))
# Row (3,14)-(11,14); (7,14) and (11,14) circled
add_many(rect(3, 14, 11, 14, [(7, 14), (11, 14)]))
# Rect (7,11)-(8,13); (8,11) and (7,12) circled
add_many(rect(7, 11, 8, 13, [(8, 11), (7, 12)]))
# Col (8,15)-(8,18) no circles
add_many(rect(8, 15, 8, 18))
# Row (9,18)-(20,18); (11,18) (14,18) (16,18) circled
add_many(rect(9, 18, 20, 18, [(11, 18), (14, 18), (16, 18)]))
# Col (20,14)-(20,17); (20,16) circled
add_many(rect(20, 14, 20, 17, [(20, 16)]))
# (13,13) solo
add(13, 13)
# Row (13,14)-(19,14); (14,14) circled
add_many(rect(13, 14, 19, 14, [(14, 14)]))
# Col (17,3)-(17,13); (17,4)(17,7)(17,13) circled
add_many(rect(17, 3, 17, 13, [(17, 4), (17, 7), (17, 13)]))
# (18,12) solo
add(18, 12)
# Row (18,10)-(20,10); (19,10) circled
add_many(rect(18, 10, 20, 10, [(19, 10)]))
# (18,8) solo
add(18, 8)
# (18,4) solo
add(18, 4)
# (16,3) and (16,4) solo
add(16, 3); add(16, 4)
# (14,4) solo
add(14, 4)
# Rect (14,2)-(15,4) no circles
add_many(rect(14, 2, 15, 4))
# Col (13,1)-(13,2) no circles
add_many(rect(13, 1, 13, 2))
# (7,2) solo (inner ward, connects downward to the south slide)
add(7, 2)
# (18,2) circled (connects to Lanthorn post)
add(18, 2, circled=True)

print(f"Dictated cells: {len(CELLS)}")


# ---------- 2. drop all existing iw_X_Y spaces + the legacy out_7_2 --------

def is_generic_iw(sid: str) -> bool:
    if not sid.startswith("iw_"):
        return False
    parts = sid[3:].split("_")
    return len(parts) == 2 and all(p.isdigit() for p in parts)


DROPPED = set()
for s in data["spaces"]:
    if not isinstance(s, dict) or "id" not in s:
        continue
    sid = s["id"]
    if is_generic_iw(sid):
        DROPPED.add(sid)
DROPPED.add("out_7_2")  # → becomes iw_7_2

data["spaces"] = [s for s in data["spaces"] if not (isinstance(s, dict) and s.get("id") in DROPPED)]


def cell_id(x: int, y: int) -> str:
    return f"iw_{x}_{y}"


# ---------- 3. emit new iw_X_Y spaces with orthogonal adjacency ------------

def ortho_nbrs(x: int, y: int) -> list[str]:
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if (x + dx, y + dy) in CELLS:
            out.append(cell_id(x + dx, y + dy))
    return out


# Special-neighbour map: named spaces to attach to a given dictated cell.
# These are the connections between cells and named structures.
SPECIAL_NBRS: dict[tuple[int, int], list[str]] = {
    # Wall-walk hookpoints (confirmed against current ww_order)
    (5, 3):   ["ww76_queens_house"],            # QH ↔ (5,3)
    (1, 8):   ["ww61"],                         # after Beauchamp, before Bell
    (2, 18):  ["ww54_devereux"],                # Devereux ↔ (2,18)
    (8, 18):  ["ww48"],                         # 2-behind Flint (50), between Flint+Bowyer
    (20, 18): ["ww37_martin"],                  # Martin ↔ (20,18)
    (20, 10): ["ww32"],                         # 2-behind Constable (34), between Constable+Broad Arrow

    # Named building hooks
    (2, 16):  ["iw_chapel_royal"],              # Chapel Royal ↔ (2,16)
    (18, 12): ["iw_museum"],                    # Museum ↔ (18,12) only
    (18, 10): [],                               # no special, orthogonal-only
    (18, 8):  ["iw_hospital"],                  # Hospital ↔ (18,8) only
    (18, 4):  ["iw_royal_armouries"],           # Royal Armouries ↔ (18,4) only
    (14, 4):  ["wt_chapel_st_john"],            # Chapel of St John ↔ (14,4) direct
    (16, 4):  ["wt_chapel_st_john"],            # Chapel of St John ↔ (16,4) via ladder
    (13, 13): ["wt_13_11_rack_sender"],         # rope edge built in board data

    # Warder posts
    (7, 9):   ["post_scaffold"],
    (8, 9):   ["post_scaffold"],
    (7, 11):  ["post_scaffold"],
    (8, 11):  ["post_scaffold"],
    (2, 13):  ["post_chapel"],
    (2, 15):  ["post_chapel"],
    (11, 14): ["post_waterloo"],
    (13, 13): ["wt_13_11_rack_sender", "post_waterloo"],  # override — (13,13) also adjacent to Waterloo post
    (13, 14): ["post_waterloo"],
    (15, 2):  ["post_lanthorn"],
    (18, 2):  ["post_lanthorn"],
    (16, 3):  ["post_lanthorn"],
    (17, 3):  ["post_lanthorn"],

    # Benches
    (10, 14): ["iw_bench_10_13"],
    (17, 9):  ["iw_bench_16_9"],

    # Queen's House
    # (The QH space (ww76_queens_house) lists (5,3) in ITS neighbours already.
    # For a bidirectional edge, (5,3) also needs QH.)
    # Already handled by (5,3) → ww76_queens_house above.

    # Exterior-south slides will be wired up in a later pass.
}


new_spaces = []
for (x, y), circled in sorted(CELLS.items()):
    nbrs = ortho_nbrs(x, y)
    nbrs.extend(SPECIAL_NBRS.get((x, y), []))
    # Dedupe preserving order
    seen = set(); ordered = []
    for n in nbrs:
        if n not in seen:
            seen.add(n); ordered.append(n)
    sp = {
        "id": cell_id(x, y),
        "label": "",
        "region": "inner_ward",
        "kind": "normal" if circled else "raven_trigger",
        "coords": [x, y],
        "neighbors": ordered,
    }
    if circled:
        sp["non_raven"] = True
    new_spaces.append(sp)


# ---------- 4. patch named-structure neighbour lists ----------------------

def find_space(sid: str):
    for s in data["spaces"]:
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    return None


def set_nbrs(sid: str, nbrs: list[str], *, extend: bool = False):
    s = find_space(sid)
    if s is None:
        return
    if extend:
        existing = s.get("neighbors") or []
        for n in nbrs:
            if n not in existing:
                existing.append(n)
        s["neighbors"] = existing
    else:
        s["neighbors"] = list(nbrs)


# Chapel Royal: (2,16) neighbor + Salt Tower via secret passage (traversal_edge — not in direct neighbors)
set_nbrs("iw_chapel_royal", ["iw_2_16"])
# Museum: (18,12) only
set_nbrs("iw_museum", ["iw_18_12"])
# Hospital: (18,8) only
set_nbrs("iw_hospital", ["iw_18_8"])
# Royal Armouries: (18,4) only
set_nbrs("iw_royal_armouries", ["iw_18_4"])
# Benches
set_nbrs("iw_bench_10_13", ["iw_10_14"])
set_nbrs("iw_bench_16_9", ["iw_17_9"])
# Chapel of St John: wt_14_8 (path exit) + (14,4) direct + (16,4) ladder
set_nbrs("wt_chapel_st_john", ["wt_14_8", "iw_14_4", "iw_16_4"])
# Warder posts
set_nbrs("post_scaffold", ["iw_7_9", "iw_8_9", "iw_7_11", "iw_8_11"])
set_nbrs("post_chapel", ["iw_2_13", "iw_2_15"])
set_nbrs("post_waterloo", ["iw_11_14", "iw_13_13", "iw_13_14"])
set_nbrs("post_lanthorn", ["iw_15_2", "iw_18_2", "iw_16_3", "iw_17_3"])

# Ensure ww76_queens_house has [ww75, iw_5_3]
set_nbrs("ww76_queens_house", ["ww75", "iw_5_3"])


# ---------- 5. splice new iw_X_Y spaces in, strip stale references --------

# Strip stale neighbour refs to dropped ids
all_ids = {s["id"] for s in data["spaces"] if isinstance(s, dict) and "id" in s}
for sp in new_spaces:
    all_ids.add(sp["id"])

for s in data["spaces"]:
    if isinstance(s, dict) and "neighbors" in s:
        s["neighbors"] = [n for n in s["neighbors"] if n in all_ids or n.startswith(("iw_", "wt_", "ww", "out_", "post_", "barracks"))]

# Insert new iw spaces (append at end)
data["spaces"].extend(new_spaces)


# ---------- 6. drop stale out_7_2 references; wire exterior-south slides --

# Rebuild the 3 south slides per dictation.
# Per user: (7,-3)-(7,1) is a single slide connecting iw_7_2 ↔ out_8_m3.
#           (9,-3)-(11,-3) single slide connecting out_8_m3 ↔ out_12_m3.
#           (18,-2)-(18,1) single slide connecting out_18_m3 ↔ iw_18_2.
# Slides are represented as SlideData (top-level slides[]) so the engine
# treats them as one-step edges; the path_coords are for rendering.

data["slides"] = [
    {
        "id": "slide_south_west",
        "from_space": "iw_7_2",
        "to_space": "out_8_m3",
        "path_coords": [[7, 1], [7, 0], [7, -1], [7, -2], [7, -3]],
        "bidirectional": True,
    },
    {
        "id": "slide_south_middle",
        "from_space": "out_8_m3",
        "to_space": "out_12_m3_extra_turn",
        "path_coords": [[9, -3], [10, -3], [11, -3]],
        "bidirectional": True,
    },
    {
        "id": "slide_south_east",
        "from_space": "out_18_m3_cradle_escape",
        "to_space": "iw_18_2",
        "path_coords": [[18, -2], [18, -1], [18, 0], [18, 1]],
        "bidirectional": True,
    },
]


# Ensure the exterior-south row cells exist and have correct neighbours.
# We let direct neighbours be bare adjacency; slides are edges from the
# slides[] array and will be added to the neighbour set by Board.__init__.
expected_ext = [
    # (id, x, y, kind, label, action)
    ("out_8_m3",                 8, -3, "normal", "", None),
    ("out_12_m3_extra_turn",    12, -3, "normal", "Extra turn",
     {"key": "extra_turn", "params": {"resets_consecutive_doubles": True}}),
    ("out_13_m3", 13, -3, "normal", "", None),
    ("out_14_m3", 14, -3, "normal", "", None),
    ("out_15_m3", 15, -3, "normal", "", None),
    ("out_16_m3", 16, -3, "normal", "", None),
    ("out_17_m3", 17, -3, "normal", "", None),
    ("out_18_m3_cradle_escape", 18, -3, "escape", "Cradle Tower (exit)", None),
]
CIRCLED_EXT = {"out_13_m3", "out_14_m3", "out_16_m3", "out_17_m3"}


def ensure_ext_space(sid: str, x: int, y: int, kind: str, label: str, action):
    sp = find_space(sid)
    if sp is None:
        sp = {"id": sid, "label": label, "region": "exterior_south", "kind": kind, "coords": [x, y], "neighbors": []}
        data["spaces"].append(sp)
    else:
        sp["region"] = "exterior_south"; sp["kind"] = kind; sp["coords"] = [x, y]; sp["label"] = label
    if action is not None:
        sp["action"] = action
    if sid in CIRCLED_EXT:
        sp["non_raven"] = True
    return sp


for sid, x, y, kind, label, action in expected_ext:
    ensure_ext_space(sid, x, y, kind, label, action)

# Rebuild exterior-south neighbours: orthogonal adjacency within exterior-south row.
ext_by_coord = {(x, y): sid for sid, x, y, *_ in expected_ext}
for sid, x, y, *_ in expected_ext:
    sp = find_space(sid)
    nbrs = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nb = ext_by_coord.get((x + dx, y + dy))
        if nb:
            nbrs.append(nb)
    sp["neighbors"] = nbrs

# Finally: strip any stale `out_7_2` references from remaining spaces.
for s in data["spaces"]:
    if isinstance(s, dict) and "neighbors" in s:
        s["neighbors"] = [n for n in s["neighbors"] if n != "out_7_2"]


# ---------- 7. write --------------------------------------------------------

BOARD.write_text(json.dumps(data, indent=2) + "\n")
print(f"Emitted {len(new_spaces)} iw_X_Y cells. Total spaces: {len(data['spaces'])}")
