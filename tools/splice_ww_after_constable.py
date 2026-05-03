"""Insert two yellow wall-walk squares between Constable Tower and Go-to-Queen's-House.

Every wall-walk space with ``wall_walk_order >= 35`` is bumped by +2 and its id
is renamed from ``ww{N:02d}{suffix}`` to ``ww{N+2:02d}{suffix}``. Two new blank
yellow (kind=normal) squares are inserted as the new ww35 and ww36.

All cross-references to renamed ids are fixed up:
  - other spaces' ``neighbors`` lists
  - ``slides`` (src / to)
  - ``traversal_edges`` (src / to)
  - top-level anchor fields (queens_house_space, devereux_space, ...)
  - data/raven_cards.json params referencing wall-walk spaces
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "data" / "board.json"
RAVEN = ROOT / "data" / "raven_cards.json"

data = json.loads(BOARD.read_text())

WW_ID_RE = re.compile(r"^ww(\d{2})(.*)$")


def parse_ww(sid: str):
    """Return (order, suffix) if sid is a wall-walk id, else None."""
    m = WW_ID_RE.match(sid)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


# Build rename map: every wall-walk id with order >= 35 gets +2.
rename: dict[str, str] = {}
for s in data["spaces"]:
    if not isinstance(s, dict):
        continue
    sid = s.get("id", "")
    parsed = parse_ww(sid)
    if parsed is None:
        continue
    order, suffix = parsed
    # Only wall-walk region spaces are renumbered (e.g. wt_* / iw_* / out_*
    # don't match the regex anyway, but guard for safety).
    if s.get("region") != "wall_walk":
        continue
    if order >= 35:
        rename[sid] = f"ww{order + 2:02d}{suffix}"

print(f"Renaming {len(rename)} wall-walk ids (order >= 35)  +2 each.")


def rn(sid: str) -> str:
    return rename.get(sid, sid)


# Pass 1: bump wall_walk_order and rename id on each affected wall-walk space.
for s in data["spaces"]:
    if not isinstance(s, dict):
        continue
    if s.get("region") != "wall_walk":
        continue
    parsed = parse_ww(s.get("id", ""))
    if parsed is None:
        continue
    order, _ = parsed
    if order >= 35:
        s["id"] = rn(s["id"])
        s["wall_walk_order"] = order + 2

# Pass 2: rewrite every ``neighbors`` list in every space.
for s in data["spaces"]:
    if not isinstance(s, dict) or "neighbors" not in s:
        continue
    s["neighbors"] = [rn(n) for n in s["neighbors"]]

# Pass 3: slides + traversal_edges.
for slide in data.get("slides", []):
    if isinstance(slide, dict):
        for key in ("from_space", "to_space", "src", "to"):
            if key in slide:
                slide[key] = rn(slide[key])
for edge in data.get("traversal_edges", []):
    if isinstance(edge, dict):
        for key in ("from_space", "to_space", "src", "to"):
            if key in edge:
                edge[key] = rn(edge[key])

# Pass 4: top-level anchor fields.
ANCHOR_KEYS = [
    "start_space",
    "escape_space",
    "queens_house_space",
    "devereux_space",
    "rack_space",
    "bloody_tower_space",
    "beauchamp_tower_space",
    "bowyer_tower_space",
    "chapel_royal_space",
    "chapel_st_john_space",
    "hospital_space",
    "museum_space",
    "royal_armouries_space",
    "shop_space",
    "barracks_space",
    "raven_deck_space",
]
for key in ANCHOR_KEYS:
    if key in data and isinstance(data[key], str):
        data[key] = rn(data[key])

# Pass 5: warder_posts + initial_warders + initial_jewel_locations.
for post in data.get("warder_posts", []):
    if isinstance(post, dict):
        if "space_id" in post:
            post["space_id"] = rn(post["space_id"])
        if "blocks" in post and isinstance(post["blocks"], list):
            post["blocks"] = [rn(x) for x in post["blocks"]]
        if "blocks_space_ids" in post and isinstance(post["blocks_space_ids"], list):
            post["blocks_space_ids"] = [rn(x) for x in post["blocks_space_ids"]]
for w in data.get("initial_warders", []):
    if isinstance(w, dict) and "location" in w:
        w["location"] = rn(w["location"])
for jid, sid in list(data.get("initial_jewel_locations", {}).items()):
    data["initial_jewel_locations"][jid] = rn(sid)
data["metallicity_destination_ids"] = [
    rn(x) for x in data.get("metallicity_destination_ids", [])
]
data["bench_space_ids"] = [rn(x) for x in data.get("bench_space_ids", [])]

# Pass 6: insert the two new yellow squares as ww35 and ww36.
# After the rename, ww34_constable still exists, ww35_go_qh is now ww37_go_qh,
# and the next one after that is ww38 (was ww36 in the old numbering).

# First find ww34_constable and the space that used to be ww35_go_qh (now ww37_go_qh).
constable = next(s for s in data["spaces"] if isinstance(s, dict) and s.get("id") == "ww34_constable")
after_new_block = next(s for s in data["spaces"] if isinstance(s, dict) and s.get("id") == "ww37_go_qh")

ww35_new = {
    "id": "ww35",
    "label": "",
    "region": "wall_walk",
    "kind": "normal",
    "wall_walk_order": 35,
    "neighbors": ["ww34_constable", "ww36"],
}
ww36_new = {
    "id": "ww36",
    "label": "",
    "region": "wall_walk",
    "kind": "normal",
    "wall_walk_order": 36,
    "neighbors": ["ww35", "ww37_go_qh"],
}

# Rewire constable and ww37_go_qh to point at the new blocks instead of each other.
constable["neighbors"] = ["ww33", "ww35"]
after_new_block["neighbors"] = ["ww36"] + [n for n in after_new_block["neighbors"] if n != "ww34_constable"]
# Preserve deterministic order: the original had ['ww34_constable', 'ww38'] (now renamed
# to 'ww38' already via Pass 2 -> which is the same ww38 because that was order 36 -> 38).
# Make sure ww37_go_qh's neighbor list is exactly [ww36, <next forward space>].
# Find what was forward of the old ww35_go_qh — it was order 36, which is now ww38.
# Validate:
forward = next(
    (s for s in data["spaces"]
     if isinstance(s, dict) and s.get("region") == "wall_walk" and s.get("wall_walk_order") == 38),
    None,
)
if forward is not None:
    after_new_block["neighbors"] = ["ww36", forward["id"]]
    # Also ensure ww38 points back at ww37_go_qh (it should already, since its
    # old neighbor ww35_go_qh was rewritten to ww37_go_qh in Pass 2).
    forward["neighbors"] = [rn(n) for n in forward["neighbors"]]

# Insert the two new spaces in the spaces list just after ww34_constable, so the
# array stays roughly in wall-walk order (the engine doesn't rely on this, but
# it's friendlier to diff).
idx = next(
    i for i, s in enumerate(data["spaces"])
    if isinstance(s, dict) and s.get("id") == "ww34_constable"
)
data["spaces"].insert(idx + 1, ww35_new)
data["spaces"].insert(idx + 2, ww36_new)

# --- raven_cards.json: fix any wall-walk id references in params --------------
raven_changed = 0
if RAVEN.exists():
    raven = json.loads(RAVEN.read_text())
    cards = raven if isinstance(raven, list) else raven.get("cards", [])
    for c in cards:
        if not isinstance(c, dict):
            continue
        params = c.get("params") or {}
        for k, v in list(params.items()):
            if isinstance(v, str) and v in rename:
                params[k] = rename[v]
                raven_changed += 1
            elif isinstance(v, list):
                new = [rename.get(x, x) if isinstance(x, str) else x for x in v]
                if new != v:
                    params[k] = new
                    raven_changed += 1
    RAVEN.write_text(json.dumps(raven, indent=2) + "\n")
    print(f"raven_cards.json: updated {raven_changed} param references.")

# --- save + verify -----------------------------------------------------------
BOARD.write_text(json.dumps(data, indent=2) + "\n")

# Sanity: walk the wall and print the new linear sequence.
ww = sorted(
    (s for s in data["spaces"] if isinstance(s, dict) and s.get("region") == "wall_walk"),
    key=lambda s: s.get("wall_walk_order", 0),
)
print(f"\nWall walk now has {len(ww)} spaces:")
for s in ww:
    print(f"  {s['wall_walk_order']:>3}  {s['id']:<30}  nbrs={s.get('neighbors', [])}")

# Reachability check from start.
all_ids = {s["id"] for s in data["spaces"] if isinstance(s, dict) and "id" in s}
adj = {sid: [] for sid in all_ids}
for s in data["spaces"]:
    if isinstance(s, dict) and "id" in s:
        for n in s.get("neighbors", []):
            if n in all_ids:
                adj[s["id"]].append(n)
for slide in data.get("slides", []):
    a, b = slide.get("from_space") or slide.get("src"), slide.get("to_space") or slide.get("to")
    if a in all_ids and b in all_ids:
        adj[a].append(b)
        if slide.get("bidirectional", True):
            adj[b].append(a)
for edge in data.get("traversal_edges", []):
    a, b = edge.get("from_space") or edge.get("src"), edge.get("to_space") or edge.get("to")
    if a in all_ids and b in all_ids:
        adj[a].append(b)
        if edge.get("direction", "bidirectional") == "bidirectional":
            adj[b].append(a)
seen = set()
stack = [data.get("start_space", "ww00_start")]
while stack:
    cur = stack.pop()
    if cur in seen:
        continue
    seen.add(cur)
    stack.extend(adj.get(cur, []))
unreachable = sorted(all_ids - seen)
print(f"\nReachable from start: {len(seen)}/{len(all_ids)}")
if unreachable:
    print(f"Unreachable: {unreachable}")
