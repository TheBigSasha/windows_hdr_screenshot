# Third-Party Notices

HDR Shot is MIT-licensed (see [LICENSE](LICENSE)). It depends on the third-party
components below. This file summarizes their licenses so the project can be
redistributed correctly, especially in bundled binary form (PyInstaller `.exe`,
winget package).

Nothing here changes the license of HDR Shot's own source. It documents the
obligations that attach to the dependencies you install or that a binary bundle
ships.

## Runtime dependencies (base install)

| Component | Role | License | Redistribution notes |
|-----------|------|---------|----------------------|
| [NumPy](https://numpy.org/) | array math | BSD-3-Clause | Permissive; include notice. |
| [Pillow](https://python-pillow.org/) | JPEG/PNG encode, image ops | MIT-CMU (HPND) | Permissive; include notice. |
| [OpenEXR](https://openexr.com/) (Python bindings + OpenEXR/Imath libs) | `.exr` output | BSD-3-Clause | Permissive; include notice. |
| [comtypes](https://github.com/enthought/comtypes) | COM GUID helpers | MIT | Permissive. |

## Optional dependencies

| Component | Extra | License | Redistribution notes |
|-----------|-------|---------|----------------------|
| [PySide6](https://doc.qt.io/qtforpython/) (Qt 6) | `[gui]` | **LGPL-3.0** (Qt Community) | **Dynamic linking required.** A bundled build must keep the Qt shared libraries as separate, replaceable `.dll`/`.pyd` files (PyInstaller onedir does this) and must reproduce the LGPL text. Do **not** static-link Qt. Users must be able to relink against their own Qt build. |
| [pillow-avif-plugin](https://github.com/fdintino/pillow-avif-plugin) | `[avif]` | plugin MIT; bundles **libaom** (BSD-2-Clause) + libavif (BSD-2) | Permissive; AV1 royalty-free (AOMedia patent license). Safe to bundle. |
| [imagecodecs](https://github.com/cgohlke/imagecodecs) | `[avif]` | BSD-3-Clause (bundles libavif/libaom, BSD-2) | Permissive; used for true 10-bit PQ HDR AVIF. Safe to bundle. |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | `[heic]` | Apache-2.0 wrapper over **libheif** (LGPL-3.0) built with **x265** (**GPL-2.0-or-later**) | ⚠️ **See HEIC warning below.** |

## ⚠️ HEIC / HEVC (x265) — do not bundle in the binary release

`pillow-heif`'s standard wheels bundle **libheif built with the x265 HEVC
encoder**, which is **GPL-2.0-or-later**. Linking GPL code into a distributed
binary would make the *entire bundle* effectively GPL, which conflicts with HDR
Shot's MIT intent. HEVC also carries patent-pool considerations for distributed
encoders.

**Policy (per issue #12, Option A):**

- HEIC is an **optional extra**: `pip install hdrshot[heic]`. A user who opts in
  locally accepts the GPL/patent terms of the wheel they install — that is their
  choice and does not affect HDR Shot's license.
- The **official binary release (PyInstaller / winget) does NOT bundle HEIC.**
  If `pillow_heif` (with an HEVC encoder) is not importable, HDR Shot degrades
  gracefully: the CLI reports a clear, actionable error and the GUI greys the
  HEIC format out with an explanatory tooltip.
- HDR is fully covered without HEIC: **UltraHDR JPEG** (the default) and
  **OpenEXR** are the always-available HDR routes, and **AVIF** (`[avif]`, BSD)
  provides an additional royalty-free 10-bit PQ HDR still.

## PySide6 / Qt (LGPL-3.0) — bundling checklist

When the PyInstaller pipeline lands (issue #15), the bundle must:

1. Ship Qt as **separate shared libraries** (onedir layout — never onefile
   static). PyInstaller's onedir mode satisfies this.
2. Include the **LGPL-3.0 license text** and this notices file in the distribution.
3. State that the user may replace the bundled Qt libraries with their own build.

## Regenerating this list

Licenses can be re-verified against the installed environment with:

```bash
pip install pip-licenses
pip-licenses --format=markdown --with-urls --with-license-file
```

Keep this file in sync when dependencies change.
