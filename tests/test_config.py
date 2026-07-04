"""Config persistence + filename templates (issues #4, #5)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from hdrshot import config
from hdrshot.core import color, pipeline
from hdrshot.core.types import DisplayInfo


def test_config_defaults(tmp_path):
    c = config.Config.load(str(tmp_path / "nope.json"))
    assert c.get("default_format") == "auto"
    assert c.get("notifications") is True


def test_config_roundtrip(tmp_path):
    p = str(tmp_path / "config.json")
    c = config.Config.load(p)
    c.set("default_format", "exr")
    c.set("save_dir", str(tmp_path / "shots"))
    c.save()
    c2 = config.Config.load(p)
    assert c2.get("default_format") == "exr"
    assert c2.get("save_dir") == str(tmp_path / "shots")


def test_config_ignores_junk_and_bad_format(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"default_format": "not-a-format", "bogus": 123}')
    c = config.Config.load(str(p))
    assert c.default_format() == "auto"          # invalid falls back
    assert "bogus" not in c.data                 # unknown keys dropped


def test_config_corrupt_json_uses_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ this is not json")
    c = config.Config.load(str(p))
    assert c.get("default_format") == "auto"


def test_resolved_save_dir_custom(tmp_path):
    c = config.Config.load(str(tmp_path / "c.json"))
    target = str(tmp_path / "myshots")
    c.set("save_dir", target)
    assert c.resolved_save_dir() == target


# -- filename templates ----------------------------------------------------- #
WHEN = datetime(2026, 7, 3, 15, 30, 45)


def test_render_filename_tokens():
    name = pipeline.render_filename("{hdr}Screenshot {date} {time}", "ultrahdr", True, when=WHEN)
    assert name == "HDR Screenshot 2026-07-03 153045.jpg"
    sdr = pipeline.render_filename("{hdr}Screenshot {date} {time}", "png", False, when=WHEN)
    assert sdr == "Screenshot 2026-07-03 153045.png"


def test_render_filename_display_and_format():
    name = pipeline.render_filename("{display}-{format}-{date}", "exr", True,
                                    display="M27P6", when=WHEN)
    assert name == "M27P6-exr-2026-07-03.exr"


def test_render_filename_sanitizes_display():
    name = pipeline.render_filename("{display}", "png", False, display='a/b:c*d', when=WHEN)
    assert "/" not in name and ":" not in name and "*" not in name


def test_render_filename_n_token():
    assert pipeline.render_filename("shot{n}", "png", False, n=1, when=WHEN) == "shot.png"
    assert pipeline.render_filename("shot{n}", "png", False, n=3, when=WHEN) == "shot (3).png"


def test_validate_template_rejects_unknown_token():
    with pytest.raises(ValueError):
        pipeline.validate_template("{bogus}-shot")
    pipeline.validate_template("{hdr}{date}{time}{display}{format}{n}")  # all valid


def _result(fill):
    buf = np.full((8, 8, 3), fill, np.float32)
    disp = DisplayInfo(0, "A", "MyMon", 0, 0, 8, 8, True, True, True, 10, 80.0, "RGB")
    return pipeline.CaptureResult(linear=buf, sdr_white_nits=80.0, display=disp,
                                  region_phys=(0, 0, 8, 8), stats=color.hdr_stats(buf, 80.0))


def test_save_with_template(tmp_path):
    info = pipeline.save(_result(8.0), "png", str(tmp_path),
                         template="{display}_{date}")
    import os
    assert os.path.basename(info["path"]).startswith("MyMon_")


def test_save_template_collision_suffix(tmp_path):
    r = _result(0.5)
    a = pipeline.save(r, "png", str(tmp_path), template="fixed")
    b = pipeline.save(r, "png", str(tmp_path), template="fixed")
    assert a["path"] != b["path"]
    assert b["path"].endswith("(2).png")
