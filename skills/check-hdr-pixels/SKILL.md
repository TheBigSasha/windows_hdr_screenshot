---
name: check-hdr-pixels
description: Quickly determine whether an image file actually contains HDR pixels (highlights above SDR white), and how bright, using HDR Shot's `hdrshot check` / `hdrshot parse`. Use to sanity-check that a screenshot or exported image really is HDR — e.g. after capturing, after an encode, or when validating that HDR content rendered. Triggers include "does this image contain HDR pixels", "is this file HDR", "verify the screenshot is HDR", "how many nits is this".
---

# Does this image contain HDR pixels?

A lightweight verification util built on HDR Shot. Use it to confirm an image is
genuinely HDR (has highlights above SDR white) rather than an 8-bit SDR frame —
something you cannot tell by viewing a tonemapped preview, where blown highlights
look like flat white.

 Supports **UltraHDR JPEG, OpenEXR, PQ HEIC, PQ AVIF, PNG, JPEG**.

## Fast boolean check (exit code)

```bash
hdrshot check IMAGE            # exit 0 = HDR, 1 = SDR, 2 = undetermined
```

Add thresholds when "a little HDR" isn't enough:

```bash
hdrshot check IMAGE --min-nits 600     # require peak luminance >= 600 nits
hdrshot check IMAGE --min-stops 2       # require >= 2 stops of highlight headroom
hdrshot check IMAGE --json              # structured verdict + reasons
```

`--json` yields `{ "is_hdr", "peak_nits", "headroom_stops", "pass", "reasons" }`.

## Full metadata (what kind of HDR, how bright)

```bash
hdrshot parse IMAGE
```

Returns JSON describing the container and luminance. Examples of the key fields:
- **EXR** → `peak_nits`, `headroom_stops`, `max_linear` (exact luminance).
- **UltraHDR** → `gainmap_max_stops`, `container.{mpf,hdrgm_xmp,iso_21496_1}`,
  `apple_compatible`.
- **HEIC / AVIF** → `bit_depth`, `transfer_characteristics` (16 = PQ),
  `color_primaries` (9 = BT.2020).
- **PNG / JPEG** → `is_hdr: false`.

`parse` also exits 0/1/2 (HDR / SDR / undetermined).

## Interpreting it

- **Source of truth = the numbers**, not a preview PNG. `peak_nits`/`peak_ratio`/
  `*_stops` reflect the true signal; a tonemapped preview clips highlights.
- **`peak_nits` is exact for EXR** and **unknown for PQ HEIC/PQ AVIF and UltraHDR** (gain-map
  is relative to SDR white — use `gainmap_max_stops`, or re-capture as EXR for
  absolute nits).
- **Undetermined (exit 2)** usually means an optional dependency is missing (e.g.
  parsing HEIC without the `[heic]` extra) — install it and retry.

## Typical use: verify your own capture

```bash
hdrshot capture --display 0 --format uhdr-jpeg --out ./out --preview ./out/p.png
hdrshot check ./out/*.jpg --min-stops 1 || echo "WARNING: capture is not HDR"
```
