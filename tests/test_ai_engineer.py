"""AI engineer: messages, history, tool loop and number grounding."""

import time

from engineer import ai_engineer


def test_messages_carry_history_and_live_data():
    msgs = ai_engineer.build_messages([("gap behind?", "1.2 seconds.")], "and now?", {"laps_completed": 4})
    assert msgs[0]["role"] == "system" and msgs[1]["content"] == "gap behind?"
    assert msgs[-1]["content"].startswith('RACE DATA: {"laps_completed":4}') and msgs[-1]["content"].endswith(
        "DRIVER: and now?"
    )


def test_answer_cleanup_and_sentences():
    assert ai_engineer.clean_answer("<think>sum it</think>**Gap is 2.1 s.**") == "Gap is 2.1 s."
    assert ai_engineer.split_sentences("Gap is 2.1 s. He's closing. Push!") == [
        "Gap is 2.1 s.",
        "He's closing.",
        "Push!",
    ]


def test_engineer_keeps_recent_history_and_reports_errors():
    seen = []
    eng = ai_engineer.Engineer(chat=lambda m, tools=None: (seen.append(len(m)), f"Copy {'x' * len(seen)}.")[1])
    for i in range(ai_engineer.HISTORY_EXCHANGES + 2):
        eng._answer(f"q{i}", {})
    eng._answer("old", {}, asked_at=time.perf_counter() - 60)
    assert eng.results.queue[-1][0].startswith("(skipped")
    assert len(eng._history) == ai_engineer.HISTORY_EXCHANGES
    assert seen[-1] == 2 + 2 * ai_engineer.HISTORY_EXCHANGES
    bad = ai_engineer.Engineer(chat=lambda m, tools=None: 1 / 0)
    bad._answer("q", {})
    assert bad.results.get_nowait()[0].startswith("(AI error")


def test_solve_runs_tool_calls_then_answers():
    replies = [
        {"content": "", "tool_calls": [{"function": {"name": "run_python", "arguments": {"code": "data['a'] * 3"}}}]},
        {"content": "Twelve."},
    ]
    sent = []
    work = []
    answer = ai_engineer.solve(
        [{"role": "user", "content": "q"}],
        {"a": 4},
        lambda m, tools=None: (sent.append((list(m), tools)), replies.pop(0))[1],
        work,
    )
    assert answer == "Twelve." and work == [("data['a'] * 3", "result = 12")]
    assert sent[1][0][-1] == {"role": "tool", "tool_name": "run_python", "content": "result = 12"}
    looping = {"content": "", "tool_calls": [{"function": {"name": "run_python", "arguments": '{"code": "1"}'}}]}
    tools_offered, work = [], []
    ai_engineer.solve(
        [], {}, lambda m, tools=None: (tools_offered.append(tools), looping if tools else "Done.")[1], work
    )
    assert tools_offered[-1] is None and len(tools_offered) == ai_engineer.MAX_TOOL_STEPS + 1
    assert [out for _, out in work] == ["result = 1"] + ["error: same code as before; read the error and change it"] * 2


def test_ungrounded_numbers_allow_rounding_only():
    src = "gap 12.3333 lap 46.44 laps 20"
    assert ai_engineer.ungrounded_numbers("About 12.3 laps, or 12, lap 46.4, 2 cars.", src) == []
    assert ai_engineer.ungrounded_numbers("Average 46.3, catch in 13.8 laps, 25 left.", src) == ["46.3", "13.8", "25"]


def test_solve_rechecks_made_up_numbers():
    user = {"role": "user", "content": 'RACE DATA: {"a":4}\n\nDRIVER: q'}
    replies = ["It's 47.3.", "Still 47.3.", "It's 47.3.", "Twelve, so 12."]
    chat = lambda m, tools=None: replies.pop(0)
    assert ai_engineer.solve([user], {"a": 4}, chat) == ai_engineer.UNCONFIRMED_ANSWER
    calc = {"content": "", "tool_calls": [{"function": {"name": "run_python", "arguments": {"code": "a * 3"}}}]}
    replies = ["It's 47.3.", calc, "Twelve, so 12."]
    assert ai_engineer.solve([user], {"a": 4}, chat) == "Twelve, so 12."


def test_engineer_retries_a_repeated_answer_without_history():
    answers = iter(["Box this lap.", "Box this lap.", "Hards, eight laps."])
    eng = ai_engineer.Engineer(chat=lambda m, tools=None: next(answers))
    eng._answer("When do I box?", {})
    eng._answer("Which slicks?", {})
    answer, _, work = eng.results.queue[-1]
    assert answer == "Hards, eight laps." and work[0][0] == "(repeat check)"
    eng.note("Track is dry", "Copy, lap 14 onward dry.")
    assert eng._history[-1] == ("Track is dry", "Copy, lap 14 onward dry.")


def test_solve_nudges_empty_answer_and_offers_calc_vars():
    snap = {
        "laps_remaining": 2,
        "lap_history": [
            {"time_s": 49.0, "type": "lap 1"},
            {"time_s": 46.0, "type": "clean"},
            {"time_s": 46.4, "type": "clean"},
            {"time_s": None, "type": "clean"},
        ],
    }
    assert ai_engineer.calc_vars(snap) == {
        "lap_times": [49.0, 46.0, 46.4],
        "clean_lap_times": [46.0, 46.4],
        "laps_left": 2,
        "time_so_far": 141.4,
    }
    replies = [
        {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "run_python",
                        "arguments": {"code": "time_so_far + mean(clean_lap_times) * laps_left"},
                    }
                }
            ],
        },
        {"content": ""},
        {"content": "About 233.8 seconds."},
    ]
    work = []
    assert ai_engineer.solve([], snap, lambda m, tools=None: replies.pop(0), work) == "About 233.8 seconds."
    assert work[0][1] == "result = 233.8"
