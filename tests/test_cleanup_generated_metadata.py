import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup-generated-metadata.py"
SCAN = Path(__file__).resolve().parents[1] / "scripts" / "scan-files.py"
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "default.json"
AUTO_POLICY = Path(__file__).resolve().parents[1] / "config" / "presets" / "windows-no-macos.json"


def load_module():
    spec = importlib.util.spec_from_file_location("cleanup_generated_metadata", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CleanupGeneratedMetadataTests(unittest.TestCase):
    def test_inventory_root_comparison_normalizes_the_selected_root(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            (root / ".DS_Store").write_bytes(b"finder")
            inventory = base / "inventory.json"
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            previous = Path.cwd()
            try:
                os.chdir(base)
                result = module.cleanup_from_inventory(Path("root"), inventory, apply=False)
            finally:
                os.chdir(previous)

            self.assertEqual(result["candidate_count"], 1)

    def test_dry_run_requires_saved_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / ".DS_Store"
            metadata.write_bytes(b"finder")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(root)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--inventory is required", result.stderr)
            self.assertTrue(metadata.exists())

    def test_apply_requires_saved_inventory_even_with_auto_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / ".DS_Store"
            metadata.write_bytes(b"finder")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(root),
                    "--apply",
                    "--policy",
                    str(AUTO_POLICY),
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--inventory is required", result.stderr)
            self.assertTrue(metadata.exists())

    def test_public_preview_policy_rejects_apply_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / ".DS_Store"
            metadata.write_bytes(b"finder")

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
            self.assertTrue(metadata.exists())

    def test_inventory_driven_apply_deletes_only_scanned_candidates_and_updates_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            listed = root / "._report.pdf"
            listed.write_bytes(b"appledouble")
            document = root / "report.pdf"
            document.write_bytes(b"document")
            inventory = base / "inventory.json"

            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)
            created_after_scan = root / "._new-after-scan.pdf"
            created_after_scan.write_bytes(b"new")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(root),
                    "--inventory",
                    str(inventory),
                    "--apply",
                    "--policy",
                    str(AUTO_POLICY),
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            updated = json.loads(inventory.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "inventory")
            self.assertEqual(payload["deleted_count"], 1)
            self.assertFalse(listed.exists())
            self.assertTrue(created_after_scan.exists())
            self.assertTrue(document.exists())
            self.assertEqual(updated["generated_metadata_candidates"], [])
            self.assertEqual(updated["generated_metadata_cleanup"]["deleted_count"], 1)
            self.assertEqual(updated["total_file_count"], 1)
            self.assertEqual(updated["known_current_file_count"], 1)

    def test_dry_run_finds_metadata_but_keeps_everything(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            (root / ".DS_Store").write_bytes(b"finder")
            (root / "._report.pdf").write_bytes(b"appledouble")
            (root / "report.pdf").write_bytes(b"document")
            inventory = base / "inventory.json"
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            result = module.cleanup_from_inventory(root, inventory, apply=False)

            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["candidate_count"], 2)
            self.assertTrue((root / ".DS_Store").exists())
            self.assertTrue((root / "._report.pdf").exists())
            self.assertTrue((root / "report.pdf").exists())

    def test_apply_deletes_only_macos_metadata_and_preserves_fonts(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            fonts = root / "fonts"
            fonts.mkdir()
            (fonts / ".DS_Store").write_bytes(b"finder")
            (fonts / "._Font.ttf").write_bytes(b"appledouble")
            (fonts / "Font.ttf").write_bytes(b"font-data")
            (fonts / "Font.otf").write_bytes(b"font-data-2")
            inventory = base / "inventory.json"
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            result = module.cleanup_from_inventory(root, inventory, apply=True)

            self.assertEqual(result["deleted_count"], 2)
            self.assertFalse((fonts / ".DS_Store").exists())
            self.assertFalse((fonts / "._Font.ttf").exists())
            self.assertTrue((fonts / "Font.ttf").exists())
            self.assertTrue((fonts / "Font.otf").exists())

    def test_stale_office_lock_is_deleted_when_companion_is_not_active(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            lock = root / "~$report.docx"
            lock.write_bytes(b"lock")
            (root / "report.docx").write_bytes(b"document")
            inventory = base / "inventory.json"
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            with mock.patch.object(module, "office_lock_is_safe_to_delete", return_value=(True, "not-active")):
                result = module.cleanup_from_inventory(root, inventory, apply=True)

            self.assertEqual(result["deleted_count"], 1)
            self.assertFalse(lock.exists())
            self.assertTrue((root / "report.docx").exists())

    def test_active_or_uncertain_office_lock_is_skipped(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            root.mkdir()
            lock = root / "~$report.docx"
            lock.write_bytes(b"lock")
            inventory = base / "inventory.json"
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(root), "--inventory-out", str(inventory)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 0, scanned.stderr)

            with mock.patch.object(module, "office_lock_is_safe_to_delete", return_value=(False, "active-or-uncertain")):
                result = module.cleanup_from_inventory(root, inventory, apply=True)

            self.assertEqual(result["deleted_count"], 0)
            self.assertEqual(result["skipped_count"], 1)
            self.assertTrue(lock.exists())


if __name__ == "__main__":
    unittest.main()
