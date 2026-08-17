import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
AUDIT = SKILL_ROOT / "scripts" / "check-public-release.py"


def run_audit(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), str(root), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class PublicReleaseAuditTests(unittest.TestCase):
    def test_public_package_has_bilingual_docs_license_and_portable_installation(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (SKILL_ROOT / "README.md").read_text(encoding="utf-8")
        readme_zh = (SKILL_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        license_text = (SKILL_ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("license: MIT", skill)
        self.assertIn("兼容", skill.split("---", 2)[1])
        self.assertIn("respond in the user's language", skill.casefold())
        self.assertIn(".agents/skills/organize-files", readme)
        self.assertIn("Agent Skills", readme)
        self.assertIn("中文", readme)
        self.assertIn("English", readme_zh)
        self.assertIn("MIT License", license_text)
        self.assertIn("organize-files contributors", license_text)

    def test_openai_metadata_requires_explicit_invocation(self):
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("$organize-files", metadata)

    def test_environment_variable_can_supply_private_denylist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "skill"
            root.mkdir()
            (root / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
            (root / "SKILL.md").write_text("PrivatePortableName", encoding="utf-8")
            denylist = base / "denylist.json"
            denylist.write_text(
                '{"version": 1, "patterns": [{"kind": "private-identity", "value": "PrivatePortableName"}]}',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["ORGANIZE_FILES_DENYLIST"] = str(denylist)

            result = subprocess.run(
                [sys.executable, str(AUDIT), str(root)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("private-identity", result.stderr)

    def test_optional_private_denylist_detects_local_identity_without_embedding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "skill"
            root.mkdir()
            (root / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
            (root / "SKILL.md").write_text("PrivateExampleName", encoding="utf-8")
            denylist = base / "denylist.json"
            denylist.write_text(
                '{"version": 1, "patterns": [{"kind": "private-identity", "value": "PrivateExampleName"}]}',
                encoding="utf-8",
            )

            result = run_audit(root, "--denylist", str(denylist))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("private-identity", result.stderr)

    def test_compiled_cache_is_reported_once_without_scanning_binary_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
            (root / "SKILL.md").write_text("generic", encoding="utf-8")
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "sample.pyc").write_bytes(b"C:\\Users\\PrivateExampleName")

            result = run_audit(root)

            payload = json.loads(result.stderr)
            self.assertEqual(payload["finding_count"], 1)
            self.assertEqual(payload["findings"][0]["kind"], "compiled-cache")

    def test_audit_requires_compiled_cache_ignore_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("generic skill", encoding="utf-8")

            result = run_audit(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing-ignore-rule", result.stderr)

    def test_audit_rejects_compiled_cache_and_local_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "local path: " + "C:" + "\\Users\\" + "ExampleUser\\skill",
                encoding="utf-8",
            )
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "sample.pyc").write_bytes(b"compiled")

            result = run_audit(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("compiled-cache", result.stderr)
            self.assertIn("local-absolute-path", result.stderr)

    def test_current_skill_package_is_public_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "organize-files"
            shutil.copytree(
                SKILL_ROOT,
                staged,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            result = run_audit(staged)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status": "PUBLIC_RELEASE_AUDIT_PASSED"', result.stdout)


if __name__ == "__main__":
    unittest.main()
