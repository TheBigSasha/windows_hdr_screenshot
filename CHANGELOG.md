# Changelog

All notable changes to HDR Shot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

### Changed
- Package restructured into `core/` (platform-free), `encoders/`, `backends/`, `ui/`.
- HEIC is now an **optional extra** (`pip install hdrshot[heic]`) and degrades
  gracefully when its x265/GPL encoder isn't installed (issue #12).
- Version is single-sourced from `hdrshot/__init__.py` via dynamic metadata.
- Save path uses the real Pictures Known Folder (OneDrive-aware); filename
  collisions get a numbered suffix (issue #17).

### Fixed
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
