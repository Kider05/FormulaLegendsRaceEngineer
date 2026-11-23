"""Gap smoothing, gap trends and positions lost if pitting."""

import pytest

from helpers import LAP, feed_trend, smoothed, snapshot
from strategy.live_strategy import (
    MAX_PACE_DIFF_PER_LAP,
    GapSmoother,
    GapTrend,
    effective_pit_loss,
    positions_lost_if_pit,
)


def test_smoother_median_ignores_misreads():
    s = GapSmoother()
    reads = [3.46, 3.57, 2.35, 3.35, 2.41, 3.41, 3.38, 3.42]
    for i, g in enumerate(reads):
        out = s.update(snapshot(12, {13: g}), now=i * 0.17)
    assert out["gaps"][13] == pytest.approx(3.395, abs=0.05)


def test_smoother_needs_min_samples():
    s = GapSmoother()
    out = s.update(snapshot(5, {6: 2.0}), now=0.0)
    assert 6 not in out["gaps"]


def test_smoother_resets_on_position_change():
    s = GapSmoother()
    for i in range(5):
        s.update(snapshot(5, {6: 2.0}), now=i * 0.2)
    out = s.update(snapshot(4, {5: 1.0}), now=1.2)
    assert out["gaps"] == {}
    assert out["position"] == 4


def test_smoother_skips_pitting_and_expires():
    s = GapSmoother()
    for i in range(5):
        s.update(snapshot(5, {6: 2.0, 7: 9.0}, pitting={7}), now=i * 0.2)
    out = s.update(snapshot(5, {}), now=10.0)
    assert out["gaps"] == {}


def test_trend_slower_chaser_is_positive():
    out = feed_trend(lambda t: 10.0 + 2.0 * t / LAP, seconds=LAP)
    assert out[2] == pytest.approx(2.0, abs=0.05)


def test_trend_faster_chaser_is_negative():
    out = feed_trend(lambda t: 10.0 - 1.0 * t / LAP, seconds=LAP)
    assert out[2] == pytest.approx(-1.0, abs=0.05)


def test_trend_needs_half_a_lap():
    out = feed_trend(lambda t: 10.0 + 2.0 * t / LAP, seconds=LAP * 0.4)
    assert 2 not in out


def test_trend_is_clamped():
    out = feed_trend(lambda t: 10.0 + 20.0 * t / LAP, seconds=LAP)
    assert out[2] == MAX_PACE_DIFF_PER_LAP


def test_trend_resets_when_gap_jumps():
    """Car behind pits: next car moves up 9s further back. No history, so no pace yet."""
    out = feed_trend(lambda t: 5.0 if t < LAP else 14.0, seconds=LAP + 2)
    assert 2 not in out


def test_trend_uses_faster_recent_pace_after_overtake():
    """Slow car (+2s/lap) passed mid-window by a fast one (-1s/lap); gap continuous."""

    def gap(t):
        mid = LAP * 0.5
        return 10.0 + 2.0 * t / LAP if t < mid else 10.0 + 2.0 * mid / LAP - 1.0 * (t - mid) / LAP

    out = feed_trend(gap, seconds=LAP)
    assert out[2] == pytest.approx(-1.0, abs=0.1)


def test_trend_without_lap_time_returns_nothing():
    trend = GapTrend()
    assert trend.update(smoothed(1, {2: 10.0}), now=0.0, lap_time_sec=None) == {}


@pytest.mark.parametrize(
    "gaps, total, expected",
    [
        ({6: 20.0}, 14, "0"),
        ({6: 3.0, 7: 18.0}, 14, "1"),
        ({6: 3.0, 7: 9.0}, 14, "2+"),
        ({6: 3.0}, 14, "1+"),
        ({}, 14, "?"),
    ],
)
def test_positions_lost_labels(gaps, total, expected):
    assert positions_lost_if_pit(smoothed(5, gaps), 14.6, total_cars=total)["label"] == expected


def test_last_place_loses_nothing():
    assert positions_lost_if_pit(smoothed(14, {}), 14.6, total_cars=14)["label"] == "0"


def test_second_last_caps_at_one():
    assert positions_lost_if_pit(smoothed(13, {14: 2.0}), 14.6, total_cars=14)["label"] == "1"


def test_no_pit_loss_data_is_unknown():
    assert positions_lost_if_pit(smoothed(5, {6: 3.0}), None)["label"] == "?"


def test_slow_chaser_makes_stop_free():
    """Silverstone test run: 16.7s loss, P2 13.3s back losing ~2.1s/lap -> kept the lead."""
    result = positions_lost_if_pit(smoothed(1, {2: 13.3}), 16.7, total_cars=14, pace={2: 2.1})
    assert result["label"] == "0"
    assert result["effective_loss_sec"] == pytest.approx(12.5)
    assert result["margin_sec"] == pytest.approx(0.8)


def test_same_gap_without_pace_loses_a_place():
    result = positions_lost_if_pit(smoothed(1, {2: 13.3, 3: 30.0}), 16.7, total_cars=14)
    assert result["label"] == "1"


def test_fast_chaser_needs_bigger_gap():
    assert effective_pit_loss(16.7, -1.0) == pytest.approx(18.7)
    result = positions_lost_if_pit(smoothed(1, {2: 17.5, 3: 30.0}), 16.7, total_cars=14, pace={2: -1.0})
    assert result["label"] == "1"
