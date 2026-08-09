"""Frozen capability-manifest contract tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hdrshot.codecs import BundleContractError, bundle_manifest, capabilities


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "architecture": "x64",
        "expected_profiles": ["uhdr-jpeg", "png", "jpeg"],
    }


@pytest.fixture
def manifest_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("hdrshot.codecs.registry.platform.machine", lambda: "AMD64")
    path = tmp_path / "bundle-capabilities.json"
    monkeypatch.setenv("HDRSHOT_CAPABILITIES_MANIFEST", str(path))
    return path


def test_valid_manifest_is_typed_and_capabilities_use_it(manifest_env: Path) -> None:
    _write(manifest_env, _valid_manifest())

    manifest = bundle_manifest()

    assert manifest == {
        "schema_version": 1,
        "architecture": "x64",
        "expected_profiles": ["uhdr-jpeg", "png", "jpeg"],
    }
    assert set(capabilities()) >= {"uhdr-jpeg", "png", "jpeg"}


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda value: value.pop("schema_version"), id="missing-field"),
        pytest.param(lambda value: value.update(schema_version=2), id="wrong-schema"),
        pytest.param(lambda value: value.update(expected_profiles=[]), id="empty"),
        pytest.param(
            lambda value: value.update(expected_profiles=["uhdr-jpeg", "png", "png"]),
            id="duplicate",
        ),
        pytest.param(
            lambda value: value.update(expected_profiles=["uhdr-jpeg", "png", "jpeg", "nope"]),
            id="unknown",
        ),
        pytest.param(
            lambda value: value.update(expected_profiles=["uhdr-jpeg", "png"]),
            id="missing-required",
        ),
        pytest.param(lambda value: value.update(architecture="arm64"), id="wrong-architecture"),
    ],
)
def test_invalid_manifest_fails_closed(manifest_env: Path, mutate) -> None:
    value = _valid_manifest()
    mutate(value)
    _write(manifest_env, value)

    with pytest.raises(BundleContractError):
        capabilities()


def test_malformed_manifest_fails_closed(manifest_env: Path) -> None:
    manifest_env.write_text("{not-json", encoding="utf-8")

    with pytest.raises(BundleContractError):
        bundle_manifest()


def test_missing_manifest_fails_closed(manifest_env: Path) -> None:
    with pytest.raises(BundleContractError):
        bundle_manifest()


def test_root_and_embedded_manifests_must_agree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    embedded_dir = tmp_path / "hdrshot"
    embedded_dir.mkdir()
    root = tmp_path / "bundle-capabilities.json"
    embedded = embedded_dir / "bundle-capabilities.json"
    _write(root, _valid_manifest())
    changed = _valid_manifest()
    changed["expected_profiles"] = ["uhdr-jpeg", "png", "jpeg", "exr"]
    _write(embedded, changed)
    monkeypatch.setattr("hdrshot.codecs.registry.platform.machine", lambda: "AMD64")
    monkeypatch.setenv("HDRSHOT_CAPABILITIES_MANIFEST", str(embedded))

    with pytest.raises(BundleContractError):
        capabilities()


def test_source_mode_without_manifest_is_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HDRSHOT_CAPABILITIES_MANIFEST", raising=False)
    monkeypatch.setattr("hdrshot.codecs.registry.sys.frozen", False, raising=False)

    assert bundle_manifest() is None
