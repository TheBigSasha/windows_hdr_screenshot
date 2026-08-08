"""Pipeline crop math, multi-monitor stitching, error policy, filenames."""
from __future__ import annotations

import numpy as np
import pytest

from hdrshot.core import pipeline
from hdrshot.core.types import DisplayInfo, MonitorCapture
from hdrshot.encoders import exr


def _display(gdi, x, y, w, h, primary=False, hdr=True, white=80.0):
    return DisplayInfo(index=0, gdi_name=gdi, friendly_name=gdi, x=x, y=y, width=w, height=h,
                       is_primary=primary, hdr_supported=hdr, hdr_enabled=hdr,
                       bits_per_color=10 if hdr else 8, sdr_white_nits=white,
                       color_encoding="RGB", rotation=0)


def _monitor(gdi, x, y, w, h, fill=0.5):
    buf = np.full((h, w, 3), fill, np.float32)
    return MonitorCapture(gdi_name=gdi, x=x, y=y, width=w, height=h, rotation=0, linear=buf)


def _single():
    caps = {"A": _monitor("A", 0, 0, 100, 100, 0.5)}
    disps = [_display("A", 0, 0, 100, 100, primary=True)]
    return caps, disps


def test_region_fully_inside():
    caps, disps = _single()
    res = pipeline.capture_region((10, 20, 30, 40), caps, disps)
    assert res.region_phys == (10, 20, 30, 40)
    assert res.linear.shape == (40, 30, 3)


def test_region_partially_outside_is_clipped():
    caps, disps = _single()
    res = pipeline.capture_region((90, 90, 30, 30), caps, disps)
    assert res.region_phys == (90, 90, 10, 10)
    assert res.linear.shape == (10, 10, 3)


def test_region_degenerate_raises():
    caps, disps = _single()
    with pytest.raises(pipeline.RegionError):
        pipeline.capture_region((10, 10, 0, 0), caps, disps)


def test_region_no_intersection_raises():
    caps, disps = _single()
    with pytest.raises(pipeline.RegionError):
        pipeline.capture_region((500, 500, 10, 10), caps, disps)


def test_region_stitches_across_monitors():
    caps = {"A": _monitor("A", 0, 0, 100, 100, 0.2),
            "B": _monitor("B", 100, 0, 100, 100, 0.8)}
    disps = [_display("A", 0, 0, 100, 100, primary=True), _display("B", 100, 0, 100, 100)]
    res = pipeline.capture_region((50, 0, 100, 100), caps, disps)
    assert res.linear.shape == (100, 100, 3)
    assert set(res.spans_displays) == {"A", "B"}
    # Left half comes from A (0.2), right half from B (0.8).
    assert np.allclose(res.linear[:, :50], 0.2)
    assert np.allclose(res.linear[:, 50:], 0.8)


def test_buffer_region_missing_display_raises():
    caps, disps = _single()
    with pytest.raises(pipeline.RegionError):
        pipeline.capture_buffer_region(caps, disps, "NONEXISTENT", (0, 0, 10, 10))


def test_buffer_region_whole_screen():
    caps, disps = _single()
    res = pipeline.capture_buffer_region(caps, disps, "A", None)
    assert res.region_phys == (0, 0, 100, 100)


def test_buffer_region_crop_is_independent_copy():
    # A sub-region crop must not alias the source, so the GUI can release the full
    # capture without keeping every monitor's buffer alive (issue #16).
    caps, disps = _single()
    res = pipeline.capture_buffer_region(caps, disps, "A", (10, 10, 40, 40))
    assert not np.shares_memory(res.linear, caps["A"].linear)


def test_buffer_region_out_of_bounds_raises():
    caps, disps = _single()
    with pytest.raises(pipeline.RegionError):
        pipeline.capture_buffer_region(caps, disps, "A", (200, 200, 10, 10))


def test_capture_display_missing_raises():
    caps, disps = _single()
    other = _display("Z", 0, 0, 100, 100)
    with pytest.raises(pipeline.RegionError):
        pipeline.capture_display(other, caps, disps)


def _result(fill, white=80.0, hdr_enabled=True):
    buf = np.full((16, 16, 3), fill, np.float32)
    from hdrshot.core import color
    disp = _display("A", 0, 0, 16, 16, hdr=hdr_enabled, white=white)
    return pipeline.CaptureResult(linear=buf, sdr_white_nits=white, display=disp,
                                  region_phys=(0, 0, 16, 16),
                                  stats=color.hdr_stats(buf, white))


def test_choose_auto_format():
    assert pipeline.choose_auto_format(_result(8.0)) == "ultrahdr"   # HDR
    assert pipeline.choose_auto_format(_result(0.5)) == "png"        # SDR


def test_is_hdr_gating():
    # HDR content on an HDR-enabled display is "live" HDR.
    assert _result(8.0, hdr_enabled=True).is_hdr is True
    # Same content on an SDR display: content is HDR-capable but not live.
    r = _result(8.0, hdr_enabled=False)
    assert r.hdr_capable_content is True
    assert r.is_hdr is False


def test_timestamped_name_hdr_prefix():
    assert pipeline.timestamped_name("ultrahdr", hdr=True).startswith("HDR Screenshot")
    assert pipeline.timestamped_name("png", hdr=False).startswith("Screenshot")


def test_unique_path_avoids_clobber(tmp_path):
    p1 = pipeline._unique_path(str(tmp_path), "shot.png")
    open(p1, "wb").close()
    p2 = pipeline._unique_path(str(tmp_path), "shot.png")
    assert p1 != p2
    assert p2.endswith("(2).png")


def test_save_writes_unique_files(tmp_path):
    r = _result(8.0)
    a = pipeline.save(r, "png", str(tmp_path))
    b = pipeline.save(r, "png", str(tmp_path))
    assert a["path"] != b["path"]


@pytest.mark.skipif(not exr.available(), reason="OpenEXR optional extra is not installed on this architecture")
def test_encode_hdr_flag(tmp_path):
    r = _result(8.0)
    info = pipeline.encode(r, "exr", str(tmp_path / "x.exr"))
    assert info["hdr"] is True
    info2 = pipeline.encode(_result(0.5), "png", str(tmp_path / "y.png"))
    assert info2["hdr"] is False
