"""Run-at-login toggle (issue #11): the per-user ``Run`` registry key on Windows.

No admin rights needed — writes ``HKCU\\...\\Run\\HDRShot``. All functions are
no-ops / False off Windows so the GUI can call them unconditionally.
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "HDRShot"


def _launch_command() -> str:
    """Best command to relaunch the app at login.

    Prefers a frozen exe (PyInstaller); otherwise ``pythonw -m hdrshot`` so no
    console window flashes.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" -m hdrshot'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Enable/disable launch at login. Returns the resulting state."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        log.debug("run-at-login set to %s", enabled)
        return enabled
    except OSError as e:
        log.warning("could not update run-at-login: %s", e)
        return is_enabled()
