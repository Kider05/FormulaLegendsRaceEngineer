"""settings_tab.py -- the Settings tab: voice and beep toggles and volumes, and the race reset."""

import tkinter as tk
from tkinter import ttk

from gui import theme


class SettingsTab:
    def __init__(self, frame, app):
        self.app = app
        self._vars = {}
        panel = theme.card(frame, "Audio")
        panel.pack(fill="x", padx=10, pady=(10, 6))
        grid = ttk.Frame(panel, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(2, weight=1)
        text_font = app.font(theme.UI_FONT, 10)
        line = text_font.metrics("linespace")
        for row, (key, label) in enumerate((("voice", "Voice callouts"), ("beep", "Fuel beep"))):
            on = tk.BooleanVar(value=app.settings[f"{key}_on"])
            volume = tk.IntVar(value=app.settings[f"{key}_volume"])
            self._vars[f"{key}_on"], self._vars[f"{key}_volume"] = on, volume
            ttk.Label(grid, text=label, style="Card.TLabel", font=text_font).grid(
                row=row, column=0, sticky="w", pady=6, padx=(0, 24)
            )
            theme.Toggle(grid, on, command=self._changed, height=line, bg=theme.SURFACE).grid(
                row=row, column=1, sticky="w", padx=(0, 24)
            )
            theme.Slider(
                grid,
                volume,
                app.font(theme.UI_FONT, 9),
                command=self._changed,
                length=16 * line,
                height=line,
                bg=theme.SURFACE,
            ).grid(row=row, column=2, sticky="w")

        race = theme.card(frame, "Race")
        race.pack(fill="x", padx=10, pady=6)
        ttk.Button(race, text="Reset race", command=app.reset_race).pack(anchor="w")
        ttk.Label(
            race,
            text="Clears this race's laps, AI history and callouts; restarts tracking if it was running.",
            style="Muted.TLabel",
            font=app.font(theme.UI_FONT, 9),
        ).pack(anchor="w", pady=(6, 0))

    def _changed(self):
        self.app.apply_settings({key: var.get() for key, var in self._vars.items()})
