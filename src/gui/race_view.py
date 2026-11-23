"""
race_view.py -- builds the text the GUI shows. Pure functions: data in, strings out, no Tkinter,
so every label can be tested without a window.
"""

from dataclasses import dataclass
from datetime import datetime

from engineer.callouts import FUEL_BEEP_LEAD
from strategy import live_strategy, race_analysis, strategy_planner, tyre_model

FUEL_BUFFER = 10
MIN_LIVE_FUEL_LAPS = 3
DEFAULT_STINT_LAPS = {"soft": 4, "medium": 5, "hard": 6, "wet": 5}
WET_EXTENDED_EXTRA_LAPS = 2


def parse_laps(text):
    """Race Laps entry -> int, or None if blank or not a whole number."""
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


def race_label(race):
    """e.g. 'Oct 14 06:16 PM  Spa (modern)  Marks Peerstallen  #11'."""
    when = datetime.fromisoformat(race["timestamp"]).astimezone().strftime("%b %d %I:%M %p")
    driver = f"  {race['driver']}" if race["driver"] else ""
    return f"{when}  {race['track']} ({race['variant']}){driver}  #{race['id']}"


def stint_row(number, stint):
    """One Analysis-tab table row for a compute_stints() stint."""
    lap_numbers = [n for n in stint["lap_numbers"] if n is not None]
    laps_label = (
        f"{stint['laps_completed']} (L{lap_numbers[0]}-L{lap_numbers[-1]})"
        if lap_numbers
        else str(stint["laps_completed"])
    )
    pit_loss = stint["pit_loss_sec"]
    return (
        number,
        (stint["compound"] or "-").capitalize(),
        laps_label,
        race_analysis.format_seconds(stint["avg_laptime_sec"]),
        race_analysis.format_seconds(stint["best_laptime_sec"]),
        f"{stint['tyre_wear']:.0f}",
        f"{stint['fuel_used']:.0f}",
        f"+{pit_loss:.1f}s" if pit_loss is not None else "-",
    )


def mark_condition(race_laps, condition, from_lap):
    """Stamp `condition` on every finished lap from from_lap on (laps still to come take it from the app)."""
    for lap in race_laps:
        if (lap.get("lap") or 0) >= from_lap:
            lap["condition"] = condition


REVIEW_COLUMNS = ("lap", "time", "compound", "tyre", "fuel", "pit", "pos", "condition", "excluded")


def lap_review_row(lap):
    """One Analysis-tab review row, in REVIEW_COLUMNS order."""
    pit = race_analysis.pit_kind(lap) or ""
    tyre, fuel = lap.get("tyre_used"), lap.get("fuel_used")
    return (
        lap.get("lap") or "-",
        race_analysis.format_seconds(lap.get("laptime_sec")),
        (lap.get("compound") or "-").capitalize(),
        f"-{tyre:.0f}" if tyre is not None else "-",
        f"-{fuel:.0f}" if fuel is not None else "-",
        pit,
        f"P{lap['position']}" if lap.get("position") else "-",
        lap.get("condition") or "",
        "yes" if lap.get("excluded") else "",
    )


def leaderboard_text(lb):
    fmt = lambda v: f"{v:.3f}s" if v is not None else "--"
    lines = [
        f"P{lb['position'] or '?'}/{lb['total_cars'] or '?'}  "
        f"ahead {fmt(lb['gap_ahead_sec'])}  behind {fmt(lb['gap_behind_sec'])}"
    ]
    for r in lb["rows"]:
        tag = "YOU" if r["is_player"] else ("PIT" if r["pitting"] else fmt(r["gap_sec"]))
        lines.append(f"  P{r['pos'] or '?'}: {tag}  {r['tyre'] or ''}")
    return "\n".join(lines)


def model_text(track, variant, model, pit_loss_sec, pit_loss_samples):
    if not track:
        return "Select or type a track to see its saved stats."
    if model is None:
        return f"{track} ({variant}): no data saved yet."
    lines = [
        f"{track} ({variant})",
        f"Pit loss {pit_loss_sec:.1f}s ({pit_loss_samples} stops)"
        if pit_loss_sec is not None
        else "No pit-stop data yet",
        "",
    ]
    for c in model.compounds:
        lines.append(f"{c:<7}{model.base_pace[c]:.2f}s  {model.wear_per_lap[c]:.0f}%/lap")
    lines.append("")
    lines.append("Sliding " + "  ".join(f"{p}% +{model.penalty(p):.1f}" for p in (40, 30)))
    return "\n".join(lines)


def compound_limits(model, compounds=("soft", "medium", "hard", "wet")):
    """{compound: 'Soft: max 5 laps (16%/lap)'} for the laps-per-tyre labels."""
    out = {}
    for c in compounds:
        if model is not None and c in model.compounds:
            out[c] = (
                f"{c.capitalize()}: max {strategy_planner.max_stint_laps(model, c)} laps"
                f" ({model.wear_per_lap[c]:.0f}%/lap)"
            )
        else:
            out[c] = f"{c.capitalize()}: --"
    return out


def prerace_text(track, race_laps_text, planner):
    """(plan text, best plan or None). planner: strategy_planner.Planner, or None without a model."""
    total_laps = parse_laps(race_laps_text)
    if not track or not race_laps_text.strip():
        return "Enter a track and race length to see a plan.", None
    if total_laps is None:
        return "Race Laps must be a whole number.", None
    if planner is None:
        return "No usable race history for this track yet. Save a few races first.", None
    plans = planner.plan(total_laps, ban_second=True)
    if not plans:
        return "No slick data for this track yet.", None

    lines = [] if planner.pit_loss_sec is not None else ["(no pit-loss data yet -- stops counted as free)"]
    best = plans[0]["total_time_sec"]
    for i, p in enumerate(plans, 1):
        delta = "" if i == 1 else f"  +{p['total_time_sec'] - best:.1f}s"
        lines.append(
            f"{i}. {strategy_planner.format_plan(p)}  ~{race_analysis.format_seconds(p['total_time_sec'])}{delta}"
        )
    return "\n".join(lines), plans[0]


def fuel_per_lap(race_laps, track_races):
    """Live fuel use once MIN_LIVE_FUEL_LAPS laps are in, else this track's history; None if unknown."""
    live = race_analysis.overall_fuel_per_lap(race_laps)
    if live is not None and len(race_laps) >= MIN_LIVE_FUEL_LAPS:
        return live
    return race_analysis.overall_fuel_per_lap([lap for race in track_races for lap in race])


def fuel_target(laps, per_lap):
    return None if per_lap is None else laps * per_lap + FUEL_BUFFER


def default_fuel_text(per_lap):
    """Fuel for each compound's default stint length, plus the extended wet stint."""
    if per_lap is None:
        return "-- (no fuel data yet)"
    lines = [f"{c[0].upper()}{n} -> {fuel_target(n, per_lap):.0f} fuel" for c, n in DEFAULT_STINT_LAPS.items()]
    wet_extended = DEFAULT_STINT_LAPS["wet"] + WET_EXTENDED_EXTRA_LAPS
    lines.append(f"W{wet_extended} -> {fuel_target(wet_extended, per_lap):.0f} fuel (extended)")
    return "\n".join(lines)


def next_fuel_text(next_stint, per_lap):
    """'Next: H8 -> 63 fuel (beep 56)' for the stint after the planned stop."""
    if next_stint is None or per_lap is None:
        return "Next: --"
    target = fuel_target(next_stint["laps"], per_lap)
    return (
        f"Next: {next_stint['compound'][0].upper()}{next_stint['laps']} -> {target:.0f} fuel "
        f"(beep {target - FUEL_BEEP_LEAD:.0f})"
    )


def _format_option(option):
    if option["pit_in"] is None:
        return "stay out to the flag"
    when = {1: "this lap", 2: "next lap"}.get(option["pit_in"], f"in {option['pit_in']} laps")
    return f"pit {when} -> {strategy_planner.format_plan(option)}"


def laps_on_set(race_laps):
    """Completed laps on the tyres now on the car (a pit-in lap belongs to the old set)."""
    n = 0
    for lap in reversed(race_laps):
        if race_analysis.is_pit_in(lap):
            break
        n += 1
    return n


def wet_window(total_laps, race_laps):
    """Conservative wet stop: box after DEFAULT_STINT_LAPS['wet'] laps on the set.
    {"pit_in": 1 = this lap, 2 = next lap..., or None if the set reaches the flag, "box_lap"}."""
    laps_done = race_laps[-1]["lap"] if race_laps else 0
    pit_in = max(1, DEFAULT_STINT_LAPS["wet"] - laps_on_set(race_laps))
    if laps_done + pit_in >= total_laps:
        return {"pit_in": None, "box_lap": None}
    return {"pit_in": pit_in, "box_lap": laps_done + pit_in}


def _wet_text(window):
    if window["pit_in"] is None:
        return "Wet: to the flag"
    when = "this lap" if window["pit_in"] == 1 else f"lap {window['box_lap']}"
    return f"Wet: possible pit window {when} (W{DEFAULT_STINT_LAPS['wet']})"


@dataclass(frozen=True)
class _Race:
    """Where the race stands, as the planner needs it."""

    total_laps: int
    laps: list
    planner: object  # strategy_planner.Planner
    current: str | None  # compound on the car
    rule_met: bool
    tyre_pct: float

    @property
    def laps_done(self):
        return self.laps[-1]["lap"] if self.laps else 0

    def pit_options(self):
        return self.planner.pit_options(self.total_laps - self.laps_done, self.current, self.rule_met)


def _fixed_pit_lines(race):
    if race.current is None:
        return ["(waiting for tyre compound read)"]
    lines = ["(no stay-out estimate on this tyre)"]
    for label, laps_left, plans in race.pit_options():
        if laps_left <= 0:
            lines.append(f"Pit {label}: no laps left to use it")
        elif not plans:
            lines.append(f"Pit {label} ({laps_left} left): no legal plan")
        else:
            lines.append(f"Pit {label} ({laps_left} left): {strategy_planner.format_plan(plans[0])}")
    return lines


EMPTY_VIEW = {
    "plan": "--",
    "warning": "",
    "stint": None,
    "in_pit_window": False,
    "current_plan": None,
    "next_stint": None,
    "pit_now_stint": None,
    "wet_window": None,
    "slick_options": None,
    "current": None,
    "pit_soon": None,
}


def live_view(race_laps_text, race_laps, current_compound, planner):
    """
    Everything the live part of the Race tab shows; keys as in EMPTY_VIEW.
    "stint" is None when it should keep its text. On wets, "wet_window" is wet_window().
    current_plan is the best plan from the tyre on the car, or None when there is no valid one
    (wets, unknown tyre, no race length or planner): {"best", "laps_done", "laps_left", "out_lap"}.
    planner: strategy_planner.Planner, or None without a model.
    next_stint is the stint after the planned stop; pit_now_stint the one after stopping this lap.
    """
    view = dict(EMPTY_VIEW)
    has_laps = bool(race_analysis.compute_stints(race_laps))
    if not has_laps:
        view["stint"] = "No laps yet."
    total_laps = parse_laps(race_laps_text)
    if total_laps is None:
        view["plan"] = "-- (enter Race Laps)"
        return view
    if planner is None:
        view["plan"] = "-- (no race history for this track yet)"
        return view

    current = current_compound or (race_laps[-1]["compound"] if race_laps else None)
    race = _Race(
        total_laps,
        race_laps,
        planner,
        current,
        rule_met=race_analysis.compound_rule_met(race_laps + [{"compound": current}]),
        tyre_pct=tyre_model.current_tyre_pct(race_laps),
    )
    view["current"] = current
    if current == "wet":
        view.update(_wet_view(race))
        return view
    options = planner.from_current(total_laps - race.laps_done, current, race.tyre_pct, race.rule_met, top_n=None)
    if not options:
        view["plan"] = "\n".join(_fixed_pit_lines(race))
        return view
    view.update(_planned_view(race, options))
    if has_laps:
        view["stint"] = _stint_text(race, _laps_left_text(options[0]["pit_in"]))
    return view


def _stint_text(race, left):
    return (
        f"{(race.current or '-').capitalize()}  {race.tyre_pct:.0f}%\nStint lap {laps_on_set(race.laps) + 1}  ·  {left}"
    )


def _laps_left_text(pit_in):
    return "to the flag" if pit_in is None else f"{pit_in} left incl. this"


def _wet_view(race):
    """On wets there is no stay-out estimate: show the conservative wet window and the slicks to box for."""
    window = wet_window(race.total_laps, race.laps)
    slick = {label: plans[0] if plans else None for label, laps_left, plans in race.pit_options() if laps_left > 0}
    this_lap = slick.get("this lap")
    return {
        "wet_window": window,
        "slick_options": {label: strategy_planner.format_plan(p) if p else None for label, p in slick.items()},
        "pit_soon": {"pit_in": 1, "stints": this_lap["stints"], "note": "slicks"} if this_lap else None,
        "stint": _stint_text(race, "to the flag" if window["pit_in"] is None else f"window lap {window['box_lap']}"),
        "plan": "\n".join([_wet_text(window)] + _fixed_pit_lines(race)[1:]),
    }


def _planned_view(race, options):
    """View entries when the planner has plans from the tyre on the car; options[0] is the best."""
    best = options[0]
    by_pit_in = {o["pit_in"]: o for o in reversed(options)}
    pit_now = by_pit_in.get(1)
    last = race.laps[-1] if race.laps else {}
    return {
        "current_plan": {
            "best": best,
            "laps_done": race.laps_done,
            "laps_left": race.total_laps - race.laps_done,
            "out_lap": race_analysis.is_pit_in(last),
        },
        "next_stint": best["stints"][0] if best["stints"] else None,
        "pit_now_stint": pit_now["stints"][0] if pit_now and pit_now["stints"] else None,
        "in_pit_window": best["pit_in"] is not None and best["pit_in"] <= 2,
        "warning": {1: "PIT THIS LAP", 2: "PIT NEXT LAP"}.get(best["pit_in"], ""),
        "pit_soon": _pit_soon(options, best),
        "plan": "\n".join(_plan_lines(by_pit_in, best)),
    }


def _pit_soon(options, best):
    """The fastest alternative that boxes this lap or next, with its cost over the best plan."""
    soon = min(
        (o for o in options if o["pit_in"] in (1, 2) and o is not best), key=lambda o: o["total_time_sec"], default=None
    )
    if soon is None:
        return None
    return {
        "pit_in": soon["pit_in"],
        "stints": soon["stints"],
        "note": f"+{soon['total_time_sec'] - best['total_time_sec']:.1f}s",
    }


def _plan_lines(by_pit_in, best):
    lines = [f"Best: {_format_option(best)}"]
    for pit_in, label in ((1, "Pit this lap"), (2, "Pit next lap")):
        option = by_pit_in.get(pit_in)
        if option is not None and option is not best:
            cost = option["total_time_sec"] - best["total_time_sec"]
            lines.append(f"{label}: {strategy_planner.format_plan(option)}  +{cost:.1f}s")
    return lines


def _planned(start_lap, stints):
    segments, lap = [], start_lap
    for stint in stints:
        segments.append(
            {"compound": stint["compound"], "start": lap, "end": lap + stint["laps"] - 1, "done_until": None}
        )
        lap += stint["laps"]
    return segments


def _from_now(total_laps, race_laps, current, pit_in, stints):
    """Driven stints, then the tyre on the car until the stop (continuing its stint if no stop
    since), then the planned stints."""
    segments = []
    for stint in race_analysis.compute_stints(race_laps):
        laps = [n for n in stint["lap_numbers"] if n is not None]
        if laps and stint["compound"]:
            segments.append({"compound": stint["compound"], "start": laps[0], "end": laps[-1], "done_until": laps[-1]})
    laps_done = race_laps[-1]["lap"] if race_laps else 0
    stay_end = total_laps if pit_in is None else laps_done + pit_in
    if current and stay_end > laps_done:
        if segments and laps_on_set(race_laps) and segments[-1]["compound"] == current:
            segments[-1]["end"] = stay_end
        else:
            segments.append({"compound": current, "start": laps_done + 1, "end": stay_end, "done_until": None})
    return segments + _planned(stay_end + 1, stints or [])


def strategy_rows(total_laps, race_laps, view, prerace_best):
    """Rows for the strategy chart: [{"label", "note", "segments": [{"compound", "start", "end",
    "done_until"}]}]. done_until is the last driven lap of that stint (None if all still planned)."""
    if total_laps is None:
        return []
    rows = []
    if prerace_best:
        rows.append({"label": "Pre-race", "note": "", "segments": _planned(1, prerace_best["stints"])})
    if view and view.get("current_plan"):
        best = view["current_plan"]["best"]
        rows.append(
            {
                "label": "Live",
                "note": "best",
                "segments": _from_now(total_laps, race_laps, view["current"], best["pit_in"], best["stints"]),
            }
        )
    elif view and view.get("wet_window"):
        rows.append(
            {
                "label": "Live",
                "note": "wet, W5",
                "segments": _from_now(total_laps, race_laps, "wet", view["wet_window"]["pit_in"], []),
            }
        )
    elif race_laps:
        rows.append({"label": "Live", "note": "", "segments": _from_now(total_laps, race_laps, None, None, [])})
    soon = view.get("pit_soon") if view else None
    if soon:
        rows.append(
            {
                "label": "Box this lap" if soon["pit_in"] == 1 else "Box next lap",
                "note": soon["note"],
                "segments": _from_now(total_laps, race_laps, view["current"], soon["pit_in"], soon["stints"]),
            }
        )
    return rows


def pit_now_result(lb, pit_loss_sec, in_pit_window):
    """positions_lost_if_pit() for the latest leaderboard; None without pit-loss or gap data."""
    if pit_loss_sec is None or lb is None or lb.get("smoothed") is None:
        return None
    pace = lb.get("pace") if in_pit_window else None
    return live_strategy.positions_lost_if_pit(
        lb["smoothed"],
        pit_loss_sec,
        total_cars=lb.get("total_cars"),
        pace=pace,
    )


def pit_now_text(lb, pit_loss_sec, pit_loss_samples, in_pit_window):
    if pit_loss_sec is None:
        return "Pit now: -- (no pit-loss data for this track)"
    result = pit_now_result(lb, pit_loss_sec, in_pit_window)
    if result is None:
        return f"Pit now: -- (pit loss {pit_loss_sec:.1f}s)"
    spare = f", {result['margin_sec']:.1f}s spare" if result["margin_sec"] is not None else ""
    chaser = result["chaser_loss_per_lap"]
    if not in_pit_window:
        pace_note = "pace adj. starts in pit window"
    elif chaser is None:
        pace_note = "car behind pace: measuring"
    else:
        pace_note = f"eff. loss {result['effective_loss_sec']:.1f}s, car behind {chaser:+.1f}s/lap"
    return (
        f"Pit now: {result['label']} pos lost{spare}\n"
        f"  ({pace_note})\n"
        f"  (pit loss {pit_loss_sec:.1f}s, {pit_loss_samples} stops)"
    )
