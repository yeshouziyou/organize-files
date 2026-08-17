"""Shared low-cost primitives for organize-files command-line tools."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path


IMAGE_EXTENSIONS = frozenset(
    {
        ".bmp", ".gif", ".heic", ".heif", ".ico", ".jpeg", ".jpg", ".png",
        ".svg", ".tif", ".tiff", ".webp",
    }
)
HASH_CHUNK_SIZE = 1024 * 1024


def resolve_relative_within_root(root: Path, relative: str, *, label: str = "path") -> Path:
    """Resolve one user-controlled relative path without crossing the selected root."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must be a safe relative path: {relative}")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes selected root: {relative}") from exc
    return resolved


def generated_metadata_kind(path: Path) -> str | None:
    name = path.name
    if name.casefold() == ".ds_store":
        return "macos-ds-store"
    if name.startswith("._"):
        return "macos-appledouble"
    if name.startswith("~$"):
        return "office-lock"
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def load_effective_policy(path: Path | None, resolver: Path, required_field: str) -> dict:
    if path is not None:
        policy = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    else:
        resolved = subprocess.run(
            [sys.executable, str(resolver)],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if resolved.returncode != 0:
            raise ValueError(f"policy resolution failed: {resolved.stderr.strip()}")
        policy = json.loads(resolved.stdout)
    if policy.get("version") != 1 or required_field not in policy:
        raise ValueError(f"policy must be version 1 with {required_field}")
    return policy


def compact_inventory_summary(inventory: dict, inventory_path: Path | None = None) -> dict:
    generated = inventory.get("generated_metadata_candidates", [])
    total = inventory.get("known_current_file_count")
    if total is None:
        total = inventory.get("total_file_count", inventory["file_count"] + len(generated))
    payload = {
        "version": inventory["version"],
        "root": inventory["root"],
        "total_file_count": total,
        "file_count": inventory["file_count"],
        "extension_counts": inventory["extension_counts"],
        "top_level_summary": inventory["top_level_summary"],
        "filename_review_candidate_count": len(inventory["filename_review_candidates"]),
        "image_audit_candidate_count": len(inventory["image_audit_candidates"]),
        "generated_metadata_candidate_count": len(generated),
        "empty_directory_candidate_count": len(inventory.get("empty_directory_candidates", [])),
        "same_size_candidate_group_count": len(inventory["same_size_candidate_groups"]),
        "duplicate_candidates_computed": inventory.get("duplicate_candidates_computed", False),
        "skipped_boundaries": inventory["skipped_boundaries"],
        "content_hashes_computed": inventory.get("content_hashes_computed", 0),
    }
    if inventory_path is not None:
        payload["inventory_path"] = str(inventory_path)
    return payload
