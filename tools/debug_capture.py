"""
debug_capture.py

One-shot diagnostic: grab a single frame the exact same way Main.py does,
and save both the full frame and each individual HUD crop to disk so you
can look at them directly. Run this while the game is actively running
and showing real race data (same situation you just tested).
dwww
This will tell us immediately whether the problem is:
  (a) capture itself is broken (black screen, desktop instead of the
      game, a corrupted/garbled image) -- likely an exclusive-fullscreen
      capture limitation with mss, or a DPI-scaling coordinate mismatch
  (b) capture looks correct, but the crop coordinates are landing on the
      wrong part of the screen
  (c) something else entirely once we can actually see it

Run from the project root:
    python tools/debug_capture.py
"""

from pathlib import Path

import cv2

from telemetry.capture import capture_frame
from telemetry.capture_guard import is_process_running
from telemetry.hud_layout import CROP_REGIONS, GAME_EXE_NAME

OUT_DIR = Path("debug_capture_output")
OUT_DIR.mkdir(exist_ok=True)


def main():
    if not is_process_running(GAME_EXE_NAME):
        print(f"'{GAME_EXE_NAME}' not found -- start the game first, then re-run this.")
        return

    frame = capture_frame()
    print(f"Captured frame shape: {frame.shape}")

    full_path = OUT_DIR / "full_frame.png"
    cv2.imwrite(str(full_path), frame)
    print(f"Saved full frame -> {full_path}")

    for name, (y1, y2, x1, x2) in CROP_REGIONS.items():
        crop = frame[y1:y2, x1:x2]
        crop_path = OUT_DIR / f"{name}_crop.png"
        cv2.imwrite(str(crop_path), crop)
        print(f"Saved '{name}' crop {crop.shape} -> {crop_path}")

    print("\nDone. Open these images and check:")
    print("  1. Does full_frame.png actually show the game (not black/desktop)?")
    print("  2. Do the individual crops land on the right HUD elements?")


if __name__ == "__main__":
    main()
