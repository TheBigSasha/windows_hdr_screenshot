"""Regression coverage for the follow-up codec/release review.

These tests deliberately model optional native providers instead of using
``importorskip``.  A missing provider is a supported source-install state, but
an installed provider that cannot load is a broken state and must never become
a green skip.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import PIL
import pytest

from hdrshot import agentcli, config
from hdrshot import selftest as selftest_module
from hdrshot.codecs import (
    PROFILES,
    BundleContractError,
    CodecCapability,
    CodecUnavailableError,
    registry,
)
from hdrshot.core import color, pipeline
from hdrshot.core.pipeline import CodecEncodeError
from hdrshot.encoders import avif_hdr, heic


def _cap(profile: str, *, available: bool, status: str,
         reason: str | None = None) -> CodecCapability:
    representation = {
        "uhdr-jpeg": "gain_map",
        "uhdr-avif": "gain_map",
        "uhdr-heic": "gain_map",
        "pq-avif": "pq",
        "pq-heic": "pq",
        "exr": "linear",
        "png": "sdr",
        "jpeg": "sdr",
        "avif-sdr": "sdr",
    }[profile]
    provider = {
        "uhdr-jpeg": "hdrshot UltraHDR encoder",
        "uhdr-avif": "libultrahdr",
        "uhdr-heic": "libultrahdr",
        "pq-avif": "imagecodecs/libavif",
        "pq-heic": "pillow-heif",
        "exr": "OpenEXR",
        "png": "Pillow",
        "jpeg": "Pillow",
        "avif-sdr": "pillow-avif-plugin",
    }[profile]
    return CodecCapability(profile, available, status, reason, representation,
                           provider, "test-provider-1.0")


def _caps(*available: str) -> dict[str, CodecCapability]:
    return {
        profile: _cap(profile, available=profile in available,
                      status="available" if profile in available else "excluded",
                      reason=None if profile in available else "not in test bundle")
        for profile in PROFILES
    }


def _result(fill: float = 0.5) -> pipeline.CaptureResult:
    linear = np.full((8, 8, 3), fill, np.float32)
    return pipeline.CaptureResult(
        linear=linear,
        sdr_white_nits=80.0,
        display=None,
        region_phys=(0, 0, 8, 8),
        stats=color.hdr_stats(linear, 80.0),
    )


# --------------------------------------------------------------------------- #
# Frozen capability manifests and stale self-test artifacts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"_remove_expected_profiles": True},
        {"schema_version": 1, "expected_profiles": "png"},
        {"schema_version": 1, "expected_profiles": ["not-a-profile"]},
        {"schema_version": 1, "expected_profiles": ["png", "png"]},
        {"schema_version": "1", "expected_profiles": ["png"]},
    ],
    ids=["missing-profiles", "profiles-not-list", "unknown-profile",
         "duplicate-profile", "wrong-schema-type"],
)
def test_invalid_frozen_manifest_fails_selftest_closed(tmp_path, monkeypatch, payload):
    """Malformed frozen manifests must not turn into an empty green bundle."""
    payload = {
        "schema_version": 1,
        "architecture": "x64",
        "expected_profiles": ["uhdr-jpeg", "png", "jpeg"],
        **payload,
    }
    if payload.pop("_remove_expected_profiles", False):
        payload.pop("expected_profiles")
    monkeypatch.setattr(registry.platform, "machine", lambda: "AMD64")
    manifest_path = tmp_path / "bundle-capabilities.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HDRSHOT_CAPABILITIES_MANIFEST", str(manifest_path))

    # A malformed manifest must produce a normal self-test failure, not a
    # traceback and not success after excluding every profile.
    with pytest.raises(BundleContractError):
        selftest_module.run_selftest(str(tmp_path / "out"))


def test_duplicate_manifest_json_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(registry.platform, "machine", lambda: "AMD64")
    manifest_path = tmp_path / "bundle-capabilities.json"
    manifest_path.write_text(
        '{"schema_version":1,"architecture":"x64",'
        '"expected_profiles":["uhdr-jpeg","png","jpeg"],'
        '"expected_profiles":["uhdr-jpeg","png","jpeg"]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HDRSHOT_CAPABILITIES_MANIFEST", str(manifest_path))

    with pytest.raises(BundleContractError, match="duplicate object key"):
        registry.bundle_manifest()


def test_frozen_selftest_requires_every_manifest_profile_even_with_stale_file(
    tmp_path, monkeypatch
):
    """An old output file cannot satisfy a currently unavailable native lane."""
    out = tmp_path / "out"
    out.mkdir()
    stale = out / "selftest_exr.exr"
    stale.write_bytes(b"stale artifact from an earlier build")

    monkeypatch.setattr(
        selftest_module,
        "bundle_manifest",
        lambda: {"schema_version": 1, "architecture": "test",
                 "expected_profiles": ["png", "exr"]},
    )
    monkeypatch.setattr(selftest_module, "capabilities", lambda: _caps("png"))

    assert selftest_module.run_selftest(str(out)) == 1
    assert stale.read_bytes() == b"stale artifact from an earlier build"


def test_checked_in_bundle_manifest_is_a_unique_known_profile_contract():
    """The source manifest remains a small, deterministic base-bundle contract."""
    path = Path(__file__).parents[1] / "packaging" / "bundle-capabilities.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = manifest["expected_profiles"]

    assert manifest["schema_version"] == 1
    assert manifest["architecture"]
    assert expected == ["uhdr-jpeg", "png", "jpeg"]
    assert len(expected) == len(set(expected))
    assert set(expected) <= set(PROFILES)


# --------------------------------------------------------------------------- #
# Native profile/result provenance and structured unavailable errors
# --------------------------------------------------------------------------- #
def test_encode_reports_resolved_native_profile_and_provider_provenance(tmp_path):
    info = pipeline.encode(_result(), "auto", str(tmp_path / "capture.png"))

    assert isinstance(info, pipeline.EncodeResult)
    assert info.requested_format == "auto"
    assert info.actual_profile == "png"
    assert info["format"] == "auto"
    assert info["profile"] == "png"
    assert info["requested_profile"] == "png"
    assert info["actual_profile"] == "png"
    assert info.legacy_profile == "png"
    assert info.container == "png"
    assert info.hdr_representation == "sdr"
    assert info["provider"] == "Pillow"
    assert info["provider_version"] == PIL.__version__
    assert info["encoded_hdr"] is False


def test_explicit_unavailable_error_preserves_structured_capability(monkeypatch, tmp_path):
    missing = _cap("pq-heic", available=False, status="missing",
                    reason="install the optional [heic] extra")
    monkeypatch.setattr(pipeline, "_capability", lambda profile: missing)

    with pytest.raises(CodecUnavailableError) as caught:
        pipeline.encode(_result(), "pq-heic", str(tmp_path / "capture.heic"))

    error = caught.value
    assert error.capability is missing
    assert error.capability.to_dict() == {
        "profile": "pq-heic",
        "available": False,
        "status": "missing",
        "reason": "install the optional [heic] extra",
        "hdr_representation": "pq",
        "provider": "pillow-heif",
        "provider_version": "test-provider-1.0",
    }
    assert error.capability.profile == "pq-heic"
    assert "pq-heic" in str(error)
    assert "missing" in str(error)
    assert "optional [heic] extra" in str(error)


def test_encode_failure_preserves_selected_profile_and_provider(monkeypatch, tmp_path):
    def fail_write(*args, **kwargs):
        raise OSError("simulated native encoder failure")

    monkeypatch.setattr(pipeline.sdr, "write_png", fail_write)

    with pytest.raises(CodecEncodeError) as caught:
        pipeline.encode(_result(), "png", str(tmp_path / "capture.png"))

    error = caught.value
    assert error.requested_profile == "png"
    assert error.actual_profile == "png"
    assert error.provider == "Pillow"
    assert error.provider_version == PIL.__version__
    assert error.status == "broken"
    assert "simulated native encoder failure" in error.reason


def test_capabilities_payload_keeps_unavailable_profiles_structured(monkeypatch):
    missing = _cap("pq-heic", available=False, status="missing", reason="not installed")
    broken = _cap("pq-avif", available=False, status="broken", reason="DLL load failed")
    fake = _caps("uhdr-jpeg", "png", "jpeg")
    fake.update({"pq-heic": missing, "pq-avif": broken})
    monkeypatch.setattr(registry, "capabilities", lambda: fake)
    monkeypatch.setattr(registry, "bundle_manifest", lambda: None)

    payload = registry.capabilities_payload()
    by_profile = {item["profile"]: item for item in payload["profiles"]}
    unavailable = {item["profile"]: item for item in payload["unavailable_profiles"]}

    assert payload["available_profiles"] == ["uhdr-jpeg", "png", "jpeg"]
    assert set(unavailable) == set(PROFILES) - set(payload["available_profiles"])
    assert unavailable["pq-heic"]["status"] == "missing"
    assert unavailable["pq-heic"]["reason"] == "not installed"
    assert unavailable["pq-avif"]["status"] == "broken"
    assert unavailable["pq-avif"]["reason"] == "DLL load failed"
    assert by_profile["pq-heic"] == unavailable["pq-heic"]


# --------------------------------------------------------------------------- #
# Config behavior and optional native-provider lanes without skips
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["missing", "broken", "excluded"])
def test_config_preserves_each_unavailable_explicit_profile_with_diagnostics(
    monkeypatch, status
):
    unavailable = _cap("pq-heic", available=False, status=status, reason=f"{status} in test")
    monkeypatch.setattr(config, "capability", lambda profile: unavailable)
    c = config.Config(data={**config.DEFAULTS, "default_format": "pq-heic"})

    # The persisted choice remains explicit, while its unavailability stays
    # machine-readable for the UI instead of silently selecting another codec.
    assert c.default_format() == "pq-heic"
    assert c.effective_default_format() == "pq-heic"
    validation = c.format_validation()
    assert validation.valid is True
    assert validation.available is False
    assert validation.code == "unavailable"
    assert validation.status == status
    assert validation.reason == f"{status} in test"


def test_config_keeps_available_builtin_profile_explicit(monkeypatch):
    c = config.Config(data={**config.DEFAULTS, "default_format": "png"})
    assert c.effective_default_format() == "png"


@pytest.mark.parametrize(
    "profile,module",
    [("exr", "OpenEXR"), ("pq-heic", "pillow_heif"),
     ("avif-sdr", "pillow_avif"), ("pq-avif", "imagecodecs")],
)
def test_optional_missing_provider_is_reported_without_skip(monkeypatch, profile, module):
    monkeypatch.setattr(registry.importlib.util, "find_spec", lambda name: None)

    cap = registry._optional(profile, module)

    assert cap.available is False
    assert cap.status == "missing"
    assert cap.reason
    assert "optional" in cap.reason


@pytest.mark.parametrize(
    "profile,module",
    [("exr", "OpenEXR"), ("pq-heic", "pillow_heif"),
     ("avif-sdr", "pillow_avif"), ("pq-avif", "imagecodecs")],
)
def test_optional_broken_provider_is_reported_without_skip(monkeypatch, profile, module):
    monkeypatch.setattr(registry.importlib.util, "find_spec", lambda name: object())

    def fail_import(name):
        raise OSError("native DLL failed to load")

    monkeypatch.setattr(registry.importlib, "import_module", fail_import)
    cap = registry._optional(profile, module)

    assert cap.available is False
    assert cap.status == "broken"
    assert "failed to load" in (cap.reason or "")
    assert "OSError" in (cap.reason or "")


def test_avif_hdr_provider_without_nclx_arguments_is_broken_not_skipped(monkeypatch):
    module = SimpleNamespace(
        __version__="2.0.0",
        AVIF=SimpleNamespace(available=True),
        avif_encode=lambda pixels: b"avif",
    )
    monkeypatch.setattr(registry.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: module)

    cap = registry._optional("pq-avif", "imagecodecs")

    assert cap.available is False
    assert cap.status == "broken"
    assert "nclx profile arguments" in (cap.reason or "")


def test_avif_hdr_provider_with_nclx_arguments_is_available_without_skip(monkeypatch):
    def avif_encode(pixels, *, primaries, transfer, matrix):
        return b"avif"

    module = SimpleNamespace(
        __version__="2.0.0",
        AVIF=SimpleNamespace(available=True),
        avif_encode=avif_encode,
    )
    monkeypatch.setattr(registry.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: module)

    cap = registry._optional("pq-avif", "imagecodecs")

    assert cap.available is True
    assert cap.status == "available"
    assert cap.provider_version == "2.0.0"


# --------------------------------------------------------------------------- #
# Complete CICP metadata (primaries, transfer, and matrix)
# --------------------------------------------------------------------------- #
def test_pq_encoders_declare_complete_bt2020_pq_cicp():
    assert (heic.CP_BT2020, heic.TC_PQ, heic.MC_BT2020_NCL) == (9, 16, 9)
    assert (avif_hdr.CP_BT2020, avif_hdr.TC_PQ, avif_hdr.MC_BT2020_NCL) == (9, 16, 9)


def test_heic_writer_passes_all_cicp_fields(monkeypatch, tmp_path):
    seen = {}

    def encode(fmt, size, data, fp, **kwargs):
        seen.update(kwargs)
        fp.write(b"nclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01\x00")

    monkeypatch.setitem(sys.modules, "pillow_heif", SimpleNamespace(encode=encode))
    result = heic.write_heic_pq(
        str(tmp_path / "capture.heic"), np.full((2, 3, 3), 1.0, np.float32)
    )

    assert seen["color_primaries"] == 9
    assert seen["transfer_characteristics"] == 16
    assert seen["matrix_coefficients"] == 9
    assert seen["full_range_flag"] == 1
    assert result["cicp"] == {
        "color_primaries": 9,
        "transfer_characteristics": 16,
        "matrix_coefficients": 9,
        "full_range_flag": 1,
    }


def test_avif_writer_passes_all_cicp_fields(monkeypatch, tmp_path):
    seen = {}

    def avif_encode(pixels, **kwargs):
        seen.update(kwargs)
        return b"nclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01\x00"

    monkeypatch.setitem(sys.modules, "imagecodecs", SimpleNamespace(avif_encode=avif_encode))
    result = avif_hdr.write_avif_pq(
        str(tmp_path / "capture.avif"), np.full((2, 3, 3), 1.0, np.float32)
    )

    assert seen["primaries"] == 9
    assert seen["transfer"] == 16
    assert seen["matrix"] == 9
    assert seen["bitspersample"] == 10
    assert result["cicp"] == {
        "color_primaries": 9,
        "transfer_characteristics": 16,
        "matrix_coefficients": 9,
        "full_range_flag": 1,
    }


def test_avif_nclx_probe_preserves_all_cicp_fields():
    encoded = b"nclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01\x00"
    assert avif_hdr._read_nclx(encoded) == {
        "color_primaries": 9,
        "transfer_characteristics": 16,
        "matrix_coefficients": 9,
        "full_range_flag": 1,
    }

    data = b"prefixcolrnclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01suffix"
    assert agentcli._scan_avif_nclx(data) == (9, 16, 9, 1)


def test_avif_parse_fallback_reports_complete_cicp_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(avif_hdr, "available", lambda: False)
    data = b"prefixcolrnclx" + struct.pack(">HHH", 9, 16, 9) + b"\x01suffix"
    path = tmp_path / "capture.avif"
    path.write_bytes(data)

    parsed = agentcli._parse_avif(str(path), data)

    assert parsed["color_primaries"] == 9
    assert parsed["transfer_characteristics"] == 16
    assert parsed["matrix_coefficients"] == 9
    assert parsed["is_hdr"] is True
