"""Platform-free core: color science, image types, and the capture/encode pipeline.

Nothing in this package touches Win32 or ctypes.windll, so it imports and runs on
any OS (Linux/macOS CI, library reuse). Platform capture lives in
:mod:`hdrshot.backends`; the encoders live in :mod:`hdrshot.encoders`.
"""
from .types import DisplayInfo, MonitorCapture, virtual_desktop_bounds

__all__ = ["DisplayInfo", "MonitorCapture", "virtual_desktop_bounds"]
