"""Whole-window GUI flow: live laps, condition reports, lap review, save, saved-race edits, reset.
Voice, audio, the AI, settings and the telemetry thread are stubbed; the database is a temp file.
Skipped where Tk can't open a display."""

import queue
import tkinter as tk

import pytest

from engineer import ai_engineer, audio
from gui import app as app_module
from gui import radio as radio_module
from gui import theme
from gui.tabs.analysis_tab import CURRENT_RACE_LABEL
from helpers import dry_laps
from storage import race_db, settings


class FakeVoice:
    status = "off"

    def __init__(self):
        self.results = queue.Queue()

    def start(self):
        pass

    def stop(self):
        pass


class FakeSpeaker:
    def __init__(self, volume=80):
        self.enabled, self.volume, self.spoken = True, volume, []

    def say(self, phrase):
        self.spoken.append(phrase)

    def say_answer(self, sentences):
        self.spoken.extend(sentences)

    def prepare(self, phrase):
        pass


def live_lap(n, compound="medium"):
    lap = dry_laps(1, compound)[0]
    lap.update(lap=n, laptime=f"00:{lap['laptime_sec']:06.3f}", lap_delta_sec=None, avg_laptime_sec=46.0)
    return lap


@pytest.fixture
def gui(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    monkeypatch.setattr(race_db, "DB_PATH", tmp_path / "race.db")
    monkeypatch.setattr(settings, "load", lambda: dict(settings.DEFAULTS))
    monkeypatch.setattr(settings, "save", lambda values: None)
    monkeypatch.setattr(audio, "Speaker", FakeSpeaker)
    monkeypatch.setattr(radio_module.voice_input, "VoiceInput", FakeVoice)
    monkeypatch.setattr(ai_engineer.Engineer, "check", lambda self: None)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    race_db.add_race_data("Silverstone", dry_laps(6, "hard"))
    window = app_module.RaceEngineerGUI(root)
    yield window
    root.destroy()


def test_race_flow_marks_reviews_saves_and_resets(gui):
    analysis = gui.analysis_tab
    assert analysis.race_var.get().endswith("#1")

    gui.race_tab.track_var.set("Silverstone")
    gui.race_tab.race_laps_var.set("20")
    gui.refresh_track_stats()
    for n in range(1, 5):
        gui.apply_update({"lap_summary": live_lap(n)})
        if n == 2:
            gui.radio.ask("Track is wet")
    assert analysis.race_var.get() == CURRENT_RACE_LABEL
    assert [lap["condition"] for lap in gui.current_race_laps] == [None, None, "wet", "wet"]
    assert "Copy, lap 3 onward wet." in gui.speaker.spoken

    analysis.review_tree.selection_set(["0"])
    analysis.set_excluded(True)
    assert gui.current_race_laps[0]["excluded"] is True

    gui.apply_update({"stopped": True})
    assert gui.race_tab.save_button.winfo_manager() == "pack"
    gui.save_race_data()
    saved_id = analysis.selected_race_id()
    assert saved_id == 2 and gui.current_race_laps == []
    assert [(lap["condition"], lap["excluded"]) for lap in race_db.race_laps(saved_id)] == [
        (None, True),
        (None, False),
        ("wet", False),
        ("wet", False),
    ]

    analysis.review_tree.selection_set(["3"])
    analysis.condition_var.set("drying")
    analysis.set_condition()
    assert race_db.race_laps(saved_id)[3]["condition"] == "drying"

    gui.apply_update({"lap_summary": live_lap(1)})
    gui.reset_race()
    assert gui.current_race_laps == [] and gui.race_tab.status_label.cget("text").endswith("Race reset")


def test_fonts_rescale_with_the_window(gui):
    mono = gui.font(theme.MONO_FONT, 10)
    gui._rescale_fonts()
    assert mono.cget("size") == round(10 * app_module.MIN_FONT_SCALE)
