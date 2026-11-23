"""Strategy planner: pre-race plans, pit options and stay-out plans."""

import pytest

from helpers import PLANNER, SILVERSTONE, stint_set
from strategy import strategy_planner
from strategy.strategy_planner import Planner, format_plan, max_stint_laps


def test_planner_extends_every_stint_when_it_saves_stops():
    best = PLANNER.plan(19, ban_second=True)[0]
    assert stint_set(best) == ["H7", "M6", "M6"]
    assert best["total_time_sec"] == pytest.approx(926.1, abs=0.1)


def test_planner_short_race_single_stint():
    assert format_plan(PLANNER.plan(7, ban_second=True)[0]) == "H7"


def test_planner_pre_race_rule_second_stint_differs():
    for p in PLANNER.plan(12, ban_second=True, top_n=5):
        if p["stops"] >= 1:
            assert p["stints"][0]["compound"] != p["stints"][1]["compound"]


def test_planner_live_rule_bans_current_compound():
    for p in PLANNER.plan(9, first_banned="medium", top_n=5):
        assert p["stints"][0]["compound"] != "medium"


def test_planner_alternatives_are_different_stint_sets():
    sets = [tuple(stint_set(p)) for p in PLANNER.plan(20, ban_second=True, top_n=3)]
    assert len(sets) == len(set(sets))


def test_puncture_limit_blocks_deep_stints(monkeypatch):
    monkeypatch.setattr(strategy_planner, "PUNCTURE_LIMIT_PCT", 25.0)
    for p in Planner(SILVERSTONE, 16.7).plan(22, ban_second=True, top_n=5):  # fresh: PLANNER has cached costs
        for s in p["stints"]:
            last_start = 100 - SILVERSTONE.wear_per_lap[s["compound"]] * (s["laps"] - 1)
            assert last_start > 25.0


def test_planner_without_data_returns_nothing():
    assert PLANNER.plan(0) == []


def test_pit_options_counts_laps_left():
    options = PLANNER.pit_options(15, "medium", rule_met=False)
    assert [(label, left) for label, left, _ in options] == [("this lap", 14), ("next lap", 13)]


def test_pit_options_bans_current_compound_until_rule_met():
    for _, _, plans in PLANNER.pit_options(15, "hard", rule_met=False, top_n=5):
        assert all(p["stints"][0]["compound"] != "hard" for p in plans)
    _, _, plans = PLANNER.pit_options(15, "hard", rule_met=True, top_n=5)[0]
    assert any(p["stints"][0]["compound"] == "hard" for p in plans)


def test_pit_options_last_lap_has_nothing_to_plan():
    options = PLANNER.pit_options(1, "soft", rule_met=True)
    assert all(left <= 0 and plans == [] for _, left, plans in options)


def test_max_stint_stops_before_tyre_runs_out():
    assert {c: max_stint_laps(SILVERSTONE, c) for c in ("soft", "medium", "hard")} == {
        "soft": 6,
        "medium": 7,
        "hard": 9,
    }


def test_no_planned_lap_ends_under_min_end():
    for p in PLANNER.plan(25, ban_second=True, top_n=5):
        for s in p["stints"]:
            wear = SILVERSTONE.wear_per_lap[s["compound"]]
            assert 100 - wear * s["laps"] >= strategy_planner.MIN_END_PCT


def test_fresh_tyre_near_the_end_stays_out():
    best = PLANNER.from_current(3, "soft", 100.0, rule_met=True)[0]
    assert best["pit_in"] is None


def test_tyre_too_worn_for_another_lap_pits_this_lap():
    best = PLANNER.from_current(10, "medium", 22.0, rule_met=True)[0]
    assert best["pit_in"] == 1


def test_stay_out_respects_compound_rule():
    for o in PLANNER.from_current(15, "hard", 80.0, rule_met=False, top_n=None):
        if o["stints"]:
            assert o["stints"][0]["compound"] != "hard"


def test_stay_out_never_runs_tyre_flat():
    for o in PLANNER.from_current(12, "soft", 60.0, rule_met=True, top_n=None):
        assert o["pit_in"] is not None and 60 - 15.8 * o["pit_in"] >= strategy_planner.MIN_END_PCT


def test_pit_this_lap_offered_even_on_dead_tyre():
    options = PLANNER.from_current(8, "soft", 4.0, rule_met=True, top_n=None)
    assert [o["pit_in"] for o in options] == [1]


def test_wets_have_no_stay_out_plan():
    assert PLANNER.from_current(8, "wet", 80.0, rule_met=True) == []


def test_unknown_pit_loss_is_planned_as_free_but_stays_unknown():
    unknown = Planner(SILVERSTONE, None)
    assert unknown.plan(19, ban_second=True) == Planner(SILVERSTONE, 0.0).plan(19, ban_second=True)
    assert unknown.pit_loss_sec is None and Planner.for_model(None, 16.7) is None


def test_planner_answers_match_a_fresh_planner_after_reuse():
    """The cached rest-of-race table is shared between calls; it must not leak between different questions."""
    used = Planner(SILVERSTONE, 16.7)
    used.plan(25, ban_second=True, top_n=5)
    used.from_current(18, "medium", 70.0, rule_met=False, top_n=None)
    fresh = Planner(SILVERSTONE, 16.7)
    assert used.plan(19, first_banned="hard", top_n=5) == fresh.plan(19, first_banned="hard", top_n=5)
    assert used.pit_options(12, "soft", rule_met=False) == fresh.pit_options(12, "soft", rule_met=False)
