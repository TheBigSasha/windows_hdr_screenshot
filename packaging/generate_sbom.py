"""Generate a deterministic SPDX 2.3 inventory for a release artifact.

When ``--bundle`` is supplied, the SBOM is derived exclusively from the
extracted bundle's table of contents.  This is important for frozen builds:
the Python environment used to create the bundle is not evidence of what was
actually shipped.  Without ``--bundle`` the script retains a source-build
fallback which inventories the current Python environment and labels the
document accordingly.

The implementation intentionally uses only the standard library.  It performs
the subset of SPDX 2.3 structural checks needed for the generated document so
release verification does not depend on an additional schema package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

SPDX_VERSION = "SPDX-2.3"
DATA_LICENSE = "CC0-1.0"
REPOSITORY = "https://github.com/TheBigSasha/windows_hdr_screenshot"
CREATOR = "Tool: packaging/generate_sbom.py"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_id(prefix: str, value: str) -> str:
    """Return a stable SPDX identifier for an arbitrary path or package name."""

    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()  # noqa: S324 - SPDX identifier only
    return f"SPDXRef-{prefix}-{digest}"


def _creation_timestamp(value: str | None) -> str:
    if value:
        timestamp = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError("--created must include a timezone")
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        try:
            parsed = datetime.fromtimestamp(int(source_date_epoch), UTC)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from exc
    else:
        parsed = datetime.now(UTC)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bundle_files(bundle: Path, output: Path) -> list[dict[str, Any]]:
    bundle = bundle.resolve()
    output = output.resolve()
    if not bundle.is_dir():
        raise ValueError(f"bundle root is not a directory: {bundle}")

    files: list[dict[str, Any]] = []
    for path in sorted(
        (candidate for candidate in bundle.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(bundle).as_posix().casefold(),
    ):
        if path.resolve() == output:
            continue
        relative_name = path.relative_to(bundle).as_posix()
        files.append(
            {
                "SPDXID": _safe_id("File", relative_name),
                "fileName": relative_name,
                "checksums": [{"algorithm": "SHA256", "checksumValue": _sha256_file(path)}],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            }
        )
    return files


def _environment_packages() -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for dist in sorted(
        metadata.distributions(), key=lambda item: (item.metadata.get("Name") or "").casefold()
    ):
        name = dist.metadata.get("Name")
        if not name:
            continue
        packages.append(
            {
                "SPDXID": _safe_id("Package", f"{name}\n{dist.version}"),
                "name": name,
                "versionInfo": dist.version,
                "downloadLocation": "NOASSERTION",
                # Core metadata's free-form License field is not guaranteed to
                # be an SPDX expression; do not emit an invalid claim.
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        )
    return packages


def _artifact_package(bundle: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = "\n".join(
        f"{item['fileName']}\t{item['checksums'][0]['checksumValue']}" for item in files
    )
    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    return {
        "SPDXID": _safe_id("Package", f"HDRShot bundle\n{digest}"),
        "name": "HDR Shot frozen bundle",
        "versionInfo": "NOASSERTION",
        "downloadLocation": "NOASSERTION",
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "licenseInfoFromFiles": ["NOASSERTION"],
        "copyrightText": "NOASSERTION",
        "filesAnalyzed": True,
        "comment": f"Extracted artifact inventory rooted at {bundle.name}.",
    }


def _document_namespace(packages: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    material = json.dumps(
        {
            "packages": packages,
            "files": files,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"{REPOSITORY}/sbom/{digest}"


def validate_spdx_document(document: dict[str, Any]) -> None:
    """Validate the generated document against the local SPDX 2.3 contract."""

    required = {"spdxVersion", "dataLicense", "SPDXID", "name", "documentNamespace", "creationInfo"}
    missing = required.difference(document)
    if missing:
        raise ValueError(f"SPDX document missing required fields: {', '.join(sorted(missing))}")
    if document["spdxVersion"] != SPDX_VERSION or document["dataLicense"] != DATA_LICENSE:
        raise ValueError("SPDX document has an unsupported version or data license")
    if document["SPDXID"] != "SPDXRef-DOCUMENT":
        raise ValueError("SPDX document must use SPDXRef-DOCUMENT")
    namespace = document["documentNamespace"]
    if not isinstance(namespace, str) or not namespace.startswith(f"{REPOSITORY}/sbom/"):
        raise ValueError("SPDX documentNamespace must be unique and repository-scoped")

    creation = document["creationInfo"]
    if not isinstance(creation, dict) or not isinstance(creation.get("created"), str):
        raise ValueError("SPDX creationInfo.created is required")
    if not creation.get("creators") or not all(
        isinstance(creator, str) and creator for creator in creation["creators"]
    ):
        raise ValueError("SPDX creationInfo.creators is required")

    known_ids = {document["SPDXID"]}
    for section, required_fields in (
        (
            "packages",
            {
                "SPDXID",
                "name",
                "versionInfo",
                "downloadLocation",
                "licenseConcluded",
                "licenseDeclared",
                "copyrightText",
            },
        ),
        (
            "files",
            {
                "SPDXID",
                "fileName",
                "checksums",
                "licenseConcluded",
                "licenseInfoInFiles",
                "copyrightText",
            },
        ),
    ):
        entries = document.get(section, [])
        if not isinstance(entries, list):
            raise ValueError(f"SPDX {section} must be an array")
        for entry in entries:
            if not isinstance(entry, dict) or not required_fields.issubset(entry):
                raise ValueError(f"SPDX {section} entry is missing required fields")
            identifier = entry["SPDXID"]
            if not isinstance(identifier, str) or identifier in known_ids:
                raise ValueError(f"SPDX identifier is missing or duplicated: {identifier!r}")
            known_ids.add(identifier)
            if section == "files":
                checksums = entry["checksums"]
                if not isinstance(checksums, list) or not checksums:
                    raise ValueError(f"SPDX file has no checksum: {entry['fileName']}")
                for checksum in checksums:
                    if checksum.get("algorithm") != "SHA256" or not isinstance(
                        checksum.get("checksumValue"), str
                    ) or len(checksum["checksumValue"]) != 64:
                        raise ValueError(f"SPDX file has an invalid SHA256 checksum: {entry['fileName']}")

    relationships = document.get("relationships", [])
    if not isinstance(relationships, list):
        raise ValueError("SPDX relationships must be an array")
    for relationship in relationships:
        if not isinstance(relationship, dict) or not {
            "spdxElement",
            "relationshipType",
            "relatedSpdxElement",
        }.issubset(relationship):
            raise ValueError("SPDX relationship is missing required fields")
        if relationship["spdxElement"] not in known_ids or relationship["relatedSpdxElement"] not in known_ids:
            raise ValueError("SPDX relationship references an unknown identifier")


def build_document(bundle: Path | None, created: str | None, output: Path) -> dict[str, Any]:
    files = _bundle_files(bundle, output) if bundle else []
    packages = [] if bundle else _environment_packages()
    relationships: list[dict[str, str]] = []

    if bundle:
        artifact = _artifact_package(bundle, files)
        packages.append(artifact)
        relationships.append(
            {
                "spdxElement": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": artifact["SPDXID"],
            }
        )
        relationships.extend(
            {
                "spdxElement": artifact["SPDXID"],
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file["SPDXID"],
            }
            for file in files
        )
    else:
        relationships.extend(
            {
                "spdxElement": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": package["SPDXID"],
            }
            for package in packages
        )

    document = {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": DATA_LICENSE,
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": (
            "HDR Shot frozen bundle artifact inventory"
            if bundle
            else "HDR Shot source-build environment dependency inventory"
        ),
        "documentNamespace": _document_namespace(packages, files),
        "creationInfo": {
            "created": _creation_timestamp(created),
            "creators": [CREATOR],
        },
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }
    validate_spdx_document(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path, help="SPDX JSON output path")
    parser.add_argument(
        "--bundle",
        "--bundle-root",
        "--artifact",
        dest="bundle",
        type=Path,
        help="extracted frozen bundle root; required for artifact-derived SBOMs",
    )
    parser.add_argument(
        "--created",
        help="RFC 3339 creation time; defaults to SOURCE_DATE_EPOCH or the current UTC time",
    )
    parser.add_argument(
        "--validate",
        "--check",
        action="store_true",
        help="run the local SPDX 2.3 structural check (also performed before every write)",
    )
    args = parser.parse_args(argv)

    try:
        document = build_document(args.bundle, args.created, args.out)
        if args.validate:
            validate_spdx_document(document)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
