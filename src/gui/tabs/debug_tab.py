"""debug_tab.py -- the Debug / Console tab: raw HUD reads, leaderboard, lap log and telemetry console output."""

import tkinter as tk
from tkinter import scrolledtext


class DebugTab:
    def __init__(self, frame, app):
        heading, mono = app.font("Segoe UI", 10, "bold"), app.font("Consolas", 10)

        def label(text, pady=(0, 0)):
            tk.Label(frame, text=text, font=heading).pack(anchor="w", padx=10, pady=pady)

        label("Current tyre compound:", pady=(10, 0))
        self.compound_label = tk.Label(frame, text="(unknown)", font=app.font("Consolas", 12))
        self.compound_label.pack(anchor="w", padx=10, pady=(0, 8))
        label("HUD reads:")
        self.fields_label = tk.Label(frame, text="", font=mono, justify="left", anchor="w")
        self.fields_label.pack(anchor="w", padx=10, pady=(0, 8))
        label("Leaderboard:")
        self.leaderboard_label = tk.Label(frame, text="(none)", font=mono, justify="left", anchor="w")
        self.leaderboard_label.pack(anchor="w", padx=10, pady=(0, 8))
        label("Lap history:")
        self.lap_log = scrolledtext.ScrolledText(frame, width=56, height=10, state="disabled", font=mono)
        self.lap_log.pack(padx=10, pady=(2, 8), fill="x")
        label("Console output:")
        self.console = scrolledtext.ScrolledText(
            frame, width=56, height=24, state="disabled", font=app.font("Consolas", 9)
        )
        self.console.pack(padx=10, pady=(2, 10), fill="both", expand=True)

    def show_fields(self, fields):
        self.fields_label.config(text="\n".join(f"{k}: {v}" for k, v in fields.items() if k != "tyre_compound"))
        if fields.get("tyre_compound"):
            self.compound_label.config(text=fields["tyre_compound"])

    def show_leaderboard(self, text):
        self.leaderboard_label.config(text=text)

    def add_lap(self, line):
        _append(self.lap_log, line)

    def clear_laps(self):
        self.lap_log.config(state="normal")
        self.lap_log.delete("1.0", tk.END)
        self.lap_log.config(state="disabled")

    def add_console(self, line):
        _append(self.console, line)
        self.console.update_idletasks()


def _append(box, line):
    box.config(state="normal")
    box.insert(tk.END, line + "\n")
    box.see(tk.END)
    box.config(state="disabled")
