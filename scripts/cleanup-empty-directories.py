#!/usr/bin/env python3
"""Remove safe empty directories after an approved organization plan finishes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

sys.dont_write_bytecode = True
from _common import load_effective_policy as load_policy


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


POLICY_RESOLVER = Path(__file__).with_name("resolve-policy.py")


def load_effective_policy(path: Path | None) -> dict:
    return load_policy(path, POLICY_RESOLVER, "empty_directories")


def require_empty_directory_apply(policy: dict) -> None:
    if policy.get("empty_directories") != "auto-if-safe":
        raise ValueError("policy forbids apply for empty directories")


def is_boundary(path: Path) -> bool:
    """Return True for links, junctions, and mount points, but not cloud sync tags."""
    if path.is_symlink() or os.path.ismount(path):
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def normalize_exclusions(root: Path, excluded: tuple[Path, ...]) -> set[Path]:
    normalized: set[Path] = set()
    for value in excluded:
        if value.is_absolute():
            raise ValueError(f"excluded paths must be relative: {value}")
        candidate = (root / value).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"excluded path escapes selected root: {value}") from exc
        normalized.add(candidate)
    return normalized


def discover(root: Path, excluded: set[Path]) -> tuple[list[Path], list[dict]]:
    directories: list[Path] = []
    skipped: list[dict] = []
    stack = [root]
    while stack:
        parent = stack.pop()
        try:
            children = sorted(parent.iterdir(), key=lambda path: path.name.casefold())
        except OSError as error:
            if parent != root:
                skipped.append({"path": parent, "reason": f"unavailable: {error}"})
            continue
        for child in children:
            try:
                if not child.is_dir():
                    continue
                resolved = child.resolve(strict=False)
                if resolved in excluded:
                    skipped.append({"path": child, "reason": "explicitly-excluded"})
                    continue
                if is_boundary(child):
                    skipped.append({"path": child, "reason": "link-junction-or-mount-boundary"})
                    continue
                directories.append(child)
                stack.append(child)
            except OSError as error:
                skipped.append({"path": child, "reason": f"unavailable: {error}"})
    return directories, skipped


def cleanup(root: Path, apply: bool, excluded: tuple[Path, ...]) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("root must be an existing directory")
    exclusions = normalize_exclusions(root, excluded)
    directories, skipped = discover(root, exclusions)
    planned_removed: set[Path] = set()
    rows: list[dict] = []

    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            children = list(path.iterdir())
            effectively_empty = all(child in planned_removed for child in children)
            if not effectively_empty:
                continue
            status = "would-delete"
            reason = "safe-empty-directory"
            if apply:
                path.rmdir()
                status = "deleted"
            planned_removed.add(path)
            rows.append({"path": path, "status": status, "reason": reason})
        except OSError as error:
            rows.append({"path": path, "status": "skipped", "reason": f"delete-check-failed: {error}"})

    for item in skipped:
        rows.append({"path": item["path"], "status": "skipped", "reason": item["reason"]})

    rendered = [
        {
            "relative_path": item["path"].relative_to(root).as_posix(),
            "status": item["status"],
            "reason": item["reason"],
        }
        for item in rows
    ]
    rendered.sort(key=lambda row: row["relative_path"].casefold())
    return {
        "version": 1,
        "root": str(root),
        "mode": "apply" if apply else "dry-run",
        "candidate_count": sum(row["status"] in {"would-delete", "deleted"} for row in rendered),
        "deleted_count": sum(row["status"] == "deleted" for row in rendered),
        "would_delete_count": sum(row["status"] == "would-delete" for row in rendered),
        "skipped_count": sum(row["status"] == "skipped" for row in rendered),
        "rows": rendered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--exclude", action="append", default=[], type=Path)
    parser.add_argument("--policy", type=Path, help="Frozen effective policy JSON.")
    args = parser.parse_args()
    try:
        if args.apply:
            require_empty_directory_apply(load_effective_policy(args.policy))
        result = cleanup(args.root, args.apply, tuple(args.exclude))
    except (OSError, ValueError) as error:
        json.dump({"version": 1, "status": "FAILED", "error": str(error)}, sys.stderr, ensure_ascii=False, indent=2)
        sys.stderr.write("\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
