"""
actions.py -- tools the AI engineer can call besides run_python. build() returns
{name: (schema, handler)}; a handler takes the model's argument dict and returns text for the model.
Handlers only validate and pass the request to a callback, so the app decides what changes.
"""

import re

from engineer.race_state import to_int

CONDITIONS = ("raining", "wet", "drying", "dry")
_REPORT = re.compile(
    r"\b(?:track|it)(?:'s|\s+is|\s+was|\s+has|\s+now|\s+getting|\s+started)*\s+"
    r"(raining|rain|wet|drying|dried|dry)\b",
    re.I,
)
_QUESTION = re.compile(r"^\s*(is|are|was|will|should|could|can|do|does|what|when|how|why)\b", re.I)
_WORD_TO_CONDITION = {"rain": "raining", "dried": "dry"}


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


PLAN_TOOL = _tool(
    "plan_stints",
    "Best tyre plans for a number of laps on fresh slick tyres, from the team's tyre model. "
    "Returns plans like 'S5: 231.4 s', fastest first.",
    {"laps": {"type": "integer", "description": "laps to plan"}},
    ["laps"],
)
CONDITION_TOOL = _tool(
    "set_track_condition",
    "Record the track state the driver reports. Applies from from_lap (default: the current lap) onward.",
    {
        "condition": {"type": "string", "enum": list(CONDITIONS)},
        "from_lap": {"type": "integer", "description": "first lap it applies to"},
    },
    ["condition"],
)
EXCLUDE_TOOL = _tool(
    "exclude_laps",
    "Leave completed laps out of the tyre model (e.g. a crash or traffic), or put them back with exclude=false.",
    {"laps": {"type": "array", "items": {"type": "integer"}}, "exclude": {"type": "boolean"}},
    ["laps"],
)


def parse_condition_report(text, current_lap):
    """'Track is drying' / 'track was dry, lap 14' -> (condition, from_lap); None for anything else,
    including questions. from_lap defaults to current_lap and must be 1..current_lap."""
    if "?" in text or _QUESTION.match(text):
        return None
    found = _REPORT.findall(text)
    if not found:
        return None
    word = found[-1].lower()
    lap = re.search(r"\blap\s+(\d+)", text, re.I)
    start = int(lap.group(1)) if lap else current_lap
    if not 1 <= start <= current_lap:
        return None
    return _WORD_TO_CONDITION.get(word, word), start


def build(current_lap, planner=None, set_condition=None, exclude=None):
    """current_lap: the lap in progress (completed laps + 1). Each callback is optional; missing ones
    leave their tool out. set_condition(condition, from_lap), exclude(laps, exclude_bool)."""
    actions = {}
    if planner:

        def plan(args):
            laps = to_int(args.get("laps"))
            return "error: laps must be a whole number" if laps is None else planner(laps)

        actions["plan_stints"] = (PLAN_TOOL, plan)
    if set_condition:

        def condition(args):
            state = str(args.get("condition", "")).lower()
            if state not in CONDITIONS:
                return f"error: condition must be one of {', '.join(CONDITIONS)}"
            start = to_int(args.get("from_lap")) or current_lap
            if not 1 <= start <= current_lap:
                return f"error: from_lap must be 1 to {current_lap}"
            set_condition(state, start)
            return f"done: lap {start} onward marked {state}"

        actions["set_track_condition"] = (CONDITION_TOOL, condition)
    if exclude:

        def leave_out(args):
            laps = args.get("laps")
            laps = [to_int(n) for n in (laps if isinstance(laps, list) else [laps])]
            if not laps or any(n is None or not 1 <= n < current_lap for n in laps):
                return f"error: laps must be completed laps, 1 to {current_lap - 1}"
            out = args.get("exclude", True) is not False
            exclude(laps, out)
            return f"done: laps {', '.join(map(str, laps))} {'excluded from' if out else 'put back in'} the model"

        actions["exclude_laps"] = (EXCLUDE_TOOL, leave_out)
    return actions
