"""
theme.py -- colours and ttk styles for the whole app, in one place. apply() styles ttk widgets and
sets option-database defaults so plain tk widgets (Text, Checkbutton, Scale...) match.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#15171b"
SURFACE = "#1d2026"
SURFACE_2 = "#262a31"
BORDER = "#2f343c"
TEXT = "#e6e8eb"
MUTED = "#8b919a"
ACCENT = "#e10600"
GOOD = "#3fb950"
WARN = "#d29922"

COMPOUND_COLOURS = {"soft": "#e10600", "medium": "#ffd12e", "hard": "#f0f0f0", "wet": "#2f6fff"}

UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"
DISPLAY_FONT = "Bahnschrift"


def pick_fonts(root):
    """Fall back to UI_FONT where the display font isn't installed (Bahnschrift ships with Windows 10+)."""
    global DISPLAY_FONT
    if DISPLAY_FONT not in tkfont.families(root):
        DISPLAY_FONT = UI_FONT


def apply(root, font):
    """font(family, size, style=None) -> a shared, window-scaled tkfont.Font."""
    root.configure(bg=BG)
    pick_fonts(root)
    for pattern, value in (
        ("*Background", BG),
        ("*Foreground", TEXT),
        ("*Text.background", SURFACE),
        ("*Entry.background", SURFACE_2),
        ("*Button.background", SURFACE_2),
        ("*Button.relief", "flat"),
        ("*Button.padX", 10),
        ("*Button.padY", 3),
        ("*selectBackground", SURFACE_2),
        ("*selectForeground", TEXT),
        ("*insertBackground", TEXT),
        ("*activeBackground", SURFACE_2),
        ("*activeForeground", TEXT),
        ("*highlightThickness", 0),
        ("*Checkbutton.selectColor", SURFACE_2),
        ("*Scale.troughColor", SURFACE_2),
        ("*Text.relief", "flat"),
        ("*TCombobox*Listbox.background", SURFACE_2),
        ("*TCombobox*Listbox.foreground", TEXT),
        ("*TCombobox*Listbox.selectBackground", BORDER),
        ("*TCombobox*Listbox.relief", "flat"),
        ("*TCombobox*Listbox.borderWidth", 0),
    ):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BG,
        darkcolor=BG,
        troughcolor=SURFACE_2,
        focuscolor=BORDER,
        font=font(UI_FONT, 10),
    )
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Section.TLabel", background=SURFACE, foreground=MUTED, font=font(DISPLAY_FONT, 9, "bold"))
    style.configure("Bar.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Warning.TLabel", background=SURFACE, foreground=ACCENT, font=font(UI_FONT, 14, "bold"))

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(14, 6), borderwidth=0)
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE)],
        foreground=[("selected", TEXT)],
        lightcolor=[("selected", ACCENT)],
        bordercolor=[("selected", ACCENT)],
    )

    style.configure("TButton", background=SURFACE_2, foreground=TEXT, borderwidth=0, padding=(12, 6))
    style.map("TButton", background=[("active", BORDER)])
    style.configure(
        "Accent.TButton", background=ACCENT, foreground="white", font=font(UI_FONT, 10, "bold"), padding=(16, 7)
    )
    style.map("Accent.TButton", background=[("active", "#b80500")])
    style.configure("Good.TButton", background="#238636", foreground="white", font=font(UI_FONT, 10, "bold"))
    style.map("Good.TButton", background=[("active", "#2ea043")])

    for widget in ("TCombobox", "TEntry"):
        style.configure(
            widget,
            fieldbackground=SURFACE_2,
            background=SURFACE_2,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=SURFACE_2,
            lightcolor=SURFACE_2,
            darkcolor=SURFACE_2,
            insertcolor=TEXT,
            padding=(6, 3),
            arrowsize=11,
        )
        style.map(
            widget,
            fieldbackground=[("readonly", SURFACE_2)],
            foreground=[("readonly", TEXT)],
            background=[("active", BORDER), ("pressed", BORDER)],
            arrowcolor=[("active", TEXT)],
            bordercolor=[("focus", BORDER)],
            lightcolor=[("focus", SURFACE_2)],
            selectbackground=[("readonly", SURFACE_2)],
            selectforeground=[("readonly", TEXT)],
        )

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, borderwidth=0)
    style.map("Treeview", background=[("selected", SURFACE_2)], foreground=[("selected", TEXT)])
    style.configure(
        "Treeview.Heading", background=SURFACE, foreground=MUTED, borderwidth=0, font=font(UI_FONT, 8, "bold")
    )
    style.map("Treeview.Heading", background=[("active", SURFACE_2)])
    style.configure("TSeparator", background=BORDER)
    style.layout(
        "Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]},
            )
        ],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=BORDER,
        troughcolor=SURFACE,
        borderwidth=0,
        lightcolor=BORDER,
        darkcolor=BORDER,
        bordercolor=SURFACE,
        arrowsize=8,
        gripcount=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", MUTED)])
    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=MUTED, font=font(UI_FONT, 8, "bold"))


def card(parent, title=None):
    """Flat surface panel with an optional small uppercase title; returns the inner frame."""
    outer = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
    if title:
        ttk.Label(outer, text=title.upper(), style="Section.TLabel").pack(anchor="w", pady=(0, 6))
    return outer


class CompoundDot(tk.Canvas):
    """Small filled circle in a compound's colour; set(None) shows an empty ring."""

    def __init__(self, parent, compound=None, size=12, bg=SURFACE, font=None):
        """With font, the dot keeps size relative to that font's line height as it scales."""
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0)
        self._oval = self.create_oval(1, 1, size - 1, size - 1, width=1)
        self._ratio = size / font.metrics("linespace") if font else None
        self._font = font
        self.set(compound)

    def rescale(self):
        if self._font:
            size = max(8, round(self._font.metrics("linespace") * self._ratio))
            self.configure(width=size, height=size)
            self.coords(self._oval, 1, 1, size - 1, size - 1)

    def set(self, compound):
        colour = COMPOUND_COLOURS.get((compound or "").lower())
        self.itemconfig(self._oval, fill=colour or SURFACE, outline=colour or MUTED)


class Toggle(tk.Canvas):
    """Minimal on/off switch bound to a BooleanVar; calls command() after a click."""

    def __init__(self, parent, variable, command=None, height=18, bg=BG):
        super().__init__(parent, width=height * 2, height=height, bg=bg, highlightthickness=0, cursor="hand2")
        self._var, self._command, self._h = variable, command, height
        self.bind("<Button-1>", self._click)
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _click(self, _event):
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        h, on = self._h, self._var.get()
        track = ACCENT if on else SURFACE_2
        self.create_oval(0, 0, h, h, fill=track, outline="")
        self.create_oval(h, 0, 2 * h, h, fill=track, outline="")
        self.create_rectangle(h / 2, 0, 1.5 * h, h, fill=track, outline="")
        knob = h + 2 if on else 2
        self.create_oval(knob, 2, knob + h - 4, h - 2, fill=TEXT, outline="")


class Slider(tk.Canvas):
    """Flat 0-100 slider with a round handle and the value on the right, bound to an IntVar."""

    def __init__(self, parent, variable, font, command=None, length=220, height=20, bg=BG):
        super().__init__(parent, width=length, height=height, bg=bg, highlightthickness=0, cursor="hand2")
        self._var, self._font, self._command = variable, font, command
        for event in ("<Button-1>", "<B1-Motion>"):
            self.bind(event, self._drag)
        self.bind("<Configure>", lambda e: self._draw())
        variable.trace_add("write", lambda *_: self._draw())

    def _geometry(self):
        r = self.winfo_height() / 2
        return r, r * 0.6, self.winfo_width() - 4 * r - r * 0.6

    def _drag(self, event):
        r, x0, x1 = self._geometry()
        value = round(max(0, min(100, (event.x - x0) / max(1, x1 - x0) * 100)))
        if value != self._var.get():
            self._var.set(value)
            if self._command:
                self._command()

    def _draw(self):
        self.delete("all")
        r, x0, x1 = self._geometry()
        if x1 <= x0:
            return
        y, knob = r, x0 + (x1 - x0) * self._var.get() / 100
        self.create_line(x0, y, x1, y, fill=SURFACE_2, width=4, capstyle="round")
        self.create_line(x0, y, knob, y, fill=ACCENT, width=4, capstyle="round")
        self.create_oval(knob - r * 0.6, y - r * 0.6, knob + r * 0.6, y + r * 0.6, fill=TEXT, outline="")
        self.create_text(self.winfo_width(), y, anchor="e", text=str(self._var.get()), fill=MUTED, font=self._font)
