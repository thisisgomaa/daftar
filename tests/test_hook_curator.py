"""Hook injection + curator dry-run tests."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "daftar" / "hooks" / "kb_hook.py"
FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


class TestHook(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-hook-"))
        src = ROOT / "template" / "workspace" / "knowledge"
        self.kb = self.tmp / "knowledge"
        shutil.copytree(src, self.kb)
        # Seed a keyword hit
        from daftar import kb as kbmod

        kbmod.cmd_add(
            self.kb,
            "coding",
            "Prefer small pull requests with focused diffs and clear tests.",
            by="owner",
        )
        os.environ["DAFTAR_KB"] = str(self.kb)

    def tearDown(self) -> None:
        os.environ.pop("DAFTAR_KB", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_hook(self, payload: dict) -> dict | None:
        p = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={**os.environ, "DAFTAR_KB": str(self.kb), "DAFTAR_HOOK_STATE": str(self.tmp / "hookstate")},
        )
        out = (p.stdout or "").strip()
        if not out:
            return None
        return json.loads(out)

    def test_inject_once_per_session(self) -> None:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-test-1",
            "prompt": "Please help me refactor this code and fix the bug ⟨Reply in English.⟩",
        }
        first = self._run_hook(payload)
        self.assertIsNotNone(first)
        ctx = first["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Coding", ctx)
        # Second time same session — no re-inject
        second = self._run_hook(payload)
        self.assertIsNone(second)

    def test_strips_reminder_brackets(self) -> None:
        # Reminder alone should not match keywords
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-test-2",
            "prompt": "⟨Reply in English. Be concise.⟩",
        }
        self.assertIsNone(self._run_hook(payload))


class TestCurator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-cur-"))
        src = ROOT / "template" / "workspace" / "knowledge"
        self.kb = self.tmp / "knowledge"
        shutil.copytree(src, self.kb)
        from daftar import kb as kbmod

        kbmod.cmd_add(
            self.kb,
            "coding",
            "Always take a backup before rewriting production configs.",
            by="assistant",
        )
        kbmod.cmd_add(
            self.kb,
            "coding",
            "Always take a backup before rewriting production configuration files.",
            by="assistant",
            force=True,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_with_fake_model(self) -> None:
        from daftar.curator import run_curator

        def runner(prompt: str) -> str:
            return json.dumps(
                {
                    "merge": [
                        {
                            "lines": [1, 2],
                            "text": "Always take a verified backup before rewriting production configs.",
                        }
                    ],
                    "retire": [],
                    "move": [],
                    "propose_core": [],
                    "propose_rule": [],
                }
            )

        code = run_curator(
            workspace=self.tmp,
            dry_run=True,
            full=True,
            topic="coding",
            runner=runner,
        )
        self.assertEqual(code, 0)
        report = (self.kb / "_archive" / "curator_last_run.md").read_text(encoding="utf-8")
        self.assertIn("coding", report)
        # Dry-run must not rewrite topic body (still 2 lessons)
        body = (self.kb / "topics" / "coding.md").read_text(encoding="utf-8")
        lessons = [l for l in body.splitlines() if l.startswith("- 20")]
        self.assertGreaterEqual(len(lessons), 2)


if __name__ == "__main__":
    unittest.main()
