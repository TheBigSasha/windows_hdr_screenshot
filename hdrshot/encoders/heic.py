"""HEIC writer: 10-bit BT.2020 PQ (HDR10-style still image).

This is the container Apple uses for HDR screenshots. We tag it with the correct
CICP nclx profile (primaries 9 = BT.2020, transfer 16 = SMPTE ST 2084 PQ, matrix
9) so HDR-aware viewers treat it as HDR10.

HEIC is an **optional extra** (``pip install hdrshot[heic]``) because the standard
``pillow-heif`` wheels bundle an x265 (GPL) HEVC encoder — see
``THIRD_PARTY_NOTICES.md``. Callers should check :func:`available` and degrade
gracefully when it is not installed.
"""
from __future__ import annotations

import numpy as np

from ..core import color

CP_BT2020 = 9      # H.273 colour primaries
TC_PQ = 16         # H.273 transfer characteristics (SMPTE ST 2084)
MC_BT2020_NCL = 9  # H.273 matrix coefficients (BT.2020 non-constant luminance)


def available() -> bool:
    """True if a working HEVC-encoding pillow-heif is importable."""
    from ..codecs import capability
    return capability("heic").available


def write_heic_pq(path: str, linear: np.ndarray, quality: int = 90) -> None:
    import pillow_heif  # pyright: ignore[reportMissingImports]

    h, w = linear.shape[:2]
    # 16-bit PQ signal; pillow-heif emits 10-bit (top 10 bits) unless 12-bit opt.
    pq16 = color.scrgb_to_pq_bt2020_u16(linear, bit_depth=16)  # (H, W, 3) uint16
    data = np.ascontiguousarray(pq16, dtype="<u2").tobytes()

    with open(path, "wb") as fp:
        pillow_heif.encode(
            "RGB;16", (w, h), data, fp,
            quality=quality,
            chroma=444,
            color_primaries=CP_BT2020,
            transfer_characteristics=TC_PQ,  # note: plural is the real kwarg
            matrix_coefficients=MC_BT2020_NCL,
            full_range_flag=1,
        )
