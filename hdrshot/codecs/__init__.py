"""Runtime codec capabilities and strict profile selection."""

from .model import CodecCapability, CodecStatus
from .registry import (
    PROFILES,
    USER_FORMATS,
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
