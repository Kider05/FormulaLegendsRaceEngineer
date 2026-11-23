"""AI race snapshot: pre-race comparison, neighbours, lap summary, tyre life."""

import pytest

from engineer import race_state
from helpers import PLANNER, SILVERSTONE, dry_laps


def test_plan_lap_times_add_pit_loss_at_stint_end():
    p = {"stints": [{"compound": "hard", "laps": 2}, {"compound": "soft", "laps": 1}]}
    times = PLANNER.lap_times(p)
    assert times[0] == pytest.approx(SILVERSTONE.lap_time("hard", 100))
    assert times[1] == pytest.approx(SILVERSTONE.lap_time("hard", 100 - 9.6) + 16.7)
    assert times[2] == pytest.approx(SILVERSTONE.lap_time("soft", 100))


def test_snapshot_compares_actual_to_prerace():
    laps = dry_laps(3, "hard", wear=10, laptime=47.0)
    best = PLANNER.plan(19, ban_second=True)[0]
    snap = race_state.build_snapshot(
        "Silverstone", 19, laps, {"tyre_compound": "hard", "fuel": "80"}, None, None, PLANNER, best, 6.6, 70.0
    )
    pre = snap["prerace_plan"]
    assert pre["actual_time_for_completed_laps_s"] == 141.0
    proj = snap["laps_summary"]["projected_finish_s"]
    assert proj["at_race_average_s"] == round(
        141.0 + snap["laps_summary"]["average_clean_lap_s"] * snap["laps_remaining"], 1
    )
    diff = 141.0 - pre["predicted_time_for_completed_laps_s"]
    assert pre["driver_vs_plan"] == f"{'behind' if diff > 0 else 'ahead of'} the plan by {abs(diff):.1f} s"
    assert pre["predicted_time_for_completed_laps_s"] == pytest.approx(sum(PLANNER.lap_times(best)[:3]), abs=0.1)
    assert snap["laps_remaining"] == 16 and snap["fuel"]["needed_to_finish"] == pytest.approx(105.6)
    assert snap["now"]["fuel_hud"] == 80 and snap["current_plan"] is None
    import json

    json.dumps(snap)


def test_neighbours_report_gap_and_closing_rate_from_driver_view():
    lb = {
        "position": 3,
        "total_cars": 14,
        "pace": {2: 0.4, 4: -0.6},
        "smoothed": {"position": 3, "gaps": {2: -1.8, 4: 2.4}, "gap_to_leader_sec": 6.0},
    }
    order = race_state.build_snapshot("S", 20, [], {}, lb, None, None, None, None, 100)["race_order"]
    assert order["car_ahead"] == {"position": 2, "gap_s": 1.8, "closing_s_per_lap": 0.4, "laps_until_gap_closes": 4.5}
    assert order["car_behind"] == {"position": 4, "gap_s": 2.4, "closing_s_per_lap": 0.6, "laps_until_gap_closes": 4.0}
    leader = race_state.build_snapshot(
        "S",
        20,
        [],
        {},
        {**lb, "position": 1, "smoothed": {"position": 1, "gaps": {}}},
        None,
        None,
        None,
        None,
        100,
    )["race_order"]
    assert leader["car_ahead"] is None and leader["car_behind"]["gap_s"] is None


def test_lap_summary_uses_clean_laps_and_current_stint():
    laps = dry_laps(4, "medium", laptime=46.0)
    laps[0]["laptime_sec"] = 49.0
    laps[2]["laptime_sec"] = 45.5
    laps[3] = {**laps[3], "laptime_sec": 55.0, "is_pitstop_lap": True}
    laps.append({**dry_laps(1, "hard", laptime=58.0)[0], "lap": 5, "is_pitstop_lap": True, "is_pit_out_lap": True})
    laps.append({**dry_laps(1, "hard", laptime=46.2)[0], "lap": 6})
    s = race_state.lap_summary(laps)
    assert s["best_lap_s"] == 45.5 and s["best_lap_number"] == 3 and s["last_minus_best_s"] == 0.7
    assert s["average_clean_lap_s"] == pytest.approx((46.0 + 45.5 + 46.2) / 3, abs=1e-3)
    assert s["current_stint"] == {"compound": "hard", "laps": 2, "average_clean_lap_s": 46.2}
    assert s["last_clean_lap_s"] == 46.2
    assert [race_state.lap_type(lap) for lap in laps] == ["lap 1", "clean", "clean", "pit in", "pit out", "clean"]
    del laps[-1]
    assert race_state.lap_summary(laps)["last_clean_lap_s"] == 45.5


def test_tyre_life_uses_this_stints_wear_on_any_compound():
    laps = dry_laps(5, "wet", wear=11)
    laps[4] = {**laps[4], "is_pitstop_lap": True}
    laps += [
        {**lap, "lap": 6 + i, "is_pitstop_lap": i == 0, "is_pit_out_lap": i == 0}
        for i, lap in enumerate(dry_laps(3, "wet", wear=12))
    ]
    life = race_state.tyre_life(laps, "wet", 64.0, 5, None)
    assert life == {
        "compound": "wet",
        "tyre_pct_start_of_lap": 64.0,
        "wear_pct_per_lap": 12.0,
        "wear_measured_from": "this stint",
        "laps_left_incl_this_one": 4,
        "lasts_to_finish": False,
    }
    fresh = race_state.tyre_life(laps[:5], "wet", 100.0, 3, None)
    assert (
        fresh["wear_measured_from"] == "earlier laps" and fresh["wear_pct_per_lap"] == 11.0 and fresh["lasts_to_finish"]
    )
    assert race_state.tyre_life([], "hard", 100.0, 9, SILVERSTONE)["wear_measured_from"] == "tyre model"
    assert race_state.tyre_life([], "wet", 100.0, 9, None) is None


def test_pit_kind_labels_in_out_and_other_laps():
    from strategy.race_analysis import is_pit_in, pit_kind

    assert [
        pit_kind(lap) for lap in ({}, {"is_pitstop_lap": True}, {"is_pitstop_lap": True, "is_pit_out_lap": True})
    ] == [
        None,
        "in",
        "out",
    ]
    assert is_pit_in({"is_pitstop_lap": True}) and not is_pit_in({"is_pitstop_lap": True, "is_pit_out_lap": True})
