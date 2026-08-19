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
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .codecs import PROFILES, capability

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "default_format": "auto",          # auto plus one canonical codec profile
    "save_dir": "",                    # "" => Pictures/Screenshots (known folder)
    "filename_template": "{hdr}Screenshot {date} {time}",
    "hotkey_region": "ctrl+shift+h",
    "hotkey_screen": "ctrl+shift+g",
    "gainmap_quality": 90,             # UltraHDR base/gainmap JPEG quality
    "gainmap_downscale": 1,            # gain-map resolution divisor (1 = full res)
    "copy_to_clipboard": False,        # also copy the SDR image on save
    "auto_save": True,                 # save the default format immediately
    "notifications": True,             # post-capture toast
    "run_at_login": False,             # start with Windows
}

# Legacy aliases are normalized once at load/set time, while unavailable
# canonical profiles remain explicit and visible to the UI.  In particular,
# issue #31 reserves ``avif`` for the single-rendition PQ AVIF meaning; it must
# not become a content-dependent SDR/HDR choice or silently turn into ``auto``.
CANONICAL_FORMATS = ("auto", *PROFILES)
LEGACY_FORMAT_ALIASES = {
    "ultrahdr": "uhdr-jpeg",
    "avif": "pq-avif",
    "heic": "pq-heic",
    "avif-hdr": "pq-avif",
}


@dataclass(frozen=True)
class FormatValidation:
    """Semantic result shared by config loading, Preferences, and Preview.

    A canonical profile can be valid but unavailable in this installation.  It
    remains a meaningful saved choice in that state so the UI can display it
    and ask the user to choose another profile; callers must not coerce it to
    ``auto`` merely because its provider is missing.
    """

    value: Any
    canonical: str | None
    valid: bool
    available: bool
    code: str
    status: str | None = None
    reason: str | None = None
    migrated: bool = False

    @property
    def selectable(self) -> bool:
        return self.valid and self.available

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "canonical": self.canonical,
            "valid": self.valid,
            "available": self.available,
            "code": self.code,
            "status": self.status,
            "reason": self.reason,
            "migrated": self.migrated,
        }

    def message(self) -> str:
        if self.code == "legacy_migrated":
            return (f"Legacy format '{self.value}' was migrated to canonical "
                    f"profile '{self.canonical}'.")
        if self.code == "unavailable":
            return (f"Format '{self.canonical}' is unavailable ({self.status or 'unknown'}): "
                    f"{self.reason or 'its codec provider is not usable'}.")
        if self.code == "invalid":
            return (f"Unknown default format {self.value!r}; choose one of "
                    f"{', '.join(CANONICAL_FORMATS)}.")
        return ""


class InvalidFormatError(ValueError):
    """Structured error raised when a caller tries to set an unknown format."""

    def __init__(self, validation: FormatValidation):
        self.validation = validation
        super().__init__(validation.message())


def validate_format(value: Any, *, allow_legacy: bool = True) -> FormatValidation:
    """Validate and normalize a user-facing format id.

    This is the single semantic format policy used by Config.load/set and both
    format selectors.  Availability is reported, not collapsed into ``auto``;
    only the deliberate legacy aliases are migrated to their representation-
    bearing canonical profiles.
    """
    if not isinstance(value, str):
        return FormatValidation(value, None, False, False, "invalid",
                                reason="format must be a string")
    value = value.strip()
    if allow_legacy and value in LEGACY_FORMAT_ALIASES:
        canonical = LEGACY_FORMAT_ALIASES[value]
        cap = capability(canonical)
        return FormatValidation(value, canonical, True, cap.available,
                                "legacy_migrated", status=cap.status,
                                reason=cap.reason, migrated=True)
    if value == "auto":
        return FormatValidation(value, "auto", True, True, "ok")
    if value not in PROFILES:
        return FormatValidation(value, None, False, False, "invalid")
    cap = capability(value)
    if cap.available:
        return FormatValidation(value, value, True, True, "ok",
                                status=cap.status, reason=cap.reason)
    return FormatValidation(value, value, True, False, "unavailable",
                            status=cap.status, reason=cap.reason)


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
    diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None = None) -> Config:
        path = path or config_path()
        data = dict(DEFAULTS)
        diagnostics: dict[str, dict[str, Any]] = {}
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                if "default_format" in loaded:
                    checked = validate_format(loaded["default_format"])
                    if checked.valid:
                        # Persist the canonical value in memory.  In particular,
                        # a missing/broken explicit profile stays explicit so
                        # Preferences/Preview can show it instead of selecting
                        # Auto or the first enabled item.
                        data["default_format"] = checked.canonical
                        if checked.code != "ok":
                            diagnostics["default_format"] = checked.to_dict()
                            log.warning("saved default_format diagnostic: %s",
                                        checked.message())
                    else:
                        diagnostics["default_format"] = checked.to_dict()
                        log.warning("saved default_format rejected: %s",
                                    checked.message())
                # Keep only known keys with the expected type; a hand-edited or
                # corrupt value must never be able to crash the app at startup.
                for k, default in DEFAULTS.items():
                    if k == "default_format":
                        continue
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
        return cls(data=data, path=path, diagnostics=diagnostics)

    def save(self, path: str | None = None) -> None:
        path = path or self.path or config_path()
        absolute_path = os.path.abspath(path)
        parent = os.path.dirname(absolute_path)
        os.makedirs(parent, exist_ok=True)
        tmp = ""
        try:
            # A unique temp file in the destination directory prevents two
            # app instances from racing over a shared ``.tmp`` pathname.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=parent,
                prefix=f".{os.path.basename(absolute_path)}.", suffix=".tmp",
                delete=False,
            ) as f:
                tmp = f.name
                json.dump(self.data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, absolute_path)  # atomic same-directory publish
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    log.debug("could not remove temporary config %s", tmp)
        self.path = path
        log.debug("config saved to %s", path)

    def get(self, key: str):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key == "default_format":
            checked = validate_format(value)
            if not checked.valid:
                self.diagnostics[key] = checked.to_dict()
                raise InvalidFormatError(checked)
            self.data[key] = checked.canonical
            if checked.code == "ok":
                self.diagnostics.pop(key, None)
            else:
                # Keep an unavailable profile (or a migrated legacy choice)
                # observable to the UI and callers instead of silently changing
                # it to a different usable codec.
                self.diagnostics[key] = checked.to_dict()
            return
        self.data[key] = value

    # -- validated accessors ------------------------------------------------ #
    def default_format(self) -> str:
        f = self.get("default_format")
        checked = validate_format(f)
        return checked.canonical if checked.valid and checked.canonical else "auto"

    def format_validation(self) -> FormatValidation:
        """Validate the currently persisted default format without coercion."""
        return validate_format(self.get("default_format"))

    def effective_default_format(self) -> str:
        """Return the canonical saved policy, including unavailable profiles.

        The name is retained for callers, but availability is intentionally not
        folded into this result.  Preview must be able to render an unavailable
        saved choice and let the user replace it explicitly.
        """
        return self.default_format()

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
