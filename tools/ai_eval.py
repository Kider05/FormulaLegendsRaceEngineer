"""
ai_eval.py -- scores local LLMs as the race engineer. Replays saved races to a few points, asks
questions whose correct answers are computed here, and checks the numbers in each spoken answer.
Leaderboard data isn't saved per lap, so gaps/position use a made-up but consistent leaderboard.

Needs Ollama running with the models pulled. Run from the project root:
    python tools/ai_eval.py                         (qwen3.5:9b, thinking off)
    python tools/ai_eval.py qwen3.5:9b gpt-oss:20b  (several models)
    python tools/ai_eval.py --think qwen3.5:9b      (thinking on)
"""

import re
import sys
import time

from engineer import ai_engineer, race_state
from gui import race_view
from storage import race_db
from strategy import tyre_model
from strategy.strategy_planner import Planner

TRACK, VARIANT = "Silverstone", "modern"
RACES_TO_USE = 3
CHECKPOINTS = (5, 10, 15)
PROGRESS_EVERY = 50
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
WORDS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
WORDS.update(
    {
        w: i
        for i, w in enumerate(
            [
                "zeroth",
                "first",
                "second",
                "third",
                "fourth",
                "fifth",
                "sixth",
                "seventh",
                "eighth",
                "ninth",
                "tenth",
                "eleventh",
                "twelfth",
                "thirteenth",
                "fourteenth",
            ]
        )
    }
)


def leaderboard_for(i):
    behind, ahead, closing = 2.4 + i, 1.8 + i / 2, 0.6
    return {
        "position": 3,
        "total_cars": 14,
        "pace": {4: -closing},
        "smoothed": {
            "position": 3,
            "gaps": {2: -ahead, 4: behind},
            "gap_ahead_sec": ahead,
            "gap_behind_sec": behind,
            "gap_to_leader_sec": ahead + 4.0,
        },
    }


def questions(snap, laps):
    """(question, expected number, tolerance, required word or None)."""
    times = [lap["laptime_sec"] for lap in laps]
    order, pre, fuel = snap["race_order"], snap.get("prerace_plan"), snap.get("fuel") or {}
    qs = [
        ("How many laps are left?", snap["laps_remaining"], 0, None),
        ("What was my last lap time?", times[-1], 0.15, None),
        ("What's my fastest lap so far?", min(times), 0.15, None),
        ("What's the gap to the car behind?", order["car_behind"]["gap_s"], 0.15, None),
        ("What position am I in?", order["position"], 0, None),
        ("How much is the car behind gaining on me per lap?", 0.6, 0.1, None),
        ("How much fuel do I need to finish the race?", fuel.get("needed_to_finish"), 1.0, None),
        ("What's my total race time so far?", sum(times), 0.5, None),
        ("How much slower was my last lap than my fastest?", times[-1] - min(times), 0.15, None),
    ]
    clean = [lap["laptime_sec"] for lap in laps if race_state.lap_type(lap) == "clean"]
    if clean:
        qs.append(("What was my last clean lap?", clean[-1], 0.15, None))
    summary, left = snap["laps_summary"], snap["laps_remaining"]
    qs.append(
        ("At this rate, how many laps until the car behind catches me?", order["car_behind"]["gap_s"] / 0.6, 0.5, None)
    )
    paces = {summary["average_clean_lap_s"], summary["current_stint"]["average_clean_lap_s"]} - {None}
    qs.append(
        (
            "If I keep my average clean lap pace, what will my final race time be in seconds?",
            tuple(summary["total_race_time_s"] + p * left for p in paces),
            1.0,
            None,
        )
    )
    qs.append(
        (
            "If I lose 1.5 seconds per lap from now to the flag, how much time do I lose in total?",
            1.5 * left,
            0.05,
            None,
        )
    )
    if len(clean) >= 3:
        qs.append(("What's the average of my last three clean laps?", sum(clean[-3:]) / 3, 0.1, None))
    if summary["current_stint"]["average_clean_lap_s"] is not None:
        qs.append(
            (
                "How much slower is my average this stint than my best lap?",
                summary["current_stint"]["average_clean_lap_s"] - summary["best_lap_s"],
                0.1,
                None,
            )
        )
    slicks = (snap.get("slicks_if_box") or {}).get("this lap")
    if slicks:
        first = slicks.split(" -> ")[0]
        compound = {"S": "soft", "M": "medium", "H": "hard"}[first[0]]
        qs.append(("Which slicks if I box this lap?", int(first[1:]), 0, compound))
    if snap["current_plan"] and snap["current_plan"]["box_on_lap"]:
        qs.append(("When am I pitting?", snap["current_plan"]["box_on_lap"], 0, None))
        qs.append(("Which lap do I box on?", snap["current_plan"]["box_on_lap"], 0, None))
    if pre and pre["actual_time_for_completed_laps_s"] is not None:
        diff = pre["actual_time_for_completed_laps_s"] - pre["predicted_time_for_completed_laps_s"]
        qs.append(
            (
                "Am I ahead or behind the pre-race plan's time, and by how much?",
                abs(diff),
                0.3,
                "behind|slower|down|over" if diff > 0 else "ahead|faster|up|under",
            )
        )
    return [q for q in qs if q[1] is not None]


def grade(answer, expected, tol, word):
    """expected may be a tuple of acceptable values."""
    targets = expected if isinstance(expected, tuple) else (expected,)
    numbers = [abs(float(n)) for n in NUMBER.findall(answer.replace(",", ""))]
    numbers += [WORDS[w] for w in re.findall(r"[a-z]+", answer.lower()) if w in WORDS]
    if any(abs(t) < tol + 1e-9 for t in targets) and re.search(
        r"\b(equal|same|identical|matched|no difference)\b", answer, re.I
    ):
        numbers.append(0.0)
    ok = any(abs(n - abs(t)) <= tol + 1e-9 for n in numbers for t in targets)
    return ok and (word is None or re.search(word, answer, re.I) is not None)


def scenarios():
    races = race_db.track_laps(TRACK, VARIANT)
    pit = race_db.median_pit_loss(TRACK, VARIANT)[0]
    for r_i in range(1, RACES_TO_USE + 1):
        history, race = races[:-r_i], races[-r_i]
        model = tyre_model.fit_races(history)
        prerace = Planner(model, pit).plan(len(race), ban_second=True)[0]
        for cp in (c for c in CHECKPOINTS if c < len(race)):
            laps = race[:cp]
            live = Planner(tyre_model.fit_races(history + [laps], use_last_race_offset=True), pit)
            view = race_view.live_view(str(len(race)), laps, race[cp]["compound"], live)
            snap = race_state.build_snapshot(
                TRACK,
                len(race),
                laps,
                {"tyre_compound": race[cp]["compound"]},
                leaderboard_for(cp),
                view,
                live,
                prerace,
                race_view.fuel_per_lap(laps, history),
                tyre_model.current_tyre_pct(laps),
            )
            yield f"race -{r_i} lap {cp}", snap, laps


def main():
    args = [a for a in sys.argv[1:] if a != "--think"]
    ai_engineer.THINK = "--think" in sys.argv
    models = args or [ai_engineer.OLLAMA_MODEL]
    cases = [(name, snap, q) for name, snap, laps in scenarios() for q in questions(snap, laps)]
    print(f"{len(cases)} questions, thinking {'on' if ai_engineer.THINK else 'off'}\n")
    for model in models:
        ai_engineer.OLLAMA_MODEL = model
        passed, total_sec, calcs, misses = 0, 0.0, 0, []
        ai_engineer.ollama_chat([{"role": "user", "content": "hi"}])
        for i, (name, snap, (question, expected, tol, word)) in enumerate(cases, 1):
            if i % PROGRESS_EVERY == 0:
                print(f"  [{i}/{len(cases)}] {passed} correct so far, avg {total_sec / (i - 1):.1f}s", flush=True)
            t0, work = time.perf_counter(), []
            try:
                answer = ai_engineer.clean_answer(
                    ai_engineer.solve(ai_engineer.build_messages([], question, snap), snap, work=work)
                )
            except Exception as e:
                answer = f"(error: {e})"
            total_sec += time.perf_counter() - t0
            calcs += len(work)
            if grade(answer, expected, tol, word):
                passed += 1
            else:
                steps = "".join(f"\n      calc: {c.strip()} -> {o}" for c, o in work)
                shown = " or ".join(f"{t:.2f}" for t in (expected if isinstance(expected, tuple) else (expected,)))
                misses.append(f"  {name} | {question} | expected {shown} | got: {answer}{steps}")
        print(
            f"{model}: {passed}/{len(cases)} correct ({100 * passed / len(cases):.0f}%), "
            f"avg {total_sec / len(cases):.1f}s per answer, {calcs} calculations run"
        )
        print("\n".join(misses) if misses else "  no misses")
        print()


if __name__ == "__main__":
    main()
