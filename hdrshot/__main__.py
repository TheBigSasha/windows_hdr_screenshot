"""Command-line entry point.

    python -m hdrshot                 # launch the GUI
    python -m hdrshot info            # list displays + HDR status
    python -m hdrshot full [opts]     # capture a whole display, save
    python -m hdrshot region X Y W H  # capture a virtual-desktop rectangle
    python -m hdrshot selftest        # synthesise HDR, write every format, verify

``--verbose`` (global) enables logging to stderr; the capture/inspection commands
accept ``--json`` for machine-readable output (see also ``hdrshot parse`` and
``hdrshot capture`` for agent workflows).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping
from typing import Any

from . import __version__
from .core import pipeline
from .core.types import virtual_desktop_bounds

# Keep canonical issue #31 profiles first while accepting every legacy alias.
FORMATS = list(pipeline.PUBLIC_FORMATS)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _backend():
    """Acquire and initialise the platform capture backend, or exit cleanly."""
    from .backends import UnsupportedPlatformError, get_backend
    try:
        b = get_backend()
    except UnsupportedPlatformError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    b.set_process_dpi_aware()
    return b


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_info(args) -> int:
    b = _backend()
    disps = b.enumerate_displays()
    vb = virtual_desktop_bounds(disps)
    if getattr(args, "json", False):
        from .agentcli import displays_to_json
        print(displays_to_json(disps, vb))
        return 0
    print(f"Virtual desktop: {vb[2]}x{vb[3]} at ({vb[0]},{vb[1]})  |  {len(disps)} display(s)\n")
    for d in disps:
        star = "*" if d.is_primary else " "
        print(f"{star} [{d.index}] {d.friendly_name}  ({d.gdi_name})")
        print(f"      {d.width}x{d.height} at ({d.x},{d.y})  {d.state_label}")
        print(f"      {d.bits_per_color}-bit {d.color_encoding}, "
              f"SDR white {d.sdr_white_nits:.0f} nits, rotation {d.rotation} deg")
    return 0


def _report(info: Mapping[str, Any]) -> None:
    extra = ""
    if "gainmap_max_stops" in info:
        extra = f"  gain map: {info['gainmap_max_stops']} stops"
    tag = "HDR" if info.get("hdr") else "SDR"
    print(f"[{tag}] {info['format']:8} -> {info['path']}{extra}")


def cmd_full(args) -> int:
    b = _backend()
    caps = b.capture_all()
    disps = b.enumerate_displays()
    targets = disps if args.all else [d for d in disps if d.index == args.display] or disps[:1]
    results = []
    for d in targets:
        res = pipeline.capture_display(d, caps, disps)
        info = pipeline.save(res, args.format, args.out)
        results.append((d, res, info))
    if getattr(args, "json", False):
        from .agentcli import captures_to_json
        print(captures_to_json(results))
        return 0
    for d, res, info in results:
        st = res.stats
        print(f"{d.friendly_name}: peak {st['peak_ratio']:.2f}x paper white "
              f"({st['peak_nits']:.0f} nits), HDR content={res.hdr_capable_content}")
        _report(info)
    return 0


def cmd_region(args) -> int:
    b = _backend()
    caps = b.capture_all()
    disps = b.enumerate_displays()
    res = pipeline.capture_region((args.x, args.y, args.w, args.h), caps, disps)
    info = pipeline.save(res, args.format, args.out)
    if getattr(args, "json", False):
        from .agentcli import captures_to_json
        print(captures_to_json([(res.display, res, info)]))
        return 0
    _report(info)
    return 0


def cmd_selftest(args) -> int:
    from .selftest import run_selftest
    return run_selftest(args.out)


def cmd_capabilities(args) -> int:
    print(json.dumps(pipeline.capabilities_payload(), indent=2))
    return 0


def cmd_parse(args) -> int:
    from .agentcli import cmd_parse as _run
    return _run(args)


def cmd_check(args) -> int:
    from .agentcli import cmd_check as _run
    return _run(args)


def cmd_capture(args) -> int:
    from .agentcli import cmd_capture as _run
    return _run(args)


def cmd_gui(args) -> int:
    try:
        from .ui.app import main as gui_main
    except ImportError as e:
        print(f"error: the GUI needs the optional [gui] extra (PySide6): "
              f"pip install \"hdrshot[gui]\"  ({e})", file=sys.stderr)
        return 2
    return gui_main()


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hdrshot", description="True HDR screenshots on Windows.")
    p.add_argument("--version", action="version", version=f"hdrshot {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging to stderr")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("info", help="list displays and HDR status")
    pi.add_argument("--json", action="store_true", help="machine-readable output")

    pf = sub.add_parser("full", help="capture an entire display")
    pf.add_argument("--display", type=int, default=0, help="display index (see `info`)")
    pf.add_argument("--all", action="store_true", help="capture every display")
    pf.add_argument("--format", choices=FORMATS, default="auto")
    pf.add_argument("--out", default=None, help="output directory")
    pf.add_argument("--json", action="store_true", help="machine-readable output")

    pr = sub.add_parser("region", help="capture a virtual-desktop rectangle")
    for a in ("x", "y", "w", "h"):
        pr.add_argument(a, type=int)
    pr.add_argument("--format", choices=FORMATS, default="auto")
    pr.add_argument("--out", default=None)
    pr.add_argument("--json", action="store_true", help="machine-readable output")

    pc = sub.add_parser("capture", help="one-shot agent capture: JSON + SDR preview + true-HDR file")
    grp = pc.add_mutually_exclusive_group()
    grp.add_argument("--display", type=int, default=None, help="display index")
    grp.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    pc.add_argument("--format", choices=FORMATS, default="auto")
    pc.add_argument("--out", default=None, help="output directory")
    pc.add_argument("--preview", default=None, help="write a tonemapped SDR preview PNG here")
    pc.add_argument("--json", action="store_true", default=True, help="(always on)")

    pp = sub.add_parser("parse", help="read an image and report its HDR metadata")
    pp.add_argument("file", help="UltraHDR / EXR / HEIC / AVIF / PNG / JPEG file")
    pp.add_argument("--preview", default=None, help="write a tonemapped SDR preview PNG here")
    pp.add_argument("--json", action="store_true", default=True, help="(always on)")

    pk = sub.add_parser("check", help="assert an image contains real HDR (exit 0/1/2)")
    pk.add_argument("file", help="image file to check")
    pk.add_argument("--min-nits", type=float, default=None, help="require peak >= this")
    pk.add_argument("--min-stops", type=float, default=None, help="require headroom >= this many stops")
    pk.add_argument("--json", action="store_true", help="machine-readable output")

    ps = sub.add_parser("selftest", help="synthesise HDR and write every format")
    ps.add_argument("--out", default=None)

    pcap = sub.add_parser("capabilities", help="report exact runtime codec profiles as JSON")
    pcap.add_argument("--json", action="store_true", default=True, help="(always on)")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    handlers = {"info": cmd_info, "full": cmd_full, "region": cmd_region,
                "capture": cmd_capture, "parse": cmd_parse, "check": cmd_check,
                "selftest": cmd_selftest, "capabilities": cmd_capabilities, None: cmd_gui}
    try:
        return handlers[args.cmd](args)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        # Machine-readable failure contract (docs/AGENTS.md): JSON on stdout when
        # --json, one line on stderr otherwise, exit 3 — never a bare traceback.
        logging.getLogger(__name__).debug("command failed", exc_info=True)
        msg = f"{type(e).__name__}: {e}"
        if getattr(args, "json", False):
            error: dict[str, Any] = {"type": type(e).__name__, "message": str(e)}
            capability = getattr(e, "capability", None)
            if capability is not None:
                error.update({
                    "profile": capability.profile,
                    "status": capability.status,
                    "reason": capability.reason,
                    "provider": capability.provider,
                    "provider_version": capability.provider_version,
                })
            elif isinstance(e, pipeline.CodecEncodeError):
                error.update({
                    "profile": e.actual_profile,
                    "status": e.status,
                    "reason": e.reason,
                    "provider": e.provider,
                    "provider_version": e.provider_version,
                })
            else:
                error.update({"status": None, "reason": None, "provider": None,
                              "provider_version": None})
            print(json.dumps({"error": error}, indent=2))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
