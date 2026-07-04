"""Full-screen region-selection overlay (Snipping-Tool / macOS style).

One :class:`Overlay` is created per :class:`QScreen`; a :class:`RegionSelector`
coordinates them. Each overlay shows the frozen SDR preview of its screen, dims
it, and lets the user rubber-band a region. Selection happens in the overlay's
logical coordinates and is mapped **proportionally** to the matched physical FP16
buffer, so it is correct under any per-monitor DPI.

Beyond drag-to-select it supports (issue #6): a **window-capture mode**
(`Space`/`Tab`) that highlights the window under the cursor via true DWM frame
bounds, a **magnifier loupe** with a live RGB / nits readout, and **arrow-key**
nudge/resize of the selection.
"""
from __future__ import annotations

import sys

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core import color

ACCENT = QColor(0, 160, 255)
DIM = QColor(0, 0, 0, 120)
LOUPE_SIZE = 128
LOUPE_ZOOM = 8


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

    def __init__(self, screen, gdi_name: str, preview_rgb: np.ndarray,
                 linear: np.ndarray | None = None, sdr_white: float = 80.0,
                 monitor_rect: tuple | None = None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self.gdi_name = gdi_name
        self._img = _qimage_from_rgb(preview_rgb)
        self._buf_w, self._buf_h = preview_rgb.shape[1], preview_rgb.shape[0]
        self._linear = linear
        self._sdr_white = sdr_white
        self._monitor_rect = monitor_rect     # physical (x, y, w, h) on the virtual desktop
        self._origin: QPoint | None = None
        self._cur: QPoint | None = None
        self._dragging = False
        self._window_mode = False
        self._window_rect: QRect | None = None  # logical widget rect for window mode

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

    def _buffer_to_widget(self, bx: int, by: int, bw: int, bh: int) -> QRect:
        sx, sy = self.width() / max(1, self._buf_w), self.height() / max(1, self._buf_h)
        return QRect(int(bx * sx), int(by * sy), int(bw * sx), int(bh * sy))

    def _selection_rect(self) -> QRect | None:
        if self._window_mode and self._window_rect is not None:
            return self._window_rect
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

        if self._cur is not None and not self._dragging:
            self._draw_loupe(p, self._cur)

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
        mode = "WINDOW" if self._window_mode else "REGION"
        text = (f"[{mode}]  Drag: region   ·   Space/Tab: window mode   ·   "
                "arrows: nudge (Shift: resize)   ·   Enter: whole screen   ·   Esc: cancel")
        p.setFont(QFont("Segoe UI", 10))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 28
        th = fm.height() + 16
        cx = (self.width() - tw) // 2
        cy = int(self.height() * 0.06)
        p.fillRect(QRect(cx, cy, tw, th), QColor(20, 20, 20, 210))
        p.setPen(Qt.white)
        p.drawText(QRect(cx, cy, tw, th), Qt.AlignCenter, text)
        if self._cur is not None:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.DashLine))
            p.drawLine(0, self._cur.y(), self.width(), self._cur.y())
            p.drawLine(self._cur.x(), 0, self._cur.x(), self.height())

    def _draw_loupe(self, p: QPainter, cursor: QPoint):
        """Magnified inset near the cursor with a live RGB / nits readout."""
        bx = int(cursor.x() / max(1, self.width()) * self._buf_w)
        by = int(cursor.y() / max(1, self.height()) * self._buf_h)
        half = LOUPE_SIZE // (2 * LOUPE_ZOOM)
        src = QRect(bx - half, by - half, half * 2, half * 2)

        lx = cursor.x() + 20
        ly = cursor.y() + 20
        if lx + LOUPE_SIZE > self.width():
            lx = cursor.x() - LOUPE_SIZE - 20
        if ly + LOUPE_SIZE + 26 > self.height():
            ly = cursor.y() - LOUPE_SIZE - 46
        dest = QRect(lx, ly, LOUPE_SIZE, LOUPE_SIZE)

        p.fillRect(QRect(lx - 2, ly - 2, LOUPE_SIZE + 4, LOUPE_SIZE + 26), QColor(20, 20, 20, 230))
        p.drawImage(dest, self._img, src)
        p.setPen(QPen(ACCENT, 1))
        p.drawRect(dest)
        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
        p.drawLine(lx + LOUPE_SIZE // 2, ly, lx + LOUPE_SIZE // 2, ly + LOUPE_SIZE)
        p.drawLine(lx, ly + LOUPE_SIZE // 2, lx + LOUPE_SIZE, ly + LOUPE_SIZE // 2)

        readout = self._readout(bx, by)
        p.setFont(QFont("Consolas", 8))
        p.setPen(Qt.white)
        p.drawText(QRect(lx, ly + LOUPE_SIZE + 2, LOUPE_SIZE, 22), Qt.AlignCenter, readout)

    def _readout(self, bx: int, by: int) -> str:
        if not (0 <= bx < self._buf_w and 0 <= by < self._buf_h):
            return ""
        if self._linear is not None:
            r, g, b = (float(v) for v in self._linear[by, bx, :3])
            nits = max(r, g, b) * color.SCRGB_REFERENCE_NITS
            return f"{nits:.0f} nits  scRGB {r:.2f},{g:.2f},{b:.2f}"
        px = self._img.pixelColor(bx, by)
        return f"RGB {px.red()},{px.green()},{px.blue()}"

    # -- window mode ------------------------------------------------------- #
    def _update_window_rect(self, local_pt):
        if sys.platform != "win32" or self._monitor_rect is None:
            self._window_rect = None
            return
        mx, my, mw, mh = self._monitor_rect
        # WindowFromPoint/DWM want *physical* screen pixels (the process is
        # per-monitor-v2 DPI aware). Qt reports logical coords, so map the local
        # cursor through the same widget->buffer scaling as _to_buffer (buffer ==
        # physical monitor pixels) and add the monitor's physical origin.
        px = mx + int(local_pt.x() / max(1, self.width()) * self._buf_w)
        py = my + int(local_pt.y() / max(1, self.height()) * self._buf_h)
        try:
            from ..backends.win32.hwnd import window_frame_bounds_at
            bounds = window_frame_bounds_at(px, py)
        except Exception:
            bounds = None
        if not bounds:
            self._window_rect = None
            return
        fl, ft, fr, fb = bounds
        bx0, by0 = max(fl, mx) - mx, max(ft, my) - my
        bx1, by1 = min(fr, mx + mw) - mx, min(fb, my + mh) - my
        if bx1 <= bx0 or by1 <= by0:
            self._window_rect = None
            return
        self._window_rect = self._buffer_to_widget(bx0, by0, bx1 - bx0, by1 - by0)

    # -- input ------------------------------------------------------------- #
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._window_mode:
            self._emit_current()
            return
        self._origin = e.position().toPoint()
        self._cur = self._origin
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, e):
        self._cur = e.position().toPoint()
        if self._window_mode:
            self._update_window_rect(self._cur)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._window_mode:
            return
        self._dragging = False
        sel = self._selection_rect()
        if sel and sel.width() > 4 and sel.height() > 4:
            self.region_selected.emit(self.gdi_name, self._to_buffer(sel))
        else:
            self._origin = self._cur = None
            self.update()

    def mouseDoubleClickEvent(self, e):
        if not self._window_mode:
            self.fullscreen_selected.emit(self.gdi_name)

    def _emit_current(self):
        sel = self._selection_rect()
        if sel and sel.width() > 4 and sel.height() > 4:
            self.region_selected.emit(self.gdi_name, self._to_buffer(sel))

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            if self._window_mode and self._window_rect is not None:
                self._emit_current()
            else:
                self.fullscreen_selected.emit(self.gdi_name)
        elif key in (Qt.Key_Space, Qt.Key_Tab):
            self._window_mode = not self._window_mode
            self._window_rect = None
            self.update()
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._nudge(key, e.modifiers())

    def _nudge(self, key, mods):
        if self._origin is None or self._cur is None:
            return
        step = 10 if (mods & Qt.ControlModifier) else 1
        dx = (-step if key == Qt.Key_Left else step if key == Qt.Key_Right else 0)
        dy = (-step if key == Qt.Key_Up else step if key == Qt.Key_Down else 0)
        if mods & Qt.ShiftModifier:                 # resize: move the moving corner
            self._cur = QPoint(self._cur.x() + dx, self._cur.y() + dy)
        else:                                       # move the whole selection
            self._origin = QPoint(self._origin.x() + dx, self._origin.y() + dy)
            self._cur = QPoint(self._cur.x() + dx, self._cur.y() + dy)
        self.update()


class RegionSelector(QObject):
    """Shows overlays across all screens and reports one result.

    ``on_region(gdi_name, buffer_rect)`` fires with a rectangle in the source
    monitor's physical-buffer coordinates; ``on_cancel()`` if dismissed.
    """

    def __init__(self, previews: dict, screen_for_gdi, linears: dict | None = None,
                 whites: dict | None = None, monitor_rects: dict | None = None):
        super().__init__()
        self._overlays: list[Overlay] = []
        self.on_region = None
        self.on_cancel = None
        self._done = False
        linears = linears or {}
        whites = whites or {}
        monitor_rects = monitor_rects or {}

        for gdi, prev in previews.items():
            screen = screen_for_gdi(gdi)
            if screen is None:
                continue
            ov = Overlay(screen, gdi, prev, linear=linears.get(gdi),
                         sdr_white=whites.get(gdi, 80.0), monitor_rect=monitor_rects.get(gdi))
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
            self._overlays[0].setFocus()

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
