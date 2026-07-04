"""Window hit-testing for the overlay's window-capture mode (issue #6).

``WindowFromPoint`` finds the window under the cursor; ``GetAncestor(GA_ROOT)``
walks up to its top-level window; and ``DwmGetWindowAttribute`` with
``DWMWA_EXTENDED_FRAME_BOUNDS`` returns the *true* frame rect (excluding the
invisible drop-shadow border ``GetWindowRect`` includes). Physical-pixel screen
coordinates throughout (the process is per-monitor-v2 DPI aware).
"""
from __future__ import annotations

import ctypes

from .com import RECT, user32

dwmapi = ctypes.windll.dwmapi

GA_ROOT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.WindowFromPoint.restype = ctypes.c_void_p
user32.WindowFromPoint.argtypes = [_POINT]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]


def window_frame_bounds_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) DWM frame bounds of the top-level window at
    screen pixel (x, y), or None if there isn't one / the query fails."""
    hwnd = user32.WindowFromPoint(_POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    rect = RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(root), DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0:
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)
