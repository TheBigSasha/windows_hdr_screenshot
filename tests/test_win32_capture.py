from __future__ import annotations

import contextlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 backend only")


def test_capture_all_skips_device_creation_for_adapter_without_outputs(monkeypatch):
    from hdrshot.backends.win32 import capture

    unused_adapter = 101
    display_adapter = 202
    output = 303
    created_for: list[int] = []

    monkeypatch.setattr(capture, "_create_factory", lambda: 1)
    monkeypatch.setattr(
        capture, "_enum_adapters", lambda _factory: [unused_adapter, display_adapter]
    )
    monkeypatch.setattr(
        capture,
        "_enum_outputs",
        lambda adapter: [] if adapter == unused_adapter else [output],
    )

    def create_device(adapter):
        created_for.append(adapter)
        return 401, 402

    monkeypatch.setattr(capture, "_create_device", create_device)
    monkeypatch.setattr(
        capture,
        "_output_desc",
        lambda _output: SimpleNamespace(
            AttachedToDesktop=True,
            DeviceName=r"\\.\DISPLAY1",
            DesktopCoordinates=SimpleNamespace(left=0, top=0, right=1, bottom=1),
            Rotation=1,
        ),
    )
    monkeypatch.setattr(
        capture,
        "_grab_output",
        lambda *_args: np.zeros((1, 1, 3), dtype=np.float16),
    )
    monkeypatch.setattr(capture, "_CaptureSession", contextlib.nullcontext)
    monkeypatch.setattr(capture, "com_release", lambda _ptr: None)

    results = capture.capture_all()

    assert created_for == [display_adapter]
    assert list(results) == [r"\\.\DISPLAY1"]
