"""Standard SDR encoders: PNG, JPEG, AVIF (8-bit).

These take an already-tonemapped 8-bit sRGB RGB array (H, W, 3) uint8.
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def _to_image(rgb_u8: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(rgb_u8[..., :3]), "RGB")


def write_png(path: str, rgb_u8: np.ndarray) -> None:
    _to_image(rgb_u8).save(path, format="PNG", optimize=False, compress_level=6)


def write_jpeg(path: str, rgb_u8: np.ndarray, quality: int = 95) -> None:
    _to_image(rgb_u8).save(path, format="JPEG", quality=quality, subsampling=0)


def write_avif_sdr(path: str, rgb_u8: np.ndarray, quality: int = 80) -> None:
    """8-bit SDR AVIF. (For true 10-bit PQ HDR AVIF see
    :mod:`hdrshot.encoders.avif_hdr`.)"""
    import pillow_avif  # pyright: ignore[reportMissingImports]  # noqa: F401
    _to_image(rgb_u8).save(path, format="AVIF", quality=quality)


def avif_available() -> bool:
    """Return whether the optional 8-bit AVIF plugin is installed."""
    from ..codecs import capability
    return capability("avif-sdr").available
