# Packaging HDR Shot

## Standalone Windows bundle (PyInstaller)

The GitHub Actions **Release** workflow (`.github/workflows/release.yml`) builds
this automatically when you push a `v*` tag. To build locally:

```bash
pip install -e .[gui] pyinstaller
cd packaging
pyinstaller hdrshot.spec --noconfirm
# -> packaging/dist/HDRShot/HDRShot.exe  (onedir bundle)
```

- **onedir, not onefile**: Qt + numpy onefile extraction is slow and trips
  antivirus, and LGPL (PySide6/Qt) requires the Qt libraries to stay as separate,
  replaceable shared libraries — onedir satisfies both.
- **HEIC is not bundled.** `pillow-heif` links x265 (GPL); bundling it would make
  the whole distribution GPL, conflicting with the MIT intent. `imagecodecs` (HDR
  AVIF) is excluded for size. Both remain `pip` extras. See
  [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
- `HDRShot.exe` launches the GUI with no args; with args it runs the CLI
  (`HDRShot.exe info`, `HDRShot.exe capture …`).
- The exe is **unsigned**, so SmartScreen warns on first run. Consider code
  signing before wide distribution.

## Run at login

Handled at runtime by `hdrshot/startup.py` (per-user `HKCU\...\Run` key) and
toggled from **Preferences → "Start HDR Shot when I sign in"**. No installer
needed.

## winget

Manifests are in `winget/` (schema 1.6.0), a `zip` + `portable` nested-installer
package. Per release, update `PackageVersion`, the `InstallerUrl` and its
`InstallerSha256` in the installer manifest, then validate and submit:

```bash
winget validate --manifest packaging/winget
# Optionally test the install locally:
winget install --manifest packaging/winget
# Then open a PR to microsoft/winget-pkgs with the three files under
#   manifests/t/TheBigSasha/HDRShot/<version>/
```

Compute the SHA256 with `Get-FileHash HDRShot-<version>-win64.zip -Algorithm SHA256`.
