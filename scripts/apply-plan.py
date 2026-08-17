#!/usr/bin/env python3
"""Safely apply an approved rename/move plan inside one selected root."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from _common import resolve_relative_within_root


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


MUTATING_ACTIONS = {"仅改名", "仅移动", "新建文件夹后移动", "移动并改名"}
NON_MUTATING_ACTIONS = {"保持不变", "跳过"}


def load_plan(plan_path: Path) -> tuple[Path, list[dict]]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if payload.get("version") != 2:
        raise ValueError("unsupported plan version")
    root = Path(payload["root"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("plan root must be an existing directory")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("plan rows must be a list")
    return root, rows


def preflight(root: Path, rows: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    for index, row in enumerate(rows, start=1):
        action = row.get("action")
        if action in NON_MUTATING_ACTIONS:
            continue
        if action not in MUTATING_ACTIONS:
            raise ValueError(f"row {index}: unsupported action {action!r}")
        source = resolve_relative_within_root(root, row["source"], label="plan source")
        target = resolve_relative_within_root(root, row["target"], label="plan target")
        source_key = os.path.normcase(str(source))
        target_key = os.path.normcase(str(target))
        if source_key == target_key:
            raise ValueError(f"row {index}: source and target resolve to the same path")
        if source_key in seen_sources or target_key in seen_targets:
            raise ValueError(f"row {index}: duplicate source or target")
        seen_sources.add(source_key)
        seen_targets.add(target_key)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"row {index}: source is not a regular file: {source}")
        if target.exists():
            raise FileExistsError(f"row {index}: target already exists: {target}")
        before = source.stat()
        expected_size = row.get("expected_size")
        expected_mtime_ns = row.get("expected_mtime_ns")
        if not isinstance(expected_size, int) or not isinstance(expected_mtime_ns, int):
            raise ValueError(f"row {index}: approved source state is required")
        if before.st_size != expected_size or before.st_mtime_ns != expected_mtime_ns:
            raise ValueError(f"row {index}: approved source state changed: {source}")
        prepared.append(
            {
                "row": index,
                "action": action,
                "source": source,
                "target": target,
                "size": expected_size,
                "mtime_ns": expected_mtime_ns,
            }
        )
    return prepared


def render(prepared: list[dict], status: str, created_dirs: list[Path]) -> dict:
    return {
        "version": 2,
        "status": status,
        "content_hashes_computed": 0,
        "created_directories": [str(path) for path in created_dirs],
        "rows": [
            {
                "row": item["row"],
                "action": item["action"],
                "source": str(item["source"]),
                "target": str(item["target"]),
                "size": item["size"],
                "mtime_ns": item["mtime_ns"],
            }
            for item in prepared
        ],
    }


def move_no_replace(source: Path, target: Path) -> None:
    """Move one regular file without overwriting an existing target."""
    if os.name == "nt":
        # Windows rename fails when the destination exists and works on
        # filesystems such as NTFS/exFAT and local sync folders without
        # requiring hard-link support.
        os.rename(source, target)
        return
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as error:
        raise OSError(f"POSIX no-replace move requires same-volume hard-link support: {error}") from error
    try:
        source.unlink()
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def remove_created_empty_directories(created_dirs: list[Path]) -> list[str]:
    errors: list[str] = []
    seen: set[Path] = set()
    for path in reversed(created_dirs):
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.exists():
                path.rmdir()
        except OSError as error:
            errors.append(f"{path}: {error}")
    return errors


def apply(prepared: list[dict]) -> tuple[dict, int]:
    moved: list[dict] = []
    created_dirs: list[Path] = []
    try:
        for item in prepared:
            parent = item["target"].parent
            missing: list[Path] = []
            cursor = parent
            while not cursor.exists():
                missing.append(cursor)
                cursor = cursor.parent
            parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(reversed(missing))
            if item["target"].exists():
                raise FileExistsError(f"target appeared during execution: {item['target']}")
            current = item["source"].stat()
            if current.st_size != item["size"] or current.st_mtime_ns != item["mtime_ns"]:
                raise RuntimeError(f"approved source state changed during execution: {item['source']}")
            move_no_replace(item["source"], item["target"])
            moved.append(item)
            after = item["target"].stat()
            if item["source"].exists() or after.st_size != item["size"] or after.st_mtime_ns != item["mtime_ns"]:
                raise RuntimeError(f"post-move metadata verification failed: {item['target']}")
    except Exception as error:
        rollback_errors: list[str] = []
        for item in reversed(moved):
            try:
                if item["target"].exists() and not item["source"].exists():
                    item["source"].parent.mkdir(parents=True, exist_ok=True)
                    move_no_replace(item["target"], item["source"])
            except Exception as rollback_error:  # pragma: no cover - exceptional host failure
                rollback_errors.append(str(rollback_error))
        directory_cleanup_errors = remove_created_empty_directories(created_dirs)
        all_rollback_errors = rollback_errors + directory_cleanup_errors
        result = render(prepared, "ROLLED_BACK" if not all_rollback_errors else "ROLLBACK_INCOMPLETE", created_dirs)
        result["error"] = str(error)
        result["rollback_errors"] = all_rollback_errors
        return result, 1
    return render(prepared, "PLAN_EXECUTION_COMPLETED", created_dirs), 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        root, rows = load_plan(args.plan.resolve(strict=True))
        prepared = preflight(root, rows)
        if args.dry_run:
            result, code = render(prepared, "PREVIEW_ONLY", []), 0
        else:
            result, code = apply(prepared)
    except Exception as error:
        result, code = {"version": 2, "status": "PREFLIGHT_FAILED", "error": str(error)}, 1
    json.dump(result, sys.stdout if code == 0 else sys.stderr, ensure_ascii=False, indent=2)
    (sys.stdout if code == 0 else sys.stderr).write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
