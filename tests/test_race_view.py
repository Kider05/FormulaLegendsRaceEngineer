"""GUI text builders, live view, wet window and strategy chart rows."""

import pytest

from engineer import callouts
from gui import race_view
from helpers import PLANNER, SILVERSTONE, dry_laps, smoothed
from strategy.strategy_planner import Planner


@pytest.mark.parametrize("text, expected", [("20", 20), (" 7 ", 7), ("", None), ("x", None), ("7.5", None)])
def test_parse_laps(text, expected):
    assert race_view.parse_laps(text) == expected


def test_prerace_text_messages():
    assert race_view.prerace_text("", "20", PLANNER)[0].startswith("Enter a track")
    assert race_view.prerace_text("Silverstone", "x", PLANNER)[0] == "Race Laps must be a whole number."
    assert race_view.prerace_text("Silverstone", "20", None)[1] is None
    text, best = race_view.prerace_text("Silverstone", "19", PLANNER)
    assert text.startswith("1. ") and best["stops"] >= 1


def test_live_view_warns_on_worn_tyre():
    laps = dry_laps(6, "medium", wear=12)
    view = race_view.live_view("20", laps, "medium", PLANNER)
    assert view["warning"] == "PIT THIS LAP" and view["in_pit_window"]
    assert "28%" in view["stint"]


def test_live_view_fresh_tyre_has_no_warning():
    view = race_view.live_view("20", dry_laps(1, "hard", wear=10), "hard", PLANNER)
    assert view["warning"] == "" and not view["in_pit_window"]
    assert view["plan"].startswith("Best: pit in")


def test_live_view_without_laps_or_model():
    assert race_view.live_view("", [], None, PLANNER)["plan"] == "-- (enter Race Laps)"
    assert race_view.live_view("20", [], None, None)["stint"] == "No laps yet."


def test_pit_now_text_switches_to_pace_in_window():
    lb = {"smoothed": smoothed(3, {4: 17.0, 5: 40.0}), "pace": {4: 1.0}, "total_cars": 14}
    assert "starts in pit window" in race_view.pit_now_text(lb, 16.7, 9, in_pit_window=False)
    inside = race_view.pit_now_text(lb, 16.7, 9, in_pit_window=True)
    assert "car behind +1.0s/lap" in inside and inside.startswith("Pit now: 0 pos lost")


def test_compound_limits_lists_every_compound():
    limits = race_view.compound_limits(SILVERSTONE)
    assert limits["soft"] == "Soft: max 6 laps (16%/lap)" and limits["wet"] == "Wet: --"


def test_default_fuel_text_has_wet_extended():
    text = race_view.default_fuel_text(6.6)
    assert "H6 -> 50 fuel" in text and "W5 -> 43 fuel\nW7 -> 56 fuel (extended)" in text


def test_next_fuel_text_shows_beep_point():
    assert race_view.next_fuel_text({"compound": "hard", "laps": 8}, 6.6) == "Next: H8 -> 63 fuel (beep 56)"
    assert race_view.next_fuel_text(None, 6.6) == "Next: --"


def test_no_pit_options_before_tyre_is_read():
    assert race_view.live_view("20", [], None, PLANNER)["plan"] == "(waiting for tyre compound read)"


def test_no_current_plan_on_wets():
    view = race_view.live_view("20", dry_laps(3, "wet"), "wet", PLANNER)
    assert view["current_plan"] is None and view["pit_now_stint"] is None


def test_current_plan_follows_tyre_on_car():
    view = race_view.live_view("20", dry_laps(2, "soft", wear=16), "soft", PLANNER)
    assert view["current_plan"]["best"]["pit_in"] is not None
    assert all(s["compound"] in ("soft", "medium", "hard") for s in view["current_plan"]["best"]["stints"])


def test_wet_window_boxes_after_five_laps_on_the_set():
    laps = dry_laps(3, "wet")
    assert race_view.wet_window(20, laps) == {"pit_in": 2, "box_lap": 5}
    laps += dry_laps(1, "wet")
    laps[-1]["lap"] = 4
    assert race_view.wet_window(20, laps) == {"pit_in": 1, "box_lap": 5}
    laps[-1]["is_pitstop_lap"] = True
    assert race_view.laps_on_set(laps) == 0 and race_view.wet_window(20, laps)["box_lap"] == 9
    assert race_view.wet_window(8, laps)["pit_in"] is None
    view = race_view.live_view("20", laps, "wet", Planner(SILVERSTONE, 20.0))
    lines = view["plan"].split("\n")
    assert lines[0] == "Wet: possible pit window lap 9 (W5)" and view["current_plan"] is None
    assert view["stint"].startswith("Wet ") and view["stint"].endswith("window lap 9")
    assert lines[1].startswith("Pit this lap") and lines[2].startswith("Pit next lap")
    assert set(view["slick_options"]) == {"this lap", "next lap"} and all(view["slick_options"].values())
    assert callouts.lap_message(None, wet={"pit_in": 1, "box_lap": 5}) == ("Possible pit window this lap.",)
    assert callouts.lap_message(None, wet={"pit_in": 2, "box_lap": 6}) == ()


def test_strategy_rows_join_driven_stints_with_plans():
    laps = dry_laps(7, "medium") + dry_laps(2, "hard")
    for i, lap in enumerate(laps):
        lap["lap"] = i + 1
    laps[6]["is_pitstop_lap"] = True
    laps[7].update(is_pitstop_lap=True, is_pit_out_lap=True)
    best = {"pit_in": 4, "stints": [{"compound": "medium", "laps": 7}], "total_time_sec": 100.0}
    view = {
        "current": "hard",
        "current_plan": {"best": best},
        "wet_window": None,
        "pit_soon": {
            "pit_in": 1,
            "stints": [{"compound": "soft", "laps": 5}, {"compound": "medium", "laps": 5}],
            "note": "+8.9s",
        },
    }
    prerace = {
        "stints": [{"compound": "hard", "laps": 7}, {"compound": "medium", "laps": 6}, {"compound": "hard", "laps": 7}]
    }
    pre, live, soon = race_view.strategy_rows(20, laps, view, prerace)
    assert [(s["compound"], s["start"], s["end"]) for s in pre["segments"]] == [
        ("hard", 1, 7),
        ("medium", 8, 13),
        ("hard", 14, 20),
    ]
    assert live["segments"] == [
        {"compound": "medium", "start": 1, "end": 7, "done_until": 7},
        {"compound": "hard", "start": 8, "end": 13, "done_until": 9},
        {"compound": "medium", "start": 14, "end": 20, "done_until": None},
    ]
    assert soon["label"] == "Box this lap" and soon["note"] == "+8.9s"
    assert [(s["compound"], s["start"], s["end"]) for s in soon["segments"]][1:] == [
        ("hard", 8, 10),
        ("soft", 11, 15),
        ("medium", 16, 20),
    ]
    wet = race_view.strategy_rows(
        20, laps[:3], {"current": "wet", "current_plan": None, "wet_window": {"pit_in": 2, "box_lap": 5}}, None
    )
    assert wet[0]["segments"][-1] == {"compound": "wet", "start": 4, "end": 5, "done_until": None}
