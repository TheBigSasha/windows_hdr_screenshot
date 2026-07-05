"""Color-space math: transfer functions against published reference values."""
from __future__ import annotations

import numpy as np

from hdrshot.core import color


def test_srgb_oetf_eotf_roundtrip():
    x = np.linspace(0.0, 1.0, 4096, dtype=np.float32)
    back = color.srgb_eotf(color.srgb_oetf(x))
    # Round-trip within ~1 code value at 8-bit.
    assert np.max(np.abs(back - x)) < 1.0 / 255.0


def test_srgb_known_points():
    # 0 -> 0, 1 -> 1; the linear/gamma segment boundary is continuous.
    assert color.srgb_oetf(np.array([0.0]))[0] == 0.0
    assert abs(color.srgb_oetf(np.array([1.0]))[0] - 1.0) < 1e-6
    # Mid-grey 0.5 linear encodes to ~0.735 sRGB.
    assert abs(color.srgb_oetf(np.array([0.5]))[0] - 0.7353569) < 1e-4


def test_pq_reference_values():
    # SMPTE ST 2084: 10000 nits -> 1.0, 0 nits -> 0.0, 100 nits -> ~0.5081.
    assert abs(color.pq_oetf(np.array([10000.0]))[0] - 1.0) < 1e-6
    # PQ(0) is c1^m2 ~= 7.3e-7 by the raw ST 2084 formula (rounds to code value 0).
    assert color.pq_oetf(np.array([0.0]))[0] < 1e-5
    assert abs(color.pq_oetf(np.array([100.0]))[0] - 0.508078) < 1e-3
    # Monotonic increasing.
    vals = color.pq_oetf(np.array([1.0, 10.0, 100.0, 1000.0, 10000.0]))
    assert np.all(np.diff(vals) > 0)


def test_bt709_to_bt2020_preserves_white():
    # Equal-energy white maps to (near) equal-energy white; rows sum ~1.
    row_sums = color._BT709_TO_BT2020.sum(axis=1)
    assert np.allclose(row_sums, 1.0, atol=1e-3)


def test_hdr_stats_detects_hdr(hdr_scene):
    st = color.hdr_stats(hdr_scene, 80.0)
    assert st["has_hdr"] is True
    assert st["peak_ratio"] > 5.0
    assert st["peak_nits"] > 400.0
    assert st["hdr_pixel_fraction"] > 0.0


def test_hdr_stats_rejects_sdr(sdr_scene):
    st = color.hdr_stats(sdr_scene, 80.0)
    assert st["has_hdr"] is False
    assert st["peak_ratio"] <= 1.0
    assert st["hdr_pixel_fraction"] == 0.0


def test_hdr_stats_scales_with_sdr_white():
    # A buffer at 2.0 (160 nits) is HDR against 80-nit white, SDR against 200-nit.
    buf = np.full((8, 8, 3), 2.0, np.float32)
    assert color.hdr_stats(buf, 80.0)["has_hdr"] is True
    assert color.hdr_stats(buf, 240.0)["has_hdr"] is False


def test_scrgb_to_sdr_u8_clips_highlights():
    buf = np.full((4, 4, 3), 10.0, np.float32)  # way above paper white
    u8 = color.scrgb_to_sdr_u8(buf, 80.0)
    assert u8.dtype == np.uint8
    assert np.all(u8 == 255)  # clipped to white


def test_pq_bt2020_u16_bit_depth():
    buf = np.full((4, 4, 3), 125.0, np.float32)  # 125 * 80 = 10000 nits -> PQ ~1.0
    out = color.scrgb_to_pq_bt2020_u16(buf, bit_depth=10)
    assert out.dtype == np.uint16
    assert out.max() <= 1023  # 10-bit range
    assert out.max() >= 1000  # near the top of the range
