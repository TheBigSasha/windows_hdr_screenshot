"""Encoder round-trips for the always-available formats."""
from __future__ import annotations

import numpy as np
import pytest

from hdrshot.core import color
from hdrshot.encoders import exr, sdr


def _read_exr_rgb(path) -> np.ndarray:
    import OpenEXR
    part = OpenEXR.File(str(path)).parts[0]
    if "RGB" in part.channels:
        return np.asarray(part.channels["RGB"].pixels, np.float32)
    if "RGBA" in part.channels:
        return np.asarray(part.channels["RGBA"].pixels, np.float32)[..., :3]
    return np.stack([np.asarray(part.channels[c].pixels, np.float32) for c in "RGB"], axis=-1)


@pytest.mark.skipif(not exr.available(), reason="OpenEXR optional extra is not installed on this architecture")
def test_exr_roundtrip_is_pixel_exact(tmp_path, hdr_scene):
    """Every pixel must survive, not just the global max — a writer that scrambles
    pixel order (regression: strided channel views handed to OpenEXR) can pass a
    max-only comparison."""
    path = tmp_path / "a.exr"
    exr.write_exr(str(path), hdr_scene, 80.0)
    back = _read_exr_rgb(path)
    expect = hdr_scene[..., :3].astype(np.float16).astype(np.float32)
    assert back.shape == expect.shape
    assert np.array_equal(back, expect)


@pytest.mark.skipif(not exr.available(), reason="OpenEXR optional extra is not installed on this architecture")
def test_exr_gradient_every_pixel(tmp_path):
    """Positionally-unique gradient: catches any reordering/truncation exactly."""
    h, w = 60, 40
    img = ((np.arange(h * w * 3, dtype=np.float32).reshape(h, w, 3) % 97) / 7.0)
    path = tmp_path / "g.exr"
    exr.write_exr(str(path), img, 80.0)
    back = _read_exr_rgb(path)
    assert np.array_equal(back, img.astype(np.float16).astype(np.float32))


@pytest.mark.skipif(not exr.available(), reason="OpenEXR optional extra is not installed on this architecture")
def test_exr_writes_alpha_when_present(tmp_path):
    rgba = np.dstack([np.full((8, 8, 3), 2.0, np.float32), np.full((8, 8), 0.5, np.float32)])
    path = tmp_path / "b.exr"
    exr.write_exr(str(path), rgba, 80.0)
    import OpenEXR
    part = OpenEXR.File(str(path)).parts[0]
    names = set(part.channels.keys())
    alpha = None
    if "A" in names:
        alpha = np.asarray(part.channels["A"].pixels, np.float32)
    elif "RGBA" in names:
        alpha = np.asarray(part.channels["RGBA"].pixels, np.float32)[..., 3]
    assert alpha is not None, f"no alpha written (channels: {names})"
    assert np.allclose(alpha, 0.5)
    assert np.allclose(_read_exr_rgb(path), 2.0)


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
