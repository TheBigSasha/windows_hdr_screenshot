"""Resolve Windows Known Folders (the real Pictures path, OneDrive-aware).

``os.path.expanduser("~/Pictures")`` misses relocated or OneDrive-redirected
folders; ``SHGetKnownFolderPath(FOLDERID_Pictures)`` returns the true location.
"""
from __future__ import annotations

import ctypes

from comtypes import GUID

from .com import S_OK

FOLDERID_Pictures = GUID("{33E28130-4E1E-4676-835A-98395C3BC3BB}")


def pictures_path() -> str | None:
    """Absolute path to the user's Pictures folder, or ``None`` on failure."""
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    ptr = ctypes.c_wchar_p()
    hr = shell32.SHGetKnownFolderPath(
        ctypes.byref(FOLDERID_Pictures), 0, None, ctypes.byref(ptr))
    if hr != S_OK:
        return None
    try:
        return ptr.value
    finally:
        ole32.CoTaskMemFree(ptr)
