"""Encoder round-trips for the always-available formats."""
from __future__ import annotations

import numpy as np
import pytest

from hdrshot.core import color
from hdrshot.encoders import exr, sdr


def test_exr_roundtrip_preserves_hdr(tmp_path, hdr_scene):
    path = tmp_path / "a.exr"
    exr.write_exr(str(path), hdr_scene, 80.0)
    import OpenEXR
    part = OpenEXR.File(str(path)).parts[0]
    rgb = part.channels["RGB"].pixels
    # fp16 storage: peak preserved to within half-float precision.
    assert float(np.max(rgb)) == pytest.approx(float(np.max(hdr_scene[..., :3])), rel=1e-2)


def test_exr_writes_alpha_when_present(tmp_path):
    rgba = np.dstack([np.full((8, 8, 3), 2.0, np.float32), np.ones((8, 8), np.float32)])
    path = tmp_path / "b.exr"
    exr.write_exr(str(path), rgba, 80.0)
    assert path.exists() and path.stat().st_size > 0


def test_png_roundtrip(tmp_path):
    u8 = (np.random.default_rng(1).random((16, 24, 3)) * 255).astype(np.uint8)
    path = tmp_path / "c.png"
    sdr.write_png(str(path), u8)
    from PIL import Image
    with Image.open(path) as im:
        assert im.size == (24, 16)
        assert np.array_equal(np.asarray(im), u8)   # PNG is lossless


def test_jpeg_writes(tmp_path):
    u8 = color.scrgb_to_sdr_u8(np.full((16, 16, 3), 0.5, np.float32), 80.0)
    path = tmp_path / "d.jpg"
    sdr.write_jpeg(str(path), u8)
    from PIL import Image
    with Image.open(path) as im:
        assert im.size == (16, 16)


def test_avif_sdr_writes(tmp_path):
    pytest.importorskip("pillow_avif")
    u8 = color.scrgb_to_sdr_u8(np.full((16, 16, 3), 0.5, np.float32), 80.0)
    path = tmp_path / "e.avif"
    sdr.write_avif_sdr(str(path), u8)
    assert path.exists() and path.stat().st_size > 0


def test_heic_availability_is_boolean():
    from hdrshot.encoders import heic
    assert isinstance(heic.available(), bool)
