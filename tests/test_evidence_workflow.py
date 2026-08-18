import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUILD_EVIDENCE = SKILL_ROOT / "scripts" / "build-evidence-index.py"
COMPILE_PLAN = SKILL_ROOT / "scripts" / "compile-plan.py"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def load_evidence_module():
    script_directory = str(BUILD_EVIDENCE.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location("build_evidence_index", BUILD_EVIDENCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def inventory_row(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "extension": path.suffix.lower(),
        "hidden": False,
        "system": False,
        "reparse_point": False,
    }


def write_inventory(path: Path, root: Path, files: list[Path]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "root": str(root.resolve()),
                "file_count": len(files),
                "files": [inventory_row(item, root) for item in files],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def decision(
    source: str,
    *,
    action: str = "保持不变",
    target: str | None = None,
    resolution: str = "compliant",
) -> dict:
    return {
        "source": source,
        "category": "测试分类",
        "new_name": Path(target or source).name,
        "target": target or source,
        "classification_basis": "正文证据",
        "classification_confidence": "高",
        "date_basis": "不适用",
        "date_confidence": "不适用",
        "action": action,
        "risk": "无",
        "resolution": resolution,
    }


class EvidenceWorkflowTests(unittest.TestCase):
    def test_compact_text_replaces_unpaired_surrogates_before_utf8_json_output(self):
        module = load_evidence_module()

        sanitized = module.compact_text("before\ud902after")

        self.assertFalse(any(0xD800 <= ord(character) <= 0xDFFF for character in sanitized))
        self.assertIn("\ufffd", sanitized)
        json.dumps({"text": sanitized}, ensure_ascii=False).encode("utf-8")

    def test_builder_reuses_inventory_and_requires_visual_evidence_for_every_human_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            note = root / "说明.txt"
            note.write_text("这是人工管理的说明正文。", encoding="utf-8")
            image = root / "看起来已经命名清楚.png"
            image.write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
                )
            )
            archive = root / "材料包.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("readme.txt", "archive evidence")
            program = root / "app.py"
            program.write_text("print('machine file')\n", encoding="utf-8")
            unlisted = root / "扫描后新增.txt"
            unlisted.write_text("must not be discovered", encoding="utf-8")

            inventory = base / "inventory.json"
            evidence = base / "evidence.json"
            write_inventory(inventory, root, [note, image, archive, program])

            result = run_script(
                BUILD_EVIDENCE,
                str(inventory),
                "--output",
                str(evidence),
                "--exclude-prefix",
                "app.py",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            rows = {row["relative_path"]: row for row in payload["files"]}
            self.assertNotIn("扫描后新增.txt", rows)
            self.assertEqual(rows["说明.txt"]["evidence_status"], "content-extracted")
            self.assertEqual(rows["看起来已经命名清楚.png"]["evidence_status"], "visual-pending")
            self.assertTrue(rows["看起来已经命名清楚.png"]["needs_visual_audit"])
            self.assertEqual(rows["材料包.zip"]["evidence_status"], "archive-indexed")
            self.assertEqual(rows["app.py"]["file_class"], "explicit-exclusion")
            self.assertEqual(payload["coverage"]["inventory_file_count"], 4)
            self.assertEqual(payload["coverage"]["pending_evidence_count"], 1)

    def test_builder_merges_ai_visual_annotations_without_rereading_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            image = root / "photo.png"
            image.write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
                )
            )
            inventory = base / "inventory.json"
            evidence = base / "evidence.json"
            annotations = base / "annotations.json"
            write_inventory(inventory, root, [image])
            annotations.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rows": [
                            {
                                "relative_path": "photo.png",
                                "evidence_status": "visual-audited",
                                "visual_summary": "白底产品照片，主体为示例设备。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                BUILD_EVIDENCE,
                str(inventory),
                "--output",
                str(evidence),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            initial = json.loads(evidence.read_text(encoding="utf-8"))
            before = image.stat()
            image.write_bytes(image.read_bytes())
            image.touch()
            # Restore the evidence key: merge mode may stat the file but must not re-extract it.
            import os
            os.utime(image, ns=(before.st_atime_ns, before.st_mtime_ns))

            result = run_script(
                BUILD_EVIDENCE,
                str(inventory),
                "--output",
                str(evidence),
                "--existing-evidence",
                str(evidence),
                "--annotations",
                str(annotations),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(payload["coverage"]["pending_evidence_count"], 0)
            self.assertEqual(payload["files"][0]["visual_summary"], "白底产品照片，主体为示例设备。")
            self.assertEqual(payload["files"][0]["parser"], initial["files"][0]["parser"])

    def test_plan_compiler_blocks_incomplete_evidence_and_missing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            image = root / "photo.png"
            image.write_bytes(b"image")
            evidence = base / "evidence.json"
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [image])
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(image, root),
                                "evidence_key": {
                                    "relative_path": "photo.png",
                                    "size": image.stat().st_size,
                                    "mtime_ns": image.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "visual-pending",
                                "evidence_kind": "image",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps({"version": 1, "rows": [decision("photo.png")]}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(base / "plan.json"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("visual-pending", result.stderr)

            decisions.write_text(json.dumps({"version": 1, "rows": []}), encoding="utf-8")
            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(base / "plan.json"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing decision", result.stderr)

    def test_plan_compiler_binds_completion_status_to_file_type_and_supports_undecided_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            image = root / "photo.png"
            image.write_bytes(b"image")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [image])
            evidence = base / "evidence.json"
            row = inventory_row(image, root)
            row.update(
                evidence_key={
                    "relative_path": "photo.png",
                    "size": image.stat().st_size,
                    "mtime_ns": image.stat().st_mtime_ns,
                    "reparse_point": False,
                },
                file_class="applicable-human",
                evidence_kind="image",
                evidence_status="metadata-reviewed",
            )
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [row],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            plan = base / "plan.json"
            preview = base / "preview.md"
            decisions.write_text(
                json.dumps(
                    {"version": 1, "rows": [decision("photo.png", action="跳过", resolution="undecided")]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                COMPILE_PLAN,
                str(evidence),
                str(decisions),
                "--output",
                str(plan),
                "--preview-out",
                str(preview),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertFalse(payload["completion_eligible"])
            self.assertEqual(payload["undecided_count"], 1)
            self.assertIn("未决", preview.read_text(encoding="utf-8"))

            result = run_script(
                COMPILE_PLAN,
                str(evidence),
                str(decisions),
                "--output",
                str(plan),
                "--require-complete",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not completion-eligible", result.stderr)

            decisions.write_text(
                json.dumps({"version": 1, "rows": [decision("photo.png")]}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(plan))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("image requires", result.stderr)

            row["evidence_status"] = "visual-audited"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [row],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(plan))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("visual_summary", result.stderr)

    def test_plan_compiler_reconciles_evidence_against_original_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [first, second])
            evidence = base / "evidence.json"
            row = inventory_row(first, root)
            row.update(
                evidence_key={
                    "relative_path": "first.txt",
                    "size": first.stat().st_size,
                    "mtime_ns": first.stat().st_mtime_ns,
                    "reparse_point": False,
                },
                file_class="applicable-human",
                evidence_kind="document",
                evidence_status="content-extracted",
            )
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [row],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps({"version": 1, "rows": [decision("first.txt")]}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(base / "plan.json"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("inventory/evidence path mismatch", result.stderr)

    def test_unknown_content_extracted_status_requires_actual_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "unknown.bin"
            source.write_bytes(b"opaque")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            row = inventory_row(source, root)
            row.update(
                evidence_key={
                    "relative_path": "unknown.bin",
                    "size": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns,
                    "reparse_point": False,
                },
                file_class="applicable-human",
                evidence_kind="unknown",
                evidence_status="content-extracted",
                content_excerpt="",
            )
            evidence = base / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [row],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps({"version": 1, "rows": [decision("unknown.bin")]}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(base / "plan.json"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("content_excerpt", result.stderr)

    def test_plan_compiler_emits_apply_plan_and_ten_field_preview_after_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "old.txt"
            source.write_text("content", encoding="utf-8")
            evidence = base / "evidence.json"
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(source, root),
                                "evidence_key": {
                                    "relative_path": "old.txt",
                                    "size": source.stat().st_size,
                                    "mtime_ns": source.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "content-extracted",
                                "evidence_kind": "document",
                                "content_excerpt": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rows": [
                            decision(
                                "old.txt",
                                action="移动并改名",
                                target="归档/20260817_说明.txt",
                                resolution="changed",
                            )
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            plan = base / "plan.json"
            preview = base / "preview.md"

            result = run_script(
                COMPILE_PLAN,
                str(evidence),
                str(decisions),
                "--output",
                str(plan),
                "--preview-out",
                str(preview),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["rows"][0]["source"], "old.txt")
            self.assertEqual(payload["rows"][0]["target"], "归档/20260817_说明.txt")
            self.assertEqual(payload["rows"][0]["expected_size"], source.stat().st_size)
            self.assertEqual(payload["rows"][0]["expected_mtime_ns"], source.stat().st_mtime_ns)
            text = preview.read_text(encoding="utf-8")
            for header in (
                "原路径",
                "建议分类",
                "建议文件名",
                "最终路径",
                "分类依据",
                "分类可信度",
                "日期依据",
                "日期可信度",
                "建议动作",
                "风险或说明",
            ):
                self.assertIn(header, text)

    def test_plan_compiler_rejects_control_characters_in_target_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "old.txt"
            source.write_text("content", encoding="utf-8")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence = base / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(source, root),
                                "evidence_key": {
                                    "relative_path": "old.txt",
                                    "size": source.stat().st_size,
                                    "mtime_ns": source.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "content-extracted",
                                "evidence_kind": "document",
                                "content_excerpt": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rows": [
                            decision(
                                "old.txt",
                                action="移动并改名",
                                target="归档/20260818_坏\u0003标题.txt",
                                resolution="changed",
                            )
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                COMPILE_PLAN,
                str(evidence),
                str(decisions),
                "--output",
                str(base / "plan.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("control character", result.stderr)

    def test_ooxml_member_over_resource_limit_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "oversized.docx"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as handle:
                handle.writestr("word/document.xml", b"<w>" + b"a" * (8 * 1024 * 1024 + 1) + b"</w>")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence = base / "evidence.json"

            result = run_script(BUILD_EVIDENCE, str(inventory), "--output", str(evidence))

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(evidence.read_text(encoding="utf-8"))["files"][0]
            self.assertEqual(row["evidence_status"], "parse-error")
            self.assertIn("resource limit", row["parse_error"])

    def test_plan_compiler_rejects_new_dated_folders_without_date_title_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "old.txt"
            source.write_text("content", encoding="utf-8")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence = base / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(source, root),
                                "evidence_key": {
                                    "relative_path": "old.txt",
                                    "size": source.stat().st_size,
                                    "mtime_ns": source.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "content-extracted",
                                "evidence_kind": "document",
                                "content_excerpt": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"

            for invalid_folder in ("202507-管理沟通", "[202507]管理沟通", "【202507】管理沟通"):
                with self.subTest(invalid_folder=invalid_folder):
                    decisions.write_text(
                        json.dumps(
                            {
                                "version": 1,
                                "rows": [
                                    decision(
                                        "old.txt",
                                        action="移动并改名",
                                        target=f"{invalid_folder}/202507_课程资料.txt",
                                        resolution="changed",
                                    )
                                ],
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    result = run_script(
                        COMPILE_PLAN,
                        str(evidence),
                        str(decisions),
                        "--output",
                        str(base / "plan.json"),
                    )

                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("日期_标题", result.stderr)

    def test_plan_compiler_reports_reused_legacy_dated_folders_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            legacy_folder = root / "[2024]数据与统计"
            legacy_folder.mkdir(parents=True)
            source = root / "old.txt"
            source.write_text("content", encoding="utf-8")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence = base / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(source, root),
                                "evidence_key": {
                                    "relative_path": "old.txt",
                                    "size": source.stat().st_size,
                                    "mtime_ns": source.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "content-extracted",
                                "evidence_kind": "document",
                                "content_excerpt": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rows": [
                            decision(
                                "old.txt",
                                action="移动并改名",
                                target="[2024]数据与统计/2024_数据与统计习题课.txt",
                                resolution="changed",
                            )
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            plan = base / "plan.json"

            result = run_script(COMPILE_PLAN, str(evidence), str(decisions), "--output", str(plan))

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            self.assertIn("legacy_folder_warnings", payload)
            self.assertEqual(
                [warning["path"] for warning in payload["legacy_folder_warnings"]],
                ["[2024]数据与统计"],
            )

    def test_plan_compiler_accepts_new_dated_folders_with_date_title_separator(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            source = root / "old.txt"
            source.write_text("content", encoding="utf-8")
            inventory = base / "inventory.json"
            write_inventory(inventory, root, [source])
            evidence = base / "evidence.json"
            evidence.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root.resolve()),
                        "inventory_path": str(inventory.resolve()),
                        "files": [
                            {
                                **inventory_row(source, root),
                                "evidence_key": {
                                    "relative_path": "old.txt",
                                    "size": source.stat().st_size,
                                    "mtime_ns": source.stat().st_mtime_ns,
                                    "reparse_point": False,
                                },
                                "file_class": "applicable-human",
                                "evidence_status": "content-extracted",
                                "evidence_kind": "document",
                                "content_excerpt": "content",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            decisions = base / "decisions.json"

            for valid_folder in ("202507_管理沟通", "2025Q3_季度资料", "2021-2022_项目资料", "学籍与校务"):
                with self.subTest(valid_folder=valid_folder):
                    decisions.write_text(
                        json.dumps(
                            {
                                "version": 1,
                                "rows": [
                                    decision(
                                        "old.txt",
                                        action="移动并改名",
                                        target=f"{valid_folder}/202507_课程资料.txt",
                                        resolution="changed",
                                    )
                                ],
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    result = run_script(
                        COMPILE_PLAN,
                        str(evidence),
                        str(decisions),
                        "--output",
                        str(base / "plan.json"),
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
