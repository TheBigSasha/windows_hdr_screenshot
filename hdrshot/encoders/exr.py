"""OpenEXR writer (lossless linear HDR).

Stores the captured scRGB buffer verbatim as HALF (fp16) R/G/B channels, linear,
BT.709 primaries, 1.0 == 80 nits. This is the reference-quality output: no tone
mapping, no gamut clipping, no quantisation beyond the fp16 the GPU gave us.
"""
from __future__ import annotations

import numpy as np
import OpenEXR


def write_exr(path: str, linear: np.ndarray, sdr_white_nits: float = 80.0) -> None:
    rgb = np.ascontiguousarray(linear[..., :3], dtype=np.float16)
    # Each channel MUST be its own contiguous array: the OpenEXR binding reads the
    # underlying buffer linearly and silently scrambles strided views like
    # rgb[..., 0] (whole-image corruption past the first rows).
    channels = {
        "R": np.ascontiguousarray(rgb[..., 0]),
        "G": np.ascontiguousarray(rgb[..., 1]),
        "B": np.ascontiguousarray(rgb[..., 2]),
    }
    if linear.shape[-1] >= 4:
        channels["A"] = np.ascontiguousarray(linear[..., 3], dtype=np.float16)

    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        # BT.709 / sRGB primaries + D65 white.
        "chromaticities": (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290),
        "hdrshot:colorSpace": "scRGB linear (BT.709), 1.0 = 80 nits",
        "hdrshot:sdrWhiteNits": float(sdr_white_nits),
    }
    f = OpenEXR.File(header, channels)
    f.write(path)
