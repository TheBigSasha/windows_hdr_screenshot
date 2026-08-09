"""Small, serialisable data model for encoder capabilities."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, TypedDict

CodecStatus = Literal["available", "missing", "broken", "excluded"]
HdrRepresentation = Literal["gain_map", "pq", "linear", "sdr"]


class BundleManifest(TypedDict):
    """Validated capability contract shipped with a frozen application."""

    schema_version: Literal[1]
    architecture: str
    expected_profiles: list[str]


@dataclass(frozen=True)
class CodecCapability:
    """The complete runtime contract for one explicit output profile.

    ``available`` is deliberately redundant with ``status``.  It keeps the JSON
    contract convenient for agents while ``status`` distinguishes an optional
    package that is absent from one that is installed but cannot initialise.
    """

    profile: str
    available: bool
    status: CodecStatus
    reason: str | None
    hdr_representation: HdrRepresentation
    provider: str | None
    provider_version: str | None

    def to_dict(self) -> dict:
        return asdict(self)
