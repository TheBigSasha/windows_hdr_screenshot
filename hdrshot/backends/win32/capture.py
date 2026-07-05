r"""DXGI Desktop Duplication capture in scRGB FP16 (Win32 backend).

We drive D3D11 + DXGI directly through ctypes (no Windows SDK / compiler needed),
calling COM methods by their vtable index (see :mod:`hdrshot.backends.win32.com`).
The key move is ``IDXGIOutput5::DuplicateOutput1`` requesting
``DXGI_FORMAT_R16G16B16A16_FLOAT``: Windows then hands us the desktop in **scRGB
FP16** whether the display is in SDR or HDR mode. In SDR mode every value lands in
[0, 1]; in HDR mode highlights go above 1.0 — that is the "true HDR" signal we
preserve end-to-end.

One D3D11 device is created per adapter; every output on that adapter is
duplicated and one frame is grabbed. Results are keyed by GDI device name so they
join to :mod:`hdrshot.backends.win32.displays`.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

import numpy as np

from ...core import color
from ...core.types import MonitorCapture, apply_rotation, rotation_to_degrees
from .com import (
    DXGI_ERROR_ACCESS_LOST,
    DXGI_ERROR_NOT_FOUND,
    DXGI_ERROR_WAIT_TIMEOUT,
    HRESULT,
    RECT,
    S_OK,
    VTBL,
    IID_ID3D11Texture2D,
    IID_IDXGIFactory1,
    IID_IDXGIOutput5,
    com_qi,
    com_release,
    d3d11,
    dxgi,
    hr_hex,
    kernel32,
    user32,
    vfn,
)

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002

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


class CaptureError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Structs (DXGI/D3D11-specific; shared RECT/POINT come from com.py)
# --------------------------------------------------------------------------- #
class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32), ("DesktopCoordinates", RECT),
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


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


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
# Idle-desktop repaint
# --------------------------------------------------------------------------- #
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
    user32.RedrawWindow(None, None, None,
                        RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW)


class _CaptureSession:
    """Keeps the display awake for the duration of a capture."""

    def __enter__(self):
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        return self

    def __exit__(self, *exc):
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)


# --------------------------------------------------------------------------- #
# Device / factory helpers
# --------------------------------------------------------------------------- #
def _create_factory():
    factory = ctypes.c_void_p()
    hr = dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != S_OK:
        raise CaptureError(f"CreateDXGIFactory1 failed: {hr_hex(hr)}")
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
    hr = proto(("D3D11CreateDevice", d3d11))(
        adapter_ptr, D3D_DRIVER_TYPE_UNKNOWN, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        None, 0, D3D11_SDK_VERSION, ctypes.byref(dev), ctypes.byref(feat), ctypes.byref(ctx))
    if hr != S_OK:
        raise CaptureError(f"D3D11CreateDevice failed: {hr_hex(hr)}")
    return dev.value, ctx.value


def _enum_adapters(factory):
    adapters = []
    i = 0
    while True:
        a = ctypes.c_void_p()
        hr = vfn(factory, VTBL.IDXGIFactory1_EnumAdapters1, HRESULT,
                 ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            factory, i, ctypes.byref(a))
        if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND or hr != S_OK:
            break
        adapters.append(a.value)
        i += 1
    log.debug("enumerated %d adapter(s)", len(adapters))
    return adapters


def _enum_outputs(adapter):
    outputs = []
    i = 0
    while True:
        o = ctypes.c_void_p()
        hr = vfn(adapter, VTBL.IDXGIAdapter_EnumOutputs, HRESULT,
                 ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            adapter, i, ctypes.byref(o))
        if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND or hr != S_OK:
            break
        outputs.append(o.value)
        i += 1
    return outputs


def _output_desc(output):
    desc = DXGI_OUTPUT_DESC()
    vfn(output, VTBL.IDXGIOutput_GetDesc, HRESULT, ctypes.POINTER(DXGI_OUTPUT_DESC))(
        output, ctypes.byref(desc))
    return desc


# --------------------------------------------------------------------------- #
# The actual grab
# --------------------------------------------------------------------------- #
def _grab_output(device, ctx, output, desc, timeout_ms=120, tries=45) -> np.ndarray | None:
    out5 = com_qi(output, IID_IDXGIOutput5)
    if not out5:
        raise CaptureError("IDXGIOutput5 unavailable (needs Windows 10 1803+)")
    dup = ctypes.c_void_p()
    fmts = (ctypes.c_uint * 1)(DXGI_FORMAT_R16G16B16A16_FLOAT)
    try:
        hr = vfn(out5, VTBL.IDXGIOutput5_DuplicateOutput1, HRESULT,
                 ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                 ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_void_p))(
            out5, device, 0, 1, fmts, ctypes.byref(dup))
        if hr != S_OK:
            raise CaptureError(f"DuplicateOutput1 failed: {hr_hex(hr)}")
        oddesc = DXGI_OUTDUPL_DESC()
        vfn(dup.value, VTBL.IDXGIOutputDuplication_GetDesc, HRESULT,
            ctypes.POINTER(DXGI_OUTDUPL_DESC))(dup.value, ctypes.byref(oddesc))
        return _acquire_and_copy(device, ctx, dup.value, oddesc, timeout_ms, tries)
    finally:
        com_release(dup.value) if dup.value else None
        com_release(out5)


def _acquire_and_copy(device, ctx, dup, oddesc, timeout_ms, tries) -> np.ndarray | None:
    frame_info = DXGI_OUTDUPL_FRAME_INFO()
    acquire = vfn(dup, VTBL.IDXGIOutputDuplication_AcquireNextFrame, HRESULT, ctypes.c_uint,
                  ctypes.POINTER(DXGI_OUTDUPL_FRAME_INFO), ctypes.POINTER(ctypes.c_void_p))
    release_frame = vfn(dup, VTBL.IDXGIOutputDuplication_ReleaseFrame, HRESULT)

    for attempt in range(tries):
        _force_desktop_repaint()
        resource = ctypes.c_void_p()
        hr = acquire(dup, timeout_ms, ctypes.byref(frame_info), ctypes.byref(resource))
        code = hr & 0xFFFFFFFF
        if code == DXGI_ERROR_WAIT_TIMEOUT:
            continue
        if code == DXGI_ERROR_ACCESS_LOST:
            raise CaptureError("desktop duplication access lost (mode change?)")
        if hr != S_OK:
            raise CaptureError(f"AcquireNextFrame failed: {hr_hex(hr)}")

        # A pointer-only update (AccumulatedFrames == 0) leaves the desktop
        # surface blank; only a content frame carries the real image.
        if frame_info.AccumulatedFrames == 0:
            if resource.value:
                com_release(resource.value)
            release_frame(dup)
            continue

        log.debug("acquired content frame after %d attempt(s)", attempt + 1)
        try:
            if oddesc.DesktopImageInSystemMemory:
                if resource.value:
                    com_release(resource.value)
                return _map_desktop_surface(dup, oddesc)
            tex = com_qi(resource.value, IID_ID3D11Texture2D)
            com_release(resource.value)
            if tex is None:
                return None
            try:
                return _copy_texture(device, ctx, tex)
            finally:
                com_release(tex)
        finally:
            release_frame(dup)

    log.warning("no content frame after %d attempts (idle desktop?)", tries)
    return None


def _map_desktop_surface(dup, oddesc) -> np.ndarray:
    mr = DXGI_MAPPED_RECT()
    hr = vfn(dup, VTBL.IDXGIOutputDuplication_MapDesktopSurface, HRESULT,
             ctypes.POINTER(DXGI_MAPPED_RECT))(dup, ctypes.byref(mr))
    if hr != S_OK:
        raise CaptureError(f"MapDesktopSurface failed: {hr_hex(hr)}")
    try:
        w, h, fmt = oddesc.ModeDesc.Width, oddesc.ModeDesc.Height, oddesc.ModeDesc.Format
        raw = ctypes.string_at(mr.pBits, mr.Pitch * h)
        return _decode_surface(raw, mr.Pitch, w, h, fmt)
    finally:
        vfn(dup, VTBL.IDXGIOutputDuplication_UnMapDesktopSurface, HRESULT)(dup)


def _decode_surface(raw: bytes, row_pitch: int, w: int, h: int, fmt: int) -> np.ndarray:
    """Decode a mapped desktop surface to scRGB-linear float32 (H, W, 3),
    where 1.0 == 80 nits.

    Windows only composites in FP16 scRGB while HDR is *enabled*; an SDR desktop
    comes back as 8-bit BGRA (or occasionally 10-bit). We normalise every case to
    the same linear representation so the rest of the pipeline is format-agnostic.
    """
    if fmt == DXGI_FORMAT_R16G16B16A16_FLOAT:
        log.debug("decoding FP16 scRGB surface %dx%d (HDR path)", w, h)
        arr = np.frombuffer(raw, np.float16).reshape(h, row_pitch // 2)
        rgba = arr[:, : w * 4].reshape(h, w, 4)
        return np.ascontiguousarray(rgba[:, :, :3]).astype(np.float32)  # already linear scRGB

    if fmt in (DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB,
               DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM_SRGB):
        log.debug("decoding 8-bit BGRA/RGBA surface %dx%d fmt=%d (SDR path)", w, h, fmt)
        arr = np.frombuffer(raw, np.uint8).reshape(h, row_pitch)
        px = arr[:, : w * 4].reshape(h, w, 4).astype(np.float32) / 255.0
        if fmt in (DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB):
            rgb = px[:, :, 2::-1]            # BGRA -> RGB
        else:
            rgb = px[:, :, :3]              # RGBA -> RGB
        return color.srgb_eotf(np.ascontiguousarray(rgb)).astype(np.float32)

    if fmt == DXGI_FORMAT_R10G10B10A2_UNORM:
        log.debug("decoding 10-bit surface %dx%d (SDR path)", w, h)
        v = np.frombuffer(raw, np.uint32).reshape(h, row_pitch // 4)[:, :w]
        r = (v & 0x3FF).astype(np.float32) / 1023.0
        g = ((v >> 10) & 0x3FF).astype(np.float32) / 1023.0
        b = ((v >> 20) & 0x3FF).astype(np.float32) / 1023.0
        rgb = np.stack([r, g, b], axis=-1)
        return color.srgb_eotf(rgb).astype(np.float32)

    raise CaptureError(f"unsupported desktop surface format {fmt}")


def _copy_texture(device, ctx, tex) -> np.ndarray:
    desc = D3D11_TEXTURE2D_DESC()
    vfn(tex, VTBL.ID3D11Texture2D_GetDesc, None, ctypes.POINTER(D3D11_TEXTURE2D_DESC))(
        tex, ctypes.byref(desc))

    staging_desc = D3D11_TEXTURE2D_DESC()
    ctypes.memmove(ctypes.byref(staging_desc), ctypes.byref(desc), ctypes.sizeof(desc))
    staging_desc.Usage = D3D11_USAGE_STAGING
    staging_desc.BindFlags = 0
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ
    staging_desc.MiscFlags = 0

    staging = ctypes.c_void_p()
    hr = vfn(device, VTBL.ID3D11Device_CreateTexture2D, HRESULT,
             ctypes.POINTER(D3D11_TEXTURE2D_DESC),
             ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
        device, ctypes.byref(staging_desc), None, ctypes.byref(staging))
    if hr != S_OK:
        raise CaptureError(f"CreateTexture2D(staging) failed: {hr_hex(hr)}")
    try:
        vfn(ctx, VTBL.ID3D11DeviceContext_CopyResource, None, ctypes.c_void_p, ctypes.c_void_p)(
            ctx, staging, tex)

        mapped = D3D11_MAPPED_SUBRESOURCE()
        hr = vfn(ctx, VTBL.ID3D11DeviceContext_Map, HRESULT, ctypes.c_void_p, ctypes.c_uint,
                 ctypes.c_int, ctypes.c_uint, ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE))(
            ctx, staging, 0, D3D11_MAP_READ, 0, ctypes.byref(mapped))
        if hr != S_OK:
            raise CaptureError(f"Map(staging) failed: {hr_hex(hr)}")
        try:
            raw = ctypes.string_at(mapped.pData, mapped.RowPitch * desc.Height)
            return _decode_surface(raw, mapped.RowPitch, desc.Width, desc.Height, desc.Format)
        finally:
            vfn(ctx, VTBL.ID3D11DeviceContext_Unmap, None, ctypes.c_void_p, ctypes.c_uint)(
                ctx, staging, 0)
    finally:
        com_release(staging.value)


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
                except CaptureError as e:
                    log.warning("skipping adapter (device creation failed): %s", e)
                    com_release(adapter)
                    continue
                try:
                    for output in _enum_outputs(adapter):
                        try:
                            desc = _output_desc(output)
                            if not desc.AttachedToDesktop:
                                continue
                            try:
                                linear = _grab_output(device, ctx, output, desc)
                            except CaptureError as e:
                                # One failing output (secure desktop, mode change,
                                # driver hiccup) must not abort the other monitors.
                                log.warning("skipping output %s: %s", desc.DeviceName, e)
                                continue
                            if linear is None:
                                log.warning("no frame for %s", desc.DeviceName)
                                continue
                            r = desc.DesktopCoordinates
                            dw, dh = r.right - r.left, r.bottom - r.top
                            deg = rotation_to_degrees(desc.Rotation)
                            # Rotate the panel-native buffer into desktop orientation so
                            # its shape matches the on-desktop rect (crops stay correct).
                            # 180 deg keeps the same shape, so it must rotate
                            # unconditionally — the shape test can only clear 90/270.
                            if deg == 180 or (
                                    deg and (linear.shape[1], linear.shape[0]) != (dw, dh)):
                                log.warning("display %s is rotated %d deg; rotating buffer "
                                            "(best-effort)", desc.DeviceName, deg)
                                linear = apply_rotation(linear, deg)
                            results[desc.DeviceName] = MonitorCapture(
                                gdi_name=desc.DeviceName, x=r.left, y=r.top,
                                width=dw, height=dh, rotation=deg, linear=linear)
                        finally:
                            com_release(output)
                finally:
                    com_release(device)
                    com_release(ctx)
                    com_release(adapter)
    finally:
        com_release(factory)
    if not results:
        raise CaptureError("No desktop frames captured (all outputs timed out).")
    log.info("captured %d output(s): %s", len(results), ", ".join(results))
    return results


if __name__ == "__main__":
    from ...core import color as _color
    from . import displays as _displays
    _displays.set_process_dpi_aware()
    caps = capture_all()
    for name, c in caps.items():
        st = _color.hdr_stats(c.linear, 80.0)
        print(f"{name}: {c.width}x{c.height}+{c.x}+{c.y} rot={c.rotation} "
              f"peak_ratio={st['peak_ratio']:.2f} peak_nits={st['peak_nits']:.0f} "
              f"has_hdr={st['has_hdr']} dtype={c.linear.dtype}")
