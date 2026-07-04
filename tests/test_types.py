"""Platform-free type helpers: rotation, virtual-desktop bounds."""
from __future__ import annotations

import numpy as np

from hdrshot.core.types import (
    DisplayInfo,
    apply_rotation,
    rotation_to_degrees,
    virtual_desktop_bounds,
)


def test_rotation_to_degrees():
    assert rotation_to_degrees(0) == 0
    assert rotation_to_degrees(1) == 0     # identity
    assert rotation_to_degrees(2) == 90
    assert rotation_to_degrees(3) == 180
    assert rotation_to_degrees(4) == 270
    assert rotation_to_degrees(99) == 0    # unknown -> 0


def test_apply_rotation_dims():
    buf = np.zeros((2, 3, 3), np.float32)  # H=2, W=3
    assert apply_rotation(buf, 0).shape == (2, 3, 3)
    assert apply_rotation(buf, 90).shape == (3, 2, 3)   # swapped
    assert apply_rotation(buf, 180).shape == (2, 3, 3)  # same
    assert apply_rotation(buf, 270).shape == (3, 2, 3)  # swapped


def test_apply_rotation_is_invertible():
    buf = np.random.default_rng(0).random((5, 7, 3)).astype(np.float32)
    r = buf
    for _ in range(4):
        r = apply_rotation(r, 90)
    assert np.array_equal(r, buf)          # four 90s == identity
    assert np.array_equal(apply_rotation(apply_rotation(buf, 180), 180), buf)


def _d(x, y, w, h):
    return DisplayInfo(0, "g", "g", x, y, w, h, False, False, False, 8, 80.0, "RGB")


def test_virtual_desktop_bounds_single():
    assert virtual_desktop_bounds([_d(0, 0, 100, 100)]) == (0, 0, 100, 100)


def test_virtual_desktop_bounds_multi():
    disps = [_d(0, 0, 100, 100), _d(100, 0, 200, 150), _d(-50, -20, 40, 40)]
    assert virtual_desktop_bounds(disps) == (-50, -20, 350, 170)


def test_virtual_desktop_bounds_empty():
    assert virtual_desktop_bounds([]) == (0, 0, 0, 0)
