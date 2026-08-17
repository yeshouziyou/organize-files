#!/usr/bin/env python3
"""Apply a separately approved exact-duplicate deletion plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from _common import resolve_relative_within_root, sha256_file


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def preflight(plan_path: Path) -> tuple[Path, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("version") != 1 or not isinstance(plan.get("rows"), list):
        raise ValueError("deletion plan must be version 1 with rows")
    root = Path(plan["root"]).resolve(strict=True)
    report_path = Path(plan["hash_report"]).resolve(strict=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("version") != 1 or Path(report.get("root", "")).resolve(strict=False) != root:
        raise ValueError("hash report does not match deletion root")
    groups = {
        row["sha256"]: set(row["paths"])
        for row in report.get("duplicate_groups", [])
    }
    hashed = {
        row["relative_path"]: row for row in report.get("hashed_files", [])
    }
    prepared: list[dict] = []
    seen_deletes: set[str] = set()
    scheduled_delete_keys = {
        value.casefold()
        for row in plan["rows"]
        if isinstance((value := row.get("delete")), str) and value
    }
    for index, row in enumerate(plan["rows"], start=1):
        delete_relative = row.get("delete")
        keep_relative = row.get("keep")
        digest = row.get("sha256")
        if not all(isinstance(value, str) and value for value in (delete_relative, keep_relative, digest)):
            raise ValueError(f"row {index}: delete, keep, and sha256 are required")
        if delete_relative == keep_relative:
            raise ValueError(f"row {index}: delete and keep paths must differ")
        if keep_relative.casefold() in scheduled_delete_keys:
            raise ValueError(f"row {index}: retained path is also scheduled for deletion: {keep_relative}")
        if delete_relative.casefold() in seen_deletes:
            raise ValueError(f"row {index}: duplicate delete path")
        seen_deletes.add(delete_relative.casefold())
        group_paths = groups.get(digest, set())
        if delete_relative not in group_paths or keep_relative not in group_paths:
            raise ValueError(f"row {index}: paths are not in the approved exact-duplicate group")
        delete_path = resolve_relative_within_root(root, delete_relative, label="duplicate delete path")
        keep_path = resolve_relative_within_root(root, keep_relative, label="duplicate keep path")
        for relative, path in ((delete_relative, delete_path), (keep_relative, keep_path)):
            expected = hashed.get(relative)
            if expected is None or not path.is_file() or path.is_symlink():
                raise ValueError(f"row {index}: duplicate candidate is unavailable: {relative}")
            current = path.stat()
            if current.st_size != expected.get("size") or current.st_mtime_ns != expected.get("mtime_ns"):
                raise ValueError(f"row {index}: duplicate candidate changed: {relative}")
            if sha256_file(path) != digest:
                raise ValueError(f"row {index}: duplicate hash changed: {relative}")
        prepared.append(
            {
                "row": index,
                "delete": delete_path,
                "keep": keep_path,
                "sha256": digest,
            }
        )
    return root, prepared


def render(
    prepared: list[dict],
    status: str,
    deleted: list[Path] | None = None,
    error: str | None = None,
) -> dict:
    deleted = deleted or []
    payload = {
        "version": 1,
        "status": status,
        "rollback_supported": False,
        "deleted_count": len(deleted),
        "deleted_paths": [str(path) for path in deleted],
        "remaining_paths": [
            str(row["delete"])
            for row in prepared
            if row["delete"] not in deleted
        ],
        "rows": [
            {
                "row": row["row"],
                "delete": str(row["delete"]),
                "keep": str(row["keep"]),
                "sha256": row["sha256"],
            }
            for row in prepared
        ],
    }
    if error is not None:
        payload["error"] = error
    return payload


def apply_deletions(prepared: list[dict]) -> tuple[dict, int]:
    deleted: list[Path] = []
    for row in prepared:
        try:
            if not row["keep"].is_file() or sha256_file(row["keep"]) != row["sha256"]:
                raise ValueError(f"retained duplicate copy changed before deletion: {row['keep']}")
            if not row["delete"].is_file() or sha256_file(row["delete"]) != row["sha256"]:
                raise ValueError(f"duplicate selected for deletion changed before deletion: {row['delete']}")
            row["delete"].unlink()
            deleted.append(row["delete"])
        except Exception as error:
            status = "DUPLICATE_DELETION_PARTIAL" if deleted else "DUPLICATE_DELETION_FAILED"
            return render(prepared, status, deleted, str(error)), 1
    return render(prepared, "DUPLICATE_DELETION_COMPLETED", deleted), 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmed", action="store_true", help="Assert that the user separately approved these exact deletion rows.")
    args = parser.parse_args()
    try:
        if args.apply and not args.confirmed:
            raise ValueError("--apply requires --confirmed after separate deletion approval")
        _, prepared = preflight(args.plan.resolve(strict=True))
        if args.apply:
            result, code = apply_deletions(prepared)
            json.dump(result, sys.stdout if code == 0 else sys.stderr, ensure_ascii=False, indent=2)
            (sys.stdout if code == 0 else sys.stderr).write("\n")
            return code
        else:
            result = render(prepared, "PREVIEW_ONLY")
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump(
            {"version": 1, "status": "DUPLICATE_DELETION_FAILED", "error": str(error)},
            sys.stderr,
            ensure_ascii=False,
            indent=2,
        )
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
