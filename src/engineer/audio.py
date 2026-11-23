"""
audio.py -- spoken callouts and the fuel beep, each on its own thread so the GUI never waits on sound.
Speech uses the Kokoro neural voice from models/; if its files or package are missing it falls back
to the Windows SAPI voice. Off Windows, speech and beeps are printed instead.
"""

import io
import itertools
import logging
import math
import queue
import sys
import threading
import time
import wave

import numpy as np

from paths import PROJECT_DIR

log = logging.getLogger(__name__)

KOKORO_MODEL = PROJECT_DIR / "models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = PROJECT_DIR / "models" / "voices-v1.0.bin"
KOKORO_VOICE = "bf_emma"
KOKORO_LANG = "en-gb"
STALE_SPEECH_SEC = 3.0
STALE_ANSWER_SEC = 20.0
BEEP_HZ = 1200
BEEP_SEC = 0.25
BEEP_SAMPLE_RATE = 22050
_CALLOUT, _ANSWER, _PREPARE = 0, 1, 2  # queue priorities, lowest first


class Speaker:
    """say() speaks callouts in order, dropping any older than STALE_SPEECH_SEC. say_answer() queues
    an AI answer sentence by sentence behind callouts, so a callout cuts in at the next sentence break.
    prepare() renders a phrase ahead of time so a later say() of the same text plays instantly."""

    def __init__(self, volume=80):
        self.enabled = True
        self.volume = volume
        self.backend = "loading"
        self._queue = queue.PriorityQueue()
        self._order = itertools.count()
        self._rendered = {}
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text):
        self._put(_CALLOUT, text, STALE_SPEECH_SEC)

    def say_answer(self, sentences):
        for sentence in sentences:
            self._put(_ANSWER, sentence, STALE_ANSWER_SEC)

    def prepare(self, text):
        if text and text not in self._rendered:
            self._queue.put((_PREPARE, next(self._order), text, time.monotonic(), None))

    def _put(self, priority, text, max_age):
        if self.enabled and text:
            self._queue.put((priority, next(self._order), text, time.monotonic(), max_age))

    def _run(self):
        kokoro = _load_kokoro()
        sapi = None if kokoro else _sapi_voice()
        self.backend = "kokoro" if kokoro else ("windows" if sapi else "print")
        while True:
            kind, _, text, queued_at, max_age = self._queue.get()
            if kind == _PREPARE:
                if kokoro and text not in self._rendered:
                    self._rendered[text] = kokoro.create(text, voice=KOKORO_VOICE, lang=KOKORO_LANG)
                continue
            if time.monotonic() - queued_at > max_age:
                continue
            if kokoro:
                if text not in self._rendered:
                    self._rendered[text] = kokoro.create(text, voice=KOKORO_VOICE, lang=KOKORO_LANG)
                samples, sample_rate = self._rendered[text]
                _play(_wav(samples * self.volume / 100, sample_rate))
            elif sapi:
                sapi.Volume = int(self.volume)
                sapi.Speak(text)
            else:
                log.info("voice (no TTS available): %s", text)


def _load_kokoro():
    if not (KOKORO_MODEL.exists() and KOKORO_VOICES.exists()):
        return None
    try:
        from kokoro_onnx import Kokoro

        return Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    except Exception as e:
        log.warning("Kokoro unavailable, using Windows voice: %s", e)
        return None


def _sapi_voice():
    """SAPI voice for this thread (COM objects must be created on the thread that uses them)."""
    if sys.platform != "win32":
        return None
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        return win32com.client.Dispatch("SAPI.SpVoice")
    except Exception:
        return None


def beep(volume):
    """Short tone at volume 0-100, played on a background thread."""
    t = np.arange(int(BEEP_SAMPLE_RATE * BEEP_SEC)) / BEEP_SAMPLE_RATE
    tone = np.sin(2 * math.pi * BEEP_HZ * t) * max(0, min(100, volume)) / 100
    threading.Thread(target=_play, args=(_wav(tone, BEEP_SAMPLE_RATE),), daemon=True).start()


def _wav(samples, sample_rate):
    """Float samples in [-1, 1] -> 16-bit mono WAV bytes."""
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def _play(wav_bytes):
    if sys.platform != "win32":
        log.debug("beep (no winsound on this platform)")
        return
    import winsound

    winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
