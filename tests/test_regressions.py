"""Regression tests for the pre-0.2.0 review findings: agent-CLI error contract,
template hardening, config robustness, gain-map capacity separation."""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from hdrshot import agentcli
from hdrshot.config import Config
from hdrshot.core import color, pipeline
from hdrshot.core.types import DisplayInfo


def _result(scene: np.ndarray) -> pipeline.CaptureResult:
    disp = DisplayInfo(0, "A", "A", 0, 0, scene.shape[1], scene.shape[0],
                       True, True, True, 10, 80.0, "RGB")
    return pipeline.CaptureResult(linear=scene, sdr_white_nits=80.0, display=disp,
                                  region_phys=(0, 0, scene.shape[1], scene.shape[0]),
                                  stats=color.hdr_stats(scene, 80.0))


# --------------------------------------------------------------------------- #
# Agent CLI failure contract: JSON + exit 2, never a traceback
# --------------------------------------------------------------------------- #
def test_cmd_parse_garbage_file_emits_json_exit_2(tmp_path, capsys):
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe1\xff\xff" + b"Exif\x00\x00MM\x00*" * 3)
    args = argparse.Namespace(file=str(bad), preview=None, json=True)
    assert agentcli.cmd_parse(args) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["is_hdr"] is None
    assert "error" in out


def test_cmd_parse_missing_file_emits_json_exit_2(capsys):
    args = argparse.Namespace(file="definitely_missing_98765.jpg", preview=None, json=True)
    assert agentcli.cmd_parse(args) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["is_hdr"] is None
    assert "error" in out


def test_cmd_check_garbage_file_undetermined(tmp_path, capsys):
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe1\xff\xffjunk")
    args = argparse.Namespace(file=str(bad), min_nits=None, min_stops=None, json=True)
    assert agentcli.cmd_check(args) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["pass"] is False
    assert out["is_hdr"] is None


def test_main_capture_bad_region_emits_json_error_exit_3(capsys):
    import pytest

    from hdrshot.__main__ import main
    try:
        rc = main(["capture", "--region", "999999", "999999", "10", "10", "--json"])
    except SystemExit:
        pytest.skip("no capture backend on this OS")
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert "error" in out and out["error"]["message"]


def test_detect_format_scans_whole_file():
    # Real-world UltraHDR can carry EXIF/ICC first: markers sit past any 4 KiB prefix.
    data = b"\xff\xd8" + b"\x00" * 5000 + b"MPF\x00hdr-gain-map"
    assert agentcli._detect_format("x.jpg", data) == "ultrahdr"


def test_check_zero_stops_is_a_real_value(tmp_path, capsys):
    sdr = np.full((16, 16, 3), 0.5, np.float32)
    p = tmp_path / "x.exr"
    pipeline.encode(_result(sdr), "exr", str(p))
    args = argparse.Namespace(file=str(p), min_nits=None, min_stops=1.0, json=True)
    assert agentcli.cmd_check(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["headroom_stops"] == 0.0
    assert not any("unknown" in r for r in out["reasons"])


def test_parse_exr_nonfinite_pixels_yield_strict_json(tmp_path, capsys):
    from hdrshot.encoders import exr as exr_enc
    arr = np.full((8, 8, 3), 2.0, np.float32)
    arr[0, 0, 0] = np.inf
    p = tmp_path / "inf.exr"
    exr_enc.write_exr(str(p), arr, 80.0)
    args = argparse.Namespace(file=str(p), preview=None, json=True)
    rc = agentcli.cmd_parse(args)
    out = json.loads(capsys.readouterr().out)   # json.loads == strict-JSON gate
    assert rc in (0, 1, 2)
    assert out["max_linear"] == 2.0             # the inf is masked, not propagated


def test_parse_exr_with_alpha_channel(tmp_path):
    from hdrshot.encoders import exr as exr_enc
    rgba = np.dstack([np.full((8, 8, 3), 3.0, np.float32), np.ones((8, 8), np.float32)])
    p = tmp_path / "a.exr"
    exr_enc.write_exr(str(p), rgba, 80.0)
    meta = agentcli.parse_file(str(p))
    assert meta["is_hdr"] is True
    assert meta["max_linear"] == 3.0


# --------------------------------------------------------------------------- #
# Filename template hardening
# --------------------------------------------------------------------------- #
def test_template_literal_braces_terminate(tmp_path):
    sdr = np.full((8, 8, 3), 0.5, np.float32)
    info1 = pipeline.save(_result(sdr), "png", str(tmp_path), template="shot {{n}}")
    info2 = pipeline.save(_result(sdr), "png", str(tmp_path), template="shot {{n}}")
    assert info1["path"] != info2["path"]
    assert os.path.exists(info1["path"]) and os.path.exists(info2["path"])


def test_template_cannot_escape_save_dir(tmp_path):
    sdr = np.full((8, 8, 3), 0.5, np.float32)
    out = tmp_path / "shots"
    out.mkdir()
    info = pipeline.save(_result(sdr), "png", str(out), template="..\\..\\evil {date}")
    assert os.path.dirname(info["path"]) == str(out)
    assert os.path.exists(info["path"])


def test_template_reserved_device_name():
    name = pipeline.render_filename("CON", "png", False)
    assert name.split(".")[0].upper() not in ("CON", "PRN", "AUX", "NUL")


# --------------------------------------------------------------------------- #
# Config robustness
# --------------------------------------------------------------------------- #
def test_config_load_non_utf8_uses_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes('{"default_format": "exr"}'.encode("utf-16"))
    c = Config.load(str(p))                      # must not raise
    assert c.get("default_format") == "auto"


def test_config_load_wrong_types_fall_back(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"gainmap_quality": "high", "notifications": "yes",
                             "hotkey_region": 5, "save_dir": ["x"],
                             "filename_template": "{hdr}ok {date}"}),
                 encoding="utf-8")
    c = Config.load(str(p))
    assert c.get("gainmap_quality") == 90
    assert c.get("notifications") is True
    assert c.get("hotkey_region") == "ctrl+shift+h"
    assert c.get("save_dir") == ""
    assert c.get("filename_template") == "{hdr}ok {date}"   # valid value kept


def test_resolved_save_dir_falls_back_on_bad_path(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    bad = str(blocker / "sub")                   # a dir under a file: OSError everywhere
    c = Config(data={**Config.load(str(tmp_path / "none.json")).data, "save_dir": bad})
    d = c.resolved_save_dir()                    # must not raise
    assert os.path.isdir(d)


# --------------------------------------------------------------------------- #
# Gain-map capacity separation (UltraHDR XMP + ISO 21496-1)
# --------------------------------------------------------------------------- #
def test_gainmap_capacities_strictly_separated():
    from hdrshot.encoders.ultrahdr import compute_gainmap
    flat_boosted = np.full((16, 16, 3), 4.0, np.float32)   # uniform 4x over SDR white
    _, meta = compute_gainmap(flat_boosted, 80.0)
    assert meta["cap_max_log2"] > meta["cap_min_log2"]
    flat_sdr = np.full((16, 16, 3), 0.5, np.float32)
    _, meta2 = compute_gainmap(flat_sdr, 80.0)
    assert meta2["cap_max_log2"] > meta2["cap_min_log2"]
