"""Tests for transcripts, project summaries, backups, and connectors."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


class ProjectsBackupConnectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-pbc-"))
        self.home = self.tmp / "home"
        self.ws = self.tmp / "workspace"
        self.home.mkdir()
        self.ws.mkdir()
        (self.ws / "inbox").mkdir()
        (self.ws / "outbox" / "sent").mkdir(parents=True)
        (self.ws / "sessions" / "transcripts").mkdir(parents=True)
        os.environ["DAFTAR_HOME"] = str(self.home)
        os.environ.pop("DAFTAR_PROFILE", None)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_script(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    # ── transcripts ───────────────────────────────────────────────

    def test_append_transcript(self) -> None:
        from daftar.sessions import append_transcript

        when = datetime(2026, 3, 15, 14, 30, tzinfo=ZoneInfo("UTC"))
        path = append_transcript(
            self.ws,
            engine="claude",
            session_id="abcdef12-zzzz",
            user_text="Where is the site redesign?",
            reply_text="Still in wireframes.",
            timezone_name="UTC",
            when=when,
        )
        self.assertEqual(path.name, "2026-03-15_claude_abcdef12.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("### 14:30 owner\nWhere is the site redesign?", text)
        self.assertIn("### 14:30 claude\nStill in wireframes.", text)
        # append-only
        append_transcript(
            self.ws,
            engine="claude",
            session_id="abcdef12-zzzz",
            user_text="ok",
            reply_text="noted",
            timezone_name="UTC",
            when=when.replace(minute=31),
        )
        text2 = path.read_text(encoding="utf-8")
        self.assertIn("Still in wireframes.", text2)
        self.assertIn("### 14:31 owner\nok", text2)

    # ── projects summarize ────────────────────────────────────────

    def test_summarize_creates_project_idempotent(self) -> None:
        from daftar import projects as projectsmod

        day = "2026-03-15"
        tdir = self.ws / "sessions" / "transcripts"
        tpath = tdir / f"{day}_claude_aabbccdd.md"
        tpath.write_text(
            "### 10:00 owner\nWork on the website redesign.\n\n"
            "### 10:00 claude\nWireframes done; next is content.\n\n",
            encoding="utf-8",
        )

        def runner(_prompt: str) -> str:
            return json.dumps(
                {
                    "projects": [
                        {
                            "slug": "",
                            "new_title": "Website Redesign",
                            "entry": "Wireframes finished.\nContent next.",
                            "status": "Wireframes done; drafting content.",
                        }
                    ],
                    "decisions": [],
                    "open_items": ["content"],
                }
            )

        r1 = projectsmod.summarize_day(
            self.ws, day, preferred_engine="claude", runner=runner
        )
        self.assertEqual(r1["processed"], 1)
        self.assertEqual(r1["entries"], 1)
        slug = "website-redesign"
        status = (self.ws / "projects" / slug / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("Wireframes done", status)
        self.assertIn("Last updated: 2026-03-15", status)
        log = (self.ws / "projects" / slug / "LOG.md").read_text(encoding="utf-8")
        self.assertIn("Wireframes finished", log)
        self.assertIn("claude", log)
        self.assertIn("aabbccdd", log)
        index = (self.ws / "projects" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Website Redesign", index)
        self.assertIn(slug, index)

        # Idempotent re-run: same fingerprint → skip
        calls = {"n": 0}

        def runner2(p: str) -> str:
            calls["n"] += 1
            return runner(p)

        r2 = projectsmod.summarize_day(
            self.ws, day, preferred_engine="claude", runner=runner2
        )
        self.assertEqual(r2["skipped"], 1)
        self.assertEqual(r2["processed"], 0)
        self.assertEqual(calls["n"], 0)
        # LOG unchanged length
        log2 = (self.ws / "projects" / slug / "LOG.md").read_text(encoding="utf-8")
        self.assertEqual(log, log2)

    def test_nightly_summarizes_yesterday(self) -> None:
        """At the nightly hour, yesterday's transcript is summarized, once."""
        from types import SimpleNamespace

        from daftar.scheduler import NightlyScheduler

        calls = self.tmp / "summary_calls.log"
        self._write_script(
            "claude",
            f'''import json, sys
from pathlib import Path
sys.stdin.read()
with Path({str(calls)!r}).open("a") as fh:
    fh.write("call\\n")
print(json.dumps({{"projects": [{{"slug": "", "new_title": "Garden Shed",
    "entry": "Roof fixed.", "status": "Roof done; paint next."}}],
    "decisions": [], "open_items": []}}))
''',
        )
        (self.ws / "sessions" / "transcripts" / "2026-03-14_claude_aabbccdd.md").write_text(
            "### 22:00 owner\nFix the shed roof.\n\n### 22:00 claude\nRoof fixed.\n\n",
            encoding="utf-8",
        )
        bridge = SimpleNamespace(
            cfg={"ENGINE": "claude", "TIMEZONE": "UTC"}, workspace=self.ws, _running=False
        )
        sched = NightlyScheduler(bridge)  # type: ignore[arg-type]
        sched._run_summaries("2026-03-15")
        status = (self.ws / "projects" / "garden-shed" / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("Roof done", status)
        self.assertEqual(calls.read_text().count("call"), 1)
        sched._run_summaries("2026-03-15")  # unchanged file: skipped
        self.assertEqual(calls.read_text().count("call"), 1)

    def test_unknown_slug_without_title_ignored(self) -> None:
        from daftar import projects as projectsmod

        day = "2026-03-16"
        tpath = self.ws / "sessions" / "transcripts" / f"{day}_codex_11223344.md"
        tpath.write_text("### 11:00 owner\nhi\n\n### 11:00 codex\nhello\n\n", encoding="utf-8")

        def runner(_p: str) -> str:
            return json.dumps(
                {
                    "projects": [
                        {
                            "slug": "does-not-exist",
                            "new_title": "",
                            "entry": "should be ignored",
                            "status": "nope",
                        }
                    ],
                    "decisions": [],
                    "open_items": [],
                }
            )

        r = projectsmod.summarize_day(self.ws, day, runner=runner)
        self.assertEqual(r["entries"], 0)
        self.assertFalse((self.ws / "projects" / "does-not-exist").exists())

    # ── backup ────────────────────────────────────────────────────

    def test_backup_now_verify_prune_restore(self) -> None:
        from daftar import backup as backupmod
        from daftar.config import save_config

        save_config(
            {
                "TELEGRAM_BOT_TOKEN": "x",
                "OWNER_ID": "1",
                "WORKSPACE": str(self.ws),
                "BACKUP_KEEP": "2",
                "BACKUP_MAX_FILE_MB": "50",
            }
        )
        (self.ws / "note.txt").write_text("hello", encoding="utf-8")
        (self.home / "state.json").write_text('{"offset": 1}\n', encoding="utf-8")
        # Large inbox file should be excluded (limit 1 MB)
        big = self.ws / "inbox" / "huge.bin"
        big.write_bytes(b"x" * (2 * 1024 * 1024))
        # sent outbox should be excluded
        (self.ws / "outbox" / "sent" / "old.txt").write_text("gone", encoding="utf-8")

        report = backupmod.run_backup(
            {
                "BACKUP_KEEP": "2",
                "BACKUP_MAX_FILE_MB": "1",
                "BACKUP_INCLUDE_CONFIG": "no",
                "BACKUP_RCLONE_REMOTE": "",
                "WORKSPACE": str(self.ws),
            },
            home=self.home,
            workspace=self.ws,
        )
        self.assertTrue(report["verified"], msg=report)
        archive = Path(report["archive"])
        self.assertTrue(archive.exists())
        self.assertTrue(Path(str(archive) + ".sha256").exists())

        # Create more archives to exercise prune
        a2, _ = backupmod.create_archive(
            workspace=self.ws, home=self.home, stamp="20200101-000001"
        )
        a3, _ = backupmod.create_archive(
            workspace=self.ws, home=self.home, stamp="20200101-000002"
        )
        # Manually verify then prune with keep=2
        ok, _ = backupmod.verify_archive(a3)
        self.assertTrue(ok)
        removed = backupmod.prune_local(2, home=self.home)
        remaining = backupmod.list_archives(home=self.home)
        self.assertEqual(len(remaining), 2)
        self.assertTrue(removed)

        # Verification failure → no prune of the bad one via run_backup path:
        # simulate by breaking sha after create
        bad, sha = backupmod.create_archive(
            workspace=self.ws, home=self.home, stamp="20990101-999999"
        )
        sha.write_text("0" * 64 + f"  {bad.name}\n", encoding="utf-8")
        before = set(backupmod.list_archives(home=self.home))
        ok, msg = backupmod.verify_archive(bad, sha)
        self.assertFalse(ok)
        # Caller must not prune when verify fails — we don't call prune_local
        after = set(backupmod.list_archives(home=self.home))
        self.assertEqual(before, after)

        # restore refuses non-empty without --force
        dest = self.tmp / "restore_target"
        dest.mkdir()
        (dest / "existing.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            backupmod.restore_archive(archive, dest, force=False)
        dest2 = self.tmp / "restore_empty"
        backupmod.restore_archive(archive, dest2, force=False)
        self.assertTrue((dest2 / "workspace" / "note.txt").exists())
        # huge inbox excluded
        self.assertFalse((dest2 / "workspace" / "inbox" / "huge.bin").exists())

    def test_backup_remote_prune_after_verify(self) -> None:
        from daftar import backup as backupmod

        remote_store = self.tmp / "remote"
        remote_store.mkdir()
        log_path = self.tmp / "rclone.log"

        script = f'''
import json, sys, shutil
from pathlib import Path
store = Path({str(remote_store)!r})
log = Path({str(log_path)!r})
argv = sys.argv[1:]
log.write_text(log.read_text() + " ".join(argv) + "\\n" if log.exists() else " ".join(argv) + "\\n")
if argv[0] == "copyto":
    src, dest = Path(argv[1]), argv[2]
    name = dest.rsplit("/", 1)[-1]
    shutil.copy2(src, store / name)
    sys.exit(0)
if argv[0] == "lsjson":
    target = argv[1]
    name = target.rsplit("/", 1)[-1]
    if name == "gdrive:Daftar" or target.endswith("Daftar"):
        rows = []
        for p in sorted(store.glob("daftar-*.tar.gz")):
            rows.append({{"Path": p.name, "Name": p.name, "Size": p.stat().st_size, "IsDir": False}})
        print(json.dumps(rows))
        sys.exit(0)
    p = store / name
    if not p.exists():
        print("[]")
        sys.exit(0)
    print(json.dumps([{{"Path": name, "Name": name, "Size": p.stat().st_size, "IsDir": False}}]))
    sys.exit(0)
if argv[0] == "hashsum":
    algo, target = argv[1], argv[2]
    name = target.rsplit("/", 1)[-1]
    p = store / name
    import hashlib
    h = hashlib.sha256() if algo == "sha256" else hashlib.md5()
    h.update(p.read_bytes())
    print(h.hexdigest() + "  " + name)
    sys.exit(0)
if argv[0] == "deletefile":
    name = argv[1].rsplit("/", 1)[-1]
    (store / name).unlink(missing_ok=True)
    sys.exit(0)
print("unknown", argv, file=sys.stderr)
sys.exit(2)
'''
        self._write_script("rclone", script)

        (self.ws / "a.txt").write_text("data", encoding="utf-8")
        (self.home / "state.json").write_text("{}\n", encoding="utf-8")

        # Seed old remote archives
        for stamp in ("20200101-000001", "20200101-000002", "20200101-000003"):
            p = remote_store / f"daftar-{stamp}.tar.gz"
            p.write_bytes(b"old")
            (remote_store / (p.name + ".sha256")).write_text("x\n", encoding="utf-8")

        report = backupmod.run_backup(
            {
                "BACKUP_KEEP": "2",
                "BACKUP_MAX_FILE_MB": "50",
                "BACKUP_INCLUDE_CONFIG": "no",
                "BACKUP_RCLONE_REMOTE": "gdrive:Daftar",
                "WORKSPACE": str(self.ws),
            },
            home=self.home,
            workspace=self.ws,
        )
        self.assertTrue(report["verified"])
        self.assertTrue(report["remote_ok"], msg=report)
        self.assertTrue(report["pruned_remote"])
        # keep 2 tar.gz on remote
        rem_archives = sorted(remote_store.glob("daftar-*.tar.gz"))
        self.assertEqual(len(rem_archives), 2)
        # New archive present
        self.assertTrue(any("daftar-" in p.name for p in rem_archives))

        # Failed verify → no remote prune: poison hashsum
        # Recreate with a broken rclone that fails hash and size
        fail_script = f'''
import sys
from pathlib import Path
log = Path({str(log_path)!r})
argv = sys.argv[1:]
log.write_text((log.read_text() if log.exists() else "") + "FAIL " + " ".join(argv) + "\\n")
if argv[0] == "copyto":
    sys.exit(0)
if argv[0] == "lsjson":
    print('[{{"Path":"x","Size":1,"IsDir":false}}]')
    sys.exit(0)
if argv[0] == "hashsum":
    sys.exit(1)
if argv[0] == "deletefile":
    # must NOT be reached for prune after failed verify in run_backup
    Path({str(self.tmp / "deleted.flag")!r}).write_text("no")
    sys.exit(0)
sys.exit(0)
'''
        self._write_script("rclone", fail_script)
        before_flag = self.tmp / "deleted.flag"
        report2 = backupmod.run_backup(
            {
                "BACKUP_KEEP": "1",
                "BACKUP_MAX_FILE_MB": "50",
                "BACKUP_INCLUDE_CONFIG": "no",
                "BACKUP_RCLONE_REMOTE": "gdrive:Daftar",
                "WORKSPACE": str(self.ws),
            },
            home=self.home,
            workspace=self.ws,
        )
        # local verify still ok; remote fails
        self.assertTrue(report2["verified"])
        self.assertFalse(report2["remote_ok"])
        self.assertEqual(report2["pruned_remote"], [])
        self.assertFalse(before_flag.exists())

    # ── connectors ────────────────────────────────────────────────

    def test_connectors_add_remove_mask_secrets(self) -> None:
        from daftar import connectors as conn

        log = self.tmp / "mcp.log"
        body = f'''
import sys
from pathlib import Path
log = Path({str(log)!r})
argv = sys.argv[1:]
with log.open("a", encoding="utf-8") as fh:
    fh.write("ARGV:" + repr(argv) + "\\n")
if "mcp" in argv:
    if "list" in argv:
        print("my-api")
    else:
        print("ok")
    sys.exit(0)
print("unexpected", argv, file=sys.stderr)
sys.exit(2)
'''
        self._write_script("claude", body)
        self._write_script("codex", body)

        lines = conn.add_http(
            "my-api",
            "https://example.com/mcp",
            headers=["Authorization: Bearer SUPERSECRETTOKEN"],
        )
        joined = "\n".join(lines)
        self.assertIn("claude", joined)
        self.assertIn("codex", joined)
        self.assertNotIn("SUPERSECRETTOKEN", joined)
        self.assertIn("Authorization", joined)
        # Codex should warn about headers
        self.assertTrue(
            any("headers" in ln.lower() or "unsupported" in ln.lower() for ln in lines)
            or "warning" in joined.lower()
        )

        # Actual argv for claude includes -H with secret (logged to file)
        raw = log.read_text(encoding="utf-8")
        self.assertIn("SUPERSECRETTOKEN", raw)
        self.assertIn("mcp", raw)

        lines2 = conn.add_stdio(
            "local",
            "npx -y fake-server",
            env=["API_KEY=sekritvalue"],
        )
        joined2 = "\n".join(lines2)
        self.assertNotIn("sekritvalue", joined2)
        self.assertIn("API_KEY=", joined2)

        rem = "\n".join(conn.remove_connector("my-api"))
        self.assertIn("remove", rem.lower() or rem)
        self.assertIn("claude", rem)
        self.assertIn("codex", rem)

        listed = "\n".join(conn.list_connectors())
        self.assertIn("claude", listed)
        self.assertIn("codex", listed)


class BridgeTranscriptProjectsTest(unittest.TestCase):
    """Light bridge hooks: transcript after turn + /projects."""

    def setUp(self) -> None:
        from test_bridge import BridgeTestCase

        # Reuse BridgeTestCase harness by composition
        self.inner = BridgeTestCase()
        self.inner.setUp()
        self.addCleanup(self.inner.tearDown)

    def test_transcript_after_turn(self) -> None:
        bridge = self.inner._bridge()
        bridge._execute_turn("hello for transcript")
        tdir = self.inner.ws / "sessions" / "transcripts"
        files = list(tdir.glob("*.md"))
        self.assertTrue(files, "expected transcript file")
        body = files[0].read_text(encoding="utf-8")
        self.assertIn("owner", body)
        self.assertIn("hello for transcript", body)
        self.assertIn("claude", body.lower() or body)

    def test_projects_command(self) -> None:
        from daftar import projects as projectsmod

        bridge = self.inner._bridge()
        projectsmod.ensure_project(self.inner.ws, "alpha", title="Alpha")
        projectsmod.write_status(
            self.inner.ws, "alpha", "Ship next week.", title="Alpha", date="2026-03-15"
        )
        projectsmod.regenerate_index(self.inner.ws)
        bridge._send_projects(self.inner.owner_id)
        with self.inner.tg_state.lock:
            texts = [
                s.get("text", "")
                for s in self.inner.tg_state.sent
                if s.get("method") == "sendMessage"
            ]
        self.assertTrue(any("Alpha" in t for t in texts), msg=texts)


if __name__ == "__main__":
    unittest.main()
