"""profiler.py -- per-stage timings for the telemetry loop, logged as averages every REPORT_EVERY_SEC."""

import logging
import time
from collections import defaultdict
from contextlib import contextmanager, nullcontext

log = logging.getLogger(__name__)
REPORT_EVERY_SEC = 10.0


class LoopProfiler:
    def __init__(self, enabled):
        self.enabled = enabled
        self._totals = defaultdict(float)
        self._cycles = 0
        self._started = time.perf_counter()

    def stage(self, name):
        """with profiler.stage("capture"): ... adds the block's time to that stage. No-op when disabled."""
        return self._timed(name) if self.enabled else nullcontext()

    @contextmanager
    def _timed(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self._totals[name] += time.perf_counter() - start

    def end_cycle(self):
        if not self.enabled:
            return
        self._cycles += 1
        elapsed = time.perf_counter() - self._started
        if elapsed < REPORT_EVERY_SEC:
            return
        log.info(self.report(elapsed))
        self._totals.clear()
        self._cycles = 0
        self._started = time.perf_counter()

    def report(self, elapsed):
        """'120 cycles at 12.0 Hz, 41.2 ms work/cycle | fields 18.0  capture 12.1 ...' (ms per cycle, slowest first)."""
        per_cycle = {name: total / self._cycles * 1000 for name, total in self._totals.items()}
        stages = "  ".join(f"{name} {ms:.1f}" for name, ms in sorted(per_cycle.items(), key=lambda kv: -kv[1]))
        return (
            f"profile: {self._cycles} cycles at {self._cycles / elapsed:.1f} Hz, "
            f"{sum(per_cycle.values()):.1f} ms work/cycle | {stages}"
        )
