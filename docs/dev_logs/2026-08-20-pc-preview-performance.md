# PC setup and preview performance - 2026-08-20

## Scope

Set up the source build on Sasha's Windows HDR workstation, measure the current
pipeline, preserve the 0.4.1 ARM responsiveness work, and remove work that made
the preview slower without producing verified HDR presentation.

## Machine

- Windows 11 Pro build 26200, x64
- NVIDIA GeForce RTX 5070 Ti, driver 610.47, 16 GB VRAM
- Intel Graphics 32.0.101.8132
- M27P6 at 3840x2160, RGB 10 bpc, Windows HDR enabled
- Python 3.12.10, PySide6 6.11.2

DXGI enumeration confirms that the RTX owns `\\.\DISPLAY5`; Intel and Microsoft
Basic Render expose no outputs. Capture must continue to create its D3D11 device
from the adapter that owns each output; no global GPU-preference override is needed.

## Baseline

The source environment was installed with GUI, development, and EXR extras.
Before changes:

- Tests: 166 passed, 2 skipped
- Ruff: pass
- Pyright: pass
- Resident DXGI capture-only samples: 322.48, 228.35, 222.05, 219.13,
  213.99, 215.63 ms
- Warm full-screen UltraHDR CLI: approximately 1.56 seconds
- 4K CPU scRGB-to-BT.2020/PQ preview conversion: approximately 779 ms warm
- 4K UltraHDR encode: approximately 787 ms warm, already asynchronous

The 0.4.1 performance work remains the right foundation: no fixed 140 ms delay,
capture on a worker, native compositor selector images, delayed FP16-to-FP32
promotion, and asynchronous encoding.

## Correction

A Qt color-space request and 16-bit OpenGL buffer do not prove that Windows
created an HDR swapchain. The controlled OpenGL window was visibly SDR and a
captured validation frame reported only 480-nit paper white. The experimental
fragment shader was therefore rejected; PQ values must not be sent to an
unverified SDR surface.

The post-capture OpenGL preview added in 0.4.5 and 0.4.7 was removed. The GUI
uses its existing SDR compositor preview. Saved EXR and UltraHDR data paths are
unchanged.

## Result

Native-4K PreviewWindow construction with the compositor preview:

- Cold: 66.51 ms
- Warm samples: 22.32, 22.53, 23.52, 23.01, 22.11 ms
- Warm median: 22.53 ms

This removes the approximately 779 ms synchronous PQ conversion from the GUI
hot path, a roughly 35x reduction for preview construction on this machine.

The capture profiler also found that the backend created devices for all three
adapters before checking whether they had outputs. A cold trace spent about
224 ms in device creation and about 60 ms in FP16 acquire/readback. Enumerating
outputs first leaves the display-owning adapter path unchanged and skips Intel
and Microsoft Basic Render device creation on this PC.

- Before warm capture median: 233.91 ms (six samples after one cold capture)
- After warm capture median: 191.62 ms (eight samples after one cold capture)
- After cold capture: 226.06 ms, down from 328.47 ms in the paired run

The warm median improved by about 18%; the cold sample improved by about 31%.

## Verification

- Focused responsive UI tests: pass
- Full pytest suite: pass
- Ruff: pass
- Pyright: pass
- hdrshot info --json: one 3840x2160 display, HDR enabled, 10 bpc

The HDR-capable duplication path temporarily returned `DXGI_ERROR_UNSUPPORTED`.
A control probe proved standard Desktop Duplication still worked, all
`DuplicateOutput1` format lists failed, and the RTX owned the output. Cycling
Windows HDR off/on recreated the mode; DPI-aware 4K FP16 scRGB capture then
succeeded again. The captured desktop peaked at 483 nits, so it is not evidence
of a 1600-nit source.

Final verification:

- 167 tests passed, 2 optional-codec tests skipped
- Ruff and Pyright passed; `git diff --check` passed
- Real 4K FP16 capture wrote valid EXR and UltraHDR files
- Release-like x64 ZIP passed `verify_bundle.ps1`; 97 PE files inspected
- Frozen self-test passed UHDR JPEG, PNG, and JPEG capability contract
- Local per-user installer was built from that verified ZIP
- Installed to `%LOCALAPPDATA%\Programs\HDRShot`
- Start Menu shortcut target and working directory verified
- Installed resident process stayed responsive
- Installed `Ctrl+Shift+G` hotkey captured and auto-saved a 4K screenshot
- Run at login remains disabled
