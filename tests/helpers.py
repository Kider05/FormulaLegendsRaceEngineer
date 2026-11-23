"""Shared test data and builders."""

from strategy.live_strategy import GapTrend
from strategy.strategy_planner import Planner, format_plan
from strategy.tyre_model import (
    PRIOR_PENALTY_SLOPES,
    TyreModel,
)

LAP = 46.0


def snapshot(position, gaps, pitting=()):
    """Leaderboard snapshot shaped like hud_reader.read_leaderboard() output."""
    rows = [{"pos": position, "gap_sec": None, "is_player": True, "pitting": False}]
    rows += [{"pos": p, "gap_sec": g, "is_player": False, "pitting": p in pitting} for p, g in gaps.items()]
    return {"position": position, "rows": rows}


def smoothed(position, gaps):
    return {"position": position, "gaps": gaps}


def feed_trend(gap_at, seconds, position=1, chaser=2):
    trend = GapTrend()
    out = {}
    t = 0.0
    while t <= seconds:
        out = trend.update(smoothed(position, {chaser: gap_at(t)}), now=t, lap_time_sec=LAP)
        t += 0.1
    return out


def dry_laps(n, compound="soft", wear=10, laptime=46.0):
    return [
        {
            "lap": i + 1,
            "compound": compound,
            "laptime_sec": laptime,
            "tyre_used": wear,
            "fuel_used": 5,
            "is_pitstop_lap": False,
            "incomplete": False,
        }
        for i in range(n)
    ]


def model_lap(lap, compound, start_pct, laptime, wear=10.0, **flags):
    return {
        "lap": lap,
        "compound": compound,
        "start_pct": start_pct,
        "laptime_sec": laptime,
        "tyre_used": wear,
        "is_pitstop_lap": False,
        "is_pit_out_lap": False,
        "incomplete": False,
        **flags,
    }


def true_penalty(s):
    return 0.3 * max(0, 45 - s) - 0.25 * max(0, 30 - s)


def synthetic_laps(outlier_every=None):
    laps, n = [], 0
    for compound, base in (("soft", 45.5), ("medium", 46.4)):
        for start in range(100, 0, -5):
            n += 1
            t = base + true_penalty(start)
            if outlier_every and n % outlier_every == 0:
                t += 6.0
            laps.append(model_lap(n + 1, compound, float(start), t))
    return laps


SILVERSTONE = TyreModel(
    base_pace={"soft": 45.87, "medium": 46.44, "hard": 46.98},
    penalty_slopes=PRIOR_PENALTY_SLOPES,
    wear_per_lap={"soft": 15.8, "medium": 11.9, "hard": 9.6},
    lap_counts={},
)
PLANNER = Planner(SILVERSTONE, 16.7)


def stint_set(p):
    return sorted(format_plan(p).split(" -> "))


def jittered_race(laps, laptime, wear):
    return [model_lap(i, "medium", None, laptime + (0.3 if i % 2 else -0.3), wear=wear) for i in range(1, laps + 1)]


def current_plan(pit_in, compound="hard", laps_done=6, laps_left=14, out_lap=False):
    stints = [{"compound": compound, "laps": 7}] if pit_in is not None else []
    return {
        "best": {"pit_in": pit_in, "stints": stints},
        "laps_done": laps_done,
        "laps_left": laps_left,
        "out_lap": out_lap,
    }
