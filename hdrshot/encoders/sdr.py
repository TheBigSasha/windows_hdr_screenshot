"""Standard SDR encoders: PNG, JPEG, AVIF (8-bit).

These take an already-tonemapped 8-bit sRGB RGB array (H, W, 3) uint8.
"""
from __future__ import annotations

import importlib.metadata

import numpy as np
import PIL
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


def provider_details(profile: str | None = None) -> tuple[str, str | None]:
    """Return the loaded provider and its real installed version.

    The registry's built-in profiles historically reported hdrshot's version.
    That is not the codec provider version, so PNG/JPEG now expose Pillow's
    version directly and AVIF exposes the plugin distribution when available.
    """
    if profile == "avif-sdr":
        try:
            import pillow_avif  # pyright: ignore[reportMissingImports]
            version = getattr(pillow_avif, "__version__", None)
        except Exception:  # pragma: no cover - optional dependency
            version = None
        if version is None:
            try:
                version = importlib.metadata.version("pillow-avif-plugin")
            except importlib.metadata.PackageNotFoundError:
                version = None
        return "pillow-avif-plugin", str(version) if version else None
    return "Pillow", str(getattr(PIL, "__version__", None) or "unknown")


def avif_available() -> bool:
    """Return whether the optional 8-bit AVIF plugin is installed."""
    from ..codecs import capability
    return capability("avif-sdr").available
