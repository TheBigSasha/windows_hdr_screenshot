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
    assert meta["full_range_flag"] == 0                  # provider-emitted limited-range NCLX
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
