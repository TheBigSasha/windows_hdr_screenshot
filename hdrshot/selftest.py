"""Self-test: synthesise a known HDR scene, write every format, verify each.

Runs with no HDR display attached, so it exercises the whole encode/verify path
deterministically and produces demo artifacts.
"""
from __future__ import annotations

import os

import numpy as np

from .core import color, pipeline
from .encoders import exr as exr_mod
from .encoders import heic as heic_mod
from .encoders import sdr as sdr_mod


def make_hdr_scene(w: int = 1280, h: int = 720) -> np.ndarray:
    """scRGB FP16 scene: a nit ramp + saturated HDR color swatches over a dim
    background. 1.0 == 80 nits (paper white)."""
    img = np.full((h, w, 3), 0.02, np.float32)

    # Top: horizontal luminance ramp 0 -> 20x paper white (0..1600 nits).
    ramp = np.linspace(0.0, 20.0, w, dtype=np.float32)
    img[40:h // 2 - 40, :, :] = ramp[None, :, None]

    # Bottom: 6 saturated swatches, left SDR (=1x) vs right HDR (=8x).
    base_colors = [(1, .1, .1), (.1, 1, .1), (.2, .4, 1),
                   (1, 1, .2), (1, .3, 1), (.2, 1, 1)]
    y0, y1 = h // 2 + 20, h - 60
    sw = w // len(base_colors)
    for i, c in enumerate(base_colors):
        c = np.array(c, np.float32)
        x0 = i * sw
        img[y0:y1, x0:x0 + sw // 2] = c * 0.9              # SDR half
        img[y0:y1, x0 + sw // 2:x0 + sw] = c * 8.0         # HDR half (640 nits peak)
    return img


def _verify_exr(path):
    import OpenEXR
    rgb = OpenEXR.File(path).parts[0].channels["RGB"].pixels
    return f"max {float(rgb.max()):.1f} (linear, >1.0 = HDR)"


def _verify_ultrahdr(path):
    import io

    from PIL import Image
    data = open(path, "rb").read()
    ok_mpf = b"MPF\x00" in data
    ok_xmp = b"hdrgm:GainMapMax" in data
    gi = data.find(b"hdrgm:GainMapMin")
    soi = data.rfind(b"\xFF\xD8", 0, gi)
    gm = Image.open(io.BytesIO(data[soi:]))
    return f"gain map {gm.size} {'L' if gm.mode=='L' else gm.mode}, MPF={ok_mpf}, hdrgm-XMP={ok_xmp}"


def _verify_heic(path):
    import pillow_heif
    im = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)[0]
    n = im.info.get("nclx_profile", {})
    return f"{im.info.get('bit_depth')}-bit, transfer={n.get('transfer_characteristics')} (16=PQ), primaries={n.get('color_primaries')} (9=BT.2020)"


def _verify_avif(path):
    from .encoders import avif_hdr
    m = avif_hdr.probe(path)
    if m and m.get("bit_depth") == 10:
        return (f"{m['bit_depth']}-bit, transfer={m.get('transfer_characteristics')} (16=PQ), "
                f"primaries={m.get('color_primaries')} (9=BT.2020)")
    return "8-bit SDR (install hdrshot[avif-hdr] for 10-bit PQ)"


def run_selftest(out_dir: str | None = None) -> int:
    out_dir = out_dir or os.path.join(pipeline.default_save_dir(), "hdrshot_selftest")
    os.makedirs(out_dir, exist_ok=True)
    img = make_hdr_scene()
    stats = color.hdr_stats(img, 80.0)
    print(f"Synthetic HDR scene: peak {stats['peak_ratio']:.1f}x paper white "
          f"({stats['peak_nits']:.0f} nits), {stats['hdr_pixel_fraction']*100:.0f}% HDR pixels\n")

    res = pipeline.CaptureResult(linear=img, sdr_white_nits=80.0, display=None,
                                 region_phys=(0, 0, img.shape[1], img.shape[0]), stats=stats)
    verifiers = {"exr": _verify_exr, "ultrahdr": _verify_ultrahdr, "heic": _verify_heic,
                 "avif": _verify_avif}
    formats = ["ultrahdr", "png", "jpeg"]
    if sdr_mod.avif_available():
        formats.append("avif")
    else:
        print("  SKIP avif       (optional extra not installed: pip install hdrshot[avif-sdr])")
    if exr_mod.available():
        formats.insert(1, "exr")
    else:
        print("  SKIP exr        (optional extra not installed: pip install hdrshot[exr])")
    if heic_mod.available():
        formats.insert(2, "heic")
    else:
        print("  SKIP heic       (optional extra not installed: pip install hdrshot[heic])")

    ok = True
    for fmt in formats:
        path = os.path.join(out_dir, f"selftest_{fmt}{pipeline.EXT[fmt]}")
        try:
            pipeline.encode(res, fmt, path)
            size = os.path.getsize(path)
            note = verifiers.get(fmt, lambda p: "written")(path)
            print(f"  OK  {fmt:9} {size:>8} B  {note}")
        except Exception as e:
            ok = False
            print(f"  ERR {fmt:9} {e!r}")
    print(f"\nArtifacts in: {out_dir}")
    return 0 if ok else 1
