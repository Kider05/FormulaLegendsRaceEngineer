"""Python sandbox for the AI's calculations."""

from engineer import sandbox


def test_sandbox_computes_and_blocks_escapes():
    data = {"laps": [46.1, 46.5, 47.0], "gap": 7.4, "rate": 0.6}
    assert sandbox.run('data["gap"] / data["rate"]', data) == "result = 12.3333"
    assert (
        sandbox.run("x = mean(data['laps'])\nprint(len(data['laps']))\nround(x, 2)", data)
        == "printed: 3\nresult = 46.53"
    )
    for code, error in (
        ("import os", "Import"),
        ("().__class__", ".__class__"),
        ("(x for x in []).gi_frame", ".gi_frame"),
        ("'{0.__class__}'.format(1)", ".format"),
        ("__import__('os')", "__import__"),
    ):
        assert sandbox.run(code, data) == f"error: not allowed: {error}"
    assert sandbox.run("open('x')", data).startswith("error: NameError")
    assert sandbox.run("gap / rate", data) == "result = 12.3333"
    assert sandbox.run("n = len(laps) * 2", data) == "result = 6"
    assert sandbox.run("round(46.4321, 0.1)", data) == "result = 46.4"
    assert sandbox.run("data['k'] + k", data, {"k": 2}) == "result = 4"
    assert sandbox.run("data['gapp']", data) == "error: KeyError: \"'gapp' not found, available keys: laps, gap, rate\""
    assert sandbox.run("1 +", data).startswith("error: SyntaxError")


def test_sandbox_times_out(monkeypatch):
    monkeypatch.setattr(sandbox, "TIMEOUT_SEC", 0.5)
    assert sandbox.run("while True: pass", {}) == "error: took longer than 0s"
