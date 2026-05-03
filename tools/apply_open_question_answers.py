"""One-shot: apply the user's answers to board.json._open_questions.

Run from the repo root:  python tools/apply_open_question_answers.py
"""
from __future__ import annotations

import json
from pathlib import Path

BOARD = Path(__file__).resolve().parent.parent / "data" / "board.json"

data = json.loads(BOARD.read_text())


def find_space(sid: str) -> dict:
    for s in data["spaces"]:
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    raise KeyError(sid)


# -- Q5: Devereux also draws a tower card (in addition to granting a coin) ---
# Encoded in the rules block via kind-dispatch list, not on the space itself.

# -- Q7: Extra turn resets consecutive doubles ------------------------------
ex = find_space("ww47_extra_turn")
ex.setdefault("action", {}).setdefault("params", {})["resets_consecutive_doubles"] = True

# -- Q8: Go back by roll = uses the dice total that put you on this square --
gb = find_space("ww63_go_back")
gb.setdefault("action", {}).setdefault("params", {})["uses_landing_roll"] = True

# -- Q11: Go-to-Queens-House + accredit ends the turn on landing ------------
qa = find_space("ww31_qh_accredit")
qa.setdefault("action", {}).setdefault("params", {})["ends_turn_on_landing"] = True

# -- Q22, Q23: rope and ladder are two-way but require a (reusable) rope/ladder card --
for edge in data.get("traversal_edges", []):
    if edge.get("item") == "rope":
        edge["built_in"] = False  # a card is required
        edge["consumes_card"] = False  # but the card isn't discarded
        edge["requires_card"] = "rope"
        edge["direction"] = "bidirectional"
        # Movement into the White Tower via the rope still triggers
        # white_tower_forward_only once you're inside.
        edge["notes"] = "Two-way, reusable. Entering the tower this way still obeys forward-only."
    elif edge.get("item") == "ladder":
        edge["built_in"] = False
        edge["consumes_card"] = False
        edge["requires_card"] = "ladder"
        edge["direction"] = "bidirectional"
        edge["notes"] = "Two-way, reusable. Entering the chapel still obeys forward-only."
    elif edge.get("item") in (None, "secret_passage"):
        # Q24: Chapel Royal <-> Salt: bidirectional, free, costs 1 move
        edge["item"] = "secret_passage"
        edge["built_in"] = True
        edge["consumes_card"] = False
        edge["direction"] = "bidirectional"
        edge["movement_cost"] = 1
        edge["notes"] = "Bidirectional. Counts as one step of movement."

# -- Q25: Outside south exterior row = free movement (no forward-only). ------
# (No field needed — forward-only is opt-in per region in the rules engine.)

# -- Q26: Chapel Royal has no special landing effect (purely a location). ----
# No change needed; kind=chapel_royal currently has no `action`.

# -- Q27: Museum draws a tower card on landing. ------------------------------
# Encoded via rules.tower_card_draw_kinds below.

# -- Q28: out_7_2 does NOT connect to (6,3) or Queen's House. ----------------
# Already only lists ['iw_7_3','iw_8_2','out_8_m3']; nothing to remove.

# -- Rules block: record the authoritative mechanic clarifications -----------
rules = data.setdefault("rules", {})
rules.update({
    # Q1: wall walk is a linear dead-end, not a cycle.
    "wall_walk_is_closed_loop": False,

    # Q2/Q3/Q4/Q5/Q6/Q27: auto-tower-card-draw mechanic. Kinds in this set
    # auto-draw a tower card when landed on. Listed space ids override.
    "tower_card_draw_kinds": ["tower", "devereux", "museum"],
    "tower_card_draw_exception_space_ids": [
        "ww29_broad_arrow",  # Q3: surrender weapons only
    ],
    # Q4: kind=bowyer_tower → torture only (no tower card). Already excluded
    # by virtue of not being in tower_card_draw_kinds.
    # Q6: kind=beauchamp_tower is 'just visiting' when reached via wall walk;
    # confinement happens only via the Raven card that sends a player there.
    "beauchamp_tower_confinement_only_from_raven_card": True,

    # Q5: Devereux grants a coin AND draws a tower card.
    "devereux_grants_coin": True,

    # Q7: 'Extra turn' resets the consecutive-doubles counter.
    "extra_turn_resets_consecutive_doubles": True,

    # Q8: 'Go back the number thrown' uses the dice total that caused you to
    # land on that square (whether from the normal roll or a split-7 leg).
    "go_back_uses_landing_roll": True,

    # Q9: 'Swap a card' — attacker picks one of their own cards + a target
    # player; one of the target's cards is chosen at random and the two are
    # exchanged.
    "swap_card_defender_random": True,

    # Q10: 'Change a card' — discard one card from hand, draw top of tower deck.
    "change_card_draws_top_of_tower_deck": True,

    # Q11: 'Go to Queen's House & accredit' ends the turn on landing.
    "qh_accredit_ends_turn_on_landing": True,

    # Q18: warders start in their posts (not the barracks).
    "warders_start_in_posts": True,

    # Q21: White Tower forward-only — already present — can't be forced back.
    # Q22/Q23: rope/ladder are two-way but require a (non-discarded) card.
    # Q24: secret passage is bidirectional and counts as 1 step.
    "secret_passage_movement_cost": 1,

    # Q25: exterior_south region uses free movement (no forward-only).
    "exterior_south_forward_only": False,

    # Q26: Chapel Royal has no special landing effect (location-only).

    # Q28: out_7_2 does NOT connect to Queen's House or to iw_6_3.
})

# All 28 open questions have been answered or already resolved.
data["_open_questions"] = []
data.setdefault("_answered_questions_note", (
    "All initial open questions have been resolved with the user. "
    "See the `rules` block for mechanic clarifications; per-space action "
    "params (e.g. resets_consecutive_doubles, uses_landing_roll, "
    "ends_turn_on_landing) encode the rest."
))

BOARD.write_text(json.dumps(data, indent=2) + "\n")
print("Applied.  Remaining open questions:", len(data["_open_questions"]))
