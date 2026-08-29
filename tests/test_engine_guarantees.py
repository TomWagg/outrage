"""Guarantees the whole engine leans on, one test each.

These are not rules so much as the properties every rule is written against:
a rejected intent changes nothing, a sub-system refusal reads as a refusal,
both decks are shuffled when they come back round, a forced miss doesn't eat
what you paid for, and the birthday card knows what day it is.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from server.game.board import Board
from server.game.cards import Card
from server.game.combat import CombatError
from server.game.rng import Rng
from server.game.rules import RuleError, _GLOBAL_RNG, apply
from server.game.state import Combat, GameState, Phase, PlayerState


BOARD = Board.from_file(Path(__file__).resolve().parent.parent / "data" / "board.json")


def tower(name: str, **kw) -> Card:
    return Card(id=f"tower:{name}:1", kind="tower", name=name, **kw)


def make_state(**kw) -> GameState:
    _GLOBAL_RNG.set(Rng(seed=3))
    players = kw.pop("players", None) or [
        PlayerState(username="p1", color="red", position=BOARD.data.start_space),
        PlayerState(username="p2", color="blue", position=BOARD.data.start_space),
    ]
    return GameState(
        players=players,
        turn_order=[p.username for p in players],
        phase=kw.pop("phase", Phase.TURN_START),
        **kw,
    )


# ---------------------------------------------------------------------------
# A rejected intent leaves the caller's game exactly as it was
# ---------------------------------------------------------------------------


def test_a_rejected_intent_does_not_touch_the_callers_state():
    """Handlers mutate as they go, so ``apply`` has to work on a copy.

    ``attempt_jewel`` parks a pending attempt *before* it validates the tool
    cards, so a bad tool id is the shortest route to a handler that raises with
    changes already made.
    """
    jewel_space = BOARD.data.initial_jewel_locations["sword"]
    state = make_state(players=[
        PlayerState(username="p1", color="red", position=jewel_space),
    ])
    state.jewels_available = {"sword": jewel_space}
    before = state.model_dump()

    with pytest.raises(RuleError):
        apply(state, "attempt_jewel",
              {"username": "p1", "tool_card_ids": ["not-a-card"]},
              board=BOARD, rng=Rng(seed=1))

    assert state.model_dump() == before
    assert state.turn.pending_jewel is None


def test_a_successful_intent_still_returns_a_usable_state():
    """The copy is only a rollback device — a handler that returns hands over
    a state the caller can keep using."""
    state = make_state()
    new_state, evs = apply(state, "roll_dice", {"username": "p1"},
                           board=BOARD, rng=Rng(seed=5))
    assert [e["kind"] for e in evs][0] == "dice_rolled"
    assert new_state.turn.roll
    assert new_state is not state


# ---------------------------------------------------------------------------
# Sub-system refusals are refusals, not crashes
# ---------------------------------------------------------------------------


def test_a_combat_refusal_surfaces_as_a_rule_error():
    """``combat.py`` raises ``CombatError``. The server maps ``RuleError`` to a
    polite refusal and everything else to an internal error, so the two must be
    joined up or a wrong-phase reveal reads to the player as a crash."""
    state = make_state(phase=Phase.COMBAT)
    state.combat = Combat(attacker="p1", defender="p2",
                          space_id=BOARD.data.start_space,
                          phase="defender_selecting")

    with pytest.raises(RuleError) as caught:
        apply(state, "reveal_combat", {"username": "p1"},
              board=BOARD, rng=Rng(seed=1))
    assert not isinstance(caught.value, CombatError)
    assert "defender_selecting" in str(caught.value)


def test_an_effect_refusal_surfaces_as_a_rule_error():
    state = make_state(players=[
        PlayerState(username="p1", color="red",
                    position=BOARD.data.start_space, accredited=True,
                    hand=[tower("Sanctuary", category="utility",
                                effect_key="sanctuary")]),
        PlayerState(username="p2", color="blue", position=BOARD.data.start_space),
    ])
    # Sanctuary from the Chapel itself is fine; from a cell it is not.
    state.player("p1").status = state.player("p1").status.__class__.IMPRISONED
    with pytest.raises(RuleError):
        apply(state, "play_card_pre_roll",
              {"username": "p1", "card_id": "tower:Sanctuary:1"},
              board=BOARD, rng=Rng(seed=1))


# ---------------------------------------------------------------------------
# Both decks reshuffle when they come back round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deck", ["tower", "raven"])
def test_a_recycled_deck_is_shuffled(deck: str):
    """An unshuffled recycle deals the whole deck back out in a known order —
    which for the raven deck means the second pass is the first one backwards."""
    from server.game.rules import _draw_raven, _draw_tower

    cards = [Card(id=f"{deck}:c:{i}", kind=deck, name=f"c{i}") for i in range(40)]
    state = make_state()
    if deck == "tower":
        state.tower_draw, state.tower_discard = [], list(cards)
        draw = _draw_tower
    else:
        state.raven_draw, state.raven_discard = [], list(cards)
        draw = _draw_raven

    _GLOBAL_RNG.set(Rng(seed=99))
    order = [draw(state).id for _ in range(len(cards))]

    # A straight recycle pops from the end, so no shuffle would give exactly
    # the discard pile reversed.
    assert order != [c.id for c in reversed(cards)]
    assert sorted(order) == sorted(c.id for c in cards)


def test_a_deck_with_nothing_left_anywhere_returns_none():
    from server.game.rules import _draw_tower
    state = make_state()
    state.tower_draw, state.tower_discard = [], []
    assert _draw_tower(state) is None


# ---------------------------------------------------------------------------
# A forced miss doesn't swallow a Tower Pass
# ---------------------------------------------------------------------------


def test_a_tower_pass_bought_during_a_missed_turn_still_buys_a_turn():
    """The client offers card play while you are sitting a turn out, so the
    extra turn has to survive the miss being consumed — otherwise the card is
    spent on nothing."""
    state = make_state(players=[
        PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    miss_next_turn=True,
                    hand=[tower("Tower Pass", category="utility",
                                effect_key="tower_pass")]),
        PlayerState(username="p2", color="blue", position=BOARD.data.start_space),
    ])

    state, _ = apply(state, "play_card_pre_roll",
                     {"username": "p1", "card_id": "tower:Tower Pass:1",
                      "params": {"mode": "extra_turn"}},
                     board=BOARD, rng=Rng(seed=1))
    assert state.turn.extra_turns_queued == 1
    assert state.phase == Phase.PRE_ROLL      # playing a card moves the phase on

    state, evs = apply(state, "end_turn", {"username": "p1"},
                       board=BOARD, rng=Rng(seed=1))
    kinds = [e["kind"] for e in evs]

    # The miss is spent here, and the turn it bought starts now.
    assert "missed_turn" in kinds
    assert "extra_turn_used" in kinds
    assert state.current_player().username == "p1"
    assert state.player("p1").miss_next_turn is False
    assert state.phase == Phase.TURN_START


def test_a_plain_missed_turn_still_passes_play_on():
    state = make_state(players=[
        PlayerState(username="p1", color="red", position=BOARD.data.start_space,
                    miss_next_turn=True),
        PlayerState(username="p2", color="blue", position=BOARD.data.start_space),
    ])
    state, evs = apply(state, "end_turn", {"username": "p1"},
                       board=BOARD, rng=Rng(seed=1))

    assert "missed_turn" in [e["kind"] for e in evs]
    assert state.current_player().username == "p2"
    assert state.player("p1").miss_next_turn is False


# ---------------------------------------------------------------------------
# The birthday card knows what day it is
# ---------------------------------------------------------------------------


def _birthday_draw_counts(today: str | None) -> int:
    from server.game.cards_effects import dispatch
    state = make_state()
    state.tower_draw = [Card(id=f"tower:d:{i}", kind="tower", name="Dummy")
                        for i in range(20)]
    params = {} if today is None else {"today": today}
    _, evs = dispatch("queens_birthday", state, state.player("p1"), params,
                      board=BOARD, rng=Rng(seed=1))
    return len([e for e in evs if e["kind"] == "tower_card_drawn"])


def test_the_queens_birthday_doubles_the_draw_on_the_day():
    assert _birthday_draw_counts("2026-04-21") == 4   # 2 players x 2
    assert _birthday_draw_counts("2026-04-20") == 2   # 2 players x 1


def test_the_queens_birthday_falls_back_to_the_real_date():
    """No ``today`` param means the wall clock, so the card works in a real
    game rather than being permanently stuck on the off-day branch."""
    expected = 4 if (date.today().month, date.today().day) == (4, 21) else 2
    assert _birthday_draw_counts(None) == expected


def test_a_bad_today_value_is_refused_rather_than_ignored():
    from server.game.cards_effects import EffectError, dispatch
    state = make_state()
    with pytest.raises(EffectError):
        dispatch("queens_birthday", state, state.player("p1"),
                 {"today": "not-a-date"}, board=BOARD, rng=Rng(seed=1))
