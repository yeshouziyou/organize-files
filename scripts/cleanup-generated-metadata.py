#!/usr/bin/env python3
"""Remove approved macOS metadata and stale Office lock files from one root."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

sys.dont_write_bytecode = True
from _common import generated_metadata_kind as candidate_kind
from _common import load_effective_policy as load_policy


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ONE_DAY_SECONDS = 24 * 60 * 60
POLICY_RESOLVER = Path(__file__).with_name("resolve-policy.py")


def load_effective_policy(path: Path | None) -> dict:
    policy = load_policy(path, POLICY_RESOLVER, "generated_metadata")
    if not isinstance(policy["generated_metadata"], dict):
        raise ValueError("generated_metadata policy must be an object")
    return policy


def require_generated_metadata_apply(policy: dict) -> None:
    generated = policy["generated_metadata"]
    blocked = [
        kind
        for kind in ("macos-ds-store", "macos-appledouble", "office-lock")
        if generated.get(kind) not in {"auto", "auto-if-safe"}
    ]
    if blocked:
        raise ValueError(f"policy forbids apply for generated metadata: {blocked}")


def exclusive_open_available(path: Path) -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(path), 0, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return True


def office_lock_is_safe_to_delete(lock_path: Path) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "office-lock-safety-check-requires-windows"
    if not exclusive_open_available(lock_path):
        return False, "lock-file-is-active-or-unavailable"
    companion = lock_path.with_name(lock_path.name[2:])
    if companion.exists():
        if companion.is_file() and exclusive_open_available(companion):
            return True, "companion-document-not-active"
        return False, "companion-document-is-active-or-unavailable"
    if time.time() - lock_path.stat().st_mtime >= ONE_DAY_SECONDS:
        return True, "missing-companion-and-lock-older-than-24h"
    return False, "missing-companion-but-lock-is-recent"


def inventory_target(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"inventory path is not a safe relative path: {relative_path}")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"inventory path escapes selected root: {relative_path}") from exc
    return resolved


def update_inventory_after_cleanup(
    inventory_path: Path, inventory: dict, result: dict
) -> None:
    deleted_paths = {
        row["relative_path"] for row in result["rows"] if row["status"] == "deleted"
    }
    inventory["generated_metadata_candidates"] = [
        row
        for row in inventory["generated_metadata_candidates"]
        if row["relative_path"] not in deleted_paths
    ]
    inventory["known_current_file_count"] = inventory.get("file_count", len(inventory.get("files", []))) + len(inventory["generated_metadata_candidates"])
    inventory["total_file_count"] = inventory["known_current_file_count"]
    inventory["generated_metadata_cleanup"] = {
        "mode": "apply",
        "deleted_count": result["deleted_count"],
        "skipped_count": result["skipped_count"],
        "deleted_paths": sorted(deleted_paths, key=str.casefold),
    }
    temporary = inventory_path.with_name(f"{inventory_path.name}.tmp")
    temporary.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, inventory_path)


def cleanup_from_inventory(root: Path, inventory_path: Path, apply: bool) -> dict:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != 1:
        raise ValueError("inventory is not a supported version-1 scan")
    inventory_root = Path(inventory["root"]).resolve(strict=True)
    if inventory_root != root:
        raise ValueError("inventory root does not match selected root")
    candidates = inventory.get("generated_metadata_candidates")
    if not isinstance(candidates, list):
        raise ValueError("inventory does not contain generated_metadata_candidates")

    rows: list[dict] = []
    for candidate in candidates:
        relative_path = candidate.get("relative_path")
        expected_kind = candidate.get("kind")
        if not isinstance(relative_path, str) or not isinstance(expected_kind, str):
            raise ValueError("inventory generated-metadata row is malformed")
        path = inventory_target(root, relative_path)
        status = "skipped"
        reason = "inventory-candidate-unchecked"
        if not path.exists():
            reason = "inventory-candidate-missing"
        elif not path.is_file() or path.is_symlink():
            reason = "inventory-candidate-is-not-regular-file"
        elif candidate_kind(path) != expected_kind:
            reason = "inventory-candidate-kind-changed"
        else:
            current = path.stat()
            if (
                current.st_size != candidate.get("size")
                or current.st_mtime_ns != candidate.get("mtime_ns")
            ):
                reason = "inventory-candidate-metadata-changed"
            else:
                if expected_kind == "office-lock":
                    safe, reason = office_lock_is_safe_to_delete(path)
                else:
                    safe, reason = True, "approved-generated-metadata"
                status = "would-delete" if safe else "skipped"
                if apply and safe:
                    try:
                        path.unlink()
                        status = "deleted"
                    except OSError as error:
                        status = "skipped"
                        reason = f"delete-failed: {error}"
        rows.append(
            {
                "relative_path": relative_path,
                "kind": expected_kind,
                "status": status,
                "reason": reason,
            }
        )

    rows.sort(key=lambda row: row["relative_path"].casefold())
    result = {
        "version": 1,
        "root": str(root),
        "source": "inventory",
        "inventory_path": str(inventory_path),
        "mode": "apply" if apply else "dry-run",
        "candidate_count": len(rows),
        "deleted_count": sum(row["status"] == "deleted" for row in rows),
        "would_delete_count": sum(row["status"] == "would-delete" for row in rows),
        "skipped_count": sum(row["status"] == "skipped" for row in rows),
        "rows": rows,
    }
    if apply:
        update_inventory_after_cleanup(inventory_path, inventory, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Use exact generated-metadata candidates from a saved scan without rescanning the root.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--policy", type=Path, help="Frozen effective policy JSON.")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("root must be an existing directory")
        if args.apply:
            require_generated_metadata_apply(load_effective_policy(args.policy))
        if not args.inventory:
            raise ValueError("--inventory is required for dry-run and apply")
        inventory_path = args.inventory.resolve(strict=True)
        result = cleanup_from_inventory(root, inventory_path, args.apply)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump(
            {"version": 1, "status": "FAILED", "error": str(error)},
            sys.stderr,
            ensure_ascii=False,
            indent=2,
        )
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
