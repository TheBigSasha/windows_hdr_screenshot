r"""UltraHDR (Google v1) writer: base SDR JPEG + luminance gain-map JPEG.

This is the "HDR inside a JPEG" format Chrome, Android and the Windows Photos app
render on HDR displays, and the portable analog of what macOS screenshots produce.
A dumb decoder just sees the SDR base; an HDR-aware one multiplies the base by
``2^(gain)`` to recover highlights.

Container layout, MPF byte offsets, XMP packets and the encode formula are all per
the reference codec ``google/libultrahdr`` (jpegrutils.cpp / multipictureformat.cpp
/ gainmapmath.cpp), verified byte-for-byte.
"""
from __future__ import annotations

import io
import struct

import numpy as np
from PIL import Image

from ..core import color


class UltraHDREncodeError(RuntimeError):
    """A structural invariant of the UltraHDR container was violated."""

APP1, APP2, SOI = b"\xFF\xE1", b"\xFF\xE2", b"\xFF\xD8"
XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"  # 29 bytes incl NUL
OFFSET = 1.0 / 64.0                           # hdrgm default OffsetSDR/OffsetHDR
_LUMA_709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


# --------------------------------------------------------------------------- #
# Gain-map computation
# --------------------------------------------------------------------------- #
def compute_gainmap(linear: np.ndarray, sdr_white_nits: float,
                    downscale: int = 1, max_boost_ceiling_stops: float = 8.0):
    """Return (gainmap_u8 HxW, metadata dict) for an scRGB FP16 buffer.

    The gain map is a single-channel (luminance) map: recovery = normalised
    log2(Y_hdr/Y_sdr) ^ Gamma, quantised to 8-bit, exactly as libultrahdr writes.
    """
    norm = np.clip(linear[..., :3].astype(np.float32) / color.sdr_scale(sdr_white_nits), 0.0, None)
    sdr_lin = np.clip(norm, 0.0, 1.0)
    y_hdr = norm @ _LUMA_709
    y_sdr = sdr_lin @ _LUMA_709

    gain = (y_hdr + OFFSET) / (y_sdr + OFFSET)          # linear boost, >= ~1
    max_boost = float(np.max(gain)) if gain.size else 1.0
    min_boost = float(np.min(gain)) if gain.size else 1.0

    gain_max_log2 = float(np.clip(np.log2(max(max_boost, 1.0 + 1e-6)), 0.0, max_boost_ceiling_stops))
    gain_min_log2 = float(np.clip(np.log2(max(min_boost, 1.0)), 0.0, gain_max_log2))

    if gain_max_log2 - gain_min_log2 < 1e-6:            # no meaningful headroom -> flat map
        gm = np.zeros(y_hdr.shape, np.float32)
    else:
        log2g = np.clip(np.log2(np.clip(gain, 2 ** gain_min_log2, 2 ** gain_max_log2)),
                        gain_min_log2, gain_max_log2)
        gm = (log2g - gain_min_log2) / (gain_max_log2 - gain_min_log2)   # Gamma == 1.0
    gm_u8 = np.clip(gm * 255.0 + 0.5, 0, 255).astype(np.uint8)

    if downscale > 1:
        img = Image.fromarray(gm_u8, "L")
        img = img.resize((max(1, img.width // downscale), max(1, img.height // downscale)),
                         Image.Resampling.BILINEAR)
        gm_u8 = np.asarray(img)

    # Decoders interpolate display weight as (H - CapacityMin)/(CapacityMax -
    # CapacityMin); equal capacities (a uniformly-boosted or flat image) would
    # divide by zero. Keep the capacities strictly separated.
    cap_min_log2 = max(gain_min_log2, 0.0)
    cap_max_log2 = gain_max_log2
    if cap_max_log2 - cap_min_log2 < 1e-4:
        cap_min_log2 = 0.0
        cap_max_log2 = max(cap_max_log2, 1e-4)

    meta = {
        "gain_min_log2": gain_min_log2, "gain_max_log2": gain_max_log2, "gamma": 1.0,
        "offset_sdr": OFFSET, "offset_hdr": OFFSET,
        "cap_min_log2": cap_min_log2, "cap_max_log2": cap_max_log2,
    }
    return gm_u8, meta


# --------------------------------------------------------------------------- #
# MPF APP2 (validated against libultrahdr's byte dump)
# --------------------------------------------------------------------------- #
def _build_mpf_app2(primary_image_size: int, gainmap_size: int, gainmap_offset: int) -> bytes:
    sig = b"MPF\x00"
    endian = b"\x4D\x4D\x00\x2A"                    # big-endian MM + TIFF 0x002A
    body = struct.pack(">I", 8)                     # first-IFD offset from base
    body += struct.pack(">H", 3)                    # 3 IFD entries
    body += struct.pack(">HHI", 0xB000, 7, 4) + b"0100"          # MPFVersion
    body += struct.pack(">HHI", 0xB001, 4, 1) + struct.pack(">I", 2)  # NumberOfImages = 2
    body += struct.pack(">HHI", 0xB002, 7, 32)                   # MPEntry tag, 32 bytes
    bytes_written = len(sig) + len(endian) + len(body)
    mp_entry_offset = bytes_written - len(sig) + 4 + 4           # == 0x32
    body += struct.pack(">I", mp_entry_offset)
    body += struct.pack(">I", 0)                                 # attribute-IFD offset
    # Entry #1 primary
    body += struct.pack(">I", 0x00030000) + struct.pack(">I", primary_image_size)
    body += struct.pack(">I", 0) + struct.pack(">HH", 0, 0)
    # Entry #2 gain map
    body += struct.pack(">I", 0x00000000) + struct.pack(">I", gainmap_size)
    body += struct.pack(">I", gainmap_offset) + struct.pack(">HH", 0, 0)
    mpf = sig + endian + body
    return APP2 + struct.pack(">H", 2 + len(mpf)) + mpf          # length 0x0058


def _app1_xmp(xmp_xml: str) -> bytes:
    payload = XMP_NS + xmp_xml.encode("utf-8")
    return APP1 + struct.pack(">H", 2 + len(payload)) + payload


def _encode_jpeg(pil_img: Image.Image, quality: int, icc: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"format": "JPEG", "quality": quality, "subsampling": 0}
    if icc:
        kw["icc_profile"] = icc
    pil_img.save(buf, **kw)
    return buf.getvalue()


def _after_soi(jpeg: bytes) -> bytes:
    if jpeg[:2] != SOI:
        raise UltraHDREncodeError("expected JPEG SOI marker at start of encoded image")
    return jpeg[2:]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def write_ultrahdr(path: str, linear: np.ndarray, sdr_white_nits: float = 80.0,
                   quality: int = 90, base_u8: np.ndarray | None = None,
                   gainmap_downscale: int = 1) -> dict:
    """Write an UltraHDR JPEG. Returns the gain-map metadata used."""
    if base_u8 is None:
        base_u8 = color.scrgb_to_sdr_u8(linear, sdr_white_nits)
    gm_u8, meta = compute_gainmap(linear, sdr_white_nits, downscale=gainmap_downscale)

    gm_xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="hdrshot">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        'hdrgm:Version="1.0" '
        f'hdrgm:GainMapMin="{meta["gain_min_log2"]:.6f}" '
        f'hdrgm:GainMapMax="{meta["gain_max_log2"]:.6f}" '
        f'hdrgm:Gamma="{meta["gamma"]:.6f}" '
        f'hdrgm:OffsetSDR="{meta["offset_sdr"]:.6f}" '
        f'hdrgm:OffsetHDR="{meta["offset_hdr"]:.6f}" '
        f'hdrgm:HDRCapacityMin="{meta["cap_min_log2"]:.6f}" '
        f'hdrgm:HDRCapacityMax="{meta["cap_max_log2"]:.6f}" '
        'hdrgm:BaseRenditionIsHDR="False"/></rdf:RDF></x:xmpmeta>')
    gm_raw = _encode_jpeg(Image.fromarray(gm_u8, "L"), quality)
    # Gain-map image: SOI + hdrgm XMP + ISO 21496-1 full metadata block + scan.
    from . import iso_gainmap
    gainmap_jpeg = (SOI + _app1_xmp(gm_xmp) + iso_gainmap.full_segment(meta)
                    + _after_soi(gm_raw))
    gainmap_len = len(gainmap_jpeg)

    prim_xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="hdrshot">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0">'
        '<Container:Directory><rdf:Seq>'
        '<rdf:li rdf:parseType="Resource">'
        '<Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
        '<rdf:li rdf:parseType="Resource">'
        '<Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" '
        f'Item:Length="{gainmap_len}"/></rdf:li>'
        '</rdf:Seq></Container:Directory></rdf:Description></rdf:RDF></x:xmpmeta>')
    prim_xmp_app1 = _app1_xmp(prim_xmp)

    base_body = _after_soi(_encode_jpeg(Image.fromarray(base_u8[..., :3], "RGB"), quality))

    # Base image also carries a version-only ISO 21496-1 APP2 stub (before MPF) so
    # strict Apple readers detect the ISO gain map.
    iso_stub = iso_gainmap.version_stub_segment()

    # The MPF APP2 segment has a fixed structure (3 IFD entries + 2 fixed-width
    # MP entries), so its length is independent of the offset values it carries.
    # Build once with placeholder values to measure it, derive the offsets from
    # that measured length, then rebuild for real — no hard-coded byte count.
    mpf_seg_len = len(_build_mpf_app2(0, 0, 0))
    lead = 2 + len(prim_xmp_app1) + len(iso_stub)          # SOI + APP1 XMP + ISO stub
    primary_image_size = lead + mpf_seg_len + len(base_body)
    base_of_mpf = lead + 8                                 # MPF offsets are relative to the TIFF header
    gainmap_offset = primary_image_size - base_of_mpf      # gain-map SOI sits right after the primary
    mpf = _build_mpf_app2(primary_image_size, gainmap_len, gainmap_offset)
    if len(mpf) != mpf_seg_len:
        raise UltraHDREncodeError(
            f"MPF segment length is not fixed ({mpf_seg_len} -> {len(mpf)}); offset math invalid")

    with open(path, "wb") as f:
        f.write(SOI + prim_xmp_app1 + iso_stub + mpf + base_body + gainmap_jpeg)
    return meta
