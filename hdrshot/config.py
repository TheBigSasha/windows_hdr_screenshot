"""Persisted user configuration (issue #5).

A tiny JSON store at ``%APPDATA%\\hdrshot\\config.json`` (platform-appropriate
elsewhere). Platform-free and importable anywhere; the GUI, hotkeys, save-path and
notification features all read from one :class:`Config`.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field

from .codecs import USER_FORMATS, capability

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "default_format": "auto",          # auto/ultrahdr/exr/heic/png/jpeg/avif-*
    "save_dir": "",                    # "" => Pictures/Screenshots (known folder)
    "filename_template": "{hdr}Screenshot {date} {time}",
    "hotkey_region": "ctrl+shift+h",
    "hotkey_screen": "ctrl+shift+g",
    "gainmap_quality": 90,             # UltraHDR base/gainmap JPEG quality
    "gainmap_downscale": 1,            # gain-map resolution divisor (1 = full res)
    "copy_to_clipboard": False,        # also copy the SDR image on save
    "notifications": True,             # post-capture toast
    "run_at_login": False,             # start with Windows
}

_VALID_FORMATS = set(USER_FORMATS)


def _type_ok(value, default) -> bool:
    """Is ``value`` an acceptable stand-in for ``default``'s type? Exact-ish:
    bool is not an int here, but an int is fine where a float would be."""
    want = type(default)
    if want is bool:
        return isinstance(value, bool)
    if want is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if want is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, want)


def config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "hdrshot")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


@dataclass
class Config:
    data: dict = field(default_factory=lambda: dict(DEFAULTS))
    path: str = ""

    @classmethod
    def load(cls, path: str | None = None) -> Config:
        path = path or config_path()
        data = dict(DEFAULTS)
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # Keep only known keys with the expected type; a hand-edited or
                # corrupt value must never be able to crash the app at startup.
                for k, default in DEFAULTS.items():
                    if k not in loaded:
                        continue
                    v = loaded[k]
                    if _type_ok(v, default):
                        data[k] = v
                    else:
                        log.warning("config key %r has %s, expected %s; using default",
                                    k, type(v).__name__, type(default).__name__)
        except FileNotFoundError:
            log.debug("no config at %s; using defaults", path)
        # ValueError covers json.JSONDecodeError AND UnicodeDecodeError
        # (a UTF-16/garbage file must not kill the GUI).
        except (ValueError, OSError) as e:
            log.warning("config load failed (%s); using defaults", e)
        return cls(data=data, path=path)

    def save(self, path: str | None = None) -> None:
        path = path or self.path or config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, path)         # atomic
        self.path = path
        log.debug("config saved to %s", path)

    def get(self, key: str):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    # -- validated accessors ------------------------------------------------ #
    def default_format(self) -> str:
        f = self.get("default_format")
        return f if f in _VALID_FORMATS else "auto"

    def effective_default_format(self) -> str:
        """Return a configured format only when its explicit profile is usable.

        ``avif`` remains a compatibility alias and is resolved per scene by the
        pipeline. Optional profiles that are missing or broken are reconciled to
        ``auto`` so a stale config cannot make the GUI fail on every capture.
        """
        fmt = self.default_format()
        if fmt in {"auto", "ultrahdr", "png", "jpeg", "avif"}:
            return fmt
        cap = capability(fmt)
        if cap.available:
            return fmt
        log.warning("configured format %r is %s; using auto", fmt, cap.status)
        return "auto"

    def resolved_save_dir(self) -> str:
        """The configured save dir, or the default Pictures/Screenshots folder.
        Falls back to the default if the configured folder can't be created
        (unplugged drive, bad path) — a save must never crash on this."""
        d = (self.get("save_dir") or "").strip()
        if d:
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError as e:
                log.warning("configured save_dir %r unusable (%s); using default", d, e)
        from .core import pipeline
        return pipeline.default_save_dir()
