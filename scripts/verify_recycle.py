#!/usr/bin/env python3
"""Verify whether an original path appears in Windows Recycle Bin $I metadata.

This avoids Shell.Application COM and tolerates inaccessible SID folders.
It does not delete anything.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable


def drive_root(path: Path) -> Path:
    drive = path.drive
    if not drive:
        raise ValueError(f"Path has no Windows drive letter: {path}")
    return Path(drive + "\\")


def iter_i_files(recycle_root: Path) -> Iterable[Path]:
    try:
        sid_dirs = list(recycle_root.iterdir())
    except (OSError, PermissionError):
        return []
    found: list[Path] = []
    for sid_dir in sid_dirs:
        if not sid_dir.is_dir():
            continue
        try:
            for item in sid_dir.iterdir():
                if item.is_file() and item.name.upper().startswith("$I"):
                    found.append(item)
        except (OSError, PermissionError):
            continue
    return found


def contains_path(raw: bytes, target: str) -> bool:
    # Search both full path and basename as UTF-16LE. This is resilient across
    # $I structure versions and avoids depending on one fixed offset.
    candidates = [target, os.path.normpath(target), os.path.basename(target)]
    lowered = raw.lower()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            needle = candidate.encode("utf-16le").lower()
        except UnicodeEncodeError:
            continue
        if needle in lowered:
            return True
    return False


def scan(target: Path) -> dict:
    recycle = drive_root(target) / "$Recycle.Bin"
    matches = []
    inaccessible = 0
    try:
        sid_dirs = list(recycle.iterdir()) if recycle.exists() else []
    except (OSError, PermissionError):
        sid_dirs = []
        inaccessible += 1

    for sid_dir in sid_dirs:
        if not sid_dir.is_dir():
            continue
        try:
            items = list(sid_dir.iterdir())
        except (OSError, PermissionError):
            inaccessible += 1
            continue
        for item in items:
            if not (item.is_file() and item.name.upper().startswith("$I")):
                continue
            try:
                raw = item.read_bytes()
            except (OSError, PermissionError):
                inaccessible += 1
                continue
            if contains_path(raw, str(target)):
                matches.append(str(item))

    return {
        "target": str(target),
        "recycle_root": str(recycle),
        "found_in_recycle_metadata": bool(matches),
        "matches": matches,
        "inaccessible_entries_skipped": inaccessible,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="Original Windows path to verify")
    ap.add_argument("--json-out", help="Optional JSON output path")
    args = ap.parse_args()
    result = scan(Path(args.target))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    return 2 if result["found_in_recycle_metadata"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
