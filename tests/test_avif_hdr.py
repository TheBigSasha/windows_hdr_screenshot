"""True 10-bit BT.2020 PQ HDR AVIF (issue #10). Skipped unless imagecodecs is present."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("imagecodecs")

from hdrshot.encoders import avif_hdr  # noqa: E402

# Older imagecodecs (e.g. last Python-3.10 wheels) lack the nclx kwargs; the HDR
# path is unavailable there, so the optional profile tests skip.
if not avif_hdr.available():
    pytest.skip("imagecodecs present but its avif_encode lacks nclx support",
                allow_module_level=True)


def test_available_true_with_nclx_imagecodecs():
    assert avif_hdr.available() is True


def test_write_and_probe_pq(tmp_path, hdr_scene):
    p = tmp_path / "hdr.avif"
    avif_hdr.write_avif_pq(str(p), hdr_scene)
    assert p.stat().st_size > 0
    meta = avif_hdr.probe(str(p))
    assert meta is not None
    assert meta["bit_depth"] == 10                       # 10-bit, not 8
    assert meta["transfer_characteristics"] == 16        # PQ
    assert meta["color_primaries"] == 9                  # BT.2020
    assert meta["matrix_coefficients"] == 9              # BT.2020 non-constant luminance
    assert meta["full_range_flag"] == 1                  # full-range PQ sample codes
    assert meta["width"] == hdr_scene.shape[1]
    assert meta["height"] == hdr_scene.shape[0]


def test_pipeline_encodes_hdr_avif(tmp_path, hdr_scene):
    from hdrshot.core import color, pipeline
    res = pipeline.CaptureResult(linear=hdr_scene, sdr_white_nits=80.0, display=None,
                                 region_phys=(0, 0, hdr_scene.shape[1], hdr_scene.shape[0]),
                                 stats=color.hdr_stats(hdr_scene, 80.0))
    info = pipeline.encode(res, "avif", str(tmp_path / "x.avif"))
    assert info["hdr"] is True                           # HDR AVIF, not SDR fallback


def test_roundtrip_preserves_10bit(tmp_path, hdr_scene):
    import imagecodecs
    p = tmp_path / "r.avif"
    avif_hdr.write_avif_pq(str(p), hdr_scene)
    dec = np.asarray(imagecodecs.avif_decode(p.read_bytes()))
    assert dec.dtype == np.uint16                        # 10-bit container survives
    assert dec.max() > 255                               # values exceed 8-bit range


def test_neutral_luminance_patches_roundtrip_numerically(tmp_path):
    """Prove that uint16 input means right-justified 10-bit PQ, not just dtype."""
    import imagecodecs

    expected_nits = np.array([0, 80, 100, 203, 400, 1000, 4000, 10000], np.float32)
    row = np.repeat((expected_nits / 80.0), 32)
    scene = np.repeat(row[None, :, None], 32, axis=0)
    scene = np.repeat(scene, 3, axis=2).astype(np.float32)
    path = tmp_path / "neutral-patches.avif"

    avif_hdr.write_avif_pq(str(path), scene, quality=100)
    decoded = np.asarray(imagecodecs.avif_decode(path.read_bytes()), dtype=np.float32)
    pq_codes = np.array([
        decoded[:, index * 32 + 8:(index + 1) * 32 - 8].mean()
        for index in range(len(expected_nits))
    ]) / 1023.0

    m1 = 2610.0 / 16384.0
    m2 = 2523.0 / 4096.0 * 128.0
    c1 = 3424.0 / 4096.0
    c2 = 2413.0 / 4096.0 * 32.0
    c3 = 2392.0 / 4096.0 * 32.0
    power = np.power(pq_codes, 1.0 / m2)
    reconstructed = 10000.0 * np.power(
        np.maximum(power - c1, 0.0) / (c2 - c3 * power), 1.0 / m1
    )

    assert np.all(np.diff(reconstructed) > 0)
    assert reconstructed[4] > 203.0  # above-SDR signal survives the round-trip
    np.testing.assert_allclose(reconstructed[1:], expected_nits[1:], rtol=0.02, atol=1.0)
