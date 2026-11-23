"""
radio.py -- the driver's radio link: push-to-talk questions in, AI engineer answers out. Condition reports
("track is wet") are handled instantly without the AI. The AI's tool requests (mark condition, exclude laps)
arrive from its worker thread and are applied on the Tk thread by poll().
"""

import queue
import time

from engineer import actions as engineer_actions
from engineer import ai_engineer, race_state, voice_input
from gui import race_view
from strategy import tyre_model

POLL_MS = 100
ACK_AFTER_SEC = 1.5


class Radio:
    def __init__(self, app):
        self.app = app
        self._waiting = []  # [asked_at, acked] per unanswered question, oldest first
        self._actions = queue.Queue()
        self.engineer = ai_engineer.Engineer()
        self.engineer.check()
        self.voice = voice_input.VoiceInput()
        try:
            self.voice.start()
        except Exception as e:
            self.voice.status = f"unavailable: {e}"

    def poll(self):
        app = self.app
        app.ai_tab.show_status(self.voice.status, self.engineer.status)
        while not self.voice.results.empty():
            text, took = self.voice.results.get_nowait()
            app.ai_tab.add(f"You: {text}  ({took:.1f}s)")
            self.ask(text)
        while not self.engineer.results.empty():
            answer, took, work = self.engineer.results.get_nowait()
            if self._waiting:
                self._waiting.pop(0)
            for code, output in work:
                app.ai_tab.add(f"  calc: {code.strip()}\n    -> {output}")
            app.ai_tab.add(f"Engineer: {answer}  ({took:.1f}s)")
            if not answer.startswith("("):
                app.speaker.say_answer(ai_engineer.split_sentences(answer))
        while not self._actions.empty():
            kind, a, b = self._actions.get_nowait()
            if kind == "condition":
                app.set_track_condition(a, b)
            else:
                app.set_current_excluded(a, b)
        if self._waiting and not self._waiting[0][1] and time.monotonic() - self._waiting[0][0] > ACK_AFTER_SEC:
            self._waiting[0][1] = True
            app.speaker.say_answer(["Copy, we're checking."])
        app.root.after(POLL_MS, self.poll)

    def ask(self, question):
        app = self.app
        laps = app.current_race_laps
        current_lap = (laps[-1]["lap"] if laps else 0) + 1
        report = engineer_actions.parse_condition_report(question, current_lap)
        if report:
            app.set_track_condition(*report)
            answer = f"Copy, lap {report[1]} onward {report[0]}."
            app.ai_tab.add(f"Engineer: {answer}  (instant)")
            app.speaker.say_answer([answer])
            self.engineer.note(question, answer)
            return
        if not self.engineer.status.startswith("ready"):
            self.engineer.check()
            app.ai_tab.add(f"(AI unavailable: {self.engineer.status})")
            return
        planner = app.current_planner()
        snapshot = race_state.build_snapshot(
            app.race_tab.track(),
            race_view.parse_laps(app.race_tab.race_laps_text()),
            laps,
            {**app.latest_fields, "tyre_compound": app.current_compound},
            app.latest_leaderboard,
            app.view,
            planner,
            app.prerace_best,
            race_view.fuel_per_lap(laps, app.track_races),
            tyre_model.current_tyre_pct(laps),
            app.track_condition,
        )
        self._waiting.append([time.monotonic(), False])
        acts = engineer_actions.build(
            current_lap,
            planner=race_state.stint_planner(planner),
            set_condition=lambda c, f: self._actions.put(("condition", c, f)),
            exclude=lambda laps_, out: self._actions.put(("exclude", laps_, out)),
        )
        self.engineer.ask(question, snapshot, acts)

    def reset(self):
        self.engineer.reset()

    def stop(self):
        self.voice.stop()
