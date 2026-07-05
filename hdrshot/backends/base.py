"""The capture-backend seam.

A :class:`CaptureBackend` knows how to enumerate displays and grab one
scRGB-linear frame per output on a given platform. The Win32 implementation lives
in :mod:`hdrshot.backends.win32`; the seam exists so a macOS (ScreenCaptureKit
EDR) or Wayland backend could slot in without touching the pipeline.

This module is platform-free: it imports only typing + :mod:`hdrshot.core`, so it
loads on any OS. Concrete backends bind their platform libraries lazily.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..core.types import DisplayInfo, MonitorCapture


class UnsupportedPlatformError(RuntimeError):
    """Raised when no capture backend exists for the current platform."""


@runtime_checkable
class CaptureBackend(Protocol):
    """What the pipeline and GUI need from any platform's capture layer."""

    #: Short platform identifier, e.g. ``"win32"``.
    name: str

    def set_process_dpi_aware(self) -> None:
        """Opt in to per-monitor physical-pixel coordinates. Idempotent, and a
        no-op on platforms without the concept."""
        ...

    def enumerate_displays(self) -> list[DisplayInfo]:
        """One :class:`DisplayInfo` per active display, HDR state included."""
        ...

    def capture_all(self) -> dict[str, MonitorCapture]:
        """Grab one scRGB-linear frame from every attached output, keyed by GDI
        (or platform-equivalent) device name."""
        ...
