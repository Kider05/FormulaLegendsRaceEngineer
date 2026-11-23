"""HUD text parsing."""

import pytest

from telemetry.hud_reader import parse_gap


@pytest.mark.parametrize(
    "text, expected",
    [
        ("+0.401", 0.401),
        ("-1.318", -1.318),
        ("-+0.613", 0.613),
        ("-2.620.", -2.620),
        ("+1 Lap", None),
        ("", None),
        (None, None),
        ("+1.234 -5.678", None),
    ],
)
def test_parse_gap(text, expected):
    assert parse_gap(text) == expected


def test_profiler_logs_per_cycle_stage_times(monkeypatch, caplog):
    from telemetry import profiler

    clock = iter([0.0, 1.0, 1.004, 1.01, 1.012, 20.0, 20.0])
    monkeypatch.setattr(profiler.time, "perf_counter", lambda: next(clock))
    prof = profiler.LoopProfiler(enabled=True)
    with prof.stage("capture"):
        pass
    with prof.stage("fields"):
        pass
    with caplog.at_level("INFO"):
        prof.end_cycle()
    assert caplog.messages == ["profile: 1 cycles at 0.1 Hz, 6.0 ms work/cycle | capture 4.0  fields 2.0"]


def test_disabled_profiler_does_nothing():
    from telemetry.profiler import LoopProfiler

    prof = LoopProfiler(enabled=False)
    with prof.stage("capture"):
        pass
    prof.end_cycle()
    assert not prof._totals and prof._cycles == 0


class FakeScreen:
    """Stands in for MSS: grabs come from a fixed BGRA image of the window."""

    def __init__(self, image):
        self.image, self.grabs = image, []

    def grab(self, region):
        self.grabs.append(region)
        top, left = region["top"] - 50, region["left"] - 30
        return self.image[top : top + region["height"], left : left + region["width"]]


def test_region_capture_matches_full_capture_inside_every_region(monkeypatch):
    import numpy as np

    from telemetry import capture
    from telemetry.hud_layout import READ_REGIONS

    image = np.random.default_rng(0).integers(0, 256, (2160, 3840, 4), np.uint8)
    screen = FakeScreen(image)
    monkeypatch.setattr(capture, "find_window", lambda title: (30, 50, 30 + 3840, 50 + 2160))
    monkeypatch.setattr(capture, "_screen", lambda: screen)

    full = capture.capture_frame()
    partial = capture.capture_frame(READ_REGIONS)
    for y1, y2, x1, x2 in READ_REGIONS:
        assert np.array_equal(partial[y1:y2, x1:x2], full[y1:y2, x1:x2])
    grabbed = sum(g["width"] * g["height"] for g in screen.grabs[1:])
    assert len(screen.grabs) == 1 + 4 and grabbed < 0.15 * 3840 * 2160
    assert capture.capture_frame(READ_REGIONS) is partial
