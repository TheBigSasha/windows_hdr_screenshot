"""hdrshot - True HDR screenshots on Windows.

Captures the desktop as scRGB FP16 (the color space Windows composites HDR in)
and saves it losslessly (EXR), as a gain-map UltraHDR JPEG (the macOS-equivalent
HDR-in-a-JPEG format), or as 10-bit PQ HEIC. Falls back to standard SDR encodes
(PNG / JPEG / AVIF) when no HDR content is present.
"""

__version__ = "0.1.0"
