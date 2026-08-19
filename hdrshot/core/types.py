r"""Platform-free data types shared by the pipeline, backends and UI.

These carry results *out* of a capture backend and *into* the encoders. They hold
no Win32 handles and import nothing platform-specific, so ``hdrshot.core`` stays
importable on any OS.

Coordinate conventions
----------------------
* ``DisplayInfo`` / ``MonitorCapture`` rectangles are **physical pixels** on the
  virtual desktop (per-monitor-v2 DPI awareness), keyed by the GDI device name
  (e.g. ``\\.\DISPLAY5``) so the two Win32 enumeration sources join cleanly.
* ``MonitorCapture.linear`` is native scRGB-linear float16 (or float32 for
  platform/test backends), BT.709 primaries, where ``1.0`` == 80 nits. The
  pipeline promotes only the selected region before analysis/encoding.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DisplayInfo:
    """One active display, joined across ``EnumDisplayMonitors`` (rectangles) and
    ``QueryDisplayConfig`` (HDR / bit depth / SDR white)."""

    index: int
    gdi_name: str
    friendly_name: str
    x: int
    y: int
    width: int
    height: int
    is_primary: bool
    hdr_supported: bool
    hdr_enabled: bool
    bits_per_color: int
    sdr_white_nits: float
    color_encoding: str
    rotation: int = 0  # 0/90/180/270 degrees clockwise; 0 when unknown

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height

    @property
    def state_label(self) -> str:
        if self.hdr_enabled:
            return "HDR ON"
        if self.hdr_supported:
            return "HDR-capable (off)"
        return "SDR only"


@dataclass
class MonitorCapture:
    """One captured output: an scRGB-linear buffer plus where it lives on
    the virtual desktop."""

    gdi_name: str
    x: int
    y: int
    width: int
    height: int
    rotation: int
    linear: np.ndarray  # (H, W, 3) float16/float32, scRGB linear, 1.0 == 80 nits

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


def rotation_to_degrees(code: int) -> int:
    """Map the Win32 rotation enum to clockwise degrees.

    Both ``DXGI_MODE_ROTATION`` and ``DISPLAYCONFIG_ROTATION`` use the same values:
    0/1 = identity, 2 = 90, 3 = 180, 4 = 270. Anything else -> 0.
    """
    return {0: 0, 1: 0, 2: 90, 3: 180, 4: 270}.get(int(code), 0)


def apply_rotation(linear: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate a captured buffer into on-desktop orientation.

    DXGI Desktop Duplication hands back the panel-native surface; on a rotated
    display its dimensions are transposed relative to the on-desktop rectangle, so
    crops line up wrong (issue #17). Rotating here restores the invariant that a
    capture's shape matches its desktop rect. Direction follows the standard
    ``DXGI_MODE_ROTATION`` convention and is best-effort for exotic setups.
    """
    if degrees == 90:
        return np.ascontiguousarray(np.rot90(linear, k=1))
    if degrees == 180:
        return np.ascontiguousarray(np.rot90(linear, k=2))
    if degrees == 270:
        return np.ascontiguousarray(np.rot90(linear, k=3))
    return linear


def virtual_desktop_bounds(displays: list[DisplayInfo]) -> tuple[int, int, int, int]:
    """Union rectangle ``(x, y, w, h)`` covering all displays, physical pixels."""
    if not displays:
        return (0, 0, 0, 0)
    x0 = min(d.x for d in displays)
    y0 = min(d.y for d in displays)
    x1 = max(d.x + d.width for d in displays)
    y1 = max(d.y + d.height for d in displays)
    return (x0, y0, x1 - x0, y1 - y0)
