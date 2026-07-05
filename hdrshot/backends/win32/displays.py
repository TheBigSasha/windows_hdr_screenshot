r"""Display enumeration and HDR / advanced-color detection (Win32 backend).

Combines two Win32 sources:
  * EnumDisplayMonitors + GetMonitorInfo -> physical-pixel rectangles on the
    virtual desktop (requires per-monitor-v2 DPI awareness).
  * QueryDisplayConfig + DisplayConfigGetDeviceInfo -> per-output HDR state,
    bit depth, SDR white level and rotation.

Everything is keyed by the GDI device name (e.g. r"\\.\DISPLAY5") so the two
sources can be joined.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from ...core.types import DisplayInfo, rotation_to_degrees
from .com import RECT, set_process_dpi_aware, user32

__all__ = ["DisplayInfo", "enumerate_displays", "set_process_dpi_aware"]

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# QueryDisplayConfig structs
# --------------------------------------------------------------------------- #
QDC_ONLY_ACTIVE_PATHS = 0x00000002
ERROR_SUCCESS = 0

TYPE_GET_SOURCE_NAME = 1
TYPE_GET_TARGET_NAME = 2
TYPE_GET_ADVANCED_COLOR_INFO = 9
TYPE_GET_SDR_WHITE_LEVEL = 11


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class _PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", wintypes.BOOL), ("statusFlags", wintypes.UINT)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE_INFO),
                ("targetInfo", _PATH_TARGET_INFO), ("flags", wintypes.UINT)]


class _MODE_INFO(ctypes.Structure):
    # union payload is 64 bytes; we never read it.
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", LUID), ("blob", ctypes.c_byte * 64)]


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", LUID), ("id", wintypes.UINT)]


class _ADVANCED_COLOR_INFO(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("value", wintypes.UINT),
                ("colorEncoding", wintypes.UINT), ("bitsPerColorChannel", wintypes.UINT)]


class _SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER),
                ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class _TARGET_DEVICE_NAME_FLAGS(ctypes.Structure):
    _fields_ = [("value", wintypes.UINT)]


class _TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER),
                ("flags", _TARGET_DEVICE_NAME_FLAGS),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.USHORT),
                ("edidProductCodeId", wintypes.USHORT),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


class _SDR_WHITE_LEVEL(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("SDRWhiteLevel", wintypes.ULONG)]


COLOR_ENCODING = {0: "RGB", 1: "YCbCr444", 2: "YCbCr422", 3: "YCbCr420", 4: "Intensity"}


# --------------------------------------------------------------------------- #
# Monitor rectangles (physical pixels)
# --------------------------------------------------------------------------- #
class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


MONITORINFOF_PRIMARY = 0x1
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
    ctypes.POINTER(RECT), wintypes.LPARAM)


def _monitor_rects() -> dict:
    """gdi_name -> (rect tuple, is_primary) in physical virtual-desktop pixels."""
    out: dict = {}

    def cb(hmon, hdc, lprc, lparam):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out[mi.szDevice] = (
                (r.left, r.top, r.right - r.left, r.bottom - r.top),
                bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            )
        return True

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(cb), 0)
    return out


def _device_info(struct, info_type: int, adapter: LUID, tid: int) -> bool:
    struct.header.type = info_type
    struct.header.size = ctypes.sizeof(struct)
    struct.header.adapterId = adapter
    struct.header.id = tid
    return user32.DisplayConfigGetDeviceInfo(ctypes.byref(struct)) == ERROR_SUCCESS


def enumerate_displays() -> list[DisplayInfo]:
    """Return one DisplayInfo per active display, joined across both APIs."""
    rects = _monitor_rects()

    n_path = wintypes.UINT()
    n_mode = wintypes.UINT()
    if user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), ctypes.byref(n_mode)) != ERROR_SUCCESS:
        log.warning("GetDisplayConfigBufferSizes failed; falling back to rect-only enumeration")
        return _fallback_from_rects(rects)
    paths = (_PATH_INFO * n_path.value)()
    modes = (_MODE_INFO * n_mode.value)()
    if user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), paths,
                                 ctypes.byref(n_mode), modes, None) != ERROR_SUCCESS:
        log.warning("QueryDisplayConfig failed; falling back to rect-only enumeration")
        return _fallback_from_rects(rects)

    displays: list[DisplayInfo] = []
    for i in range(n_path.value):
        p = paths[i]
        src = _SOURCE_DEVICE_NAME()
        if not _device_info(src, TYPE_GET_SOURCE_NAME, p.sourceInfo.adapterId, p.sourceInfo.id):
            continue
        gdi = src.viewGdiDeviceName

        ac = _ADVANCED_COLOR_INFO()
        hdr_supported = hdr_enabled = False
        bpc = 8
        enc = 0
        if _device_info(ac, TYPE_GET_ADVANCED_COLOR_INFO, p.targetInfo.adapterId, p.targetInfo.id):
            hdr_supported = bool(ac.value & 0x1)
            hdr_enabled = bool(ac.value & 0x2)
            bpc = ac.bitsPerColorChannel
            enc = ac.colorEncoding

        wl = _SDR_WHITE_LEVEL()
        nits = 80.0
        if _device_info(wl, TYPE_GET_SDR_WHITE_LEVEL, p.targetInfo.adapterId, p.targetInfo.id):
            nits = wl.SDRWhiteLevel / 1000.0 * 80.0

        tn = _TARGET_DEVICE_NAME()
        friendly = ""
        if _device_info(tn, TYPE_GET_TARGET_NAME, p.targetInfo.adapterId, p.targetInfo.id):
            friendly = tn.monitorFriendlyDeviceName

        rect, primary = rects.get(gdi, ((0, 0, 0, 0), False))
        displays.append(DisplayInfo(
            index=len(displays), gdi_name=gdi, friendly_name=friendly or gdi,
            x=rect[0], y=rect[1], width=rect[2], height=rect[3], is_primary=primary,
            hdr_supported=hdr_supported, hdr_enabled=hdr_enabled, bits_per_color=bpc,
            sdr_white_nits=nits, color_encoding=COLOR_ENCODING.get(enc, str(enc)),
            rotation=rotation_to_degrees(p.targetInfo.rotation)))

    displays.sort(key=lambda d: (not d.is_primary, d.x, d.y))
    for i, d in enumerate(displays):
        d.index = i
    log.debug("enumerated %d display(s)", len(displays))
    return displays


def _fallback_from_rects(rects: dict) -> list[DisplayInfo]:
    out = []
    for i, (gdi, (rect, primary)) in enumerate(rects.items()):
        out.append(DisplayInfo(i, gdi, gdi, rect[0], rect[1], rect[2], rect[3],
                               primary, False, False, 8, 80.0, "RGB"))
    return out


# Re-exported for callers that still import it from here.
def virtual_desktop_bounds(displays):
    from ...core.types import virtual_desktop_bounds as _vdb
    return _vdb(displays)


if __name__ == "__main__":
    set_process_dpi_aware()
    for d in enumerate_displays():
        tag = "HDR-ON" if d.hdr_enabled else ("HDR-capable" if d.hdr_supported else "SDR")
        star = "*" if d.is_primary else " "
        print(f"{star}[{d.index}] {d.gdi_name} {d.friendly_name!r} "
              f"{d.width}x{d.height}+{d.x}+{d.y} rot={d.rotation} {tag} "
              f"{d.bits_per_color}bpc {d.color_encoding} sdrwhite={d.sdr_white_nits:.0f}nits")
