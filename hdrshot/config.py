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

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "default_format": "auto",          # auto/ultrahdr/exr/heic/png/jpeg/avif
    "save_dir": "",                    # "" => Pictures/Screenshots (known folder)
    "filename_template": "{hdr}Screenshot {date} {time}",
    "hotkey_region": "ctrl+shift+h",
    "hotkey_screen": "ctrl+shift+g",
    "gainmap_quality": 90,             # UltraHDR base/gainmap JPEG quality
    "gainmap_downscale": 1,            # gain-map resolution divisor (1 = full res)
    "copy_to_clipboard": False,        # also copy the SDR image on save
    "copy_only": False,                # copy without writing a file
    "ask_where_to_save": False,        # prompt for a path each save
    "notifications": True,             # post-capture toast
    "run_at_login": False,             # start with Windows
}

_VALID_FORMATS = {"auto", "ultrahdr", "exr", "heic", "png", "jpeg", "avif"}


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
                # Keep only known keys; ignore junk/forward-compat extras gracefully.
                for k in DEFAULTS:
                    if k in loaded:
                        data[k] = loaded[k]
        except FileNotFoundError:
            log.debug("no config at %s; using defaults", path)
        except (json.JSONDecodeError, OSError) as e:
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

    def resolved_save_dir(self) -> str:
        """The configured save dir, or the default Pictures/Screenshots folder."""
        d = (self.get("save_dir") or "").strip()
        if d:
            os.makedirs(d, exist_ok=True)
            return d
        from .core import pipeline
        return pipeline.default_save_dir()
