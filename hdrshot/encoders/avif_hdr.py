"""True 10-bit BT.2020 PQ HDR AVIF via imagecodecs/libavif."""
from __future__ import annotations

import struct

import numpy as np

from ..core import color

CP_BT2020 = 9
TC_PQ = 16
MC_BT2020_NCL = 9


def available() -> bool:
    """Return the registry's tri-state probe as a compatibility boolean."""
    from ..codecs import capability
    return capability("avif-hdr").available


def write_avif_pq(path: str, linear: np.ndarray, quality: int = 90) -> None:
    """Write a 10-bit BT.2020 PQ HDR AVIF from an scRGB buffer."""
    import imagecodecs  # pyright: ignore[reportMissingImports]

    pq10 = color.scrgb_to_pq_bt2020_u16(linear, bit_depth=10)
    encoded = imagecodecs.avif_encode(
        np.ascontiguousarray(pq10),
        level=max(0, min(100, quality)),
        bitspersample=10,
        pixelformat="yuv444",
        primaries=CP_BT2020,
        transfer=TC_PQ,
        matrix=MC_BT2020_NCL,
    )
    with open(path, "wb") as fp:
        fp.write(encoded)


def probe(path: str) -> dict | None:
    """Return decoded dimensions and the AVIF nclx colour profile."""
    try:
        import imagecodecs  # pyright: ignore[reportMissingImports]
        with open(path, "rb") as fp:
            data = fp.read()
        arr = np.asarray(imagecodecs.avif_decode(data))
    except Exception:
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
    """Parse an ISO-BMFF ``colr`` box carrying an ``nclx`` profile."""
    i = data.find(b"nclx")
    if i == -1 or i + 10 > len(data):
        return None
    cp, tc, mc = struct.unpack_from(">HHH", data, i + 4)
    return {"color_primaries": cp, "transfer_characteristics": tc,
            "matrix_coefficients": mc}
