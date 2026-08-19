"""Orchestration: capture -> crop -> detect HDR -> choose format -> encode.

Platform-free: it imports the capture *backend* through the factory (lazily), so
this module — and the whole encode path, selftest and ``parse`` — import and run
on any OS. Only the functions that actually grab pixels need a backend.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np

from ..backends import get_backend
from ..codecs import CodecUnavailableError, capability
from ..codecs.model import CodecCapability, HdrRepresentation
from ..codecs.registry import capabilities_payload as _registry_capabilities_payload
from ..encoders import exr, sdr, ultrahdr
from . import color
from .types import DisplayInfo, MonitorCapture, virtual_desktop_bounds  # noqa: F401

log = logging.getLogger(__name__)

# Formats and their file extensions.
EXT = {
    "uhdr-jpeg": ".jpg", "uhdr-avif": ".avif", "uhdr-heic": ".heic",
    "pq-avif": ".avif", "pq-heic": ".heic", "exr": ".exr",
    "png": ".png", "jpeg": ".jpg", "avif-sdr": ".avif",
    # Legacy aliases are intentionally retained for persisted settings and
    # callers written before issue #31 defined the canonical profile IDs.
    "ultrahdr": ".jpg", "avif": ".avif", "heic": ".heic",
    "avif-hdr": ".avif",
}
HDR_FORMATS = {"uhdr-jpeg", "uhdr-avif", "uhdr-heic", "pq-avif", "pq-heic",
               "exr", "ultrahdr", "heic", "avif", "avif-hdr"}

# Issue #31 names the semantic profile, not just the file container.  The
# registry still exposes the pre-#31 IDs, so this module owns the compatibility
# translation until the registry can be migrated without changing its public
# capability payload.  In particular, ``avif`` remains single-rendition PQ and
# does not become a gain-map AVIF merely because a future provider is installed.
CANONICAL_PROFILES = (
    "uhdr-jpeg", "uhdr-avif", "uhdr-heic", "pq-avif", "pq-heic",
    "exr", "png", "jpeg", "avif-sdr",
)
LEGACY_ALIASES = {
    "ultrahdr": "uhdr-jpeg",
    "avif": "pq-avif",
    "heic": "pq-heic",
    "avif-hdr": "pq-avif",
}
PUBLIC_FORMATS = (
    "auto", *CANONICAL_PROFILES,
    # Keep old CLI/config values accepted and document their semantics above.
    "ultrahdr", "avif", "heic", "avif-hdr",
)
_REGISTRY_PROFILES = {profile: profile for profile in CANONICAL_PROFILES}
_LEGACY_PROFILE_NAMES = {
    "uhdr-jpeg": "ultrahdr",
    "pq-avif": "avif",
    "pq-heic": "heic",
}
_REPRESENTATION = {
    "uhdr-jpeg": "gain_map", "uhdr-avif": "gain_map", "uhdr-heic": "gain_map",
    "pq-avif": "pq", "pq-heic": "pq", "exr": "linear",
    "png": "sdr", "jpeg": "sdr", "avif-sdr": "sdr",
}
_CONTAINER = {
    "uhdr-jpeg": "jpeg", "uhdr-avif": "avif", "uhdr-heic": "heic",
    "pq-avif": "avif", "pq-heic": "heic", "exr": "exr",
    "png": "png", "jpeg": "jpeg", "avif-sdr": "avif",
}


class RegionError(ValueError):
    """A requested region does not intersect any captured display."""


OptionalDependencyError = CodecUnavailableError


class CodecEncodeError(RuntimeError):
    """A typed failure raised after a selected codec failed to encode.

    ``CodecUnavailableError`` covers capability discovery and selection.  This
    separate error preserves the selected profile/provider when the native or
    optional encoder fails during the actual write, allowing CLI/agent callers
    to handle both cases without parsing exception text.
    """

    status = "broken"

    def __init__(self, *, requested_profile: str, actual_profile: str,
                 provider: str | None, provider_version: str | None,
                 cause: Exception):
        self.requested_profile = requested_profile
        self.actual_profile = actual_profile
        self.provider = provider
        self.provider_version = provider_version
        self.reason = f"{type(cause).__name__}: {cause}"
        super().__init__(
            f"codec profile {actual_profile!r} provider {provider or 'unknown'!r} "
            f"failed: {self.reason}")


@dataclass(frozen=True)
class EncodeResult(Mapping[str, Any]):
    """Structured encode result with a dict-compatible read interface.

    New callers should use the typed fields.  ``Mapping`` behavior and the
    legacy ``format``/``hdr``/``profile`` keys are deliberate compatibility
    affordances for existing UI workers and automation integrations.
    """

    path: str
    requested_format: str
    requested_profile: str
    actual_profile: str
    legacy_profile: str
    container: str
    hdr_representation: str
    provider: str | None
    provider_version: str | None
    encoded_hdr: bool
    metadata_standard: str | None = None
    cicp: dict[str, int] | None = None
    gainmap_max_stops: float | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": self.path,
            # ``format`` is the historical requested-format key.  The explicit
            # spelling removes the old alias/profile ambiguity for new clients.
            "format": self.requested_format,
            "requested_format": self.requested_format,
            "profile": self.actual_profile,
            "requested_profile": self.requested_profile,
            "actual_profile": self.actual_profile,
            "legacy_profile": self.legacy_profile,
            "container": self.container,
            "hdr_representation": self.hdr_representation,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "hdr": self.encoded_hdr,
            "encoded_hdr": self.encoded_hdr,
        }
        if self.metadata_standard is not None:
            result["metadata_standard"] = self.metadata_standard
        if self.cicp is not None:
            result["cicp"] = dict(self.cicp)
        if self.gainmap_max_stops is not None:
            result["gainmap_max_stops"] = self.gainmap_max_stops
        if self.warnings:
            result["warnings"] = list(self.warnings)
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def canonical_profile(fmt: str, hdr_content: bool) -> str:
    """Return the issue #31 profile for a CLI/config format or legacy alias."""
    if fmt == "auto":
        return "uhdr-jpeg" if hdr_content else "png"
    profile = LEGACY_ALIASES.get(fmt, fmt)
    if profile not in CANONICAL_PROFILES:
        raise ValueError(
            f"unknown format {fmt!r}; valid formats: {', '.join(PUBLIC_FORMATS)}")
    return profile


def _registry_profile(profile: str) -> str:
    try:
        return _REGISTRY_PROFILES[profile]
    except KeyError:
        # These canonical issue #31 profiles describe future gain-map BMFF
        # providers; do not map them to the existing PQ encoders.
        raise KeyError(profile) from None


def _unavailable_capability(profile: str) -> CodecCapability:
    provider = {
        "uhdr-avif": "libultrahdr",
        "uhdr-heic": "libultrahdr",
    }.get(profile)
    return CodecCapability(
        profile=profile,
        available=False,
        status="excluded",
        reason=("canonical gain-map profile is not implemented by the current "
                "providers; issue #31 defines the future libultrahdr path"),
        hdr_representation=cast(HdrRepresentation, _REPRESENTATION[profile]),
        provider=provider,
        provider_version=None,
    )


def _capability(profile: str) -> CodecCapability:
    """Expose the registry capability under its canonical profile ID."""
    try:
        registry_cap = capability(_registry_profile(profile))
    except KeyError:
        return _unavailable_capability(profile)
    return CodecCapability(
        profile=profile,
        available=registry_cap.available,
        status=registry_cap.status,
        reason=registry_cap.reason,
        hdr_representation=cast(HdrRepresentation, _REPRESENTATION[profile]),
        provider=registry_cap.provider,
        provider_version=registry_cap.provider_version,
    )


def require_profile(profile: str) -> CodecCapability:
    """Require a canonical profile without allowing representation fallback."""
    cap = _capability(profile)
    if not cap.available:
        raise CodecUnavailableError(cap)
    return cap


def _provider_details(profile: str, cap: CodecCapability) -> tuple[str | None, str | None]:
    """Return provider identity/version from the encoder, not stale registry data."""
    modules = {
        "uhdr-jpeg": ultrahdr,
        "pq-avif": None,
        "pq-heic": None,
        "exr": exr,
        "png": sdr,
        "jpeg": sdr,
        "avif-sdr": sdr,
    }
    module = modules.get(profile)
    if profile == "pq-avif":
        from ..encoders import avif_hdr
        module = avif_hdr
    elif profile == "pq-heic":
        from ..encoders import heic
        module = heic
    if module is not None:
        details = getattr(module, "provider_details", None)
        if callable(details):
            try:
                details_result = details(profile)
                if (isinstance(details_result, tuple) and len(details_result) == 2
                        and isinstance(details_result[0], str)):
                    return details_result[0], details_result[1]
            except Exception as exc:  # pragma: no cover - defensive diagnostics
                log.debug("provider version lookup failed for %s: %s", profile, exc)
    return cap.provider, cap.provider_version


def capabilities_payload() -> dict[str, Any]:
    """Return the central canonical-profile capability payload."""
    raw = _registry_capabilities_payload()
    legacy_rows = {row["profile"]: row for row in raw.get("profiles", [])}
    profiles: list[dict[str, Any]] = []
    for profile in CANONICAL_PROFILES:
        row = dict(legacy_rows.get(profile, _capability(profile).to_dict()))
        row["profile"] = profile
        row["legacy_profile"] = _LEGACY_PROFILE_NAMES.get(profile)
        row["hdr_representation"] = _REPRESENTATION[profile]
        cap = _capability(profile)
        provider, version = _provider_details(profile, cap)
        row["provider"] = provider
        row["provider_version"] = version
        profiles.append(row)
    available = [row["profile"] for row in profiles if row.get("available")]
    return {
        "schema_version": 2,
        "architecture": raw.get("architecture"),
        "expected_profiles": raw.get("expected_profiles"),
        "profiles": profiles,
        "available_profiles": available,
        "unavailable_profiles": [row for row in profiles if not row.get("available")],
        "aliases": {**LEGACY_ALIASES, "auto": "uhdr-jpeg or png"},
    }


# --------------------------------------------------------------------------- #
# Save location
# --------------------------------------------------------------------------- #
def _pictures_dir() -> str:
    """The real Pictures folder (honouring OneDrive / Known Folder redirection on
    Windows), falling back to ``~/Pictures`` elsewhere or on failure."""
    if sys.platform == "win32":
        try:
            from ..backends.win32.knownfolders import pictures_path
            p = pictures_path()
            if p:
                return p
        except Exception as e:  # pragma: no cover - defensive
            log.debug("known-folder Pictures lookup failed: %s", e)
    return os.path.join(os.path.expanduser("~"), "Pictures")


def default_save_dir() -> str:
    d = os.path.join(_pictures_dir(), "Screenshots")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class CaptureResult:
    linear: np.ndarray          # cropped scRGB FP16, 1.0 == 80 nits
    sdr_white_nits: float
    display: DisplayInfo | None
    region_phys: tuple          # (x, y, w, h) within the source display buffer
    stats: dict = field(default_factory=dict)
    spans_displays: list[str] = field(default_factory=list)  # gdi names if stitched

    @property
    def is_hdr(self) -> bool:
        enabled = bool(self.display and self.display.hdr_enabled)
        return self.stats.get("has_hdr", False) and enabled

    @property
    def hdr_capable_content(self) -> bool:
        """True if the pixels themselves carry >SDR-white values, regardless of
        whether Windows HDR is currently toggled on."""
        return self.stats.get("has_hdr", False)


# --------------------------------------------------------------------------- #
# Capture helpers
# --------------------------------------------------------------------------- #
def _display_for(disps, gdi_name):
    for d in disps:
        if d.gdi_name == gdi_name:
            return d
    return None


def _acquire(caps, disps):
    """Fill in caps/disps from the platform backend if not supplied."""
    if caps is not None and disps is not None:
        return caps, disps
    backend = get_backend()
    backend.set_process_dpi_aware()
    if disps is None:
        disps = backend.enumerate_displays()
    if caps is None:
        caps = backend.capture_all()
    return caps, disps


def _intersect(ax, ay, aw, ah, bx, by, bw, bh):
    """Rectangle intersection in a common coordinate space, or None."""
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def capture_region(phys_rect: tuple[int, int, int, int],
                   caps: dict | None = None,
                   disps: list | None = None) -> CaptureResult:
    """Crop a virtual-desktop physical rectangle out of the captured buffers.

    If the rectangle spans multiple displays it is **stitched** across them into a
    single buffer in virtual-desktop coordinates (each monitor's absolute
    scRGB-linear pixels composite directly; the dominant monitor's SDR white is
    used as the tone-mapping reference). A rectangle that intersects no display
    raises :class:`RegionError` rather than silently returning another monitor.
    """
    caps, disps = _acquire(caps, disps)
    x, y, w, h = phys_rect
    if w <= 0 or h <= 0:
        raise RegionError(f"degenerate region {phys_rect!r}")

    # Which captured monitors does the rectangle touch, and by how much?
    hits = []
    for mc in caps.values():
        inter = _intersect(x, y, w, h, mc.x, mc.y, mc.width, mc.height)
        if inter:
            hits.append((inter[2] * inter[3], mc, inter))
    if not hits:
        avail = "; ".join(f"{mc.gdi_name} {mc.width}x{mc.height}+{mc.x}+{mc.y}"
                          for mc in caps.values())
        raise RegionError(
            f"region {w}x{h}+{x}+{y} intersects no captured display. Available: {avail}")

    hits.sort(key=lambda t: t[0], reverse=True)
    dominant = hits[0][1]
    disp = _display_for(disps, dominant.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0

    if len(hits) == 1:
        _, mc, (ix, iy, iw, ih) = hits[0]
        # .copy() (not ascontiguousarray): a full-width slice is already contiguous
        # and would alias — keeping the whole monitor buffer alive after release.
        crop = np.array(
            mc.linear[iy - mc.y:iy - mc.y + ih, ix - mc.x:ix - mc.x + iw],
            dtype=np.float32, order="C", copy=True,
        )
        return CaptureResult(linear=crop, sdr_white_nits=white, display=disp,
                             region_phys=(ix, iy, iw, ih),
                             stats=color.hdr_stats(crop, white))

    # Multi-monitor: composite each intersection into a virtual-space canvas.
    log.info("region spans %d displays; stitching", len(hits))
    canvas = np.zeros((h, w, 3), np.float32)
    for _, mc, (ix, iy, iw, ih) in hits:
        src = mc.linear[iy - mc.y:iy - mc.y + ih, ix - mc.x:ix - mc.x + iw, :3]
        canvas[iy - y:iy - y + ih, ix - x:ix - x + iw] = src
    return CaptureResult(linear=canvas, sdr_white_nits=white, display=disp,
                         region_phys=(x, y, w, h),
                         stats=color.hdr_stats(canvas, white),
                         spans_displays=[mc.gdi_name for _, mc, _ in hits])


def capture_buffer_region(caps: dict, disps: list, gdi_name: str,
                          buffer_rect: tuple | None) -> CaptureResult:
    """Crop directly from one output's captured buffer.

    ``buffer_rect`` is ``(x, y, w, h)`` in that buffer's own physical pixels, or
    ``None`` for the whole screen. Used by the selection overlay.
    """
    mc = caps.get(gdi_name)
    if mc is None:
        raise RegionError(f"no captured buffer for display {gdi_name!r} "
                          f"(have: {', '.join(caps) or 'none'})")
    disp = _display_for(disps, mc.gdi_name)
    white = disp.sdr_white_nits if disp else 80.0
    if buffer_rect is None:
        # Whole screen: a float32 platform buffer stays zero-copy; the native
        # Windows FP16 buffer is promoted once for analysis and encoding.
        crop = mc.linear.astype(np.float32, copy=False)
        rect = (0, 0, mc.width, mc.height)
    else:
        bx, by, bw, bh = buffer_rect
        x0, y0 = max(0, bx), max(0, by)
        x1, y1 = min(mc.width, bx + bw), min(mc.height, by + bh)
        if x1 <= x0 or y1 <= y0:
            raise RegionError(f"selection {buffer_rect!r} outside buffer {mc.width}x{mc.height}")
        crop = np.array(mc.linear[y0:y1, x0:x1], dtype=np.float32,
                        order="C", copy=True)   # independent, encode-ready copy
        rect = (x0, y0, x1 - x0, y1 - y0)
    return CaptureResult(linear=crop, sdr_white_nits=white, display=disp,
                         region_phys=rect, stats=color.hdr_stats(crop, white))


def capture_display(disp: DisplayInfo, caps: dict | None = None,
                    disps: list | None = None) -> CaptureResult:
    caps, disps = _acquire(caps, disps)
    mc = caps.get(disp.gdi_name)
    if mc is None:
        raise RegionError(f"no captured buffer for display {disp.gdi_name!r} "
                          f"(have: {', '.join(caps) or 'none'})")
    linear = mc.linear.astype(np.float32, copy=False)
    return CaptureResult(linear=linear, sdr_white_nits=disp.sdr_white_nits,
                         display=disp, region_phys=(0, 0, mc.width, mc.height),
                         stats=color.hdr_stats(linear, disp.sdr_white_nits))


# --------------------------------------------------------------------------- #
# Format choice + encode
# --------------------------------------------------------------------------- #
def choose_auto_format(result: CaptureResult) -> str:
    """Pick the best format automatically, macOS-style: gain-map JPEG for HDR,
    PNG for SDR."""
    if result.hdr_capable_content:
        return "uhdr-jpeg"
    return "png"


def resolve_profile(result: CaptureResult, fmt: str) -> str:
    """Resolve a public format id/legacy alias to one canonical profile."""
    return canonical_profile(fmt, result.hdr_capable_content)


def encode(result: CaptureResult, fmt: str, path: str, *,
           gainmap_quality: int | None = None,
           gainmap_downscale: int | None = None) -> EncodeResult:
    """Encode to ``path`` and return a typed, backward-compatible result."""
    requested_format = fmt
    profile = resolve_profile(result, fmt)
    cap = require_profile(profile)
    legacy_profile = _LEGACY_PROFILE_NAMES.get(profile, profile)
    provider, provider_version = _provider_details(profile, cap)
    lin = result.linear
    white = result.sdr_white_nits
    encoded_hdr = cap.hdr_representation != "sdr" and result.hdr_capable_content
    metadata_standard = None
    cicp = None
    gainmap_max_stops = None
    log.debug("encoding %s (%s) -> %s (%dx%d)", requested_format, profile, path,
              lin.shape[1], lin.shape[0])
    try:
        if profile == "exr":
            exr.write_exr(path, lin, white)
        elif profile == "uhdr-jpeg":
            meta = ultrahdr.write_ultrahdr(
                path, lin, white,
                quality=gainmap_quality if gainmap_quality else 90,
                gainmap_downscale=gainmap_downscale if gainmap_downscale else 1)
            gainmap_max_stops = round(meta["gain_max_log2"], 3)
            metadata_standard = "ISO 21496-1 + HDR Gain Map XMP"
        elif profile == "pq-heic":
            from ..encoders import heic
            result_meta = heic.write_heic_pq(path, lin)
            metadata_standard = "CICP/NCLX"
            cicp = result_meta.get("cicp") if result_meta else None
        elif profile == "png":
            sdr.write_png(path, color.scrgb_to_sdr_u8(lin, white))
        elif profile == "jpeg":
            sdr.write_jpeg(path, color.scrgb_to_sdr_u8(lin, white))
        elif profile == "pq-avif":
            from ..encoders import avif_hdr
            result_meta = avif_hdr.write_avif_pq(path, lin)
            metadata_standard = "CICP/NCLX"
            cicp = result_meta.get("cicp") if result_meta else None
        elif profile == "avif-sdr":
            sdr.write_avif_sdr(path, color.scrgb_to_sdr_u8(lin, white))
        else:
            # uhdr-avif and uhdr-heic intentionally have no implementation yet.
            raise CodecUnavailableError(_unavailable_capability(profile))
    except CodecUnavailableError:
        raise
    except Exception as exc:
        raise CodecEncodeError(
            requested_profile=profile, actual_profile=profile,
            provider=provider, provider_version=provider_version, cause=exc) from exc
    return EncodeResult(
        path=path,
        requested_format=requested_format,
        requested_profile=profile,
        actual_profile=profile,
        legacy_profile=legacy_profile,
        container=_CONTAINER[profile],
        hdr_representation=_REPRESENTATION[profile],
        provider=provider,
        provider_version=provider_version,
        encoded_hdr=encoded_hdr,
        metadata_standard=metadata_standard,
        cicp=cicp,
        gainmap_max_stops=gainmap_max_stops,
    )


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
def timestamped_name(fmt: str, hdr: bool, when=None) -> str:
    from datetime import datetime
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H%M%S")
    prefix = "HDR " if hdr else ""
    return f"{prefix}Screenshot {stamp}{EXT.get(fmt, EXT.get(LEGACY_ALIASES.get(fmt, fmt), '.png'))}"


TEMPLATE_TOKENS = {"date", "time", "display", "format", "hdr", "n"}


def validate_template(template: str) -> None:
    """Raise ValueError if a filename template uses an unknown ``{token}``."""
    import string
    for _, fieldname, _, _ in string.Formatter().parse(template):
        if fieldname is not None and fieldname not in TEMPLATE_TOKENS:
            raise ValueError(
                f"unknown token {{{fieldname}}} in filename template; "
                f"valid tokens: {', '.join('{' + t + '}' for t in sorted(TEMPLATE_TOKENS))}")


_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _sanitize(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    if out.split(".")[0].upper() in _RESERVED_NAMES:   # CON.jpg is still the CON device
        out = "_" + out
    return out


def render_filename(template: str, fmt: str, hdr: bool, *, display: str = "",
                    n: int = 1, when=None) -> str:
    """Render a filename from a template + tokens. ``{n}`` is blank for n<=1."""
    from datetime import datetime
    dt = when or datetime.now()
    import re
    body = template.format(
        date=dt.strftime("%Y-%m-%d"),
        time=dt.strftime("%H%M%S"),
        display=_sanitize(display or ""),
        format=fmt,
        hdr="HDR " if hdr else "",
        n="" if n <= 1 else f" ({n})",
    )
    body = re.sub(r"\s+", " ", body).strip()
    # Sanitize the whole rendered name: a template with path separators, reserved
    # characters or device names must stay a plain filename inside the save dir.
    body = _sanitize(body) or "Screenshot"
    return body + EXT.get(fmt, EXT.get(LEGACY_ALIASES.get(fmt, fmt), ".png"))


def _unique_path(out_dir: str, filename: str) -> str:
    """Avoid clobbering an existing file (timestamps have 1-second resolution)."""
    path = os.path.join(out_dir, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        cand = os.path.join(out_dir, f"{stem} ({n}){ext}")
        if not os.path.exists(cand):
            return cand
        n += 1


def save(result: CaptureResult, fmt: str = "auto", out_dir: str | None = None,
         template: str | None = None, *,
         gainmap_quality: int | None = None,
         gainmap_downscale: int | None = None) -> EncodeResult:
    profile = resolve_profile(result, fmt)
    cap = require_profile(profile)
    out_dir = out_dir or default_save_dir()
    os.makedirs(out_dir, exist_ok=True)
    hdr = cap.hdr_representation != "sdr" and result.hdr_capable_content
    # Use the canonical profile for the filename extension and `{format}` token;
    # the original CLI/config spelling remains in `requested_format`/`format`.
    path = _choose_path(out_dir, profile, hdr, result, template)
    info = encode(result, fmt, path,
                  gainmap_quality=gainmap_quality, gainmap_downscale=gainmap_downscale)
    log.info("saved %s (%s)", path, "HDR" if info.get("hdr") else "SDR")
    return info


def _choose_path(out_dir: str, fmt: str, hdr: bool, result: CaptureResult,
                 template: str | None) -> str:
    """Pick a non-clobbering output path, using a filename template if given."""
    if not template:
        return _unique_path(out_dir, timestamped_name(fmt, hdr))
    validate_template(template)
    display_name = result.display.friendly_name if result.display else ""
    # Detect a real {n} field (a literal "{{n}}" renders as constant "{n}" text and
    # must get the collision suffix, or this loop would never terminate).
    import string
    has_n = any(fieldname == "n"
                for _, fieldname, _, _ in string.Formatter().parse(template))
    n = 1
    while True:
        fname = render_filename(template, fmt, hdr, display=display_name, n=n)
        if not has_n and n > 1:                       # template lacks {n}; append a suffix
            stem, ext = os.path.splitext(fname)
            fname = f"{stem} ({n}){ext}"
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            return path
        n += 1
