# Changelog

All notable changes to HDR Shot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.2] - 2026-08-18

### Fixed
- The post-public release verifier now passes the repository explicitly to
  `gh release download`, so it works in the checkout-free publish job.

## [0.4.1] - 2026-08-18

### Added
- Automatic post-selection saving, enabled by default and configurable in
  Preferences, so a capture produces a file without a second Save click.
- A per-user single-instance guard: relaunching HDR Shot activates the resident
  process instead of creating conflicting hotkey and Desktop Duplication owners.

### Changed
- The region selector now uses the native Windows compositor preview rather
  than CPU-tone-mapping the entire FP16 desktop twice. D3D11 capture remains
  native, while full-quality encoding stays asynchronous.
- Desktop Duplication now negotiates the required BGRA8 scan-out fallback as
  well as FP16 scRGB and 10-bit RGB, improving Qualcomm/ARM64 driver compatibility.
- Capture startup no longer includes a fixed 140 ms delay. Worker lifetimes and
  selector focus are held explicitly, and the UI reports capture progress,
  timeouts, Desktop Duplication denial, and session exhaustion visibly.
- The native setup app closes only the exact installed HDR Shot process during
  upgrades and provides normal confirmation/success/error dialogs.

### Fixed
- Capture button and global-hotkey actions no longer hide the app and then fail
  silently when the selector cannot be created or Windows denies capture.
- Successful saves no longer crash the GUI callback by trying to mutate the
  immutable typed encoder result, and completion signals cannot disappear with
  a short-lived worker.

## [0.4.0] - 2026-08-09

### Fixed
- The Windows installer is now windowed, so installation does not flash a terminal.
- Per-user Start Menu registration now uses the indexed Programs path and is
  verified to target the installed GUI on native x64 and ARM64 runners.

## [0.3.0] - 2026-08-08

### Added
- Native x64 and ARM64 release lanes with a publish-last verification graph.
- A structured codec capability registry shared by the pipeline, UI, CLI,
  preferences, configuration, self-test, and agent JSON.
- Strict explicit AVIF profiles: HDR AVIF can no longer silently become SDR.
- Frozen-bundle capability contracts, extracted-ZIP smoke tests, SPDX dependency
  inventories, generated WinGet manifests, checksums, and build provenance.
- Transactional staged installer upgrades with architecture/version validation
  and rollback retention.

### Changed
- Optional codec probes distinguish missing providers from broken native loads;
  global Pyright missing-import diagnostics remain enabled.
- The official frozen bundle advertises only UltraHDR, PNG, and JPEG, with every
  optional provider excluded explicitly.
- Viewer compatibility language is limited to claims that can be reproduced in
  the target environment; production installation is not advertised before a
  verified release exists.

## [0.2.0] - 2026-07-05

### Added
- **Capture-backend seam** (`hdrshot.backends`): a `CaptureBackend` protocol with a
  lazy Win32 implementation. The pure package (`core`, `encoders`, `agentcli`)
  now imports and runs on any OS — enabling cross-platform CI and library reuse.
- **Machine-readable / agent CLI** (issue #7): `--json` on `info`/`full`/`region`;
  new `hdrshot parse <file>` (report HDR metadata + optional SDR preview),
  `hdrshot capture` (one-shot: true-HDR file + SDR preview + JSON), and
  `hdrshot check` (assert an image contains real HDR; exits 0/1/2).
- **Agent skills** and documentation for automated HDR screenshots (issue #8).
- **True 10-bit BT.2020 PQ HDR AVIF** output via the optional `[avif-hdr]` extra
  (imagecodecs / libavif) (issue #10).
- **ISO 21496-1 gain-map metadata** co-embedded in UltraHDR output for Apple
  Photos / Preview / Quick Look HDR (issue #9).
- Logging throughout the package plus a `--verbose` CLI flag (issue #17).
- Multi-monitor region **stitching** for rectangles that span displays (issue #17).
- Automated tests (`pytest`), `ruff`, `pyright`, and GitHub Actions CI on Windows
  and Ubuntu (issue #13).
- `LICENSE` (MIT) and `THIRD_PARTY_NOTICES.md`; optional extras `[gui]`, `[heic]`,
  `[avif-hdr]`, `[all]`, `[dev]` (issue #12, #15).
- Persisted configuration + Preferences dialog, save templates, global hotkeys and
  post-capture toasts (issues #2, #3, #4, #5).
- Overlay window-capture mode, magnifier loupe with nits readout, timed capture
  and arrow-key nudge (issue #6).
- PyInstaller onedir bundle (windowed `HDRShot.exe` + console `hdrshot-cli.exe`),
  tag-triggered release workflow, winget manifest and run-at-login (issue #11).

### Changed
- Package restructured into `core/` (platform-free), `encoders/`, `backends/`, `ui/`.
- HEIC is now an **optional extra** (`pip install hdrshot[heic]`) and degrades
  gracefully when its x265/GPL encoder isn't installed (issue #12).
- Version is single-sourced from `hdrshot/__init__.py` via dynamic metadata.
- Save path uses the real Pictures Known Folder (OneDrive-aware); filename
  collisions get a numbered suffix (issue #17).

### Fixed
- **EXR output was corrupt** beyond the first rows of any real capture: the writer
  handed the OpenEXR binding strided channel views, which it reads linearly. Now
  written from contiguous per-channel copies and covered by a pixel-exact test.
- Window-capture mode always selected the whole screen (the hit test saw the
  overlay itself); it now skips this process's windows in z-order.
- `parse`/`check`/`capture` no longer dump tracebacks on missing/corrupt files or
  invalid regions/display indexes — structured JSON errors with documented exit
  codes (see docs/AGENTS.md).
- 180°-rotated displays were never rotated into desktop orientation.
- One failing output no longer aborts a multi-monitor capture.
- Corrupt, non-UTF-8 or wrong-typed `config.json` values fall back to defaults
  instead of crashing the GUI at startup.
- `gainmap_quality` / `gainmap_downscale` preferences are actually applied.
- UltraHDR/ISO capacities are kept strictly separated (no divide-by-zero weights
  in decoders on uniformly-boosted captures); non-finite EXR pixels no longer
  produce invalid JSON from `parse`.
- Filename templates are fully sanitized (path separators, reserved device names)
  and a literal `{{n}}` can no longer hang the save loop.
- Silent wrong-monitor fallbacks in the pipeline now raise a clear error (issue #17).
- `assert`s in the UltraHDR encoder replaced with real exceptions that survive
  `python -O`; MPF offsets derived from the built segment length (issue #17).
- Rotated (portrait) displays are rotated into desktop orientation so crops line
  up (issue #17, best-effort).

## [0.1.0] - 2026-07-03

### Added
- Initial release: true HDR desktop capture on Windows via DXGI Desktop
  Duplication in scRGB FP16, with UltraHDR JPEG / OpenEXR / 10-bit PQ HEIC output
  and PNG/JPEG/AVIF SDR fallbacks. GUI (tray + region overlay + preview) and CLI
  (`info` / `full` / `region` / `selftest`).
