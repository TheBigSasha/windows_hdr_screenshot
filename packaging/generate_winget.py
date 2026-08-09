"""Generate and locally validate the three WinGet 1.6 manifests.

The installer manifest is deliberately generated from exact archive inputs (or
their explicitly supplied SHA-256 digests).  No placeholder URL or digest is
accepted, so checked-in descriptive metadata cannot accidentally become a
release claim for bytes that were never published.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

PACKAGE_IDENTIFIER = "TheBigSasha.HDRShot"
MANIFEST_VERSION = "1.6.0"
DEFAULT_LOCALE = "en-US"
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_digest(explicit: str | None, archive: Path | None, label: str) -> str:
    if explicit and archive:
        raise ValueError(f"provide either --{label}-sha256 or --{label}-archive, not both")
    if archive:
        if not archive.is_file():
            raise ValueError(f"{label} archive does not exist: {archive}")
        digest = _sha256(archive)
    elif explicit:
        digest = explicit.strip()
    else:
        raise ValueError(f"an exact {label} archive or SHA-256 digest is required")
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} installer digest must be exactly 64 hexadecimal characters")
    return digest.upper()


def _common(package_version: str, manifest_type: str) -> dict[str, Any]:
    return {
        "PackageIdentifier": PACKAGE_IDENTIFIER,
        "PackageVersion": package_version,
        "ManifestType": manifest_type,
        "ManifestVersion": MANIFEST_VERSION,
    }


def build_manifests(
    version: str,
    x64_url: str,
    x64_sha256: str,
    arm64_url: str,
    arm64_sha256: str,
) -> dict[str, dict[str, Any]]:
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"unsupported WinGet package version: {version!r}")
    urls = {"x64": x64_url, "arm64": arm64_url}
    for architecture, url in urls.items():
        if not url.startswith("https://") or any(token in url for token in ("<", ">", "TODO")):
            raise ValueError(f"{architecture} installer URL must be a concrete HTTPS URL")
    for architecture, digest in {"x64": x64_sha256, "arm64": arm64_sha256}.items():
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"{architecture} installer digest is not a SHA-256 value")

    version_manifest = {
        **_common(version, "version"),
        "DefaultLocale": DEFAULT_LOCALE,
    }
    locale_manifest = {
        **_common(version, "defaultLocale"),
        "PackageLocale": DEFAULT_LOCALE,
        "Publisher": "TheBigSasha",
        "PublisherUrl": "https://github.com/TheBigSasha",
        "PackageName": "HDR Shot",
        "PackageUrl": "https://github.com/TheBigSasha/windows_hdr_screenshot",
        "License": "MIT",
        "LicenseUrl": (
            "https://github.com/TheBigSasha/windows_hdr_screenshot/blob/main/LICENSE"
        ),
        "Copyright": "Copyright (c) 2026 Sasha (TheBigSasha)",
        "ShortDescription": "True HDR screenshots on Windows with gain-map UltraHDR output.",
        "Description": (
            "HDR Shot captures the desktop in scRGB FP16 and preserves highlights "
            "in gain-map UltraHDR JPEG, with PNG and JPEG SDR fallbacks. The "
            "standalone bundle includes a Snipping-Tool-style capture UI and a "
            "machine-readable CLI for agents."
        ),
        "Moniker": "hdrshot",
        "Tags": ["hdr", "screenshot", "ultrahdr", "gain-map", "capture"],
    }
    installer_manifest = {
        **_common(version, "installer"),
        "Installers": [
            {
                "Architecture": "x64",
                "InstallerUrl": x64_url,
                "InstallerSha256": x64_sha256.upper(),
            },
            {
                "Architecture": "arm64",
                "InstallerUrl": arm64_url,
                "InstallerSha256": arm64_sha256.upper(),
            },
        ],
        "NestedInstallerType": "portable",
        "NestedInstallerFiles": [
            {
                "RelativeFilePath": r"HDRShot\HDRShot.exe",
                "PortableCommandAlias": "hdrshot-gui",
            },
            {
                "RelativeFilePath": r"HDRShot\hdrshot-cli.exe",
                "PortableCommandAlias": "hdrshot",
            },
        ],
        "InstallerType": "zip",
    }
    manifests = {
        "TheBigSasha.HDRShot.yaml": version_manifest,
        "TheBigSasha.HDRShot.locale.en-US.yaml": locale_manifest,
        "TheBigSasha.HDRShot.installer.yaml": installer_manifest,
    }
    validate_manifests(manifests)
    return manifests


def validate_manifests(manifests: dict[str, dict[str, Any]]) -> None:
    """Validate the generated WinGet shape without requiring PyYAML."""

    expected_names = {
        "TheBigSasha.HDRShot.yaml",
        "TheBigSasha.HDRShot.locale.en-US.yaml",
        "TheBigSasha.HDRShot.installer.yaml",
    }
    if set(manifests) != expected_names:
        raise ValueError("WinGet output must contain version, default-locale, and installer manifests")

    for name, manifest in manifests.items():
        required = {"PackageIdentifier", "PackageVersion", "ManifestType", "ManifestVersion"}
        if not required.issubset(manifest):
            raise ValueError(f"{name} is missing common WinGet fields")
        if manifest["PackageIdentifier"] != PACKAGE_IDENTIFIER:
            raise ValueError(f"{name} has the wrong package identifier")
        if manifest["ManifestVersion"] != MANIFEST_VERSION:
            raise ValueError(f"{name} must use WinGet manifest schema {MANIFEST_VERSION}")

    version_manifest = manifests["TheBigSasha.HDRShot.yaml"]
    if version_manifest["ManifestType"] != "version" or version_manifest.get("DefaultLocale") != DEFAULT_LOCALE:
        raise ValueError("WinGet version manifest has the wrong shape")

    locale_manifest = manifests["TheBigSasha.HDRShot.locale.en-US.yaml"]
    locale_required = {
        "PackageLocale",
        "Publisher",
        "PublisherUrl",
        "PackageName",
        "PackageUrl",
        "License",
        "LicenseUrl",
        "ShortDescription",
        "Description",
        "Tags",
    }
    if locale_manifest["ManifestType"] != "defaultLocale" or not locale_required.issubset(
        locale_manifest
    ):
        raise ValueError("WinGet default-locale manifest has the wrong shape")
    if locale_manifest["PackageLocale"] != DEFAULT_LOCALE or not locale_manifest["Tags"]:
        raise ValueError("WinGet default-locale metadata is incomplete")

    installer_manifest = manifests["TheBigSasha.HDRShot.installer.yaml"]
    installers = installer_manifest.get("Installers")
    if installer_manifest["ManifestType"] != "installer" or not isinstance(installers, list):
        raise ValueError("WinGet installer manifest has the wrong shape")
    if {item.get("Architecture") for item in installers} != {"x64", "arm64"}:
        raise ValueError("WinGet installer manifest must contain x64 and arm64 installers")
    for installer in installers:
        if not _SHA256_RE.fullmatch(installer.get("InstallerSha256", "")):
            raise ValueError("WinGet installer manifest contains an invalid SHA-256")
        if not installer.get("InstallerUrl", "").startswith("https://"):
            raise ValueError("WinGet installer URL must use HTTPS")
    if installer_manifest.get("InstallerType") != "zip":
        raise ValueError("WinGet installer type must be zip")
    if installer_manifest.get("NestedInstallerType") != "portable":
        raise ValueError("WinGet nested installer type must be portable")
    nested_files = installer_manifest.get("NestedInstallerFiles")
    if not isinstance(nested_files, list) or len(nested_files) != 2:
        raise ValueError("WinGet installer manifest must expose both executables")
    paths = {item.get("RelativeFilePath") for item in nested_files}
    expected_paths = {r"HDRShot\HDRShot.exe", r"HDRShot\hdrshot-cli.exe"}
    if paths != expected_paths or any(path.count("\\") != 1 for path in paths):
        raise ValueError("WinGet nested installer paths must use one Windows backslash")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, str):
        if re.fullmatch(r"[A-Za-z0-9._:/+-]+", value):
            return value
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_yaml(manifest: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in manifest.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for item_key, item_value in item.items():
                        prefix = "  - " if first else "    "
                        lines.append(f"{prefix}{item_key}: {_yaml_scalar(item_value)}")
                        first = False
                else:
                    lines.append(f"  - {_yaml_scalar(item)}")
        elif key == "Description":
            lines.append("Description: |-")
            words = str(value).split()
            current = "  "
            for word in words:
                if len(current) > 2 and len(current) + len(word) + 1 > 78:
                    lines.append(current.rstrip())
                    current = "  " + word + " "
                else:
                    current += word + " "
            if current.strip():
                lines.append(current.rstrip())
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def write_manifests(out_dir: Path, manifests: dict[str, dict[str, Any]]) -> None:
    validate_manifests(manifests)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename in sorted(manifests):
        (out_dir / filename).write_text(_render_yaml(manifests[filename]), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--x64-url", required=True)
    parser.add_argument("--arm64-url", required=True)
    parser.add_argument("--x64-sha256")
    parser.add_argument("--arm64-sha256")
    parser.add_argument("--x64-archive", type=Path)
    parser.add_argument("--arm64-archive", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--validate",
        "--check",
        action="store_true",
        help="run the local WinGet shape check (also performed before every write)",
    )
    args = parser.parse_args(argv)

    try:
        manifests = build_manifests(
            args.version,
            args.x64_url,
            _release_digest(args.x64_sha256, args.x64_archive, "x64"),
            args.arm64_url,
            _release_digest(args.arm64_sha256, args.arm64_archive, "arm64"),
        )
        if args.validate:
            validate_manifests(manifests)
        write_manifests(args.out_dir, manifests)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
