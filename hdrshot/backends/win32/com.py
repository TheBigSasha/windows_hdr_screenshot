r"""Shared Win32 / COM plumbing for the capture backend.

Everything here binds ``ctypes.windll`` at import, which is safe because this
module is only ever imported through :func:`hdrshot.backends.get_backend` on
Windows — never off-platform.

Raw vtable calling
------------------
We invoke COM methods by **vtable index** (no Windows SDK / compiler needed). A
wrong index is a memory-corrupting call, not an exception, so every index used in
the backend is named in :data:`VTBL` below with its interface, method and the
header it came from. Call sites reference these names rather than bare integers.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from comtypes import GUID

log = logging.getLogger("hdrshot.backends.win32")

# --------------------------------------------------------------------------- #
# System libraries (bound once; Windows-only import path)
# --------------------------------------------------------------------------- #
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
d3d11 = ctypes.windll.d3d11
dxgi = ctypes.windll.dxgi

# --------------------------------------------------------------------------- #
# HRESULT + common codes
# --------------------------------------------------------------------------- #
HRESULT = ctypes.c_long
S_OK = 0
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
E_ACCESSDENIED = 0x80070005


def hr_ok(hr: int) -> bool:
    return (hr & 0xFFFFFFFF) == S_OK


def hr_hex(hr: int) -> str:
    return f"0x{hr & 0xFFFFFFFF:08X}"


# --------------------------------------------------------------------------- #
# Shared structs (were duplicated across capture.py / displays.py)
# --------------------------------------------------------------------------- #
class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


# --------------------------------------------------------------------------- #
# Vtable index table — the single source of truth for the fragile indices.
# Format: interface method -> index. Derived from the Windows SDK headers noted.
# --------------------------------------------------------------------------- #
class VTBL:
    # IUnknown (Unknwn.h) — base of every COM interface.
    QueryInterface = 0
    AddRef = 1
    Release = 2
    # IDXGIFactory1 (dxgi.h) : inherits IDXGIObject(3) + IDXGIFactory(5..11).
    IDXGIFactory1_EnumAdapters1 = 12
    # IDXGIAdapter (dxgi.h)
    IDXGIAdapter_EnumOutputs = 7
    # IDXGIOutput (dxgi.h)
    IDXGIOutput_GetDesc = 7
    # IDXGIOutput5 (dxgi1_5.h)
    IDXGIOutput5_DuplicateOutput1 = 26
    # IDXGIOutputDuplication (dxgi1_2.h)
    IDXGIOutputDuplication_GetDesc = 7
    IDXGIOutputDuplication_AcquireNextFrame = 8
    IDXGIOutputDuplication_MapDesktopSurface = 12
    IDXGIOutputDuplication_UnMapDesktopSurface = 13
    IDXGIOutputDuplication_ReleaseFrame = 14
    # ID3D11Device (d3d11.h)
    ID3D11Device_CreateTexture2D = 5
    # ID3D11DeviceContext (d3d11.h)
    ID3D11DeviceContext_Map = 14
    ID3D11DeviceContext_Unmap = 15
    ID3D11DeviceContext_CopyResource = 47
    # ID3D11Texture2D (d3d11.h) : inherits ID3D11DeviceChild(3..6) + ID3D11Resource(7..9)
    ID3D11Texture2D_GetDesc = 10


# Common IIDs.
IID_IDXGIFactory1 = GUID("{770aae78-f26f-4dba-a829-253c83d1b387}")
IID_IDXGIOutput5 = GUID("{80A07424-AB52-42EB-833C-0C42FD282D98}")
IID_ID3D11Texture2D = GUID("{6f15aaf2-d208-4e89-9ab4-489535d34f9c}")


# --------------------------------------------------------------------------- #
# Raw vtable calling
# --------------------------------------------------------------------------- #
def vfn(ptr, index: int, restype, *argtypes):
    """Bind the ``index``-th method in ``ptr``'s vtable as a callable.

    The first argument of the returned callable is always the interface pointer
    (``this``). ``index`` must come from :class:`VTBL`.
    """
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
    addr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(addr)


def com_release(ptr) -> None:
    if ptr:
        vfn(ptr, VTBL.Release, ctypes.c_ulong)(ptr)


def com_qi(ptr, iid: GUID):
    """QueryInterface; returns the raw pointer value or ``None``."""
    out = ctypes.c_void_p()
    hr = vfn(ptr, VTBL.QueryInterface, HRESULT,
             ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        ptr, ctypes.byref(iid), ctypes.byref(out))
    return out.value if hr == S_OK else None


# --------------------------------------------------------------------------- #
# DPI awareness (physical-pixel coordinates everywhere)
# --------------------------------------------------------------------------- #
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def set_process_dpi_aware() -> None:
    """Opt in to per-monitor-v2 DPI awareness so every Win32 coordinate we read is
    in physical pixels. Must run before any window is created. Idempotent."""
    try:
        user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            user32.SetProcessDPIAware()
