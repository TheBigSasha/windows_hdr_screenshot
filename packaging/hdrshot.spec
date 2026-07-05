# PyInstaller spec — onedir build of HDR Shot.
#
# onedir (not onefile): Qt + numpy onefile extraction is slow and antivirus-prone,
# and LGPL (PySide6/Qt) requires the Qt libraries to remain separate, replaceable
# shared libraries — which onedir satisfies. The HEIC (x265/GPL) encoder is
# deliberately NOT bundled (see THIRD_PARTY_NOTICES.md); HEIC stays a pip extra.
#
# Build:  pyinstaller packaging/hdrshot.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    # Encoders / backends are imported lazily by string in the pipeline, so make
    # sure the analysis keeps them.
    collect_submodules("hdrshot.encoders")
    + collect_submodules("hdrshot.backends")
    + ["hdrshot.agentcli", "hdrshot.config", "hdrshot.hotkeys", "hdrshot.startup"]
)

# Keep the bundle lean and license-clean.
excludes = [
    "pillow_heif",       # x265 / GPL — never bundled
    "imagecodecs",       # large; HDR AVIF stays an opt-in pip extra
    "tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtQuick",
    "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtCharts",
]

a = Analysis(
    ["hdrshot_launcher.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HDRShot",
    debug=False,
    strip=False,
    upx=False,
    console=False,           # GUI app: no console window
    icon=None,
)
# Console-subsystem twin for scripts/agents: a windowed exe cannot reliably
# deliver stdout (file redirects come up empty) or exit codes, so the JSON
# agent CLI gets its own bootloader sharing the same bundle.
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hdrshot-cli",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="HDRShot",
)
