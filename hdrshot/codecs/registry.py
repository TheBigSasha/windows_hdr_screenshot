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

from .model import CodecCapability, HdrRepresentation

log = logging.getLogger(__name__)

# These are user-facing format ids. ``avif`` is retained as a compatibility alias:
# it resolves to the explicit SDR or PQ profile based on the scene, but never
# silently changes profile when the selected encoder is unavailable.
USER_FORMATS = ("auto", "ultrahdr", "exr", "heic", "png", "jpeg",
                "avif", "avif-sdr", "avif-hdr")
PROFILES = ("ultrahdr", "exr", "heic", "png", "jpeg", "avif-sdr", "avif-hdr")

_PROFILE_INFO: dict[str, tuple[HdrRepresentation, str | None, str | None]] = {
    "ultrahdr": ("gain_map", "hdrshot", None),
    "exr": ("linear", "OpenEXR", "OpenEXR"),
    "heic": ("pq", "pillow-heif", "pillow-heif"),
    "png": ("sdr", "Pillow", "Pillow"),
    "jpeg": ("sdr", "Pillow", "Pillow"),
    "avif-sdr": ("sdr", "pillow-avif-plugin", "pillow-avif-plugin"),
    "avif-hdr": ("pq", "imagecodecs/libavif", "imagecodecs"),
}

_OPTIONAL_MODULES = {
    "exr": "OpenEXR",
    "heic": "pillow_heif",
    "avif-sdr": "pillow_avif",
    "avif-hdr": "imagecodecs",
}


class CodecUnavailableError(RuntimeError):
    """Raised when an explicitly requested profile cannot be encoded."""

    def __init__(self, capability: CodecCapability):
        self.capability = capability
        reason = capability.reason or f"status={capability.status}"
        super().__init__(f"codec profile {capability.profile!r} is "
                         f"{capability.status}: {reason}")


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
    from .. import __version__
    return CodecCapability(profile, True, "available", None, representation,
                           provider, __version__)


def _optional(profile: str, module_name: str) -> CodecCapability:
    representation, provider, distribution = _PROFILE_INFO[profile]
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception as exc:
        return CodecCapability(profile, False, "broken", f"discovery failed: {exc}",
                               representation, provider, None)
    if spec is None:
        extra = {"avif-sdr": "avif-sdr", "avif-hdr": "avif-hdr"}.get(profile, profile)
        return CodecCapability(profile, False, "missing",
                               f"install the optional [{extra}] extra",
                               representation, provider, None)
    try:
        module = importlib.import_module(module_name)
        if profile == "avif-hdr":
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


def bundle_manifest() -> dict | None:
    """Read the frozen-bundle capability contract, if one is present."""
    path = _manifest_path()
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        expected = value.get("expected_profiles")
        if (not isinstance(value, dict) or not isinstance(expected, list)
                or any(p not in PROFILES for p in expected)):
            raise ValueError("expected_profiles must list known codec profiles")
        return value
    except Exception as exc:
        log.error("invalid codec capability manifest %s: %s", path, exc)
        return {"schema_version": 1, "architecture": platform.machine(),
                "expected_profiles": [], "manifest_error": str(exc)}


def capabilities() -> dict[str, CodecCapability]:
    """Probe every profile and return a stable profile-keyed registry."""
    found: dict[str, CodecCapability] = {
        "ultrahdr": _builtin("ultrahdr", "hdrshot UltraHDR encoder"),
        "png": _builtin("png", "Pillow"),
        "jpeg": _builtin("jpeg", "Pillow"),
    }
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
        return "ultrahdr" if hdr_content else "png"
    if fmt == "avif":
        return "avif-hdr" if hdr_content else "avif-sdr"
    if fmt in PROFILES:
        return fmt
    raise ValueError(f"unknown format {fmt!r}; valid formats: {', '.join(USER_FORMATS)}")


def capabilities_payload() -> dict:
    manifest = bundle_manifest()
    caps = capabilities()
    return {
        "schema_version": 1,
        "architecture": (manifest or {}).get("architecture") or platform.machine(),
        "expected_profiles": (manifest or {}).get("expected_profiles"),
        "profiles": [caps[p].to_dict() for p in PROFILES],
        "available_profiles": [p for p in PROFILES if caps[p].available],
        "unavailable_profiles": [caps[p].to_dict() for p in PROFILES if not caps[p].available],
    }
