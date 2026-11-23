"""
ai_engineer.py -- answers the driver's questions with a local LLM (Ollama), using a race_state
snapshot as its only source of facts. Arithmetic goes through the run_python tool (sandbox.py), so
numbers are computed, not guessed. ask() returns immediately; answers arrive on `results` as
(answer_text, seconds_taken, work) where work is [(code, output), ...].
"""

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request

from engineer import sandbox

OLLAMA_URL = "http://localhost:11434"
STALE_QUESTION_SEC = 15
OLLAMA_MODEL = "qwen3.5:9b"
THINK = False
KEEP_ALIVE = "30m"
TIMEOUT_SEC = 60
HISTORY_EXCHANGES = 6
MAX_TOOL_STEPS = 3
UNCONFIRMED_ANSWER = "Can't confirm that one."
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

SYSTEM_PROMPT = """You are the race engineer on the radio to your driver in the arcade racing game Formula Legends.
Answer the driver's question using only the RACE DATA in their message.

Rules:
- Reply in 1-2 short sentences, like F1 team radio. No greetings, no sign-offs, no filler, no lists.
- Round lap times and gaps to 0.1 s. Write numbers as digits (45.9, not "four-five point nine").
- Say tyre plans in words: "S4 -> H7" is "softs for 4 laps, then hards for 7".
- If a ready-made value answers the question (laps_summary, race_order, fuel, prerace_plan), just read it.
- Any other arithmetic (adding, subtracting, dividing, averaging, projecting, what-ifs) MUST go through
  the run_python tool. Never do maths in your head. Only say numbers that came from RACE DATA or run_python.
- In run_python, read every value from `data` by its exact key; never type race numbers into the code.
  If it returns an error, fix the code and call it again.
- Never invent a number. If RACE DATA doesn't contain what is needed, reply only: "No data on that yet."
- A radio check or greeting ("hello?", "radio check", "can you hear me") gets: "Loud and clear."
- If the question is garbled, reply only: "Say again?"
- Don't change the pit strategy. Pit calls come separately; you may restate current_plan if asked.
- "When do I pit/box" -> current_plan.box_on_lap. "Ahead/behind the plan" -> prerace_plan.driver_vs_plan.
- "When will the car behind catch me / when do I catch the car ahead" -> race_order.car_behind/car_ahead
  .laps_until_gap_closes. gap_to_leader_s is only about the leader.
- "Final/finish race time" -> laps_summary.projected_finish_s (no pit stops included).
- "Which tyres for N laps / what to switch to" -> the plan_stints tool. Never pick tyres yourself.
- "Will these tyres last / can I make the end / how many laps left on them" -> tyre_life.
- On wets, "when do I pit" -> wet_window (the conservative stop). Never predict the rain.
- On wets, "which slicks / what tyres if I box" -> slicks_if_box (the plan if boxing this lap or next lap).
- Answer the new question; never repeat a previous answer word for word.
- If current_plan and wet_window are both missing, say there is no live plan on this tyre. Never invent a
  plan, never suggest slicks on wets, and never quote prerace_plan as what to do now.
- now.track_condition is the track state the driver last reported (null = not reported). Never predict rain.
- When the driver STATES the track state (raining, wet, drying, dry), call set_track_condition; when they
  ask to drop or restore laps, call exclude_laps. Then confirm in a few words, e.g. "Copy, lap 12 onward wet."
  A question like "is it wet?" is not a report.
- run_python code is short arithmetic: no comments, no text answers.
- Only "clean" laps in lap_history show true pace; "lap 1", "pit in" and "pit out" laps are slow by design.
- If the driver gives numbers or a what-if, use them as data and reason it through.
- If the driver corrects you, accept it and answer again.

Data notes: times are seconds. Tyre % is tyre remaining, not wear. In race_order, closing_s_per_lap > 0 means that gap is shrinking
(car_behind catching the driver, or the driver catching car_ahead); laps_until_gap_closes is that gap / rate.
Game rules: the first pit stop must change tyre compound (wets count as a change). Tyre sets are unlimited.
Tyres are not run below about 5%."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Run a short Python calculation. `data` is the RACE DATA as a dict. Ready variables: "
                "lap_times (every lap, oldest first), clean_lap_times (clean laps only, oldest first, so [-3:] "
                "is the last three), laps_left, time_so_far (seconds raced so far, all laps). Built-ins: abs, "
                "min, max, sum, len, round, sorted, range, enumerate, zip, mean, median, stdev, sqrt, floor, "
                "ceil. No imports. The last line's value is returned. Example: time_so_far + "
                "mean(clean_lap_times) * laps_left"
            ),
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python code"}},
                "required": ["code"],
            },
        },
    }
]


class Engineer:
    def __init__(self, chat=None):
        self.results = queue.Queue()
        self.status = "checking"
        self._history = []
        self._lock = threading.Lock()
        self._chat = chat or ollama_chat

    def check(self):
        """Background check that Ollama is running and has OLLAMA_MODEL; sets status."""
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        try:
            with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            self.status = "Ollama not running (start it, or install from ollama.com)"
            return
        if OLLAMA_MODEL not in names:
            self.status = f"model missing: run  ollama pull {OLLAMA_MODEL}"
            return
        self.status = f"loading {OLLAMA_MODEL} into GPU memory"
        try:
            self._chat([{"role": "user", "content": "Reply OK."}])
        except Exception as e:
            self.status = f"model failed to load: {e}"
            return
        self.status = f"ready ({OLLAMA_MODEL})"

    def ask(self, question, snapshot, actions=None):
        """actions: engineer.actions.build() result, the tools the model may call besides run_python."""
        threading.Thread(
            target=self._answer, args=(question, snapshot, time.perf_counter(), actions), daemon=True
        ).start()

    def _answer(self, question, snapshot, asked_at=None, actions=None):
        t0 = asked_at or time.perf_counter()
        with self._lock:
            if time.perf_counter() - t0 > STALE_QUESTION_SEC:
                self.results.put(
                    (f"(skipped, asked {time.perf_counter() - t0:.0f}s ago)", time.perf_counter() - t0, [])
                )
                return
            messages = build_messages(self._history, question, snapshot)
            work = []
            try:
                answer = clean_answer(solve(messages, snapshot, self._chat, work, actions))
            except Exception as e:
                self.results.put((f"(AI error: {e})", time.perf_counter() - t0, work))
                return
            if self._history and answer == self._history[-1][1]:
                work.append(("(repeat check)", "same as the last answer; asking again without history"))
                answer = clean_answer(
                    solve(build_messages([], question, snapshot), snapshot, self._chat, work, actions)
                )
            if not answer:
                answer = "Say again?"
            self._history = (self._history + [(question, answer)])[-HISTORY_EXCHANGES:]
            self.results.put((answer, time.perf_counter() - t0, work))

    def note(self, question, answer):
        """Record an exchange answered outside the model, so follow-up questions have context."""
        with self._lock:
            self._history = (self._history + [(question, answer)])[-HISTORY_EXCHANGES:]

    def reset(self):
        with self._lock:
            self._history = []


def build_messages(history, question, snapshot):
    """System prompt, recent exchanges as plain text, then the question with the live data."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for q, a in history:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    data = json.dumps(snapshot, separators=(",", ":"))
    messages.append({"role": "user", "content": f"RACE DATA: {data}\n\nDRIVER: {question}"})
    return messages


def calc_vars(snapshot):
    """Common building blocks for run_python, so the model needn't rebuild them from lap_history."""
    history = [lap for lap in snapshot.get("lap_history") or [] if lap.get("time_s") is not None]
    lap_times = [lap["time_s"] for lap in history]
    return {
        "lap_times": lap_times,
        "clean_lap_times": [lap["time_s"] for lap in history if lap.get("type") == "clean"],
        "laps_left": snapshot.get("laps_remaining"),
        "time_so_far": round(sum(lap_times), 3),
    }


def _text(reply):
    return reply if isinstance(reply, str) else reply.get("content", "")


def ungrounded_numbers(answer, sources):
    """Numbers in the answer that don't match any number in `sources` (allowing for rounding).
    Small whole numbers (0-3) are always fine."""
    known = [float(n) for n in NUMBER.findall(sources)]
    bad = []
    for text in NUMBER.findall(answer.replace(",", "")):
        a = abs(float(text))
        if "." not in text and a <= 3:
            continue
        tol = 0.051 if "." in text else 1.0
        if not any(abs(a - abs(k)) < tol for k in known):
            bad.append(text)
    return bad


def solve(messages, snapshot, chat=None, work=None, actions=None):
    """Answer, then check every number in it came from the data, the question or a successful
    calculation. If not, ask once more to recompute; if still not, give UNCONFIRMED_ANSWER."""
    chat, work = chat or ollama_chat, work if work is not None else []
    messages = list(messages)
    given = messages_source(messages)
    answer = _solve_once(messages, snapshot, chat, work, actions)
    sources = lambda: given + "\n".join(o for c, o in work if not o.startswith("error") and c != "(answer check)")
    bad = ungrounded_numbers(answer, sources())
    if not bad:
        return answer
    work.append(("(answer check)", f"not backed by data: {', '.join(bad)}; asking again"))
    messages += [
        {"role": "assistant", "content": answer},
        {
            "role": "user",
            "content": f"Check: {', '.join(bad)} is not in RACE DATA or a successful run_python "
            "result. Recompute with run_python, then answer again.",
        },
    ]
    answer = _solve_once(messages, snapshot, chat, work, actions)
    return UNCONFIRMED_ANSWER if ungrounded_numbers(answer, sources()) else answer


def messages_source(messages):
    """The latest user message: the live RACE DATA plus the question."""
    return next(
        (m["content"] for m in reversed(messages) if m["role"] == "user" and "RACE DATA" in m["content"]),
        messages[-1]["content"] if messages else "",
    )


def _solve_once(messages, snapshot, chat, work, actions=None):
    """Chat until the model answers in words (appending to `messages`), running each run_python call
    in the sandbox. The last round offers no tools, so the model must answer with what it has. A
    repeated call is refused rather than re-run; an empty answer after calculating gets one nudge."""
    extra = calc_vars(snapshot)
    actions = actions or {}
    tools = TOOLS + [schema for schema, _ in actions.values()]
    for step in range(MAX_TOOL_STEPS + 1):
        reply = chat(messages, tools if step < MAX_TOOL_STEPS else None)
        calls = [] if isinstance(reply, str) else reply.get("tool_calls") or []
        if not calls:
            answer = _text(reply)
            if not answer.strip() and work:
                messages.append(
                    {"role": "user", "content": "Now answer the driver in 1-2 sentences using that result."}
                )
                answer = _text(chat(messages, None))
            return answer
        messages.append({"role": "assistant", "content": reply.get("content", ""), "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            args = args if isinstance(args, dict) else {}
            code = args.get("code", "")
            if fn.get("name") in actions:
                code = f"{fn['name']}({', '.join(map(str, args.values()))})"
                output = actions[fn["name"]][1](args)
            elif fn.get("name") != "run_python":
                output = "error: unknown tool"
            elif any(code.strip() == done.strip() for done, _ in work):
                output = "error: same code as before; read the error and change it"
            else:
                output = sandbox.run(code, snapshot, extra)
            work.append((code, output))
            messages.append({"role": "tool", "tool_name": fn.get("name", ""), "content": output})
    return ""


def ollama_chat(messages, tools=None):
    """One /api/chat round; returns the reply message dict (content and any tool_calls)."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": THINK,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.2},
    }
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        return json.load(r)["message"]


def clean_answer(text):
    """Strip any <think> block and markdown so only speakable text remains."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return re.sub(r"[*_#`]", "", text).strip()


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
