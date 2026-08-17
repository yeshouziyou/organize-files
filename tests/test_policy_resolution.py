import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = SKILL_ROOT / "scripts" / "resolve-policy.py"


def run_resolver(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RESOLVER), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=env,
    )


class PolicyResolutionTests(unittest.TestCase):
    def test_resolver_can_save_the_fixed_effective_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "policy.json"

            result = run_resolver("--no-local-config", "--output", str(output))

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["policy_source"], "public-safe-default")
            self.assertEqual(payload["empty_directories"], "preview")

    def test_public_default_is_preview_only(self):
        result = run_resolver("--no-local-config")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["generated_metadata"]["macos-ds-store"], "preview")
        self.assertEqual(payload["generated_metadata"]["macos-appledouble"], "preview")
        self.assertEqual(payload["generated_metadata"]["office-lock"], "preview")
        self.assertEqual(payload["empty_directories"], "preview")
        self.assertEqual(payload["language"], "auto")

    def test_cross_agent_environment_variable_selects_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "organize-files.json"
            local.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "preset": "windows-no-macos",
                        "language": "en",
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["ORGANIZE_FILES_CONFIG"] = str(local)

            result = run_resolver("--platform", "windows", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["policy_source"], str(local.resolve()))
            self.assertEqual(payload["language"], "en")
            self.assertEqual(payload["generated_metadata"]["macos-ds-store"], "auto")

    def test_windows_no_macos_preset_preserves_current_local_cleanup_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "organize-files.local.json"
            local.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "preset": "windows-no-macos",
                        "timezone": "Asia/Shanghai",
                        "language": "zh-CN",
                    }
                ),
                encoding="utf-8",
            )

            result = run_resolver("--local-config", str(local), "--platform", "windows")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["generated_metadata"]["macos-ds-store"], "auto")
            self.assertEqual(payload["generated_metadata"]["macos-appledouble"], "auto")
            self.assertEqual(payload["generated_metadata"]["office-lock"], "auto-if-safe")
            self.assertEqual(payload["empty_directories"], "auto-if-safe")
            self.assertEqual(payload["timezone"], "Asia/Shanghai")
            self.assertEqual(payload["language"], "zh-CN")

    def test_macos_forces_generated_metadata_to_preview_even_with_windows_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "organize-files.local.json"
            local.write_text(
                json.dumps({"version": 1, "preset": "windows-no-macos"}),
                encoding="utf-8",
            )

            result = run_resolver("--local-config", str(local), "--platform", "macos")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["runtime_platform"], "macos")
            self.assertEqual(payload["generated_metadata"]["macos-ds-store"], "preview")
            self.assertEqual(payload["generated_metadata"]["macos-appledouble"], "preview")
            self.assertEqual(payload["generated_metadata"]["office-lock"], "preview")
            self.assertIn("macos-generated-metadata-protection", payload["policy_adjustments"])

    def test_unknown_preset_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "organize-files.local.json"
            local.write_text(
                json.dumps({"version": 1, "preset": "missing"}),
                encoding="utf-8",
            )

            result = run_resolver("--local-config", str(local))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown preset", result.stderr)


if __name__ == "__main__":
    unittest.main()
