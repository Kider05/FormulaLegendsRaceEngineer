"""race_tab.py -- the Race tab: session bar, Live / Strategy / Fuel cards and the tyre strategy chart."""

import tkinter as tk
from tkinter import ttk

from gui import race_view, theme
from gui.strategy_chart import StrategyChart
from storage import race_db


class RaceTab:
    def __init__(self, frame, app):
        self.app = app
        font = app.font
        mono = font(theme.MONO_FONT, 10)
        frame.columnconfigure((0, 1, 2), weight=1, uniform="race")
        frame.rowconfigure(2, weight=1)

        bar = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(10, 6))
        self.ready_button = ttk.Button(bar, text="Ready to Race", style="Accent.TButton", command=app.toggle)
        self.ready_button.pack(side="right")
        self.save_button = ttk.Button(bar, text="Save Race Data", style="Good.TButton", command=app.save_race_data)
        self.status_label = ttk.Label(bar, text="● Not running", style="Bar.TLabel")
        self.status_label.pack(side="right", padx=(0, 14))
        self._build_session_fields(bar)

        live = theme.card(frame, "Live")
        live.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 6))
        tyre_row = ttk.Frame(live, style="Card.TFrame")
        tyre_row.pack(fill="x")
        self.compound_dot = theme.CompoundDot(tyre_row, size=14, font=mono)
        self.compound_dot.pack(side="left", anchor="n", padx=(0, 8), pady=4)
        self._dots = [self.compound_dot]
        self.stint_label = ttk.Label(
            tyre_row,
            text="No laps yet.",
            style="Card.TLabel",
            font=font(theme.DISPLAY_FONT, 12, "bold"),
            justify="left",
        )
        self.stint_label.pack(side="left", fill="x")
        self.pit_warning_label = ttk.Label(live, text="", style="Warning.TLabel")
        self.pit_warning_label.pack(anchor="w", pady=(8, 2))
        self.live_plan_label = ttk.Label(live, text="--", style="Card.TLabel", font=mono, justify="left")
        self.live_plan_label.pack(anchor="w", pady=(2, 8))
        ttk.Separator(live).pack(fill="x", pady=(0, 8))
        self.pit_now_label = ttk.Label(live, text="Pit now: --", style="Muted.TLabel", font=mono, justify="left")
        self.pit_now_label.pack(anchor="w")
        self.last_lap_label = ttk.Label(live, text="", style="Muted.TLabel", font=mono)
        self.last_lap_label.pack(anchor="w", pady=(4, 0))

        strategy = theme.card(frame, "Strategy")
        strategy.grid(row=1, column=1, sticky="nsew", padx=5, pady=(0, 6))
        self.prerace_label = ttk.Label(
            strategy,
            text="Enter a track and race length to see a plan.",
            style="Card.TLabel",
            font=mono,
            justify="left",
        )
        self.prerace_label.pack(anchor="w", pady=(0, 10))
        ttk.Label(strategy, text="MAX LAPS PER SET", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        self.compound_limit_labels = {}
        for compound in ("soft", "medium", "hard", "wet"):
            row = ttk.Frame(strategy, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            dot = theme.CompoundDot(row, compound, size=10, font=mono)
            dot.pack(side="left", padx=(0, 8))
            self._dots.append(dot)
            label = ttk.Label(row, text=f"{compound.capitalize()}: --", style="Card.TLabel", font=mono)
            label.pack(side="left")
            self.compound_limit_labels[compound] = label

        fuel = theme.card(frame, "Fuel")
        fuel.grid(row=1, column=2, sticky="nsew", padx=(5, 10), pady=(0, 6))
        self.next_fuel_label = ttk.Label(
            fuel, text="Next: --", style="Card.TLabel", font=font(theme.DISPLAY_FONT, 13, "bold")
        )
        self.next_fuel_label.pack(anchor="w", pady=(0, 8))
        ttk.Label(fuel, text="DEFAULT STINTS", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        self.fuel_plan_label = ttk.Label(fuel, text="--", style="Card.TLabel", font=mono, justify="left")
        self.fuel_plan_label.pack(anchor="w", pady=(0, 10))
        ttk.Label(fuel, text="TRACK", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        self.track_stats_label = ttk.Label(
            fuel, text="", style="Muted.TLabel", font=font(theme.MONO_FONT, 9), justify="left"
        )
        self.track_stats_label.pack(anchor="w")
        self.driver_bonus_label = ttk.Label(fuel, text="", style="Muted.TLabel", font=font(theme.UI_FONT, 9, "italic"))
        self.driver_bonus_label.pack(anchor="w", pady=(4, 0))

        chart_card = theme.card(frame, "Tyre strategy")
        chart_card.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 10))
        self.strategy_chart = StrategyChart(chart_card, font(theme.DISPLAY_FONT, 9, "bold"), font(theme.UI_FONT, 8))
        self.strategy_chart.pack(fill="both", expand=True)

    def _build_session_fields(self, bar):
        def field(label, widget, padx=(0, 10)):
            ttk.Label(bar, text=label, style="Bar.TLabel").pack(side="left", padx=(0, 5))
            widget.pack(side="left", padx=padx)
            return widget

        self.track_var = tk.StringVar()
        self.track_combo = field(
            "Track", ttk.Combobox(bar, textvariable=self.track_var, values=race_db.known_tracks(), width=12)
        )
        self.track_combo.bind("<<ComboboxSelected>>", self.app.refresh_track_stats)
        self.track_combo.bind("<KeyRelease>", self.app.refresh_track_stats)
        self.variant_var = tk.StringVar(value="Modern")
        variant = field(
            "Variant",
            ttk.Combobox(
                bar, textvariable=self.variant_var, state="readonly", values=["Vintage", "Classic", "Modern"], width=7
            ),
        )
        variant.bind("<<ComboboxSelected>>", self.app.refresh_track_stats)
        self.race_laps_var = tk.StringVar()
        laps = field("Laps", ttk.Entry(bar, textvariable=self.race_laps_var, width=4))
        laps.bind("<KeyRelease>", self.app.refresh_prerace_plan)
        self.era_stage_var = tk.StringVar(value="Mid")
        field(
            "Era",
            ttk.Combobox(
                bar, textvariable=self.era_stage_var, state="readonly", values=["Early", "Mid", "Late"], width=5
            ),
            padx=(0, 4),
        )
        self.era_decade_var = tk.StringVar(value="20s")
        ttk.Combobox(
            bar, textvariable=self.era_decade_var, state="readonly", values=[f"{d}0s" for d in range(1, 10)], width=4
        ).pack(side="left", padx=(0, 10))
        self.driver_var = tk.StringVar()
        self._driver_bonuses = dict(race_db.known_drivers())
        self.driver_combo = field(
            "Driver",
            ttk.Combobox(bar, textvariable=self.driver_var, values=list(self._driver_bonuses), width=13),
        )
        self.driver_combo.bind("<<ComboboxSelected>>", self._show_driver_bonus)
        self.driver_combo.bind("<KeyRelease>", self._show_driver_bonus)

    # --- session inputs -------------------------------------------------------

    def track(self):
        return self.track_var.get().strip()

    def variant(self):
        return self.variant_var.get().strip().lower() or race_db.DEFAULT_VARIANT

    def race_laps_text(self):
        return self.race_laps_var.get()

    def driver(self):
        return self.driver_var.get().strip() or None

    def era(self):
        """(decade, stage) as saved with a race; None for blanks."""
        return self.era_decade_var.get().strip() or None, self.era_stage_var.get().strip().lower() or None

    def remember_driver(self, name):
        if name and name not in self._driver_bonuses:
            self._driver_bonuses[name] = None
            self.driver_combo.config(values=list(self._driver_bonuses))

    def refresh_tracks(self):
        self.track_combo.config(values=race_db.known_tracks())

    def _show_driver_bonus(self, _event=None):
        self.driver_bonus_label.config(text=self._driver_bonuses.get(self.driver_var.get().strip()) or "")

    # --- display --------------------------------------------------------------

    def show_status(self, text):
        """Status text in the session bar, coloured by what it says."""
        lower = text.lower()
        if "fail" in lower:
            colour = theme.ACCENT
        elif lower.startswith(("[active", "saved")):
            colour = theme.GOOD
        elif lower.startswith(("[", "starting", "stopping")):
            colour = theme.WARN
        else:
            colour = theme.MUTED
        self.status_label.config(text=f"● {text}", foreground=colour)

    def show_running(self, running):
        self.ready_button.config(text="Stop" if running else "Ready to Race")

    def show_save_button(self, visible):
        if visible:
            self.save_button.pack(side="right", padx=(0, 8), before=self.status_label)
        else:
            self.save_button.pack_forget()

    def show_view(self, view, compound):
        self.pit_warning_label.config(text=view["warning"])
        self.live_plan_label.config(text=view["plan"])
        if view["stint"] is not None:
            self.stint_label.config(text=view["stint"])
        self.compound_dot.set(compound)

    def show_last_lap(self, lap):
        if lap is None:
            self.last_lap_label.config(text="")
            return
        number, laptime, compound, tyre, _, pit, pos = race_view.lap_review_row(lap)[:7]
        self.last_lap_label.config(text=f"L{number} {laptime}  {compound[0]}{tyre}%  {pos} {pit}".rstrip())

    def show_fuel(self, next_text, plan_text):
        self.next_fuel_label.config(text=next_text)
        self.fuel_plan_label.config(text=plan_text)

    def show_prerace(self, text):
        self.prerace_label.config(text=text)

    def show_track_stats(self, text):
        self.track_stats_label.config(text=text)

    def show_compound_limits(self, limits):
        for compound, text in limits.items():
            self.compound_limit_labels[compound].config(text=text)

    def show_pit_now(self, text):
        self.pit_now_label.config(text=text)

    def show_chart(self, rows, total_laps, current_lap):
        self.strategy_chart.show(rows, total_laps, current_lap)

    def rescale(self):
        for dot in self._dots:
            dot.rescale()
