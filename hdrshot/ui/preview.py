"""Post-capture preview card: shows the shot, HDR/SDR badge and save options.

Encoding runs on a worker thread (issue #16) so a 4K UltraHDR/EXR save doesn't
freeze the UI; controls disable while saving. Defaults, save folder and filename
template come from the persisted config (issues #4, #5).
"""
from __future__ import annotations

import os
import subprocess

import numpy as np
from PySide6.QtCore import Qt, QThreadPool
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

from ..core import color, pipeline
from .workers import EncodeWorker

# (id, label, description)
FORMAT_ITEMS = [
    ("auto", "Auto", "best format for the content"),
    ("ultrahdr", "UltraHDR JPEG", "SDR JPEG + gain map · opens everywhere, HDR on capable viewers"),
    ("exr", "OpenEXR", "lossless linear scRGB · editing / archival"),
    ("heic", "HEIC (PQ)", "10-bit BT.2020 PQ · needs the [heic] extra"),
    ("png", "PNG", "lossless 8-bit SDR"),
    ("jpeg", "JPEG", "8-bit SDR"),
    ("avif", "AVIF", "10-bit PQ with [avif-hdr], else 8-bit SDR"),
]


def _qimage_from_rgb(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class PreviewWindow(QWidget):
    def __init__(self, result: pipeline.CaptureResult, config=None, on_saved=None):
        super().__init__(None, Qt.Window)
        self.result = result
        self.config = config
        self.on_saved = on_saved
        self.default_dir = config.resolved_save_dir() if config else pipeline.default_save_dir()
        self.template = config.get("filename_template") if config else None
        self._sdr_u8 = color.scrgb_to_preview_u8(result.linear, result.sdr_white_nits)
        self._saved_path: str | None = None

        self.setWindowTitle("HDR Shot — Preview")
        self.setObjectName("preview")
        self.setMinimumWidth(560)
        self.setAttribute(Qt.WA_DeleteOnClose)   # closing frees the FP16 buffer
        self._build()

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

        pix = QPixmap.fromImage(_qimage_from_rgb(self._sdr_u8))
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
        self._update_hint()
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

    def _populate_formats(self):
        from ..encoders import heic
        is_hdr = self.result.hdr_capable_content
        heic_ok = heic.available()
        default = (self.config.default_format() if self.config else "auto")
        for fid, label, _ in FORMAT_ITEMS:
            self.combo.addItem(label, fid)
            if fid == "heic" and not heic_ok:
                idx = self.combo.count() - 1
                self.combo.model().item(idx).setEnabled(False)
                self.combo.setItemData(idx, "install hdrshot[heic] to enable", Qt.ToolTipRole)
        # Select the configured default, else UltraHDR for HDR / PNG for SDR.
        want = default if default != "auto" else ("ultrahdr" if is_hdr else "png")
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == want and self.combo.model().item(i).isEnabled():
                self.combo.setCurrentIndex(i)
                break

    def _update_hint(self):
        fid = self.combo.currentData()
        for k, _, desc in FORMAT_ITEMS:
            if k == fid:
                self.hint.setText(desc)
                break

    # -- actions ----------------------------------------------------------- #
    def _set_saving(self, saving: bool):
        for w in (self.save_btn, self.combo, self.copy_btn):
            w.setEnabled(not saving)
        if saving:
            self.setCursor(Qt.WaitCursor)
            self.status.setText("Saving…")
        else:
            self.unsetCursor()

    def _save(self):
        fid = self.combo.currentData()
        self._set_saving(True)
        gq = self.config.get("gainmap_quality") if self.config else None
        gd = self.config.get("gainmap_downscale") if self.config else None
        worker = EncodeWorker(self.result, fid, self.default_dir, template=self.template,
                              gainmap_quality=gq, gainmap_downscale=gd)
        worker.signals.finished.connect(self._on_saved_ok)
        worker.signals.error.connect(self._on_saved_err)
        QThreadPool.globalInstance().start(worker)

    def _on_saved_ok(self, info: dict):
        self._set_saving(False)
        info["peak_nits"] = self.result.stats.get("peak_nits", 0.0)
        self._saved_path = info["path"]
        self.open_btn.setEnabled(True)
        self.status.setText(f"Saved {os.path.basename(info['path'])}")
        if self.config and self.config.get("copy_to_clipboard"):
            self._copy(quiet=True)
        if self.on_saved:
            self.on_saved(info, self._sdr_u8)

    def _on_saved_err(self, msg: str):
        self._set_saving(False)
        self.status.setText(f"Save failed: {msg}")

    def _copy(self, quiet: bool = False):
        QGuiApplication.clipboard().setImage(_qimage_from_rgb(self._sdr_u8))
        if not quiet:
            self.status.setText("Copied SDR image to clipboard")

    def _open_folder(self):
        if self._saved_path:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._saved_path)])
