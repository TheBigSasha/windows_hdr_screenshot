"""hdrshot - True HDR screenshots on Windows.

Captures the desktop as scRGB FP16 (the color space Windows composites HDR in)
and saves it losslessly (EXR), as a gain-map UltraHDR JPEG (the macOS-equivalent
HDR-in-a-JPEG format), 10-bit PQ HEIC, or 10-bit PQ AVIF. Falls back to standard
SDR encodes (PNG / JPEG / AVIF) when no HDR content is present.

The package is layered so the pure parts import on any OS:

    core/       color science, pipeline, image types  (platform-free)
    encoders/   exr · ultrahdr · heic · avif · sdr     (platform-free)
    backends/   CaptureBackend protocol + win32/       (lazy ctypes)
    ui/         Qt shell                               (optional extra)

This module is the single source of the version string; ``pyproject.toml`` reads
it back via ``[tool.setuptools.dynamic]``.
"""

__version__ = "0.2.0"
