"""Codex engine, profiles, AGENTS.md, topic injection, delegation tests."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

# Reuse FakeTG from test_bridge
from test_bridge import FakeTG, make_handler  # noqa: E402


class CodexProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-codex-"))
        self.home = self.tmp / "home"
        self.ws = self.tmp / "workspace"
        self.home.mkdir()
        src = ROOT / "template" / "workspace"
        shutil.copytree(src, self.ws)
        # Seed a coding keyword for topic injection
        from daftar import kb as kbmod

        kbmod.cmd_add(
            self.ws / "knowledge",
            "coding",
            "Prefer small pull requests with focused diffs and clear tests.",
            by="owner",
        )

        os.environ["DAFTAR_HOME"] = str(self.home)
        os.environ.pop("DAFTAR_PROFILE", None)
        self.codex_home = self.tmp / "codex_home"
        self.codex_home.mkdir()
        os.environ["CODEX_HOME"] = str(self.codex_home)

        self.tg_state = FakeTG()
        handler = make_handler(self.tg_state)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        self._install_fake("claude", FAKE_CLAUDE)
        self._install_fake("codex", FAKE_CODEX)
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")

        self.owner_id = 424242
        self._bridges: list = []

    def _install_fake(self, name: str, script: Path) -> None:
        link = self.bin_dir / name
        link.write_text(
            "#!/bin/sh\nexec python3 \"%s\" \"$@\"\n" % script,
            encoding="utf-8",
        )
        link.chmod(link.stat().st_mode | stat.S_IEXEC)

    def tearDown(self) -> None:
        for b in getattr(self, "_bridges", []):
            try:
                b.engine.cancel()
                b.stop()
            except Exception:
                pass
            deadline = time.time() + 5
            while getattr(b, "_running", False) and time.time() < deadline:
                b.engine.cancel()
                time.sleep(0.05)
        self.httpd.shutdown()
        self.httpd.server_close()
        os.environ.pop("DAFTAR_PROFILE", None)
        os.environ.pop("CODEX_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save_cfg(self, profile: str, **extra: str) -> None:
        from daftar.config import save_config, set_active_profile

        set_active_profile(profile)
        from daftar.config import save_config as sc

        base = {
            "TELEGRAM_BOT_TOKEN": "TESTTOKEN",
            "OWNER_ID": str(self.owner_id),
            "WORKSPACE": str(self.ws),
            "LANGUAGE": "English",
            "PERMISSION_MODE": "bypassPermissions",
            "ROTATE_TOKENS": "120000",
            "TELEGRAM_API_BASE": f"http://127.0.0.1:{self.port}",
            "SUMMARY_MODEL": "sonnet",
            "NIGHTLY_HOUR": "99",
            "ENGINE": "codex",
            "PERSONA_FILE": "persona/PERSONA.md",
            "BOT_USERNAME": "codex_bot",
        }
        base.update(extra)
        sc(base)

    def _bridge(self, profile: str = "codex"):
        from daftar.bridge import Bridge
        from daftar.config import load_config, set_active_profile

        set_active_profile(profile)
        b = Bridge(load_config())
        self._bridges.append(b)
        return b

    def _wait_sent(self, pred, timeout: float = 5.0) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.tg_state.lock:
                hits = [s for s in self.tg_state.sent if pred(s)]
                if hits:
                    return hits
            time.sleep(0.05)
        with self.tg_state.lock:
            return list(self.tg_state.sent)

    def test_codex_text_turn_e2e(self) -> None:
        self._save_cfg("codex")
        bridge = self._bridge("codex")
        bridge._execute_turn("hello codex")
        with self.tg_state.lock:
            texts = [s.get("text", "") for s in self.tg_state.sent if s.get("method") == "sendMessage"]
        self.assertTrue(any("Echo" in t for t in texts), msg=texts)
        self.assertIsNotNone(bridge.state.current_session_id)
        self.assertTrue((self.ws / "AGENTS.md").exists())

    def test_codex_resume_keeps_id(self) -> None:
        self._save_cfg("codex")
        bridge = self._bridge("codex")
        bridge._execute_turn("first")
        sid = bridge.state.current_session_id
        self.assertTrue(sid)
        bridge._execute_turn("second")
        self.assertEqual(bridge.state.current_session_id, sid)
        with self.tg_state.lock:
            texts = [s.get("text", "") for s in self.tg_state.sent if s.get("method") == "sendMessage"]
        self.assertTrue(any("Resumed" in t for t in texts), msg=texts)

    def test_codex_interrupt(self) -> None:
        self._save_cfg("codex")
        bridge = self._bridge("codex")
        t = threading.Thread(target=bridge._run_turn, args=("SLOW please",), daemon=True)
        t.start()
        time.sleep(0.15)
        bridge._enqueue_user_text("first")
        bridge._enqueue_user_text("second")
        t.join(timeout=10)
        deadline = time.time() + 8
        while time.time() < deadline and bridge._running:
            time.sleep(0.05)
        time.sleep(0.2)
        with self.tg_state.lock:
            texts = [s.get("text", "") for s in self.tg_state.sent if s.get("method") == "sendMessage"]
        joined = "\n".join(texts)
        self.assertTrue(
            any("first" in t and "second" in t for t in texts)
            or ("first" in joined and "second" in joined),
            msg=texts,
        )

    def test_codex_image_delivered_as_photo(self) -> None:
        self._save_cfg("codex")
        bridge = self._bridge("codex")
        bridge._execute_turn("please MAKE_IMAGE now")
        with self.tg_state.lock:
            methods = [s.get("method") for s in self.tg_state.sent]
        self.assertIn("sendPhoto", methods, msg=methods)

    def test_agents_md_regen_and_persona(self) -> None:
        from daftar.agents_md import ensure_agents_md, build_agents_md

        # Two personas in shared workspace
        p_claude = self.ws / "persona" / "PERSONA.claude.md"
        p_codex = self.ws / "persona" / "PERSONA.codex.md"
        p_claude.write_text("# Persona\n\nYou are ClaudeBot.\n", encoding="utf-8")
        p_codex.write_text("# Persona\n\nYou are CodexBot.\n", encoding="utf-8")

        ensure_agents_md(self.ws, persona_file=p_codex, force=True)
        body = (self.ws / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("CodexBot", body)
        self.assertIn("Generated by Daftar", body)
        self.assertNotIn("ClaudeBot", body)

        # MAP change → regen
        map_path = self.ws / "knowledge" / "MAP.md"
        old_mtime = (self.ws / "AGENTS.md").stat().st_mtime
        time.sleep(0.05)
        map_path.write_text(map_path.read_text(encoding="utf-8") + "\n- **extra line**\n", encoding="utf-8")
        ensure_agents_md(self.ws, persona_file=p_codex)
        self.assertGreater((self.ws / "AGENTS.md").stat().st_mtime, old_mtime - 0.001)
        self.assertIn("extra line", (self.ws / "AGENTS.md").read_text(encoding="utf-8"))

        # Right persona when switching
        text = build_agents_md(self.ws, p_claude)
        self.assertIn("ClaudeBot", text)

    def test_codex_topic_injection_once(self) -> None:
        self._save_cfg("codex")
        bridge = self._bridge("codex")
        # First turn mentioning code should inject topic into prompt (seen tracked)
        bridge._execute_turn("Please help me refactor this code and fix the bug")
        seen = bridge.state.extra.get("kb_seen") or []
        self.assertIn("coding", seen)
        # Second turn same session — no new injection (seen stays)
        n = len(seen)
        bridge._execute_turn("more code please")
        self.assertEqual(len(bridge.state.extra.get("kb_seen") or []), n)

    def test_two_profiles_shared_workspace(self) -> None:
        from daftar.config import (
            load_config,
            set_active_profile,
            state_path,
            save_config,
        )

        # Default claude profile
        set_active_profile("")
        save_config(
            {
                "TELEGRAM_BOT_TOKEN": "CLAUDETOKEN",
                "OWNER_ID": str(self.owner_id),
                "WORKSPACE": str(self.ws),
                "ENGINE": "claude",
                "PERSONA_FILE": "persona/PERSONA.md",
                "BOT_USERNAME": "claude_bot",
                "NIGHTLY_HOUR": "99",
                "TELEGRAM_API_BASE": f"http://127.0.0.1:{self.port}",
            }
        )
        # Codex profile sharing workspace
        self._save_cfg(
            "codex",
            PERSONA_FILE="persona/PERSONA.codex.md",
            BOT_USERNAME="codex_bot",
        )
        (self.ws / "persona" / "PERSONA.codex.md").write_text(
            "# Persona\n\nCodex voice.\n", encoding="utf-8"
        )

        set_active_profile("")
        c_cfg = load_config()
        set_active_profile("codex")
        x_cfg = load_config()
        self.assertEqual(c_cfg["ENGINE"], "claude")
        self.assertEqual(x_cfg["ENGINE"], "codex")
        self.assertEqual(Path(c_cfg["WORKSPACE"]), Path(x_cfg["WORKSPACE"]))
        self.assertNotEqual(state_path(""), state_path("codex"))

        # Separate state files
        from daftar.sessions import AppState, save_state, load_state

        set_active_profile("")
        st = AppState(current_session_id="claude-sess")
        save_state(st)
        set_active_profile("codex")
        st2 = AppState(current_session_id="codex-sess")
        save_state(st2)
        set_active_profile("")
        self.assertEqual(load_state().current_session_id, "claude-sess")
        set_active_profile("codex")
        self.assertEqual(load_state().current_session_id, "codex-sess")

    def test_delegate_claude_to_codex(self) -> None:
        from daftar.config import set_active_profile, save_config
        from daftar import jobs as jobsmod

        # Claude (default) + Codex profile
        set_active_profile("")
        save_config(
            {
                "TELEGRAM_BOT_TOKEN": "CLAUDETOKEN",
                "OWNER_ID": str(self.owner_id),
                "WORKSPACE": str(self.ws),
                "ENGINE": "claude",
                "PERSONA_FILE": "persona/PERSONA.md",
                "NIGHTLY_HOUR": "99",
                "TELEGRAM_API_BASE": f"http://127.0.0.1:{self.port}",
                "BOT_USERNAME": "claude_bot",
            }
        )
        self._save_cfg("codex", PERSONA_FILE="persona/PERSONA.codex.md")
        (self.ws / "persona" / "PERSONA.codex.md").write_text(
            "# Persona\n\nCodex.\n", encoding="utf-8"
        )

        # Enqueue from default → codex
        set_active_profile("")
        job_id = jobsmod.enqueue(
            "codex",
            title="draw logo",
            brief="Make a simple logo. MAKE_IMAGE please.",
            requester="default",
        )
        self.assertTrue(job_id)
        q = list((self.home / "profiles" / "codex" / "jobs" / "queue").glob("*.json"))
        self.assertEqual(len(q), 1)

        # Worker on codex bridge
        codex_bridge = self._bridge("codex")
        codex_bridge._process_one_job()
        result_dir = self.ws / "delegations" / job_id
        self.assertTrue((result_dir / "RESULT.md").exists())
        results = list((self.home / "jobs" / "results").glob("*.json"))
        self.assertEqual(len(results), 1)
        result = json.loads(results[0].read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")

        # Requester (claude) polls and delivers
        set_active_profile("")
        from daftar.bridge import Bridge
        from daftar.config import load_config

        claude_bridge = Bridge(load_config())
        self._bridges.append(claude_bridge)
        claude_bridge._poll_job_results()
        with self.tg_state.lock:
            texts = [s.get("text", "") for s in self.tg_state.sent if s.get("method") == "sendMessage"]
        self.assertTrue(
            any("Echo" in t or "Delegated" in t or "Resumed" in t or "draw" in t.lower() for t in texts)
            or texts,
            msg=texts,
        )
        # Result consumed
        self.assertFalse(list((self.home / "jobs" / "results").glob("*.json")))

    def test_delegate_failed_job(self) -> None:
        from daftar.config import set_active_profile, save_config
        from daftar import jobs as jobsmod

        self._save_cfg("codex")
        set_active_profile("")
        save_config(
            {
                "TELEGRAM_BOT_TOKEN": "CLAUDETOKEN",
                "OWNER_ID": str(self.owner_id),
                "WORKSPACE": str(self.ws),
                "ENGINE": "claude",
                "NIGHTLY_HOUR": "99",
                "TELEGRAM_API_BASE": f"http://127.0.0.1:{self.port}",
            }
        )
        job_id = jobsmod.enqueue(
            "codex",
            title="fail me",
            brief="FORCE_AUTH_FAIL",
            requester="default",
        )
        bridge = self._bridge("codex")
        bridge._process_one_job()
        results = list((self.home / "jobs" / "results").glob("*.json"))
        self.assertEqual(len(results), 1)
        data = json.loads(results[0].read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["id"], job_id)

    def test_codex_prompt_via_stdin_round_trip(self) -> None:
        """Prompts reach codex on stdin intact: leading "- " and ~200 KB."""
        from daftar.engine_codex import CodexEngine
        from daftar.projects import _run_codex

        record = self.tmp / "prompt.txt"
        os.environ["FAKE_CODEX_PROMPT_OUT"] = str(record)
        self.addCleanup(os.environ.pop, "FAKE_CODEX_PROMPT_OUT", None)
        dash = "- first bullet\n- second bullet --json -o nowhere"
        big = "- big " + ("x" * 63 + "\n") * 3200  # ~200 KB, > pipe buffer
        self.assertGreater(len(big), 200_000)

        engine = CodexEngine(cwd=self.ws, binary="codex")
        res = engine.run(dash)
        self.assertFalse(res.is_error, msg=res.error)
        self.assertEqual(record.read_text(encoding="utf-8"), dash)
        self.assertTrue(res.session_id)

        res2 = engine.run(big, resume=res.session_id)
        self.assertFalse(res2.is_error, msg=res2.error)
        self.assertTrue(res2.text.startswith("Resumed("), msg=res2.text[:80])
        self.assertEqual(record.read_text(encoding="utf-8"), big)

        out = _run_codex(big, model="")
        self.assertIsNotNone(out)
        self.assertEqual(record.read_text(encoding="utf-8"), big)


if __name__ == "__main__":
    unittest.main()
