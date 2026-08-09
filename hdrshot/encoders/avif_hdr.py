"""True 10-bit BT.2020 PQ HDR AVIF via imagecodecs/libavif."""
from __future__ import annotations

import importlib.metadata
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


def provider_details(profile: str | None = None) -> tuple[str, str | None]:
    try:
        import imagecodecs  # pyright: ignore[reportMissingImports]
        package_version = getattr(imagecodecs, "__version__", None)
        libavif_version = getattr(imagecodecs, "avif_version", lambda: None)()
    except Exception:  # pragma: no cover - optional dependency
        package_version = None
        libavif_version = None
    if package_version is None:
        try:
            package_version = importlib.metadata.version("imagecodecs")
        except importlib.metadata.PackageNotFoundError:
            package_version = None
    version = str(package_version) if package_version else None
    if libavif_version:
        version = f"{version} ({libavif_version})" if version else str(libavif_version)
    return "imagecodecs/libavif", version


def write_avif_pq(path: str, linear: np.ndarray, quality: int = 90) -> dict:
    """Write a 10-bit BT.2020 PQ HDR AVIF from an scRGB buffer."""
    import imagecodecs  # pyright: ignore[reportMissingImports]

    pq10 = color.scrgb_to_pq_bt2020_u16(linear, bit_depth=10)
    encoded = imagecodecs.avif_encode(
        np.ascontiguousarray(pq10),
        level=max(0, min(100, quality)),
        bitspersample=10,
        # imagecodecs/libavif exposes no range keyword and its native provider
        # emits limited-range NCLX for this RGB-to-YUV path. Keep the
        # non-subsampled 10-bit format and verify that exact emitted contract
        # below rather than silently writing mismatched HDR metadata.
        pixelformat="yuv444",
        primaries=CP_BT2020,
        transfer=TC_PQ,
        matrix=MC_BT2020_NCL,
    )
    cicp = _assert_cicp(encoded)
    with open(path, "wb") as fp:
        fp.write(encoded)
    return {"metadata_standard": "CICP/NCLX", "cicp": cicp}


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
           "matrix_coefficients": None, "full_range_flag": None}
    if nclx:
        out.update(nclx)
    return out


def _read_nclx(data: bytes) -> dict[str, int] | None:
    """Parse an ISO-BMFF ``colr`` box carrying an ``nclx`` profile."""
    i = data.find(b"nclx")
    if i == -1 or i + 12 > len(data):
        return None
    cp, tc, mc = struct.unpack_from(">HHH", data, i + 4)
    return {"color_primaries": cp, "transfer_characteristics": tc,
            "matrix_coefficients": mc, "full_range_flag": data[i + 10] & 1}


def _assert_cicp(data: bytes) -> dict[str, int]:
    expected = {
        "color_primaries": CP_BT2020,
        "transfer_characteristics": TC_PQ,
        "matrix_coefficients": MC_BT2020_NCL,
        "full_range_flag": 0,
    }
    actual = _read_nclx(data)
    if actual is None or actual != expected:
        raise RuntimeError(f"AVIF CICP/NCLX metadata mismatch: expected {expected}, got {actual}")
    return actual
