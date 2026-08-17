import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL = SKILL_ROOT / "SKILL.md"
SCAN = SKILL_ROOT / "scripts" / "scan-files.py"
QUERY = SKILL_ROOT / "scripts" / "query-inventory.py"
APPLY = SKILL_ROOT / "scripts" / "apply-plan.py"
HASH_DUPLICATES = SKILL_ROOT / "scripts" / "hash-duplicate-candidates.py"
APPLY_DUPLICATES = SKILL_ROOT / "scripts" / "apply-duplicate-deletions.py"
BUILD_EVIDENCE = SKILL_ROOT / "scripts" / "build-evidence-index.py"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def bound_plan_row(source: Path, root: Path, target: str, action: str) -> dict:
    stat = source.stat()
    return {
        "source": source.relative_to(root).as_posix(),
        "target": target,
        "action": action,
        "expected_size": stat.st_size,
        "expected_mtime_ns": stat.st_mtime_ns,
    }


def load_script_module(path: Path, module_name: str):
    script_directory = str(path.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FileToolTests(unittest.TestCase):
    def test_scan_is_metadata_only_and_groups_same_size_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "b.txt").write_text("size", encoding="utf-8")
            (root / "c.txt").write_text("different", encoding="utf-8")

            result = run_script(
                SCAN,
                str(root),
                "--full-output",
                "--include-duplicate-candidates",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["file_count"], 3)
            self.assertTrue(all("sha256" not in row for row in payload["files"]))
            self.assertEqual(
                sorted(payload["same_size_candidate_groups"][0]["paths"]),
                ["a.txt", "b.txt"],
            )

    def test_scan_summarizes_full_scope_and_flags_filename_review_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "业务").mkdir()
            (root / "程序").mkdir()
            (root / "RootNote.md").write_text("note", encoding="utf-8")
            (root / "业务" / "Final.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (root / "业务" / "2024Q1_季度总结.md").write_text("summary", encoding="utf-8")
            (root / "业务" / "2022-2023_年度总结.pdf").write_bytes(b"pdf")
            (root / "业务" / "EventPhoto.jpg").write_bytes(b"photo")
            (root / "业务" / "20240101_活动合影.jpg").write_bytes(b"dated-photo")
            (root / "程序" / "static").mkdir()
            (root / "程序" / "package.json").write_text("{}", encoding="utf-8")
            (root / "程序" / "static" / "icon.png").write_bytes(b"icon")
            (root / "LICENSE").write_text("license", encoding="utf-8")

            result = run_script(SCAN, str(root), "--full-output")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["extension_counts"],
                {
                    ".csv": 1,
                    ".jpg": 2,
                    ".json": 1,
                    ".md": 2,
                    ".pdf": 1,
                    ".png": 1,
                    "[no-extension]": 1,
                },
            )
            summaries = {row["name"]: row for row in payload["top_level_summary"]}
            self.assertEqual(summaries["."]["file_count"], 2)
            self.assertEqual(summaries["业务"]["file_count"], 5)
            self.assertEqual(summaries["程序"]["file_count"], 2)

            candidates = {
                row["relative_path"]: row["reasons"]
                for row in payload["filename_review_candidates"]
            }
            self.assertEqual(candidates["RootNote.md"], ["missing_date_prefix"])
            self.assertEqual(
                candidates["业务/Final.csv"],
                ["missing_date_prefix", "opaque_title"],
            )
            self.assertNotIn("业务/2024Q1_季度总结.md", candidates)
            self.assertNotIn("业务/2022-2023_年度总结.pdf", candidates)
            self.assertNotIn("程序/package.json", candidates)

            image_candidates = {
                row["relative_path"]: row["reasons"]
                for row in payload["image_audit_candidates"]
            }
            self.assertEqual(
                image_candidates["业务/EventPhoto.jpg"],
                ["asset_or_archive_decision", "missing_date_prefix"],
            )
            self.assertEqual(
                image_candidates["业务/20240101_活动合影.jpg"],
                ["asset_or_archive_decision"],
            )
            self.assertEqual(
                image_candidates["程序/static/icon.png"],
                ["asset_or_archive_decision", "missing_date_prefix"],
            )

    def test_scan_writes_full_inventory_but_prints_only_compact_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "b.txt").write_text("size", encoding="utf-8")
            (root / "photo.jpg").write_bytes(b"photo")
            inventory = base / "inventory.json"

            result = run_script(SCAN, str(root), "--inventory-out", str(inventory))

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            full = json.loads(inventory.read_text(encoding="utf-8"))
            self.assertNotIn("files", summary)
            self.assertNotIn("filename_review_candidates", summary)
            self.assertNotIn("image_audit_candidates", summary)
            self.assertEqual(summary["file_count"], 3)
            self.assertEqual(summary["filename_review_candidate_count"], 2)
            self.assertEqual(summary["image_audit_candidate_count"], 1)
            self.assertEqual(Path(summary["inventory_path"]), inventory.resolve())
            self.assertEqual(len(full["files"]), 3)
            self.assertEqual(full["same_size_candidate_groups"], [])
            self.assertFalse(full["duplicate_candidates_computed"])

    def test_scan_separates_generated_metadata_and_records_empty_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty-folder").mkdir()
            (root / ".DS_Store").write_bytes(b"finder")
            (root / "._report.pdf").write_bytes(b"appledouble")
            (root / "~$report.docx").write_bytes(b"lock")
            (root / "report.pdf").write_bytes(b"document")

            result = run_script(SCAN, str(root), "--full-output")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["total_file_count"], 4)
            self.assertEqual(payload["file_count"], 1)
            self.assertEqual(
                [row["relative_path"] for row in payload["generated_metadata_candidates"]],
                ["._report.pdf", ".DS_Store", "~$report.docx"],
            )
            self.assertTrue(
                all(
                    {"kind", "size", "mtime_ns"}.issubset(row)
                    for row in payload["generated_metadata_candidates"]
                )
            )
            self.assertEqual(payload["empty_directory_candidates"], ["empty-folder"])
            self.assertEqual([row["relative_path"] for row in payload["files"]], ["report.pdf"])

    def test_query_inventory_returns_only_requested_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text(name, encoding="utf-8")
            inventory = base / "inventory.json"
            scanned = run_script(SCAN, str(root), "--inventory-out", str(inventory))
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            result = run_script(
                QUERY,
                str(inventory),
                "--section",
                "files",
                "--offset",
                "1",
                "--limit",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["total_matching"], 3)
            self.assertEqual(payload["offset"], 1)
            self.assertEqual(payload["limit"], 1)
            self.assertEqual(len(payload["rows"]), 1)
            self.assertEqual(payload["rows"][0]["relative_path"], "b.txt")

    def test_query_inventory_returns_generated_metadata_from_saved_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            (root / "._report.pdf").write_bytes(b"appledouble")
            (root / "report.pdf").write_bytes(b"document")
            inventory = base / "inventory.json"
            scanned = run_script(SCAN, str(root), "--inventory-out", str(inventory))
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            result = run_script(
                QUERY,
                str(inventory),
                "--section",
                "generated-metadata",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["total_matching"], 1)
            self.assertEqual(payload["rows"][0]["relative_path"], "._report.pdf")

    def test_apply_dry_run_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.txt"
            source.write_text("payload", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root": str(root),
                        "rows": [
                            bound_plan_row(source, root, "folder/new.txt", "移动并改名")
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan), "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(source.exists())
            self.assertFalse((root / "folder" / "new.txt").exists())
            self.assertEqual(json.loads(result.stdout)["status"], "PREVIEW_ONLY")

    def test_apply_moves_without_changing_size_or_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.txt"
            source.write_text("payload", encoding="utf-8")
            before = source.stat()
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root": str(root),
                        "rows": [
                            bound_plan_row(source, root, "folder/new.txt", "移动并改名")
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan))

            self.assertEqual(result.returncode, 0, result.stderr)
            target = root / "folder" / "new.txt"
            self.assertFalse(source.exists())
            self.assertTrue(target.exists())
            self.assertEqual(target.stat().st_size, before.st_size)
            self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(
                json.loads(result.stdout)["status"],
                "PLAN_EXECUTION_COMPLETED",
            )

    def test_windows_move_does_not_require_hard_link_support(self):
        module = load_script_module(APPLY, "apply_plan_windows_move")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("payload", encoding="utf-8")

            with mock.patch.object(module.os, "name", "nt"), mock.patch.object(
                module.os, "link", side_effect=AssertionError("hard link must not be used")
            ):
                module.move_no_replace(source, target)

            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "payload")

    def test_apply_refuses_existing_target_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.txt"
            target = root / "new.txt"
            source.write_text("source", encoding="utf-8")
            target.write_text("target", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root": str(root),
                        "rows": [
                            bound_plan_row(source, root, "new.txt", "仅改名")
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_text(encoding="utf-8"), "source")
            self.assertEqual(target.read_text(encoding="utf-8"), "target")

    def test_apply_rejects_paths_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.txt").write_text("payload", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root": str(root),
                        "rows": [
                            bound_plan_row(root / "old.txt", root, "../escape.txt", "仅改名")
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((root / "old.txt").exists())
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_apply_preflight_failure_uses_plan_result_version_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"version": 2, "root": str(root), "rows": "not-a-list"}),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr)["version"], 2)

    def test_apply_rejects_source_changed_after_plan_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.txt"
            source.write_text("approved", encoding="utf-8")
            row = bound_plan_row(source, root, "new.txt", "仅改名")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"version": 2, "root": str(root), "rows": [row]}, ensure_ascii=False),
                encoding="utf-8",
            )
            source.write_text("changed after approval", encoding="utf-8")

            result = run_script(APPLY, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approved source state changed", result.stderr)
            self.assertTrue(source.exists())
            self.assertFalse((root / "new.txt").exists())

    def test_failed_plan_rolls_back_moves_and_removes_created_empty_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            blocker = root / "blocker"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            blocker.write_text("not a directory", encoding="utf-8")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root": str(root),
                        "rows": [
                            bound_plan_row(first, root, "created/first.txt", "移动并改名"),
                            bound_plan_row(second, root, "blocker/second.txt", "移动并改名"),
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertFalse((root / "created").exists())

    def test_hashes_only_same_size_candidates_and_reports_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "b.txt").write_text("same", encoding="utf-8")
            (root / "c.txt").write_text("diff", encoding="utf-8")
            (root / "large.txt").write_text("different-size", encoding="utf-8")
            inventory = base / "inventory.json"
            scanned = run_script(
                SCAN,
                str(root),
                "--inventory-out",
                str(inventory),
                "--include-duplicate-candidates",
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            report = base / "duplicates.json"

            result = run_script(HASH_DUPLICATES, str(inventory), "--output", str(report))

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["content_hashes_computed"], 3)
            self.assertEqual(payload["duplicate_groups"][0]["paths"], ["a.txt", "b.txt"])
            self.assertNotIn("large.txt", [row["relative_path"] for row in payload["hashed_files"]])

    def test_duplicate_deletion_requires_explicit_confirmation_and_preserves_keep_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            keep = root / "keep.txt"
            delete = root / "delete.txt"
            keep.write_text("same", encoding="utf-8")
            delete.write_text("same", encoding="utf-8")
            inventory = base / "inventory.json"
            scanned = run_script(
                SCAN,
                str(root),
                "--inventory-out",
                str(inventory),
                "--include-duplicate-candidates",
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            report = base / "duplicates.json"
            hashed = run_script(HASH_DUPLICATES, str(inventory), "--output", str(report))
            self.assertEqual(hashed.returncode, 0, hashed.stderr)
            digest = json.loads(report.read_text(encoding="utf-8"))["duplicate_groups"][0]["sha256"]
            plan = base / "duplicate-deletion-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root),
                        "hash_report": str(report),
                        "rows": [
                            {
                                "delete": "delete.txt",
                                "keep": "keep.txt",
                                "sha256": digest,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            dry_run = run_script(APPLY_DUPLICATES, str(plan))
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertTrue(delete.exists())
            unconfirmed = run_script(APPLY_DUPLICATES, str(plan), "--apply")
            self.assertNotEqual(unconfirmed.returncode, 0)
            self.assertTrue(delete.exists())
            applied = run_script(APPLY_DUPLICATES, str(plan), "--apply", "--confirmed")

            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue(keep.exists())
            self.assertFalse(delete.exists())

    def test_duplicate_deletion_reports_partial_completion_without_claiming_rollback(self):
        module = load_script_module(APPLY_DUPLICATES, "apply_duplicate_deletions_partial")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "keep.txt"
            first = root / "first.txt"
            second = root / "second.txt"
            for path in (keep, first, second):
                path.write_text("same", encoding="utf-8")
            digest = module.sha256_file(keep)
            prepared = [
                {"row": 1, "delete": first, "keep": keep, "sha256": digest},
                {"row": 2, "delete": second, "keep": keep, "sha256": digest},
            ]
            original_unlink = Path.unlink

            def fail_second(path: Path, *args, **kwargs):
                if path == second:
                    raise PermissionError("simulated delete failure")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", autospec=True, side_effect=fail_second):
                result, code = module.apply_deletions(prepared)

            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "DUPLICATE_DELETION_PARTIAL")
            self.assertEqual(result["deleted_count"], 1)
            self.assertFalse(result["rollback_supported"])
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(keep.exists())

    def test_duplicate_deletion_preflight_rejects_a_keep_path_scheduled_for_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text("same", encoding="utf-8")
            inventory = base / "inventory.json"
            scanned = run_script(
                SCAN,
                str(root),
                "--inventory-out",
                str(inventory),
                "--include-duplicate-candidates",
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            report = base / "duplicates.json"
            hashed = run_script(HASH_DUPLICATES, str(inventory), "--output", str(report))
            self.assertEqual(hashed.returncode, 0, hashed.stderr)
            digest = json.loads(report.read_text(encoding="utf-8"))["duplicate_groups"][0]["sha256"]
            plan = base / "duplicate-deletion-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root),
                        "hash_report": str(report),
                        "rows": [
                            {"delete": "b.txt", "keep": "a.txt", "sha256": digest},
                            {"delete": "c.txt", "keep": "b.txt", "sha256": digest},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_script(APPLY_DUPLICATES, str(plan))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("also scheduled for deletion", result.stderr)
            self.assertTrue(all((root / name).exists() for name in ("a.txt", "b.txt", "c.txt")))

    def test_path_boundary_validation_is_shared_by_all_filesystem_tools(self):
        common = (SKILL_ROOT / "scripts" / "_common.py").read_text(encoding="utf-8")
        self.assertIn("def resolve_relative_within_root", common)
        for script in (APPLY, APPLY_DUPLICATES, HASH_DUPLICATES, BUILD_EVIDENCE):
            source = script.read_text(encoding="utf-8")
            self.assertNotIn("def inside_root", source, script.name)
            self.assertIn("resolve_relative_within_root", source, script.name)

    def test_scan_agrees_that_ico_and_heif_are_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            for name in ("icon.ico", "photo.heif"):
                (root / name).write_bytes(b"not-a-real-image")
            inventory = base / "inventory.json"
            scanned = run_script(SCAN, str(root), "--inventory-out", str(inventory))
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            payload = json.loads(inventory.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(row["relative_path"] for row in payload["image_audit_candidates"]),
                ["icon.ico", "photo.heif"],
            )

    def test_skill_requires_ai_owned_completion_reconciliation(self):
        skill = SKILL.read_text(encoding="utf-8")

        self.assertIn("## AI-owned completion audit", skill)
        self.assertIn("all files = applicable human files + explicit exclusions", skill)
        self.assertIn("applicable human files = compliant + changed + undecided", skill)
        self.assertIn("Plan execution complete", skill)
        self.assertIn("Directory organization complete", skill)
        self.assertIn("## Image audit", skill)
        self.assertIn("human archive", skill)
        self.assertIn("program/design asset", skill)
        self.assertIn("EXIF", skill)
        self.assertIn("contact sheets", skill)
        self.assertIn("single recursive inventory", skill)
        self.assertIn("compact stdout", skill)
        self.assertIn("query-inventory.py", skill)
        self.assertIn("inventory changed", skill)
        self.assertIn("one pre-execution filesystem walk", skill)
        self.assertIn("--inventory <inventory.json>", skill)
        self.assertIn("never rescan", skill)
        self.assertIn("every applicable human file", skill)
        self.assertIn("path and filename are routing hints, not content evidence", skill)
        self.assertIn("build-evidence-index.py", skill)
        self.assertIn("compile-plan.py", skill)
        self.assertIn("hash-duplicate-candidates.py", skill)
        self.assertIn("apply-duplicate-deletions.py", skill)
        self.assertIn("runtime platform", skill)
        self.assertIn("expected_size", skill)
        self.assertIn("expected_mtime_ns", skill)
        self.assertIn("one task-local temporary directory", skill)
        self.assertIn("directory-specific scripts", skill)
        self.assertNotIn("Decide clear files from the saved path, name, and metadata", skill)
        self.assertNotIn("Inspect content only where path, name, and metadata are insufficient", skill)


if __name__ == "__main__":
    unittest.main()
