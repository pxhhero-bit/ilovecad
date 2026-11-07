#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download and verify the pinned GNU LibreDWG build and its matching source.

The GPL requires the Corresponding Source of the exact LibreDWG build shipped
with any binary release. This script fetches both artifacts and records their
SHA-256 digests in third_party/libredwg/PIN.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TP = ROOT / "third_party" / "libredwg"
PIN_FILE = TP / "PIN.json"
BIN_DIR = TP / "bin"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "IloveCAD/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def ensure(url: str, dest: Path, expected: str, label: str, pin: dict, key: str) -> str:
    if not dest.exists() or dest.stat().st_size == 0:
        download(url, dest)
    actual = digest(dest)
    if expected and expected != "auto" and expected != actual:
        raise SystemExit(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    if expected == "auto":
        pin[key]["sha256"] = actual
        PIN_FILE.write_text(json.dumps(pin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  recorded {label} sha256 = {actual}")
    else:
        print(f"  verified {label} sha256 = {actual[:16]}...")
    return actual


def extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)


def find_tools(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for p in root.rglob("*"):
        if p.is_file():
            stem = p.stem.lower()
            if stem in ("dwgread", "dwgwrite", "dwg2dxf", "dxf2dwg", "dwgrewrite"):
                found.setdefault(stem, str(p))
            elif p.name.lower().startswith("libredwg") and p.suffix.lower() == ".dll":
                found.setdefault("libredwg_dll", str(p))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify-only", action="store_true", help="only verify existing files")
    ap.add_argument("--skip-source", action="store_true", help="do not download the source tarball")
    args = ap.parse_args()

    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    win = pin["windows"]
    src = pin["source"]
    zip_path = TP / win["archive"]

    print(f"LibreDWG pinned version: {pin['version']} (target {pin['target_dwg_version']})")
    if args.verify_only:
        if not zip_path.exists():
            raise SystemExit("archive missing; run without --verify-only first")
        print("  sha256:", digest(zip_path))
        return 0

    print("[1/3] Windows tools")
    ensure(win["url"], zip_path, win["sha256"], "win64 zip", pin, "windows")

    print("[2/3] Extracting")
    if BIN_DIR.exists():
        shutil.rmtree(BIN_DIR)
    extract_zip(zip_path, BIN_DIR)
    tools = find_tools(BIN_DIR)
    for name in ("dwgread", "dwgwrite", "dwg2dxf", "dxf2dwg"):
        print(f"  {name}: {'OK' if name in tools else 'missing'}")
    if "dwgread" not in tools or "dwgwrite" not in tools:
        print("  WARNING: required tools not found in archive; the kernel will fall back to PATH lookup")
    (TP / "tools.json").write_text(json.dumps(tools, indent=2), encoding="utf-8")

    print("[3/3] Corresponding source (GPL requirement)")
    if args.skip_source:
        print("  skipped by request")
    else:
        src_path = TP / src["archive"]
        try:
            ensure(src["url"], src_path, src["sha256"], "source tarball", pin, "source")
        except Exception as exc:  # network mirror fallback
            print(f"  primary source download failed ({exc}); trying GNU mirror")
            ensure(src["gnu_mirror"], src_path, src["sha256"], "source tarball", pin, "source")
        with tarfile.open(src_path) as tf:
            print(f"  source archive contains {len(tf.getnames())} entries")

    print("Done. Run: python scripts/compile_ui.py && python scripts/run.py")
    if sys.platform not in ("win32", "cygwin"):
        print("Non-Windows: build LibreDWG from the source tarball with")
        print("  ./configure --enable-release --disable-bindings && make && make install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
