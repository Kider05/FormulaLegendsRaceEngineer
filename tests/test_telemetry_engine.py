"""TelemetrySession.step() driven by fake frames: the HUD readers are swapped for functions that
return what each frame dict says, so no screen, game or sleeping is involved."""

import queue

import pytest

from telemetry import telemetry_engine as engine
from test_lap_tracker import UNREADABLE, fmt

LEADERBOARD = {
    "position": 3,
    "total_cars": 14,
    "rows": [],
    "gap_ahead_sec": 1.5,
    "gap_behind_sec": 2.0,
    "gap_to_leader_sec": 6.0,
}
FIELDS = ("lap", "laptime", "tyre", "fuel", "last_lap_time", "hp")


class FakeUIStateTracker:
    def update(self, frame, _configs):
        return frame["ui"], {}


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(engine, "read_all_fields", lambda frame: dict(frame["reads"]))
    monkeypatch.setattr(engine, "read_tyre_compound", lambda frame: (frame["compound"], 0.0))
    monkeypatch.setattr(engine, "read_pit_fuel", lambda frame: 62)
    monkeypatch.setattr(engine, "read_leaderboard", lambda frame: dict(LEADERBOARD))
    monkeypatch.setattr(engine, "ui_state_configs", dict)
    monkeypatch.setattr(engine, "NamedStateTracker", FakeUIStateTracker)
    return engine.TelemetrySession(queue.Queue())


def racing(lap, laptime, total=2, last_lap="--:--.---"):
    reads = {
        "lap": (f"{lap}|{total}", []),
        "laptime": (fmt(laptime), []),
        "tyre": ("90", []),
        "fuel": ("50", []),
        "last_lap_time": (last_lap, []),
        "hp": ("100", []),
    }
    return {"reads": reads, "compound": "medium", "ui": None}


def screen(ui=None):
    """HUD unreadable: a menu screen when ui is set, else something unknown (e.g. results)."""
    return {"reads": dict.fromkeys(FIELDS, UNREADABLE), "compound": None, "ui": ui}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def tick(self, seconds=0.1):
        self.now += seconds
        return self.now


def drive_lap(session, clock, lap, seconds, last_lap="--:--.---"):
    """Polls every 0.5s of laptime; returns the intervals step() asked for."""
    return [session.step(racing(lap, i * 0.5, last_lap=last_lap), clock.tick(0.5)) for i in range(int(seconds * 2) + 1)]


def updates(session, key):
    return [u[key] for u in session._queue.queue if key in u]


def test_race_publishes_laps_then_finishes_itself(session):
    clock = Clock()
    drive_lap(session, clock, 1, 30.0)
    intervals = drive_lap(session, clock, 2, 29.0, last_lap=fmt(30.05))
    assert set(intervals) == {engine.ACTIVE_POLL_INTERVAL_SEC, engine.HIGH_PRECISION_POLL_INTERVAL_SEC}
    assert intervals[-1] == engine.HIGH_PRECISION_POLL_INTERVAL_SEC

    results = []
    while not results or results[-1] is not None:
        results.append(session.step(screen(), clock.tick(1.0)))
        assert len(results) < 10
    laps = [s for s in updates(session, "lap_summary") if s]
    assert [(s["lap"], s["laptime_sec"], s["position"]) for s in laps] == [(1, 30.05, 3), (2, 29.0, 3)]
    assert updates(session, "mode")[-1] == "race finished"

    session.close()
    assert session._queue.queue[-1] == {"stopped": True, "lap_summary": None}


def test_stopping_mid_lap_hands_back_the_unfinished_lap(session):
    clock = Clock()
    drive_lap(session, clock, 1, 10.0)
    session.close()
    last = session._queue.queue[-1]
    assert last["stopped"] and last["lap_summary"]["incomplete"] and last["lap_summary"]["lap"] == 1


def test_pit_screen_publishes_fuel_then_the_stop_ending(session):
    clock = Clock()
    drive_lap(session, clock, 1, 5.0)
    assert session.step(screen("pitstop"), clock.tick()) == engine.ACTIVE_POLL_INTERVAL_SEC
    session.step(racing(1, 9.0), clock.tick())
    assert updates(session, "pit_fuel") == [62, None]
    assert updates(session, "pit_stop_ended") == [True]
    assert "idle (pitstop)" in updates(session, "mode")


def test_pause_screen_polls_slowly(session):
    clock = Clock()
    drive_lap(session, clock, 1, 5.0)
    assert session.step(screen("pause"), clock.tick()) == engine.IDLE_POLL_INTERVAL_SEC
    assert updates(session, "mode")[-1] == "idle (pause)"


def test_frozen_laptime_stops_counting_as_racing(session):
    clock = Clock()
    frozen = racing(1, 12.0)
    frozen["reads"] = {**dict.fromkeys(FIELDS, UNREADABLE), "laptime": frozen["reads"]["laptime"], "hp": ("100", [])}
    frozen["compound"] = None
    for _ in range(engine.STALE_LAPTIME_CYCLES + 6):
        session.step(frozen, clock.tick())
    modes = updates(session, "mode")
    assert modes[1] == "active" and modes[-1] == "idle"


def test_missing_game_is_reported_and_no_frame_returned(session, monkeypatch):
    monkeypatch.setattr(engine, "is_process_running", lambda exe: False)
    assert engine._grab_frame(session) is None
    assert "not found" in updates(session, "status")[0]
