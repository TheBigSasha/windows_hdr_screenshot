"""System-wide capture hotkeys (issue #2).

Parses hotkey strings like ``"ctrl+shift+h"`` and, on Windows, registers them with
``RegisterHotKey`` so they fire from any foreground app. The string parser is pure
and platform-free (unit-tested); registration is Win32 and integrates with Qt via
a native event filter that catches ``WM_HOTKEY``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

# Win32 modifier flags for RegisterHotKey.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

# Named virtual-key codes (letters/digits are computed).
_VK_NAMED = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "printscreen": 0x2C, "prtsc": 0x2C, "snapshot": 0x2C,
}


class HotkeyError(ValueError):
    """A hotkey string could not be parsed."""


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``"ctrl+shift+h"`` -> ``(modifier_flags, virtual_key_code)``.

    Raises :class:`HotkeyError` on an empty, modifier-only, or unknown key spec.
    """
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError(f"empty hotkey: {spec!r}")
    mods = 0
    key = None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif key is None:
            key = p
        else:
            raise HotkeyError(f"more than one non-modifier key in {spec!r}")
    if key is None:
        raise HotkeyError(f"hotkey {spec!r} has no non-modifier key")
    vk = _vk_for(key)
    if vk is None:
        raise HotkeyError(f"unknown key {key!r} in {spec!r}")
    return mods | MOD_NOREPEAT, vk


def _vk_for(key: str) -> int | None:
    if key in _VK_NAMED:
        return _VK_NAMED[key]
    if len(key) == 1:
        c = key.upper()
        if "A" <= c <= "Z" or "0" <= c <= "9":
            return ord(c)
    return None


class HotkeyManager:
    """Registers global hotkeys and routes ``WM_HOTKEY`` to callbacks (Windows).

    Non-Windows: construction is a no-op and :meth:`register` returns False, so the
    GUI degrades cleanly.
    """

    def __init__(self):
        import sys
        self._enabled = sys.platform == "win32"
        self._by_id: dict[int, tuple[str, Callable[[], None]]] = {}
        self._next_id = 0xB000
        self._filter = None
        if self._enabled:
            self._install_filter()

    def _install_filter(self) -> None:
        import ctypes

        from PySide6.QtCore import QAbstractNativeEventFilter
        from PySide6.QtWidgets import QApplication

        mgr = self

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                if event_type == b"windows_generic_MSG":
                    msg = ctypes.wintypes.MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY:
                        cb = mgr._by_id.get(msg.wParam)
                        if cb:
                            cb[1]()
                            return True, 0
                return False, 0

        # ctypes.wintypes.MSG needs importing on some builds.
        import ctypes.wintypes  # noqa: F401
        self._filter = _Filter()
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def register(self, spec: str, callback: Callable[[], None]) -> bool:
        """Register ``spec`` -> ``callback``. Returns True on success.

        Reports conflicts/parse errors via the logger and a False return rather
        than raising, so one bad hotkey doesn't take the app down.
        """
        if not self._enabled:
            return False
        try:
            mods, vk = parse_hotkey(spec)
        except HotkeyError as e:
            log.warning("invalid hotkey %r: %s", spec, e)
            return False
        import ctypes
        hk_id = self._next_id
        self._next_id += 1
        if not ctypes.windll.user32.RegisterHotKey(None, hk_id, mods, vk):
            log.warning("could not register hotkey %r (already in use?)", spec)
            return False
        self._by_id[hk_id] = (spec, callback)
        log.debug("registered hotkey %r as id %d", spec, hk_id)
        return True

    def unregister_all(self) -> None:
        if not self._enabled:
            return
        import ctypes
        for hk_id in list(self._by_id):
            ctypes.windll.user32.UnregisterHotKey(None, hk_id)
        self._by_id.clear()
