"""Post-capture preview card: shows the shot, HDR/SDR badge and save options.

Encoding runs on a worker thread (issue #16) so a 4K UltraHDR/EXR save doesn't
freeze the UI; controls disable while saving. Defaults, save folder and filename
template come from the persisted config (issues #4, #5).
"""
from __future__ import annotations

import os
import subprocess

import numpy as np
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import CANONICAL_FORMATS, validate_format
from ..core import color, pipeline
from .workers import EncodeWorker

# (id, label, description)
FORMAT_ITEMS = [
    ("auto", "Auto", "best format for the content"),
    ("uhdr-jpeg", "UltraHDR JPEG", "SDR JPEG + gain map · HDR on compatible viewers"),
    ("uhdr-avif", "UltraHDR AVIF", "gain-map AVIF · needs the libultrahdr provider"),
    ("uhdr-heic", "UltraHDR HEIC", "gain-map HEIC · needs the libultrahdr provider"),
    ("pq-avif", "AVIF (PQ)", "single-rendition 10-bit BT.2020 PQ · needs [avif-hdr]"),
    ("pq-heic", "HEIC (PQ)", "single-rendition 10-bit BT.2020 PQ · needs [heic]"),
    ("exr", "OpenEXR", "lossless linear scRGB · editing / archival"),
    ("png", "PNG", "lossless 8-bit SDR"),
    ("jpeg", "JPEG", "8-bit SDR"),
    ("avif-sdr", "AVIF (SDR)", "8-bit SDR · needs the [avif-sdr] extra"),
]

assert tuple(item[0] for item in FORMAT_ITEMS) == CANONICAL_FORMATS


def _qimage_from_rgb(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class PreviewWindow(QWidget):
    def __init__(self, result: pipeline.CaptureResult, config=None, on_saved=None,
                 preview_image: QImage | None = None):
        super().__init__(None, Qt.Window)
        self.result = result
        self.config = config
        self.on_saved = on_saved
        self.default_dir = config.resolved_save_dir() if config else pipeline.default_save_dir()
        self.template = config.get("filename_template") if config else None
        self._preview_image = (preview_image.copy() if preview_image is not None
                               else _qimage_from_rgb(color.scrgb_to_preview_u8(
                                   result.linear, result.sdr_white_nits)))
        self._saved_path: str | None = None
        self._encode_worker: EncodeWorker | None = None
        self._auto_save_started = False

        self.setWindowTitle("HDR Shot — Preview")
        self.setObjectName("preview")
        self.setMinimumWidth(560)
        self.setAttribute(Qt.WA_DeleteOnClose)   # closing frees the FP16 buffer
        self._build()
        if config and config.get("auto_save"):
            QTimer.singleShot(0, self._auto_save)

    # -- ui ---------------------------------------------------------------- #
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        badges = QHBoxLayout()
        st = self.result.stats
        is_hdr = self.result.hdr_capable_content
        badge = QLabel("HDR" if is_hdr else "SDR")
        badge.setObjectName("hdrBadge" if is_hdr else "sdrBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedHeight(24)
        badges.addWidget(badge)

        d = self.result.display
        if is_hdr:
            info = f"peak {st['peak_ratio']:.1f}× paper white · {st['peak_nits']:.0f} nits"
        else:
            info = "no highlights above SDR white"
        if d is not None and not d.hdr_enabled and is_hdr:
            info += "   ·   (Windows HDR is OFF — turn it on for live HDR capture)"
        lbl = QLabel(info)
        lbl.setObjectName("subtle")
        badges.addWidget(lbl, 1)
        root.addLayout(badges)

        pix = QPixmap.fromImage(self._preview_image)
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setPixmap(pix.scaled(720, 460, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._thumb.setObjectName("thumb")
        self._thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._thumb)

        fr = QHBoxLayout()
        fr.addWidget(QLabel("Save as"))
        self.combo = QComboBox()
        self._populate_formats()
        fr.addWidget(self.combo)
        self.hint = QLabel()
        self.hint.setObjectName("subtle")
        fr.addWidget(self.hint, 1)
        root.addLayout(fr)
        self.combo.currentIndexChanged.connect(self._update_hint)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("sep")
        root.addWidget(line)

        br = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        br.addWidget(self.status, 1)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        for b in (self.copy_btn, self.open_btn, self.save_btn):
            br.addWidget(b)
        root.addLayout(br)
        self._update_hint()

    def _populate_formats(self):
        for fid, label, _ in FORMAT_ITEMS:
            self.combo.addItem(label, fid)
            checked = validate_format(fid, allow_legacy=False)
            if not checked.available:
                idx = self.combo.count() - 1
                self.combo.model().item(idx).setEnabled(False)
                self.combo.setItemData(idx, checked.message(), Qt.ToolTipRole)
        saved = (self.config.format_validation()
                 if self.config else validate_format("auto"))
        if saved.valid and saved.canonical:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == saved.canonical:
                    # Keep an unavailable explicit saved profile selected and
                    # visible. Never fall through to Auto or the first item.
                    self.combo.setCurrentIndex(i)
                    return
        if saved.value is not None:
            self.combo.addItem(f"Invalid saved value: {saved.value}", saved.value)
            idx = self.combo.count() - 1
            self.combo.model().item(idx).setEnabled(False)
            self.combo.setItemData(idx, saved.message(), Qt.ToolTipRole)
            self.combo.setCurrentIndex(idx)

    def _update_hint(self):
        fid = self.combo.currentData()
        checked = validate_format(fid, allow_legacy=False)
        for k, _, desc in FORMAT_ITEMS:
            if k == fid:
                self.hint.setText(desc if checked.selectable else checked.message())
                break
        else:
            self.hint.setText(checked.message())
        if hasattr(self, "save_btn"):
            self.save_btn.setEnabled(checked.selectable)

    # -- actions ----------------------------------------------------------- #
    def _set_saving(self, saving: bool):
        for w in (self.save_btn, self.combo, self.copy_btn):
            w.setEnabled(not saving)
        if saving:
            self.setCursor(Qt.WaitCursor)
            self.status.setText("Saving…")
        else:
            self.unsetCursor()
            self._update_hint()

    def _save(self):
        if self._encode_worker is not None:
            return
        fid = self.combo.currentData()
        checked = validate_format(fid, allow_legacy=False)
        if not checked.selectable:
            self.status.setText(checked.message())
            return
        self._set_saving(True)
        gq = self.config.get("gainmap_quality") if self.config else None
        gd = self.config.get("gainmap_downscale") if self.config else None
        worker = EncodeWorker(self.result, fid, self.default_dir, template=self.template,
                              gainmap_quality=gq, gainmap_downscale=gd)
        worker.signals.finished.connect(self._on_saved_ok)
        worker.signals.error.connect(self._on_saved_err)
        self._encode_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _auto_save(self):
        if self._auto_save_started:
            return
        self._auto_save_started = True
        self._save()

    def _on_saved_ok(self, info):
        self._encode_worker = None
        self._set_saving(False)
        # pipeline.save returns the immutable typed EncodeResult. Convert at the
        # UI boundary before attaching presentation-only peak data; 0.4.0 tried
        # to mutate the Mapping and crashed every successful save callback.
        payload = info.to_dict() if hasattr(info, "to_dict") else dict(info)
        payload["peak_nits"] = self.result.stats.get("peak_nits", 0.0)
        self._saved_path = payload["path"]
        self.open_btn.setEnabled(True)
        self.status.setText(f"Saved {os.path.basename(payload['path'])}")
        if self.config and self.config.get("copy_to_clipboard"):
            self._copy(quiet=True)
        if self.on_saved:
            self.on_saved(payload, self._preview_image)

    def _on_saved_err(self, msg: str):
        self._encode_worker = None
        self._set_saving(False)
        self.status.setText(f"Save failed: {msg}")

    def _copy(self, quiet: bool = False):
        QGuiApplication.clipboard().setImage(self._preview_image)
        if not quiet:
            self.status.setText("Copied SDR image to clipboard")

    def _open_folder(self):
        if self._saved_path:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._saved_path)])
