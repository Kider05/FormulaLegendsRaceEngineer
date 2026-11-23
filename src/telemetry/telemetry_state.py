"""
telemetry_state.py

Two related concerns, handled together because they naturally overlap:

  1. Race-active detection -- don't waste capture/read work during a pause,
     pit stop, menu, or pre-race procedure.
  2. Confidence-gated state -- a bad/low-confidence read should never
     overwrite a previously-good value. The stored state only updates on
     a confident read.

"""


# ---------------------------------------------------------------------------
# 1. Race-active detection
# ---------------------------------------------------------------------------


class MultiFieldActivityMonitor:
    """
    Hysteresis on "is the race HUD active", judged from ALL fields each
    cycle rather than one canary field.
    "Every field failed this cycle" is a much stronger, more specific
    signal that you're in a menu/pause/pit-stop screen than any single
    field failing on its own (which could just be one bad frame on an
    otherwise-fine HUD).

    Usage:
        monitor = MultiFieldActivityMonitor()
        ...
        reads = {
            "lap": (lap_result, lap_low_conf),
            "laptime": (laptime_result, laptime_low_conf),
            "hp": (hp_result, hp_low_conf),
            "fuel": (fuel_result, fuel_low_conf),
            "tyre": (tyre_result, tyre_low_conf),
        }
        monitor.record(reads)

        if monitor.is_active():
            ...
    """

    def __init__(self, consecutive_misses_to_go_idle=3, consecutive_hits_to_go_active=2, min_good_fields=1):
        self._misses_needed = consecutive_misses_to_go_idle
        self._hits_needed = consecutive_hits_to_go_active
        # A cycle counts as a "hit" if at least this many fields read
        # confidently. Default 1 -- "at least SOMETHING is reading fine"
        # is enough to call the cycle active; only when NOTHING reads do
        # we start counting toward idle.
        self._min_good_fields = min_good_fields
        self._consecutive_misses = 0
        self._consecutive_hits = 0
        self._active = True

    def record(self, reads):
        """reads: dict of field_name -> (result, low_confidence)"""
        good_count = 0
        for result, low_confidence in reads.values():
            if bool(result) and not low_confidence and "?" not in result:
                good_count += 1

        is_good_cycle = good_count >= self._min_good_fields

        if is_good_cycle:
            self._consecutive_hits += 1
            self._consecutive_misses = 0
            if not self._active and self._consecutive_hits >= self._hits_needed:
                self._active = True
        else:
            self._consecutive_misses += 1
            self._consecutive_hits = 0
            if self._active and self._consecutive_misses >= self._misses_needed:
                self._active = False

    def is_active(self):
        return self._active


# ---------------------------------------------------------------------------
# 2. Confidence-gated state store
# ---------------------------------------------------------------------------


class TelemetryState:
    """
    Holds the last known-good value for each field. A field only updates
    when a read comes back confident -- a low-confidence or '?'-containing
    read is logged but does NOT overwrite the stored value, so a single
    bad frame (motion blur, a HUD flicker, an odd lighting moment) can't
    corrupt data a consumer (like a race-engineer display) is relying on.
    """

    def __init__(self):
        self._values = {}  # field_name -> last known-good value (string)
        self._miss_streak = {}  # field_name -> consecutive bad-read count

    def update(self, field_name, result, low_confidence):
        is_good_read = bool(result) and not low_confidence and "?" not in result

        if is_good_read:
            self._values[field_name] = result
            self._miss_streak[field_name] = 0
            return True
        else:
            self._miss_streak[field_name] = self._miss_streak.get(field_name, 0) + 1
            return False

    def get(self, field_name, default=None):
        return self._values.get(field_name, default)

    def miss_streak(self, field_name):
        """How many consecutive bad reads this field has had in a row."""
        return self._miss_streak.get(field_name, 0)

    def snapshot(self):
        """All current known-good values, for logging/display."""
        return dict(self._values)
