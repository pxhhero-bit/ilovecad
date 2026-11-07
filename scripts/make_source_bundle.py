#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the GPL Corresponding Source bundle for a binary release.

Includes IloveCAD's complete source tree plus the exact LibreDWG source
tarball used to build the shipped tools. Never includes ad/membership assets,
credentials or user data.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TP = ROOT / "third_party" / "libredwg"
DIST = ROOT / "dist"
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "bin", "generated", "screenshots", "dist"}
SKIP_SUFFIX = {".pyc", ".pyo", ".zip", ".tar.xz", ".dwg", ".dxf", ".pdf", ".log"}


def main() -> int:
    pin = json.loads((TP / "PIN.json").read_text(encoding="utf-8"))
    DIST.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = DIST / f"IloveCAD-source-{stamp}-libredwg-{pin['version']}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            rel = path.relative_to(ROOT)
            if not path.is_file() or any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIX:
                continue
            zf.write(path, f"IloveCAD/{rel.as_posix()}")
            count += 1
        src_tarball = TP / pin["source"]["archive"]
        if src_tarball.exists():
            zf.write(src_tarball, f"IloveCAD/third_party/libredwg/{src_tarball.name}")
            count += 1
        else:
            print("WARNING: LibreDWG source tarball missing; run scripts/fetch_libredwg.py first")
        zf.writestr(
            "IloveCAD/SOURCE_MANIFEST.txt",
            "\n".join([
                "IloveCAD - GPL-3.0-or-later Corresponding Source",
                f"Built: {datetime.now(timezone.utc).isoformat()}",
                f"LibreDWG version: {pin['version']}",
                f"LibreDWG source sha256: {pin['source']['sha256']}",
                f"LibreDWG win64 sha256: {pin['windows']['sha256']}",
                "License: see COPYING, NOTICE, THIRD_PARTY.md",
                "No advertising, membership, subscription or account code is included.",
            ]) + "\n",
        )
    print(f"Wrote {out} ({count} files, {out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
