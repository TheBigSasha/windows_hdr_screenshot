# HDR Shot

HDR Shot captures the Windows desktop from the DXGI scRGB floating-point
framebuffer, preserving highlights above SDR white. It writes a gain-map
UltraHDR JPEG by default for HDR content and PNG for SDR content, with explicit
profiles for linear EXR, single-rendition PQ AVIF/HEIC, and SDR AVIF.

## Features

- Native Windows HDR capture with multi-monitor and region stitching.
- UltraHDR JPEG with MPF, `hdrgm` XMP, and ISO 21496-1 gain-map metadata.
- Lossless linear OpenEXR, 10-bit BT.2020 PQ HEIC, 8-bit SDR AVIF, and 10-bit
  PQ AVIF through opt-in extras.
- A single runtime capability registry used by the pipeline, CLI, GUI,
  preferences, configuration, self-test, and agent JSON.
- Machine-readable `parse`, `check`, `capture`, and `capabilities` commands.

The SDR base of an UltraHDR JPEG is broadly readable. HDR rendering depends on
the viewer and display; claims about a particular viewer should be verified with
the versioned fixtures in the relevant environment.

## Install and run

Requires Windows 10 1803+ / Windows 11 and Python 3.10+.

### Production install

The release workflow publishes production artifacts only after native x64 and
ARM64 builds pass clean extracted-ZIP verification. Until a verified release is
available, use the source workflow below. This keeps a missing `/releases/latest`
endpoint from being presented as a working installer.

Each release contains architecture-specific `HDRShot-<version>-<arch>-setup.exe`
installers plus the underlying `HDRShot.exe`/`hdrshot-cli.exe` bundles, exact
capability contracts, SHA-256 files, dependency SBOMs, and build provenance. The reviewed
`install.ps1` accepts an explicit `-InstallDir`, stages and validates a new
version, then swaps it transactionally while retaining the previous version for
rollback.

### Development environment

```bat
run.bat
```

Manual equivalent:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[gui]"
.venv\Scripts\python -m hdrshot
```

### Optional profiles

```bash
pip install .
pip install ".[gui]"
pip install ".[exr]"
pip install ".[heic]"
pip install ".[avif-sdr]"
pip install ".[avif-hdr]"
pip install ".[all]"
```

Canonical profiles carry their representation: `uhdr-jpeg` is a gain-map JPEG,
`pq-avif` and `pq-heic` are single-rendition PQ outputs, and `avif-sdr` is
ordinary SDR. The legacy aliases `ultrahdr`, `avif`, and `heic` retain their
historical meanings (`uhdr-jpeg`, `pq-avif`, and `pq-heic` respectively). If an
explicit profile is unavailable, the command fails with a structured
`CodecUnavailableError`; it never silently changes representation.

## Usage

```bat
python -m hdrshot info
python -m hdrshot full --all --format auto
python -m hdrshot region 100 100 800 600 --format uhdr-jpeg
python -m hdrshot capabilities
python -m hdrshot selftest
```

For agents:

```bash
hdrshot capabilities --json
hdrshot capture --display 0 --format uhdr-jpeg --out ./shots --preview ./shots/p.png
hdrshot parse ./shots/shot.jpg
hdrshot check ./shots/shot.jpg --min-stops 1
```

`capabilities` reports each explicit profile as `available`, `missing`,
`broken`, or `excluded`, including the provider, version, representation, and
reason. Capture JSON records `requested_profile`, `actual_profile`, provider,
and capability state. See [docs/AGENTS.md](docs/AGENTS.md) for the complete
machine-facing contract.

## Formats

| Profile | Representation | Dependency |
| --- | --- | --- |
| `uhdr-jpeg` | gain-map JPEG | bundled (`ultrahdr` is a legacy alias) |
| `exr` | linear half-float | `[exr]` |
| `pq-heic` | 10-bit BT.2020 PQ | `[heic]` (x265/GPL; not bundled) |
| `avif-sdr` | 8-bit SDR AVIF | `[avif-sdr]` |
| `pq-avif` | 10-bit BT.2020 PQ AVIF | `[avif-hdr]` |
| `png`, `jpeg` | SDR | bundled |

The GUI disables profiles that are unavailable and explains whether the provider
is missing, broken, or intentionally excluded from the frozen bundle.

## Development checks

```bash
pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m pyright
```

The release self-test is stricter than generic source CI: a frozen artifact
contains `bundle-capabilities.json`, and its advertised profile set must match
the executable's runtime registry exactly. Every advertised profile is encoded,
read back, and checked for `requested_profile == actual_profile`.

## Project layout

```text
hdrshot/codecs/       structured capability model and registry
hdrshot/core/         color science, pipeline, and capture result types
hdrshot/encoders/     UltraHDR, EXR, HEIC, AVIF, and SDR adapters
hdrshot/backends/     platform seam and Windows DXGI implementation
hdrshot/ui/           optional Qt GUI
docs/AGENTS.md        JSON schemas and exit-code contract
packaging/            PyInstaller, verification, SBOM, and WinGet generation
```

## Notes

- HEIC is intentionally excluded from official binary bundles because the
  common wheel includes an x265/GPL encoder. See `THIRD_PARTY_NOTICES.md`.
- The frozen release bundle intentionally contains UltraHDR, PNG, and JPEG.
  Its capability manifest is the source of truth; it cannot be extended with
  `pip install` after packaging.
- EXR provides exact linear luminance for analysis. UltraHDR headroom is
  relative to SDR white; use `gainmap_max_stops` or capture EXR for exact nits.
