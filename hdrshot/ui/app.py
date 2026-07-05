"""Main application: toolbar window, tray icon, and capture orchestration.

Capture and encode run on worker threads (issue #16); global hotkeys (#2),
post-capture toasts (#3), a Preferences window (#5) and configurable save
paths/templates (#4) are wired in through the persisted `Config`.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..core import pipeline
from ..hotkeys import HotkeyManager
from .overlay import RegionSelector
from .preview import PreviewWindow
from .settings import PreferencesDialog
from .toast import Toast
from .workers import CaptureWorker

log = logging.getLogger(__name__)

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
QPushButton#ghost { background: transparent; border: 1px solid #3a3a42; }
QComboBox { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px; padding: 5px 8px; }
QComboBox QAbstractItemView { background: #26262b; selection-background-color: #0a84ff; }
QLineEdit, QSpinBox { background: #2a2a30; border: 1px solid #3a3a42; border-radius: 6px; padding: 5px 8px; }
QFrame#sep { color: #2c2c31; }
QCheckBox { color: #e8e8ea; }
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
    def __init__(self, controller: HdrShotApp):
        super().__init__(None)
        self.controller = controller
        self.setObjectName("main")
        self.setWindowTitle("HDR Shot")
        self.setFixedWidth(440)
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
        gear = QPushButton("⚙")
        gear.setObjectName("ghost")
        gear.setFixedWidth(40)
        gear.setToolTip("Preferences")
        gear.clicked.connect(self.controller.open_preferences)
        header.addWidget(gear)
        root.addLayout(header)

        sub = QLabel("True HDR screenshots — gain-map JPEG, EXR, PQ HEIC/AVIF on HDR "
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
        disps = self.controller.backend.enumerate_displays()
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
        # Per-display state (issue #17): don't hide a mixed HDR/SDR setup.
        per = "   ·   ".join(f"{d.friendly_name}: {d.state_label}" for d in disps)
        hk = self.controller.config.get("hotkey_region")
        self.foot.setText(f"{note}\n{per}\nHotkey: {hk}  ·  saves to your Pictures\\Screenshots.")


class HdrShotApp:
    def __init__(self, app: QApplication, backend):
        self.app = app
        self.backend = backend
        self.config = Config.load()
        self.pool = QThreadPool.globalInstance()
        self.app.setStyleSheet(STYLE)
        self.app.setWindowIcon(make_icon())
        self.window = MainWindow(self)
        self._selector: RegionSelector | None = None
        self._previews: list[PreviewWindow] = []
        self._toast: Toast | None = None
        self._caps = None
        self._disps = None
        self._pending_fullscreen = False
        self._capturing = False
        self._build_tray()
        self._setup_hotkeys()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self.app)
        self.tray.setToolTip("HDR Shot")
        menu = QMenu()
        act_new = QAction("New Screenshot", self.app)
        act_new.triggered.connect(lambda: self.start_capture())
        act_screen = QAction("Capture Whole Screen", self.app)
        act_screen.triggered.connect(lambda: self.start_capture(fullscreen=True))
        timed = QMenu("Timed capture", menu)
        for secs in (3, 5, 10):
            a = QAction(f"Region after {secs}s", self.app)
            a.triggered.connect(lambda _=False, s=secs: self._timed_capture(s))
            timed.addAction(a)
        act_show = QAction("Open HDR Shot", self.app)
        act_show.triggered.connect(self.show_window)
        act_prefs = QAction("Preferences…", self.app)
        act_prefs.triggered.connect(self.open_preferences)
        act_quit = QAction("Quit", self.app)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_new)
        menu.addAction(act_screen)
        menu.addMenu(timed)
        menu.addSeparator()
        menu.addAction(act_show)
        menu.addAction(act_prefs)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_window() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _setup_hotkeys(self):
        # One manager for the app's lifetime: a new manager per reload would
        # install a fresh native event filter each time and never remove the old.
        if not hasattr(self, "hotkeys"):
            self.hotkeys = HotkeyManager()
        failed = []
        for spec, cb in ((self.config.get("hotkey_region"),
                          lambda: self.start_capture()),
                         (self.config.get("hotkey_screen"),
                          lambda: self.start_capture(fullscreen=True))):
            if spec and not self.hotkeys.register(spec, cb):
                failed.append(str(spec))
        if failed:
            self.tray.showMessage(
                "HDR Shot", "Could not register hotkey(s): " + ", ".join(failed) +
                " (invalid or already in use)", QSystemTrayIcon.Warning)

    def _reload_hotkeys(self):
        self.hotkeys.unregister_all()
        self._setup_hotkeys()

    def show_window(self):
        self.window.refresh_status()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def open_preferences(self):
        dlg = PreferencesDialog(self.config, on_apply=self._on_prefs_applied, parent=self.window)
        dlg.setStyleSheet(STYLE)
        dlg.exec()

    def _on_prefs_applied(self):
        self._reload_hotkeys()
        self.window.refresh_status()

    def quit(self):
        try:
            self.hotkeys.unregister_all()
        finally:
            self.app.quit()

    # -- capture flow ------------------------------------------------------ #
    def _timed_capture(self, seconds: int):
        self.window.hide()
        QTimer.singleShot(seconds * 1000, lambda: self.start_capture())

    def start_capture(self, fullscreen: bool = False):
        if self._capturing or self._selector is not None:
            return                                       # already mid-capture
        self._capturing = True
        self._pending_fullscreen = fullscreen
        self.window.hide()
        QTimer.singleShot(140, self._spawn_capture)      # let the window disappear

    def _spawn_capture(self):
        worker = CaptureWorker(self.backend)
        worker.signals.finished.connect(self._on_captured)
        worker.signals.error.connect(self._on_capture_error)
        self.pool.start(worker)

    def _on_capture_error(self, msg: str):
        self._capturing = False
        self._caps = None                        # drop any full-monitor buffers
        self.tray.showMessage("HDR Shot", f"Capture failed: {msg}", QSystemTrayIcon.Warning)
        self.show_window()

    def _on_captured(self, payload):
        caps, disps, previews = payload
        self._caps, self._disps = caps, disps
        if not disps:
            self._on_capture_error("no displays detected")
            return

        if self._pending_fullscreen:
            primary = next((d for d in disps if d.is_primary), disps[0])
            self._on_region(primary.gdi_name, None)
            return

        lookup = self._screen_lookup(disps)
        linears = {name: mc.linear for name, mc in caps.items()}
        whites = {d.gdi_name: d.sdr_white_nits for d in disps}
        monitor_rects = {name: (mc.x, mc.y, mc.width, mc.height) for name, mc in caps.items()}
        self._selector = RegionSelector(previews, lookup, linears=linears,
                                        whites=whites, monitor_rects=monitor_rects)
        self._selector.on_region = self._on_region
        self._selector.on_cancel = self._on_cancel
        self._selector.show()

    def _screen_lookup(self, disps):
        """Match a GDI device name to its QScreen deterministically (issue #17).

        Qt 6 ``QScreen.name()`` returns the *friendly* monitor name ("M27P6"), not
        the GDI device name — so try, in order: exact GDI name (older Qt), unique
        friendly name, then a positional zip of both lists sorted by origin.
        """
        screens = QGuiApplication.screens()
        by_name = {s.name(): s for s in screens}
        names_unique = len(by_name) == len(screens)
        friendly_for_gdi = {d.gdi_name: d.friendly_name for d in disps}
        s_sorted = sorted(screens, key=lambda s: (s.geometry().x(), s.geometry().y()))
        d_sorted = sorted(disps, key=lambda d: (d.x, d.y))
        pos_map = {d.gdi_name: s for d, s in zip(d_sorted, s_sorted, strict=False)}

        def lookup(gdi):
            if gdi in by_name:                          # Qt 5-era GDI names
                return by_name[gdi]
            friendly = friendly_for_gdi.get(gdi)
            if names_unique and friendly in by_name:    # Qt 6 friendly names
                return by_name[friendly]
            log.debug("QScreen name did not match %s; using positional fallback", gdi)
            return pos_map.get(gdi)
        return lookup

    def _on_region(self, gdi_name, buffer_rect):
        self._selector = None
        try:
            result = pipeline.capture_buffer_region(self._caps, self._disps, gdi_name, buffer_rect)
        except Exception as e:
            self._on_capture_error(str(e))
            return
        # Release the full-monitor scRGB float32 buffers now. A region crop is an
        # independent copy, so every monitor is freed; a whole-screen grab aliases
        # the selected display (kept alive by the preview) but frees the others.
        self._caps = None
        self._capturing = False
        # Keep every open preview alive (a new capture must not hard-delete an
        # earlier preview holding an unsaved shot); pruned on window close.
        preview = PreviewWindow(result, self.config, on_saved=self._on_saved)
        preview.setStyleSheet(STYLE)
        self._previews.append(preview)
        preview.destroyed.connect(
            lambda _=None, p=preview: p in self._previews and self._previews.remove(p))
        preview.show()
        preview.raise_()
        preview.activateWindow()

    def _on_saved(self, info: dict, preview_u8):
        if not self.config.get("notifications"):
            return
        is_hdr = bool(info.get("hdr"))
        peak = float(info.get("peak_nits", 0.0) or 0.0)
        self._toast = Toast(preview_u8, info["path"], is_hdr, peak)
        self._toast.popup()

    def _on_cancel(self):
        self._selector = None
        self._caps = None
        self._capturing = False
        self.show_window()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    from ..backends import UnsupportedPlatformError, get_backend
    try:
        backend = get_backend()
    except UnsupportedPlatformError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    backend.set_process_dpi_aware()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    controller = HdrShotApp(app, backend)
    controller.show_window()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
