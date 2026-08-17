#!/usr/bin/env python3
"""Query a saved organize-files inventory without rescanning its source root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
from _common import compact_inventory_summary


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


SECTIONS = {
    "files": "files",
    "filename-review": "filename_review_candidates",
    "images": "image_audit_candidates",
    "same-size": "same_size_candidate_groups",
    "top-level": "top_level_summary",
    "generated-metadata": "generated_metadata_candidates",
    "empty-directories": "empty_directory_candidates",
}


def relative_path_for(row: dict | str, section: str) -> str | None:
    if section == "empty-directories" and isinstance(row, str):
        return row
    if not isinstance(row, dict):
        return None
    if section in {"files", "filename-review", "images", "generated-metadata"}:
        return row.get("relative_path")
    return None


def matches(row: dict | str, section: str, top_level: str | None, extension: str | None) -> bool:
    relative_path = relative_path_for(row, section)
    if relative_path is None:
        return not top_level and not extension
    path = PurePosixPath(relative_path)
    if top_level:
        actual = path.parts[0] if len(path.parts) > 1 else "."
        if actual.casefold() != top_level.casefold():
            return False
    if extension:
        normalized = extension.casefold()
        if normalized != "[no-extension]" and not normalized.startswith("."):
            normalized = f".{normalized}"
        actual_extension = path.suffix.casefold() or "[no-extension]"
        if actual_extension != normalized:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--section", choices=["summary", *SECTIONS], default="summary")
    parser.add_argument("--top-level")
    parser.add_argument("--extension")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if args.offset < 0 or args.limit < 1:
        parser.error("--offset must be non-negative and --limit must be positive")

    inventory_path = args.inventory.resolve(strict=True)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != 1 or not isinstance(inventory.get("files"), list):
        parser.error("inventory is not a supported version-1 scan")

    if args.section == "summary":
        payload = compact_inventory_summary(inventory)
    else:
        rows = inventory[SECTIONS[args.section]]
        filtered = [
            row
            for row in rows
            if matches(row, args.section, args.top_level, args.extension)
        ]
        payload = {
            "section": args.section,
            "total_matching": len(filtered),
            "offset": args.offset,
            "limit": args.limit,
            "rows": filtered[args.offset : args.offset + args.limit],
        }

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
