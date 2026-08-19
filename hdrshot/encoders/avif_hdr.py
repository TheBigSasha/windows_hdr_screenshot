"""True 10-bit BT.2020 PQ HDR AVIF via imagecodecs/libavif."""
from __future__ import annotations

import importlib.metadata

import numpy as np

from ..core import color
from .avif_container import MAX_FILE_BYTES, AvifContainerError, inspect_avif

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
    encoded = bytes(imagecodecs.avif_encode(
        np.ascontiguousarray(pq10),
        level=max(0, min(100, quality)),
        bitspersample=10,
        # Screenshot text and one-pixel UI edges need full chroma resolution.
        # imagecodecs/libavif treats this RGB input as full range; the emitted
        # primary-item CICP contract is verified below before anything is saved.
        pixelformat="yuv444",
        primaries=CP_BT2020,
        transfer=TC_PQ,
        matrix=MC_BT2020_NCL,
    ))
    cicp = _assert_cicp(encoded)
    with open(path, "wb") as fp:
        fp.write(encoded)
    return {"metadata_standard": "CICP/NCLX", "cicp": cicp}


def probe(path: str) -> dict | None:
    """Return bounded primary-item AVIF metadata without native decoding."""
    try:
        with open(path, "rb") as fp:
            data = fp.read(MAX_FILE_BYTES + 1)
        return inspect_avif(data)
    except (OSError, AvifContainerError):
        return None


def _read_nclx(data: bytes) -> dict[str, int] | None:
    """Return only the ``nclx`` property associated with the primary item."""
    try:
        metadata = inspect_avif(data)
    except AvifContainerError:
        return None
    fields = (
        "color_primaries",
        "transfer_characteristics",
        "matrix_coefficients",
        "full_range_flag",
    )
    if any(metadata.get(field) is None for field in fields):
        return None
    return {field: int(metadata[field]) for field in fields}


def _assert_cicp(data: bytes) -> dict[str, int]:
    expected = {
        "color_primaries": CP_BT2020,
        "transfer_characteristics": TC_PQ,
        "matrix_coefficients": MC_BT2020_NCL,
        "full_range_flag": 1,
    }
    actual = _read_nclx(data)
    if actual is None or actual != expected:
        raise RuntimeError(f"AVIF CICP/NCLX metadata mismatch: expected {expected}, got {actual}")
    return actual
