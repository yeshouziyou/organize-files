#!/usr/bin/env python3
"""Fail when a skill package contains local identity or generated artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


GENERIC_FORBIDDEN_CONTENT = (
    ("local-absolute-path", ("C:" + "\\" + "Users" + "\\").encode("utf-8")),
    ("local-absolute-path", ("/" + "Users" + "/").encode("utf-8")),
)
IGNORED_DIRECTORY_NAMES = {".git"}
REQUIRED_IGNORE_RULES = {"__pycache__/", "*.py[cod]"}
PORTABLE_DENYLIST_LOCATIONS = (
    Path.home() / ".config" / "organize-files" / "private-denylist.json",
    Path.home() / ".agents" / "organize-files-private-denylist.json",
)
LEGACY_CODEX_DENYLIST = Path.home() / ".codex" / "organize-files-private-denylist.json"


def discover_private_denylist() -> Path | None:
    environment_path = os.environ.get("ORGANIZE_FILES_DENYLIST")
    if environment_path:
        return Path(environment_path).expanduser().resolve(strict=True)
    for candidate in (*PORTABLE_DENYLIST_LOCATIONS, LEGACY_CODEX_DENYLIST):
        if candidate.is_file():
            return candidate.resolve(strict=True)
    return None


def load_denylist(path: Path | None) -> tuple[tuple[str, bytes], ...]:
    patterns = list(GENERIC_FORBIDDEN_CONTENT)
    if path is None:
        return tuple(patterns)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("patterns"), list):
        raise ValueError("denylist must be version 1 with a patterns list")
    for index, row in enumerate(payload["patterns"], start=1):
        kind = row.get("kind")
        value = row.get("value")
        if not isinstance(kind, str) or not kind or not isinstance(value, str) or not value:
            raise ValueError(f"denylist pattern {index} requires kind and value")
        patterns.append((kind, value.encode("utf-8")))
    return tuple(patterns)


def audit(root: Path, forbidden_content: tuple[tuple[str, bytes], ...]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    gitignore = root / ".gitignore"
    existing_rules = set()
    if gitignore.is_file():
        existing_rules = {
            line.strip()
            for line in gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    for rule in sorted(REQUIRED_IGNORE_RULES):
        if rule not in existing_rules:
            findings.append(
                {"kind": "missing-ignore-rule", "path": ".gitignore", "detail": rule}
            )
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRECTORY_NAMES for part in parts):
            continue
        if "__pycache__" in parts:
            if path.is_dir() and path.name == "__pycache__":
                findings.append({"kind": "compiled-cache", "path": relative})
            continue
        if path.is_dir():
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.casefold() in {".pyc", ".pyo"}:
            findings.append({"kind": "compiled-cache", "path": relative})
            continue
        try:
            content = path.read_bytes()
        except OSError as error:
            findings.append({"kind": "unreadable-file", "path": relative, "detail": str(error)})
            continue
        for kind, needle in forbidden_content:
            if needle in content:
                findings.append({"kind": kind, "path": relative})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--denylist", type=Path, help="Optional repository-external private denylist JSON.")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("root must be an existing directory")
        denylist_path = args.denylist.resolve(strict=True) if args.denylist else discover_private_denylist()
        findings = audit(root, load_denylist(denylist_path))
        payload = {
            "version": 1,
            "status": "PUBLIC_RELEASE_AUDIT_FAILED" if findings else "PUBLIC_RELEASE_AUDIT_PASSED",
            "root": str(root),
            "finding_count": len(findings),
            "findings": findings,
        }
        stream = sys.stderr if findings else sys.stdout
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        return 1 if findings else 0
    except Exception as error:
        json.dump(
            {"version": 1, "status": "PUBLIC_RELEASE_AUDIT_ERROR", "error": str(error)},
            sys.stderr,
            ensure_ascii=False,
            indent=2,
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
