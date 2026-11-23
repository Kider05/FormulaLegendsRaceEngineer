"""
voice_input.py -- hold-to-talk: records the Windows default microphone while PTT_KEY is held, then
transcribes locally with faster-whisper: large-v3-turbo on the GPU when NVIDIA's CUDA libraries are
installed (pip nvidia-cublas-cu12, nvidia-cudnn-cu12), otherwise small.en on the CPU.
Finished questions arrive on `results` as (text, seconds taken).
The mic stream stays open so the first syllable isn't lost to stream start-up; audio is only kept
while the key is held.
"""

import os
import queue
import sys
import threading
import time

import numpy as np

PTT_KEY = "v"
SAMPLE_RATE = 16000
MIN_CLIP_SEC = 0.3
GPU_MODEL = "large-v3-turbo"
CPU_MODEL = "small.en"
VOCAB_PROMPT = (
    "Race engineer radio. Box, box. Softs, mediums, hards, wets. Car ahead, car behind, gap ahead, gap behind, "
    "pace, tyres, fuel, stint, pit stop, undercut, P1, P2, P3, laps to go."
)


class VoiceInput:
    def __init__(self):
        self.results = queue.Queue()
        self.status = "loading speech model"
        self.ready_status = "ready"
        self._clips = queue.Queue()
        self._chunks = []
        self._recording = False
        self._lock = threading.Lock()
        self._stream = None
        self._listener = None

    def start(self):
        """Open the mic and key hook, and load the model in the background. Raises if either device fails."""
        import sounddevice
        from pynput import keyboard

        self._stream = sounddevice.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._on_audio
        )
        self._stream.start()
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        threading.Thread(target=self._transcribe_loop, daemon=True).start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
        if self._stream is not None:
            self._stream.close()

    def _on_audio(self, data, _frames, _time_info, _status):
        if self._recording:
            with self._lock:
                self._chunks.append(data[:, 0].copy())

    def _on_press(self, key):
        if _is_ptt(key) and not self._recording:
            with self._lock:
                self._chunks = []
            self._recording = True
            self.status = "listening"

    def _on_release(self, key):
        if _is_ptt(key) and self._recording:
            self._recording = False
            with self._lock:
                clip = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
                self._chunks = []
            if len(clip) >= MIN_CLIP_SEC * SAMPLE_RATE:
                self.status = "transcribing"
                self._clips.put(clip)
            else:
                self.status = self.ready_status

    def _transcribe_loop(self):
        try:
            model, label = load_model()
        except Exception as e:
            self.status = f"speech model failed: {e}"
            return
        self.ready_status = f"ready ({label})"
        self.status = self.ready_status
        while True:
            clip = self._clips.get()
            t0 = time.perf_counter()
            text = transcribe(model, clip)
            if text:
                self.results.put((text, time.perf_counter() - t0))
            if not self._recording:
                self.status = self.ready_status


def load_model():
    """(WhisperModel, label): GPU large-v3-turbo if CUDA works, else CPU small.en."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(GPU_MODEL, device="cuda", compute_type="float16")
        transcribe(model, np.zeros(SAMPLE_RATE // 2, np.float32))
        return model, f"GPU {GPU_MODEL}"
    except Exception:
        return WhisperModel(CPU_MODEL, device="cpu", compute_type="int8"), f"CPU {CPU_MODEL}"


def _add_cuda_dll_dirs():
    """Windows: let ctranslate2 find the cuBLAS/cuDNN DLLs from the nvidia-* pip packages."""
    if sys.platform != "win32":
        return
    for base in sys.path:
        for lib in ("cublas", "cudnn"):
            bin_dir = os.path.join(base, "nvidia", lib, "bin")
            if os.path.isdir(bin_dir):
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]


def transcribe(model, clip):
    segments, _ = model.transcribe(clip, language="en", beam_size=5, vad_filter=True, initial_prompt=VOCAB_PROMPT)
    return " ".join(s.text.strip() for s in segments).strip()


def _is_ptt(key):
    char = getattr(key, "char", None)
    return char is not None and char.lower() == PTT_KEY
