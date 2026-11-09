"""Knowledge base tests."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestKB(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-kb-"))
        src = ROOT / "template" / "workspace" / "knowledge"
        self.kb = self.tmp / "knowledge"
        shutil.copytree(src, self.kb)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_dedupe_map(self) -> None:
        from daftar import kb as kbmod

        code, msg = kbmod.cmd_add(
            self.kb,
            "coding",
            "Always run the test suite before declaring a fix complete.",
            by="assistant",
        )
        self.assertEqual(code, 0, msg)
        # Near duplicate
        code2, msg2 = kbmod.cmd_add(
            self.kb,
            "coding",
            "Always run the test suite before declaring a fix complete!",
            by="assistant",
        )
        self.assertEqual(code2, 1, msg2)
        self.assertIn("Near-duplicate", msg2)
        # Force
        code3, _ = kbmod.cmd_add(
            self.kb,
            "coding",
            "Always run the test suite before declaring a fix complete!",
            by="assistant",
            force=True,
        )
        self.assertEqual(code3, 0)
        # Map regenerated
        m = (self.kb / "MAP.md").read_text(encoding="utf-8")
        self.assertIn("Knowledge map", m)
        self.assertIn("coding", m.lower() or m)
        # check
        c, out = kbmod.cmd_check(self.kb)
        self.assertEqual(c, 0, out)

    def test_new_topic(self) -> None:
        from daftar import kb as kbmod

        code, msg = kbmod.cmd_new_topic(
            self.kb, "widgets", "Widgets", "When working on widgets"
        )
        self.assertEqual(code, 0, msg)
        self.assertTrue((self.kb / "topics" / "widgets.md").exists())


if __name__ == "__main__":
    unittest.main()
