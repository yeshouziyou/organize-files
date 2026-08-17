#!/usr/bin/env python3
"""Create a metadata-only inventory for a selected file-organization root."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
from _common import IMAGE_EXTENSIONS, compact_inventory_summary, generated_metadata_kind


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


HUMAN_REVIEW_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".dot",
    ".dotx",
    ".key",
    ".md",
    ".mm",
    ".numbers",
    ".odt",
    ".pages",
    ".pdf",
    ".pps",
    ".ppsx",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".tsv",
    ".txt",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".xmind",
    ".zip",
}
DATE_TOKEN = r"(?:19|20)\d{2}(?:(?:0[1-9]|1[0-2])(?:(?:0[1-9]|[12]\d|3[01]))?|Q[1-4])?"
DATE_PREFIX = re.compile(rf"^{DATE_TOKEN}(?:-{DATE_TOKEN})?_", re.IGNORECASE)
OPAQUE_TITLE = re.compile(
    r"^(?:untitled|document\d*|doc\d*|final|copy|draft|edited|文件\d*|新建(?:文档|文件)?\d*|副本\d*|最终版|最新版|修改版)$",
    re.IGNORECASE,
)
OPAQUE_SUFFIX = re.compile(
    r"(?:^|[-_ ])(?:copy|draft|edited|副本|最终版|最新版|修改版)(?:\d+)?$|\(\d+\)$",
    re.IGNORECASE,
)


def is_boundary_directory(path: Path) -> bool:
    if path.is_symlink() or os.path.ismount(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def filename_review_reasons(path: Path) -> list[str]:
    if path.suffix.lower() not in HUMAN_REVIEW_EXTENSIONS:
        return []
    reasons: list[str] = []
    if not DATE_PREFIX.match(path.name):
        reasons.append("missing_date_prefix")
    stem = path.stem.strip()
    if OPAQUE_TITLE.fullmatch(stem) or OPAQUE_SUFFIX.search(stem):
        reasons.append("opaque_title")
    return reasons


def image_audit_reasons(path: Path) -> list[str]:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return []
    reasons = ["asset_or_archive_decision"]
    if not DATE_PREFIX.match(path.name):
        reasons.append("missing_date_prefix")
    return reasons


def extension_label(extension: str) -> str:
    return extension or "[no-extension]"


def scan(root: Path, include_duplicate_candidates: bool = False) -> dict:
    files: list[dict] = []
    generated_metadata_candidates: list[dict] = []
    empty_directory_candidates: list[str] = []
    skipped_boundaries: list[str] = []
    stack = [root]

    while stack:
        directory = stack.pop()
        has_entries = False
        with os.scandir(directory) as entries:
            for entry in entries:
                has_entries = True
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_dir(follow_symlinks=False):
                    if is_boundary_directory(path):
                        skipped_boundaries.append(relative)
                    else:
                        stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    skipped_boundaries.append(relative)
                    continue

                info = entry.stat(follow_symlinks=False)
                attributes = getattr(info, "st_file_attributes", 0)
                row = {
                    "relative_path": relative,
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "ctime_ns": info.st_ctime_ns,
                    "extension": path.suffix.lower(),
                    "hidden": path.name.startswith(".")
                    or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)),
                    "system": bool(attributes & getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0)),
                    "reparse_point": bool(
                        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    ),
                    "offline": bool(attributes & getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0)),
                }
                kind = generated_metadata_kind(path)
                if kind:
                    generated_metadata_candidates.append(
                        {
                            "relative_path": relative,
                            "kind": kind,
                            "size": info.st_size,
                            "mtime_ns": info.st_mtime_ns,
                            "ctime_ns": info.st_ctime_ns,
                        }
                    )
                else:
                    files.append(row)
        if directory != root and not has_entries:
            empty_directory_candidates.append(directory.relative_to(root).as_posix())

    files.sort(key=lambda row: row["relative_path"].casefold())
    generated_metadata_candidates.sort(
        key=lambda row: row["relative_path"].casefold()
    )
    candidate_groups = []
    if include_duplicate_candidates:
        by_size: dict[int, list[str]] = defaultdict(list)
        for row in files:
            by_size[row["size"]].append(row["relative_path"])
        candidate_groups = [
            {"size": size, "paths": sorted(paths, key=str.casefold)}
            for size, paths in by_size.items()
            if len(paths) > 1
        ]
        candidate_groups.sort(key=lambda group: (group["size"], group["paths"]))

    extension_counts = dict(
        sorted(Counter(extension_label(row["extension"]) for row in files).items())
    )
    top_level: dict[str, list[dict]] = defaultdict(list)
    for row in files:
        relative = Path(row["relative_path"])
        name = relative.parts[0] if len(relative.parts) > 1 else "."
        top_level[name].append(row)
    top_level_summary = []
    for name, rows in sorted(top_level.items(), key=lambda item: item[0].casefold()):
        top_level_summary.append(
            {
                "name": name,
                "file_count": len(rows),
                "extension_counts": dict(
                    sorted(
                        Counter(extension_label(row["extension"]) for row in rows).items()
                    )
                ),
            }
        )

    filename_review_candidates = []
    for row in files:
        reasons = filename_review_reasons(Path(row["relative_path"]))
        if reasons:
            filename_review_candidates.append(
                {"relative_path": row["relative_path"], "reasons": reasons}
            )

    image_audit_candidates = []
    for row in files:
        reasons = image_audit_reasons(Path(row["relative_path"]))
        if reasons:
            image_audit_candidates.append(
                {"relative_path": row["relative_path"], "reasons": reasons}
            )

    return {
        "version": 1,
        "root": str(root),
        "total_file_count": len(files) + len(generated_metadata_candidates),
        "file_count": len(files),
        "extension_counts": extension_counts,
        "top_level_summary": top_level_summary,
        "files": files,
        "filename_review_candidates": filename_review_candidates,
        "image_audit_candidates": image_audit_candidates,
        "generated_metadata_candidates": generated_metadata_candidates,
        "empty_directory_candidates": sorted(
            empty_directory_candidates, key=str.casefold
        ),
        "same_size_candidate_groups": candidate_groups,
        "duplicate_candidates_computed": include_duplicate_candidates,
        "skipped_boundaries": sorted(skipped_boundaries, key=str.casefold),
        "content_hashes_computed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--inventory-out",
        type=Path,
        help="Write the full inventory to this JSON path outside the scanned root.",
    )
    parser.add_argument(
        "--full-output",
        action="store_true",
        help="Print the full inventory instead of the compact summary.",
    )
    parser.add_argument(
        "--include-duplicate-candidates",
        action="store_true",
        help="Build same-size candidate groups only when duplicate review is in scope.",
    )
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        parser.error("root must be an existing directory")

    inventory_path = None
    if args.inventory_out:
        inventory_path = args.inventory_out.resolve()
        try:
            inventory_path.relative_to(root)
        except ValueError:
            pass
        else:
            parser.error("--inventory-out must be outside the scanned root")
        if not inventory_path.parent.is_dir():
            parser.error("--inventory-out parent directory must already exist")

    inventory = scan(root, args.include_duplicate_candidates)
    if inventory_path:
        inventory_path.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    payload = inventory if args.full_output else compact_inventory_summary(inventory, inventory_path)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
