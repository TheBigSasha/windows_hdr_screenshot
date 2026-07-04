"""Orchestration: capture -> crop -> detect HDR -> choose format -> encode.

Platform-free: it imports the capture *backend* through the factory (lazily), so
this module — and the whole encode path, selftest and ``parse`` — import and run
on any OS. Only the functions that actually grab pixels need a backend.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

import numpy as np

from ..backends import get_backend
from ..encoders import exr, sdr, ultrahdr
from . import color
from .types import DisplayInfo, MonitorCapture, virtual_desktop_bounds  # noqa: F401

log = logging.getLogger(__name__)

# Formats and their file extensions.
EXT = {
    "ultrahdr": ".jpg", "exr": ".exr", "heic": ".heic",
    "png": ".png", "jpeg": ".jpg", "avif": ".avif",
}
HDR_FORMATS = {"ultrahdr", "exr", "heic", "avif"}


class RegionError(ValueError):
    """A requested region does not intersect any captured display."""


class OptionalDependencyError(RuntimeError):
    """A format was requested whose optional encoder dependency isn't installed."""


# --------------------------------------------------------------------------- #
# Save location
# --------------------------------------------------------------------------- #
def _pictures_dir() -> str:
    """The real Pictures folder (honouring OneDrive / Known Folder redirection on
    Windows), falling back to ``~/Pictures`` elsewhere or on failure."""
    if sys.platform == "win32":
        try:
            from ..backends.win32.knownfolders import pictures_path
            p = pictures_path()
            if p:
                return p
        except Exception as e:  # pragma: no cover - defensive
            log.debug("known-folder Pictures lookup failed: %s", e)
    return os.path.join(os.path.expanduser("~"), "Pictures")


def default_save_dir() -> str:
    d = os.path.join(_pictures_dir(), "Screenshots")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class CaptureResult:
    linear: np.ndarray          # cropped scRGB FP16, 1.0 == 80 nits
    sdr_white_nits: float
    display: DisplayInfo | None
    region_phys: tuple          # (x, y, w, h) within the source display buffer
    stats: dict = field(default_factory=dict)
    spans_displays: list[str] = field(default_factory=list)  # gdi names if stitched

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


def _acquire(caps, disps):
    """Fill in caps/disps from the platform backend if not supplied."""
    if caps is not None and disps is not None:
        return caps, disps
    backend = get_backend()
    backend.set_process_dpi_aware()
    if disps is None:
        disps = backend.enumerate_displays()
    if caps is None:
        caps = backend.capture_all()
    return caps, disps


def _intersect(ax, ay, aw, ah, bx, by, bw, bh):
    """Rectangle intersection in a common coordinate space, or None."""
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def capture_region(phys_rect: tuple[int, int, int, int],
                   caps: dict | None = None,
                   disps: list | None = None) -> CaptureResult:
    """Crop a virtual-desktop physical rectangle out of the captured buffers.

    If the rectangle spans multiple displays it is **stitched** across them into a
    single buffer in virtual-desktop coordinates (each monitor's absolute
    scRGB-linear pixels composite directly; the dominant monitor's SDR white is
    used as the tone-mapping reference). A rectangle that intersects no display
    raises :class:`RegionError` rather than silently returning another monitor.
    """
    caps, disps = _acquire(caps, disps)
    x, y, w, h = phys_rect
    if w <= 0 or h <= 0:
        raise RegionError(f"degenerate region {phys_rect!r}")

    # Which captured monitors does the rectangle touch, and by how much?
    hits = []
    for mc in caps.values():
        inter = _intersect(x, y, w, h, mc.x, mc.y, mc.width, mc.height)
        if inter:
            hits.append((inter[2] * inter[3], mc, inter))
    if not hits:
        avail = "; ".join(f"{mc.gdi_name} {mc.width}x{mc.height}+{mc.x}+{mc.y}"
                          for mc in caps.values())
        raise RegionError(
            f"region {w}x{h}+{x}+{y} intersects no captured display. Available: {avail}")

    hits.sort(key=lambda t: t[0], reverse=True)
    dominant = hits[0][1]
    disp = _display_for(disps, dominant.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0

    if len(hits) == 1:
        _, mc, (ix, iy, iw, ih) = hits[0]
        crop = np.ascontiguousarray(mc.linear[iy - mc.y:iy - mc.y + ih, ix - mc.x:ix - mc.x + iw])
        return CaptureResult(linear=crop, sdr_white_nits=white, display=disp,
                             region_phys=(ix, iy, iw, ih),
                             stats=color.hdr_stats(crop, white))

    # Multi-monitor: composite each intersection into a virtual-space canvas.
    log.info("region spans %d displays; stitching", len(hits))
    canvas = np.zeros((h, w, 3), np.float32)
    for _, mc, (ix, iy, iw, ih) in hits:
        src = mc.linear[iy - mc.y:iy - mc.y + ih, ix - mc.x:ix - mc.x + iw, :3]
        canvas[iy - y:iy - y + ih, ix - x:ix - x + iw] = src
    return CaptureResult(linear=canvas, sdr_white_nits=white, display=disp,
                         region_phys=(x, y, w, h),
                         stats=color.hdr_stats(canvas, white),
                         spans_displays=[mc.gdi_name for _, mc, _ in hits])


def capture_buffer_region(caps: dict, disps: list, gdi_name: str,
                          buffer_rect: tuple | None) -> CaptureResult:
    """Crop directly from one output's captured buffer.

    ``buffer_rect`` is ``(x, y, w, h)`` in that buffer's own physical pixels, or
    ``None`` for the whole screen. Used by the selection overlay.
    """
    mc = caps.get(gdi_name)
    if mc is None:
        raise RegionError(f"no captured buffer for display {gdi_name!r} "
                          f"(have: {', '.join(caps) or 'none'})")
    disp = _display_for(disps, mc.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0
    if buffer_rect is None:
        # Whole screen: alias the source buffer (no wasteful full-res copy). The
        # preview legitimately needs this display's pixels; callers releasing the
        # capture dict still free every *other* monitor's buffer.
        crop = mc.linear
        rect = (0, 0, mc.width, mc.height)
    else:
        bx, by, bw, bh = buffer_rect
        x0, y0 = max(0, bx), max(0, by)
        x1, y1 = min(mc.width, bx + bw), min(mc.height, by + bh)
        if x1 <= x0 or y1 <= y0:
            raise RegionError(f"selection {buffer_rect!r} outside buffer {mc.width}x{mc.height}")
        crop = np.ascontiguousarray(mc.linear[y0:y1, x0:x1])
        rect = (x0, y0, x1 - x0, y1 - y0)
    return CaptureResult(linear=crop, sdr_white_nits=white, display=disp,
                         region_phys=rect, stats=color.hdr_stats(crop, white))


def capture_display(disp: DisplayInfo, caps: dict | None = None,
                    disps: list | None = None) -> CaptureResult:
    caps, disps = _acquire(caps, disps)
    mc = caps.get(disp.gdi_name)
    if mc is None:
        raise RegionError(f"no captured buffer for display {disp.gdi_name!r} "
                          f"(have: {', '.join(caps) or 'none'})")
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
    log.debug("encoding %s -> %s (%dx%d)", fmt, path, lin.shape[1], lin.shape[0])

    if fmt == "exr":
        exr.write_exr(path, lin, white)
    elif fmt == "ultrahdr":
        meta = ultrahdr.write_ultrahdr(path, lin, white)
        info["gainmap_max_stops"] = round(meta["gain_max_log2"], 3)
    elif fmt == "heic":
        from ..encoders import heic
        if not heic.available():
            raise OptionalDependencyError(
                "HEIC output needs the optional 'heic' extra: pip install hdrshot[heic]. "
                "UltraHDR, EXR and AVIF cover HDR without it.")
        heic.write_heic_pq(path, lin)
    elif fmt == "png":
        sdr.write_png(path, color.scrgb_to_sdr_u8(lin, white))
    elif fmt == "jpeg":
        sdr.write_jpeg(path, color.scrgb_to_sdr_u8(lin, white))
    elif fmt == "avif":
        # True 10-bit BT.2020 PQ AVIF when the content is HDR and libavif is
        # available; otherwise an 8-bit SDR AVIF.
        if result.hdr_capable_content:
            try:
                from ..encoders import avif_hdr
            except ImportError:
                avif_hdr = None
            if avif_hdr is not None and avif_hdr.available():
                avif_hdr.write_avif_pq(path, lin)
                info["hdr"] = True
                return info
            log.warning("HDR AVIF encoder unavailable; writing SDR AVIF instead")
            info["hdr"] = False
        sdr.write_avif_sdr(path, color.scrgb_to_sdr_u8(lin, white))
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return info


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
def timestamped_name(fmt: str, hdr: bool, when=None) -> str:
    from datetime import datetime
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H%M%S")
    prefix = "HDR " if hdr else ""
    return f"{prefix}Screenshot {stamp}{EXT.get(fmt, '.png')}"


TEMPLATE_TOKENS = {"date", "time", "display", "format", "hdr", "n"}


def validate_template(template: str) -> None:
    """Raise ValueError if a filename template uses an unknown ``{token}``."""
    import string
    for _, fieldname, _, _ in string.Formatter().parse(template):
        if fieldname is not None and fieldname not in TEMPLATE_TOKENS:
            raise ValueError(
                f"unknown token {{{fieldname}}} in filename template; "
                f"valid tokens: {', '.join('{' + t + '}' for t in sorted(TEMPLATE_TOKENS))}")


def _sanitize(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip()


def render_filename(template: str, fmt: str, hdr: bool, *, display: str = "",
                    n: int = 1, when=None) -> str:
    """Render a filename from a template + tokens. ``{n}`` is blank for n<=1."""
    from datetime import datetime
    dt = when or datetime.now()
    import re
    body = template.format(
        date=dt.strftime("%Y-%m-%d"),
        time=dt.strftime("%H%M%S"),
        display=_sanitize(display or ""),
        format=fmt,
        hdr="HDR " if hdr else "",
        n="" if n <= 1 else f" ({n})",
    )
    body = re.sub(r"\s+", " ", body).strip() or "Screenshot"
    return body + EXT.get(fmt, ".png")


def _unique_path(out_dir: str, filename: str) -> str:
    """Avoid clobbering an existing file (timestamps have 1-second resolution)."""
    path = os.path.join(out_dir, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        cand = os.path.join(out_dir, f"{stem} ({n}){ext}")
        if not os.path.exists(cand):
            return cand
        n += 1


def save(result: CaptureResult, fmt: str = "auto", out_dir: str | None = None,
         template: str | None = None) -> dict:
    if fmt == "auto":
        fmt = choose_auto_format(result)
    out_dir = out_dir or default_save_dir()
    os.makedirs(out_dir, exist_ok=True)
    hdr = fmt in HDR_FORMATS and result.hdr_capable_content
    path = _choose_path(out_dir, fmt, hdr, result, template)
    info = encode(result, fmt, path)
    info["path"] = path
    log.info("saved %s (%s)", path, "HDR" if info.get("hdr") else "SDR")
    return info


def _choose_path(out_dir: str, fmt: str, hdr: bool, result: CaptureResult,
                 template: str | None) -> str:
    """Pick a non-clobbering output path, using a filename template if given."""
    if not template:
        return _unique_path(out_dir, timestamped_name(fmt, hdr))
    validate_template(template)
    display_name = result.display.friendly_name if result.display else ""
    has_n = "{n}" in template
    n = 1
    while True:
        fname = render_filename(template, fmt, hdr, display=display_name, n=n)
        if not has_n and n > 1:                       # template lacks {n}; append a suffix
            stem, ext = os.path.splitext(fname)
            fname = f"{stem} ({n}){ext}"
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            return path
        n += 1
