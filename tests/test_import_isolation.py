"""The cross-platform seam (issue #14): importing the pure package must NOT pull
in the Win32 backend. This is what lets the encoders/color/pipeline tests run on
Linux CI, and is asserted here so a stray top-level ``import`` regresses loudly.
"""
from __future__ import annotations

import subprocess
import sys


def test_pure_import_does_not_load_win32():
    # Run in a fresh interpreter so the assertion isn't polluted by other tests
    # (which may legitimately construct the backend on Windows).
    code = (
        "import sys;"
        "import hdrshot, hdrshot.core.pipeline, hdrshot.core.color, hdrshot.core.types;"
        "import hdrshot.encoders.ultrahdr, hdrshot.encoders.exr, hdrshot.encoders.sdr,"
        " hdrshot.encoders.heic, hdrshot.encoders.avif_hdr;"
        "import hdrshot.backends, hdrshot.agentcli;"
        "leaked=[m for m in sys.modules if 'backends.win32' in m];"
        "assert not leaked, leaked;"
        "print('OK')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout


def test_backend_factory_reports_availability():
    from hdrshot.backends import backend_available
    assert isinstance(backend_available(), bool)


def test_unsupported_platform_error_exists():
    from hdrshot.backends import UnsupportedPlatformError
    assert issubclass(UnsupportedPlatformError, RuntimeError)
