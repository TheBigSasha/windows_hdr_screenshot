"""ISO 21496-1 gain-map metadata (issue #9): structure + round-trip decode.

Byte layout verified against libultrahdr gainmapmetadata.cpp: big-endian; signed
gainMapMin/Max + offsets, unsigned gamma numerator + all denominators; the full
per-field-denominator layout (use_common_denominator clear).
"""
from __future__ import annotations

import struct

from hdrshot.encoders import iso_gainmap, ultrahdr


def _meta():
    return {
        "gain_min_log2": 0.0, "gain_max_log2": 4.3007, "gamma": 1.0,
        "offset_sdr": 1 / 64, "offset_hdr": 1 / 64,
        "cap_min_log2": 0.0, "cap_max_log2": 4.3007,
    }


def test_metadata_length_and_header():
    blob = iso_gainmap.build_metadata(_meta())
    # 5 (versions+flags) + 16 (base+alt headroom N/D) + 40 (10 per-channel N/D) = 61
    assert len(blob) == 61
    assert struct.unpack_from(">HH", blob, 0) == (0, 0)   # versions
    assert blob[4] == 0x40                                # flags: useBaseColorSpace


def test_metadata_roundtrip_values():
    m = _meta()
    blob = iso_gainmap.build_metadata(m)
    off = 5
    bn, bd, an, ad = struct.unpack_from(">IIII", blob, off)
    off += 16
    mn, md, xn, xd, gn, gd = struct.unpack_from(">iIiIII", blob, off)
    off += 24
    son, sod, aon, aod = struct.unpack_from(">iIiI", blob, off)

    assert bn / bd == 0.0                                 # base headroom (SDR)
    assert abs(an / ad - m["cap_max_log2"]) < 1e-4        # alternate headroom
    assert mn / md == 0.0                                 # gain map min
    assert abs(xn / xd - m["gain_max_log2"]) < 1e-4       # gain map max
    assert gn / gd == 1.0                                 # gamma
    assert son / sod == 1 / 64                            # base offset
    assert aon / aod == 1 / 64                            # alternate offset


def test_signed_gain_min_encoding():
    # A negative gain-map min must encode as a signed int32 (not wrap to huge u32).
    m = _meta()
    m["gain_min_log2"] = -0.5
    blob = iso_gainmap.build_metadata(m)
    mn, md = struct.unpack_from(">iI", blob, 21)
    assert mn / md < 0


def test_segments_wrap_with_iso_urn():
    full = iso_gainmap.full_segment(_meta())
    stub = iso_gainmap.version_stub_segment()
    assert full[:2] == b"\xFF\xE2"                        # APP2
    assert b"urn:iso:std:iso:ts:21496:-1\x00" in full
    assert struct.unpack_from(">H", full, 2)[0] == len(full) - 2
    assert struct.unpack_from(">H", stub, 2)[0] == len(stub) - 2
    assert len(stub) < len(full)


def test_ultrahdr_embeds_iso_metadata(tmp_path, hdr_scene):
    path = tmp_path / "shot.jpg"
    ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    data = path.read_bytes()
    # Both the base stub and the gain-map full block carry the ISO URN.
    assert data.count(b"urn:iso:std:iso:ts:21496:-1\x00") == 2
    assert b"hdrgm:GainMapMax" in data                    # hdrgm XMP still present too


def test_parse_reports_apple_compatible(tmp_path, hdr_scene):
    from hdrshot import agentcli
    path = tmp_path / "shot.jpg"
    ultrahdr.write_ultrahdr(str(path), hdr_scene, 80.0)
    meta = agentcli.parse_file(str(path))
    assert meta["container"]["iso_21496_1"] is True
    assert meta["apple_compatible"] is True
