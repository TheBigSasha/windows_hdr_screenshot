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

import importlib.metadata
import struct

import numpy as np

from ..core import color

CP_BT2020 = 9      # H.273 colour primaries
TC_PQ = 16         # H.273 transfer characteristics (SMPTE ST 2084)
MC_BT2020_NCL = 9  # H.273 matrix coefficients (BT.2020 non-constant luminance)


def available() -> bool:
    """True if a working HEVC-encoding pillow-heif is importable."""
    from ..codecs import capability
    return capability("heic").available


def provider_details(profile: str | None = None) -> tuple[str, str | None]:
    try:
        import pillow_heif  # pyright: ignore[reportMissingImports]
        version = getattr(pillow_heif, "__version__", None)
    except Exception:  # pragma: no cover - optional dependency
        version = None
    if version is None:
        try:
            version = importlib.metadata.version("pillow-heif")
        except importlib.metadata.PackageNotFoundError:
            version = None
    return "pillow-heif", str(version) if version else None


def _read_nclx(data: bytes) -> dict[str, int] | None:
    """Read the complete CICP/NCLX tuple from a HEIF container."""
    i = data.find(b"nclx")
    if i < 0 or i + 12 > len(data):
        return None
    cp, tc, mc = struct.unpack_from(">HHH", data, i + 4)
    return {
        "color_primaries": cp,
        "transfer_characteristics": tc,
        "matrix_coefficients": mc,
        "full_range_flag": data[i + 10] & 1,
    }


def _assert_cicp(data: bytes) -> dict[str, int]:
    actual = _read_nclx(data)
    expected = {
        "color_primaries": CP_BT2020,
        "transfer_characteristics": TC_PQ,
        "matrix_coefficients": MC_BT2020_NCL,
        "full_range_flag": 1,
    }
    if actual is None or actual != expected:
        raise RuntimeError(f"HEIC CICP/NCLX metadata mismatch: expected {expected}, got {actual}")
    return actual


def write_heic_pq(path: str, linear: np.ndarray, quality: int = 90) -> dict:
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
    with open(path, "rb") as fp:
        cicp = _assert_cicp(fp.read())
    return {"metadata_standard": "CICP/NCLX", "cicp": cicp}
