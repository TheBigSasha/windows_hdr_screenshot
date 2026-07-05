# Contributing to HDR Shot

Thanks for your interest! This project turns a working prototype into a
releasable, portable, tested product — see the
[architecture roadmap (#18)](https://github.com/TheBigSasha/windows_hdr_screenshot/issues/18).

## Development setup

```bash
git clone https://github.com/TheBigSasha/windows_hdr_screenshot
cd windows_hdr_screenshot
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]   # gui + heic + avif-hdr + test tools
```

## Checks (must pass before a PR)

```bash
.venv\Scripts\python -m pytest        # unit tests
.venv\Scripts\python -m ruff check .  # lint + import order
.venv\Scripts\python -m pyright       # type check (pure layers)
.venv\Scripts\python -m hdrshot selftest   # synthetic HDR encode path
```

CI (`.github/workflows/ci.yml`) runs the same on **Windows** (full: lint, types,
tests, selftest artifacts) and **Ubuntu** (the pure math/encoder tests — proving
the cross-platform seam). Real DXGI capture can't run in CI (no interactive
desktop), which is why the synthetic `selftest` path exists.

## Architecture

```
hdrshot/
  core/         color science, pipeline, DisplayInfo/MonitorCapture types  (pure, cross-platform)
  encoders/     ultrahdr · iso_gainmap · exr · heic · avif_hdr · sdr        (pure)
  backends/     CaptureBackend protocol + win32/ (lazy ctypes)             (platform seam)
  ui/           Qt shell — app · overlay · preview · settings · toast · workers
  agentcli.py   --json / parse / check / capture (agent-facing)
  config.py · hotkeys.py · startup.py
```

Principles (from the roadmap):

- **One platform seam.** Only `backends/win32/` touches ctypes/`windll`, imported
  lazily via `backends.get_backend()`. The pure layers must import on any OS —
  `tests/test_import_isolation.py` enforces this, so never add a top-level
  `import` of a win32 module to `core`/`encoders`/`agentcli`.
- **Loud failures over silent fallbacks.** Raise (CLI) or surface (GUI) rather
  than saving the wrong monitor. See `pipeline.RegionError`.
- **The GUI is a thin client** of the same `pipeline` the CLI uses — keep
  `pipeline` Qt-free.

### The fragile part: raw COM

`backends/win32/com.py` calls COM methods by **vtable index** (no Windows SDK
needed). A wrong index corrupts memory rather than raising. Every index lives in
the documented `VTBL` table with its interface + method; add new ones there with a
comment naming the header they come from.

### Byte-exact formats

`encoders/ultrahdr.py` (MPF + hdrgm XMP) and `encoders/iso_gainmap.py` (ISO
21496-1) write hand-computed byte offsets/layouts. They are covered by round-trip
tests (`tests/test_ultrahdr.py`, `tests/test_iso_gainmap.py`) that re-parse our own
output — extend those when touching the containers.

## Adding a capture backend

Implement the `CaptureBackend` protocol (`backends/base.py`) in a new
`backends/<platform>/` package and wire it into `get_backend()`. macOS
ScreenCaptureKit EDR is the realistic next target. The pipeline, encoders and CLI
need no changes.

## Commit / PR conventions

- Keep changes focused; match the surrounding code's style and comment density.
- Reference the relevant issue number.
- Update `CHANGELOG.md` under `[Unreleased]`.
