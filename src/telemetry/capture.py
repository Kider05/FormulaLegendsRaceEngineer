"""capture.py -- grabs the game window as a BGR frame: all of it, or only the regions that get read."""

import ctypes
import sys
import threading
from functools import cache

import cv2
import mss
import mss.exception
import numpy as np

from telemetry.capture_guard import find_window
from telemetry.hud_layout import GAME_WINDOW_TITLE

MERGE_GAP = 100  # regions closer than this (px) share one grab


@cache
def enable_dpi_awareness():
    """Report real pixels, not Windows display-scaled ones. Call before any window exists:
    switching later makes Windows resize open windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


_local = threading.local()


def _screen():
    """One MSS per thread, reused every grab(); Windows screen handles belong to the thread that made them."""
    if not hasattr(_local, "screen"):
        _local.screen = mss.MSS()
    return _local.screen


@cache
def grab_boxes(regions):
    """Regions merged into a few (y1, y2, x1, x2) boxes: each grab has a fixed cost, so neighbours share one."""
    boxes = []
    for box in sorted(regions):
        merging = True
        while merging:
            merging = False
            for other in boxes:
                if _near(box, other):
                    boxes.remove(other)
                    box = (min(box[0], other[0]), max(box[1], other[1]), min(box[2], other[2]), max(box[3], other[3]))
                    merging = True
                    break
        boxes.append(box)
    return tuple(boxes)


def _near(a, b):
    return max(a[0], b[0]) - min(a[1], b[1]) <= MERGE_GAP and max(a[2], b[2]) - min(a[3], b[3]) <= MERGE_GAP


def _blank_frame(height, width):
    """One reusable frame per thread; grabbed regions overwrite the same pixels every call."""
    frame = getattr(_local, "frame", None)
    if frame is None or frame.shape[:2] != (height, width):
        frame = _local.frame = np.zeros((height, width, 3), np.uint8)
    return frame


def capture_frame(regions=None):
    """Grab the game window as BGR; None if the window isn't found or Windows refuses the grab
    (e.g. game minimised). A failed grab drops this thread's MSS so the next call starts fresh.

    regions: (y1, y2, x1, x2) boxes on the window. Only those are grabbed and the rest of the frame is
    black; the same frame object is returned every call. Grab time scales with pixels, and the HUD
    regions are about a tenth of a 4K window."""
    enable_dpi_awareness()
    bounds = find_window(GAME_WINDOW_TITLE)
    if bounds is None:
        return None

    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    try:
        if regions is None:
            raw = np.array(_screen().grab({"left": left, "top": top, "width": width, "height": height}))
            return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
        frame = _blank_frame(height, width)
        for y1, y2, x1, x2 in grab_boxes(tuple(regions)):
            y2, x2 = min(y2, height), min(x2, width)
            if y1 >= y2 or x1 >= x2:
                continue
            raw = np.asarray(_screen().grab({"left": left + x1, "top": top + y1, "width": x2 - x1, "height": y2 - y1}))
            frame[y1:y2, x1:x2] = raw[:, :, :3]
        return frame
    except mss.exception.ScreenShotError:
        _local.__dict__.pop("screen", None)
        return None
