"""
strategy_chart.py -- F1-style tyre strategy chart: one row per plan, a thin line per stint in the
compound's colour, a ring badge where each stint starts, the stint length on its line and a chequered
flag at the finish.
Driven laps are full colour; planned laps are dimmed. Draws race_view.strategy_rows() output.
All sizes come from the label font's line height, so the chart scales with the window.
"""

import tkinter as tk
from tkinter import font as tkfont

from gui import theme

FLAG_W = 1.6  # flag width in badge radii
FLAG_GAP = 0.5  # space between the last stint line and the flag pole, in badge radii
FLAG_COLS, FLAG_ROWS = 4, 3


def _blend(colour, into, amount):
    a = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(into[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b, strict=True))


class StrategyChart(tk.Canvas):
    def __init__(self, parent, label_font, note_font):
        super().__init__(parent, bg=theme.SURFACE, highlightthickness=0, height=160)
        self._label, self._note = label_font, note_font
        self._badge = tkfont.Font(family=theme.DISPLAY_FONT, size=-12, weight="bold")
        self._number = tkfont.Font(family=theme.DISPLAY_FONT, size=-11)
        self._data = ([], None, 0)
        self.bind("<Configure>", lambda e: self._draw())

    def show(self, rows, total_laps, current_lap):
        self._data = (rows, total_laps, current_lap)
        self._draw()

    def _draw(self):
        self.delete("all")
        rows, total, current = self._data
        u = self._label.metrics("linespace")
        width, height = self.winfo_width(), self.winfo_height()
        self.configure(height=max(3, len(rows)) * round(3.4 * u))
        if not rows or not total or width < 20 * u:
            self.create_text(
                u,
                u,
                anchor="w",
                fill=theme.MUTED,
                font=self._note,
                text="Strategy appears once a track and race length are set.",
            )
            return
        row_h = max(3.2 * u, min(5 * u, height / len(rows)))
        r = 0.8 * u
        self._badge.configure(size=-round(r * 1.15))
        self._number.configure(size=-round(r * 1.05))
        label_w = max(self._label.measure(row["label"].upper()) for row in rows) + 2 * u
        x0, x1 = label_w + r, width - (FLAG_GAP + FLAG_W) * r - 6
        x = lambda lap: x0 + (x1 - x0) * lap / total

        for i, row in enumerate(rows):
            y = i * row_h + row_h / 2
            self.create_text(
                u * 0.5, y - 0.5 * u, anchor="w", text=row["label"].upper(), fill=theme.TEXT, font=self._label
            )
            self.create_text(u * 0.5, y + 0.55 * u, anchor="w", text=row["note"], fill=theme.MUTED, font=self._note)
            if i:
                self.create_line(u * 0.5, i * row_h, width - 4, i * row_h, fill=theme.BORDER)
            self._row(row["segments"], y, x, r, total)

        if current:
            cx = x(current - 1)
            self.create_line(cx, 2, cx, len(rows) * row_h - 2, fill=theme.MUTED, dash=(2, 3))

    def _row(self, segments, y, x, r, total):
        line_w = max(3, round(r / 3))
        for n, seg in enumerate(segments):
            colour = theme.COMPOUND_COLOURS.get(seg["compound"], theme.MUTED)
            dim = _blend(colour, theme.SURFACE, 0.55)
            start, end = x(seg["start"] - 1), x(seg["end"])
            done = seg["done_until"]
            split = x(done) if done is not None else start
            if split > start:
                self.create_line(start, y, split, y, fill=colour, width=line_w)
            if end > split:
                self.create_line(split, y, end, y, fill=dim, width=line_w)
            laps = seg["end"] - seg["start"] + 1
            last = n == len(segments) - 1
            if end - start > 6 * r:
                mid = (start + end) / 2 + r / 2
                w = self._number.measure(str(laps)) / 2 + r * 0.4
                self.create_rectangle(mid - w, y - r * 0.8, mid + w, y + r * 0.8, fill=theme.SURFACE, outline="")
                self.create_text(mid, y, text=str(laps), fill=theme.TEXT, font=self._number)
            self.create_oval(start - r, y - r, start + r, y + r, fill=theme.BG, outline=colour, width=2)
            self.create_text(start, y, text=seg["compound"][0].upper(), fill=theme.TEXT, font=self._badge)
            if last and seg["end"] == total:
                self._flag(end + FLAG_GAP * r, y, r, finished=done == total)
            elif last:
                self.create_oval(end - r / 3, y - r / 3, end + r / 3, y + r / 3, fill=dim, outline="")

    def _flag(self, x, y, r, finished):
        """Chequered flag on a pole at the finish; bright once the race is done, dim while still planned."""
        light, dark = (theme.TEXT, theme.BG) if finished else (theme.MUTED, theme.SURFACE_2)
        self.create_line(x, y + r, x, y - r, fill=light, width=2)
        cell = FLAG_W * r / FLAG_COLS
        for row in range(FLAG_ROWS):
            for col in range(FLAG_COLS):
                left, top = x + 1 + col * cell, y - r + row * cell
                fill = light if (row + col) % 2 == 0 else dark
                self.create_rectangle(left, top, left + cell, top + cell, fill=fill, outline="")
