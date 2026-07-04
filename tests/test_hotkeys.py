"""Hotkey string parsing (issue #2) — pure, platform-free."""
from __future__ import annotations

import pytest

from hdrshot import hotkeys
from hdrshot.hotkeys import MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, HotkeyError, parse_hotkey


def test_parse_basic():
    mods, vk = parse_hotkey("ctrl+shift+h")
    assert mods & MOD_CONTROL
    assert mods & MOD_SHIFT
    assert mods & MOD_NOREPEAT
    assert vk == ord("H")


def test_parse_function_key():
    _, vk = parse_hotkey("alt+f5")
    assert vk == 0x74


def test_parse_named_key():
    _, vk = parse_hotkey("ctrl+printscreen")
    assert vk == 0x2C


def test_parse_case_insensitive_and_spaces():
    a = parse_hotkey("CTRL + Shift + G")
    b = parse_hotkey("ctrl+shift+g")
    assert a == b


@pytest.mark.parametrize("bad", ["", "ctrl+shift", "ctrl+", "a+b", "ctrl+nope"])
def test_parse_rejects_bad(bad):
    with pytest.raises(HotkeyError):
        parse_hotkey(bad)


def test_manager_disabled_is_noop():
    # A disabled manager (non-Windows) must not raise and register() returns False.
    mgr = hotkeys.HotkeyManager.__new__(hotkeys.HotkeyManager)
    mgr._enabled = False
    mgr._by_id = {}
    assert mgr.register("ctrl+shift+h", lambda: None) is False
    mgr.unregister_all()  # also a no-op
