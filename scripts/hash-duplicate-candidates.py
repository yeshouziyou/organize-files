#!/usr/bin/env python3
"""Hash only frozen same-size candidates and report exact byte duplicates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
from _common import resolve_relative_within_root, sha256_file


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def build_report(inventory_path: Path) -> dict:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != 1:
        raise ValueError("inventory must use version 1")
    if not inventory.get("duplicate_candidates_computed"):
        raise ValueError("inventory was not scanned with --include-duplicate-candidates")
    root = Path(inventory["root"]).resolve(strict=True)
    rows = {row["relative_path"].casefold(): row for row in inventory.get("files", [])}
    candidate_paths: set[str] = set()
    for group in inventory.get("same_size_candidate_groups", []):
        for relative in group.get("paths", []):
            candidate_paths.add(relative)

    hashed_files: list[dict] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for relative in sorted(candidate_paths, key=str.casefold):
        row = rows.get(relative.casefold())
        if row is None:
            raise ValueError(f"same-size candidate missing from inventory: {relative}")
        path = resolve_relative_within_root(root, relative, label="inventory path")
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"candidate is not a regular file: {relative}")
        current = path.stat()
        if current.st_size != row.get("size") or current.st_mtime_ns != row.get("mtime_ns"):
            raise ValueError(f"candidate changed after inventory: {relative}")
        digest = sha256_file(path)
        hashed_files.append(
            {
                "relative_path": relative,
                "size": current.st_size,
                "mtime_ns": current.st_mtime_ns,
                "sha256": digest,
            }
        )
        hashes[digest].append(relative)

    duplicate_groups = [
        {"sha256": digest, "paths": sorted(paths, key=str.casefold)}
        for digest, paths in hashes.items()
        if len(paths) > 1
    ]
    duplicate_groups.sort(key=lambda row: row["paths"][0].casefold())
    return {
        "version": 1,
        "root": str(root),
        "inventory_path": str(inventory_path),
        "content_hashes_computed": len(hashed_files),
        "hashed_files": hashed_files,
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "deletion_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        inventory_path = args.inventory.resolve(strict=True)
        output = args.output.resolve(strict=False)
        if not output.parent.is_dir():
            raise ValueError("--output parent must already exist")
        report = build_report(inventory_path)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        json.dump(
            {
                "version": 1,
                "status": "DUPLICATE_HASHING_COMPLETED",
                "output": str(output),
                "content_hashes_computed": report["content_hashes_computed"],
                "duplicate_group_count": report["duplicate_group_count"],
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump(
            {"version": 1, "status": "DUPLICATE_HASHING_FAILED", "error": str(error)},
            sys.stderr,
            ensure_ascii=False,
            indent=2,
        )
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
