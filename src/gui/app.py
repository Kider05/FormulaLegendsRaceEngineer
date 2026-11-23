"""
app.py -- the main window. Owns the race state, runs the telemetry thread, and keeps the tabs in sync.
Each tab builds and updates its own widgets (gui/tabs/); the radio link to the AI engineer is gui/radio.py.
"""

import argparse
import logging
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import log_setup
from engineer import audio, callouts
from gui import race_view, theme
from gui.radio import Radio
from gui.tabs.ai_tab import AITab
from gui.tabs.analysis_tab import CURRENT_RACE_LABEL, AnalysisTab
from gui.tabs.debug_tab import DebugTab
from gui.tabs.race_tab import RaceTab
from gui.tabs.settings_tab import SettingsTab
from storage import race_db, settings
from strategy import tyre_model
from strategy.strategy_planner import Planner
from telemetry import telemetry_engine
from telemetry.capture import enable_dpi_awareness
from telemetry.lap_summary import format_summary, parse_laptime_seconds

BASE_WIDTH, BASE_HEIGHT = 1000, 720
MIN_FONT_SCALE, MAX_FONT_SCALE = 0.8, 4.0
FONT_GROWTH = 1.3  # fonts grow this much faster than the window
POLL_MS = 100
log = logging.getLogger(__name__)
TK_NAMED_FONTS = ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont")


class RaceEngineerGUI:
    def __init__(self, root, profile=False):
        self.root = root
        self.profile = profile
        self.root.title("Formula Legends Race Engineer")
        dpi_scale = root.winfo_fpixels("1i") / 96
        self._base_size = (round(BASE_WIDTH * dpi_scale), round(BASE_HEIGHT * dpi_scale))
        self.root.geometry("{}x{}".format(*self._base_size))
        self._fonts = {}
        self._named_font_sizes = {name: tkfont.nametofont(name).cget("size") for name in TK_NAMED_FONTS}
        self._resize_job = None

        self.stop_event = None
        self.update_queue = None
        self.worker_thread = None
        self._restart_after_stop = False
        self.current_race_laps = []
        self.latest_leaderboard = None
        self.latest_fields = {}
        self.current_compound = None
        self.track_condition = None
        self.pit_loss_sec = None
        self.pit_loss_samples = 0
        self.in_pit_window = False
        self.track_races = []
        self.tyre_model = None
        self.live_model = None
        self.planner = None  # track history
        self.live_planner = None  # track history plus this race
        self.view = None
        self.prerace_best = None
        self.settings = settings.load()
        self.speaker = audio.Speaker(self.settings["voice_volume"])
        self.speaker.enabled = self.settings["voice_on"]
        self.callouts = callouts.CalloutTracker()
        self.fuel_beep = callouts.FuelBeep()

        theme.apply(root, self.font)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.race_tab = RaceTab(self._new_tab("Race"), self)
        self.analysis_tab = AnalysisTab(self._new_tab("Analysis"), self)
        self.debug_tab = DebugTab(self._new_tab("Debug / Console"), self)
        self.ai_tab = AITab(self._new_tab("AI"), self)
        self.settings_tab = SettingsTab(self._new_tab("Settings"), self)
        self.refresh_track_stats()
        self.analysis_tab.refresh_race_list()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Configure>", self._on_resize)
        self.radio = Radio(self)
        self.radio.poll()

    def _new_tab(self, title):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=title)
        return frame

    # --- fonts ----------------------------------------------------------------

    def font(self, family, size, style=None):
        """Shared Font object; sizes are at the starting window size and scale with the window."""
        key = (family, size, style)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(
                family=family,
                size=size,
                weight="bold" if style == "bold" else "normal",
                slant="italic" if style == "italic" else "roman",
            )
        return self._fonts[key]

    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(POLL_MS, self._rescale_fonts)

    def _rescale_fonts(self):
        self._resize_job = None
        growth = (self.root.winfo_width() / self._base_size[0] * self.root.winfo_height() / self._base_size[1]) ** 0.5
        scale = max(MIN_FONT_SCALE, min(MAX_FONT_SCALE, 1 + (growth - 1) * FONT_GROWTH))
        for (_, size, _), font in self._fonts.items():
            font.configure(size=round(size * scale))
        for name, size in self._named_font_sizes.items():
            tkfont.nametofont(name).configure(size=round(size * scale))
        row_height = tkfont.nametofont("TkDefaultFont").metrics("linespace") + 6
        ttk.Style(self.root).configure("Treeview", rowheight=row_height)
        self.race_tab.rescale()
        self._refresh_chart()

    # --- derived state and redraws ----------------------------------------------

    def refresh_track_stats(self, _event=None):
        track, variant = self.race_tab.track(), self.race_tab.variant()
        self.pit_loss_sec, self.pit_loss_samples = race_db.median_pit_loss(track, variant) if track else (None, 0)
        self.track_races = race_db.track_laps(track, variant) if track else []
        self.tyre_model = tyre_model.fit_races(self.track_races) if self.track_races else None
        self.planner = Planner.for_model(self.tyre_model, self.pit_loss_sec)
        self.race_tab.show_track_stats(
            race_view.model_text(track, variant, self.tyre_model, self.pit_loss_sec, self.pit_loss_samples)
        )
        self.update_pit_now()
        self.refresh_live_state()
        self.refresh_prerace_plan()

    def refresh_prerace_plan(self, _event=None):
        text, self.prerace_best = race_view.prerace_text(
            self.race_tab.track(), self.race_tab.race_laps_text(), self.planner
        )
        self.race_tab.show_prerace(text)
        self._refresh_chart()

    def refresh_live_state(self):
        """Refit the model with this race's laps, then redraw everything that depends on it."""
        self.live_model = (
            tyre_model.fit_races(self.track_races + [self.current_race_laps], use_last_race_offset=True)
            if self.current_race_laps
            else None
        )
        self.live_planner = Planner.for_model(self.live_model, self.pit_loss_sec)
        self.update_live_plan()
        self.race_tab.show_compound_limits(race_view.compound_limits(self.live_model or self.tyre_model))

    def current_planner(self):
        """Planner with this race's laps once there are any, else the track history's."""
        return self.live_planner or self.planner

    def update_live_plan(self):
        view = race_view.live_view(
            self.race_tab.race_laps_text(),
            self.current_race_laps,
            self.current_compound,
            self.current_planner(),
        )
        self.view = view
        for phrase in callouts.lap_message(view["current_plan"], wet=view["wet_window"])[:1]:
            self.speaker.prepare(phrase)
        self.in_pit_window = view["in_pit_window"]
        self.race_tab.show_view(view, self.current_compound)
        self.race_tab.show_last_lap(self.current_race_laps[-1] if self.current_race_laps else None)
        self._refresh_chart()
        per_lap = race_view.fuel_per_lap(self.current_race_laps, self.track_races)
        self.race_tab.show_fuel(
            race_view.next_fuel_text(view["next_stint"], per_lap), race_view.default_fuel_text(per_lap)
        )

    def update_pit_now(self):
        self.race_tab.show_pit_now(
            race_view.pit_now_text(
                self.latest_leaderboard, self.pit_loss_sec, self.pit_loss_samples, self.in_pit_window
            )
        )

    def _refresh_chart(self):
        laps = self.current_race_laps
        total = race_view.parse_laps(self.race_tab.race_laps_text())
        rows = race_view.strategy_rows(total, laps, self.view, self.prerace_best)
        self.race_tab.show_chart(rows, total, laps[-1]["lap"] + 1 if laps else None)

    def apply_settings(self, values):
        self.settings = values
        settings.save(values)
        self.speaker.enabled = values["voice_on"]
        self.speaker.volume = values["voice_volume"]

    # --- callouts and fuel beep -------------------------------------------------

    def _check_callouts(self, laptime_text):
        plan = self.view["current_plan"] if self.view else None
        places = ""
        if plan is not None and plan["best"]["pit_in"] == 2:
            places = callouts.places_phrase(
                race_view.pit_now_result(self.latest_leaderboard, self.pit_loss_sec, in_pit_window=True)
            )
        laps_done = self.current_race_laps[-1]["lap"] if self.current_race_laps else 0
        avg = self.current_race_laps[-1].get("avg_laptime_sec") if self.current_race_laps else None
        wet = self.view["wet_window"] if self.view else None
        for phrase in self.callouts.update(laps_done + 1, parse_laptime_seconds(laptime_text), avg, plan, places, wet):
            self.speaker.say(phrase)

    def _check_fuel_beep(self, fuel):
        stint = self.view["pit_now_stint"] if self.view else None
        per_lap = race_view.fuel_per_lap(self.current_race_laps, self.track_races)
        target = race_view.fuel_target(stint["laps"], per_lap) if stint else None
        if self.fuel_beep.update(fuel, target) and self.settings["beep_on"]:
            audio.beep(self.settings["beep_volume"])

    # --- race actions -----------------------------------------------------------

    def set_track_condition(self, condition, from_lap):
        """Track state from this lap onward: stamps finished laps >= from_lap and every lap still to come."""
        self.track_condition = condition
        race_view.mark_condition(self.current_race_laps, condition, from_lap)
        self.ai_tab.add(f"(lap {from_lap} onward marked {condition})")
        self.refresh_live_state()
        self.analysis_tab.reload_if_current()

    def set_current_excluded(self, lap_numbers, excluded):
        for lap in self.current_race_laps:
            if lap.get("lap") in lap_numbers:
                lap["excluded"] = excluded
        self.ai_tab.add(f"(laps {', '.join(map(str, lap_numbers))} {'excluded' if excluded else 'included'})")
        self.refresh_live_state()
        self.analysis_tab.reload_if_current()

    def _clear_race(self):
        self.current_race_laps = []
        self.latest_leaderboard = None
        self.current_compound = None
        self.track_condition = None
        self.callouts = callouts.CalloutTracker()
        self.fuel_beep.reset()
        self.radio.reset()
        self.race_tab.show_save_button(False)
        self.race_tab.show_last_lap(None)
        self.debug_tab.clear_laps()
        self.refresh_live_state()
        self.analysis_tab.refresh_race_list()

    def reset_race(self):
        if self.current_race_laps and not messagebox.askyesno(
            "Reset race", "Clear this race's laps and start over? Unsaved laps are lost."
        ):
            return
        if self.running():
            self._restart_after_stop = True
            self.stop()
        else:
            self._clear_race()
            self.race_tab.show_status("Race reset")
        self.ai_tab.add("(race reset)")

    def save_race_data(self):
        track = self.race_tab.track()
        if not track or not self.current_race_laps:
            return
        driver = self.race_tab.driver()
        era_decade, era_stage = self.race_tab.era()
        try:
            race_id = race_db.add_race_data(
                track,
                self.current_race_laps,
                variant=self.race_tab.variant(),
                driver_name=driver,
                era_decade=era_decade,
                era_stage=era_stage,
                starting_position=self.current_race_laps[0].get("start_position"),
            )
        except Exception as exc:
            log.exception("race save failed: %s", exc)
            self.race_tab.show_status("Save failed -- check console, laps kept, try again")
            return
        self.race_tab.remember_driver(driver)
        self.race_tab.refresh_tracks()
        self.current_race_laps = []
        self.analysis_tab.refresh_race_list(select=race_id)
        self.race_tab.show_save_button(False)
        self.race_tab.show_status(f"Saved to {track!r}")
        self.refresh_track_stats()

    # --- telemetry thread -------------------------------------------------------

    def running(self):
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def toggle(self):
        if self.running():
            self.stop()
        else:
            self.start()

    def start(self):
        self._clear_race()
        self.stop_event = threading.Event()
        self.update_queue = queue.Queue(maxsize=200)
        self.worker_thread = threading.Thread(
            target=telemetry_engine.run_telemetry_loop,
            args=(self.stop_event,),
            kwargs={"update_queue": self.update_queue, "profile": self.profile},
            daemon=True,
        )
        self.worker_thread.start()
        self.race_tab.show_running(True)
        self.race_tab.show_status("Starting...")
        self.poll_queue()

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self.race_tab.show_running(False)
        self.race_tab.show_status("Stopping...")

    def poll_queue(self):
        if self.update_queue is None:
            return
        try:
            while True:
                self.apply_update(self.update_queue.get_nowait())
        except queue.Empty:
            pass
        if self.running() or not self.update_queue.empty():
            self.root.after(POLL_MS, self.poll_queue)

    def apply_update(self, update):
        if update.get("status"):
            self.race_tab.show_status(update["status"])
        if "mode" in update:
            self.race_tab.show_status(f"[{update['mode']}]")
        fields = update.get("fields")
        if fields:
            self.latest_fields = fields
            self.debug_tab.show_fields(fields)
            if fields.get("tyre_compound") and fields["tyre_compound"] != self.current_compound:
                self.current_compound = fields["tyre_compound"]
                self.update_live_plan()
        if update.get("leaderboard"):
            self.latest_leaderboard = update["leaderboard"]
            self.debug_tab.show_leaderboard(race_view.leaderboard_text(self.latest_leaderboard))
            self.update_pit_now()
        if update.get("lap_summary"):
            self._add_lap(update["lap_summary"])
        if update.get("log_line") is not None:
            self.debug_tab.add_console(update["log_line"])
        if update.get("pit_stop_ended"):
            self.fuel_beep.reset()
        elif update.get("pit_fuel") is not None:
            self._check_fuel_beep(update["pit_fuel"])
        if fields and fields.get("laptime"):
            self._check_callouts(fields["laptime"])
        if update.get("stopped"):
            self._on_stopped()

    def _add_lap(self, lap):
        lap["condition"] = self.track_condition
        self.current_race_laps.append(lap)
        self.race_tab.show_last_lap(lap)
        self.debug_tab.add_lap(format_summary(lap))
        self.refresh_live_state()
        if len(self.current_race_laps) == 1:
            self.analysis_tab.refresh_race_list(select=CURRENT_RACE_LABEL)
        self.analysis_tab.reload_if_current()

    def _on_stopped(self):
        if self._restart_after_stop:
            self._restart_after_stop = False
            self.start()
            return
        self.race_tab.show_status("Stopped")
        self.race_tab.show_running(False)
        if self.current_race_laps:
            self.race_tab.show_save_button(True)
            self.analysis_tab.refresh_race_list(select=CURRENT_RACE_LABEL)

    def on_close(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self.radio.stop()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Formula Legends race engineer")
    parser.add_argument("--debug", action="store_true", help="log every telemetry cycle")
    parser.add_argument("--profile", action="store_true", help="log telemetry loop stage timings every 10s")
    args = parser.parse_args(argv)
    log_setup.setup(debug=args.debug)
    enable_dpi_awareness()
    root = tk.Tk()
    RaceEngineerGUI(root, profile=args.profile)
    root.mainloop()


if __name__ == "__main__":
    main()
