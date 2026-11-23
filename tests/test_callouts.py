"""Voice callouts and the fuel beep."""

import pytest

from engineer import callouts
from helpers import current_plan


@pytest.mark.parametrize(
    "result, expected",
    [
        (None, ""),
        ({"label": "0", "margin_sec": 2.34}, "Clear by 2.3 seconds."),
        ({"label": "0", "margin_sec": None}, "Clear."),
        ({"label": "1", "margin_sec": None}, "Lose one place."),
        ({"label": "2+", "margin_sec": None}, "Lose two plus."),
        ({"label": "?", "margin_sec": None}, ""),
    ],
)
def test_places_phrase(result, expected):
    assert callouts.places_phrase(result) == expected


def test_lap_messages():
    assert callouts.lap_message(current_plan(1)) == ("Box, box. Box this lap, hards.",)
    assert callouts.lap_message(current_plan(2, "medium"), "Lose one place.") == (
        "Box next lap, mediums.",
        "Lose one place.",
    )
    assert callouts.lap_message(current_plan(2, "medium")) == ("Box next lap, mediums.",)
    assert callouts.lap_message(current_plan(7, out_lap=True)) == ("7 laps, box lap 13.",)
    assert callouts.lap_message(current_plan(None, laps_left=6, out_lap=True)) == ("6 laps, to the flag.",)
    assert callouts.lap_message(current_plan(5)) == ()
    assert callouts.lap_message(None) == ()


def test_callout_fires_once_at_two_thirds():
    tracker, plan = callouts.CalloutTracker(), current_plan(1)
    assert tracker.update(7, 10.0, 45.0, plan) == ()
    assert tracker.update(7, 30.5, 45.0, plan) == ("Box, box. Box this lap, hards.",)
    assert tracker.update(7, 40.0, 45.0, plan) == ()
    assert tracker.update(8, 5.0, 45.0, plan) == ()
    assert tracker.update(8, 31.0, 45.0, plan)


def test_stale_timer_on_new_lap_does_not_fire():
    tracker, plan = callouts.CalloutTracker(), current_plan(1)
    assert tracker.update(8, 46.0, 45.0, plan) == ()
    assert tracker.update(8, 0.5, 45.0, plan) == ()
    assert tracker.update(8, 31.0, 45.0, plan)


def test_fuel_beep_once_per_stop():
    beep = callouts.FuelBeep()
    assert not beep.update(40, 63)
    assert beep.update(56, 63)
    assert not beep.update(60, 63)
    beep.reset()
    assert beep.update(70, 63)


def test_fuel_beep_needs_target():
    assert not callouts.FuelBeep().update(50, None)
