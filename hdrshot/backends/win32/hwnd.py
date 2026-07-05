"""Window hit-testing for the overlay's window-capture mode (issue #6).

``WindowFromPoint`` finds the window under the cursor; ``GetAncestor(GA_ROOT)``
walks up to its top-level window; and ``DwmGetWindowAttribute`` with
``DWMWA_EXTENDED_FRAME_BOUNDS`` returns the *true* frame rect (excluding the
invisible drop-shadow border ``GetWindowRect`` includes). Physical-pixel screen
coordinates throughout (the process is per-monitor-v2 DPI aware).

The topmost window under the cursor is the selection overlay itself (it must
accept mouse input, so it cannot be click-through), so the hit test walks down
the z-order past every window of this process — and past invisible/cloaked
ones — to the first foreign window containing the point.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .com import RECT, kernel32, user32

dwmapi = ctypes.windll.dwmapi

GA_ROOT = 2
GW_HWNDNEXT = 2
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.WindowFromPoint.restype = ctypes.c_void_p
user32.WindowFromPoint.argtypes = [_POINT]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindow.restype = ctypes.c_void_p
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]


def _is_own_window(hwnd) -> bool:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == kernel32.GetCurrentProcessId()


def _is_cloaked(hwnd) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED,
                                      ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return hr == 0 and cloaked.value != 0


def _contains_point(hwnd, x: int, y: int) -> bool:
    rect = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return False
    return rect.left <= x < rect.right and rect.top <= y < rect.bottom


def _first_foreign_toplevel(root, x: int, y: int):
    """Walk the top-level z-order downward from ``root`` (exclusive) to the first
    visible, non-cloaked window of another process containing (x, y)."""
    hwnd = user32.GetWindow(root, GW_HWNDNEXT)
    for _ in range(2048):                        # hard bound; z-lists are finite
        if not hwnd:
            return None
        if (user32.IsWindowVisible(hwnd) and not _is_own_window(hwnd)
                and not _is_cloaked(hwnd) and _contains_point(hwnd, x, y)):
            return hwnd
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return None


def window_frame_bounds_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) DWM frame bounds of the top-level *foreign*
    window at screen pixel (x, y), or None if there isn't one / the query fails.
    Windows of the calling process (the overlay) are skipped."""
    hwnd = user32.WindowFromPoint(_POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    if _is_own_window(root):
        root = _first_foreign_toplevel(root, int(x), int(y))
        if not root:
            return None
    rect = RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(root), DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0:
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)
