"""Per-player redaction of :class:`GameState` for transmission over the wire.

Opponents never see each other's hands — only the hand size. Combat card
selections are hidden until both sides commit. The pending_move destinations
(and any other info the current turn holder needs to decide) are sent intact
to the acting player; other players receive a trimmed version.
"""
from __future__ import annotations

from typing import Any, Optional

from ..game.state import GameState


def redact_game_for_player(game: GameState, username: Optional[str]) -> dict[str, Any]:
    """Return a JSON-serialisable dict snapshot of ``game`` for ``username``.

    - ``username`` is the viewer. They see their own hand in full.
    - Every other player has ``hand`` replaced by ``hand_size``.
    - Combat ``attacker_cards`` / ``defender_cards`` are hidden from the
      opposing side (and any spectator) until the combat phase is ``revealed``
      or ``resolved``.
    - Pending move destinations are only included if the viewer is the
      current turn holder. Other viewers still see ``phase`` / whose turn it is.
    """
    data = game.model_dump()

    # Players. Once the game is over there's nothing left to protect, and the
    # end-of-game screen shows everyone's final hand — so stop redacting.
    game_over = getattr(game.phase, "value", game.phase) == "GAME_OVER"
    for p in data.get("players", []):
        p["hand_size"] = len(p.get("hand", []))
        if not game_over and p.get("username") != username:
            p["hand"] = []

    # Combat
    combat = data.get("combat")
    if combat is not None:
        phase = combat.get("phase")
        hide = phase in ("attacker_selecting", "defender_selecting", "defender_specials")
        if hide:
            if username != combat.get("attacker"):
                combat["attacker_cards_count"] = len(combat.get("attacker_cards", []))
                combat["attacker_cards"] = []
            if username != combat.get("defender"):
                combat["defender_cards_count"] = len(combat.get("defender_cards", []))
                combat["defender_cards"] = []

    # Decks: reveal only counts, not contents.
    data["tower_draw_count"] = len(game.tower_draw)
    data["tower_discard_count"] = len(game.tower_discard)
    data["raven_draw_count"] = len(game.raven_draw)
    data["raven_discard_count"] = len(game.raven_discard)
    data["tower_draw"] = []
    data["tower_discard"] = []
    data["raven_draw"] = []
    data["raven_discard"] = []

    # Pending move destinations are meaningful only to the acting player.
    turn = data.get("turn") or {}
    cur = _current_turn_username(game)
    if username != cur:
        pm = turn.get("pending_move")
        if pm is not None:
            turn["pending_move"] = {
                "steps": pm.get("steps"),
                "remaining_steps": pm.get("remaining_steps", 0),
                "has_destinations": bool(pm.get("destinations")),
            }
    data["turn"] = turn

    return data


def _current_turn_username(game: GameState) -> Optional[str]:
    if not game.turn_order:
        return None
    try:
        return game.turn_order[game.current_turn_index]
    except IndexError:
        return None
