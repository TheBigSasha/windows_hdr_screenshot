"""Regression coverage for the responsive native capture orchestration."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QRect, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hdrshot.config import DEFAULTS, Config  # noqa: E402
from hdrshot.core.pipeline import CaptureResult  # noqa: E402
from hdrshot.core.types import MonitorCapture  # noqa: E402
from hdrshot.ui.overlay import Overlay  # noqa: E402
from hdrshot.ui.preview import HDRPreviewWidget, PreviewWindow  # noqa: E402
from hdrshot.ui.single_instance import SingleInstance, instance_name  # noqa: E402
from hdrshot.ui.workers import CaptureWorker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_instance_name_is_stable_and_does_not_leak_scope():
    first = instance_name(r"C:\Users\person\AppData\Roaming\hdrshot")
    assert first == instance_name(r"C:\Users\person\AppData\Roaming\hdrshot")
    assert first.startswith("HDRShot-")
    assert "person" not in first.lower()


@pytest.mark.skipif(os.name != "nt", reason="Win32 mutex ownership")
def test_single_instance_mutex_rejects_duplicate(qapp, tmp_path, monkeypatch):
    owner = SingleInstance(str(tmp_path))
    duplicate = SingleInstance(str(tmp_path))
    monkeypatch.setattr(duplicate, "_send_to_owner", lambda command: True)
    try:
        assert owner.acquire()
        assert not duplicate.acquire()
    finally:
        duplicate.close()
        owner.close()


def test_capture_worker_emits_raw_gpu_capture_without_cpu_preview():
    linear = np.zeros((4, 6, 3), np.float32)
    capture = MonitorCapture("DISPLAY", 0, 0, 6, 4, 0, linear)

    class Backend:
        def capture_all(self):
            return {"DISPLAY": capture}

        def enumerate_displays(self):
            return ["display-info"]

    received = []
    worker = CaptureWorker(Backend())
    worker.signals.finished.connect(received.append)
    worker.run()
    assert received == [({"DISPLAY": capture}, ["display-info"])]


def test_hdr_preview_requests_pq_float_gpu_surface(qapp):
    pixels = np.ones((4, 6, 4), dtype=np.float16)
    widget = HDRPreviewWidget(pixels)
    try:
        color_space = widget.format().colorSpace()
        assert color_space.isValid()
        assert color_space.description() == "BT.2100(PQ)"
        assert widget.format().redBufferSize() == 16
        assert widget.format().greenBufferSize() == 16
        assert widget.format().blueBufferSize() == 16
    finally:
        widget.close()


def test_overlay_maps_native_preview_to_full_resolution_buffer(qapp):
    screen = qapp.primaryScreen()
    assert screen is not None
    preview = QImage(100, 50, QImage.Format_RGB888)
    preview.fill(QColor("red"))
    linear = np.zeros((100, 200, 3), np.float32)
    overlay = Overlay(screen, "DISPLAY", preview, linear=linear,
                      monitor_rect=(0, 0, 200, 100))
    overlay.resize(100, 50)
    try:
        assert overlay._to_buffer(QRect(25, 10, 50, 20)) == (50, 20, 100, 40)
        crop = overlay.preview_crop((50, 20, 100, 40))
        assert (crop.width(), crop.height()) == (50, 20)
    finally:
        overlay.close()


def test_preview_auto_saves_and_keeps_worker_alive(qapp, tmp_path):
    linear = np.full((24, 32, 3), 0.5, np.float32)
    result = CaptureResult(
        linear=linear,
        sdr_white_nits=80.0,
        display=None,
        region_phys=(0, 0, 32, 24),
        stats={"peak_ratio": 0.5, "peak_nits": 40.0,
               "hdr_pixel_fraction": 0.0, "has_hdr": False},
    )
    config = Config(data={**DEFAULTS, "save_dir": str(tmp_path), "auto_save": True})
    native_preview = QImage(32, 24, QImage.Format_RGB888)
    native_preview.fill(QColor("blue"))
    saved = []
    loop = QEventLoop()

    def on_saved(info, image):
        saved.append((info, image))
        loop.quit()

    window = PreviewWindow(result, config, on_saved=on_saved,
                           preview_image=native_preview)
    window.show()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    try:
        assert saved, "automatic save did not complete"
        assert os.path.isfile(saved[0][0]["path"])
        assert isinstance(saved[0][1], QImage)
        assert window._encode_worker is None
    finally:
        window.close()
