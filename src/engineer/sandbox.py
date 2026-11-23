"""
sandbox.py -- runs the AI's arithmetic as real Python in a separate, short-lived process.
The code sees only `data` (the race snapshot) and a small set of safe built-ins: no imports,
no files, no network, only plain list/dict/str methods. The last expression (or a variable
named `result`, or the last variable assigned) is the answer. The snapshot's top-level keys are also
plain variables, so `laps_summary` works as well as `data['laps_summary']`; `extra` adds more variables.
"""

import ast
import json
import subprocess
import sys

TIMEOUT_SEC = 2.0
MAX_OUTPUT_CHARS = 1500
BLOCKED_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.AsyncFunctionDef,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.ClassDef,
    ast.With,
    ast.AsyncWith,
)
ALLOWED_ATTRS = set(
    [
        "get",
        "items",
        "keys",
        "values",
        "append",
        "extend",
        "index",
        "count",
        "sort",
        "reverse",
        "copy",
        "pop",
        "insert",
        "lower",
        "upper",
        "startswith",
        "endswith",
        "split",
        "strip",
        "join",
        "replace",
    ]
)

RUNNER = r"""
import ast, builtins, json, math, statistics, sys
ALLOWED = ("abs all any bool dict enumerate filter float int isinstance len list map max min "
           "range reversed round set sorted str sum tuple zip").split()
EXTRA = {"sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil, "mean": statistics.mean,
         "median": statistics.median, "stdev": statistics.stdev}

def tidy(v):
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return [tidy(x) for x in v]
    if isinstance(v, dict):
        return {k: tidy(x) for k, x in v.items()}
    return v

class Data(dict):
    def __missing__(self, key):
        raise KeyError(f"{key!r} not found, available keys: {', '.join(map(str, self))}")

def wrap(v):
    if isinstance(v, dict):
        return Data({k: wrap(x) for k, x in v.items()})
    if isinstance(v, list):
        return [wrap(x) for x in v]
    return v

req = json.loads(sys.stdin.read())
tree = ast.parse(req["code"])
last = tree.body[-1] if tree.body else None
if isinstance(last, ast.Expr):
    tree.body[-1] = ast.Assign([ast.Name("result", ast.Store())], last.value)
    ast.fix_missing_locations(tree)
answer_name = (last.targets[0].id if isinstance(last, ast.Assign) and len(last.targets) == 1
               and isinstance(last.targets[0], ast.Name) else "result")
printed = []
data = wrap(req["data"])
env = {k: v for k, v in data.items() if str(k).isidentifier()}
data.update(wrap(req["extra"]))
env.update(wrap(req["extra"]))
env.update({"__builtins__": {n: getattr(builtins, n) for n in ALLOWED}, "data": data})
env["__builtins__"].update(EXTRA)

def lenient_round(x, ndigits=None):
    if isinstance(ndigits, float) and 0 < ndigits < 1:
        ndigits = round(-math.log10(ndigits))
    return round(x, ndigits)

env["__builtins__"]["round"] = lenient_round
env["__builtins__"]["print"] = lambda *a: printed.append(" ".join(map(str, a)))
try:
    exec(compile(tree, "<calc>", "exec"), env)
    out = {"result": repr(tidy(env.get("result", env.get(answer_name)))), "printed": printed}
except Exception as e:
    out = {"error": f"{type(e).__name__}: {e}", "printed": printed}
print(json.dumps(out))
"""


def check(code):
    """Error text if the code uses anything outside the sandbox rules, else None."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, BLOCKED_NODES):
            return f"not allowed: {type(node).__name__}"
        if isinstance(node, ast.Attribute) and node.attr not in ALLOWED_ATTRS:
            return f"not allowed: .{node.attr}"
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            return f"not allowed: {node.id}"
    return None


def run(code, data, extra=None):
    """Run `code` against `data`; returns text for the AI, e.g. 'result = 12.3333' or 'error: ...'."""
    problem = check(code)
    if problem:
        return f"error: {problem}"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", RUNNER],
            input=json.dumps({"code": code, "data": data, "extra": extra or {}}),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"error: took longer than {TIMEOUT_SEC:.0f}s"
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return f"error: {(proc.stderr.strip().splitlines() or ['no output'])[-1]}"
    lines = [f"printed: {p}" for p in out["printed"]]
    lines.append(f"error: {out['error']}" if "error" in out else f"result = {out['result']}")
    return "\n".join(lines)[:MAX_OUTPUT_CHARS]
