"""Orchestration: capture -> crop -> detect HDR -> choose format -> encode.

Kept free of any Qt dependency so it can run headless (CLI, self-test) and be
driven by the GUI alike.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from . import capture, color, displays
from .encoders import exr, sdr, ultrahdr

# Formats and their file extensions.
EXT = {
    "ultrahdr": ".jpg", "exr": ".exr", "heic": ".heic",
    "png": ".png", "jpeg": ".jpg", "avif": ".avif",
}
HDR_FORMATS = {"ultrahdr", "exr", "heic"}


def default_save_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class CaptureResult:
    linear: np.ndarray          # cropped scRGB FP16, 1.0 == 80 nits
    sdr_white_nits: float
    display: displays.DisplayInfo | None
    region_phys: tuple          # (x, y, w, h) within the source display buffer
    stats: dict = field(default_factory=dict)

    @property
    def is_hdr(self) -> bool:
        enabled = bool(self.display and self.display.hdr_enabled)
        return self.stats.get("has_hdr", False) and enabled

    @property
    def hdr_capable_content(self) -> bool:
        """True if the pixels themselves carry >SDR-white values, regardless of
        whether Windows HDR is currently toggled on."""
        return self.stats.get("has_hdr", False)


# --------------------------------------------------------------------------- #
# Capture helpers
# --------------------------------------------------------------------------- #
def _display_for(disps, gdi_name):
    for d in disps:
        if d.gdi_name == gdi_name:
            return d
    return None


def capture_region(phys_rect: tuple[int, int, int, int],
                   caps: dict | None = None,
                   disps: list | None = None) -> CaptureResult:
    """Crop a virtual-desktop physical rectangle out of the captured buffers.

    The rectangle is resolved against the display that contains its centre; the
    crop is clipped to that display.
    """
    if caps is None:
        caps = capture.capture_all()
    if disps is None:
        disps = displays.enumerate_displays()
    x, y, w, h = phys_rect
    cx, cy = x + w // 2, y + h // 2

    src = None
    for name, mc in caps.items():
        if mc.x <= cx < mc.x + mc.width and mc.y <= cy < mc.y + mc.height:
            src = mc
            break
    if src is None:                                   # fall back to first
        src = next(iter(caps.values()))

    lx, ly = x - src.x, y - src.y
    lx0, ly0 = max(0, lx), max(0, ly)
    lx1, ly1 = min(src.width, lx + w), min(src.height, ly + h)
    crop = src.linear[ly0:ly1, lx0:lx1]

    disp = _display_for(disps, src.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0
    return CaptureResult(linear=np.ascontiguousarray(crop), sdr_white_nits=white,
                         display=disp, region_phys=(lx0, ly0, lx1 - lx0, ly1 - ly0),
                         stats=color.hdr_stats(crop, white))


def capture_buffer_region(caps: dict, disps: list, gdi_name: str,
                          buffer_rect: tuple | None) -> CaptureResult:
    """Crop directly from one output's captured buffer.

    ``buffer_rect`` is ``(x, y, w, h)`` in that buffer's own physical pixels, or
    ``None`` for the whole screen. Used by the selection overlay.
    """
    mc = caps.get(gdi_name) or next(iter(caps.values()))
    disp = _display_for(disps, mc.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0
    if buffer_rect is None:
        crop = mc.linear
        rect = (0, 0, mc.width, mc.height)
    else:
        x, y, w, h = buffer_rect
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(mc.width, x + w), min(mc.height, y + h)
        crop = np.ascontiguousarray(mc.linear[y0:y1, x0:x1])
        rect = (x0, y0, x1 - x0, y1 - y0)
    return CaptureResult(linear=crop, sdr_white_nits=white, display=disp,
                         region_phys=rect, stats=color.hdr_stats(crop, white))


def capture_display(disp: displays.DisplayInfo,
                    caps: dict | None = None) -> CaptureResult:
    if caps is None:
        caps = capture.capture_all()
    mc = caps.get(disp.gdi_name) or next(iter(caps.values()))
    return CaptureResult(linear=mc.linear, sdr_white_nits=disp.sdr_white_nits,
                         display=disp, region_phys=(0, 0, mc.width, mc.height),
                         stats=color.hdr_stats(mc.linear, disp.sdr_white_nits))


# --------------------------------------------------------------------------- #
# Format choice + encode
# --------------------------------------------------------------------------- #
def choose_auto_format(result: CaptureResult) -> str:
    """Pick the best format automatically, macOS-style: gain-map JPEG for HDR,
    PNG for SDR."""
    if result.hdr_capable_content:
        return "ultrahdr"
    return "png"


def encode(result: CaptureResult, fmt: str, path: str) -> dict:
    """Encode the result to ``path`` in ``fmt``. Returns info about what was written."""
    if fmt == "auto":
        fmt = choose_auto_format(result)
    lin = result.linear
    white = result.sdr_white_nits
    info = {"format": fmt, "path": path, "hdr": fmt in HDR_FORMATS and result.hdr_capable_content}

    if fmt == "exr":
        exr.write_exr(path, lin, white)
    elif fmt == "ultrahdr":
        meta = ultrahdr.write_ultrahdr(path, lin, white)
        info["gainmap_max_stops"] = round(meta["gain_max_log2"], 3)
    elif fmt == "heic":
        from .encoders import heic  # optional dependency path
        heic.write_heic_pq(path, lin)
    elif fmt == "png":
        sdr.write_png(path, color.scrgb_to_sdr_u8(lin, white))
    elif fmt == "jpeg":
        sdr.write_jpeg(path, color.scrgb_to_sdr_u8(lin, white))
    elif fmt == "avif":
        sdr.write_avif_sdr(path, color.scrgb_to_sdr_u8(lin, white))
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return info


def timestamped_name(fmt: str, hdr: bool) -> str:
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    prefix = "HDR " if hdr else ""
    return f"{prefix}Screenshot {stamp}{EXT.get(fmt, '.png')}"


def save(result: CaptureResult, fmt: str = "auto",
         out_dir: str | None = None) -> dict:
    if fmt == "auto":
        fmt = choose_auto_format(result)
    out_dir = out_dir or default_save_dir()
    os.makedirs(out_dir, exist_ok=True)
    hdr = fmt in HDR_FORMATS and result.hdr_capable_content
    path = os.path.join(out_dir, timestamped_name(fmt, hdr))
    info = encode(result, fmt, path)
    info["path"] = path
    return info
