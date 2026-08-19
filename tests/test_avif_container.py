"""Bounded AVIF metadata and ISO 21496-1 graph classification."""
from __future__ import annotations

import struct

import pytest

from hdrshot import agentcli
from hdrshot.encoders.avif_container import AvifContainerError, inspect_avif

from .avif_fixtures import box, make_avif


def test_primary_item_cicp_bit_depth_and_chroma_are_associated():
    meta = inspect_avif(make_avif())

    assert meta["primary_item_id"] == 1
    assert meta["primary_item_type"] == "av01"
    assert meta["bit_depth"] == 10
    assert meta["chroma_format"] == "yuv444"
    assert meta["color_primaries"] == 9
    assert meta["transfer_characteristics"] == 16
    assert meta["matrix_coefficients"] == 9
    assert meta["full_range_flag"] == 1
    assert meta["hdr_representation"] == "pq"
    assert meta["is_hdr"] is True


def test_nclx_full_range_is_the_high_bit_not_the_low_bit():
    assert inspect_avif(make_avif(nclx=(9, 16, 9, 1)))["full_range_flag"] == 1
    assert inspect_avif(make_avif(nclx=(9, 16, 9, 0)))["full_range_flag"] == 0

    malformed = make_avif().replace(
        b"nclx" + struct.pack(">HHH", 9, 16, 9) + b"\x80",
        b"nclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01",
    )
    with pytest.raises(AvifContainerError, match="reserved range bits"):
        inspect_avif(malformed)


def test_10bit_sdr_is_not_misclassified_as_hdr():
    meta = inspect_avif(make_avif(nclx=(1, 13, 1, 1), bit_depth=10))
    assert meta["bit_depth"] == 10
    assert meta["hdr_representation"] == "sdr"
    assert meta["is_hdr"] is False


def test_incidental_nclx_in_mdat_cannot_override_primary_item():
    fake = b"colrnclx" + struct.pack(">HHH", 9, 16, 9) + b"\x80"
    data = make_avif(nclx=(1, 13, 1, 1), bit_depth=8, stray_nclx=fake)
    meta = inspect_avif(data)
    assert meta["transfer_characteristics"] == 13
    assert meta["is_hdr"] is False
    assert agentcli._scan_avif_nclx(data) == (1, 13, 1, 1)


def test_iso_gainmap_requires_tmap_dimg_and_altr_graph():
    data = make_avif(gain_map=True, nclx=(1, 13, 1, 1), bit_depth=8)
    meta = inspect_avif(data)
    assert meta["gainmap_metadata_present"] is True
    assert meta["gainmap_metadata_valid"] is True
    assert meta["hdr_representation"] == "gain_map"
    assert meta["metadata_standard"] == "ISO 21496-1"
    assert meta["is_hdr"] is True
    assert agentcli._detect_format("capture.avif", data) == "uhdr-avif"
    parsed = agentcli._parse_avif("capture.avif", data)
    assert parsed["format"] == "uhdr-avif"
    assert parsed["metadata_standard"] == "ISO 21496-1"
    assert parsed["metadata_valid"] is True
    assert parsed["measurement_source"] == "metadata_only"
    assert parsed["viewer_compatibility"] == "not_tested"


def test_tmap_marker_without_reference_graph_is_not_claimed_as_ultrahdr():
    data = make_avif(gain_map=True)
    # Rename the real dimg reference. The tmap item remains, but its graph is incomplete.
    data = data.replace(b"dimg", b"junk", 1)
    meta = inspect_avif(data)
    assert meta["gainmap_metadata_present"] is True
    assert meta["gainmap_metadata_valid"] is False
    assert meta["hdr_representation"] == "pq"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda data: data[:-1],
        lambda data: b"\0\0\0\x04" + data[4:],
        lambda data: data.replace(b"ipma", b"junk", 1),
        lambda data: data.replace(b"avif", b"heic", 2),
    ],
    ids=["truncated", "box-smaller-than-header", "missing-ipma", "not-avif"],
)
def test_malformed_or_ambiguous_containers_fail_closed(mutator):
    with pytest.raises(AvifContainerError):
        inspect_avif(mutator(make_avif()))


def test_box_count_budget_is_enforced():
    data = make_avif() + b"".join(box(b"free", b"") for _ in range(4096))
    with pytest.raises(AvifContainerError, match="exceeds 4096 boxes"):
        inspect_avif(data)
