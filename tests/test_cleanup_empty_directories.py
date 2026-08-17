import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup-empty-directories.py"
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = SKILL_ROOT / "config" / "default.json"


def load_module():
    spec = importlib.util.spec_from_file_location("cleanup_empty_directories", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CleanupEmptyDirectoriesTests(unittest.TestCase):
    def test_public_preview_policy_rejects_apply_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "keep"
            empty.mkdir()

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(root),
                    "--apply",
                    "--policy",
                    str(DEFAULT_POLICY),
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("policy forbids apply", result.stderr)
            self.assertTrue(empty.exists())

    def test_skill_routes_post_execution_empty_directory_cleanup(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        standard = (SKILL_ROOT / "references" / "通用文件分类标准.md").read_text(encoding="utf-8")
        rule = (SKILL_ROOT / "references" / "20260816_空目录清理规则.md").read_text(encoding="utf-8")
        risk = (SKILL_ROOT / "references" / "20260816_文件整理风险与验证规则.md").read_text(encoding="utf-8")

        self.assertIn("cleanup-empty-directories.py", skill)
        self.assertIn("`1.7`", skill)
        self.assertIn("版本：1.7", standard)
        self.assertIn("安全空目录", standard)
        self.assertIn("版本：1.1", rule)
        self.assertIn("版本：1.5", risk)
        self.assertIn("空目录清理规则", risk)

    def test_dry_run_finds_nested_empty_directories_without_deleting(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = root / "old" / "nested"
            leaf.mkdir(parents=True)

            result = module.cleanup(root, apply=False, excluded=())

            self.assertEqual(result["would_delete_count"], 2)
            self.assertEqual(result["deleted_count"], 0)
            self.assertTrue(leaf.exists())
            self.assertTrue(root.exists())

    def test_apply_deletes_empty_directories_bottom_up_but_preserves_content_and_root(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old" / "nested").mkdir(parents=True)
            kept = root / "kept"
            kept.mkdir()
            (kept / "document.txt").write_text("content", encoding="utf-8")

            result = module.cleanup(root, apply=True, excluded=())

            self.assertEqual(result["deleted_count"], 2)
            self.assertFalse((root / "old").exists())
            self.assertTrue(kept.exists())
            self.assertTrue(root.exists())

    def test_excluded_empty_directory_is_preserved(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected = root / "program-project" / "empty-cache"
            protected.mkdir(parents=True)

            result = module.cleanup(root, apply=True, excluded=(Path("program-project"),))

            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertTrue(protected.exists())


if __name__ == "__main__":
    unittest.main()
