"""
leaderboard_debug.py - One run: captures the leaderboard, reads every row
(position, gap, tyre) with the live reader, works out which row is the player
and prints what it found. Crops are saved to debug_output/ for when the
console isn't enough.

Run: python tools/leaderboard_debug.py   (edit LEADERBOARD_CROPS in src/telemetry/hud_layout.py and rerun to tune)
"""

import time

import cv2

from paths import PROJECT_DIR
from telemetry.capture import capture_frame
from telemetry.capture_guard import is_process_running
from telemetry.hud_layout import GAME_EXE_NAME, GAME_WINDOW_TITLE, LEADERBOARD_CROPS
from telemetry.hud_reader import PLAYER_ROW_MIN_SATURATION, WRENCH_PATH, read_leaderboard, ring_refs, wrench_gray

OUTPUT_DIR = PROJECT_DIR / "debug_output"


def fmt_tyre(compound, dist):
    if dist is None:
        return "-"
    return f"{compound or '?'}({dist:.0f})"


def save_crops(frame):
    OUTPUT_DIR.mkdir(exist_ok=True)
    run_dir = OUTPUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(exist_ok=True)
    annotated = frame.copy()
    for name, (y1, y2, x1, x2) in LEADERBOARD_CROPS.items():
        cv2.imwrite(str(run_dir / f"{name}.png"), frame[y1:y2, x1:x2])
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(annotated, name, (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(run_dir / "_full_annotated.png"), annotated)
    return run_dir


def main():
    if not is_process_running(GAME_EXE_NAME):
        print(f"'{GAME_EXE_NAME}' not running -- start the game first.")
        return

    frame = capture_frame()
    if frame is None:
        print(f"Window '{GAME_WINDOW_TITLE}' not found.")
        return

    print(f"Crops saved to {save_crops(frame)}\n")
    if wrench_gray() is None:
        print(f"WARNING: wrench template not readable at {WRENCH_PATH} -- pitting check skipped\n")
    skipped = ring_refs()[1]
    if skipped:
        print(f"ignored (need a 56px crop with the whole icon): {', '.join(skipped)}\n")

    lb = read_leaderboard(frame)
    print(f"current_pos: {lb['current_pos_text']!r}  -> P{lb['position']} of {lb['total_cars']}\n")

    print(f"{'row':8s} {'pos':>6s} {'gap':>10s} {'tyre':>12s} {'saturation':>10s} {'wrench':>7s}")
    for r in lb["rows"]:
        if r["is_player"]:
            gap, tyre_cell = "(player)", "(player)"
        else:
            gap = "PITTING" if r["pitting"] else r["gap_text"]
            tyre_cell = fmt_tyre(r["tyre"], r["tyre_dist"])
        sat = f"{r['saturation']:10.1f}" if r["saturation"] is not None else f"{'-':>10s}"
        wcell = f"{r['wrench_score']:7.2f}" if r["wrench_score"] is not None else f"{'-':>7s}"
        print(f"{r['row']:8s} {r['pos_text']!r:>6s} {gap!r:>10s} {tyre_cell:>12s} {sat} {wcell}")

    player = next((r for r in lb["rows"] if r["is_player"]), None)
    if player is None:
        print(
            f"\nplayer row: NOT FOUND -- no row matched P{lb['position']} and no badge reached "
            f"saturation {PLAYER_ROW_MIN_SATURATION}"
        )
    elif player["row"] == "leader":
        print("\nplayer row: leader  (current_pos is 1)")
    else:
        how = "pos matches current_pos" if player["pos"] == lb["position"] else "picked by badge saturation"
        print(f"\nplayer row: {player['row']}  ({how}, saturation {player['saturation']:.1f})")


if __name__ == "__main__":
    main()
