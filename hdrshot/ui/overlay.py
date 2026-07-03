"""Full-screen region-selection overlay (Snipping-Tool / macOS style).

One :class:`Overlay` is created per :class:`QScreen`; a :class:`RegionSelector`
coordinates them. Each overlay shows the frozen SDR preview of its screen, dims
it, and lets the user rubber-band a region. Selection happens in the overlay's
logical coordinates and is mapped **proportionally** to the matched physical FP16
buffer, so it is correct under any per-monitor DPI without touching devicePixelRatio.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

ACCENT = QColor(0, 160, 255)
DIM = QColor(0, 0, 0, 120)


def _qimage_from_rgb(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class Overlay(QWidget):
    """Covers exactly one screen. Emits region rectangles in that screen's
    physical-buffer coordinates (origin at the buffer's top-left)."""

    region_selected = Signal(str, tuple)   # gdi_name, (x, y, w, h) in buffer px
    fullscreen_selected = Signal(str)      # gdi_name
    cancelled = Signal()

    def __init__(self, screen, gdi_name: str, preview_rgb: np.ndarray):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self.gdi_name = gdi_name
        self._img = _qimage_from_rgb(preview_rgb)
        self._buf_w, self._buf_h = preview_rgb.shape[1], preview_rgb.shape[0]
        self._origin: QPoint | None = None
        self._cur: QPoint | None = None
        self._dragging = False

    # -- coordinate mapping ------------------------------------------------ #
    def _to_buffer(self, rect: QRect) -> tuple[int, int, int, int]:
        w_l, h_l = max(1, self.width()), max(1, self.height())
        sx, sy = self._buf_w / w_l, self._buf_h / h_l
        x = int(round(rect.left() * sx))
        y = int(round(rect.top() * sy))
        w = int(round(rect.width() * sx))
        h = int(round(rect.height() * sy))
        x = max(0, min(x, self._buf_w - 1))
        y = max(0, min(y, self._buf_h - 1))
        w = max(1, min(w, self._buf_w - x))
        h = max(1, min(h, self._buf_h - y))
        return (x, y, w, h)

    def _selection_rect(self) -> QRect | None:
        if self._origin is None or self._cur is None:
            return None
        return QRect(self._origin, self._cur).normalized()

    # -- painting ---------------------------------------------------------- #
    def paintEvent(self, _):
        p = QPainter(self)
        target = QRect(0, 0, self.width(), self.height())
        p.drawImage(target, self._img)
        p.fillRect(target, DIM)

        sel = self._selection_rect()
        if sel and sel.width() > 1 and sel.height() > 1:
            src = QRect(
                int(sel.left() / max(1, self.width()) * self._img.width()),
                int(sel.top() / max(1, self.height()) * self._img.height()),
                int(sel.width() / max(1, self.width()) * self._img.width()),
                int(sel.height() / max(1, self.height()) * self._img.height()))
            p.drawImage(sel, self._img, src)                 # reveal (undimmed)
            p.setPen(QPen(ACCENT, 2))
            p.drawRect(sel)
            self._draw_badge(p, sel)
        else:
            self._draw_hint(p)

    def _draw_badge(self, p: QPainter, sel: QRect):
        bx, by, bw, bh = self._to_buffer(sel)
        label = f"{bw} x {bh}"
        p.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label) + 16
        th = fm.height() + 8
        lx = min(sel.left(), self.width() - tw)
        ly = sel.top() - th - 6
        if ly < 4:
            ly = sel.top() + 6
        p.fillRect(QRect(lx, ly, tw, th), QColor(20, 20, 20, 220))
        p.setPen(Qt.white)
        p.drawText(QRect(lx, ly, tw, th), Qt.AlignCenter, label)

    def _draw_hint(self, p: QPainter):
        text = "Drag to select   ·   double-click / Enter: whole screen   ·   Esc: cancel"
        p.setFont(QFont("Segoe UI", 11))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 28
        th = fm.height() + 16
        cx = (self.width() - tw) // 2
        cy = int(self.height() * 0.06)
        p.fillRect(QRect(cx, cy, tw, th), QColor(20, 20, 20, 210))
        p.setPen(Qt.white)
        p.drawText(QRect(cx, cy, tw, th), Qt.AlignCenter, text)
        # crosshair follows cursor
        if self._cur is not None:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
            p.drawLine(0, self._cur.y(), self.width(), self._cur.y())
            p.drawLine(self._cur.x(), 0, self._cur.x(), self.height())

    # -- input ------------------------------------------------------------- #
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._cur = self._origin
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, e):
        self._cur = e.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._dragging = False
        sel = self._selection_rect()
        if sel and sel.width() > 4 and sel.height() > 4:
            self.region_selected.emit(self.gdi_name, self._to_buffer(sel))
        else:
            self._origin = self._cur = None
            self.update()

    def mouseDoubleClickEvent(self, e):
        self.fullscreen_selected.emit(self.gdi_name)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.fullscreen_selected.emit(self.gdi_name)


class RegionSelector(QObject):
    """Shows overlays across all screens and reports one result.

    ``on_region(gdi_name, buffer_rect)`` fires with a rectangle in the source
    monitor's physical-buffer coordinates; ``on_cancel()`` if dismissed.
    """

    def __init__(self, previews: dict, screen_for_gdi):
        super().__init__()
        self._overlays: list[Overlay] = []
        self.on_region = None
        self.on_cancel = None
        self._done = False

        for gdi, prev in previews.items():
            screen = screen_for_gdi(gdi)
            if screen is None:
                continue
            ov = Overlay(screen, gdi, prev)
            ov.region_selected.connect(self._region)
            ov.fullscreen_selected.connect(self._fullscreen)
            ov.cancelled.connect(self._cancel)
            self._overlays.append(ov)

    def show(self):
        for ov in self._overlays:
            ov.showFullScreen()
            ov.raise_()
        if self._overlays:
            self._overlays[0].activateWindow()

    def _close_all(self):
        for ov in self._overlays:
            ov.close()
        self._overlays.clear()

    def _region(self, gdi, rect):
        if self._done:
            return
        self._done = True
        self._close_all()
        if self.on_region:
            self.on_region(gdi, rect)

    def _fullscreen(self, gdi):
        if self._done:
            return
        self._done = True
        self._close_all()
        if self.on_region:
            self.on_region(gdi, None)   # None -> whole screen

    def _cancel(self):
        if self._done:
            return
        self._done = True
        self._close_all()
        if self.on_cancel:
            self.on_cancel()
