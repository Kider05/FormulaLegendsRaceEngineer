"""
debug_ui_state.py

Quick, standalone check of the pause/pitstop UI-state detection --
grabs ONE frame right now and reports both states' scores immediately,
without needing to wait for Main.py's activity monitor to actually
declare idle first (which requires a few consecutive failed digit-reads
before it even checks UI state at all).

Run this while the game is actually showing the state you want to test
(paused, or sitting at the pit-stop garage screen):

    python tools/debug_ui_state.py
"""

from pathlib import Path

import cv2

from telemetry.capture import capture_frame
from telemetry.capture_guard import is_process_running
from telemetry.hud_layout import GAME_EXE_NAME
from telemetry.hud_reader import ui_state_configs
from telemetry.ui_state_detector import check_named_states

OUT_DIR = Path("debug_ui_state_output")
OUT_DIR.mkdir(exist_ok=True)


def main():
    if not is_process_running(GAME_EXE_NAME):
        print(f"'{GAME_EXE_NAME}' not found -- start the game first, then re-run this.")
        return

    frame = capture_frame()
    if frame is None:
        print("Game window not found -- make sure it's actually visible.")
        return

    matched_state, scores = check_named_states(frame, ui_state_configs())

    print(f"Matched state: {matched_state!r}")
    print("All scores:")
    for name, score in scores.items():
        print(f"  {name}: {score:.3f}")

    # Save each region's actual live crop so you can visually confirm the
    # coordinates are landing on the right part of the screen -- same
    # purpose as debug_capture.py's saved crops, just for these two
    # regions specifically.
    for name, (region, _template) in ui_state_configs().items():
        y1, y2, x1, x2 = region
        crop = frame[y1:y2, x1:x2]
        crop_path = OUT_DIR / f"{name}_live_crop.png"
        cv2.imwrite(str(crop_path), crop)
        print(f"Saved live '{name}' region -> {crop_path}")

    print("\nOpen the saved crops and compare against ui_states/pause.png and")
    print("ui_states/pitstop.png to check alignment if a score looks off.")


if __name__ == "__main__":
    main()
