# HDR Shot for agents & automation

HDR Shot is built to be driven by scripts and AI agents, not just clicked. Every
capture/inspection command emits JSON, uses meaningful exit codes, and never
requires screen-scraping. This page is the machine-facing reference; for a
task-oriented walkthrough see the [`hdr-screenshot` skill](../skills/hdr-screenshot/SKILL.md).

## Why a special tool for agents

An ordinary screenshot on an HDR monitor is 8-bit SDR: highlights above SDR white
are clipped to white, so **an agent literally cannot distinguish HDR from SDR by
looking at the pixels**. A tonemapped preview has the same problem. HDR Shot
captures the floating-point HDR framebuffer and exposes the true luminance as
numbers, so an agent can decide deterministically.

> **Rule:** the SDR preview understates HDR. Use `peak_nits`, `peak_ratio`,
> `gainmap_max_stops` (and the true-HDR file) as the source of truth. Every JSON
> payload repeats this in its `notes` field.

## Install (headless / agent)

```bash
pip install hdrshot                 # base: capture + PNG/JPEG/EXR/UltraHDR
pip install "hdrshot[avif-hdr]"     # + true 10-bit PQ HDR AVIF
pip install "hdrshot[heic]"         # + 10-bit PQ HEIC (pulls x265/GPL; see notes)
```
No GUI/Qt is required for any command below. Windows 10 1803+ / 11 for live
capture; the analysis commands (`parse`, `check`, `selftest`) run on any OS.

## Commands & JSON schemas

### `hdrshot info --json`
```json
{
  "virtual_desktop": { "x": 0, "y": 0, "width": 5760, "height": 2160 },
  "display_count": 2,
  "any_hdr_enabled": true,
  "displays": [
    {
      "index": 0, "gdi_name": "\\\\.\\DISPLAY6", "friendly_name": "M27P6",
      "x": 0, "y": 0, "width": 3840, "height": 2160, "is_primary": true,
      "hdr_supported": true, "hdr_enabled": true, "bits_per_color": 10,
      "sdr_white_nits": 480.0, "color_encoding": "RGB", "rotation": 0,
      "state": "HDR ON"
    }
  ]
}
```

### `hdrshot capture [--display N | --region X Y W H] [--format F] [--out DIR] [--preview PNG]`
Grabs, saves the true-HDR file, optionally writes a viewable SDR preview PNG, and
prints JSON. Always JSON.
```json
{
  "captures": [
    {
      "path": "…/HDR Screenshot 2026-07-03 153045.jpg",
      "format": "ultrahdr", "width": 1200, "height": 800,
      "encoded_hdr": true,      // written in an HDR format AND content is HDR
      "hdr_content": true,      // pixels carry values above SDR white
      "is_hdr_live": true,      // hdr_content AND the source display has HDR on
      "peak_nits": 1406.2,      // 0 or approximate for UltraHDR (see below)
      "peak_ratio": 2.93,       // brightest channel / SDR white
      "hdr_pixel_fraction": 0.12,
      "sdr_white_nits": 480.0,
      "gainmap_max_stops": 1.55,          // UltraHDR only
      "spans_displays": ["\\\\.\\DISPLAY6"], // only when a region stitched monitors
      "display": { "gdi_name": "…", "friendly_name": "…",
                   "hdr_enabled": true, "sdr_white_nits": 480.0 },
      "preview_png": "…/preview.png"
    }
  ],
  "notes": "The SDR preview understates HDR: …"
}
```
`full --json` and `region --json` emit the same `captures` shape.

### `hdrshot parse FILE [--preview PNG]`
Reads HDR metadata out of an existing file. **Exit 0 = HDR, 1 = SDR, 2 = undetermined.**
Fields vary by format:
```jsonc
// EXR (exact luminance)
{ "format": "exr", "is_hdr": true, "max_linear": 20.0, "peak_nits": 1600.0,
  "headroom_stops": 4.32, "channels": ["RGB"] }
// UltraHDR (relative gain map)
{ "format": "ultrahdr", "is_hdr": true, "gainmap_min_stops": 0.0,
  "gainmap_max_stops": 4.30, "gamma": 1.0, "peak_ratio_over_sdr_white": 19.7,
  "apple_compatible": true,
  "container": { "mpf": true, "hdrgm_xmp": true, "iso_21496_1": true } }
// HEIC / AVIF (nclx)
{ "format": "heic", "is_hdr": true, "bit_depth": 10,
  "transfer_characteristics": 16, "transfer_name": "PQ (SMPTE ST 2084)",
  "color_primaries": 9, "primaries_name": "BT.2020" }
```

### `hdrshot check FILE [--min-nits N] [--min-stops S] [--json]`
Assertion helper for validating your own captures. **Exit 0 = pass, 1 = fail,
2 = undetermined.**
```json
{ "file": "…", "format": "exr", "is_hdr": true, "peak_nits": 1600.0,
  "headroom_stops": 4.32, "pass": true, "reasons": ["HDR"] }
```

## Exit codes

| Command | 0 | 1 | 2 | other |
|---------|---|---|---|-------|
| `check` | HDR (passes thresholds) | SDR / below threshold | undetermined | — |
| `parse` | HDR | SDR | undetermined | — |
| `info`/`full`/`region`/`capture` | success | — | unsupported platform (no capture backend) | argparse error |

## Interpreting luminance

- **`peak_nits` is exact** for EXR and HEIC/AVIF-PQ. For **UltraHDR it is
  approximate/omitted** because the gain map is relative to SDR white — use
  `gainmap_max_stops`, or `peak_ratio_over_sdr_white = 2^gainmap_max_stops`, and
  multiply by the display's `sdr_white_nits` for an absolute estimate.
- **`peak_ratio`** is the brightest channel divided by SDR paper white. `> 1.05`
  with a nonzero `hdr_pixel_fraction` is the HDR trigger.
- For pixel-exact analysis, capture **EXR** (`--format exr`) and `parse` it.

## Python API (skip the subprocess)

Everything the CLI does is importable and platform-free except the actual grab:

```python
from hdrshot.backends import get_backend        # raises UnsupportedPlatformError off-Windows
from hdrshot.core import pipeline, color
from hdrshot import agentcli

backend = get_backend()
backend.set_process_dpi_aware()
caps  = backend.capture_all()                    # {gdi_name: MonitorCapture}
disps = backend.enumerate_displays()

res  = pipeline.capture_region((100, 100, 1200, 800), caps, disps)
info = pipeline.save(res, "ultrahdr", out_dir="./shots")
print(res.stats["peak_nits"], res.hdr_capable_content)

# Parse / verify without capturing:
meta = agentcli.parse_file("shot.jpg")           # dict; is_hdr, gainmap_max_stops, …
```

`hdrshot.core`, `hdrshot.encoders`, and `hdrshot.agentcli` import on **any OS**
(Linux/macOS), so encode/parse/verify logic can run in cross-platform CI; only
`get_backend().capture_all()` needs Windows.

## Robust automation patterns

```bash
# Gate a workflow on the display actually being in HDR mode:
hdrshot info --json | jq -e '.any_hdr_enabled' >/dev/null || { echo "HDR is off"; exit 1; }

# Capture, then assert the result is real HDR before trusting it:
hdrshot capture --display 0 --format ultrahdr --out ./out --preview ./out/p.png > cap.json
hdrshot check ./out/*.jpg --min-stops 1 || echo "capture came back SDR"

# Deterministic HDR/SDR branch by exit code:
if hdrshot check "$IMG" >/dev/null; then echo HDR; else echo SDR; fi
```
