#!/usr/bin/env python3
"""Build one reusable content-evidence index from an existing file inventory."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

sys.dont_write_bytecode = True
from _common import IMAGE_EXTENSIONS, resolve_relative_within_root


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".ini", ".rtf", ".log",
}
OOXML_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
LEGACY_DOCUMENT_EXTENSIONS = {".doc", ".xls", ".ppt", ".wps", ".et", ".dps"}
PDF_EXTENSIONS = {".pdf"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}
ARCHIVE_EXTENSIONS = {".zip"}
OTHER_ARCHIVE_EXTENSIONS = {".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
PENDING_STATUSES = {
    "visual-pending", "media-pending", "legacy-content-pending",
    "archive-content-pending", "unsupported", "parse-error",
}
ANNOTATION_FIELDS = {
    "file_class", "evidence_status", "content_excerpt", "visual_summary",
    "media_summary", "archive_summary", "internal_title", "date_candidates",
    "exclusion_reason", "notes",
}
MAX_EXCERPT = 8000
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ZIP_MEMBERS = 10_000
MAX_ZIP_COMPRESSION_RATIO = 200


def outside_root(root: Path, path: Path, label: str) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return
    raise ValueError(f"{label} must be outside the selected root")


def normalized_prefix(value: str) -> str:
    text = value.replace("\\", "/").strip("/")
    if not text or text in {".", ".."} or text.startswith("../") or "/../" in text:
        raise ValueError(f"invalid exclusion prefix: {value!r}")
    return text


def is_excluded(relative: str, prefixes: list[str]) -> bool:
    folded = relative.casefold()
    return any(folded == prefix.casefold() or folded.startswith(prefix.casefold() + "/") for prefix in prefixes)


def compact_text(value: str, limit: int = MAX_EXCERPT) -> str:
    value = re.sub(r"[\ud800-\udfff]", "\ufffd", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def read_text_file(path: Path) -> tuple[str, str]:
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise ValueError(f"text resource limit exceeded: {path.stat().st_size} bytes")
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return compact_text(raw.decode(encoding)), encoding
        except UnicodeDecodeError:
            continue
    return compact_text(raw.decode("utf-8", errors="replace")), "utf-8-replace"


def xml_text(data: bytes) -> str:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""
    return " ".join(text for text in root.itertext() if text and text.strip())


def validate_zip_limits(handle: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = handle.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"ZIP resource limit exceeded: {len(infos)} members")
    total = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        by_name[info.filename] = info
        total += info.file_size
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"ZIP resource limit exceeded: member {info.filename!r} is {info.file_size} bytes")
        if info.file_size and info.compress_size == 0:
            raise ValueError(f"ZIP resource limit exceeded: member {info.filename!r} has zero compressed size")
        if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
            raise ValueError(f"ZIP resource limit exceeded: member {info.filename!r} compression ratio is too high")
    if total > MAX_ZIP_TOTAL_BYTES:
        raise ValueError(f"ZIP resource limit exceeded: {total} uncompressed bytes")
    return by_name


def extract_ooxml(path: Path) -> tuple[str, list[str], str]:
    patterns = {
        ".docx": ("word/document.xml",),
        ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
        ".pptx": ("ppt/slides/slide",),
    }[path.suffix.lower()]
    fragments: list[str] = []
    dates: list[str] = []
    with zipfile.ZipFile(path) as handle:
        members = validate_zip_limits(handle)
        for name in sorted(members):
            if any(name == pattern or name.startswith(pattern) for pattern in patterns) and name.endswith(".xml"):
                fragments.append(xml_text(handle.read(name)))
                if sum(len(item) for item in fragments) >= MAX_EXCERPT:
                    break
        if "docProps/core.xml" in members:
            core = xml_text(handle.read("docProps/core.xml"))
            dates.extend(re.findall(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z?)?\b", core))
    return compact_text(" ".join(fragments)), dates, "stdlib-ooxml"


def extract_pdf(path: Path) -> tuple[str, dict, str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return "", {}, "pypdf-unavailable"
    reader = PdfReader(str(path))
    protection = {"encrypted": bool(reader.is_encrypted)}
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return "", protection, "pypdf-encrypted-unreadable"
    fragments: list[str] = []
    for page in reader.pages:
        fragments.append(page.extract_text() or "")
        if sum(len(item) for item in fragments) >= MAX_EXCERPT:
            break
    return compact_text(" ".join(fragments)), protection, "pypdf"


def image_metadata(path: Path) -> tuple[dict, str]:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return {}, "pillow-unavailable"
    try:
        with Image.open(path) as image:
            metadata = {"width": image.width, "height": image.height, "format": image.format}
            exif = image.getexif()
            for key in (36867, 36868, 306):
                value = exif.get(key)
                if value:
                    metadata.setdefault("exif_dates", []).append(str(value))
            return metadata, "pillow"
    except Exception as error:
        return {"metadata_error": str(error)}, "pillow-error"


def archive_index(path: Path) -> tuple[list[dict], str]:
    with zipfile.ZipFile(path) as handle:
        validate_zip_limits(handle)
        rows = [
            {"name": info.filename, "size": info.file_size, "directory": info.is_dir()}
            for info in handle.infolist()[:2000]
        ]
    return rows, "stdlib-zip"


def base_row(inventory_row: dict) -> dict:
    return {
        "relative_path": inventory_row["relative_path"],
        "size": inventory_row["size"],
        "mtime_ns": inventory_row["mtime_ns"],
        "ctime_ns": inventory_row.get("ctime_ns"),
        "extension": inventory_row.get("extension", Path(inventory_row["relative_path"]).suffix.lower()),
        "evidence_key": {
            "relative_path": inventory_row["relative_path"],
            "size": inventory_row["size"],
            "mtime_ns": inventory_row["mtime_ns"],
            "reparse_point": bool(inventory_row.get("reparse_point")),
        },
        "file_class": "applicable-human",
        "evidence_kind": "unknown",
        "evidence_status": "unsupported",
        "parser": None,
        "content_excerpt": "",
        "internal_title": "",
        "date_candidates": [],
        "needs_visual_audit": False,
        "needs_media_audit": False,
    }


def inspect_file(path: Path, row: dict) -> dict:
    extension = row["extension"]
    try:
        if extension in TEXT_EXTENSIONS:
            excerpt, parser = read_text_file(path)
            row.update(evidence_kind="document", evidence_status="content-extracted", content_excerpt=excerpt, parser=parser)
        elif extension in OOXML_EXTENSIONS:
            excerpt, dates, parser = extract_ooxml(path)
            row.update(
                evidence_kind="document",
                evidence_status="content-extracted" if excerpt else "parse-error",
                content_excerpt=excerpt,
                date_candidates=dates,
                parser=parser,
            )
        elif extension in PDF_EXTENSIONS:
            excerpt, protection, parser = extract_pdf(path)
            row.update(
                evidence_kind="document",
                evidence_status="content-extracted" if excerpt else "parse-error",
                content_excerpt=excerpt,
                protection=protection,
                parser=parser,
            )
        elif extension in IMAGE_EXTENSIONS:
            metadata, parser = image_metadata(path)
            row.update(
                evidence_kind="image",
                evidence_status="visual-pending",
                image_metadata=metadata,
                parser=parser,
                needs_visual_audit=True,
            )
            row["date_candidates"] = metadata.get("exif_dates", [])
        elif extension in VIDEO_EXTENSIONS or extension in AUDIO_EXTENSIONS:
            row.update(evidence_kind="media", evidence_status="media-pending", parser="metadata-only", needs_media_audit=True)
        elif extension in ARCHIVE_EXTENSIONS:
            entries, parser = archive_index(path)
            row.update(evidence_kind="archive", evidence_status="archive-indexed", archive_entries=entries, parser=parser)
        elif extension in LEGACY_DOCUMENT_EXTENSIONS:
            row.update(evidence_kind="document", evidence_status="legacy-content-pending", parser="legacy-conversion-required")
        elif extension in OTHER_ARCHIVE_EXTENSIONS:
            row.update(evidence_kind="archive", evidence_status="archive-content-pending", parser="archive-listing-required")
    except Exception as error:
        row.update(evidence_status="parse-error", parser=row.get("parser") or "error", parse_error=str(error))
    return row


def load_annotations(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("rows"), list):
        raise ValueError("annotations must be version 1 with a rows list")
    result: dict[str, dict] = {}
    for index, annotation in enumerate(payload["rows"], start=1):
        relative = annotation.get("relative_path")
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"annotation {index}: relative_path is required")
        if relative in result:
            raise ValueError(f"annotation {index}: duplicate relative_path {relative}")
        unknown = set(annotation) - ANNOTATION_FIELDS - {"relative_path"}
        if unknown:
            raise ValueError(f"annotation {index}: unsupported fields {sorted(unknown)}")
        result[relative] = annotation
    return result


def merge_annotations(rows: list[dict], annotations: dict[str, dict]) -> None:
    by_path = {row["relative_path"]: row for row in rows}
    unknown = sorted(set(annotations) - set(by_path), key=str.casefold)
    if unknown:
        raise ValueError(f"annotations reference paths absent from inventory: {unknown[:5]}")
    for relative, annotation in annotations.items():
        row = by_path[relative]
        for key in ANNOTATION_FIELDS:
            if key in annotation:
                row[key] = annotation[key]
        if row.get("file_class") == "explicit-exclusion":
            if not row.get("exclusion_reason"):
                raise ValueError(f"explicit exclusion requires exclusion_reason: {relative}")
            row["evidence_status"] = "structural-exclusion"
        row["needs_visual_audit"] = row.get("evidence_status") == "visual-pending"
        row["needs_media_audit"] = row.get("evidence_status") == "media-pending"


def create_contact_sheets(rows: list[dict], root: Path, output: Path, batch_size: int) -> list[str]:
    try:
        from PIL import Image, ImageDraw, ImageOps  # type: ignore
    except ImportError:
        return []
    candidates = [row for row in rows if row.get("evidence_status") == "visual-pending"]
    if not candidates:
        return []
    output.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    cell_width, cell_height, thumb_height = 300, 240, 190
    columns = 4
    for batch_number in range(math.ceil(len(candidates) / batch_size)):
        batch = candidates[batch_number * batch_size : (batch_number + 1) * batch_size]
        grid_rows = math.ceil(len(batch) / columns)
        sheet = Image.new("RGB", (cell_width * columns, cell_height * grid_rows), "white")
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(batch):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            try:
                with Image.open(resolve_relative_within_root(root, row["relative_path"], label="inventory path")) as source:
                    thumb = ImageOps.contain(source.convert("RGB"), (cell_width - 16, thumb_height - 8))
                    sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 4))
            except Exception:
                draw.rectangle((x + 8, y + 8, x + cell_width - 8, y + thumb_height - 8), outline="red")
                draw.text((x + 16, y + 80), "PREVIEW ERROR", fill="red")
            label = f"{batch_number * batch_size + index + 1}: {row['relative_path']}"
            draw.text((x + 8, y + thumb_height + 2), label[:46], fill="black")
        target = output / f"images-{batch_number + 1:03d}.jpg"
        sheet.save(target, quality=88)
        written.append(str(target.resolve()))
    return written


def coverage(rows: list[dict]) -> dict:
    human = [row for row in rows if row.get("file_class") == "applicable-human"]
    excluded = [row for row in rows if row.get("file_class") == "explicit-exclusion"]
    pending = [row for row in human if row.get("evidence_status") in PENDING_STATUSES]
    return {
        "inventory_file_count": len(rows),
        "applicable_human_count": len(human),
        "explicit_exclusion_count": len(excluded),
        "completed_evidence_count": len(human) - len(pending),
        "pending_evidence_count": len(pending),
        "reconciled": len(rows) == len(human) + len(excluded),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-evidence", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--exclude-prefix", action="append", default=[])
    parser.add_argument("--contact-sheet-dir", type=Path)
    parser.add_argument("--contact-sheet-size", type=int, default=32)
    args = parser.parse_args()
    try:
        inventory_path = args.inventory.resolve(strict=True)
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("files"), list):
            raise ValueError("inventory must be version 1 with a files list")
        root = Path(payload["root"]).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("inventory root is not an existing directory")
        output = args.output.resolve(strict=False)
        outside_root(root, output, "--output")
        if not output.parent.is_dir():
            raise ValueError("--output parent directory must already exist")
        prefixes = [normalized_prefix(value) for value in args.exclude_prefix]
        existing_payload = None
        existing_rows: dict[str, dict] = {}
        if args.existing_evidence:
            existing_path = args.existing_evidence.resolve(strict=True)
            existing_payload = json.loads(existing_path.read_text(encoding="utf-8"))
            if existing_payload.get("version") != 1 or not isinstance(existing_payload.get("files"), list):
                raise ValueError("existing evidence must be version 1 with a files list")
            if Path(existing_payload.get("root", "")).resolve(strict=False) != root:
                raise ValueError("existing evidence root does not match inventory root")
            if Path(existing_payload.get("inventory_path", "")).resolve(strict=False) != inventory_path:
                raise ValueError("existing evidence does not reference this inventory")
            for old_row in existing_payload["files"]:
                relative = old_row.get("relative_path")
                if not isinstance(relative, str) or not relative:
                    raise ValueError("existing evidence row lacks relative_path")
                key = relative.casefold()
                if key in existing_rows:
                    raise ValueError(f"existing evidence has duplicate path: {relative}")
                existing_rows[key] = old_row
            if prefixes or args.contact_sheet_dir:
                raise ValueError("merge mode reuses existing exclusions and contact sheets; omit --exclude-prefix and --contact-sheet-dir")
        rows: list[dict] = []
        seen: set[str] = set()
        for index, inventory_row in enumerate(payload["files"], start=1):
            relative = inventory_row.get("relative_path")
            if not isinstance(relative, str) or not relative:
                raise ValueError(f"inventory row {index}: relative_path is required")
            key = relative.casefold()
            if key in seen:
                raise ValueError(f"inventory row {index}: duplicate path {relative}")
            seen.add(key)
            source = resolve_relative_within_root(root, relative, label="inventory path")
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"inventory row {index}: source is not a regular file: {relative}")
            stat = source.stat()
            if stat.st_size != inventory_row["size"] or stat.st_mtime_ns != inventory_row["mtime_ns"]:
                raise ValueError(f"inventory evidence key changed: {relative}")
            if existing_payload is not None:
                old_row = existing_rows.get(key)
                if old_row is None:
                    raise ValueError(f"existing evidence is missing inventory path: {relative}")
                evidence_key = old_row.get("evidence_key") or {}
                if (
                    evidence_key.get("relative_path", "").casefold() != relative.casefold()
                    or evidence_key.get("size") != inventory_row["size"]
                    or evidence_key.get("mtime_ns") != inventory_row["mtime_ns"]
                    or bool(evidence_key.get("reparse_point")) != bool(inventory_row.get("reparse_point"))
                ):
                    raise ValueError(f"existing evidence key does not match inventory: {relative}")
                row = dict(old_row)
            else:
                row = base_row(inventory_row)
            if existing_payload is None and is_excluded(relative, prefixes):
                row.update(
                    file_class="explicit-exclusion",
                    evidence_kind="exclusion",
                    evidence_status="structural-exclusion",
                    exclusion_reason="explicit verified boundary supplied by caller",
                    parser="not-opened",
                )
            elif existing_payload is None:
                inspect_file(source, row)
            rows.append(row)
        if existing_payload is not None and len(existing_rows) != len(rows):
            raise ValueError("existing evidence contains paths absent from inventory")
        annotations = load_annotations(args.annotations.resolve(strict=True) if args.annotations else None)
        merge_annotations(rows, annotations)
        contact_sheets: list[str] = list(existing_payload.get("contact_sheets", [])) if existing_payload else []
        if args.contact_sheet_dir:
            sheet_dir = args.contact_sheet_dir.resolve(strict=False)
            outside_root(root, sheet_dir, "--contact-sheet-dir")
            if args.contact_sheet_size < 1:
                raise ValueError("--contact-sheet-size must be positive")
            contact_sheets = create_contact_sheets(rows, root, sheet_dir, args.contact_sheet_size)
        result = {
            "version": 1,
            "root": str(root),
            "inventory_path": str(inventory_path),
            "files": rows,
            "coverage": coverage(rows),
            "contact_sheets": contact_sheets,
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        json.dump(
            {"version": 1, "output": str(output), "coverage": result["coverage"], "contact_sheet_count": len(contact_sheets)},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        json.dump({"version": 1, "status": "EVIDENCE_BUILD_FAILED", "error": str(error)}, sys.stderr, ensure_ascii=False, indent=2)
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
