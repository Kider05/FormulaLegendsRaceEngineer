"""
telemetry_engine.py -- the capture -> read -> track -> publish loop.

run_telemetry_loop() owns everything that touches the outside world: waiting for the game, grabbing
frames, sleeping. TelemetrySession.step() turns one frame into updates and has no capture or sleep in
it, so it can be driven by tests. Reading lives in hud_reader, lap logic in lap_summary.
"""

import contextlib
import logging
import queue
import time

from strategy.live_strategy import GapSmoother, GapTrend
from telemetry.capture import capture_frame
from telemetry.capture_guard import is_process_running
from telemetry.hud_layout import GAME_EXE_NAME, GAME_WINDOW_TITLE, READ_REGIONS
from telemetry.hud_reader import (
    format_leaderboard_brief,
    read_all_fields,
    read_leaderboard,
    read_pit_fuel,
    read_tyre_compound,
    ui_state_configs,
)
from telemetry.lap_summary import PITSTOP_FUEL_GRACE_SEC, LapTracker, format_summary
from telemetry.profiler import LoopProfiler
from telemetry.telemetry_state import MultiFieldActivityMonitor, TelemetryState
from telemetry.ui_state_detector import NamedStateTracker

ACTIVE_POLL_INTERVAL_SEC = 0.1  # 10 Hz while racing
IDLE_POLL_INTERVAL_SEC = 1.0  # 1 Hz while paused/menu
GAME_NOT_RUNNING_POLL_INTERVAL_SEC = 2.0

# Poll fast near a lap's predicted end. Shrinks the gap between our last
# captured laptime and the true value before the display resets.
HIGH_PRECISION_POLL_INTERVAL_SEC = 0.02  # 50 Hz
HIGH_PRECISION_MARGIN_SEC = 5.0

LEADERBOARD_INTERVAL_SEC = 0.1

# Laptime reading the same this many polls running counts as frozen, not as racing.
STALE_LAPTIME_CYCLES = 5

log = logging.getLogger(__name__)


def _sleep_rest(cycle_start, interval):
    """Sleep what's left of interval after this cycle's work, so the poll rate is the interval."""
    time.sleep(max(0.0, interval - (time.monotonic() - cycle_start)))


class _FrozenLaptime:
    """Spots a laptime that reads cleanly but isn't moving. A pit stop once kept 'hp' and 'laptime'
    both readable and static, which alone looked like racing to the activity monitor."""

    def __init__(self):
        self._last = None
        self._unchanged = 0

    def mask(self, reads):
        """reads for the activity monitor: laptime marked low-confidence once frozen."""
        laptime = reads.get("laptime", (None, True))[0]
        if laptime is not None and laptime == self._last:
            self._unchanged += 1
        else:
            self._unchanged, self._last = 0, laptime
        if self._unchanged < STALE_LAPTIME_CYCLES:
            return reads
        return {**reads, "laptime": (laptime, ["stale"])}


class TelemetrySession:
    """One run from Ready to Stop: every tracker's state plus the queue the GUI reads."""

    def __init__(self, update_queue=None, profile=False):
        self._queue = update_queue
        self._profiler = LoopProfiler(profile)
        self._monitor = MultiFieldActivityMonitor(min_good_fields=2)
        self._state = TelemetryState()
        self._lap_tracker = LapTracker()
        self._ui_state_tracker = NamedStateTracker()
        self._frozen_laptime = _FrozenLaptime()
        self._gap_smoother = GapSmoother()
        self._gap_trend = GapTrend()
        self._ui_state = None
        self._last_pitstop_time = None
        self._last_leaderboard_read = 0.0
        self._leaderboard = None
        self._was_active = False
        self._finished = False

    def stage(self, name):
        return self._profiler.stage(name)

    def step(self, frame, now):
        """One frame through read -> track -> publish.
        Returns seconds until the next poll, or None once the race has finished."""
        reads = self._read_hud(frame)
        self._monitor.record(self._frozen_laptime.mask(reads))
        for name, (result, low_confidence) in reads.items():
            self._state.update(name, result, low_confidence)

        # Before the lap tracker, so its pit-stop allowances aren't a poll late.
        ui_state = self._update_ui_state(frame, now)
        near_pitstop = self._last_pitstop_time is not None and now - self._last_pitstop_time <= PITSTOP_FUEL_GRACE_SEC
        if now - self._last_leaderboard_read >= LEADERBOARD_INTERVAL_SEC:
            self._update_leaderboard(frame, now)
        with self.stage("tracker"):
            lap_summary = self._lap_tracker.record(reads, now=now, near_pitstop=near_pitstop)
        self._profiler.end_cycle()
        if lap_summary is not None:
            self.log(format_summary(lap_summary))

        if ui_state is not None:
            return self._on_menu_screen(ui_state, lap_summary)
        if self._monitor.is_active():
            return self._on_racing(lap_summary, now)
        return self._on_idle(lap_summary)

    def close(self):
        """Finish the lap in progress (else it's lost), unless the race already finished, and signal stop."""
        final_summary = None
        if not self._finished:
            final_summary = self._lap_tracker.finalize_current_lap()
            if final_summary is not None:
                self.log(format_summary(final_summary))
            self.log("\nStopped.")
        self.publish(must_deliver=True, stopped=True, lap_summary=final_summary)

    # --- output ---------------------------------------------------------------

    def publish(self, must_deliver=False, **update):
        """Push to the GUI queue. Display updates are dropped if it's full;
        must_deliver ones (lap summaries, stop signal) block until there's room."""
        if self._queue is None:
            return
        if must_deliver:
            self._queue.put(update)
            return
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(update)

    def log(self, message, level=logging.INFO):
        """Log + push as log_line for the GUI's debug console."""
        log.log(level, message)
        self.publish(log_line=message)

    def report_waiting(self, message):
        self.log(message)
        self.publish(status=message)

    def _report(self, mode, lap_summary):
        fields = self._state.snapshot()
        self.log(
            f"[{mode}]   last known: {fields}\n    LB: {format_leaderboard_brief(self._leaderboard)}",
            level=logging.DEBUG,
        )
        self.publish(must_deliver=lap_summary is not None, mode=mode, fields=fields, lap_summary=lap_summary)

    # --- reading --------------------------------------------------------------

    def _read_hud(self, frame):
        with self.stage("fields"):
            reads = read_all_fields(frame)
        with self.stage("compound"):
            compound, _ = read_tyre_compound(frame)
        reads["tyre_compound"] = (compound or "", [] if compound else ["?"])
        return reads

    def _update_ui_state(self, frame, now):
        """Pause / pit-stop screen, with hysteresis: a real detection can flap for dozens of cycles
        (seen: a water streak degrading the match in a rainy race)."""
        with self.stage("ui_state"):
            ui_state, scores = self._ui_state_tracker.update(frame, ui_state_configs())
        previous, self._ui_state = self._ui_state, ui_state
        if ui_state != previous:
            self.log(
                f"  (UI-state changed: {previous!r} -> {ui_state!r}, "
                f"raw scores this frame: { {k: round(v, 3) for k, v in scores.items()} })"
            )
        if ui_state == "pitstop":
            self._last_pitstop_time = now
            self.publish(pit_fuel=read_pit_fuel(frame))
        elif previous == "pitstop":
            self.publish(must_deliver=True, pit_fuel=None, pit_stop_ended=True)
        return ui_state

    def _update_leaderboard(self, frame, now):
        self._last_leaderboard_read = now
        with self.stage("leaderboard"):
            leaderboard = read_leaderboard(frame)
        leaderboard["smoothed"] = self._gap_smoother.update(leaderboard, now)
        leaderboard["pace"] = self._gap_trend.update(
            leaderboard["smoothed"], now, self._lap_tracker.average_completed_laptime_sec
        )
        self._lap_tracker.record_leaderboard(leaderboard)
        self._leaderboard = leaderboard
        self.publish(leaderboard=leaderboard)

    # --- what the frame showed -> report and next poll interval ----------------

    def _on_menu_screen(self, ui_state, lap_summary):
        self._report(f"idle ({ui_state})", lap_summary)
        self._was_active = False
        return ACTIVE_POLL_INTERVAL_SEC if ui_state == "pitstop" else IDLE_POLL_INTERVAL_SEC

    def _on_racing(self, lap_summary, now):
        """Polls fast near the lap's predicted end, to catch the timer just before it resets."""
        self._report("active", lap_summary)
        self._was_active = True
        predicted = self._lap_tracker.predicted_lap_duration_sec
        lap_start = self._lap_tracker.lap_start_time
        if predicted is not None and lap_start is not None and now - lap_start >= predicted - HIGH_PRECISION_MARGIN_SEC:
            return HIGH_PRECISION_POLL_INTERVAL_SEC
        return ACTIVE_POLL_INTERVAL_SEC

    def _on_idle(self, lap_summary):
        """HUD gone with no known screen. Straight after racing, on the final lap with a plausible
        laptime, that is the results screen: finish the last lap now (nothing else ever would)."""
        was_active, self._was_active = self._was_active, False
        finish = None
        if was_active and self._lap_tracker.is_final_lap_and_plausible():
            finish = self._lap_tracker.finalize_current_lap(incomplete=False)
        if finish is None:
            self._report("idle", lap_summary)
            return IDLE_POLL_INTERVAL_SEC
        self.log(f"Race finished! {format_summary(finish)}")
        self._report("race finished", finish)
        self._finished = True
        self.log("Race finished -- stopping automatically.")
        return None


def _grab_frame(session):
    """The game's frame, or None (after telling the GUI why) while the game or its window is missing."""
    if not is_process_running(GAME_EXE_NAME):
        session.report_waiting(f"'{GAME_EXE_NAME}' not found -- waiting for it to start...")
        return None
    with session.stage("capture"):
        frame = capture_frame(READ_REGIONS)
    if frame is None:
        session.report_waiting(f"Window '{GAME_WINDOW_TITLE}' not found -- waiting...")
    return frame


def run_telemetry_loop(stop_event, update_queue=None, profile=False):
    """
    Runs until stop_event is set or the race finishes. Meant for a background thread: Tkinter needs
    the main thread, so everything reaches the GUI as dicts on update_queue.
    profile: log per-stage timings every few seconds (see profiler.py).
    """
    session = TelemetrySession(update_queue, profile)
    try:
        while not stop_event.is_set():
            cycle_start = time.monotonic()
            frame = _grab_frame(session)
            if frame is None:
                time.sleep(GAME_NOT_RUNNING_POLL_INTERVAL_SEC)
                continue
            interval = session.step(frame, time.time())
            if interval is None:
                break
            _sleep_rest(cycle_start, interval)
    finally:
        session.close()
