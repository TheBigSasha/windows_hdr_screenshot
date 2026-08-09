"""Generate a dependency inventory in SPDX JSON without another build tool."""
from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    packages = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name")
        version = dist.version
        if not name:
            continue
        packages.append({
            "SPDXID": f"SPDXRef-Package-{name.replace(' ', '-')}",
            "name": name,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "licenseConcluded": dist.metadata.get("License") or "NOASSERTION",
            "licenseDeclared": dist.metadata.get("License") or "NOASSERTION",
            "copyrightText": "NOASSERTION",
        })
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "HDR Shot frozen runtime dependency inventory",
        "documentNamespace": "https://github.com/TheBigSasha/windows_hdr_screenshot/sbom",
        "creator": "Tool: packaging/generate_sbom.py",
        "packages": packages,
    }
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
