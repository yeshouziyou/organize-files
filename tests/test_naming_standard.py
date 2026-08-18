import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL = SKILL_ROOT / "SKILL.md"
STANDARD = SKILL_ROOT / "references" / "通用文件命名标准.md"


class NamingStandardContractTests(unittest.TestCase):
    def test_strong_subject_records_require_verified_subject_in_filename(self):
        skill = SKILL.read_text(encoding="utf-8")
        standard = STANDARD.read_text(encoding="utf-8")

        version = re.search(r"^> 版本：(\d+\.\d+)$", standard, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertIn(
            f"`references/通用文件命名标准.md` `{version.group(1)}`",
            skill,
        )
        self.assertIn("强主体档案", standard)
        self.assertIn("不得仅根据所选根目录或父文件夹推定主体", standard)
        self.assertIn("空白模板不添加姓名", standard)
        self.assertIn("强主体档案是否保留了经证据确认的当事人姓名", standard)


if __name__ == "__main__":
    unittest.main()
