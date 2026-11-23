"""Tyre model: start-% bookkeeping, fitting, live pace offset, lap filters."""

import pytest

from helpers import dry_laps, jittered_race, model_lap, synthetic_laps, true_penalty
from strategy import tyre_model
from strategy.race_analysis import compound_rule_met
from strategy.tyre_model import (
    PRIOR_PENALTY_SLOPES,
    annotate_start_pct,
    current_tyre_pct,
    fit,
    fit_races,
)


def test_compound_rule_met_counts_wets():
    assert not compound_rule_met(dry_laps(3, "medium"))
    assert compound_rule_met(dry_laps(2, "wet") + dry_laps(2, "medium"))


def test_annotate_resets_before_out_lap_and_new_compound():
    race = [
        model_lap(1, "medium", None, 47, wear=12),
        model_lap(2, "medium", None, 47, wear=12),
        model_lap(3, "medium", None, 50, wear=12, is_pitstop_lap=True),
        model_lap(4, "medium", None, 55, wear=12, is_pitstop_lap=True, is_pit_out_lap=True),
        model_lap(5, "medium", None, 47, wear=12),
        model_lap(6, "hard", None, 50, wear=9),
        model_lap(7, "hard", None, 47, wear=9),
    ]
    pcts = [lap["start_pct"] for lap in annotate_start_pct(race)]
    assert pcts == [100, 88, 76, 100, 88, 100, 91]


def test_old_in_lap_rows_use_previous_compound():
    """Older rows store an in-lap under the new tyre; it was driven on the old one."""
    race = [
        model_lap(1, "wet", None, 49, wear=11),
        model_lap(2, "wet", None, 49, wear=11),
        model_lap(3, "medium", None, 55, wear=11, is_pitstop_lap=True),
        model_lap(4, "medium", None, 59, wear=12, is_pitstop_lap=True, is_pit_out_lap=True),
        model_lap(5, "medium", None, 47, wear=12),
    ]
    laps = annotate_start_pct(race)
    assert [lap["compound"] for lap in laps] == ["wet", "wet", "wet", "medium", "medium"]
    assert laps[4]["near_wet"]


def test_mid_lap_pct_is_half_a_lap_of_wear_in():
    lap = annotate_start_pct([model_lap(2, "hard", None, 46, wear=10)])[0]
    assert lap["mid_pct"] == 95


def test_model_ignores_lap1_pit_laps_and_dead_tyres():
    assert not tyre_model.is_model_lap(model_lap(1, "soft", 100.0, 49))
    assert not tyre_model.is_model_lap(model_lap(5, "soft", 50.0, 49, is_pit_out_lap=True))
    assert not tyre_model.is_model_lap(model_lap(5, "soft", -2.0, 49))
    assert tyre_model.is_model_lap(model_lap(5, "soft", 50.0, 46))


def test_fit_recovers_base_pace():
    m = fit(synthetic_laps())
    assert m.base_pace["soft"] == pytest.approx(45.5, abs=0.15)
    assert m.base_pace["medium"] == pytest.approx(46.4, abs=0.15)
    assert m.penalty(35) == pytest.approx(true_penalty(35), abs=0.4)


def test_fit_shrugs_off_driver_errors():
    clean, messy = fit(synthetic_laps()), fit(synthetic_laps(outlier_every=6))
    assert messy.base_pace["medium"] == pytest.approx(clean.base_pace["medium"], abs=0.25)


def test_no_low_tyre_laps_falls_back_to_prior():
    laps = [model_lap(i + 2, "medium", 100.0 - 5 * i, 46.4) for i in range(8)]
    assert fit(laps).penalty_slopes == pytest.approx(PRIOR_PENALTY_SLOPES, abs=0.01)


def test_penalty_never_shrinks_as_tyre_wears():
    laps = synthetic_laps() + [model_lap(90 + i, "soft", 10.0, 44.0) for i in range(30)]
    m = fit(laps)
    assert m.penalty(10) >= m.penalty(35) >= m.penalty(45)


def test_too_few_laps_drops_compound():
    laps = synthetic_laps() + [model_lap(99, "hard", 90.0, 47.0)]
    assert "hard" not in fit(laps).compounds


def test_slick_laps_next_to_wets_are_skipped():
    race = (
        [model_lap(i, "medium", None, 46.5) for i in range(1, 5)]
        + [model_lap(i, "wet", None, 49.0) for i in range(5, 8)]
        + [model_lap(i, "medium", None, 47.5) for i in range(8, 13)]
    )
    flags = {lap["lap"]: lap["near_wet"] for lap in annotate_start_pct(race)}
    assert [n for n, f in flags.items() if f] == [3, 4, 8, 9]


def test_fit_races_pools_several_races():
    races = [[model_lap(i, "medium", None, 46.4, wear=12) for i in range(1, 6)] for _ in range(2)]
    m = fit_races(races)
    assert m.lap_counts["medium"] == 8
    assert m.base_pace["medium"] == pytest.approx(46.4, abs=0.05)


def test_current_tyre_pct_tracks_wear_and_stops():
    race = [model_lap(1, "medium", None, 47.0, wear=12), model_lap(2, "medium", None, 46.5, wear=12)]
    assert current_tyre_pct([]) == 100.0
    assert current_tyre_pct(race) == pytest.approx(76.0)
    race.append({**model_lap(3, "medium", None, 55.0, wear=12), "is_pitstop_lap": True, "tyre_gained": 80})
    assert current_tyre_pct(race) == 100.0


def test_live_race_offset_follows_current_pace():
    history = [jittered_race(6, 46.4, 5) for _ in range(3)]
    m = fit_races(history + [jittered_race(9, 47.4, 5)], use_last_race_offset=True)
    assert 0.5 < m.pace_offset < 1.0
    assert m.lap_time("medium", 90) == pytest.approx(m.base_pace["medium"] + m.pace_offset)
    assert fit_races(history + [jittered_race(9, 47.4, 5)]).pace_offset == 0.0


def test_one_live_lap_barely_moves_offset():
    history = [jittered_race(6, 46.4, 5) for _ in range(3)]
    assert fit_races(history + [jittered_race(2, 47.4, 5)], use_last_race_offset=True).pace_offset < 0.3


def test_model_skips_excluded_laps_and_slicks_marked_wet():
    lap = {"lap": 3, "compound": "medium", "laptime_sec": 46.0, "start_pct": 80.0}
    assert tyre_model.is_model_lap(lap)
    assert not tyre_model.is_model_lap({**lap, "excluded": True})
    assert not tyre_model.is_model_lap({**lap, "condition": "drying"})
    assert tyre_model.is_model_lap({**lap, "condition": "dry"})
    assert tyre_model.is_model_lap({**lap, "compound": "wet", "condition": "wet"})
