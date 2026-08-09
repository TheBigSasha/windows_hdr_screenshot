"""Deterministic codec discovery and strict output-profile policy.

The application has two kinds of optional dependency state: a package may not be
installed, or it may be installed but fail to load because of a DLL, ABI, or
architecture problem.  Keeping those states separate prevents a broken native
codec from becoming a green skipped test and lets the UI explain the real cause.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from .model import BundleManifest, CodecCapability, HdrRepresentation

log = logging.getLogger(__name__)

# Canonical profile IDs encode both the container and HDR representation.
# Legacy names remain accepted through the compatibility table but are never
# emitted in the capability payload or frozen manifest.
PROFILES = (
    "uhdr-jpeg", "uhdr-avif", "uhdr-heic",
    "pq-avif", "pq-heic", "exr", "png", "jpeg", "avif-sdr",
)
LEGACY_ALIASES = {
    "ultrahdr": "uhdr-jpeg",
    "avif": "pq-avif",
    "heic": "pq-heic",
    "avif-hdr": "pq-avif",
}
USER_FORMATS = ("auto", *PROFILES, *LEGACY_ALIASES)
_MANIFEST_SCHEMA_VERSION = 1
_MANIFEST_KEYS = frozenset({"schema_version", "architecture", "expected_profiles"})
_REQUIRED_BUNDLE_PROFILES = frozenset({"uhdr-jpeg", "png", "jpeg"})

# Release manifests use the short Windows names, while platform.machine() uses
# the names reported by the Python runtime.  These aliases are equivalent for
# contract purposes; unknown architecture strings are not.
_ARCHITECTURE_ALIASES = {
    "x64": "x64",
    "amd64": "x64",
    "x86_64": "x64",
    "8664": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "aa64": "arm64",
}

_PROFILE_INFO: dict[str, tuple[HdrRepresentation, str | None, str | None]] = {
    "uhdr-jpeg": ("gain_map", "hdrshot", None),
    "uhdr-avif": ("gain_map", "libultrahdr", None),
    "uhdr-heic": ("gain_map", "libultrahdr", None),
    "pq-avif": ("pq", "imagecodecs/libavif", "imagecodecs"),
    "pq-heic": ("pq", "pillow-heif", "pillow-heif"),
    "exr": ("linear", "OpenEXR", "OpenEXR"),
    "png": ("sdr", "Pillow", "Pillow"),
    "jpeg": ("sdr", "Pillow", "Pillow"),
    "avif-sdr": ("sdr", "pillow-avif-plugin", "pillow-avif-plugin"),
}

_OPTIONAL_MODULES = {
    "exr": "OpenEXR",
    "pq-heic": "pillow_heif",
    "avif-sdr": "pillow_avif",
    "pq-avif": "imagecodecs",
}


class CodecUnavailableError(RuntimeError):
    """Raised when an explicitly requested profile cannot be encoded."""

    def __init__(self, capability: CodecCapability):
        self.capability = capability
        reason = capability.reason or f"status={capability.status}"
        super().__init__(f"codec profile {capability.profile!r} is "
                         f"{capability.status}: {reason}")


class BundleContractError(RuntimeError):
    """Raised when a frozen bundle's capability contract is invalid.

    A frozen executable must never infer its supported codecs from ambient
    imports.  Keeping this failure typed lets the CLI and callers report a
    machine-readable contract failure without confusing it with a missing
    optional codec.
    """

    def __init__(self, path: Path | str, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"invalid frozen codec capability manifest {self.path}: {reason}")


def _distribution_version(distribution: str | None, module=None) -> str | None:
    if module is not None:
        version = getattr(module, "__version__", None)
        if version:
            return str(version)
    if distribution:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            pass
        except Exception as exc:  # pragma: no cover - defensive metadata path
            log.debug("version lookup failed for %s: %s", distribution, exc)
    return None


def _builtin(profile: str, provider: str) -> CodecCapability:
    representation, _, _ = _PROFILE_INFO[profile]
    version = None
    if profile in {"png", "jpeg"}:
        try:
            pillow = importlib.import_module("PIL")
            version = _distribution_version("Pillow", pillow)
        except Exception:  # pragma: no cover - Pillow is a required dependency
            version = _distribution_version("Pillow")
    else:
        from .. import __version__
        version = __version__
    return CodecCapability(profile, True, "available", None, representation,
                           provider, version)


def _optional(profile: str, module_name: str) -> CodecCapability:
    profile = LEGACY_ALIASES.get(profile, profile)
    representation, provider, distribution = _PROFILE_INFO[profile]
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception as exc:
        return CodecCapability(profile, False, "broken", f"discovery failed: {exc}",
                               representation, provider, None)
    if spec is None:
        extra = {"avif-sdr": "avif-sdr", "pq-avif": "avif-hdr",
                 "pq-heic": "heic"}.get(profile, profile)
        return CodecCapability(profile, False, "missing",
                               f"install the optional [{extra}] extra",
                               representation, provider, None)
    try:
        module = importlib.import_module(module_name)
        if profile == "pq-avif":
            avif = getattr(module, "AVIF", None)
            if not bool(getattr(avif, "available", False)):
                raise RuntimeError("imagecodecs AVIF codec is unavailable")
            encoder = getattr(module, "avif_encode", None)
            if not callable(encoder):
                raise RuntimeError("imagecodecs has no avif_encode function")
            try:
                params = inspect.signature(encoder).parameters
            except (TypeError, ValueError):
                params = None
            if params is not None and "primaries" not in params:
                raise RuntimeError("imagecodecs AVIF encoder lacks nclx profile arguments")
        return CodecCapability(profile, True, "available", None, representation,
                               provider, _distribution_version(distribution, module))
    except Exception as exc:
        return CodecCapability(profile, False, "broken",
                               f"{module_name} failed to load: {type(exc).__name__}: {exc}",
                               representation, provider,
                               _distribution_version(distribution))


def _manifest_path() -> Path | None:
    explicit = os.environ.get("HDRSHOT_CAPABILITIES_MANIFEST")
    if explicit:
        return Path(explicit)
    if not getattr(sys, "frozen", False):
        return None
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return root / "hdrshot" / "bundle-capabilities.json"


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key {key!r}")
        value[key] = item
    return value


def _architecture_key(value: object, *, path: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BundleContractError(path, f"{field} must be a nonempty string")
    key = _ARCHITECTURE_ALIASES.get(value.strip().casefold())
    if key is None:
        raise BundleContractError(path, f"unsupported {field} {value!r}")
    return key


def _validate_manifest(value: object, path: Path) -> BundleManifest:
    if not isinstance(value, dict):
        raise BundleContractError(path, "manifest root must be a JSON object")
    if set(value) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(value))
        extra = sorted(set(value) - _MANIFEST_KEYS)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if extra:
            details.append(f"unknown fields: {', '.join(extra)}")
        raise BundleContractError(path, "; ".join(details))

    schema_version = value["schema_version"]
    if type(schema_version) is not int or schema_version != _MANIFEST_SCHEMA_VERSION:
        raise BundleContractError(
            path,
            f"schema_version must be exactly {_MANIFEST_SCHEMA_VERSION}",
        )

    architecture = _architecture_key(value["architecture"], path=path, field="architecture")
    runtime_name = platform.machine()
    runtime_architecture = _architecture_key(
        runtime_name, path=path, field="runtime architecture"
    )
    if architecture != runtime_architecture:
        raise BundleContractError(
            path,
            f"architecture {value['architecture']!r} does not match "
            f"runtime architecture {runtime_name!r}",
        )

    profiles = value["expected_profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise BundleContractError(path, "expected_profiles must be a nonempty list")
    if any(not isinstance(profile, str) or not profile for profile in profiles):
        raise BundleContractError(
            path, "expected_profiles must contain nonempty profile strings"
        )
    if len(set(profiles)) != len(profiles):
        raise BundleContractError(path, "expected_profiles must not contain duplicates")
    unknown = sorted(set(profiles) - set(PROFILES))
    if unknown:
        raise BundleContractError(
            path, f"expected_profiles contains unknown profiles: {', '.join(unknown)}"
        )
    missing_required = sorted(_REQUIRED_BUNDLE_PROFILES - set(profiles))
    if missing_required:
        raise BundleContractError(
            path,
            "expected_profiles must include: " + ", ".join(missing_required),
        )

    # Return a fresh, canonical object and do not expose arbitrary JSON fields
    # to callers after validation.
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "architecture": architecture,
        "expected_profiles": list(profiles),
    }


def _read_manifest(path: Path) -> BundleManifest:
    try:
        present = path.is_file()
    except OSError as exc:
        raise BundleContractError(path, f"manifest cannot be accessed: {exc}") from exc
    if not present:
        raise BundleContractError(path, "manifest file is missing")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except BundleContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BundleContractError(path, f"malformed JSON: {exc}") from exc
    return _validate_manifest(value, path)


def _companion_manifest_path(path: Path) -> Path | None:
    """Return the other copy when a bundle has root and embedded manifests."""
    if path.name.casefold() != "bundle-capabilities.json":
        return None
    if path.parent.name.casefold() == "hdrshot":
        return path.parent.parent / path.name
    return path.parent / "hdrshot" / path.name


def _manifest_signature(manifest: BundleManifest) -> tuple[int, str, tuple[str, ...]]:
    return (
        manifest["schema_version"],
        manifest["architecture"],
        tuple(sorted(manifest["expected_profiles"])),
    )


def bundle_manifest() -> BundleManifest | None:
    """Read and validate the frozen-bundle capability contract.

    Source/development runs have no manifest and continue to discover codecs
    normally.  An explicitly configured manifest and every frozen run are
    strict modes: missing or invalid contracts raise ``BundleContractError``.
    """
    path = _manifest_path()
    if path is None:
        return None

    manifest = _read_manifest(path)
    companion = _companion_manifest_path(path)
    if companion is not None and companion != path:
        try:
            companion_present = companion.is_file()
        except OSError as exc:
            raise BundleContractError(
                companion, f"manifest cannot be accessed: {exc}"
            ) from exc
        if not companion_present:
            return manifest
        embedded = _read_manifest(companion)
        if _manifest_signature(manifest) != _manifest_signature(embedded):
            raise BundleContractError(
                path,
                f"root and embedded manifests disagree ({companion})",
            )
    return manifest


def capabilities() -> dict[str, CodecCapability]:
    """Probe every profile and return a stable profile-keyed registry."""
    found: dict[str, CodecCapability] = {
        "uhdr-jpeg": _builtin("uhdr-jpeg", "hdrshot UltraHDR encoder"),
        "png": _builtin("png", "Pillow"),
        "jpeg": _builtin("jpeg", "Pillow"),
    }
    for profile in ("uhdr-avif", "uhdr-heic"):
        found[profile] = CodecCapability(
            profile, False, "excluded",
            "canonical gain-map profile requires the future libultrahdr provider",
            _PROFILE_INFO[profile][0], _PROFILE_INFO[profile][1], None,
        )
    for profile, module_name in _OPTIONAL_MODULES.items():
        found[profile] = _optional(profile, module_name)

    manifest = bundle_manifest()
    if manifest is not None:
        expected = set(manifest.get("expected_profiles", []))
        for profile, cap in list(found.items()):
            if profile not in expected:
                found[profile] = CodecCapability(
                    profile, False, "excluded",
                    "not included in this frozen bundle; install the source extra instead",
                    cap.hdr_representation, cap.provider, cap.provider_version)
    return {profile: found[profile] for profile in PROFILES}


def capability(profile: str) -> CodecCapability:
    profile = LEGACY_ALIASES.get(profile, profile)
    try:
        return capabilities()[profile]
    except KeyError as exc:
        raise ValueError(f"unknown codec profile {profile!r}; valid profiles: {', '.join(PROFILES)}") from exc


def require(profile: str) -> CodecCapability:
    cap = capability(profile)
    if not cap.available:
        raise CodecUnavailableError(cap)
    return cap


def profile_for_format(fmt: str, hdr_content: bool) -> str:
    """Resolve a user format id to one explicit encoder profile."""
    if fmt == "auto":
        return "uhdr-jpeg" if hdr_content else "png"
    fmt = LEGACY_ALIASES.get(fmt, fmt)
    if fmt in PROFILES:
        return fmt
    raise ValueError(f"unknown format {fmt!r}; valid formats: {', '.join(USER_FORMATS)}")


def capabilities_payload() -> dict:
    manifest = bundle_manifest()
    caps = capabilities()
    return {
        "schema_version": 2,
        "architecture": (manifest or {}).get("architecture") or platform.machine(),
        "expected_profiles": (manifest or {}).get("expected_profiles"),
        "profiles": [caps[p].to_dict() for p in PROFILES],
        "available_profiles": [p for p in PROFILES if caps[p].available],
        "unavailable_profiles": [caps[p].to_dict() for p in PROFILES if not caps[p].available],
        "aliases": dict(LEGACY_ALIASES),
    }
