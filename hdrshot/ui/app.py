"""Main application: toolbar window, tray icon, and capture orchestration."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QGuiApplication, QIcon,
                           QLinearGradient, QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import capture, color, displays, pipeline
from .overlay import RegionSelector
from .preview import PreviewWindow

STYLE = """
* { font-family: 'Segoe UI'; color: #e8e8ea; }
QWidget#main, QWidget#preview { background: #1c1c1f; }
QLabel#title { font-size: 17px; font-weight: 600; }
QLabel#subtle { color: #9a9aa2; font-size: 12px; }
QLabel#thumb { background: #101012; border: 1px solid #2c2c31; border-radius: 8px; }
QLabel#hdrPill { background: #113a24; color: #43d17f; border: 1px solid #1e6b41;
                 border-radius: 11px; padding: 2px 12px; font-weight: 600; font-size: 12px; }
QLabel#offPill { background: #3a3212; color: #e0b23a; border: 1px solid #6b5a1e;
                 border-radius: 11px; padding: 2px 12px; font-weight: 600; font-size: 12px; }
QLabel#sdrPill { background: #26262b; color: #a8a8b0; border: 1px solid #38383f;
                 border-radius: 11px; padding: 2px 12px; font-weight: 600; font-size: 12px; }
QLabel#hdrBadge { background: #113a24; color: #43d17f; border-radius: 6px;
                  padding: 0 10px; font-weight: 700; }
QLabel#sdrBadge { background: #26262b; color: #a8a8b0; border-radius: 6px;
                  padding: 0 10px; font-weight: 700; }
QPushButton { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 8px;
              padding: 9px 16px; font-size: 13px; }
QPushButton:hover { background: #33333b; border-color: #4a4a54; }
QPushButton#primary, QPushButton#capture { background: #0a84ff; border: none; color: white;
                                            font-weight: 600; }
QPushButton#primary:hover, QPushButton#capture:hover { background: #3a9bff; }
QComboBox { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px; padding: 5px 8px; }
QComboBox QAbstractItemView { background: #26262b; selection-background-color: #0a84ff; }
QFrame#sep { color: #2c2c31; }
"""


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor("#0a84ff"))
    g.setColorAt(1, QColor("#8b5cf6"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(Qt.white)
    p.setFont(QFont("Segoe UI", 17, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, "HDR")
    p.end()
    return QIcon(pm)


class MainWindow(QWidget):
    def __init__(self, controller: "HdrShotApp"):
        super().__init__(None)
        self.controller = controller
        self.setObjectName("main")
        self.setWindowTitle("HDR Shot")
        self.setFixedWidth(420)
        self._build()
        self.refresh_status()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("HDR Shot")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.pill = QLabel("…")
        header.addWidget(self.pill)
        root.addLayout(header)

        sub = QLabel("True HDR screenshots — gain-map JPEG, EXR or PQ HEIC on HDR "
                     "displays, standard formats on SDR.")
        sub.setObjectName("subtle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        btns = QHBoxLayout()
        region = QPushButton("⬚  Capture Region")
        region.setObjectName("capture")
        region.clicked.connect(lambda: self.controller.start_capture())
        screen = QPushButton("🖵  Whole Screen")
        screen.clicked.connect(lambda: self.controller.start_capture(fullscreen=True))
        btns.addWidget(region, 2)
        btns.addWidget(screen, 1)
        root.addLayout(btns)

        self.foot = QLabel("")
        self.foot.setObjectName("subtle")
        self.foot.setWordWrap(True)
        root.addWidget(self.foot)

    def refresh_status(self):
        disps = displays.enumerate_displays()
        primary = next((d for d in disps if d.is_primary), disps[0] if disps else None)
        if primary is None:
            self.pill.setText("no display")
            return
        if primary.hdr_enabled:
            self.pill.setObjectName("hdrPill")
            self.pill.setText("HDR ON")
            note = "Highlights above SDR white are captured as true HDR."
        elif primary.hdr_supported:
            self.pill.setObjectName("offPill")
            self.pill.setText("HDR capable · off")
            note = "Windows HDR is off. Toggle it with Win+Alt+B for live HDR capture."
        else:
            self.pill.setObjectName("sdrPill")
            self.pill.setText("SDR")
            note = "This display is SDR — screenshots use standard PNG/JPEG."
        self.pill.setStyleSheet("")            # re-evaluate object-name style
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        n = len(disps)
        self.foot.setText(f"{note}\nSaves to Pictures\\Screenshots · {n} display"
                          f"{'s' if n != 1 else ''} detected.")


class HdrShotApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.app.setStyleSheet(STYLE)
        self.app.setWindowIcon(make_icon())
        self.window = MainWindow(self)
        self._selector: RegionSelector | None = None
        self._preview: PreviewWindow | None = None
        self._caps = None
        self._disps = None
        self._pending_fullscreen = False
        self._build_tray()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self.app)
        self.tray.setToolTip("HDR Shot")
        menu = QMenu()
        act_new = QAction("New Screenshot", self.app)
        act_new.triggered.connect(lambda: self.start_capture())
        act_show = QAction("Open HDR Shot", self.app)
        act_show.triggered.connect(self.show_window)
        act_quit = QAction("Quit", self.app)
        act_quit.triggered.connect(self.app.quit)
        for a in (act_new, act_show, act_quit):
            menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_window() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_window(self):
        self.window.refresh_status()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    # -- capture flow ------------------------------------------------------ #
    def start_capture(self, fullscreen: bool = False):
        self._pending_fullscreen = fullscreen
        self.window.hide()
        QTimer.singleShot(180, self._do_capture)   # let the window disappear

    def _do_capture(self):
        try:
            self._caps = capture.capture_all()
        except Exception as e:
            self.tray.showMessage("HDR Shot", f"Capture failed: {e}",
                                  QSystemTrayIcon.Warning)
            self.show_window()
            return
        self._disps = displays.enumerate_displays()

        if self._pending_fullscreen:
            primary = next((d for d in self._disps if d.is_primary), self._disps[0])
            self._on_region(primary.gdi_name, None)
            return

        previews = {name: color.scrgb_to_preview_u8(
                        mc.linear, self._white_for(name))
                    for name, mc in self._caps.items()}
        lookup = self._screen_lookup(self._disps)
        self._selector = RegionSelector(previews, lookup)
        self._selector.on_region = self._on_region
        self._selector.on_cancel = self._on_cancel
        self._selector.show()

    def _white_for(self, gdi_name: str) -> float:
        for d in self._disps:
            if d.gdi_name == gdi_name:
                return d.sdr_white_nits
        return 80.0

    def _screen_lookup(self, disps):
        screens = QGuiApplication.screens()
        by_name: dict = {}
        for s in screens:
            by_name.setdefault(s.name(), []).append(s)
        s_sorted = sorted(screens, key=lambda s: (s.geometry().x(), s.geometry().y()))
        d_sorted = sorted(disps, key=lambda d: (d.x, d.y))
        pos_map = {d.gdi_name: s for d, s in zip(d_sorted, s_sorted)}

        def lookup(gdi):
            d = next((x for x in disps if x.gdi_name == gdi), None)
            if d and len(by_name.get(d.friendly_name, [])) == 1:
                return by_name[d.friendly_name][0]
            return pos_map.get(gdi)
        return lookup

    def _on_region(self, gdi_name, buffer_rect):
        result = pipeline.capture_buffer_region(self._caps, self._disps, gdi_name, buffer_rect)
        self._preview = PreviewWindow(result)
        self._preview.show()
        self._preview.raise_()
        self._preview.activateWindow()
        self._selector = None

    def _on_cancel(self):
        self._selector = None
        self.show_window()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    displays.set_process_dpi_aware()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    controller = HdrShotApp(app)
    controller.show_window()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
