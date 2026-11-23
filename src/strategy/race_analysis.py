"""race_analysis.py -- stint, fuel and compound-rule helpers over lap summaries."""

from collections.abc import Sequence
from typing import Literal

from strategy.race_types import Lap, StintSummary


def pit_kind(lap: Lap) -> Literal["in", "out"] | None:
    """'in' for the lap you stop at the end of, 'out' for the lap leaving the pits, None for any other lap."""
    if lap.get("is_pit_out_lap"):
        return "out"
    return "in" if lap.get("is_pitstop_lap") else None


def is_pit_in(lap: Lap) -> bool:
    """The lap you stop at the end of. It still belongs to the old tyre set."""
    return pit_kind(lap) == "in"


def compute_stints(laps: Sequence[Lap]) -> list[StintSummary]:
    """Splits laps into stints wherever two consecutive laps are both pitstop-flagged
    (in-lap of the old stint + out-lap of the new one)."""
    stints: list[StintSummary] = []
    current: list[Lap] = []
    prev: Lap | None = None

    for lap in laps:
        new_stint = prev is None or (lap.get("is_pitstop_lap") and prev.get("is_pitstop_lap"))
        if new_stint and current:
            stints.append(_summarize_stint(current))
            current = []
        current.append(lap)
        prev = lap

    if current:
        stints.append(_summarize_stint(current))

    return stints


def _summarize_stint(laps: list[Lap]) -> StintSummary:
    """One stint's aggregated stats from its laps."""
    clean_times = [
        t
        for lap in laps
        if not lap.get("incomplete") and pit_kind(lap) is None and (t := lap.get("laptime_sec")) is not None
    ]
    avg_laptime_sec = sum(clean_times) / len(clean_times) if clean_times else None
    best_laptime_sec = min(clean_times, default=None)

    tyre_wear = sum(v for lap in laps if (v := lap.get("tyre_used")) is not None and v >= 0)
    fuel_used = sum(v for lap in laps if (v := lap.get("fuel_used")) is not None)

    pit_loss_sec = next(
        (lap["pitstop_time_lost_sec"] for lap in laps if lap.get("pitstop_time_lost_sec") is not None),
        None,
    )

    return {
        "compound": laps[0].get("compound"),
        "lap_numbers": [lap.get("lap") for lap in laps],
        "laps_completed": len(laps),
        "avg_laptime_sec": avg_laptime_sec,
        "best_laptime_sec": best_laptime_sec,
        "tyre_wear": tyre_wear,
        "fuel_used": fuel_used,
        "pit_loss_sec": pit_loss_sec,
    }


def overall_fuel_per_lap(laps: Sequence[Lap]) -> float | None:
    """Avg fuel used per lap, all compounds pooled, clean laps only. None if none."""
    used = [
        v
        for lap in laps
        if not lap.get("incomplete") and pit_kind(lap) is None and (v := lap.get("fuel_used")) is not None
    ]
    return sum(used) / len(used) if used else None


def compound_rule_met(laps: Sequence[Lap]) -> bool:
    """True once the race has had any change of compound (wets count)."""
    return len({lap.get("compound") for lap in laps if lap.get("compound")}) > 1


def format_seconds(sec: float | None) -> str:
    """83.456 -> '01:23.456'; None -> '-'."""
    if sec is None:
        return "-"
    minutes = int(sec // 60)
    remainder = sec - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"
