"""True 10-bit BT.2020 PQ HDR AVIF via libavif (issue #10).

The pip ``pillow-avif-plugin`` wheels are 8-bit with no CICP control, so they can
only write SDR AVIF. ``imagecodecs`` bundles a full libavif that can emit 10-bit
with an nclx colour profile (primaries 9 / transfer 16 / matrix 9 = BT.2020 PQ).

This is an **optional extra** (``pip install hdrshot[avif-hdr]``). When it is not
installed, :func:`available` returns ``False`` and the pipeline falls back to an
8-bit SDR AVIF (with a logged warning).
"""
from __future__ import annotations

# imagecodecs is the optional [avif-hdr] extra; absence is handled at runtime.
# pyright: reportMissingImports=false
import logging
import struct

import numpy as np

from ..core import color

log = logging.getLogger(__name__)

CP_BT2020 = 9   # H.273 colour primaries (== imagecodecs AVIF.COLOR_PRIMARIES.BT2020)
TC_PQ = 16      # H.273 transfer characteristics (SMPTE ST 2084 PQ)
MC_BT2020_NCL = 9  # H.273 matrix coefficients (BT.2020 non-constant luminance)


def available() -> bool:
    """True if imagecodecs with a working AVIF codec is importable."""
    try:
        import imagecodecs
    except Exception:
        return False
    return bool(getattr(getattr(imagecodecs, "AVIF", None), "available", False))


def write_avif_pq(path: str, linear: np.ndarray, quality: int = 90) -> None:
    """Write a 10-bit BT.2020 PQ HDR AVIF from an scRGB FP16 buffer."""
    import imagecodecs

    pq10 = color.scrgb_to_pq_bt2020_u16(linear, bit_depth=10)  # (H, W, 3) uint16, 10-bit values
    encoded = imagecodecs.avif_encode(
        np.ascontiguousarray(pq10),
        level=max(0, min(100, quality)),   # imagecodecs: 0..100 quality (100 = lossless)
        bitspersample=10,
        pixelformat="yuv444",
        primaries=CP_BT2020,
        transfer=TC_PQ,
        matrix=MC_BT2020_NCL,
    )
    with open(path, "wb") as fp:
        fp.write(encoded)


def probe(path: str) -> dict | None:
    """Return {bit_depth, width, height, transfer_characteristics, color_primaries,
    matrix_coefficients} for an AVIF, or None if it can't be read.

    imagecodecs doesn't surface the nclx profile on decode, so the CICP is parsed
    straight out of the file's ``colr``/``nclx`` box.
    """
    try:
        import imagecodecs
        with open(path, "rb") as fp:
            data = fp.read()
        arr = np.asarray(imagecodecs.avif_decode(data))
    except Exception as e:
        log.debug("avif probe failed: %s", e)
        return None
    bit_depth = 10 if arr.dtype == np.uint16 else 8
    h, w = arr.shape[:2]
    nclx = _read_nclx(data)
    out = {"bit_depth": bit_depth, "width": int(w), "height": int(h),
           "transfer_characteristics": None, "color_primaries": None,
           "matrix_coefficients": None}
    if nclx:
        out.update(nclx)
    return out


def _read_nclx(data: bytes) -> dict | None:
    """Parse the ISO-BMFF ``colr`` box with ``nclx`` colour type: after the
    'nclx' fourcc come three big-endian uint16 (primaries, transfer, matrix)."""
    i = data.find(b"nclx")
    if i == -1 or i + 4 + 6 > len(data):
        return None
    cp, tc, mc = struct.unpack_from(">HHH", data, i + 4)
    return {"color_primaries": cp, "transfer_characteristics": tc,
            "matrix_coefficients": mc}
