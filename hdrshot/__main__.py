"""Command-line entry point.

    python -m hdrshot                 # launch the GUI
    python -m hdrshot info            # list displays + HDR status
    python -m hdrshot full [opts]     # capture a whole display, save
    python -m hdrshot region X Y W H  # capture a virtual-desktop rectangle
    python -m hdrshot selftest        # synthesise HDR, write every format, verify
"""
from __future__ import annotations

import argparse
import sys

from . import displays, pipeline

FORMATS = ["auto", "ultrahdr", "exr", "heic", "png", "jpeg", "avif"]


def cmd_info(args) -> int:
    displays.set_process_dpi_aware()
    disps = displays.enumerate_displays()
    vb = displays.virtual_desktop_bounds(disps)
    print(f"Virtual desktop: {vb[2]}x{vb[3]} at ({vb[0]},{vb[1]})  |  {len(disps)} display(s)\n")
    for d in disps:
        state = "HDR ON" if d.hdr_enabled else ("HDR-capable (off)" if d.hdr_supported else "SDR only")
        star = "*" if d.is_primary else " "
        print(f"{star} [{d.index}] {d.friendly_name}  ({d.gdi_name})")
        print(f"      {d.width}x{d.height} at ({d.x},{d.y})  {state}")
        print(f"      {d.bits_per_color}-bit {d.color_encoding}, SDR white {d.sdr_white_nits:.0f} nits")
    return 0


def _report(info: dict) -> None:
    extra = ""
    if "gainmap_max_stops" in info:
        extra = f"  gain map: {info['gainmap_max_stops']} stops"
    tag = "HDR" if info.get("hdr") else "SDR"
    print(f"[{tag}] {info['format']:8} -> {info['path']}{extra}")


def cmd_full(args) -> int:
    displays.set_process_dpi_aware()
    from . import capture
    caps = capture.capture_all()
    disps = displays.enumerate_displays()
    targets = disps if args.all else [d for d in disps if d.index == args.display] or disps[:1]
    for d in targets:
        res = pipeline.capture_display(d, caps)
        st = res.stats
        print(f"{d.friendly_name}: peak {st['peak_ratio']:.2f}x paper white "
              f"({st['peak_nits']:.0f} nits), HDR content={res.hdr_capable_content}")
        _report(pipeline.save(res, args.format, args.out))
    return 0


def cmd_region(args) -> int:
    displays.set_process_dpi_aware()
    res = pipeline.capture_region((args.x, args.y, args.w, args.h))
    _report(pipeline.save(res, args.format, args.out))
    return 0


def cmd_selftest(args) -> int:
    from .selftest import run_selftest
    return run_selftest(args.out)


def cmd_gui(args) -> int:
    from .ui.app import main as gui_main
    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hdrshot", description="True HDR screenshots on Windows.")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("info", help="list displays and HDR status")

    pf = sub.add_parser("full", help="capture an entire display")
    pf.add_argument("--display", type=int, default=0, help="display index (see `info`)")
    pf.add_argument("--all", action="store_true", help="capture every display")
    pf.add_argument("--format", choices=FORMATS, default="auto")
    pf.add_argument("--out", default=None, help="output directory")

    pr = sub.add_parser("region", help="capture a virtual-desktop rectangle")
    for a in ("x", "y", "w", "h"):
        pr.add_argument(a, type=int)
    pr.add_argument("--format", choices=FORMATS, default="auto")
    pr.add_argument("--out", default=None)

    ps = sub.add_parser("selftest", help="synthesise HDR and write every format")
    ps.add_argument("--out", default=None)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"info": cmd_info, "full": cmd_full, "region": cmd_region,
                "selftest": cmd_selftest, None: cmd_gui}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
