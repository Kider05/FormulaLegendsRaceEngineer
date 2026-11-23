"""analysis_tab.py -- the Analysis tab: pick a race (saved or the current one), see its stints and review its laps."""

import tkinter as tk
from tkinter import ttk

from engineer.actions import CONDITIONS
from gui import race_view, theme
from storage import race_db
from strategy import race_analysis

CURRENT_RACE_LABEL = "Current race (unsaved)"
MIN_STINT_ROWS, MAX_STINT_ROWS = 2, 6
STINT_COLUMNS = {
    "stint": "Stint",
    "compound": "Compound",
    "laps": "Laps",
    "avg": "Avg Laptime",
    "best": "Best Laptime",
    "tyre": "Tyre Wear",
    "fuel": "Fuel Used",
    "pit_loss": "Pit Loss",
}
REVIEW_HEADINGS = {
    "lap": "Lap",
    "time": "Time",
    "compound": "Compound",
    "tyre": "Tyre",
    "fuel": "Fuel",
    "pit": "Pit",
    "pos": "Pos",
    "condition": "Condition",
    "excluded": "Excluded",
}


class AnalysisTab:
    def __init__(self, frame, app):
        self.app = app
        self._race_ids = {}
        self._shown_laps = []
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        bar = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        ttk.Label(bar, text="Race", style="Bar.TLabel").pack(side="left", padx=(0, 8))
        self.race_var = tk.StringVar()
        self.race_combo = ttk.Combobox(bar, textvariable=self.race_var, state="readonly", width=40)
        self.race_combo.pack(side="left")
        self.race_combo.bind("<<ComboboxSelected>>", self.load_race)
        ttk.Button(bar, text="Refresh", command=self.refresh_race_list).pack(side="right")

        stints = theme.card(frame, "Stints")
        stints.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.stint_tree = ttk.Treeview(stints, columns=tuple(STINT_COLUMNS), show="headings", height=MIN_STINT_ROWS)
        for col, heading in STINT_COLUMNS.items():
            self.stint_tree.heading(col, text=heading)
            self.stint_tree.column(col, width=85, anchor="center")
        self.stint_tree.pack(fill="x")

        review = theme.card(frame, "Lap review")
        review.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        controls = ttk.Frame(review, style="Card.TFrame")
        controls.pack(fill="x", pady=(0, 8))
        self.condition_var = tk.StringVar(value="wet")
        ttk.Combobox(
            controls, textvariable=self.condition_var, state="readonly", width=10, values=CONDITIONS + ("(clear)",)
        ).pack(side="left")
        ttk.Button(controls, text="Set condition", command=self.set_condition).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Exclude", command=lambda: self.set_excluded(True)).pack(side="left", padx=(18, 0))
        ttk.Button(controls, text="Include", command=lambda: self.set_excluded(False)).pack(side="left", padx=(6, 0))
        ttk.Label(controls, text="Select laps (Ctrl/Shift for several), then apply.", style="Muted.TLabel").pack(
            side="right"
        )

        table = ttk.Frame(review, style="Card.TFrame")
        table.pack(fill="both", expand=True)
        self.review_tree = ttk.Treeview(
            table, columns=race_view.REVIEW_COLUMNS, show="headings", height=12, selectmode="extended"
        )
        for col in race_view.REVIEW_COLUMNS:
            self.review_tree.heading(col, text=REVIEW_HEADINGS[col])
            self.review_tree.column(col, width=70, anchor="center")
        for tree in (self.stint_tree, self.review_tree):
            for compound, colour in theme.COMPOUND_COLOURS.items():
                tree.tag_configure(compound, foreground=colour)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.review_tree.yview)
        self.review_tree.configure(yscrollcommand=scroll.set)
        self.review_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

    def refresh_race_list(self, select=None):
        """select: CURRENT_RACE_LABEL, a saved race id, or None to keep the selection (newest race if gone)."""
        labels = {race_view.race_label(r): r["id"] for r in race_db.list_races()}
        has_current = bool(self.app.current_race_laps)
        self._race_ids = ({CURRENT_RACE_LABEL: None} if has_current else {}) | labels
        self.race_combo.config(values=list(self._race_ids))
        by_id = {race_id: label for label, race_id in labels.items()}
        if select == CURRENT_RACE_LABEL and has_current:
            choice = CURRENT_RACE_LABEL
        elif select in by_id:
            choice = by_id[select]
        elif self.race_var.get() in self._race_ids:
            choice = self.race_var.get()
        else:
            choice = next(iter(self._race_ids), "")
        self.race_var.set(choice)
        self.load_race()

    def showing_current(self):
        return self.race_var.get() == CURRENT_RACE_LABEL

    def selected_race_id(self):
        return self._race_ids.get(self.race_var.get())

    def _laps(self):
        if self.showing_current():
            return self.app.current_race_laps
        race_id = self.selected_race_id()
        return race_db.race_laps(race_id) if race_id is not None else []

    def load_race(self, _event=None):
        laps = self._laps()
        self._shown_laps = laps
        self.stint_tree.delete(*self.stint_tree.get_children())
        stints = race_analysis.compute_stints(laps)
        self.stint_tree.configure(height=min(max(len(stints), MIN_STINT_ROWS), MAX_STINT_ROWS))
        for i, stint in enumerate(stints, start=1):
            self.stint_tree.insert(
                "", tk.END, values=race_view.stint_row(i, stint), tags=((stint["compound"] or "").lower(),)
            )
        self.review_tree.delete(*self.review_tree.get_children())
        for i, lap in enumerate(laps):
            self.review_tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=race_view.lap_review_row(lap),
                tags=((lap.get("compound") or "").lower(),),
            )

    def reload_if_current(self):
        if self.showing_current():
            selected = self.review_tree.selection()
            self.load_race()
            self.review_tree.selection_set([i for i in selected if self.review_tree.exists(i)])

    def _apply(self, condition=None, excluded=None):
        """Edits the selected laps: in memory for the current race, in the database for a saved one."""
        selected = self.review_tree.selection()
        laps = [self._shown_laps[int(i)] for i in selected]
        if not laps:
            return
        if self.showing_current():
            for lap in laps:
                if condition is not None:
                    lap["condition"] = condition or None
                if excluded is not None:
                    lap["excluded"] = excluded
            self.app.refresh_live_state()
        else:
            race_db.review_laps([lap["db_id"] for lap in laps], condition=condition, excluded=excluded)
            self.app.refresh_track_stats()
        self.load_race()
        self.review_tree.selection_set(selected)

    def set_condition(self):
        value = self.condition_var.get()
        self._apply(condition="" if value == "(clear)" else value)

    def set_excluded(self, excluded):
        self._apply(excluded=excluded)
