from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

_ARCHIVE_RE = re.compile(r"^HDRShot-(\d+\.\d+\.\d+)-(win64|win-arm64)\.zip$")


def _platform_architecture() -> str:
    machine = platform.machine().upper()
    if machine == "ARM64":
        return "arm64"
    if machine in {"AMD64", "X86_64"}:
        return "x64"
    raise RuntimeError(f"Unsupported Windows architecture: {machine}")


def _pe_machine(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RuntimeError(f"{path.name} is not a PE executable")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset < 0 or offset + 6 > len(data) or data[offset : offset + 4] != b"PE\0\0":
        raise RuntimeError(f"{path.name} has an invalid PE header")
    return struct.unpack_from("<H", data, offset + 4)[0]


def _payload_directory() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    payload = base / "payload"
    if not payload.is_dir():
        raise RuntimeError("installer payload directory is missing")
    archives = sorted(payload.glob("HDRShot-*.zip"))
    if len(archives) != 1:
        raise RuntimeError(f"installer must contain exactly one bundle archive, found {len(archives)}")
    return payload


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"{Path(args[0]).name} failed with {result.returncode}: {details}")
    return result


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"archive contains an unsafe path: {member.filename}") from exc
        zipped.extractall(destination)


def _ps_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _create_start_menu_shortcut(gui: Path, install_dir: Path) -> None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    shortcut = local_app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "HDR Shot.lnk"
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$ws=New-Object -ComObject WScript.Shell;"
        f"$s=$ws.CreateShortcut({_ps_quote(shortcut)});"
        f"$s.TargetPath={_ps_quote(gui)};"
        f"$s.WorkingDirectory={_ps_quote(install_dir)};"
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        print(f"warning: could not create Start Menu shortcut: {result.stderr.strip()}", file=sys.stderr)


def _validate_bundle(bundle: Path, version: str, architecture: str, work: Path) -> tuple[Path, Path]:
    expected_machine = 0xAA64 if architecture == "arm64" else 0x8664
    gui = bundle / "HDRShot.exe"
    cli = bundle / "hdrshot-cli.exe"
    for executable in (gui, cli):
        if not executable.is_file():
            raise RuntimeError(f"bundle is missing {executable.name}")
        if _pe_machine(executable) != expected_machine:
            raise RuntimeError(f"{executable.name} has the wrong architecture")
    version_output = _run([str(cli), "--version"]).stdout.strip()
    if version_output != f"hdrshot {version}":
        raise RuntimeError(f"bundle version mismatch: {version_output!r}")
    capabilities = json.loads(_run([str(cli), "capabilities", "--json"]).stdout)
    if capabilities.get("architecture") != architecture or not capabilities.get("available_profiles"):
        raise RuntimeError("bundle capability contract is invalid")
    _run([str(cli), "selftest", "--out", str(work / "selftest")])
    return gui, cli


def install(install_dir: Path | None, no_launch: bool) -> int:
    architecture = _platform_architecture()
    payload = _payload_directory()
    archive = next(payload.glob("HDRShot-*.zip"))
    match = _ARCHIVE_RE.fullmatch(archive.name)
    if not match:
        raise RuntimeError(f"invalid embedded archive name: {archive.name}")
    version, suffix = match.groups()
    expected_suffix = "win-arm64" if architecture == "arm64" else "win64"
    if suffix != expected_suffix:
        raise RuntimeError(f"installer targets {suffix}, but this device is {architecture}")

    sidecar = payload / f"{archive.name}.sha256"
    if not sidecar.is_file():
        raise RuntimeError("installer payload is missing its SHA-256 sidecar")
    records = [line.split() for line in sidecar.read_text(encoding="ascii").splitlines() if line.strip()]
    if len(records) != 1 or len(records[0]) < 2 or records[0][1].lstrip("*") != archive.name:
        raise RuntimeError("installer payload has an invalid SHA-256 sidecar")
    actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual_hash.lower() != records[0][0].lower():
        raise RuntimeError("embedded archive failed SHA-256 verification")

    default_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target = Path(install_dir) if install_dir else default_dir / "Programs" / "HDRShot"
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_root = Path(tempfile.mkdtemp(prefix="HDRShot-installer-"))
    stage = target.parent / f".{target.name}.new-{uuid.uuid4()}"
    rollback = target.parent / f".{target.name}.rollback-{uuid.uuid4()}"
    old_moved = False
    try:
        extracted = temp_root / "expanded"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        bundle = extracted / "HDRShot"
        gui, _ = _validate_bundle(bundle, version, architecture, temp_root)
        shutil.copytree(bundle, stage)
        if target.exists():
            if not target.is_dir():
                raise RuntimeError(f"install target is not a directory: {target}")
            shutil.move(str(target), str(rollback))
            old_moved = True
        shutil.move(str(stage), str(target))
        stage = None
        _create_start_menu_shortcut(target / "HDRShot.exe", target)
        if not no_launch:
            subprocess.Popen([str(target / "HDRShot.exe")], cwd=target)
        print(f"HDR Shot {version} installed to {target}")
        return 0
    except Exception:
        if old_moved and not target.exists() and rollback.exists():
            shutil.move(str(rollback), str(target))
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a native HDR Shot release bundle.")
    parser.add_argument("--install-dir", type=Path, help="per-user destination directory")
    parser.add_argument("--no-launch", action="store_true", help="do not start HDR Shot after installation")
    args = parser.parse_args()
    try:
        return install(args.install_dir, args.no_launch)
    except Exception as exc:
        print(f"HDR Shot installer failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
