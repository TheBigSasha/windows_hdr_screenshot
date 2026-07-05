"""Windows capture backend (DXGI Desktop Duplication in scRGB FP16).

Only imported on Windows, via :func:`hdrshot.backends.get_backend`. Importing this
package binds the ctypes libraries (see :mod:`.com`), so it must never be imported
off-platform.
"""
from __future__ import annotations

from ...core.types import DisplayInfo, MonitorCapture
from . import capture as _capture
from . import displays as _displays
from .com import set_process_dpi_aware


class Win32CaptureBackend:
    """:class:`hdrshot.backends.base.CaptureBackend` for Windows."""

    name = "win32"

    def set_process_dpi_aware(self) -> None:
        set_process_dpi_aware()

    def enumerate_displays(self) -> list[DisplayInfo]:
        return _displays.enumerate_displays()

    def capture_all(self) -> dict[str, MonitorCapture]:
        return _capture.capture_all()


__all__ = ["Win32CaptureBackend"]
