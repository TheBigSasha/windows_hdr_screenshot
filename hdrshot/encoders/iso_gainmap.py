r"""ISO 21496-1 gain-map metadata for UltraHDR JPEGs (issue #9).

Apple Photos / Preview / Quick Look render HDR gain maps from **ISO 21496-1**
metadata (they ignore Adobe's ``hdrgm`` XMP). This module serialises that binary
block and its APP2 wrapping so a single ``.jpg`` renders HDR on Apple *and* on
Chrome / Windows Photos / Android — the ISO block is additive to the existing MPF
+ ``hdrgm`` XMP path.

Byte layout verified against Google libultrahdr's ``gainmapmetadata.cpp`` /
``gmm.h`` (the de-facto interop target): all multi-byte ints big-endian;
``gainMapMin/Max`` and the base/alternate offsets are **signed** (s32), the gamma
numerator and every denominator are **unsigned** (u32); ``gainMapMin/Max`` and the
HDR-headroom fields are **log2** values (stops). We emit the full per-field
layout (``use_common_denominator`` clear) since our denominators differ.

Two APP2 segments are written (matching libultrahdr):
  * a **version-only stub** in the base image (lets strict readers detect ISO
    gain-map presence), placed before the MPF segment;
  * the **full metadata block** in the appended gain-map image, after its SOI.
"""
from __future__ import annotations

import struct
from fractions import Fraction

# libultrahdr's kIsoNameSpace + trailing NUL (28 bytes).
ISO_URN = b"urn:iso:std:iso:ts:21496:-1\x00"

APP2 = b"\xFF\xE2"

_MAX_DEN = 1 << 24   # keeps numerators well within int32 for our log2/offset range


def _fraction(value: float, signed: bool) -> tuple[int, int]:
    """Approximate ``value`` as ``(numerator, denominator)`` fitting the CICP int
    types. A spec-correct rational (decoders compute N/D); not required to be
    byte-identical to libultrahdr's continued-fraction output."""
    max_num = 0x7FFFFFFF if signed else 0xFFFFFFFF
    neg = signed and value < 0
    f = Fraction(abs(float(value))).limit_denominator(_MAX_DEN)
    num, den = f.numerator, (f.denominator or 1)
    num = min(num, max_num)
    den = min(den, 0xFFFFFFFF) or 1
    return (-num if neg else num), den


def build_metadata(meta: dict) -> bytes:
    """Serialise the ISO 21496-1 GainMapMetadata block (single luma channel)."""
    out = struct.pack(">HH", 0, 0)          # minimum_version, writer_version
    out += struct.pack(">B", 0x40)          # flags: useBaseColorSpace only

    bn, bd = _fraction(meta["cap_min_log2"], signed=False)   # base HDR headroom
    an, ad = _fraction(meta["cap_max_log2"], signed=False)   # alternate HDR headroom
    out += struct.pack(">II", bn, bd)
    out += struct.pack(">II", an, ad)

    # One channel (luma). Each numerator followed by its own denominator.
    mn, md = _fraction(meta["gain_min_log2"], signed=True)
    xn, xd = _fraction(meta["gain_max_log2"], signed=True)
    gn, gd = _fraction(meta["gamma"], signed=False)
    son, sod = _fraction(meta["offset_sdr"], signed=True)
    aon, aod = _fraction(meta["offset_hdr"], signed=True)
    out += struct.pack(">iI", mn, md)       # gainMapMin
    out += struct.pack(">iI", xn, xd)       # gainMapMax
    out += struct.pack(">II", gn, gd)       # gamma (numerator unsigned)
    out += struct.pack(">iI", son, sod)     # baseOffset
    out += struct.pack(">iI", aon, aod)     # alternateOffset
    return out


def _app2(payload: bytes) -> bytes:
    return APP2 + struct.pack(">H", 2 + len(ISO_URN) + len(payload)) + ISO_URN + payload


def version_stub_segment() -> bytes:
    """The base-image ISO APP2 (version-only payload)."""
    return _app2(struct.pack(">HH", 0, 0))


def full_segment(meta: dict) -> bytes:
    """The gain-map-image ISO APP2 (full metadata block)."""
    return _app2(build_metadata(meta))
