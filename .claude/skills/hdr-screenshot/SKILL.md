---
name: hdr-screenshot
description: Capture and reason about TRUE HDR screenshots on Windows with HDR Shot. Use when an agent needs to screenshot an HDR display (or verify HDR rendering) and must read real luminance — not an 8-bit SDR frame where blown highlights read as flat white. Triggers include "capture an HDR screenshot", "is this display showing real HDR", "how bright is this highlight", "grab the screen as UltraHDR", or verifying that HDR content actually rendered.
---

# HDR Shot — HDR-aware screenshots for agents

Ordinary screen grabs (PrintScreen, most capture APIs) return an **8-bit SDR**
image even on an HDR monitor: every highlight above SDR white is clipped to
`255,255,255`, so you literally cannot tell an HDR scene from an SDR one by
looking. **HDR Shot** captures the real floating-point HDR framebuffer and lets
you read the true luminance.

## The one thing to remember

> A tonemapped **SDR preview understates HDR**. Blown highlights look flat white
> in the preview PNG. The **`peak_nits`, `peak_ratio`, and gain-map `*_stops`**
> fields in the JSON (and the true-HDR file itself) are the source of truth for
> how bright the capture actually is. Never conclude "not HDR" from the preview —
> check the numbers.

## Prerequisites

- Windows 10 1803+ / Windows 11 with `hdrshot` installed (`pip install -e .` in
  the repo, or `pip install hdrshot`). No GUI needed for agent use.
- Real HDR pixels only exist when **Windows HDR is ON** for the display
  (`Settings → Display → Use HDR`, or `Win`+`Alt`+`B`). With HDR off the desktop
  is 8-bit SDR and captures are correct but contain no HDR. Check with
  `hdrshot info --json` (`any_hdr_enabled`).

## Core commands (all emit JSON for parsing)

### 1. See the displays and their HDR state
```bash
hdrshot info --json
```
Returns `virtual_desktop`, `any_hdr_enabled`, and per-display `hdr_enabled`,
`sdr_white_nits`, `bits_per_color`, `rotation`, geometry.

### 2. One-shot capture → true-HDR file + viewable SDR preview + JSON
```bash
# Whole display 0:
hdrshot capture --display 0 --format ultrahdr --out ./shots --preview ./shots/preview.png
# A virtual-desktop rectangle (x y w h):
hdrshot capture --region 100 100 1200 800 --format auto --out ./shots --preview ./shots/preview.png
```
The JSON `captures[0]` has `path` (the true-HDR file), `preview_png` (SDR PNG you
can open/view), `peak_nits`, `peak_ratio`, `hdr_pixel_fraction`,
`gainmap_max_stops`, `encoded_hdr`, `hdr_content`, `is_hdr_live`, and the source
`display`. **View `preview_png` to see the framing; trust the numbers for luminance.**

### 3. Read HDR metadata back from any image
```bash
hdrshot parse ./shots/whatever.jpg --preview ./shots/p.png
```
Works on UltraHDR / EXR / HEIC / AVIF / PNG / JPEG. Reports `is_hdr`, and per
format: gain-map stops (UltraHDR), `peak_nits` + `headroom_stops` (EXR), nclx
`bit_depth`/`transfer`/`primaries` (HEIC/AVIF). **Exit code: 0 = HDR, 1 = SDR,
2 = undetermined** — usable directly in a shell gate.

### 4. Assert an image really is HDR (sanity-check your own work)
```bash
hdrshot check ./shots/shot.jpg                  # exit 0 if HDR, 1 if not
hdrshot check ./shots/shot.jpg --min-nits 600   # also require >= 600 nits peak
hdrshot check ./shots/shot.jpg --min-stops 2    # also require >= 2 stops headroom
```

## Formats (pick with `--format`)

| Format     | HDR? | Use when |
|------------|------|----------|
| `auto`     | —    | Default. UltraHDR for HDR content, PNG for SDR. |
| `ultrahdr` | yes  | Portable "HDR-in-a-JPEG" (gain map). Opens everywhere; HDR on Chrome/Windows Photos/Android (+ Apple with ISO metadata). Best default. |
| `exr`      | yes  | Lossless linear scRGB. Exact luminance for analysis/archival. `parse` reports precise `peak_nits`. |
| `heic`     | yes  | 10-bit BT.2020 PQ (needs the `[heic]` extra). |
| `avif`     | yes* | 10-bit BT.2020 PQ with the `[avif-hdr]` extra; else 8-bit SDR. |
| `png`/`jpeg` | no | Plain SDR. |

For **exact numeric analysis**, capture `exr` and `parse` it — `peak_nits` is the
true luminance. For **sharing/rendering**, use `ultrahdr`.

## Worked examples

**"Verify display 0 is showing real HDR right now."**
```bash
hdrshot info --json                              # confirm displays[0].hdr_enabled == true
hdrshot capture --display 0 --format exr --out ./v --preview ./v/p.png
hdrshot check ./v/*.exr --min-nits 300           # exit 0 => real HDR highlights present
```
Then view `./v/p.png` for context, but base the HDR/not-HDR call on `check`'s exit
code and the `peak_nits` in the capture JSON.

**"Capture a region as UltraHDR and report peak nits."**
```bash
hdrshot capture --region 0 0 800 600 --format ultrahdr --out ./r --preview ./r/p.png
```
Read `captures[0].peak_nits` and `captures[0].gainmap_max_stops` from the JSON.
(For UltraHDR, `peak_ratio_over_sdr_white = 2^gainmap_max_stops`; multiply by the
display `sdr_white_nits` for an absolute-nits estimate.)

**"Compare how one scene encodes across UltraHDR / EXR / HEIC."**
```bash
for f in ultrahdr exr heic; do
  hdrshot capture --display 0 --format $f --out ./cmp
done
hdrshot parse ./cmp/*.jpg      # UltraHDR: gain-map stops
hdrshot parse ./cmp/*.exr      # EXR: exact peak_nits
hdrshot parse ./cmp/*.heic     # HEIC: 10-bit PQ nclx (transfer 16, primaries 9)
```

## Gotchas

- **No HDR display / HDR off** → captures succeed but `is_hdr_live` is false and
  format falls back to SDR. That's correct, not a bug.
- **Headless CI / no interactive desktop** can't do a real DXGI grab; use
  `hdrshot selftest` to exercise the encode path on a synthetic HDR scene.
- **Rotated (portrait) monitors** are best-effort; verify framing in the preview.
- Peak-nits is **unknown for UltraHDR** (its gain map is relative to SDR white);
  use `gainmap_max_stops`, or capture EXR for absolute nits.
