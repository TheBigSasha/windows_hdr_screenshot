r"""DXGI Desktop Duplication capture in scRGB FP16.

We drive D3D11 + DXGI directly through ctypes (no Windows SDK / compiler needed),
calling COM methods by their vtable index. The key move is
``IDXGIOutput5::DuplicateOutput1`` requesting ``DXGI_FORMAT_R16G16B16A16_FLOAT``:
Windows then hands us the desktop in **scRGB FP16** whether the display is in SDR
or HDR mode. In SDR mode every value lands in [0, 1]; in HDR mode highlights go
above 1.0 — that is the "true HDR" signal we preserve end-to-end.

One D3D11 device is created per adapter; every output on that adapter is
duplicated and one frame is grabbed. Results are keyed by GDI device name so they
join to :mod:`hdrshot.displays`.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np
from comtypes import GUID

from . import color

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002

# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
_d3d11 = ctypes.windll.d3d11
_dxgi = ctypes.windll.dxgi

HRESULT = ctypes.c_long
S_OK = 0
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_NOT_FOUND = 0x887A0002

DXGI_FORMAT_R16G16B16A16_FLOAT = 10
DXGI_FORMAT_R10G10B10A2_UNORM = 24
DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_FORMAT_R8G8B8A8_UNORM_SRGB = 29
DXGI_FORMAT_B8G8R8A8_UNORM = 87
DXGI_FORMAT_B8G8R8A8_UNORM_SRGB = 91
D3D_DRIVER_TYPE_UNKNOWN = 0
D3D11_SDK_VERSION = 7
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1

IID_IDXGIFactory1 = GUID("{770aae78-f26f-4dba-a829-253c83d1b387}")
IID_IDXGIOutput5 = GUID("{80A07424-AB52-42EB-833C-0C42FD282D98}")
IID_ID3D11Texture2D = GUID("{6f15aaf2-d208-4e89-9ab4-489535d34f9c}")


# --------------------------------------------------------------------------- #
# Structs
# --------------------------------------------------------------------------- #
class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32), ("DesktopCoordinates", _RECT),
                ("AttachedToDesktop", wintypes.BOOL), ("Rotation", wintypes.UINT),
                ("Monitor", wintypes.HMONITOR)]


class DXGI_SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", wintypes.UINT), ("Quality", wintypes.UINT)]


class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                ("MipLevels", wintypes.UINT), ("ArraySize", wintypes.UINT),
                ("Format", wintypes.UINT), ("SampleDesc", DXGI_SAMPLE_DESC),
                ("Usage", wintypes.UINT), ("BindFlags", wintypes.UINT),
                ("CPUAccessFlags", wintypes.UINT), ("MiscFlags", wintypes.UINT)]


class _POINTER_POSITION(ctypes.Structure):
    _fields_ = [("Position", _POINT), ("Visible", wintypes.BOOL)]


class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [("LastPresentTime", ctypes.c_longlong),
                ("LastMouseUpdateTime", ctypes.c_longlong),
                ("AccumulatedFrames", wintypes.UINT),
                ("RectsCoalesced", wintypes.BOOL),
                ("ProtectedContentMaskedOut", wintypes.BOOL),
                ("PointerPosition", _POINTER_POSITION),
                ("TotalMetadataBufferSize", wintypes.UINT),
                ("PointerShapeBufferSize", wintypes.UINT)]


class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", wintypes.UINT),
                ("DepthPitch", wintypes.UINT)]


class _DXGI_MODE_DESC(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                ("RefreshNum", wintypes.UINT), ("RefreshDen", wintypes.UINT),
                ("Format", wintypes.UINT), ("ScanlineOrdering", wintypes.UINT),
                ("Scaling", wintypes.UINT)]


class DXGI_OUTDUPL_DESC(ctypes.Structure):
    _fields_ = [("ModeDesc", _DXGI_MODE_DESC), ("Rotation", wintypes.UINT),
                ("DesktopImageInSystemMemory", wintypes.BOOL)]


class DXGI_MAPPED_RECT(ctypes.Structure):
    _fields_ = [("Pitch", ctypes.c_int), ("pBits", ctypes.c_void_p)]


# --------------------------------------------------------------------------- #
# Raw COM vtable calling
# --------------------------------------------------------------------------- #
def _vfn(ptr, index, restype, *argtypes):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
    addr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(addr)


def _release(ptr):
    if ptr:
        _vfn(ptr, 2, ctypes.c_ulong)(ptr)


def _qi(ptr, iid):
    out = ctypes.c_void_p()
    hr = _vfn(ptr, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        ptr, ctypes.byref(iid), ctypes.byref(out))
    return out.value if hr == S_OK else None


class CaptureError(Exception):
    pass


RDW_INVALIDATE = 0x0001
RDW_ALLCHILDREN = 0x0080
RDW_UPDATENOW = 0x0100


def _force_desktop_repaint() -> None:
    """Desktop Duplication only delivers a real frame (AccumulatedFrames >= 1)
    when desktop *content* changes; a static screen yields only pointer-only
    frames whose surface is blank. Asking every top-level window to repaint the
    same content is an invisible way to generate that content change, so a
    one-shot grab works on an idle desktop. (Cursor moves don't count — the
    pointer is composited separately.)"""
    _user32.RedrawWindow(None, None, None,
                         RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW)


class _CaptureSession:
    """Keeps the display awake for the duration of a capture."""

    def __enter__(self):
        _kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        return self

    def __exit__(self, *exc):
        _kernel32.SetThreadExecutionState(ES_CONTINUOUS)


@dataclass
class MonitorCapture:
    gdi_name: str
    x: int
    y: int
    width: int
    height: int
    rotation: int
    linear: np.ndarray  # (H, W, 3) float32, scRGB linear, 1.0 == 80 nits


# --------------------------------------------------------------------------- #
# Device / factory helpers
# --------------------------------------------------------------------------- #
def _create_factory():
    factory = ctypes.c_void_p()
    hr = _dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != S_OK:
        raise CaptureError(f"CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08X}")
    return factory.value


def _create_device(adapter_ptr):
    dev = ctypes.c_void_p()
    ctx = ctypes.c_void_p()
    feat = ctypes.c_int()
    proto = ctypes.WINFUNCTYPE(
        HRESULT, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_void_p))
    hr = proto(("D3D11CreateDevice", _d3d11))(
        adapter_ptr, D3D_DRIVER_TYPE_UNKNOWN, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        None, 0, D3D11_SDK_VERSION, ctypes.byref(dev), ctypes.byref(feat), ctypes.byref(ctx))
    if hr != S_OK:
        raise CaptureError(f"D3D11CreateDevice failed: 0x{hr & 0xFFFFFFFF:08X}")
    return dev.value, ctx.value


def _enum_adapters(factory):
    adapters = []
    i = 0
    while True:
        a = ctypes.c_void_p()
        hr = _vfn(factory, 12, HRESULT, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            factory, i, ctypes.byref(a))  # EnumAdapters1
        if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND or hr != S_OK:
            break
        adapters.append(a.value)
        i += 1
    return adapters


def _enum_outputs(adapter):
    outputs = []
    i = 0
    while True:
        o = ctypes.c_void_p()
        hr = _vfn(adapter, 7, HRESULT, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            adapter, i, ctypes.byref(o))  # EnumOutputs
        if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND or hr != S_OK:
            break
        outputs.append(o.value)
        i += 1
    return outputs


def _output_desc(output):
    desc = DXGI_OUTPUT_DESC()
    _vfn(output, 7, HRESULT, ctypes.POINTER(DXGI_OUTPUT_DESC))(output, ctypes.byref(desc))
    return desc


# --------------------------------------------------------------------------- #
# The actual grab
# --------------------------------------------------------------------------- #
def _grab_output(device, ctx, output, desc, timeout_ms=120, tries=45) -> np.ndarray | None:
    out5 = _qi(output, IID_IDXGIOutput5)
    if not out5:
        raise CaptureError("IDXGIOutput5 unavailable (needs Windows 10 1803+)")
    dup = ctypes.c_void_p()
    fmts = (ctypes.c_uint * 1)(DXGI_FORMAT_R16G16B16A16_FLOAT)
    try:
        hr = _vfn(out5, 26, HRESULT, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                  ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_void_p))(
            out5, device, 0, 1, fmts, ctypes.byref(dup))  # DuplicateOutput1
        if hr != S_OK:
            raise CaptureError(f"DuplicateOutput1 failed: 0x{hr & 0xFFFFFFFF:08X}")
        oddesc = DXGI_OUTDUPL_DESC()
        _vfn(dup.value, 7, HRESULT, ctypes.POINTER(DXGI_OUTDUPL_DESC))(
            dup.value, ctypes.byref(oddesc))  # GetDesc
        return _acquire_and_copy(device, ctx, dup.value, oddesc, timeout_ms, tries)
    finally:
        _release(dup.value) if dup.value else None
        _release(out5)


def _acquire_and_copy(device, ctx, dup, oddesc, timeout_ms, tries) -> np.ndarray | None:
    frame_info = DXGI_OUTDUPL_FRAME_INFO()
    acquire = _vfn(dup, 8, HRESULT, ctypes.c_uint,
                   ctypes.POINTER(DXGI_OUTDUPL_FRAME_INFO), ctypes.POINTER(ctypes.c_void_p))
    release_frame = _vfn(dup, 14, HRESULT)

    for _ in range(tries):
        _force_desktop_repaint()
        resource = ctypes.c_void_p()
        hr = acquire(dup, timeout_ms, ctypes.byref(frame_info), ctypes.byref(resource))
        code = hr & 0xFFFFFFFF
        if code == DXGI_ERROR_WAIT_TIMEOUT:
            continue
        if code == DXGI_ERROR_ACCESS_LOST:
            raise CaptureError("desktop duplication access lost (mode change?)")
        if hr != S_OK:
            raise CaptureError(f"AcquireNextFrame failed: 0x{code:08X}")

        # A pointer-only update (AccumulatedFrames == 0) leaves the desktop
        # surface blank; only a content frame carries the real image.
        if frame_info.AccumulatedFrames == 0:
            if resource.value:
                _release(resource.value)
            release_frame(dup)
            continue

        try:
            if oddesc.DesktopImageInSystemMemory:
                if resource.value:
                    _release(resource.value)
                return _map_desktop_surface(dup, oddesc)
            tex = _qi(resource.value, IID_ID3D11Texture2D)
            _release(resource.value)
            if tex is None:
                return None
            try:
                return _copy_texture(device, ctx, tex)
            finally:
                _release(tex)
        finally:
            release_frame(dup)

    return None


def _map_desktop_surface(dup, oddesc) -> np.ndarray:
    mr = DXGI_MAPPED_RECT()
    hr = _vfn(dup, 12, HRESULT, ctypes.POINTER(DXGI_MAPPED_RECT))(
        dup, ctypes.byref(mr))  # MapDesktopSurface
    if hr != S_OK:
        raise CaptureError(f"MapDesktopSurface failed: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        w, h, fmt = oddesc.ModeDesc.Width, oddesc.ModeDesc.Height, oddesc.ModeDesc.Format
        raw = ctypes.string_at(mr.pBits, mr.Pitch * h)
        return _decode_surface(raw, mr.Pitch, w, h, fmt)
    finally:
        _vfn(dup, 13, HRESULT)(dup)  # UnMapDesktopSurface


def _decode_surface(raw: bytes, row_pitch: int, w: int, h: int, fmt: int) -> np.ndarray:
    """Decode a mapped desktop surface to scRGB-linear float32 (H, W, 3),
    where 1.0 == 80 nits.

    Windows only composites in FP16 scRGB while HDR is *enabled*; an SDR desktop
    comes back as 8-bit BGRA (or occasionally 10-bit). We normalise every case to
    the same linear representation so the rest of the pipeline is format-agnostic.
    """
    if fmt == DXGI_FORMAT_R16G16B16A16_FLOAT:
        arr = np.frombuffer(raw, np.float16).reshape(h, row_pitch // 2)
        rgba = arr[:, : w * 4].reshape(h, w, 4)
        return np.ascontiguousarray(rgba[:, :, :3]).astype(np.float32)  # already linear scRGB

    if fmt in (DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB,
               DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM_SRGB):
        arr = np.frombuffer(raw, np.uint8).reshape(h, row_pitch)
        px = arr[:, : w * 4].reshape(h, w, 4).astype(np.float32) / 255.0
        if fmt in (DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB):
            rgb = px[:, :, 2::-1]            # BGRA -> RGB
        else:
            rgb = px[:, :, :3]              # RGBA -> RGB
        return color.srgb_eotf(np.ascontiguousarray(rgb)).astype(np.float32)

    if fmt == DXGI_FORMAT_R10G10B10A2_UNORM:
        v = np.frombuffer(raw, np.uint32).reshape(h, row_pitch // 4)[:, :w]
        r = (v & 0x3FF).astype(np.float32) / 1023.0
        g = ((v >> 10) & 0x3FF).astype(np.float32) / 1023.0
        b = ((v >> 20) & 0x3FF).astype(np.float32) / 1023.0
        rgb = np.stack([r, g, b], axis=-1)
        return color.srgb_eotf(rgb).astype(np.float32)

    raise CaptureError(f"unsupported desktop surface format {fmt}")


def _copy_texture(device, ctx, tex) -> np.ndarray:
    # Source description (Texture2D::GetDesc == vtable index 10).
    desc = D3D11_TEXTURE2D_DESC()
    _vfn(tex, 10, None, ctypes.POINTER(D3D11_TEXTURE2D_DESC))(tex, ctypes.byref(desc))

    staging_desc = D3D11_TEXTURE2D_DESC()
    ctypes.memmove(ctypes.byref(staging_desc), ctypes.byref(desc), ctypes.sizeof(desc))
    staging_desc.Usage = D3D11_USAGE_STAGING
    staging_desc.BindFlags = 0
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ
    staging_desc.MiscFlags = 0

    staging = ctypes.c_void_p()
    hr = _vfn(device, 5, HRESULT, ctypes.POINTER(D3D11_TEXTURE2D_DESC),
              ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
        device, ctypes.byref(staging_desc), None, ctypes.byref(staging))  # CreateTexture2D
    if hr != S_OK:
        raise CaptureError(f"CreateTexture2D(staging) failed: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        _vfn(ctx, 47, None, ctypes.c_void_p, ctypes.c_void_p)(
            ctx, staging, tex)  # CopyResource(dst, src)

        mapped = D3D11_MAPPED_SUBRESOURCE()
        hr = _vfn(ctx, 14, HRESULT, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int,
                  ctypes.c_uint, ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE))(
            ctx, staging, 0, D3D11_MAP_READ, 0, ctypes.byref(mapped))  # Map
        if hr != S_OK:
            raise CaptureError(f"Map(staging) failed: 0x{hr & 0xFFFFFFFF:08X}")
        try:
            raw = ctypes.string_at(mapped.pData, mapped.RowPitch * desc.Height)
            return _decode_surface(raw, mapped.RowPitch, desc.Width, desc.Height, desc.Format)
        finally:
            _vfn(ctx, 15, None, ctypes.c_void_p, ctypes.c_uint)(ctx, staging, 0)  # Unmap
    finally:
        _release(staging.value)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def capture_all() -> dict[str, MonitorCapture]:
    """Capture one scRGB FP16 frame from every attached output.

    Returns a mapping ``gdi_name -> MonitorCapture``.
    """
    factory = _create_factory()
    results: dict[str, MonitorCapture] = {}
    try:
        with _CaptureSession():
            for adapter in _enum_adapters(factory):
                try:
                    device, ctx = _create_device(adapter)
                except CaptureError:
                    _release(adapter)
                    continue
                try:
                    for output in _enum_outputs(adapter):
                        try:
                            desc = _output_desc(output)
                            if not desc.AttachedToDesktop:
                                continue
                            linear = _grab_output(device, ctx, output, desc)
                            if linear is None:
                                continue
                            r = desc.DesktopCoordinates
                            results[desc.DeviceName] = MonitorCapture(
                                gdi_name=desc.DeviceName, x=r.left, y=r.top,
                                width=r.right - r.left, height=r.bottom - r.top,
                                rotation=desc.Rotation, linear=linear)
                        finally:
                            _release(output)
                finally:
                    _release(device)
                    _release(ctx)
                    _release(adapter)
    finally:
        _release(factory)
    if not results:
        raise CaptureError("No desktop frames captured (all outputs timed out).")
    return results


if __name__ == "__main__":
    from . import displays, color
    displays.set_process_dpi_aware()
    caps = capture_all()
    for name, c in caps.items():
        st = color.hdr_stats(c.linear, 80.0)
        print(f"{name}: {c.width}x{c.height}+{c.x}+{c.y} rot={c.rotation} "
              f"peak_ratio={st['peak_ratio']:.2f} peak_nits={st['peak_nits']:.0f} "
              f"has_hdr={st['has_hdr']} dtype={c.linear.dtype}")
