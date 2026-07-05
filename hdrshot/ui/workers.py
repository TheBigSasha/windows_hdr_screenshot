"""Background workers so capture and encode never block the Qt main thread (issue #16).

`pipeline` is already Qt-free, so these are thin `QRunnable`s that emit their result
back to the GUI thread via signals. Capture (up to several seconds of DXGI retries
+ full-res FP16 copies) and encode (two JPEG passes for UltraHDR, zip for EXR) both
run here instead of freezing the UI.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ..core import color, pipeline

log = logging.getLogger(__name__)


class _Signals(QObject):
    finished = Signal(object)
    error = Signal(str)


class CaptureWorker(QRunnable):
    """Grabs every display, enumerates them, and pre-renders SDR previews — all
    off the main thread. Emits ``(caps, disps, previews)``."""

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            caps = self.backend.capture_all()
            disps = self.backend.enumerate_displays()
            white = {d.gdi_name: d.sdr_white_nits for d in disps}
            previews = {name: color.scrgb_to_preview_u8(mc.linear, white.get(name, 80.0))
                        for name, mc in caps.items()}
            self.signals.finished.emit((caps, disps, previews))
        except Exception as e:  # pragma: no cover - surfaced to the GUI
            log.exception("capture worker failed")
            self.signals.error.emit(str(e))


class EncodeWorker(QRunnable):
    """Encodes/saves a `CaptureResult` off the main thread. Emits the info dict."""

    def __init__(self, result: pipeline.CaptureResult, fmt: str, out_dir: str,
                 template: str | None = None, gainmap_quality: int | None = None,
                 gainmap_downscale: int | None = None):
        super().__init__()
        self.result = result
        self.fmt = fmt
        self.out_dir = out_dir
        self.template = template
        self.gainmap_quality = gainmap_quality
        self.gainmap_downscale = gainmap_downscale
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            info = pipeline.save(self.result, self.fmt, self.out_dir, template=self.template,
                                 gainmap_quality=self.gainmap_quality,
                                 gainmap_downscale=self.gainmap_downscale)
            self.signals.finished.emit(info)
        except Exception as e:  # pragma: no cover - surfaced to the GUI
            log.exception("encode worker failed")
            self.signals.error.emit(str(e))
