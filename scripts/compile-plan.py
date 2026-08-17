#!/usr/bin/env python3
"""Validate evidence coverage and compile declarative decisions into an apply plan."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ACTIONS = {"保持不变", "仅改名", "仅移动", "新建文件夹后移动", "移动并改名", "跳过"}
MUTATING_ACTIONS = {"仅改名", "仅移动", "新建文件夹后移动", "移动并改名"}
COMPLETE_EVIDENCE_BY_KIND = {
    "document": {"content-extracted", "legacy-content-extracted"},
    "image": {"visual-audited"},
    "media": {"media-audited"},
    "archive": {"archive-indexed", "archive-content-extracted"},
    "unknown": {"content-extracted", "metadata-reviewed"},
}
FIELDS = (
    "source", "category", "new_name", "target", "classification_basis",
    "classification_confidence", "date_basis", "date_confidence", "action", "risk",
)
REQUIRED_FIELDS = FIELDS + ("resolution",)
RESOLUTIONS = {"compliant", "changed", "undecided", "exclusion"}
HEADERS = (
    "原路径", "建议分类", "建议文件名", "最终路径", "分类依据",
    "分类可信度", "日期依据", "日期可信度", "建议动作", "风险或说明",
)
DATE_FIELD_PATTERN = (
    r"(?:\d{8}-\d{8}|\d{6}-\d{6}|\d{4}-\d{4}|"
    r"\d{4}Q[1-4]|\d{8}|\d{6}|\d{4})"
)
BRACKETED_DATE_FOLDER_RE = re.compile(
    rf"^[\[【](?P<date>{DATE_FIELD_PATTERN})[\]】](?P<title>.*)$"
)
DATE_PREFIX_FOLDER_RE = re.compile(
    rf"^(?P<date>{DATE_FIELD_PATTERN})(?P<rest>.*)$"
)


def relative_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a safe root-relative path: {value}")
    return path.as_posix()


def real_path(root: Path, relative: str) -> Path:
    resolved = (root / Path(relative)).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes selected root: {relative}") from exc
    return resolved


def dated_folder_issue(name: str) -> str | None:
    if BRACKETED_DATE_FOLDER_RE.fullmatch(name):
        return "dated folder uses brackets instead of 日期_标题"
    match = DATE_PREFIX_FOLDER_RE.fullmatch(name)
    if not match:
        return None
    rest = match.group("rest")
    if not rest.startswith("_") or len(rest) == 1:
        return "dated folder must use 日期_标题 with a non-empty title"
    return None


def inspect_target_folders(root: Path, target: str) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    current = root
    for part in PurePosixPath(target).parent.parts:
        current = current / part
        issue = dated_folder_issue(part)
        if issue is None:
            continue
        relative = current.relative_to(root).as_posix()
        if current.is_dir():
            warnings.append(
                {
                    "path": relative,
                    "issue": issue,
                    "handling": "existing legacy folder reused without renaming; report only",
                }
            )
            continue
        raise ValueError(
            f"new or renamed dated folder must use 日期_标题; invalid folder: {relative}"
        )
    return warnings


def load_version_one(path: Path, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("files" if label == "evidence" else "rows"), list):
        collection = "files" if label == "evidence" else "rows"
        raise ValueError(f"{label} must be version 1 with a {collection} list")
    return payload


def missing_evidence_proof(row: dict) -> str | None:
    kind = row.get("evidence_kind", "unknown")
    status = row.get("evidence_status")
    if kind == "image" and status == "visual-audited" and not str(row.get("visual_summary", "")).strip():
        return "visual-audited evidence requires visual_summary"
    if kind == "media" and status == "media-audited" and not str(row.get("media_summary", "")).strip():
        return "media-audited evidence requires media_summary"
    if kind == "document" and status in {"content-extracted", "legacy-content-extracted"} and not str(row.get("content_excerpt", "")).strip():
        return f"{status} evidence requires content_excerpt"
    if kind == "archive" and status == "archive-indexed" and not isinstance(row.get("archive_entries"), list):
        return "archive-indexed evidence requires archive_entries"
    if kind == "archive" and status == "archive-content-extracted" and not str(row.get("archive_summary", "")).strip():
        return "archive-content-extracted evidence requires archive_summary"
    if kind == "unknown" and status == "content-extracted" and not str(row.get("content_excerpt", "")).strip():
        return "content-extracted evidence requires content_excerpt"
    if kind == "unknown" and status == "metadata-reviewed" and not str(row.get("notes", "")).strip():
        return "metadata-reviewed evidence requires notes"
    return None


def require_complete_evidence(index: int, row: dict, evidence_row: dict) -> None:
    kind = evidence_row.get("evidence_kind", "unknown")
    status = evidence_row.get("evidence_status")
    allowed_statuses = COMPLETE_EVIDENCE_BY_KIND.get(kind)
    if allowed_statuses is None:
        raise ValueError(f"decision row {index}: unsupported evidence_kind {kind!r}")
    proof_error = missing_evidence_proof(evidence_row)
    if status in allowed_statuses and proof_error is None:
        return
    if status in allowed_statuses and proof_error:
        raise ValueError(f"decision row {index}: {proof_error}")
    if kind == "image":
        raise ValueError(f"decision row {index}: image requires visual-audited evidence, got {status!r}")
    if kind == "media":
        raise ValueError(f"decision row {index}: media requires media-audited evidence, got {status!r}")
    raise ValueError(f"decision row {index}: incomplete evidence status {status!r} for {row['source']}")


def reconcile_inventory(evidence: dict, root: Path) -> None:
    inventory_value = evidence.get("inventory_path")
    if not isinstance(inventory_value, str) or not inventory_value:
        raise ValueError("evidence inventory_path is required")
    inventory_path = Path(inventory_value).resolve(strict=True)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != 1 or not isinstance(inventory.get("files"), list):
        raise ValueError("referenced inventory must be version 1 with a files list")
    if Path(inventory.get("root", "")).resolve(strict=False) != root:
        raise ValueError("inventory/evidence root mismatch")
    inventory_rows = {row["relative_path"].casefold(): row for row in inventory["files"]}
    evidence_rows = {row["relative_path"].casefold(): row for row in evidence["files"]}
    if set(inventory_rows) != set(evidence_rows):
        missing = sorted(
            (row["relative_path"] for key, row in inventory_rows.items() if key not in evidence_rows),
            key=str.casefold,
        )
        extra = sorted(
            (row["relative_path"] for key, row in evidence_rows.items() if key not in inventory_rows),
            key=str.casefold,
        )
        raise ValueError(f"inventory/evidence path mismatch; missing={missing[:10]}, extra={extra[:10]}")
    for key, inventory_row in inventory_rows.items():
        evidence_row = evidence_rows[key]
        evidence_key = evidence_row.get("evidence_key") or {}
        if (
            evidence_key.get("relative_path", "").casefold() != inventory_row["relative_path"].casefold()
            or evidence_key.get("size") != inventory_row.get("size")
            or evidence_key.get("mtime_ns") != inventory_row.get("mtime_ns")
            or bool(evidence_key.get("reparse_point")) != bool(inventory_row.get("reparse_point"))
        ):
            raise ValueError(f"inventory/evidence key mismatch: {inventory_row['relative_path']}")


def validate(evidence: dict, decisions: dict) -> tuple[Path, list[dict], int, list[dict[str, str]]]:
    root = Path(evidence["root"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("evidence root is not an existing directory")
    reconcile_inventory(evidence, root)
    evidence_rows: dict[str, dict] = {}
    for index, row in enumerate(evidence["files"], start=1):
        source = relative_path(row.get("relative_path"), f"evidence row {index} relative_path")
        key = source.casefold()
        if key in evidence_rows:
            raise ValueError(f"duplicate evidence path: {source}")
        evidence_rows[key] = row

    decision_rows: dict[str, dict] = {}
    for index, row in enumerate(decisions["rows"], start=1):
        missing = [field for field in REQUIRED_FIELDS if field not in row or not isinstance(row[field], str) or not row[field].strip()]
        if missing:
            raise ValueError(f"decision row {index}: missing fields {missing}")
        source = relative_path(row["source"], f"decision row {index} source")
        key = source.casefold()
        if key in decision_rows:
            raise ValueError(f"decision row {index}: duplicate source {source}")
        decision_rows[key] = {**row, "source": source}

    missing = sorted(
        (row["relative_path"] for key, row in evidence_rows.items() if key not in decision_rows),
        key=str.casefold,
    )
    if missing:
        raise ValueError(f"missing decision rows for evidence paths: {missing[:10]}")
    extra = sorted(
        (row["source"] for key, row in decision_rows.items() if key not in evidence_rows),
        key=str.casefold,
    )
    if extra:
        raise ValueError(f"decisions reference paths absent from evidence: {extra[:10]}")

    prepared: list[dict] = []
    undecided_count = 0
    target_keys: set[str] = set()
    source_keys = set(evidence_rows)
    legacy_folder_warnings: dict[str, dict[str, str]] = {}
    for index, original in enumerate(decisions["rows"], start=1):
        row = decision_rows[relative_path(original["source"], "source").casefold()]
        evidence_row = evidence_rows[row["source"].casefold()]
        action = row["action"]
        if action not in ACTIONS:
            raise ValueError(f"decision row {index}: unsupported action {action!r}")
        file_class = evidence_row.get("file_class")
        status = evidence_row.get("evidence_status")
        resolution = row["resolution"]
        if resolution not in RESOLUTIONS:
            raise ValueError(f"decision row {index}: unsupported resolution {resolution!r}")
        if file_class == "explicit-exclusion":
            if status != "structural-exclusion" or not evidence_row.get("exclusion_reason"):
                raise ValueError(f"decision row {index}: exclusion lacks structural evidence")
            if action != "跳过" or resolution != "exclusion":
                raise ValueError(f"decision row {index}: explicit exclusion must use resolution=exclusion and action=跳过")
        elif file_class == "applicable-human":
            if resolution == "undecided":
                if action != "跳过":
                    raise ValueError(f"decision row {index}: undecided row must use 跳过")
                undecided_count += 1
            elif resolution == "compliant":
                require_complete_evidence(index, row, evidence_row)
                if action != "保持不变":
                    raise ValueError(f"decision row {index}: compliant row must use 保持不变")
            elif resolution == "changed":
                require_complete_evidence(index, row, evidence_row)
                if action not in MUTATING_ACTIONS:
                    raise ValueError(f"decision row {index}: changed row requires a mutating action")
            else:
                raise ValueError(f"decision row {index}: applicable human file cannot use resolution={resolution}")
        else:
            raise ValueError(f"decision row {index}: unsupported file_class {file_class!r}")

        target = relative_path(row["target"], f"decision row {index} target")
        row = {**row, "target": target}
        source_path = real_path(root, row["source"])
        target_path = real_path(root, target)
        for warning in inspect_target_folders(root, target):
            legacy_folder_warnings.setdefault(warning["path"].casefold(), warning)
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError(f"decision row {index}: source is not a regular file: {row['source']}")
        stat = source_path.stat()
        if stat.st_size != evidence_row.get("size") or stat.st_mtime_ns != evidence_row.get("mtime_ns"):
            raise ValueError(f"decision row {index}: evidence key changed for {row['source']}")
        if action in MUTATING_ACTIONS:
            if os.path.normcase(str(source_path)) == os.path.normcase(str(target_path)):
                raise ValueError(f"decision row {index}: mutating action resolves to same path")
            target_key = os.path.normcase(str(target_path))
            if target_key in target_keys:
                raise ValueError(f"decision row {index}: duplicate target {target}")
            target_keys.add(target_key)
            if target_path.exists():
                raise ValueError(f"decision row {index}: target already exists: {target}")
            if target.casefold() in source_keys:
                raise ValueError(f"decision row {index}: target is another inventoried source: {target}")
        elif target.casefold() != row["source"].casefold():
            raise ValueError(f"decision row {index}: non-mutating action must keep source as target")
        prepared.append(row)
    warnings = sorted(legacy_folder_warnings.values(), key=lambda item: item["path"].casefold())
    return root, prepared, undecided_count, warnings


def write_preview(path: Path, rows: list[dict], legacy_folder_warnings: list[dict[str, str]]) -> None:
    labels = dict(zip(FIELDS, HEADERS))
    parts = ["# 文件整理预览", ""]
    if legacy_folder_warnings:
        parts.extend(["## 历史文件夹命名提示", ""])
        for warning in legacy_folder_warnings:
            parts.append(
                f"- `{warning['path']}`：{warning['issue']}；{warning['handling']}。"
            )
        parts.append("")
    for index, row in enumerate(rows, start=1):
        parts.append(f"## {index}. {row['source']}")
        parts.append("")
        for field in FIELDS:
            value = row[field].replace("\n", " ").strip()
            if field == "action" and row.get("resolution") == "undecided":
                value = f"{value}（未决）"
            parts.append(f"- {labels[field]}：{value}")
        parts.append("")
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-out", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        evidence_path = args.evidence.resolve(strict=True)
        decisions_path = args.decisions.resolve(strict=True)
        evidence = load_version_one(evidence_path, "evidence")
        decisions = load_version_one(decisions_path, "decisions")
        root, rows, undecided_count, legacy_folder_warnings = validate(evidence, decisions)
        completion_eligible = undecided_count == 0
        if args.require_complete and not completion_eligible:
            raise ValueError(f"plan is not completion-eligible: {undecided_count} undecided rows")
        output = args.output.resolve(strict=False)
        if not output.parent.is_dir():
            raise ValueError("--output parent directory must already exist")
        evidence_by_source = {
            item["relative_path"].casefold(): item for item in evidence["files"]
        }
        plan = {
            "version": 2,
            "root": str(root),
            "completion_eligible": completion_eligible,
            "undecided_count": undecided_count,
            "legacy_folder_warnings": legacy_folder_warnings,
            "rows": [
                {
                    "source": row["source"],
                    "target": row["target"],
                    "action": row["action"],
                    "expected_size": evidence_by_source[row["source"].casefold()]["size"],
                    "expected_mtime_ns": evidence_by_source[row["source"].casefold()]["mtime_ns"],
                }
                for row in rows
            ],
        }
        output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.preview_out:
            preview = args.preview_out.resolve(strict=False)
            if not preview.parent.is_dir():
                raise ValueError("--preview-out parent directory must already exist")
            write_preview(preview, rows, legacy_folder_warnings)
        counts = {action: sum(row["action"] == action for row in rows) for action in sorted(ACTIONS)}
        json.dump(
            {
                "version": 1,
                "status": "PLAN_COMPILED",
                "output": str(output),
                "row_count": len(rows),
                "completion_eligible": completion_eligible,
                "undecided_count": undecided_count,
                "legacy_folder_warning_count": len(legacy_folder_warnings),
                "action_counts": counts,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump({"version": 1, "status": "PLAN_COMPILE_FAILED", "error": str(error)}, sys.stderr, ensure_ascii=False, indent=2)
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
