"""
tts_benchmark.py -- how fast Kokoro speaks the race callouts on this PC.
Prints model load time and, per phrase, synthesis time vs audio length, then plays each phrase.
Run it with the game running too, to see the cost under load.

Setup: pip install kokoro-onnx, and put kokoro-v1.0.onnx (and/or kokoro-v1.0.int8.onnx) and voices-v1.0.bin
in the project's models/ folder (from the kokoro-onnx GitHub release "model-files-v1.0").
Run from the project root:  python tools/tts_benchmark.py [model file name] [voice]
"""

import io
import sys
import time
import wave
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "kokoro-v1.0.onnx"
VOICE = sys.argv[2] if len(sys.argv) > 2 else "bf_emma"
PHRASES = [
    "Box, box. Box this lap, hards.",
    "Box next lap, mediums. Clear by 2.3 seconds.",
    "Box next lap, softs. Lose one place.",
    "7 laps, box lap 15.",
    "6 laps, to the flag.",
]


def to_wav(samples, sample_rate):
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def play(wav_bytes):
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)


def main():
    model_path, voices_path = MODELS_DIR / MODEL, MODELS_DIR / "voices-v1.0.bin"
    missing = [p.name for p in (model_path, voices_path) if not p.exists()]
    if missing:
        print(f"Missing in {MODELS_DIR}: {', '.join(missing)}")
        return

    from kokoro_onnx import Kokoro

    t0 = time.perf_counter()
    kokoro = Kokoro(str(model_path), str(voices_path))
    print(f"Model {MODEL} loaded in {time.perf_counter() - t0:.2f}s, voice {VOICE}\n")

    worst = 0.0
    for text in PHRASES:
        t0 = time.perf_counter()
        samples, sample_rate = kokoro.create(text, voice=VOICE, speed=1.0, lang="en-gb")
        took = time.perf_counter() - t0
        worst = max(worst, took)
        print(f"{took * 1000:6.0f} ms to make {len(samples) / sample_rate:4.1f}s of audio  | {text}")
        play(to_wav(samples, sample_rate))

    verdict = "fine even without pre-rendering" if worst < 0.5 else "too slow to render on demand; needs pre-rendering"
    print(f"\nSlowest phrase: {worst * 1000:.0f} ms -> {verdict}")


if __name__ == "__main__":
    main()
