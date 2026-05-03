"""Strip auto-generated iw_X_Y cells that the user never explicitly dictated.

Keeps:
  - All named iw_* spaces (iw_shop, iw_rack, iw_chapel_royal, iw_museum,
    iw_hospital, iw_royal_armouries, iw_bloody_tower, iw_bench_10_13,
    iw_bench_16_9).
  - Explicitly dictated grid cells: hookpoints + traversal anchors + the
    two bench-adjacent cells implied by the user's manual edits.
Removes:
  - Every other iw_<digits>_<digits> cell that was auto-generated.

Also cleans up neighbor lists so no kept space points at a removed cell, and
ensures the benches are reachable (bidirectional edges with their adjacent
cells).
"""
from __future__ import annotations

import json
from pathlib import Path

BOARD = Path(__file__).resolve().parent.parent / "data" / "board.json"
data = json.loads(BOARD.read_text())

KEEP_GENERIC = {
    # Wall-walk → inner-ward hookpoints (from original dictation)
    "iw_5_3", "iw_1_8", "iw_2_18", "iw_8_18", "iw_20_18", "iw_20_10",
    # White Tower traversal-anchor cells
    "iw_13_13", "iw_16_4",
    # Bench-adjacent cells implied by the user's manual bench neighbours
    "iw_10_14", "iw_17_9",
}


def is_generic_iw(sid: str) -> bool:
    if not sid.startswith("iw_"):
        return False
    parts = sid[3:].split("_")
    return len(parts) == 2 and all(p.isdigit() for p in parts)


# Pass 1: collect ids to remove
removed = {
    s["id"] for s in data["spaces"]
    if isinstance(s, dict) and "id" in s
    and is_generic_iw(s["id"]) and s["id"] not in KEEP_GENERIC
}
print(f"Removing {len(removed)} auto-generated iw_X_Y cells.")

# Pass 2: drop those spaces
data["spaces"] = [
    s for s in data["spaces"]
    if not (isinstance(s, dict) and s.get("id") in removed)
]

# Pass 3: strip references to removed cells from every remaining space's neighbors
for s in data["spaces"]:
    if not isinstance(s, dict) or "neighbors" not in s:
        continue
    s["neighbors"] = [n for n in s["neighbors"] if n not in removed]

# Pass 4: ensure bench bidirectional edges. The user one-way connected the
# bench nodes; add the reverse edges on the adjacent cells.
def find_space(sid: str):
    for s in data["spaces"]:
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    return None

for bench_id, nbr_id in [("iw_bench_10_13", "iw_10_14"), ("iw_bench_16_9", "iw_17_9")]:
    nbr = find_space(nbr_id)
    if nbr is not None and bench_id not in nbr.setdefault("neighbors", []):
        nbr["neighbors"].append(bench_id)

# Sanity: no kept iw_X_Y cell should still reference a removed one.
leaks = 0
for s in data["spaces"]:
    if not isinstance(s, dict) or "neighbors" not in s:
        continue
    for n in s["neighbors"]:
        if n in removed:
            leaks += 1
            print(f"  LEAK: {s['id']} still refs {n}")

print(f"neighbor leaks: {leaks}")

BOARD.write_text(json.dumps(data, indent=2) + "\n")
print("done")
