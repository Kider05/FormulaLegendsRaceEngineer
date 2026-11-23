"""
lap_summary.py

Buffers per-poll telemetry for the current lap; on a lap-counter change,
summarizes it and starts a fresh temp file for the next lap.

Lap number is our own count (advances by 1 on each detected change), not
OCR's parsed value. A change is detected by either the live timer resetting
(confirmed over several polls) or OCR's lap field reading a different
number than tracked (confirmed once). LAST LAP is preferred over the live
timer when it arrives within a deadline window after a transition;
otherwise falls back to the peak live-timer value seen during that lap.
"""

import logging
import re
import time
from dataclasses import dataclass

from strategy.race_types import Lap

# hud_reader.read_all_fields() output: {field: (text, low-confidence positions)}
Reads = dict[str, tuple[str | None, list]]

log = logging.getLogger(__name__)

_LAPTIME_RE = re.compile(r"^(\d+):(\d+)\.(\d+)$")
_LAP_FIELD_RE = re.compile(r"^(\d+)\|(\d+)$")

MAX_PLAUSIBLE_CHANGE = 3
PITSTOP_FUEL_GRACE_SEC = 10.0
CONFIRM_AFTER = 3

# plausibility cap scales with elapsed time since last accepted reading
MAX_CHANGE_RATE_PER_SEC = 1.0

FIELD_CONFIRM_AFTER = 5

# max wait after a lap transition for LAST LAP; longer if pit-affected
LAST_LAP_FINALIZE_DELAY_SEC = 0.5
PITSTOP_LAST_LAP_DELAY_SEC = 2.0

# fastest plausible laptime; distinguishes a legit early LAST LAP from a stale one
MIN_PLAUSIBLE_LAP_SEC = 15.0

# live timer under this, after being over it, means it reset at the line
FRESH_LAP_SEC = 2.0


def _clean(result, low_conf):
    """True if a read is present, confident, and free of '?' glyphs."""
    return bool(result) and not low_conf and "?" not in result


def _recover_lap_field(lap_field):
    """Leading '1' misread as '|' (e.g. '15|22' -> '|5|22'): substitute it back."""
    if lap_field and lap_field.startswith("|"):
        candidate = "1" + lap_field[1:]
        if _LAP_FIELD_RE.match(candidate):
            return candidate
    return lap_field


def parse_current_lap(lap_field):
    """'12|25' -> 12, or None if unparseable."""
    if not lap_field:
        return None
    m = _LAP_FIELD_RE.match(_recover_lap_field(lap_field))
    return int(m.group(1)) if m else None


def parse_total_laps(lap_field):
    """'12|25' -> 25, or None if unparseable."""
    if not lap_field:
        return None
    m = _LAP_FIELD_RE.match(_recover_lap_field(lap_field))
    return int(m.group(2)) if m else None


def parse_int_field(value):
    """'73' -> 73, or None if unparseable (tyre/fuel/hp)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_laptime_seconds(laptime_field):
    """'MM:SS.mmm' -> seconds as float, e.g. '01:23.456' -> 83.456."""
    if not laptime_field:
        return None
    m = _LAPTIME_RE.match(laptime_field)
    if not m:
        return None
    minutes, seconds, millis = m.groups()
    frac = int(millis) / (10 ** len(millis))
    return int(minutes) * 60 + int(seconds) + frac


class _PlausibleFieldTracker:
    def __init__(self, enforce_decrease_only):
        self.value = None
        self.enforce_decrease_only = enforce_decrease_only
        self._pending_value = None
        self._pending_count = 0
        self._last_update_time = None

    def update(self, new_value, now=None, relaxed=False):
        """Returns (accepted, reason). reason set only when rejected."""
        if self.value is None or new_value == 100 or relaxed:
            return self._accept(new_value, now)

        elapsed = now - self._last_update_time if now is not None and self._last_update_time is not None else 0
        effective_cap = max(MAX_PLAUSIBLE_CHANGE, MAX_CHANGE_RATE_PER_SEC * elapsed)
        if self.enforce_decrease_only and new_value > self.value:
            reason = f"{new_value} is an increase from {self.value} (tyre only decreases, except reset to 100)"
        elif abs(new_value - self.value) > effective_cap:
            reason = f"jump of {abs(new_value - self.value)} from {self.value} exceeds cap of {effective_cap:.1f}"
        else:
            return self._accept(new_value, now)

        if self._pending_value == new_value:
            self._pending_count += 1
        else:
            self._pending_value, self._pending_count = new_value, 1
        if self._pending_count >= FIELD_CONFIRM_AFTER:
            return self._accept(new_value, now)
        return False, reason

    def _accept(self, value, now):
        self.value, self._last_update_time = value, now
        self._pending_value, self._pending_count = None, 0
        return True, None


@dataclass
class _Lap:
    """One lap's gauge readings. end_*, the timer peak and position are filled in when it closes."""

    num: int | None
    start_time: float
    start_tyre: int | None
    start_fuel: int | None
    start_compound: str | None
    had_pitstop: bool = False
    tyre_before_pit: int | None = None
    fuel_before_pit: int | None = None
    end_tyre: int | None = None
    end_fuel: int | None = None
    end_compound: str | None = None
    peak_sec: float | None = None
    peak_str: str | None = None
    position: int | None = None
    deadline: float | None = None


def _usage(start: int | None, before_pit: int | None, end: int | None) -> tuple[int | None, int]:
    """(used, gained): drop from lap start to the pit stop (or lap end), and the jump at the stop."""
    if start is None:
        return None, 0
    checkpoint = before_pit if before_pit is not None else end
    used = start - checkpoint if checkpoint is not None else None
    gained = max(0, end - before_pit) if before_pit is not None and end is not None else 0
    return used, gained


def _used_or_average(used: float | None, history: list[float], incomplete: bool) -> float | None:
    """A negative drop is a misread: swap in the average so far. Complete laps' drops join history."""
    if used is None:
        return None
    if used < 0:
        return sum(history) / len(history) if history else 0
    if not incomplete:
        history.append(used)
    return used


class _PitLoss:
    """Time lost to a stop: in-lap plus out-lap time over the clean average before the stop.
    Back-to-back stops chain (each out-lap is the next in-lap) until a plain out-lap closes them."""

    def __init__(self):
        self._excess = None
        self._baseline = None
        self._stops = 0

    def update(self, is_in, is_out, lap_sec, baseline):
        """(seconds lost, double stop) on the out-lap that closes a stop, else (None, False)."""
        if is_in and is_out:
            if self._excess is not None and lap_sec is not None:
                self._excess += lap_sec - self._baseline
                self._stops += 1
            else:
                self._excess = self._baseline = None
        elif is_in:
            self._stops = 1
            if baseline is not None and lap_sec is not None:
                self._excess, self._baseline = lap_sec - baseline, baseline
            else:
                self._excess = self._baseline = None
        elif is_out and self._excess is not None and lap_sec is not None:
            lost = self._excess + (lap_sec - self._baseline)
            double = self._stops > 1
            self._excess = self._baseline = None
            return lost, double
        return None, False


def _r3(x):
    return None if x is None else round(x, 3)


class LapTracker:
    """
    Call record(reads, now=<time.time()>) once per poll cycle with the
    reads-shaped dict hud_reader.read_all_fields() returns.
    Returns a lap summary dict once a lap is finalized, else None (not
    necessarily on the poll the transition was detected).
    """

    def __init__(self):
        self._lap = None  # current lap
        self._pending = None  # closed lap waiting for LAST LAP
        self._total_laps = None
        self._lap_candidate = None
        self._lap_candidate_count = 0
        self._tyre = _PlausibleFieldTracker(enforce_decrease_only=True)
        self._fuel = _PlausibleFieldTracker(enforce_decrease_only=False)
        self._compound = None
        self._peak_sec = None
        self._peak_str = None
        self._position = None  # latest clean position this lap
        self._start_position = None
        self._start_position_emitted = False
        self._last_lap_sec = None
        self._last_lap_str = None
        self._last_lap_seen_sec = None
        self._last_good_laptime_sec = None
        self._lap_times = []
        self._clean_lap_times = []  # excludes lap 1 and pit in/out laps
        self._tyre_used_history = []
        self._fuel_used_history = []
        self._previous_lap_pit_in = False
        self._pit_loss = _PitLoss()
        self.last_ignored = []

    @property
    def lap_start_time(self):
        """Wall-clock time.time() the current lap started, or None before the first lap."""
        return self._lap.start_time if self._lap else None

    @property
    def predicted_lap_duration_sec(self):
        """Most recently completed lap's time, as a guess for the current one."""
        return self._lap_times[-1] if self._lap_times else None

    @property
    def average_completed_laptime_sec(self):
        """Average across clean laps (not lap 1, not pit in/out); all completed laps until one exists."""
        laps = self._clean_lap_times or self._lap_times
        return sum(laps) / len(laps) if laps else None

    def is_final_lap_and_plausible(self, tolerance: float = 0.3) -> bool:
        """True if on the final lap and the in-progress laptime is within
        `tolerance` of the average of prior laps."""
        if self._lap is None or self._lap.num is None or self._lap.num != self._total_laps:
            return False
        avg = self.average_completed_laptime_sec
        if self._last_good_laptime_sec is None or not avg or avg <= 0:
            return False
        return abs(self._last_good_laptime_sec - avg) / avg <= tolerance

    def record_leaderboard(self, snapshot: dict) -> None:
        """Keep the latest clean position for the current lap. Call before
        record() so a lap ending this cycle gets it."""
        position = snapshot.get("position")
        if position is not None:
            self._position = position
            if self._start_position is None and (self._lap is None or self._lap.num in (None, 1)):
                self._start_position = position

    def record(self, reads: Reads, now: float | None = None, near_pitstop: bool = False) -> Lap | None:
        now = now if now is not None else time.time()
        if near_pitstop and self._lap:
            self._lap.had_pitstop = True
        self.last_ignored = []

        lap_num = self._read_lap_number(reads)
        laptime_text, laptime_low_conf = reads.get("laptime", (None, True))
        laptime_sec = parse_laptime_seconds(laptime_text) if _clean(laptime_text, laptime_low_conf) else None
        fresh_timer = self._update_peak(laptime_sec, laptime_text)
        lap_changed = self._lap_changed(fresh_timer, lap_num)
        self._read_gauges(reads, now, near_pitstop)
        self._read_last_lap(reads)

        summary = self._resolve_pending(now)
        if self._lap is None or self._lap.num is None:
            self._lap = self._new_lap(lap_num, now)
        elif lap_changed:
            if self._pending is not None:
                summary = self._merge_summary(summary, self._finalize_pending())
            self._pending = self._close_lap()
            self._pending.deadline = now + (
                PITSTOP_LAST_LAP_DELAY_SEC if self._pending.had_pitstop else LAST_LAP_FINALIZE_DELAY_SEC
            )
            self._peak_sec = self._peak_str = self._position = None
            self._lap = self._new_lap(self._lap.num + 1, now, had_pitstop=near_pitstop)
            summary = self._merge_summary(summary, self._resolve_pending(now))

        if laptime_sec is not None:
            self._last_good_laptime_sec = laptime_sec
        self._drop_stale_last_lap(now)
        return summary

    def finalize_current_lap(self, incomplete: bool = True, trust_last_lap: bool = False) -> Lap | None:
        """Force-finalize whatever's outstanding (no natural next-lap transition).

        trust_last_lap: False (default) always uses the peak-timer fallback,
        since the held LAST LAP value has no staleness check here (unlike
        the normal transition path) and may belong to the previous lap, not
        this one. Only pass True if the caller has independently verified
        the LAST LAP value belongs to this exact lap."""
        if self._pending is not None:
            return self._finalize_pending()
        if self._lap is None or self._lap.num is None:
            return None
        return self._summarize(self._close_lap(), incomplete=incomplete, use_last_lap=trust_last_lap)

    # --- per-poll reads -------------------------------------------------------

    def _read_lap_number(self, reads):
        text, low_conf = reads.get("lap", (None, True))
        if not _clean(text, low_conf):
            return None
        total = parse_total_laps(text)
        if total is not None:
            self._total_laps = total
        return parse_current_lap(text)

    def _update_peak(self, laptime_sec, laptime_text):
        """Tracks the timer's peak this lap; True if the timer looks freshly reset at the line."""
        if laptime_sec is not None and (self._peak_sec is None or laptime_sec > self._peak_sec):
            self._peak_sec, self._peak_str = laptime_sec, laptime_text
        return (
            laptime_sec is not None
            and laptime_sec < FRESH_LAP_SEC
            and (self._peak_sec is None or self._peak_sec >= FRESH_LAP_SEC)
        )

    def _lap_changed(self, fresh_timer, lap_num):
        """Timer reset, or the lap counter reading ahead of our count CONFIRM_AFTER polls running."""
        if self._lap is None or self._lap.num is None:
            return False
        if fresh_timer or lap_num is None or lap_num <= self._lap.num:
            self._lap_candidate, self._lap_candidate_count = None, 0
            return fresh_timer
        if lap_num == self._lap_candidate:
            self._lap_candidate_count += 1
        else:
            self._lap_candidate, self._lap_candidate_count = lap_num, 1
        if self._lap_candidate_count >= CONFIRM_AFTER:
            self._lap_candidate, self._lap_candidate_count = None, 0
            return True
        self.last_ignored.append(f"lap: change to {lap_num!r} not yet confirmed")
        return False

    def _read_gauge(self, reads, name, tracker, now, relaxed=False):
        """Accepted gauge value this poll, or None."""
        text, low_conf = reads.get(name, (None, True))
        value = parse_int_field(text) if _clean(text, low_conf) else None
        if value is None:
            return None
        accepted, reason = tracker.update(value, now=now, relaxed=relaxed)
        if not accepted:
            self.last_ignored.append(f"{name}: ignored {value!r} ({reason})")
            return None
        return value

    def _read_gauges(self, reads, now, near_pitstop):
        tyre = self._read_gauge(reads, "tyre", self._tyre, now)
        fuel = self._read_gauge(reads, "fuel", self._fuel, now, relaxed=near_pitstop)
        if self._lap and not near_pitstop:
            if tyre not in (None, 100):
                self._lap.tyre_before_pit = tyre
            if fuel not in (None, 100):
                self._lap.fuel_before_pit = fuel
        compound, compound_low_conf = reads.get("tyre_compound", (None, True))
        if _clean(compound, compound_low_conf):
            self._compound = compound

    def _read_last_lap(self, reads):
        text, low_conf = reads.get("last_lap_time", (None, True))
        if not _clean(text, low_conf):
            return
        sec = parse_laptime_seconds(text)
        if sec is None:
            self.last_ignored.append(f"last_lap_time: read {text!r} confidently but invalid -- likely a NULLED lap")
        elif sec != self._last_lap_seen_sec:
            self._last_lap_sec, self._last_lap_str, self._last_lap_seen_sec = sec, text, sec

    def _drop_stale_last_lap(self, now):
        """A LAST LAP arriving early in a lap with nothing pending belongs to the previous lap."""
        if (
            self._pending is None
            and self._last_lap_sec is not None
            and self._lap is not None
            and now - self._lap.start_time < MIN_PLAUSIBLE_LAP_SEC
        ):
            self._last_lap_sec = self._last_lap_str = None

    # --- lap lifecycle --------------------------------------------------------

    def _new_lap(self, num, now, had_pitstop=False):
        return _Lap(num, now, self._tyre.value, self._fuel.value, self._compound, had_pitstop)

    def _close_lap(self):
        lap = self._lap
        lap.end_tyre, lap.end_fuel, lap.end_compound = self._tyre.value, self._fuel.value, self._compound
        lap.peak_sec, lap.peak_str, lap.position = self._peak_sec, self._peak_str, self._position
        return lap

    def _resolve_pending(self, now):
        """Finalize the pending lap once LAST LAP has a value or its deadline passed."""
        if self._pending is not None and (self._last_lap_sec is not None or now >= self._pending.deadline):
            return self._finalize_pending()
        return None

    def _finalize_pending(self):
        lap, self._pending = self._pending, None
        return self._summarize(lap)

    @staticmethod
    def _merge_summary(current, candidate):
        """Combine at most one summary per record() call; log+drop any extra."""
        if candidate is None:
            return current
        if current is None:
            return candidate
        log.warning("dropped a lap summary: two transitions within %ss", LAST_LAP_FINALIZE_DELAY_SEC)
        return current

    def _summarize(self, lap: _Lap, incomplete: bool = False, use_last_lap: bool = True) -> Lap:
        assert lap.num is not None, "laps only close once their number has been read"
        tyre_drop, tyre_gained = _usage(lap.start_tyre, lap.tyre_before_pit, lap.end_tyre)
        fuel_drop, fuel_gained = _usage(lap.start_fuel, lap.fuel_before_pit, lap.end_fuel)
        tyre_used = _used_or_average(tyre_drop, self._tyre_used_history, incomplete)
        fuel_used = _used_or_average(fuel_drop, self._fuel_used_history, incomplete)

        if not incomplete and use_last_lap and self._last_lap_sec is not None:
            lap_sec, lap_str = self._last_lap_sec, self._last_lap_str
        else:
            lap_sec, lap_str = lap.peak_sec, lap.peak_str
        self._last_lap_sec = self._last_lap_str = None

        # pit = compound change OR a captured tyre/fuel reset (same-compound stop)
        compound_changed = None not in (lap.start_compound, lap.end_compound) and lap.start_compound != lap.end_compound
        is_pit_in = not incomplete and (tyre_gained > 0 or fuel_gained > 0 or compound_changed)
        is_pit_out = not incomplete and self._previous_lap_pit_in
        self._previous_lap_pit_in = is_pit_in

        baseline = self.average_completed_laptime_sec
        delta = None
        if not incomplete and lap_sec is not None:
            if self._lap_times:
                delta = lap_sec - self._lap_times[-1]
            self._lap_times.append(lap_sec)
            if not (is_pit_in or is_pit_out) and lap.num != 1:
                self._clean_lap_times.append(lap_sec)
        pit_lost, double_stop = self._pit_loss.update(is_pit_in, is_pit_out, lap_sec, baseline)

        summary: Lap = {
            "lap": lap.num,
            "laptime": lap_str,
            "laptime_sec": _r3(lap_sec),
            "tyre_used": _r3(tyre_used),
            "tyre_gained": _r3(tyre_gained),
            "fuel_used": _r3(fuel_used),
            "fuel_gained": _r3(fuel_gained),
            "lap_delta_sec": _r3(delta),
            "avg_laptime_sec": _r3(self.average_completed_laptime_sec),
            "incomplete": incomplete,
            "compound": (lap.start_compound or lap.end_compound) if is_pit_in else lap.end_compound,
            "is_pitstop_lap": is_pit_in or is_pit_out,
            "is_pit_out_lap": is_pit_out,
            "is_double_stop": double_stop,
            "pitstop_time_lost_sec": _r3(pit_lost),
            "position": lap.position,
        }
        if not self._start_position_emitted:
            self._start_position_emitted = True
            if lap.num == 1:
                summary["start_position"] = self._start_position
        return summary


def format_summary(summary: Lap) -> str:
    """Human-readable one-line rendering of a lap summary dict."""
    tag = " [INCOMPLETE]" if summary.get("incomplete") else ""
    parts = [f"Lap {summary['lap']}: {summary['laptime'] or '?'}{tag}"]

    if summary["lap_delta_sec"] is not None:
        sign = "+" if summary["lap_delta_sec"] >= 0 else ""
        parts.append(f"delta {sign}{summary['lap_delta_sec']:.3f}s")

    if summary["avg_laptime_sec"] is not None:
        parts.append(f"avg {summary['avg_laptime_sec']:.3f}s")

    if summary["tyre_used"] is not None:
        parts.append(f"tyre -{summary['tyre_used']}")
    if summary.get("tyre_gained"):
        parts.append(f"tyre +{summary['tyre_gained']} (reset)")

    if summary["fuel_used"] is not None:
        parts.append(f"fuel -{summary['fuel_used']}")
    if summary.get("fuel_gained"):
        parts.append(f"fuel +{summary['fuel_gained']} (refuel)")

    if summary.get("position") is not None:
        parts.append(f"P{summary['position']}")

    return " | ".join(parts)
