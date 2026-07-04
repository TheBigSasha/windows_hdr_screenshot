"""Shared fixtures. The pure test suite must import on any OS (no Win32)."""
from __future__ import annotations

import numpy as np
import pytest

from hdrshot.selftest import make_hdr_scene


@pytest.fixture
def hdr_scene() -> np.ndarray:
    """The synthetic HDR scene (nit ramp to 1600 + saturated swatches)."""
    return make_hdr_scene(320, 200)


@pytest.fixture
def sdr_scene() -> np.ndarray:
    """A flat mid-grey SDR buffer: everything at/below paper white."""
    return np.full((64, 64, 3), 0.5, np.float32)
