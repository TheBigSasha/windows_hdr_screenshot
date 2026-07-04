"""Capture backends and the factory that selects one for the current platform.

Importing this package is cheap and platform-free — the Win32 ctypes bindings are
only loaded when :func:`get_backend` actually constructs the Win32 backend. That
is what keeps ``import hdrshot`` (and the whole pure pipeline) working off-Windows.
"""
from __future__ import annotations

import sys

from .base import CaptureBackend, UnsupportedPlatformError

__all__ = ["CaptureBackend", "UnsupportedPlatformError", "get_backend", "backend_available"]


def get_backend() -> CaptureBackend:
    """Return the capture backend for this platform.

    Raises :class:`UnsupportedPlatformError` where no backend exists. The heavy
    platform module is imported here, not at package import time.
    """
    if sys.platform == "win32":
        from .win32 import Win32CaptureBackend
        return Win32CaptureBackend()
    raise UnsupportedPlatformError(
        f"No HDR capture backend for platform {sys.platform!r}. "
        "Capture currently requires Windows 10 1803+ / Windows 11. "
        "The pure pipeline (encoders, color, selftest, parse) still works here."
    )


def backend_available() -> bool:
    """True if a capture backend exists for this platform (does not construct it)."""
    return sys.platform == "win32"
