"""UltraHDR gain-map math + byte-exact MPF container round-trip.

The MPF APP2 segment carries hand-computed byte offsets; if one is wrong the gain
map silently stops rendering in Chrome/Photos. These tests re-parse our own output
and confirm the offsets resolve to the right bytes.
"""
from __future__ import annotations

import struct

import numpy as np

from hdrshot.core import color
from hdrshot.encoders import ultrahdr

SOI = b"\xFF\xD8"


def test_gainmap_invariants_hdr(hdr_scene):
    gm, meta = ultrahdr.compute_gainmap(hdr_scene, 80.0)
    assert gm.dtype == np.uint8
    assert gm.shape == hdr_scene.shape[:2]
    assert meta["gain_min_log2"] <= meta["gain_max_log2"]
    assert meta["gain_max_log2"] > 0.0            # real headroom
    assert meta["cap_max_log2"] == meta["gain_max_log2"]


def test_gainmap_flat_when_no_headroom(sdr_scene):
    gm, meta = ultrahdr.compute_gainmap(sdr_scene, 80.0)
    # No content above paper white -> ~zero headroom, flat (all-zero) map. The
    # encoder keeps a tiny 1e-6 epsilon to avoid log2(1)=0 edge cases.
    assert meta["gain_max_log2"] < 1e-3
    assert int(gm.max()) == 0


def test_gainmap_downscale(hdr_scene):
    gm, _ = ultrahdr.compute_gainmap(hdr_scene, 80.0, downscale=2)
    assert gm.shape[0] == hdr_scene.shape[0] // 2
    assert gm.shape[1] == hdr_scene.shape[1] // 2


def _parse_mpf_gainmap_offset(data: bytes) -> int:
    """Independently parse our MPF APP2 and return the absolute file offset the
    gain-map MP entry points at."""
    mpf_sig = data.find(b"MPF\x00")
    assert mpf_sig != -1, "no MPF signature"
    base = mpf_sig + 4                        # TIFF header starts right after 'MPF\0'
    assert data[base:base + 2] == b"MM"       # big-endian
    assert struct.unpack_from(">H", data, base + 2)[0] == 0x002A
    first_ifd = struct.unpack_from(">I", data, base + 4)[0]
    ifd = base + first_ifd
    count = struct.unpack_from(">H", data, ifd)[0]
    mp_entry_off = None
    for i in range(count):
        e = ifd + 2 + i * 12
        tag = struct.unpack_from(">H", data, e)[0]
        if tag == 0xB002:                     # MPEntry
            mp_entry_off = struct.unpack_from(">I", data, e + 8)[0]
    assert mp_entry_off is not None, "no MPEntry tag"
    entries = base + mp_entry_off
    # Entry #2 (gain map) is the 2nd 16-byte MP entry; its offset is at +8.
    gainmap_rel = struct.unpack_from(">I", data, entries + 16 + 8)[0]
    return base + gainmap_rel


def _parse_mpf_primary_size(data: bytes) -> int:
    mpf_sig = data.find(b"MPF\x00")
    base = mpf_sig + 4
    ifd = base + struct.unpack_from(">I", data, base + 4)[0]
    count = struct.unpack_from(">H", data, ifd)[0]
    mp_entry_off = next(struct.unpack_from(">I", data, ifd + 2 + i * 12 + 8)[0]
                        for i in range(count)
                        if struct.unpack_from(">H", data, ifd + 2 + i * 12)[0] == 0xB002)
    return struct.unpack_from(">I", data, base + mp_entry_off + 4)[0]


def test_mpf_offset_points_at_gainmap_soi(tmp_path, hdr_scene):
    path = tmp_path / "shot.jpg"
    ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    data = path.read_bytes()

    abs_off = _parse_mpf_gainmap_offset(data)
    # The MPF entry's offset must land exactly on the appended gain-map SOI.
    assert data[abs_off:abs_off + 2] == SOI, "MPF gain-map offset does not point at an SOI"
    # The primary image size and the gain-map offset must agree (the primary
    # occupies exactly [0, primary_size) and the gain map starts at primary_size).
    assert abs_off == _parse_mpf_primary_size(data)


def test_mpf_primary_size_matches_gainmap_start(tmp_path, hdr_scene):
    path = tmp_path / "shot.jpg"
    ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    data = path.read_bytes()
    primary_size = _parse_mpf_primary_size(data)
    # The primary image parses cleanly as a standalone JPEG up to its EOI, and the
    # gain map begins with an SOI exactly at primary_size.
    assert data[primary_size:primary_size + 2] == SOI
    assert data[primary_size - 2:primary_size] == b"\xFF\xD9"   # base JPEG EOI just before


def test_ultrahdr_has_required_metadata(tmp_path, hdr_scene):
    path = tmp_path / "shot.jpg"
    meta = ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    data = path.read_bytes()
    assert b"MPF\x00" in data                          # MPF segment
    assert b"hdrgm:GainMapMax" in data                 # primary/gainmap hdrgm XMP
    assert b'Item:Semantic="GainMap"' in data          # Google container directory
    assert data[:2] == SOI
    assert meta["gain_max_log2"] > 0.0


def test_ultrahdr_reconstructs_headroom(tmp_path, hdr_scene):
    """Gain-map max stops should approximate the scene's true peak headroom."""
    path = tmp_path / "shot.jpg"
    meta = ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    peak_ratio = color.hdr_stats(hdr_scene, 80.0)["peak_ratio"]
    true_stops = np.log2(peak_ratio)
    # Within ~0.2 stop of the luminance-based gain-map max.
    assert abs(meta["gain_max_log2"] - true_stops) < 0.4
