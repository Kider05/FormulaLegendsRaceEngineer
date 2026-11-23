"""Golden HUD reads on the saved 4K screenshots in screenshots/test (gitignored, so skipped where absent).
Expected values were read off each screenshot by eye. Vintage-HUD shots (1950test*) aren't covered:
the glyph templates are the modern font."""

from functools import cache

import cv2
import pytest

from paths import PROJECT_DIR
from telemetry import hud_reader
from telemetry.lap_summary import parse_laptime_seconds
from telemetry.ui_state_detector import check_named_states

SHOTS = PROJECT_DIR / "screenshots" / "test"
pytestmark = pytest.mark.skipif(not SHOTS.is_dir(), reason="no screenshots/test folder")

FLAGGED = "flagged"  # not readable on screen: must come back low-confidence, never confidently wrong
BLANK = "blank"  # dashes on screen: must not parse as a time
SKIP = "skip"  # known gap, not asserted

FIELD_NAMES = ("lap", "laptime", "hp", "fuel", "tyre", "last_lap_time")
FIELDS = {
    "NurburgTest1.PNG": ("4|20", "00:28.202", "72", "100", "26", "01:04.699"),
    "bakutest.PNG": ("6|10", "00:59.770", "72", "52", "29", "01:03.796"),
    "spalatetest.png": ("15|22", "00:29.196", "97", "43", "76", "00:49.701"),
    "spatest1.PNG": ("10|11", "00:11.667", "97", "38", "50", BLANK),
    "test1.PNG": ("1|22", "00:37.764", "83", "94", "87", FLAGGED),
    "test2.PNG": ("8|9", "00:02.567", "100", "37", "85", "01:22.564"),
    "leaderboardtest.png": ("2|20", "01:04.698", "97", "86", "68", "00:49.234"),
    "leaderboardtest2.png": (FLAGGED, "00:33.431", "90", "64", "95", "01:27.932"),
}

COMPOUNDS = {
    "NurburgTest1.PNG": "soft",
    "bakutest.PNG": "hard",
    "spalatetest.png": "hard",
    "spatest1.PNG": "hard",
    "test1.PNG": "soft",
    "test2.PNG": "wet",
    "leaderboardtest.png": "soft",
    "leaderboardtest2.png": "medium",
    "Pause.PNG": None,
    "pitstop.PNG": None,
}

UI_STATES = {"Pause.PNG": "pause", "pitstop.PNG": "pitstop"} | {name: None for name in FIELDS}

# position, total cars, then per row (leader, row2, row3, row4): position, gap, tyre. Player row has no gap or tyre.
# leaderboardtest*.png have debug boxes drawn over the leaderboard, so they're left out.
LEADERBOARDS = {
    "NurburgTest1.PNG": (
        11,
        14,
        [1, 10, 11, 12],
        [-27.530, -6.222, None, 13.335],
        ["hard", "medium", None, "hard"],
    ),
    "bakutest.PNG": (1, 14, [1, 2, 3, 4], [None, 2.794, 8.199, 17.582], [None, "hard", "hard", "hard"]),
    # P4 is "+1 LAP": no gap, and its tyre icon sits further left than the crop.
    "spalatetest.png": (1, 14, [1, 2, 3, 4], [None, 47.910, 54.066, None], [None, "hard", "medium", SKIP]),
    "spatest1.PNG": (1, 14, [1, 2, 3, 4], [None, 32.552, 37.835, 39.276], [None, "hard", "soft", "medium"]),
    "test1.PNG": (13, 14, [1, 12, 13, 14], [-11.984, -6.720, None, 6.834], ["hard", "hard", None, "soft"]),
    "test2.PNG": (1, 14, [1, 2, 3, 4], [None, 22.219, 24.628, 25.464], [None, "wet", "wet", "wet"]),
}


@cache
def frame(name):
    path = SHOTS / name
    if not path.exists():
        pytest.skip(f"{name} missing")
    return cv2.imread(str(path))


@pytest.mark.parametrize("name", FIELDS)
def test_hud_fields(name):
    reads = hud_reader.read_all_fields(frame(name))
    for field, expected in zip(FIELD_NAMES, FIELDS[name], strict=True):
        text, low_confidence = reads[field]
        if expected == FLAGGED:
            assert low_confidence, f"{field} read {text!r} confidently"
        elif expected == BLANK:
            assert parse_laptime_seconds(text) is None, f"{field} parsed {text!r}"
        else:
            assert (text, low_confidence) == (expected, []), field


@pytest.mark.parametrize("name", COMPOUNDS)
def test_tyre_compound(name):
    assert hud_reader.read_tyre_compound(frame(name))[0] == COMPOUNDS[name]


@pytest.mark.parametrize("name", UI_STATES)
def test_ui_state(name):
    assert check_named_states(frame(name), hud_reader.ui_state_configs())[0] == UI_STATES[name]


def test_pit_fuel():
    assert hud_reader.read_pit_fuel(frame("pitstop.PNG")) == 50


@pytest.mark.parametrize("name", LEADERBOARDS)
def test_leaderboard(name):
    position, total, positions, gaps, tyres = LEADERBOARDS[name]
    lb = hud_reader.read_leaderboard(frame(name))
    assert (lb["position"], lb["total_cars"]) == (position, total)
    assert [row["pos"] for row in lb["rows"]] == positions
    assert [row["gap_sec"] for row in lb["rows"]] == gaps
    assert [row["is_player"] for row in lb["rows"]] == [p == position for p in positions]
    for row, tyre in zip(lb["rows"], tyres, strict=True):
        if tyre != SKIP:
            assert row["tyre"] == tyre, row["row"]
    gap_of = dict(zip(positions, gaps, strict=True))
    behind, ahead = gap_of.get(position + 1), gap_of.get(position - 1)
    assert lb["gap_behind_sec"] == behind
    assert lb["gap_ahead_sec"] == (abs(ahead) if ahead is not None else None)
