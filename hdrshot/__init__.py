"""hdrshot - True HDR screenshots on Windows.

Captures the desktop as scRGB FP16 and writes strict, explicit output profiles.
The runtime codec registry is shared by the pipeline, UI, CLI, configuration,
self-test, and agent JSON; unavailable profiles are reported instead of being
silently converted to another representation.

The package is layered so the pure parts import on any OS:

    core/       color science, pipeline, image types  (platform-free)
    encoders/   exr · ultrahdr · heic · avif · sdr     (platform-free)
    backends/   CaptureBackend protocol + win32/       (lazy ctypes)
    ui/         Qt shell                               (optional extra)

This module is the single source of the version string; ``pyproject.toml`` reads
it back via ``[tool.setuptools.dynamic]``.
"""

__version__ = "0.4.8"
