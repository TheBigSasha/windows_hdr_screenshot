"""Deterministic encoder contract test used by source CI and release artifacts."""
from __future__ import annotations

import os

import numpy as np

from .codecs import PROFILES, bundle_manifest, capabilities
from .core import color, pipeline


def make_hdr_scene(w: int = 1280, h: int = 720) -> np.ndarray:
    """Create an scRGB fixture with both SDR and well-known HDR highlights."""
    img = np.full((h, w, 3), 0.02, np.float32)
    ramp = np.linspace(0.0, 20.0, w, dtype=np.float32)
    img[40:h // 2 - 40, :, :] = ramp[None, :, None]
    base_colors = [(1, .1, .1), (.1, 1, .1), (.2, .4, 1),
                   (1, 1, .2), (1, .3, 1), (.2, 1, 1)]
    y0, y1 = h // 2 + 20, h - 60
    sw = w // len(base_colors)
    for i, c in enumerate(base_colors):
        colour = np.array(c, np.float32)
        x0 = i * sw
        img[y0:y1, x0:x0 + sw // 2] = colour * 0.9
        img[y0:y1, x0 + sw // 2:x0 + sw] = colour * 8.0
    return img


def _verify_exr(path: str) -> str:
    import OpenEXR  # pyright: ignore[reportMissingImports]
    rgb = OpenEXR.File(path).parts[0].channels["RGB"].pixels
    return f"max {float(np.asarray(rgb).max()):.1f} (linear, >1.0 = HDR)"


def _verify_ultrahdr(path: str) -> str:
    import io

    from PIL import Image
    with open(path, "rb") as fp:
        data = fp.read()
    ok_mpf = b"MPF\x00" in data
    ok_xmp = b"hdrgm:GainMapMax" in data
    gi = data.find(b"hdrgm:GainMapMin")
    soi = data.rfind(b"\xFF\xD8", 0, gi)
    gm = Image.open(io.BytesIO(data[soi:]))
    if not (ok_mpf and ok_xmp and gm.mode == "L"):
        raise ValueError("UltraHDR container is missing MPF, gain-map XMP, or L gain map")
    return f"gain map {gm.size} L, MPF={ok_mpf}, hdrgm-XMP={ok_xmp}"


def _verify_heic(path: str) -> str:
    import pillow_heif  # pyright: ignore[reportMissingImports]
    im = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)[0]
    n = im.info.get("nclx_profile", {})
    if im.info.get("bit_depth") != 10 or n.get("transfer_characteristics") != 16:
        raise ValueError("HEIC is not a 10-bit PQ image")
    return f"10-bit, transfer={n.get('transfer_characteristics')} (16=PQ), " \
           f"primaries={n.get('color_primaries')} (9=BT.2020)"


def _verify_avif(path: str, hdr: bool) -> str:
    from .encoders import avif_hdr
    if hdr:
        meta = avif_hdr.probe(path)
        if not meta or meta.get("bit_depth") != 10 or meta.get("transfer_characteristics") != 16:
            raise ValueError("AVIF is not a 10-bit PQ image")
        return (f"10-bit, transfer={meta.get('transfer_characteristics')} (16=PQ), "
                f"primaries={meta.get('color_primaries')} (9=BT.2020)")
    from PIL import Image
    with Image.open(path) as im:
        if im.mode not in {"RGB", "RGBA"}:
            raise ValueError(f"unexpected SDR AVIF mode {im.mode}")
        return f"{im.mode} SDR"


def _verify(profile: str, path: str) -> str:
    if profile == "exr":
        return _verify_exr(path)
    if profile == "ultrahdr":
        return _verify_ultrahdr(path)
    if profile == "heic":
        return _verify_heic(path)
    if profile == "avif-hdr":
        return _verify_avif(path, True)
    if profile == "avif-sdr":
        return _verify_avif(path, False)
    return "written and readable"


def run_selftest(out_dir: str | None = None) -> int:
    """Encode every available profile and enforce frozen-bundle contracts.

    Generic source CI may legitimately skip optional profiles. A frozen bundle
    carries ``bundle-capabilities.json``; there the available profile set must
    exactly match the manifest and every advertised profile must encode and verify.
    """
    out_dir = out_dir or os.path.join(pipeline.default_save_dir(), "hdrshot_selftest")
    os.makedirs(out_dir, exist_ok=True)
    caps = capabilities()
    manifest = bundle_manifest()
    strict = manifest is not None
    expected = set(manifest.get("expected_profiles", [])) if manifest else None
    available = {profile for profile, cap in caps.items() if cap.available}

    if strict and expected is not None and available != expected:
        missing = sorted(expected - available)
        unexpected = sorted(available - expected)
        print(f"CAPABILITY CONTRACT FAILED: missing={missing}, unexpected={unexpected}")
        return 1

    img = make_hdr_scene()
    stats = color.hdr_stats(img, 80.0)
    print(f"Synthetic HDR scene: peak {stats['peak_ratio']:.1f}x paper white "
          f"({stats['peak_nits']:.0f} nits), {stats['hdr_pixel_fraction'] * 100:.0f}% HDR pixels\n")
    res = pipeline.CaptureResult(linear=img, sdr_white_nits=80.0, display=None,
                                 region_phys=(0, 0, img.shape[1], img.shape[0]), stats=stats)

    ok = True
    for profile in PROFILES:
        cap = caps[profile]
        if not cap.available:
            if strict and expected is not None and profile in expected:
                print(f"  ERR {profile:9} unavailable: {cap.status}: {cap.reason}")
                ok = False
            else:
                print(f"  SKIP {profile:8} ({cap.status}: {cap.reason})")
            continue
        path = os.path.join(out_dir, f"selftest_{profile}{pipeline.EXT[profile]}")
        try:
            info = pipeline.encode(res, profile, path)
            if info.get("requested_profile") != profile or info.get("actual_profile") != profile:
                raise ValueError(f"profile drift: requested={profile}, info={info}")
            note = _verify(profile, path)
            print(f"  OK  {profile:9} {os.path.getsize(path):>8} B  "
                  f"requested_profile={profile}, actual_profile={info['actual_profile']}, {note}")
        except Exception as exc:
            ok = False
            print(f"  ERR {profile:9} {type(exc).__name__}: {exc}")
    print(f"\nArtifacts in: {out_dir}")
    return 0 if ok else 1
