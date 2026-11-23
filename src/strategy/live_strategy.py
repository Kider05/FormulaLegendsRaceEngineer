"""live_strategy.py -- in-race strategy logic. Pure functions/classes, no capture or GUI."""

from collections import deque
from statistics import median

GAP_WINDOW_SEC = 2.0
GAP_MIN_SAMPLES = 3


class GapSmoother:
    """
    Median gap per car position over the last window_sec, from
    hud_reader.read_leaderboard() snapshots.

    Positions are relative to the player, so all history is dropped when the
    player's position changes. A position needs min_samples reads inside the
    window before it's reported, so a lone bad read is never output.
    """

    def __init__(self, window_sec=GAP_WINDOW_SEC, min_samples=GAP_MIN_SAMPLES):
        self.window_sec = window_sec
        self.min_samples = min_samples
        self._history = {}
        self._player_pos = None

    def reset(self):
        self._history.clear()
        self._player_pos = None

    def update(self, snapshot, now):
        """Add one snapshot's clean gaps; returns smoothed(), see there."""
        position = snapshot.get("position")
        if position is not None and position != self._player_pos:
            self._history.clear()
            self._player_pos = position

        for row in snapshot.get("rows", []):
            if row["is_player"] or row["pitting"] or row["pos"] is None or row["gap_sec"] is None:
                continue
            self._history.setdefault(row["pos"], deque()).append((now, row["gap_sec"]))

        cutoff = now - self.window_sec
        for pos in list(self._history):
            samples = self._history[pos]
            while samples and samples[0][0] < cutoff:
                samples.popleft()
            if not samples:
                del self._history[pos]

        return self.smoothed()

    def smoothed(self):
        """
        {"position", "gaps": {pos: median_gap_sec}, "gap_ahead_sec",
        "gap_behind_sec", "gap_to_leader_sec"}. Gaps keep the HUD's sign (- ahead, + behind);
        the three named fields are positive magnitudes or None.
        """
        gaps = {
            pos: median(g for _, g in samples)
            for pos, samples in self._history.items()
            if len(samples) >= self.min_samples
        }

        def magnitude(pos, sign):
            gap = gaps.get(pos)
            return abs(gap) if gap is not None and gap * sign > 0 else None

        p = self._player_pos
        return {
            "position": p,
            "gaps": gaps,
            "gap_ahead_sec": magnitude(p - 1, -1) if p and p > 1 else None,
            "gap_behind_sec": magnitude(p + 1, 1) if p else None,
            "gap_to_leader_sec": magnitude(1, -1) if p and p > 1 else None,
        }


TREND_WINDOW_LAPS = 1.0
TREND_MIN_SPAN_LAPS = 0.5
TREND_SAMPLE_SEC = 0.5
TREND_MIN_SAMPLES = 5
MAX_PACE_DIFF_PER_LAP = 3.0
GAP_JUMP_SEC = 1.0
GAP_JUMP_RATE = 0.3


class GapTrend:
    """
    How fast each position's gap is changing, as seconds per lap the car
    loses to the player (positive = falling back, negative = closing in).

    Fed GapSmoother output. Fits a least-squares line to that position's gaps
    over the last TREND_WINDOW_LAPS, sampled every TREND_SAMPLE_SEC. Needs at
    least TREND_MIN_SPAN_LAPS of history; clamped to +/-MAX_PACE_DIFF_PER_LAP.

    Positions, not cars, are tracked, so:
      - all history is dropped when the player's position changes;
      - a position's history is dropped when its gap jumps by more than
        GAP_JUMP_SEC + GAP_JUMP_RATE * elapsed (a different car moved in);
      - the result is the faster-looking of the full-window and recent
        half-window fits, so a quicker car that just overtook isn't diluted.
    """

    def __init__(self):
        self._history = {}
        self._player_pos = None
        self._last_sample_time = None

    def update(self, smoothed, now, lap_time_sec):
        """Returns {pos: loss_per_lap_sec} for positions with enough history."""
        position = smoothed.get("position")
        if position is not None and position != self._player_pos:
            self._history.clear()
            self._player_pos = position
        if not lap_time_sec:
            return {}

        if self._last_sample_time is None or now - self._last_sample_time >= TREND_SAMPLE_SEC:
            self._last_sample_time = now
            for pos, gap in smoothed["gaps"].items():
                samples = self._history.setdefault(pos, deque())
                if samples:
                    last_t, last_gap = samples[-1]
                    if abs(gap - last_gap) > GAP_JUMP_SEC + GAP_JUMP_RATE * (now - last_t):
                        samples.clear()
                samples.append((now, gap))

        cutoff = now - TREND_WINDOW_LAPS * lap_time_sec
        result = {}
        for pos in list(self._history):
            samples = self._history[pos]
            while samples and samples[0][0] < cutoff:
                samples.popleft()
            if not samples:
                del self._history[pos]
                continue
            full = _loss_per_lap(samples, lap_time_sec, TREND_MIN_SPAN_LAPS)
            if full is None:
                continue
            half_cutoff = now - TREND_WINDOW_LAPS * lap_time_sec / 2
            recent = _loss_per_lap([x for x in samples if x[0] >= half_cutoff], lap_time_sec, TREND_MIN_SPAN_LAPS / 2)
            result[pos] = full if recent is None else min(full, recent)
        return result


def _loss_per_lap(samples, lap_time_sec, min_span_laps):
    if len(samples) < TREND_MIN_SAMPLES or samples[-1][0] - samples[0][0] < min_span_laps * lap_time_sec:
        return None
    n = len(samples)
    mean_t = sum(t for t, _ in samples) / n
    mean_g = sum(g for _, g in samples) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in samples)
    if var_t == 0:
        return None
    slope = sum((t - mean_t) * (g - mean_g) for t, g in samples) / var_t
    loss = slope * lap_time_sec
    return max(-MAX_PACE_DIFF_PER_LAP, min(MAX_PACE_DIFF_PER_LAP, loss))


MAX_POSITIONS_CHECKED = 2
PIT_LAPS = 2


def effective_pit_loss(pit_loss_sec, loss_per_lap_sec=None):
    """
    Gap a chaser closes while you pit. The stop spans PIT_LAPS laps (in + out),
    during which a chaser losing loss_per_lap_sec to you per lap claws back less.
    """
    return pit_loss_sec - PIT_LAPS * (loss_per_lap_sec or 0.0)


def positions_lost_if_pit(smoothed, pit_loss_sec, total_cars=None, pace=None):
    """
    How many cars behind would pass during a stop right now: 0, 1 or 2 (2 = "2+").

    Walks back from the car directly behind; a car passes if its gap is under
    its effective_pit_loss() (pace: GapTrend output; missing = no correction).
    Stops at the first car that wouldn't pass, the last car in the race, or
    a car whose gap is unknown.

    Returns {"lost", "label", "margin_sec", "effective_loss_sec", "chaser_loss_per_lap"}.
    label is "0", "1", "2+", "1+" (car 2 back unknown) or "?" (nothing known).
    margin_sec is how far the first non-passing car is outside its effective loss.
    The last two describe the car directly behind.
    """
    pace = pace or {}
    result = {"lost": None, "label": "?", "margin_sec": None, "effective_loss_sec": None, "chaser_loss_per_lap": None}
    position = smoothed.get("position")
    if pit_loss_sec is None or position is None:
        return result

    result["chaser_loss_per_lap"] = pace.get(position + 1)
    result["effective_loss_sec"] = effective_pit_loss(pit_loss_sec, pace.get(position + 1))

    lost = 0
    for offset in range(1, MAX_POSITIONS_CHECKED + 1):
        behind = position + offset
        if total_cars is not None and behind > total_cars:
            return {**result, "lost": lost, "label": str(lost)}
        gap = smoothed["gaps"].get(behind)
        if gap is None or gap <= 0:
            if lost == 0:
                return result
            return {**result, "lost": lost, "label": f"{lost}+"}
        loss = effective_pit_loss(pit_loss_sec, pace.get(behind))
        if gap >= loss:
            return {**result, "lost": lost, "label": str(lost), "margin_sec": gap - loss}
        lost += 1

    return {**result, "lost": lost, "label": f"{lost}+"}
