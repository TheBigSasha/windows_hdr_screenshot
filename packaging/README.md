# Packaging HDR Shot

## Standalone Windows bundle

The GitHub Actions Release workflow performs this graph:

```text
tag/source identity + reusable quality suite
  -> native package matrix [x64, arm64]
  -> exact ZIP extraction and verification
  -> capabilities / CLI / selftest / dependency inventory
  -> SHA-256 files + generated WinGet manifests + provenance
  -> protected publish job last
```

The onedir bundle contains a windowed `HDRShot.exe` and a console
`hdrshot-cli.exe` over one shared runtime. The frozen
`bundle-capabilities.json` contract intentionally advertises UltraHDR, PNG,
and JPEG only; optional codec providers are excluded explicitly in the spec.
The artifact's capability command and self-test must agree with that manifest.

To build locally:

```bash
pip install -c packaging/constraints-release.txt -e ".[gui]" pyinstaller
cd packaging
pyinstaller hdrshot.spec --noconfirm --clean
```

Use `packaging/verify_bundle.ps1` against the extracted ZIP before treating a
local build as releasable. The release workflow also generates an SPDX JSON
dependency inventory and verifies the SHA-256 of the pinned LGPL text it ships
alongside Qt.

## Installer

`install.ps1` is a versioned-release installer for x64 and ARM64. It derives
one exact asset name from the release tag and architecture, verifies the ZIP
digest, validates both PE headers and the capability contract, runs CLI
self-tests from a staged directory, then swaps the staged tree into place. A
failed swap restores the previous tree; the previous version is retained for
rollback after success.

The installer is intentionally not advertised as a working command until a
tagged release exists. It does not execute a mutable remote script internally.

## WinGet

The checked-in locale metadata is descriptive only. The final installer
manifest is generated in the protected publish job from the exact x64 and ARM64
archive URLs and hashes, then attached to the release as a versioned manifest
bundle. This avoids placeholder hashes and keeps package version, tag, filename,
and release bytes identical.

## Run at login

Handled at runtime by `hdrshot/startup.py` using the per-user Run key and
toggled from Preferences. No installer registration is required.
