"""Post-capture preview card: shows the shot, HDR/SDR badge and save options."""
from __future__ import annotations

import os
import subprocess

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import color, pipeline

# (id, label, description) — HDR options first when the shot is HDR.
FORMAT_ITEMS = [
    ("auto", "Auto", "best format for the content"),
    ("ultrahdr", "UltraHDR JPEG", "SDR JPEG + gain map · opens everywhere, HDR on capable viewers"),
    ("exr", "OpenEXR", "lossless linear scRGB · editing / archival"),
    ("heic", "HEIC (PQ)", "10-bit BT.2020 PQ · HDR10-style still"),
    ("png", "PNG", "lossless 8-bit SDR"),
    ("jpeg", "JPEG", "8-bit SDR"),
    ("avif", "AVIF", "8-bit SDR (compact)"),
]


def _qimage_from_rgb(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class PreviewWindow(QWidget):
    def __init__(self, result: pipeline.CaptureResult, default_dir: str | None = None):
        super().__init__(None, Qt.Window)
        self.result = result
        self.default_dir = default_dir or pipeline.default_save_dir()
        self._sdr_u8 = color.scrgb_to_preview_u8(result.linear, result.sdr_white_nits)
        self._saved_path: str | None = None

        self.setWindowTitle("HDR Shot — Preview")
        self.setObjectName("preview")
        self.setMinimumWidth(560)
        self._build()

    # -- ui ---------------------------------------------------------------- #
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # badge row
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

        # image
        pix = QPixmap.fromImage(_qimage_from_rgb(self._sdr_u8))
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setPixmap(pix.scaled(720, 460, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._thumb.setObjectName("thumb")
        self._thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._thumb)

        # format row
        fr = QHBoxLayout()
        fr.addWidget(QLabel("Save as"))
        self.combo = QComboBox()
        for fid, label, _ in FORMAT_ITEMS:
            self.combo.addItem(label, fid)
        self.combo.setCurrentIndex(1 if is_hdr else 4)  # UltraHDR or PNG
        self.combo.currentIndexChanged.connect(self._update_hint)
        fr.addWidget(self.combo)
        self.hint = QLabel()
        self.hint.setObjectName("subtle")
        fr.addWidget(self.hint, 1)
        root.addLayout(fr)
        self._update_hint()

        # buttons
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

    def _update_hint(self):
        fid = self.combo.currentData()
        for k, _, desc in FORMAT_ITEMS:
            if k == fid:
                self.hint.setText(desc)
                break

    # -- actions ----------------------------------------------------------- #
    def _save(self):
        fid = self.combo.currentData()
        self.setCursor(Qt.WaitCursor)
        try:
            info = pipeline.save(self.result, fid, self.default_dir)
            self._saved_path = info["path"]
            self.open_btn.setEnabled(True)
            self.status.setText(f"Saved {os.path.basename(info['path'])}")
        except Exception as e:  # pragma: no cover - surfaced to the user
            self.status.setText(f"Save failed: {e}")
        finally:
            self.unsetCursor()

    def _copy(self):
        QGuiApplication.clipboard().setImage(_qimage_from_rgb(self._sdr_u8))
        self.status.setText("Copied SDR image to clipboard")

    def _open_folder(self):
        if self._saved_path:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._saved_path)])
