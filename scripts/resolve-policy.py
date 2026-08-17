#!/usr/bin/env python3
"""Resolve the public default policy and an optional local preset selection."""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import re
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = SKILL_ROOT / "config" / "default.json"
PRESET_DIRECTORY = SKILL_ROOT / "config" / "presets"
PORTABLE_CONFIG_LOCATIONS = (
    Path.home() / ".config" / "organize-files" / "config.json",
    Path.home() / ".agents" / "organize-files.local.json",
)
LEGACY_CODEX_CONFIG = Path.home() / ".codex" / "organize-files.local.json"
PRESET_NAME_RE = re.compile(r"^[a-z0-9-]+$")
POLICY_VALUES = {"preview", "auto", "auto-if-safe", "confirm"}
PLATFORM_NAMES = {"windows", "macos", "linux", "other"}


def discover_local_config() -> Path | None:
    environment_path = os.environ.get("ORGANIZE_FILES_CONFIG")
    if environment_path:
        return Path(environment_path).expanduser().resolve(strict=True)
    for candidate in (*PORTABLE_CONFIG_LOCATIONS, LEGACY_CODEX_CONFIG):
        if candidate.is_file():
            return candidate.resolve(strict=True)
    return None


def runtime_platform_name() -> str:
    value = platform.system().casefold()
    if value == "windows":
        return "windows"
    if value == "darwin":
        return "macos"
    if value == "linux":
        return "linux"
    return "other"


def load_json(path: Path, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError(f"{label} must use version 1")
    return payload


def merge_policy(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in {"version", "name", "preset"}:
            continue
        if key == "generated_metadata":
            if not isinstance(value, dict):
                raise ValueError("generated_metadata must be an object")
            merged.setdefault(key, {}).update(value)
        else:
            merged[key] = value
    return merged


def validate_policy(policy: dict) -> None:
    generated = policy.get("generated_metadata")
    if not isinstance(generated, dict):
        raise ValueError("generated_metadata policy is required")
    for kind in ("macos-ds-store", "macos-appledouble", "office-lock"):
        if generated.get(kind) not in POLICY_VALUES:
            raise ValueError(f"unsupported policy for {kind}: {generated.get(kind)!r}")
    for field in ("empty_directories", "ordinary_file_deletion", "duplicate_deletion"):
        if policy.get(field) not in POLICY_VALUES:
            raise ValueError(f"unsupported policy for {field}: {policy.get(field)!r}")


def apply_platform_safety(policy: dict, runtime_platform: str) -> None:
    adjustments: list[str] = []
    if runtime_platform != "windows":
        generated = policy["generated_metadata"]
        protected_kinds = ("macos-ds-store", "macos-appledouble", "office-lock")
        if any(generated.get(kind) != "preview" for kind in protected_kinds):
            for kind in protected_kinds:
                generated[kind] = "preview"
            adjustments.append(
                "macos-generated-metadata-protection"
                if runtime_platform == "macos"
                else "non-windows-generated-metadata-protection"
            )
    policy["runtime_platform"] = runtime_platform
    policy["policy_adjustments"] = adjustments


def resolve_policy(local_config: Path | None, runtime_platform: str | None = None) -> dict:
    policy = load_json(DEFAULT_POLICY, "default policy")
    source = "public-safe-default"
    if local_config is not None:
        local = load_json(local_config, "local config")
        preset = local.get("preset")
        if not isinstance(preset, str) or not PRESET_NAME_RE.fullmatch(preset):
            raise ValueError(f"unknown preset: {preset!r}")
        preset_path = PRESET_DIRECTORY / f"{preset}.json"
        if not preset_path.is_file():
            raise ValueError(f"unknown preset: {preset}")
        policy = merge_policy(policy, load_json(preset_path, "preset"))
        policy = merge_policy(policy, local)
        source = str(local_config)
    validate_policy(policy)
    selected_platform = runtime_platform or runtime_platform_name()
    if selected_platform not in PLATFORM_NAMES:
        raise ValueError(f"unsupported runtime platform: {selected_platform!r}")
    apply_platform_safety(policy, selected_platform)
    policy["version"] = 1
    policy["policy_source"] = source
    return policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--local-config", type=Path)
    group.add_argument("--no-local-config", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--platform", choices=sorted(PLATFORM_NAMES), help="Override runtime platform for testing or packaging checks.")
    args = parser.parse_args()
    try:
        local_config: Path | None
        if args.no_local_config:
            local_config = None
        elif args.local_config:
            local_config = args.local_config.resolve(strict=True)
        else:
            local_config = discover_local_config()
        policy = resolve_policy(local_config, args.platform)
        if args.output:
            output = args.output.resolve(strict=False)
            if not output.parent.is_dir():
                raise ValueError("--output parent directory must already exist")
            output.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        json.dump(policy, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump(
            {"version": 1, "status": "POLICY_RESOLUTION_FAILED", "error": str(error)},
            sys.stderr,
            ensure_ascii=False,
            indent=2,
        )
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
