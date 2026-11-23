"""
race_state.py -- one compact, JSON-ready snapshot of everything the app knows about the race,
for the AI engineer to answer from. Pure function: app state in, plain dict out.
Times are seconds. closing_s_per_lap > 0 means that gap is shrinking.
"""

from strategy import strategy_planner
from strategy.race_analysis import is_pit_in, pit_kind


def lap_type(lap):
    if lap.get("lap") == 1:
        return "lap 1"
    pit = pit_kind(lap)
    return f"pit {pit}" if pit else "clean"


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def to_int(value):
    """int(value), or None if it isn't a whole number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stint_planner(planner, top_n=3):
    """plan_stints tool for the AI: best plans for N laps on fresh slicks, from the real planner."""

    def plan_stints(laps):
        if planner is None:
            return "error: no tyre model for this track yet"
        if not isinstance(laps, int) or not 1 <= laps <= 60:
            return "error: laps must be a whole number from 1 to 60"
        plans = planner.plan(laps, top_n=top_n)
        if not plans:
            return "error: no legal plan"
        best = plans[0]["total_time_sec"]
        return "\n".join(
            f"{strategy_planner.format_plan(p)}: {p['total_time_sec']:.1f} s"
            + ("" if i == 0 else f" (+{p['total_time_sec'] - best:.1f} s)")
            for i, p in enumerate(plans)
        )

    return plan_stints


def tyre_life(laps, compound, tyre_pct, laps_remaining, model):
    """How long the tyres on the car last at the wear measured this stint (any compound, wets too).
    Falls back to earlier laps on the same compound, then the tyre model."""
    start = max((i + 1 for i, lap in enumerate(laps) if is_pit_in(lap)), default=0)

    def mean_wear(pool):
        used = [
            lap["tyre_used"]
            for lap in pool
            if lap.get("tyre_used") and (compound is None or lap.get("compound") == compound) and not is_pit_in(lap)
        ]
        return sum(used) / len(used) if used else None

    model_wear = model.wear_per_lap.get(compound) if model is not None and compound else None
    source, wear = next(
        ((name, w) for name, pool in (("this stint", laps[start:]), ("earlier laps", laps)) if (w := mean_wear(pool))),
        ("tyre model", model_wear),
    )
    if not wear or tyre_pct is None:
        return None
    left = max(0, int((tyre_pct - strategy_planner.MIN_END_PCT) // wear))
    return {
        "compound": compound,
        "tyre_pct_start_of_lap": _r(tyre_pct, 0),
        "wear_pct_per_lap": _r(wear, 1),
        "wear_measured_from": source,
        "laps_left_incl_this_one": left,
        "lasts_to_finish": None if laps_remaining is None else left >= laps_remaining,
    }


def _neighbour(position, gaps, pace, sign):
    """Car directly ahead (sign -1) or behind (+1): its gap and how fast the gap is shrinking."""
    if position is None:
        return None
    pos = position + sign
    gap = gaps.get(pos)
    loss = pace.get(pos)
    closing = None if loss is None else (loss if sign < 0 else -loss)
    return {
        "position": pos,
        "gap_s": _r(abs(gap)) if gap is not None else None,
        "closing_s_per_lap": _r(closing),
        "laps_until_gap_closes": _r(abs(gap) / closing, 1) if gap is not None and closing and closing > 0 else None,
    }


def lap_summary(laps):
    """Last/best/average lap and current stint, so the AI needn't do arithmetic over lap_history."""
    timed = [lap for lap in laps if lap.get("laptime_sec") is not None]
    if not timed:
        return None
    clean = [lap["laptime_sec"] for lap in timed if lap_type(lap) == "clean"]
    best = min(timed, key=lambda lap: lap["laptime_sec"])
    stint = []
    for lap in reversed(laps):
        stint.append(lap)
        if pit_kind(lap) == "out":
            break
    stint_clean = [
        lap["laptime_sec"] for lap in stint if lap.get("laptime_sec") is not None and lap_type(lap) == "clean"
    ]
    last = timed[-1]["laptime_sec"]
    return {
        "last_lap_s": _r(last, 3),
        "best_lap_s": _r(best["laptime_sec"], 3),
        "best_lap_number": best.get("lap"),
        "last_minus_best_s": _r(last - best["laptime_sec"], 3),
        "last_clean_lap_s": _r(clean[-1], 3) if clean else None,
        "average_clean_lap_s": _r(sum(clean) / len(clean), 3) if clean else None,
        "total_race_time_s": _r(sum(lap["laptime_sec"] for lap in timed), 1),
        "current_stint": {
            "compound": stint[0].get("compound"),
            "laps": len(stint),
            "average_clean_lap_s": _r(sum(stint_clean) / len(stint_clean), 3) if stint_clean else None,
        },
        "note": "clean laps exclude lap 1 and pit in/out laps",
    }


# Sections left out of the snapshot when there is nothing to put in them.
OPTIONAL_SECTIONS = (
    "race_order",
    "wet_window",
    "slicks_if_box",
    "fuel",
    "prerace_plan",
    "tyre_model",
    "tyre_model_sliding_s",
)


def build_snapshot(
    track,
    race_length,
    laps,
    fields,
    leaderboard,
    view,
    planner,
    prerace_best,
    fuel_per_lap,
    tyre_pct,
    track_condition=None,
):
    """Assembles the snapshot; each section is built by its own function below.
    planner: strategy_planner.Planner, or None without a tyre model."""
    model = planner.model if planner else None
    laps_done = laps[-1]["lap"] if laps else 0
    laps_remaining = None if race_length is None else race_length - laps_done
    view = view or {}
    now = _now(fields or {}, laps, tyre_pct, track_condition)
    snap = {
        "track": track or None,
        "race_length_laps": race_length,
        "laps_completed": laps_done,
        "laps_remaining": laps_remaining,
        "now": now,
        "lap_history": [_history_row(lap) for lap in laps],
        "laps_summary": _with_projected_finish(lap_summary(laps), laps_remaining),
        "pit_loss_s": _r(planner.pit_loss_sec, 1) if planner else None,
        "tyre_life": tyre_life(laps, now["compound"], tyre_pct, laps_remaining, model),
        "race_order": _race_order(leaderboard),
        "current_plan": _current_plan(view.get("current_plan")),
        "wet_window": _wet_window(view.get("wet_window")),
        "slicks_if_box": dict(view.get("slick_options") or {}) or None,
        "fuel": _fuel(fuel_per_lap, laps_remaining),
        "prerace_plan": _prerace_plan(prerace_best, planner, laps, laps_done),
        "tyre_model": _tyre_model(model),
        "tyre_model_sliding_s": _sliding_penalties(model),
    }
    return {k: v for k, v in snap.items() if v is not None or k not in OPTIONAL_SECTIONS}


def _now(fields, laps, tyre_pct, track_condition):
    return {
        "compound": fields.get("tyre_compound") or (laps[-1]["compound"] if laps else None),
        "tyre_remaining_pct_start_of_lap": _r(tyre_pct, 0),
        "tyre_remaining_pct_hud": to_int(fields.get("tyre")),
        "fuel_hud": to_int(fields.get("fuel")),
        "current_lap_timer": fields.get("laptime"),
        "track_condition": track_condition,
    }


def _history_row(lap):
    return {
        "lap": lap.get("lap"),
        "time_s": _r(lap.get("laptime_sec"), 3),
        "compound": lap.get("compound"),
        "tyre_used_pct": lap.get("tyre_used"),
        "fuel_used": lap.get("fuel_used"),
        "type": lap_type(lap),
        "condition": lap.get("condition"),
        "excluded": bool(lap.get("excluded")),
        "position": lap.get("position"),
    }


def _with_projected_finish(summary, laps_remaining):
    """Adds the finish time at race-average and stint-average pace."""
    if summary and laps_remaining is not None:
        paces = {
            "at_race_average_s": summary["average_clean_lap_s"],
            "at_stint_average_s": summary["current_stint"]["average_clean_lap_s"],
        }
        summary["projected_finish_s"] = {
            k: _r(summary["total_race_time_s"] + pace * laps_remaining, 1)
            for k, pace in paces.items()
            if pace is not None
        }
    return summary


def _race_order(leaderboard):
    if not leaderboard:
        return None
    smoothed = leaderboard.get("smoothed") or {}
    position = smoothed.get("position") or leaderboard.get("position")
    gaps, pace = smoothed.get("gaps") or {}, leaderboard.get("pace") or {}
    return {
        "position": position,
        "total_cars": leaderboard.get("total_cars"),
        "gap_to_leader_s": _r(smoothed.get("gap_to_leader_sec")),
        "car_ahead": _neighbour(position, gaps, pace, -1) if position and position > 1 else None,
        "car_behind": _neighbour(position, gaps, pace, +1),
    }


def _current_plan(plan):
    if not plan:
        return None
    best = plan["best"]
    return {
        "box_in_laps": best["pit_in"],
        "stints_after_stop": best["stints"],
        "box_on_lap": None if best["pit_in"] is None else plan["laps_done"] + best["pit_in"],
    }


def _wet_window(window):
    if not window:
        return None
    return {
        "box_in_laps": window["pit_in"],
        "box_on_lap": window["box_lap"],
        "note": "conservative stop after 5 laps on wets; rain timing is unknown",
    }


def _fuel(fuel_per_lap, laps_remaining):
    if fuel_per_lap is None:
        return None
    return {
        "per_lap": _r(fuel_per_lap),
        "needed_to_finish": None if laps_remaining is None else _r(fuel_per_lap * laps_remaining, 1),
    }


def _prerace_plan(prerace_best, planner, laps, laps_done):
    """The pre-race plan's predicted times against what has been driven so far."""
    if not prerace_best or planner is None:
        return None
    predicted = planner.lap_times(prerace_best)
    done = [lap.get("laptime_sec") for lap in laps]
    actual = sum(done) if done and None not in done else None
    expected = sum(predicted[:laps_done])
    delta = None if actual is None else actual - expected
    return {
        "stints": prerace_best["stints"],
        "predicted_total_s": _r(sum(predicted), 1),
        "predicted_time_for_completed_laps_s": _r(expected, 1),
        "actual_time_for_completed_laps_s": _r(actual, 1),
        "driver_vs_plan": None
        if delta is None
        else f"{'behind' if delta > 0 else 'ahead of'} the plan by {abs(delta):.1f} s",
        "note": "lap 1 includes the standing start, which the prediction does not",
    }


def _tyre_model(model):
    if model is None:
        return None
    return {
        c: {
            "base_lap_s": _r(model.base_pace[c]),
            "wear_pct_per_lap": _r(model.wear_per_lap[c], 1),
            "max_stint_laps": strategy_planner.max_stint_laps(model, c),
        }
        for c in model.compounds
    }


def _sliding_penalties(model):
    if model is None:
        return None
    return {f"{p}%": _r(model.penalty(p)) for p in (45, 40, 35, 30, 20)}
