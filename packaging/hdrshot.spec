# PyInstaller spec for the onedir Windows bundle.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("hdrshot.encoders")
    + collect_submodules("hdrshot.backends")
    + ["hdrshot.agentcli", "hdrshot.codecs", "hdrshot.config",
       "hdrshot.hotkeys", "hdrshot.startup", "hdrshot.ui.single_instance"]
)

# Keep every optional provider out explicitly. The frozen capability manifest,
# not ambient imports in a developer environment, is the artifact contract.
excludes = [
    "OpenEXR", "pillow_avif", "pillow_heif", "imagecodecs",
    "tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtQuick",
    "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtCharts",
]

a = Analysis(
    ["hdrshot_launcher.py"],
    pathex=[".."],
    binaries=[],
    datas=[("bundle-capabilities.json", "hdrshot")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="HDRShot", debug=False,
    strip=False, upx=False, console=False, icon=None,
)
exe_cli = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="hdrshot-cli", debug=False,
    strip=False, upx=False, console=True, icon=None,
)
coll = COLLECT(exe, exe_cli, a.binaries, a.datas, strip=False, upx=False,
               name="HDRShot")
