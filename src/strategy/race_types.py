"""race_types.py -- the shapes of the dicts that pass between modules.

Laps and plans stay plain dicts because they cross boundaries as they are: SQLite rows, the GUI
queue, JSON for the AI. These TypedDicts describe them for readers and the type checker; at run
time they are ordinary dicts.
"""

from typing import TypedDict

Compound = str  # "soft" | "medium" | "hard" | "wet"
Condition = str  # "raining" | "wet" | "drying" | "dry"


class Lap(TypedDict, total=False):
    """One lap. LapTracker produces the summary keys; the app and database add the review keys;
    tyre_model.annotate_start_pct adds the tyre keys while fitting."""

    # summary, from LapTracker
    lap: int
    laptime: str | None
    laptime_sec: float | None
    tyre_used: float | None
    tyre_gained: float
    fuel_used: float | None
    fuel_gained: float
    lap_delta_sec: float | None
    avg_laptime_sec: float | None
    incomplete: bool
    compound: Compound | None
    is_pitstop_lap: bool
    is_pit_out_lap: bool
    is_double_stop: bool
    pitstop_time_lost_sec: float | None
    position: int | None
    start_position: int | None  # lap 1 only
    # review, from the app and database
    condition: Condition | None
    excluded: bool
    db_id: int
    # tyre, from annotate_start_pct
    start_pct: float
    mid_pct: float
    near_wet: bool
    race: int


class Stint(TypedDict):
    compound: Compound
    laps: int


class Plan(TypedDict):
    """A plan on fresh tyres from Planner.plan; stints in driving order."""

    total_time_sec: float
    stops: int
    stints: list[Stint]


class LiveOption(TypedDict):
    """A plan from the tyre on the car (Planner.from_current): stay out pit_in laps, then stints.
    pit_in None means run to the flag."""

    pit_in: int | None
    total_time_sec: float
    stints: list[Stint]


class StintSummary(TypedDict):
    """A driven stint, from race_analysis.compute_stints."""

    compound: Compound | None
    lap_numbers: list[int | None]
    laps_completed: int
    avg_laptime_sec: float | None
    best_laptime_sec: float | None
    tyre_wear: float
    fuel_used: float
    pit_loss_sec: float | None
