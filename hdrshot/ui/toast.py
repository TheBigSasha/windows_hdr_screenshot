"""Post-capture toast notification (issue #3).

A lightweight, always-on-top custom Qt toast — no WinRT app-identity requirement.
Shows a thumbnail, an HDR/SDR badge, the save path and peak nits, with Open,
Open folder and Copy actions, and auto-dismisses.
"""
from __future__ import annotations

import os
import subprocess

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

TOAST_STYLE = """
QWidget#toast { background: #202024; border: 1px solid #34343c; border-radius: 12px; }
QLabel { color: #e8e8ea; font-family: 'Segoe UI'; }
QLabel#toastTitle { font-size: 13px; font-weight: 600; }
QLabel#toastSub { color: #9a9aa2; font-size: 11px; }
QLabel#toastThumb { background: #101012; border: 1px solid #2c2c31; border-radius: 6px; }
QLabel#hdr { background: #113a24; color: #43d17f; border-radius: 5px; padding: 1px 8px;
             font-weight: 700; font-size: 11px; }
QLabel#sdr { background: #26262b; color: #a8a8b0; border-radius: 5px; padding: 1px 8px;
             font-weight: 700; font-size: 11px; }
QPushButton { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px;
              padding: 4px 10px; font-size: 11px; color: #e8e8ea; }
QPushButton:hover { background: #33333b; }
"""


def _qimage_from_rgb(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _as_qimage(preview: np.ndarray | QImage) -> QImage:
    return preview.copy() if isinstance(preview, QImage) else _qimage_from_rgb(preview)


class Toast(QWidget):
    """One notification. Call :meth:`popup` to show it bottom-right and auto-dismiss."""

    def __init__(self, preview_u8: np.ndarray | QImage, path: str, is_hdr: bool,
                 peak_nits: float, timeout_ms: int = 5000):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(TOAST_STYLE)
        self._path = path
        self._preview = preview_u8
        self._timeout = timeout_ms
        self._build(preview_u8, path, is_hdr, peak_nits)

    def _build(self, preview_u8, path, is_hdr, peak_nits):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        thumb = QLabel()
        thumb.setObjectName("toastThumb")
        pix = QPixmap.fromImage(_as_qimage(preview_u8))
        thumb.setPixmap(pix.scaled(84, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        thumb.setFixedSize(88, 64)
        thumb.setAlignment(Qt.AlignCenter)
        root.addWidget(thumb)

        col = QVBoxLayout()
        col.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel("Screenshot saved")
        title.setObjectName("toastTitle")
        top.addWidget(title)
        badge = QLabel("HDR" if is_hdr else "SDR")
        badge.setObjectName("hdr" if is_hdr else "sdr")
        top.addWidget(badge)
        top.addStretch(1)
        col.addLayout(top)

        sub = f"{os.path.basename(path)}"
        if is_hdr and peak_nits:
            sub += f"   ·   peak {peak_nits:.0f} nits"
        lbl = QLabel(sub)
        lbl.setObjectName("toastSub")
        col.addWidget(lbl)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for text, slot in (("Open", self._open), ("Folder", self._folder), ("Copy", self._copy)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        col.addLayout(actions)
        root.addLayout(col)

    def popup(self):
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 24, geo.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        QTimer.singleShot(self._timeout, self.close)

    # -- actions ------------------------------------------------------------ #
    def _open(self):
        if os.path.exists(self._path):
            os.startfile(self._path)  # noqa: S606 - user-initiated open

    def _folder(self):
        if os.path.exists(self._path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._path)])

    def _copy(self):
        QGuiApplication.clipboard().setImage(_as_qimage(self._preview))
