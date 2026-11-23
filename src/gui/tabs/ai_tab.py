"""ai_tab.py -- the AI tab: mic and AI status, and the radio transcript."""

import time
import tkinter as tk
from tkinter import scrolledtext

from engineer.voice_input import PTT_KEY


class AITab:
    def __init__(self, frame, app):
        bold = app.font("Segoe UI", 10, "bold")
        self.mic_label = tk.Label(frame, text="Mic: --", font=bold, anchor="w")
        self.mic_label.pack(fill="x", padx=10, pady=(10, 4))
        self.ai_label = tk.Label(frame, text="AI: --", font=bold, anchor="w")
        self.ai_label.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(frame, text=f"Hold {PTT_KEY.upper()} to talk.", font=app.font("Segoe UI", 9), anchor="w").pack(
            fill="x", padx=10
        )
        self.transcript = scrolledtext.ScrolledText(frame, height=20, state="disabled", font=app.font("Consolas", 10))
        self.transcript.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    def show_status(self, mic, ai):
        self.mic_label.config(text=f"Mic: {mic}")
        self.ai_label.config(text=f"AI: {ai}")

    def add(self, line):
        self.transcript.config(state="normal")
        self.transcript.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {line}\n")
        self.transcript.see(tk.END)
        self.transcript.config(state="disabled")
