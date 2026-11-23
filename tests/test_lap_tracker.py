"""LapTracker driven by simulated HUD reads: a poll every 0.25s, the live timer counting up, LAST LAP
showing the true time (0.05s over the timer's last value) one poll after the line, pit screens hiding the HUD."""

import pytest

from telemetry.lap_summary import PITSTOP_FUEL_GRACE_SEC, LapTracker, format_summary

POLL = 0.25
PIT_SCREEN_SEC = 3.0
BLANK_LAST_LAP = "--:--.---"
UNREADABLE = ("?", [0])


def fmt(sec):
    m, ms = divmod(round(sec * 1000), 60000)
    return f"{m:02d}:{ms // 1000:02d}.{ms % 1000:03d}"


class Race:
    def __init__(self, total_laps=5, compound="medium", fuel=60):
        self.tracker = LapTracker()
        self.lap, self.total = 0, total_laps
        self.tyre, self.fuel, self.compound = 100, fuel, compound
        self.last_lap, self._next_last_lap = BLANK_LAST_LAP, BLANK_LAST_LAP
        self.ticks, self.grace = 0, 0.0
        self.summaries, self.ignored = [], []

    def poll(self, laptime, /, hidden=False, **overrides):
        reads = {
            "lap": (f"{self.lap}|{self.total}", []),
            "laptime": (fmt(laptime), []),
            "tyre": (str(self.tyre), []),
            "fuel": (str(self.fuel), []),
            "last_lap_time": (self.last_lap, []),
            "tyre_compound": (self.compound, []),
        }
        if hidden:
            reads = dict.fromkeys(reads, UNREADABLE)
        reads.update(overrides)
        summary = self.tracker.record(reads, now=1000 + self.ticks * POLL, near_pitstop=self.grace > 0)
        self.ignored += self.tracker.last_ignored
        self.ticks += 1
        self.grace = max(0.0, self.grace - POLL)
        if summary:
            self.summaries.append(summary)

    def drive(
        self,
        seconds,
        tyre_used=6,
        fuel_used=5,
        *,
        pit=None,
        blank_last_lap=False,
        last_lap_after=1,
        timer_hidden=0.0,
        glitches=None,
        position=None,
    ):
        """pit: {"at": laptime the pit screen shows, "compound": fitted, "fuel": after refuel}.
        last_lap_after: polls into this lap before LAST LAP shows the previous lap."""
        self.lap += 1
        start_tyre, start_fuel = self.tyre, self.fuel
        wear_until = pit["at"] if pit else seconds
        pitted = False
        steps = round(seconds / POLL)
        for i in range(steps + 1):
            laptime = i * POLL
            if i == last_lap_after:
                self.last_lap = self._next_last_lap
            if i == steps // 2 and position is not None:
                self.tracker.record_leaderboard({"position": position})
            if pit and pit["at"] <= laptime < pit["at"] + PIT_SCREEN_SEC:
                self.grace = PITSTOP_FUEL_GRACE_SEC
                self.poll(laptime, hidden=True)
                continue
            if pit and laptime >= pit["at"] and not pitted:
                pitted = True
                self.tyre, self.fuel, self.compound = 100, pit["fuel"], pit["compound"]
            elif not pitted:
                done = min(1.0, laptime / wear_until)
                self.tyre = start_tyre - round(tyre_used * done)
                self.fuel = start_fuel - round(fuel_used * done)
            overrides = dict((glitches or {}).get(i, {}))
            if laptime < timer_hidden:
                overrides["laptime"] = UNREADABLE
            self.poll(laptime, **overrides)
        self._next_last_lap = BLANK_LAST_LAP if blank_last_lap else fmt(seconds + 0.05)

    def finish(self):
        """Final lap ends with the HUD gone, so the engine force-finalizes it."""
        self.summaries.append(self.tracker.finalize_current_lap(incomplete=False))


def pick(summary, *keys):
    return {k: summary[k] for k in keys}


PIT_KEYS = (
    "lap",
    "laptime_sec",
    "compound",
    "is_pitstop_lap",
    "is_pit_out_lap",
    "is_double_stop",
    "pitstop_time_lost_sec",
    "tyre_used",
    "tyre_gained",
    "fuel_used",
    "fuel_gained",
)


def test_clean_race():
    race = Race(total_laps=3)
    race.tracker.record_leaderboard({"position": 6})
    race.drive(50.0, tyre_used=6, position=5)
    race.drive(47.0, tyre_used=7, position=4)
    race.drive(48.0, tyre_used=8)
    assert race.tracker.is_final_lap_and_plausible()
    race.finish()

    assert race.summaries[0] == {
        "lap": 1,
        "laptime": "00:50.050",
        "laptime_sec": 50.05,
        "tyre_used": 6,
        "tyre_gained": 0,
        "fuel_used": 5,
        "fuel_gained": 0,
        "lap_delta_sec": None,
        "avg_laptime_sec": 50.05,
        "incomplete": False,
        "compound": "medium",
        "is_pitstop_lap": False,
        "is_pit_out_lap": False,
        "is_double_stop": False,
        "pitstop_time_lost_sec": None,
        "position": 5,
        "start_position": 6,
    }
    keys = ("lap", "laptime", "laptime_sec", "lap_delta_sec", "avg_laptime_sec", "tyre_used", "position")
    assert [pick(s, *keys) for s in race.summaries[1:]] == [
        {
            "lap": 2,
            "laptime": "00:47.050",
            "laptime_sec": 47.05,
            "lap_delta_sec": -3.0,
            "avg_laptime_sec": 47.05,
            "tyre_used": 7,
            "position": 4,
        },
        # last lap has no LAST LAP read, so it's the live timer's peak
        {
            "lap": 3,
            "laptime": "00:48.000",
            "laptime_sec": 48.0,
            "lap_delta_sec": 0.95,
            "avg_laptime_sec": 47.525,
            "tyre_used": 8,
            "position": None,
        },
    ]
    assert "start_position" not in race.summaries[1]


def test_final_lap_check_needs_final_lap_and_plausible_time():
    race = Race(total_laps=2)
    race.drive(50.0)
    assert not race.tracker.is_final_lap_and_plausible()
    race.drive(48.0)
    assert race.tracker.is_final_lap_and_plausible()
    slow = Race(total_laps=2)
    slow.drive(50.0)
    slow.drive(10.0)
    assert not slow.tracker.is_final_lap_and_plausible()


def test_blank_last_lap_falls_back_to_live_timer():
    race = Race()
    race.drive(50.0)
    race.drive(47.0, blank_last_lap=True)
    race.drive(48.0)
    assert [(s["lap"], s["laptime"], s["laptime_sec"]) for s in race.summaries] == [
        (1, "00:50.050", 50.05),
        (2, "00:47.000", 47.0),
    ]
    assert any("NULLED" in msg for msg in race.ignored)


def test_late_last_lap_is_not_reused_for_the_next_lap():
    race = Race()
    race.drive(50.0)
    race.drive(47.0, last_lap_after=4)
    race.drive(48.0)
    assert [(s["lap"], s["laptime_sec"]) for s in race.summaries] == [(1, 50.0), (2, 47.05)]


def test_stopping_just_after_the_line_finalizes_the_waiting_lap():
    race = Race()
    race.drive(50.0)
    race.lap += 1
    race.poll(0.0)
    assert race.summaries == []
    assert pick(race.tracker.finalize_current_lap(), "lap", "laptime_sec", "incomplete") == {
        "lap": 1,
        "laptime_sec": 50.0,
        "incomplete": False,
    }


def test_lap_change_from_lap_counter_when_timer_unreadable():
    race = Race()
    race.drive(50.0, glitches={40: {"lap": ("3|5", [])}})
    race.drive(47.0, timer_hidden=3.0)
    race.drive(48.0)
    assert [(s["lap"], s["laptime_sec"]) for s in race.summaries] == [(1, 50.05), (2, 47.05)]
    assert any("not yet confirmed" in msg for msg in race.ignored)


def test_tyre_misreads_are_ignored():
    race = Race()
    race.drive(50.0, tyre_used=6)
    race.drive(47.0, tyre_used=7, glitches={50: {"tyre": ("8", [])}, 90: {"tyre": ("99", [])}})
    race.drive(48.0)
    assert [s["tyre_used"] for s in race.summaries] == [6, 7]
    assert any("tyre: ignored 8" in msg for msg in race.ignored)
    assert any("tyre: ignored 99" in msg for msg in race.ignored)


@pytest.mark.parametrize("new_compound", ["soft", "medium"])
def test_pit_stop(new_compound):
    race = Race()
    race.drive(50.0, tyre_used=6)
    race.drive(47.0, tyre_used=7)
    race.drive(60.0, tyre_used=8, pit={"at": 55.0, "compound": new_compound, "fuel": 58})
    race.drive(55.0, tyre_used=6)
    race.drive(46.0, tyre_used=6)
    race.finish()
    in_lap, out_lap, after = (pick(s, *PIT_KEYS) for s in race.summaries[2:])
    assert in_lap == {
        "lap": 3,
        "laptime_sec": 60.05,
        "compound": "medium",
        "is_pitstop_lap": True,
        "is_pit_out_lap": False,
        "is_double_stop": False,
        "pitstop_time_lost_sec": None,
        "tyre_used": 8,
        "tyre_gained": 21,
        "fuel_used": 5,
        "fuel_gained": 13,
    }
    # loss = in-lap and out-lap time over the clean average before the stop (lap 2, 47.05)
    assert out_lap == {
        "lap": 4,
        "laptime_sec": 55.05,
        "compound": new_compound,
        "is_pitstop_lap": True,
        "is_pit_out_lap": True,
        "is_double_stop": False,
        "pitstop_time_lost_sec": 21.0,
        "tyre_used": 6,
        "tyre_gained": 0,
        "fuel_used": 5,
        "fuel_gained": 0,
    }
    assert not after["is_pitstop_lap"] and after["pitstop_time_lost_sec"] is None
    assert race.summaries[-1]["avg_laptime_sec"] == round((47.05 + 46.0) / 2, 3)


def test_double_stop_adds_both_laps_to_the_loss():
    race = Race()
    race.drive(50.0)
    race.drive(47.0)
    race.drive(60.0, pit={"at": 55.0, "compound": "soft", "fuel": 58})
    race.drive(62.0, pit={"at": 57.0, "compound": "hard", "fuel": 60})
    race.drive(55.0)
    race.drive(46.0)
    keys = ("lap", "compound", "is_pitstop_lap", "is_pit_out_lap", "is_double_stop", "pitstop_time_lost_sec")
    assert [pick(s, *keys) for s in race.summaries[2:5]] == [
        {
            "lap": 3,
            "compound": "medium",
            "is_pitstop_lap": True,
            "is_pit_out_lap": False,
            "is_double_stop": False,
            "pitstop_time_lost_sec": None,
        },
        {
            "lap": 4,
            "compound": "soft",
            "is_pitstop_lap": True,
            "is_pit_out_lap": True,
            "is_double_stop": False,
            "pitstop_time_lost_sec": None,
        },
        {
            "lap": 5,
            "compound": "hard",
            "is_pitstop_lap": True,
            "is_pit_out_lap": True,
            "is_double_stop": True,
            "pitstop_time_lost_sec": 36.0,
        },
    ]


def test_stopping_mid_lap_gives_an_incomplete_lap():
    race = Race()
    race.drive(50.0, tyre_used=6)
    race.drive(20.0, tyre_used=3)
    summary = race.tracker.finalize_current_lap()
    assert pick(summary, "lap", "laptime", "incomplete", "tyre_used", "is_pitstop_lap", "lap_delta_sec") == {
        "lap": 2,
        "laptime": "00:20.000",
        "incomplete": True,
        "tyre_used": 3,
        "is_pitstop_lap": False,
        "lap_delta_sec": None,
    }
    assert race.tracker.average_completed_laptime_sec == 50.05


def test_format_summary():
    race = Race()
    race.drive(50.0, position=3)
    race.drive(47.0)
    assert format_summary(race.summaries[0]) == "Lap 1: 00:50.050 | avg 50.050s | tyre -6 | fuel -5 | P3"
