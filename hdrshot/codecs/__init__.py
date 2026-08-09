"""Runtime codec capabilities and strict profile selection."""

from .model import BundleManifest, CodecCapability, CodecStatus
from .registry import (
    PROFILES,
    USER_FORMATS,
    BundleContractError,
    CodecUnavailableError,
    bundle_manifest,
    capabilities,
    capabilities_payload,
    capability,
    profile_for_format,
    require,
)

__all__ = [
    "CodecCapability",
    "CodecStatus",
    "BundleManifest",
    "BundleContractError",
    "CodecUnavailableError",
    "PROFILES",
    "USER_FORMATS",
    "bundle_manifest",
    "capabilities",
    "capabilities_payload",
    "capability",
    "profile_for_format",
    "require",
]
