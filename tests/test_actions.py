"""AI actions: plan_stints, track condition reports, lap exclusion."""

from engineer import actions, ai_engineer, race_state
from gui import race_view
from helpers import SILVERSTONE, dry_laps
from strategy import tyre_model
from strategy.strategy_planner import Planner, format_plan


def test_plan_stints_tool_uses_real_planner():
    planner = race_state.stint_planner(Planner(SILVERSTONE, 20.0))
    expected = Planner(SILVERSTONE, 20.0).plan(5)[0]
    assert planner(5).startswith(f"{format_plan(expected)}: {expected['total_time_sec']:.1f} s")
    assert planner(0).startswith("error") and race_state.stint_planner(None)(5).startswith("error")
    call = {"content": "", "tool_calls": [{"function": {"name": "plan_stints", "arguments": {"laps": 5}}}]}
    replies, offered, work = [call, "Go softs."], [], []
    acts = actions.build(6, planner=planner)
    ai_engineer.solve([], {}, lambda m, tools=None: (offered.append(tools), replies.pop(0))[1], work, acts)
    assert work == [("plan_stints(5)", planner(5))] and actions.PLAN_TOOL in offered[0]


def test_condition_reports_are_parsed_and_questions_ignored():
    parse = actions.parse_condition_report
    assert parse("Track is wet, track is wet.", 8) == ("wet", 8)
    assert parse("Track is drying.", 13) == ("drying", 13)
    assert parse("Track was dry, lap 14.", 16) == ("dry", 14)
    assert parse("It's raining", 3) == ("raining", 3)
    assert parse("Track has dried", 5) == ("dry", 5)
    for text in ("Is the track wet?", "Should I go on wets", "What tyres do I go on?", "Track dry lap 30", "Box now"):
        assert parse(text, 10) is None, text


def test_rain_race_condition_flow_fills_the_review_column():
    """Live flow: laps finish one by one; reports arrive mid-lap and stamp new laps from then on."""
    laps, condition = [], None
    script = {8: "Track is wet, track is wet.", 13: "Track is drying.", 16: "Track was dry, lap 14."}
    for n in range(1, 21):
        if n in script:
            condition, start = actions.parse_condition_report(script[n], current_lap=n)
            race_view.mark_condition(laps, condition, start)
        laps.append({**dry_laps(1, "wet" if 7 < n < 14 else "medium")[0], "lap": n, "condition": condition})
        assert actions.parse_condition_report("Which slicks if I box?", n) is None
    column = [race_view.lap_review_row(lap)[7] for lap in laps]
    assert column == [""] * 7 + ["wet"] * 5 + ["drying"] + ["dry"] * 7
    model_laps = [
        lap["lap"]
        for lap in tyre_model.annotate_start_pct(laps)
        if lap["compound"] != "wet" and tyre_model.is_model_lap(lap)
    ]
    assert model_laps[-5:] == [16, 17, 18, 19, 20] and not set(range(8, 16)) & set(model_laps)


def test_condition_and_exclude_actions_validate_then_call_back():
    calls = []
    acts = actions.build(
        8,
        set_condition=lambda c, f: calls.append(("cond", c, f)),
        exclude=lambda laps, out: calls.append(("excl", laps, out)),
    )
    assert "plan_stints" not in acts
    cond, excl = acts["set_track_condition"][1], acts["exclude_laps"][1]
    assert cond({"condition": "Wet"}) == "done: lap 8 onward marked wet"
    assert cond({"condition": "drying", "from_lap": 5}) == "done: lap 5 onward marked drying"
    assert cond({"condition": "snow"}).startswith("error") and cond({"condition": "wet", "from_lap": 9}).startswith(
        "error"
    )
    assert excl({"laps": [3, 7]}) == "done: laps 3, 7 excluded from the model"
    assert excl({"laps": [3], "exclude": False}) == "done: laps 3 put back in the model"
    assert excl({"laps": [8]}).startswith("error") and excl({"laps": []}).startswith("error")
    assert calls == [("cond", "wet", 8), ("cond", "drying", 5), ("excl", [3, 7], True), ("excl", [3], False)]
