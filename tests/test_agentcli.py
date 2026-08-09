"""Agent CLI: file parsing, HDR detection, check exit logic, JSON shape."""
from __future__ import annotations

import argparse
import json

import pytest

from hdrshot import agentcli
from hdrshot.core import color, pipeline
from hdrshot.core.types import DisplayInfo
from hdrshot.encoders import exr

EXR_SKIP_REASON = "OpenEXR optional extra is not installed on this architecture"


def _write(tmp_path, fmt, scene):
    disp = DisplayInfo(0, "A", "A", 0, 0, scene.shape[1], scene.shape[0],
                       True, True, True, 10, 80.0, "RGB")
    res = pipeline.CaptureResult(linear=scene, sdr_white_nits=80.0, display=disp,
                                 region_phys=(0, 0, scene.shape[1], scene.shape[0]),
                                 stats=color.hdr_stats(scene, 80.0))
    path = tmp_path / f"x{pipeline.EXT[fmt]}"
    pipeline.encode(res, fmt, str(path))
    return str(path)


def test_parse_ultrahdr(tmp_path, hdr_scene):
    p = _write(tmp_path, "ultrahdr", hdr_scene)
    meta = agentcli.parse_file(p)
    assert meta["format"] == "uhdr-jpeg"
    assert meta["is_hdr"] is True
    assert meta["gainmap_max_stops"] > 0
    assert meta["container"]["mpf"] is True


@pytest.mark.skipif(not exr.available(), reason=EXR_SKIP_REASON)
def test_parse_exr(tmp_path, hdr_scene):
    p = _write(tmp_path, "exr", hdr_scene)
    meta = agentcli.parse_file(p)
    assert meta["format"] == "exr"
    assert meta["is_hdr"] is True
    assert meta["peak_nits"] > 400


def test_parse_png_is_sdr(tmp_path, sdr_scene):
    p = _write(tmp_path, "png", sdr_scene)
    meta = agentcli.parse_file(p)
    assert meta["format"] == "png"
    assert meta["is_hdr"] is False


def test_parse_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        agentcli.parse_file("does_not_exist_12345.jpg")


def test_detect_format(tmp_path, hdr_scene, sdr_scene):
    uhdr = _write(tmp_path, "ultrahdr", hdr_scene)
    with open(uhdr, "rb") as f:
        assert agentcli._detect_format(uhdr, f.read(4096)) == "uhdr-jpeg"
    plain = _write(tmp_path, "jpeg", sdr_scene)
    with open(plain, "rb") as f:
        assert agentcli._detect_format(plain, f.read(4096)) == "jpeg"


@pytest.mark.skipif(not exr.available(), reason=EXR_SKIP_REASON)
def test_check_hdr_passes(tmp_path, hdr_scene, capsys):
    p = _write(tmp_path, "exr", hdr_scene)
    args = argparse.Namespace(file=p, min_nits=None, min_stops=None, json=False)
    assert agentcli.cmd_check(args) == 0


def test_check_sdr_fails(tmp_path, sdr_scene):
    p = _write(tmp_path, "png", sdr_scene)
    args = argparse.Namespace(file=p, min_nits=None, min_stops=None, json=False)
    assert agentcli.cmd_check(args) == 1


@pytest.mark.skipif(not exr.available(), reason=EXR_SKIP_REASON)
def test_check_min_nits_threshold(tmp_path, hdr_scene):
    p = _write(tmp_path, "exr", hdr_scene)
    # scene peaks ~1600 nits; requiring 3000 must fail.
    args = argparse.Namespace(file=p, min_nits=3000.0, min_stops=None, json=False)
    assert agentcli.cmd_check(args) == 1
    args_ok = argparse.Namespace(file=p, min_nits=500.0, min_stops=None, json=False)
    assert agentcli.cmd_check(args_ok) == 0


@pytest.mark.skipif(not exr.available(), reason=EXR_SKIP_REASON)
def test_check_json_shape(tmp_path, hdr_scene, capsys):
    p = _write(tmp_path, "exr", hdr_scene)
    args = argparse.Namespace(file=p, min_nits=None, min_stops=None, json=True)
    agentcli.cmd_check(args)
    out = json.loads(capsys.readouterr().out)
    assert out["pass"] is True
    assert out["is_hdr"] is True


def test_captures_to_json_shape(hdr_scene):
    disp = DisplayInfo(0, "A", "A", 0, 0, 320, 200, True, True, True, 10, 480.0, "RGB")
    res = pipeline.CaptureResult(linear=hdr_scene, sdr_white_nits=480.0, display=disp,
                                 region_phys=(0, 0, 320, 200),
                                 stats=color.hdr_stats(hdr_scene, 480.0))
    info = {"format": "uhdr-jpeg", "path": "x.jpg", "hdr": True, "gainmap_max_stops": 2.5}
    payload = json.loads(agentcli.captures_to_json([(disp, res, info)]))
    assert "captures" in payload and len(payload["captures"]) == 1
    c = payload["captures"][0]
    assert c["format"] == "uhdr-jpeg"
    assert c["display"]["gdi_name"] == "A"
    assert "notes" in payload


def test_preview_from_linear(tmp_path, hdr_scene):
    out = tmp_path / "prev.png"
    agentcli.write_preview_from_linear(hdr_scene, 80.0, str(out))
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (hdr_scene.shape[1], hdr_scene.shape[0])
