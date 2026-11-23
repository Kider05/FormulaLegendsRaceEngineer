"""
capture_guard.py

Detects whether a target process/window is actually running before you
spend any time capturing or reading from it. Two levels of check:

  1. is_process_running(exe_name) -- cheap check via psutil, just "is this
     .exe running at all" (works even if the window is minimized).
  2. find_window(title_substring) -- finds the actual window via win32gui,
     which also gives you its live on-screen bounding box. Useful if the
     game window can move/resize, rather than trusting hardcoded crop
     coordinates forever.

Requires: pip install psutil pywin32
"""

import psutil

try:
    import win32gui

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


def is_process_running(exe_name):
    """
    Check whether a process with this executable name is currently
    running. Case-insensitive. e.g. is_process_running("pycharm64.exe")

    This alone doesn't tell you if the window is visible/focused, just
    that the process exists -- use find_window() if you need the actual
    on-screen window bounds too.
    """
    exe_name = exe_name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == exe_name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process list is a live snapshot -- a process can exit between
            # process_iter() listing it and us reading its info. Skip it.
            continue
    return False


def find_window(title_substring):
    """
    Find a window whose title contains title_substring (case-insensitive)
    and return its (left, top, right, bottom) screen coordinates, or None
    if no matching window is currently open.

    Requires pywin32 (Windows only).
    """
    if not _HAS_WIN32:
        raise RuntimeError("find_window requires pywin32 (pip install pywin32)")

    title_substring = title_substring.lower()
    result = {"hwnd": None}

    def _callback(hwnd, _):
        # IMPORTANT: always return True here, even after finding a match.
        # win32gui.EnumWindows's Python wrapper treats a False/0 return
        # from the underlying Windows API as a FAILURE and raises a
        # pywintypes.error using a stale error code -- which produces a
        # misleading "cannot find the file specified" message even though
        # nothing actually went wrong. Recording the match in `result`
        # and letting enumeration run to completion avoids that entirely.
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if result["hwnd"] is None and title_substring in title.lower():
            result["hwnd"] = hwnd
        return True

    win32gui.EnumWindows(_callback, None)

    if result["hwnd"] is None:
        return None
    return win32gui.GetWindowRect(result["hwnd"])
