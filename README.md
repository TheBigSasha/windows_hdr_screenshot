# HDR Shot

**True HDR screenshots on Windows.** When an HDR display is connected and HDR is
enabled, HDR Shot captures the desktop in the same wide, linear color space
Windows composites in (scRGB FP16) and saves it *with the highlights intact* —
as a gain-map **UltraHDR JPEG** (the cross-platform, macOS-style "HDR inside a
JPEG"), a lossless **OpenEXR**, or a 10-bit **BT.2020 PQ HEIC**. On an SDR display
it falls back to ordinary **PNG / JPEG / AVIF**. It has a simple, modern
capture UI in the spirit of the Windows Snipping Tool and the macOS screenshot
tools.

<p align="center"><em>Region select · whole-screen · multi-monitor · HDR/SDR auto-detect</em></p>

---

## Why this exists

Windows' built-in Snipping Tool and `Print Screen` only ever give you an 8-bit
SDR image — even on an HDR monitor, the bright highlights are clipped to white.
macOS has captured real HDR screenshots (HEIC + gain map) for a while. HDR Shot
brings that to Windows: it grabs the actual floating-point HDR framebuffer and
preserves everything above SDR white.

## Features

- **Real HDR capture** via DXGI Desktop Duplication in `R16G16B16A16_FLOAT`
  (scRGB, `1.0` = 80 nits). Highlights above paper white are preserved, not
  clipped.
- **Four HDR-aware outputs**
  - **UltraHDR JPEG** — an SDR base JPEG + a gain map (Google UltraHDR v1: MPF +
    `hdrgm` XMP). Opens *everywhere*; renders in HDR on Chrome, Android and the
    Windows Photos app. This is the default for HDR content.
  - **OpenEXR** — lossless linear scRGB (half-float). For editing / archival.
  - **HEIC** — 10-bit BT.2020 PQ (HDR10-style still). The container macOS uses.
  - Plus **PNG / JPEG / AVIF** for SDR.
- **Auto format** — picks UltraHDR for HDR content, PNG for SDR, macOS-style.
- **Smart multi-monitor** — enumerates every output, matches Qt screens to their
  physical FP16 buffers, and handles mixed per-monitor DPI (selection maps
  proportionally to physical pixels, so crops are exact).
- **HDR-aware UI** — shows each display's live HDR on/off state and, when HDR is
  off but supported, reminds you to toggle it (`Win`+`Alt`+`B`).
- **Simple capture UX** — a dark toolbar + tray icon; dimmed freeze-frame overlay
  with a rubber-band region selector, live dimensions, whole-screen and
  cancel; then a preview card with an HDR/SDR badge, format picker, Copy and Save.
- **Robust on idle screens** — Desktop Duplication normally only yields a frame
  when the desktop changes; HDR Shot forces a repaint so a one-shot grab always
  works, even on a static desktop.

## Install & run

Requires Windows 10 1803+ / Windows 11 and Python 3.10+.

```bat
run.bat
```

`run.bat` creates a virtual environment and installs dependencies on first run,
then launches the app. To do it manually:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m hdrshot
```

## Usage

**GUI:** run `run.bat` (or `python -m hdrshot`). Click **Capture Region** (drag a
box, or double-click / `Enter` for the whole screen, `Esc` to cancel), then pick
a format and **Save**. Files go to `Pictures\Screenshots`.

**Command line:**

```bat
python -m hdrshot info                     REM list displays + HDR status
python -m hdrshot full                     REM capture the primary display (auto format)
python -m hdrshot full --all --format exr  REM every display, as EXR
python -m hdrshot region 100 100 800 600   REM a virtual-desktop rectangle
python -m hdrshot selftest                 REM synthesise HDR, write every format, verify
```

## Getting real HDR output

HDR content is only present when **Windows HDR is turned on** for the display
(`Settings → System → Display → Use HDR`, or `Win`+`Alt`+`B`). With HDR on, the
desktop composites in FP16 and HDR Shot captures highlights above SDR white. With
HDR off, the desktop is 8-bit SDR — screenshots are correct but contain no HDR,
so HDR Shot saves standard formats. The app shows the current state in its header.

`python -m hdrshot selftest` demonstrates and verifies the full HDR encode path
regardless of your display, by synthesizing a known HDR scene (a nit ramp to
1600 nits plus saturated HDR swatches) and writing/validating every format.

## How it works

```
displays.py   QueryDisplayConfig + EnumDisplayMonitors  -> per-output HDR state,
                                                            bit depth, SDR white, rects
capture.py    D3D11 + DXGI Desktop Duplication (ctypes)  -> scRGB FP16 per output
                (DuplicateOutput1 requesting R16G16B16A16_FLOAT; falls back to
                 decoding 8/10-bit SDR surfaces; forces a repaint so idle
                 screens still yield a frame)
color.py      scRGB <-> SDR, sRGB transfer, PQ/BT.2020, HDR-content detection
encoders/     exr · ultrahdr (gain map) · heic (PQ) · sdr (png/jpeg/avif)
pipeline.py   capture -> crop region -> detect HDR -> choose format -> encode
ui/           app (toolbar + tray) · overlay (region select) · preview (save card)
```

The whole capture stack is pure `ctypes`/`comtypes` — no Windows SDK or C
compiler required. COM methods are invoked directly by vtable index.

## Notes & limitations

- **HDR AVIF:** true 10-bit PQ HDR AVIF is not reachable from the Python AVIF
  encoders available as wheels (they're 8-bit, no CICP control), so AVIF here is
  SDR. Use **UltraHDR**, **EXR** or **HEIC** for HDR. (This is the one place the
  requested "HDR AVIF" isn't available; the other two HDR routes fully cover it.)
- **Apple Photos:** UltraHDR (`hdrgm`) is recognized by Chrome / Android /
  Windows Photos. Apple apps read ISO 21496-1 gain-map metadata instead; adding
  that co-embedded block is a planned enhancement (the gain map itself is already
  computed correctly).
- Rotated displays are captured in their native orientation (rotation metadata is
  recorded but not yet re-applied).

## License

MIT.
