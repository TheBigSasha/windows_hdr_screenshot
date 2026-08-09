r"""Machine-readable CLI for agents and automation (issue #7).

Three problems this solves for a non-human caller:

1. **Structured results.** ``info``/``full``/``region``/``capture`` emit JSON with
   the saved path, format, dimensions, source display, HDR flags, peak nits, peak
   ratio, HDR-pixel fraction and gain-map headroom — no screen-scraping.

2. **Reading HDR back.** ``hdrshot parse <file>`` inspects an UltraHDR / EXR /
   HEIC / AVIF / PNG / JPEG file and reports whether it is HDR and *how* bright,
   with an optional tonemapped SDR preview PNG an agent can actually view.

3. **A one-shot.** ``hdrshot capture`` grabs, saves the true-HDR file, writes an
   agent-viewable SDR preview, and prints JSON in a single call.

IMPORTANT for agents: a tonemapped SDR preview **understates** HDR — blown
highlights look flat white. The ``peak_nits`` / ``peak_ratio`` / gain-map
``*_stops`` fields (and the true-HDR file itself) are the source of truth for how
bright the capture really is. See the ``notes`` field in every payload.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Any

import numpy as np

from .core import color, pipeline

log = logging.getLogger(__name__)

PREVIEW_NOTE = ("The SDR preview understates HDR: highlights above SDR white are "
                "clipped. Use peak_nits / peak_ratio / gainmap_max_stops (and the "
                "true-HDR file) as the source of truth for luminance.")


def _finite(obj):
    """Replace non-finite floats with None so the output is strict JSON (json.dumps
    would otherwise emit bare ``Infinity``/``NaN`` tokens no JSON parser accepts)."""
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _dumps(obj: dict) -> str:
    return json.dumps(_finite(obj), indent=2, sort_keys=False)


def _file_error(path: str, exc: Exception) -> dict:
    error = {
        "path": os.path.abspath(path),
        "format": None,
        "is_hdr": None,
        "error": f"{type(exc).__name__}: {exc}",
        "notes": PREVIEW_NOTE,
    }
    cap = getattr(exc, "capability", None)
    if cap is not None:
        error.update({
            "status": cap.status,
            "reason": cap.reason,
            "provider": cap.provider,
            "provider_version": cap.provider_version,
            "requested_profile": cap.profile,
        })
    elif isinstance(exc, pipeline.CodecEncodeError):
        error.update({
            "status": exc.status,
            "reason": exc.reason,
            "provider": exc.provider,
            "provider_version": exc.provider_version,
            "requested_profile": exc.requested_profile,
            "actual_profile": exc.actual_profile,
        })
    else:
        error.update({"status": None, "reason": None, "provider": None,
                      "provider_version": None})
    return error


# --------------------------------------------------------------------------- #
# info / capture serialisation
# --------------------------------------------------------------------------- #
def _display_dict(d) -> dict:
    return {
        "index": d.index,
        "gdi_name": d.gdi_name,
        "friendly_name": d.friendly_name,
        "x": d.x, "y": d.y, "width": d.width, "height": d.height,
        "is_primary": d.is_primary,
        "hdr_supported": d.hdr_supported,
        "hdr_enabled": d.hdr_enabled,
        "bits_per_color": d.bits_per_color,
        "sdr_white_nits": round(d.sdr_white_nits, 1),
        "color_encoding": d.color_encoding,
        "rotation": d.rotation,
        "state": d.state_label,
    }


def displays_to_json(disps, vb) -> str:
    return _dumps({
        "virtual_desktop": {"x": vb[0], "y": vb[1], "width": vb[2], "height": vb[3]},
        "display_count": len(disps),
        "any_hdr_enabled": any(d.hdr_enabled for d in disps),
        "displays": [_display_dict(d) for d in disps],
    })


def capture_to_dict(display, result: pipeline.CaptureResult, info: Mapping[str, Any],
                    preview_path: str | None = None) -> dict:
    st = result.stats
    h, w = result.linear.shape[:2]
    out = {
        "path": info.get("path"),
        "format": info.get("format"),
        "requested_format": info.get("requested_format", info.get("format")),
        "width": int(w),
        "height": int(h),
        "encoded_hdr": bool(info.get("hdr")),
        "requested_profile": info.get("requested_profile"),
        "actual_profile": info.get("actual_profile"),
        "legacy_profile": info.get("legacy_profile"),
        "container": info.get("container"),
        "hdr_representation": info.get("hdr_representation"),
        "metadata_standard": info.get("metadata_standard"),
        "cicp": info.get("cicp"),
        "provider": info.get("provider"),
        "provider_version": info.get("provider_version"),
        "hdr_content": bool(result.hdr_capable_content),
        "is_hdr_live": bool(result.is_hdr),
        "peak_nits": round(float(st.get("peak_nits", 0.0)), 1),
        "peak_ratio": round(float(st.get("peak_ratio", 0.0)), 4),
        "hdr_pixel_fraction": round(float(st.get("hdr_pixel_fraction", 0.0)), 6),
        "sdr_white_nits": round(float(result.sdr_white_nits), 1),
    }
    if "gainmap_max_stops" in info:
        out["gainmap_max_stops"] = info["gainmap_max_stops"]
    if getattr(result, "spans_displays", None):
        out["spans_displays"] = result.spans_displays
    if display is not None:
        out["display"] = {
            "gdi_name": display.gdi_name,
            "friendly_name": display.friendly_name,
            "hdr_enabled": display.hdr_enabled,
            "sdr_white_nits": round(display.sdr_white_nits, 1),
        }
    if preview_path:
        out["preview_png"] = preview_path
    return out


def captures_to_json(results) -> str:
    """``results`` is a list of ``(display, CaptureResult, info)`` (+ optional preview)."""
    caps = []
    for row in results:
        display, result, info = row[0], row[1], row[2]
        preview = row[3] if len(row) > 3 else None
        caps.append(capture_to_dict(display, result, info, preview))
    return _dumps({"captures": caps, "capabilities": pipeline.capabilities_payload(),
                   "notes": PREVIEW_NOTE})


# --------------------------------------------------------------------------- #
# Preview generation
# --------------------------------------------------------------------------- #
def write_preview_from_linear(linear: np.ndarray, sdr_white_nits: float, out_png: str) -> str:
    from PIL import Image
    u8 = color.scrgb_to_preview_u8(linear, sdr_white_nits)
    Image.fromarray(u8, "RGB").save(out_png, format="PNG")
    return out_png


# --------------------------------------------------------------------------- #
# File parsing
# --------------------------------------------------------------------------- #
_TC_NAMES = {16: "PQ (SMPTE ST 2084)", 18: "HLG", 1: "BT.709", 13: "sRGB", 8: "linear"}
_CP_NAMES = {9: "BT.2020", 1: "BT.709", 12: "Display P3"}


def _name(table: dict, key) -> str | None:
    """None-safe nclx code -> human name."""
    if key is None:
        return None
    return table.get(key, str(key))


def _parse_ultrahdr(path: str, data: bytes) -> dict:
    def _num(key):
        m = re.search(rf'hdrgm:{key}="([-0-9.]+)"'.encode(), data)
        return float(m.group(1)) if m else None

    gmin = _num("GainMapMin")
    gmax = _num("GainMapMax")
    gamma = _num("Gamma")
    has_mpf = b"MPF\x00" in data
    has_hdrgm = b"hdrgm:GainMapMax" in data or b"hdr-gain-map" in data
    has_iso = (b"urn:iso:std:iso:ts:21496" in data or b"urn:iso:std:iso:21496" in data
               or b"ISO 21496-1" in data)
    is_hdr = has_hdrgm or has_iso
    out = {
        "format": "uhdr-jpeg",
        "is_hdr": bool(is_hdr),
        "container": {"mpf": has_mpf, "hdrgm_xmp": has_hdrgm, "iso_21496_1": has_iso},
    }
    if gmax is not None:
        out["gainmap_min_stops"] = round(gmin, 4) if gmin is not None else None
        out["gainmap_max_stops"] = round(gmax, 4)
        out["gamma"] = gamma
        out["peak_ratio_over_sdr_white"] = round(float(2.0 ** gmax), 4)
    out["apple_compatible"] = bool(has_iso)
    return out


def _parse_exr(path: str) -> dict:
    import OpenEXR  # pyright: ignore[reportMissingImports]
    f = OpenEXR.File(path)
    part = f.parts[0]
    chans = list(part.channels.keys())
    # The reader may group planar channels: "RGB"/"RGBA" (HxWx3/4), or leave
    # separate scalar R/G/B(/A) channels. Handle all three layouts.
    if "RGB" in part.channels:
        rgb = np.asarray(part.channels["RGB"].pixels)
    elif "RGBA" in part.channels:
        rgb = np.asarray(part.channels["RGBA"].pixels)[..., :3]
    else:
        planes = [part.channels[c].pixels for c in ("R", "G", "B") if c in part.channels]
        if not planes:
            raise ValueError(f"EXR has no R/G/B color channels (found: {', '.join(chans)})")
        rgb = np.stack(planes, axis=-1)
    finite = np.asarray(rgb, np.float32)
    finite = finite[np.isfinite(finite)]
    mx = float(np.max(finite)) if finite.size else 0.0
    peak_nits = mx * color.SCRGB_REFERENCE_NITS
    return {
        "format": "exr",
        "is_hdr": bool(mx > 1.0),
        "channels": chans,
        "max_linear": round(mx, 4),
        "peak_nits": round(peak_nits, 1),
        "headroom_stops": round(float(np.log2(mx)), 4) if mx > 1.0 else 0.0,
        "color_space": "scRGB linear (BT.709), 1.0 = 80 nits",
    }


def _parse_heif(path: str, fmt: str) -> dict:
    import pillow_heif  # pyright: ignore[reportMissingImports]
    im = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)[0]
    n = im.info.get("nclx_profile", {}) or {}
    bit_depth = im.info.get("bit_depth")
    tc = n.get("transfer_characteristics")
    cp = n.get("color_primaries")
    return {
        "format": fmt,
        "is_hdr": bool(tc == 16 or (bit_depth and bit_depth >= 10)),
        "bit_depth": bit_depth,
        "transfer_characteristics": tc,
        "transfer_name": _name(_TC_NAMES, tc),
        "color_primaries": cp,
        "primaries_name": _name(_CP_NAMES, cp),
        "matrix_coefficients": n.get("matrix_coefficients"),
        "full_range_flag": n.get("full_range_flag"),
        "width": im.size[0], "height": im.size[1],
    }


def _parse_generic_sdr(path: str, fmt: str) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        mode = im.mode
    return {"format": fmt, "is_hdr": False, "width": w, "height": h, "mode": mode,
            "note": "standard SDR image (no HDR metadata)"}


def _detect_format(path: str, head: bytes) -> str:
    """``head`` should be the WHOLE file when available: real-world UltraHDR JPEGs
    can carry EXIF/ICC segments before the MPF/XMP markers, well past any fixed
    prefix."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "uhdr-jpeg" if b"MPF\x00" in head or b"hdr-gain-map" in head else "jpeg"
    if ext == ".exr" or head[:4] == b"\x76\x2f\x31\x01":
        return "exr"
    if ext in (".heic", ".heif"):
        return "pq-heic"
    if ext == ".avif":
        nclx = _scan_avif_nclx(head)
        return "pq-avif" if nclx is not None and nclx[1] == 16 else "avif-sdr"
    if ext == ".png":
        return "png"
    # Fall back to magic sniffing.
    if b"ftypheic" in head or b"ftypmif1" in head:
        return "pq-heic"
    if b"ftypavif" in head:
        nclx = _scan_avif_nclx(head)
        return "pq-avif" if nclx is not None and nclx[1] == 16 else "avif-sdr"
    return "jpeg"


def parse_file(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as fp:
        data = fp.read()
    fmt = _detect_format(path, data)
    result: dict
    try:
        if fmt == "uhdr-jpeg":
            result = _parse_ultrahdr(path, data)
        elif fmt == "exr":
            result = _parse_exr(path)
        elif fmt == "pq-heic":
            result = _parse_heif(path, "pq-heic")
        elif fmt in ("pq-avif", "avif-sdr"):
            result = _parse_avif(path, data)
        else:
            result = _parse_generic_sdr(path, fmt)
    except ImportError as e:
        result = {"format": fmt, "is_hdr": None,
                  "error": f"cannot parse {fmt}: optional dependency missing ({e})"}
    except Exception as e:
        # Corrupt/truncated/hostile file: an undetermined verdict, never a traceback.
        result = {"format": fmt, "is_hdr": None,
                  "error": f"unreadable {fmt}: {type(e).__name__}: {e}"}
    result["path"] = os.path.abspath(path)
    result["size_bytes"] = len(data)
    result["notes"] = PREVIEW_NOTE
    return result


def _parse_avif(path: str, data: bytes) -> dict:
    # Prefer imagecodecs (exposes the real bit depth / nclx); fall back to Pillow.
    try:
        from .encoders import avif_hdr
        if avif_hdr.available():
            meta = avif_hdr.probe(path)
            if meta:
                tc = meta.get("transfer_characteristics")
                cp = meta.get("color_primaries")
                return {
                    "format": "pq-avif" if tc == 16 or (meta.get("bit_depth", 8) >= 10) else "avif-sdr",
                    "is_hdr": bool(tc == 16 or (meta.get("bit_depth", 8) >= 10)),
                    "bit_depth": meta.get("bit_depth"),
                    "transfer_characteristics": tc,
                    "transfer_name": _name(_TC_NAMES, tc),
                    "color_primaries": cp,
                    "primaries_name": _name(_CP_NAMES, cp),
                    "matrix_coefficients": meta.get("matrix_coefficients"),
                    "full_range_flag": meta.get("full_range_flag"),
                    "width": meta.get("width"), "height": meta.get("height"),
                }
    except Exception as e:  # pragma: no cover - best-effort probe
        log.debug("imagecodecs AVIF probe failed: %s", e)
    # No imagecodecs: read the nclx colr box straight from the container so a
    # 10-bit PQ HDR AVIF is not misreported as SDR on a base install.
    nclx = _scan_avif_nclx(data)
    if nclx is not None:
        cp, tc, mc, full_range = nclx
        out = {
            "format": "pq-avif" if tc == 16 else "avif-sdr",
            "is_hdr": bool(tc == 16),
            "transfer_characteristics": tc,
            "transfer_name": _name(_TC_NAMES, tc),
            "color_primaries": cp,
            "primaries_name": _name(_CP_NAMES, cp),
            "matrix_coefficients": mc,
            "full_range_flag": full_range,
            "note": "container-level nclx probe (install hdrshot[avif-hdr] for bit depth)",
        }
        return out
    return _parse_generic_sdr(path, "avif-sdr")


def _scan_avif_nclx(data: bytes) -> tuple[int, int, int, int] | None:
    """Find the first ISOBMFF ``colr`` box with an ``nclx`` profile and return
    (primaries, transfer, matrix, full-range flag), or None."""
    idx = data.find(b"colrnclx")
    if idx < 0 or idx + 15 > len(data):
        return None
    import struct
    cp, tc, mc = struct.unpack(">HHH", data[idx + 8: idx + 14])
    return cp, tc, mc, data[idx + 14] & 1


# --------------------------------------------------------------------------- #
# Preview from an existing file (for `parse --preview`)
# --------------------------------------------------------------------------- #
def write_preview_from_file(path: str, out_png: str) -> str:
    """Best-effort tonemapped SDR PNG from an arbitrary capture file."""
    fmt = None
    with open(path, "rb") as fp:
        head = fp.read(4096)
    fmt = _detect_format(path, head)
    from PIL import Image
    if fmt == "exr":
        import OpenEXR  # pyright: ignore[reportMissingImports]
        part = OpenEXR.File(path).parts[0]
        try:
            rgb = part.channels["RGB"].pixels
        except Exception:
            rgb = np.stack([part.channels[c].pixels for c in ("R", "G", "B")], axis=-1)
        return write_preview_from_linear(np.asarray(rgb, np.float32), 80.0, out_png)
    if fmt in ("pq-heic", "heic", "heif"):
        import pillow_heif  # pyright: ignore[reportMissingImports]
        im = pillow_heif.open_heif(path, convert_hdr_to_8bit=True)[0].to_pillow()
        im.convert("RGB").save(out_png, format="PNG")
        return out_png
    # UltraHDR/JPEG/PNG/AVIF: Pillow yields the SDR base rendition directly.
    with Image.open(path) as im:
        im.convert("RGB").save(out_png, format="PNG")
    return out_png


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def cmd_parse(args) -> int:
    try:
        meta = parse_file(args.file)
    except Exception as e:
        print(_dumps(_file_error(args.file, e)))
        return 2                                  # undetermined — never a traceback
    if getattr(args, "preview", None):
        try:
            meta["preview_png"] = os.path.abspath(write_preview_from_file(args.file, args.preview))
        except Exception as e:
            meta["preview_error"] = str(e)
    print(_dumps(meta))
    # Exit 0 if HDR, 1 if SDR, 2 if undetermined — handy for shell/agent gating.
    if meta.get("is_hdr") is True:
        return 0
    if meta.get("is_hdr") is False:
        return 1
    return 2


def cmd_check(args) -> int:
    """Assert an image contains real HDR (optionally above nits/stops thresholds).

    Exit 0 = passes, 1 = fails (SDR / below threshold), 2 = undetermined. Designed
    for an agent to sanity-check its own capture: ``hdrshot check shot.jpg``.
    """
    try:
        meta = parse_file(args.file)
    except Exception as e:
        err = _file_error(args.file, e)
        verdict = {"file": err["path"], "format": None, "is_hdr": None, "peak_nits": None,
                   "headroom_stops": None, "pass": False, "reasons": [err["error"]],
                   "status": err.get("status"), "reason": err.get("reason"),
                   "provider": err.get("provider"),
                   "provider_version": err.get("provider_version")}
        if getattr(args, "json", False):
            print(_dumps(verdict))
        else:
            print(f"UNDETERMINED: {os.path.basename(args.file)} ({err['error']})")
        return 2
    is_hdr = meta.get("is_hdr")
    peak_nits = meta.get("peak_nits")
    stops = meta.get("gainmap_max_stops")
    if stops is None:                       # explicit None test: 0.0 is a real value
        stops = meta.get("headroom_stops")

    reasons = []
    ok = is_hdr is True
    if not ok:
        reasons.append("no HDR metadata / no highlights above SDR white")
    if args.min_nits is not None:
        if peak_nits is None:
            reasons.append(f"peak_nits unknown for {meta.get('format')} (cannot check --min-nits)")
            ok = False
        elif peak_nits < args.min_nits:
            reasons.append(f"peak {peak_nits} nits < required {args.min_nits}")
            ok = False
    if args.min_stops is not None:
        if stops is None:
            reasons.append(f"headroom stops unknown for {meta.get('format')}")
            ok = False
        elif stops < args.min_stops:
            reasons.append(f"headroom {stops} stops < required {args.min_stops}")
            ok = False

    verdict = {"file": meta["path"], "format": meta.get("format"), "is_hdr": is_hdr,
               "peak_nits": peak_nits, "headroom_stops": stops, "pass": ok,
               "reasons": reasons or (["HDR"] if ok else [])}
    if getattr(args, "json", False):
        print(_dumps(verdict))
    else:
        status = "HDR OK" if ok else ("UNDETERMINED" if is_hdr is None else "NOT HDR")
        detail = f" ({'; '.join(reasons)})" if reasons else ""
        extra = f", peak {peak_nits} nits" if peak_nits else ""
        print(f"{status}: {os.path.basename(meta['path'])} [{meta.get('format')}]{extra}{detail}")
    if is_hdr is None:
        return 2
    return 0 if ok else 1


def cmd_capture(args) -> int:
    from .__main__ import _backend
    backend = _backend()
    caps = backend.capture_all()
    disps = backend.enumerate_displays()

    if args.region is not None:
        x, y, w, h = args.region
        res = pipeline.capture_region((x, y, w, h), caps, disps)
    else:
        idx = args.display if args.display is not None else 0
        target = next((d for d in disps if d.index == idx), None)
        if target is None:
            raise pipeline.RegionError(
                f"no display with index {idx} (available: "
                f"{', '.join(str(d.index) for d in disps) or 'none'})")
        res = pipeline.capture_display(target, caps, disps)

    info = pipeline.save(res, args.format, args.out)
    preview = None
    if getattr(args, "preview", None):
        preview = os.path.abspath(write_preview_from_linear(res.linear, res.sdr_white_nits,
                                                            args.preview))
    print(captures_to_json([(res.display, res, info, preview)]))
    return 0
