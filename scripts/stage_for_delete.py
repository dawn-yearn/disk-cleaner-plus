#!/usr/bin/env python3
"""Reversible staging and hash-based dedupe for Windows cleanup.

Default behavior is preview-only. Use --execute to move data.
Same-volume moves use rename when possible, avoiding recycle-bin hooks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from collections import defaultdict
from pathlib import Path


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def unique_dest(staging: Path, source: Path) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    base = staging / source.name
    if not base.exists():
        return base
    return staging / f"{source.stem}__{stamp()}{source.suffix}"


def same_volume(a: Path, b: Path) -> bool:
    return a.drive.lower() == b.drive.lower() if a.drive and b.drive else False


def stage_one(source: Path, staging: Path, execute: bool) -> dict:
    source = source.resolve(strict=False)
    dest = unique_dest(staging, source)
    result = {
        "source": str(source),
        "destination": str(dest),
        "exists": source.exists(),
        "same_volume": same_volume(source, dest),
        "executed": False,
        "status": "PREVIEW",
    }
    if not source.exists():
        result["status"] = "SOURCE_MISSING"
        return result
    if not execute:
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if same_volume(source, dest):
            os.replace(str(source), str(dest))
        else:
            shutil.move(str(source), str(dest))
        result["executed"] = True
        result["status"] = "STAGED"
    except Exception as exc:  # noqa: BLE001 - log exact agent-facing failure
        result["status"] = "FAILED"
        result["error"] = repr(exc)
    return result


def dedupe(root: Path, staging: Path, min_size_mb: float, execute: bool) -> dict:
    min_size = int(min_size_mb * 1024 * 1024)
    by_size: dict[int, list[Path]] = defaultdict(list)
    errors = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError as exc:
                errors.append({"path": str(p), "error": repr(exc)})
                continue
            if size >= min_size:
                by_size[size].append(p)

    duplicate_groups = []
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for p in files:
            try:
                by_hash[sha256_file(p)].append(p)
            except OSError as exc:
                errors.append({"path": str(p), "error": repr(exc)})
        for digest, group in by_hash.items():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda x: str(x).lower())
            keep = ordered[0]
            moved = []
            for extra in ordered[1:]:
                moved.append(stage_one(extra, staging, execute))
            duplicate_groups.append({
                "sha256": digest,
                "size": size,
                "keep": str(keep),
                "duplicates": moved,
            })

    return {
        "root": str(root),
        "staging": str(staging),
        "execute": execute,
        "groups": duplicate_groups,
        "errors": errors,
    }


def write_manifest(result: dict, manifest: Path | None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage")
    s.add_argument("source")
    s.add_argument("--staging", required=True)
    s.add_argument("--execute", action="store_true")
    s.add_argument("--manifest")

    d = sub.add_parser("dedupe")
    d.add_argument("root")
    d.add_argument("--staging", required=True)
    d.add_argument("--min-size-mb", type=float, default=100)
    d.add_argument("--execute", action="store_true")
    d.add_argument("--manifest")

    args = ap.parse_args()
    if args.cmd == "stage":
        result = stage_one(Path(args.source), Path(args.staging), args.execute)
    else:
        result = dedupe(Path(args.root), Path(args.staging), args.min_size_mb, args.execute)
    write_manifest(result, Path(args.manifest) if args.manifest else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
