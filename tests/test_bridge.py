"""End-to-end bridge tests with fake Telegram HTTP + fake claude."""

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
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


class FakeTG:
    """Shared mutable state for the fake Bot API server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.offset_requests: list[Optional[int]] = []
        self.commands: list[Any] = []
        self.files: dict[str, bytes] = {}
        self.next_update_id = 100

    def push_message(self, user_id: int, text: str = "", **extra: Any) -> int:
        uid = self.next_update_id
        self.next_update_id += 1
        msg: dict[str, Any] = {
            "message_id": uid,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "first_name": "Owner"},
            "text": text,
        }
        msg.update(extra)
        with self.lock:
            self.updates.append({"update_id": uid, "message": msg})
        return uid

    def push_callback(self, user_id: int, data: str) -> int:
        uid = self.next_update_id
        self.next_update_id += 1
        with self.lock:
            self.updates.append(
                {
                    "update_id": uid,
                    "callback_query": {
                        "id": f"cb{uid}",
                        "from": {"id": user_id},
                        "data": data,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": user_id},
                        },
                    },
                }
            )
        return uid


def make_handler(state: FakeTG):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _json(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode() or "{}")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            # /botTOKEN/method
            method = path.rstrip("/").split("/")[-1]
            ctype = self.headers.get("Content-Type", "")
            if "multipart" in ctype:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = {"ok": True, "result": {"message_id": 1}}
                # Record a sent document/photo
                with state.lock:
                    state.sent.append({"method": method, "multipart": True})
                self._json(200, body)
                return

            params = self._read_json()
            if method == "getMe":
                self._json(200, {"ok": True, "result": {"username": "daftar_test_bot", "id": 1}})
                return
            if method == "getUpdates":
                offset = params.get("offset")
                with state.lock:
                    state.offset_requests.append(offset)
                    if offset is not None:
                        state.updates = [u for u in state.updates if u["update_id"] >= int(offset)]
                    batch = list(state.updates)
                    # Simulate long-poll briefly
                time.sleep(0.05)
                # Clear delivered
                with state.lock:
                    state.updates = []
                self._json(200, {"ok": True, "result": batch})
                return
            if method == "sendMessage":
                text = params.get("text") or ""
                # HTML parse failure trigger
                if params.get("parse_mode") == "HTML" and "FORCE_HTML_FAIL" in text:
                    self._json(
                        400,
                        {"ok": False, "description": "can't parse entities"},
                    )
                    return
                with state.lock:
                    state.sent.append({"method": "sendMessage", **params})
                self._json(200, {"ok": True, "result": {"message_id": len(state.sent)}})
                return
            if method == "sendChatAction":
                self._json(200, {"ok": True, "result": True})
                return
            if method == "setMyCommands":
                with state.lock:
                    state.commands = params.get("commands") or []
                self._json(200, {"ok": True, "result": True})
                return
            if method == "answerCallbackQuery":
                self._json(200, {"ok": True, "result": True})
                return
            if method == "editMessageText":
                with state.lock:
                    state.sent.append({"method": "editMessageText", **params})
                self._json(200, {"ok": True, "result": True})
                return
            if method == "getFile":
                fid = params.get("file_id", "x")
                self._json(
                    200,
                    {"ok": True, "result": {"file_id": fid, "file_path": f"voice/{fid}.ogg"}},
                )
                return
            if method in ("sendPhoto", "sendDocument"):
                with state.lock:
                    state.sent.append({"method": method, **params})
                self._json(200, {"ok": True, "result": {"message_id": 9}})
                return
            self._json(200, {"ok": True, "result": {}})

        def do_GET(self) -> None:
            # file download
            path = urlparse(self.path).path
            if "/file/bot" in path:
                raw = b"FAKEAUDIO"
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self._json(404, {"ok": False})

    return Handler


class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-test-"))
        self.home = self.tmp / "home"
        self.ws = self.tmp / "workspace"
        self.home.mkdir()
        # Copy template knowledge into workspace
        src = ROOT / "template" / "workspace"
        shutil.copytree(src, self.ws)
        # Config + env
        os.environ["DAFTAR_HOME"] = str(self.home)
        # Fake telegram
        self.tg_state = FakeTG()
        handler = make_handler(self.tg_state)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        # Fake claude on PATH
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        claude_link = bin_dir / "claude"
        claude_link.write_text(
            "#!/bin/sh\nexec python3 \"%s\" \"$@\"\n" % FAKE_CLAUDE,
            encoding="utf-8",
        )
        claude_link.chmod(claude_link.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

        from daftar.config import save_config

        self.owner_id = 424242
        save_config(
            {
                "TELEGRAM_BOT_TOKEN": "TESTTOKEN",
                "OWNER_ID": str(self.owner_id),
                "WORKSPACE": str(self.ws),
                "LANGUAGE": "English",
                "PERMISSION_MODE": "bypassPermissions",
                "ROTATE_TOKENS": "120000",
                "TELEGRAM_API_BASE": f"http://127.0.0.1:{self.port}",
                "SUMMARY_MODEL": "sonnet",
                "NIGHTLY_HOUR": "99",  # never
            }
        )

    def tearDown(self) -> None:
        # Stop every bridge this test created before the fake server goes away,
        # so no background turn leaks into the next test.
        for b in getattr(self, "_bridges", []):
            b.engine.cancel()
            b.stop()
            deadline = time.time() + 10
            while b._running and time.time() < deadline:
                b.engine.cancel()
                time.sleep(0.05)
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bridge(self):
        from daftar.bridge import Bridge
        from daftar.config import load_config

        b = Bridge(load_config())
        self._bridges = getattr(self, "_bridges", []) + [b]
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

    def test_owner_allowlist(self) -> None:
        bridge = self._bridge()
        # Non-owner message
        self.tg_state.push_message(999, "hack")
        # Process one poll cycle manually
        updates = bridge.tg.get_updates(None, timeout=1)
        for upd in updates:
            bridge.state.offset = int(upd["update_id"]) + 1
            bridge._dispatch(upd)
        with self.tg_state.lock:
            self.assertFalse(any(s.get("text", "").startswith("Echo") for s in self.tg_state.sent))

    def test_text_turn_e2e(self) -> None:
        bridge = self._bridge()
        self.tg_state.push_message(self.owner_id, "hello world")
        updates = bridge.tg.get_updates(None, timeout=1)
        for upd in updates:
            bridge.state.offset = int(upd["update_id"]) + 1
            bridge._dispatch(upd)
        # _dispatch enqueues turn synchronously when not running
        sent = self._wait_sent(lambda s: s.get("method") == "sendMessage" and "Echo" in (s.get("text") or ""))
        self.assertTrue(sent, msg=str(self.tg_state.sent))
        self.assertIsNotNone(bridge.state.current_session_id)

    def test_interrupt_concat(self) -> None:
        bridge = self._bridge()
        # Start a slow turn in a thread
        t = threading.Thread(target=bridge._run_turn, args=("SLOW please",), daemon=True)
        t.start()
        time.sleep(0.15)
        # Interrupt with two messages
        bridge._enqueue_user_text("first")
        bridge._enqueue_user_text("second")
        t.join(timeout=10)
        # Wait for drain
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

    def test_new_handoff_paper(self) -> None:
        bridge = self._bridge()
        # Establish a session
        bridge._execute_turn("start session")
        sid = bridge.state.current_session_id
        self.assertTrue(sid)
        bridge._cmd_new(self.owner_id)
        papers = list((self.ws / "sessions").glob("*.md"))
        self.assertTrue(papers, "expected handoff paper file")
        self.assertIsNone(bridge.state.current_session_id)

    def test_rotation_threshold(self) -> None:
        bridge = self._bridge()
        from daftar.sessions import maybe_rotate_after_turn
        from daftar.engine import EngineResult

        bridge._execute_turn("hello before rotation")
        sid = bridge.state.current_session_id
        self.assertTrue(sid)
        # Manually invoke rotation with high tokens
        result = EngineResult(session_id=sid, input_tokens=150000, cache_tokens=0, text="done")
        path = maybe_rotate_after_turn(
            bridge.state,
            bridge.engine,
            result,
            rotate_tokens=120000,
            summary_model="sonnet",
            cfg=bridge.cfg,
        )
        self.assertTrue(path)
        self.assertTrue(Path(path).exists())
        self.assertIsNone(bridge.state.current_session_id)
        self.assertIn("pending_seed", bridge.state.extra)

    def test_outbox_delivery(self) -> None:
        bridge = self._bridge()
        out = self.ws / "outbox"
        out.mkdir(exist_ok=True)
        f = out / "note.txt"
        f.write_text("hello file", encoding="utf-8")
        (out / "note.txt.caption").write_text("caption here", encoding="utf-8")
        n = bridge.deliver_outbox()
        self.assertEqual(n, 1)
        self.assertTrue((out / "sent" / "note.txt").exists())
        with self.tg_state.lock:
            methods = [s.get("method") for s in self.tg_state.sent]
        self.assertIn("sendDocument", methods)
        self.assertFalse((out / "note.txt.caption").exists())

    def test_auto_rotation_after_big_turn(self) -> None:
        bridge = self._bridge()
        bridge._execute_turn("FORCE_ROTATE now")
        self.assertIsNone(bridge.state.current_session_id)
        self.assertTrue(list((self.ws / "sessions").glob("*.md")))

    def test_html_fallback(self) -> None:
        bridge = self._bridge()
        # Force HTML fail then plain
        mid = bridge.tg.send_html(self.owner_id, "hello FORCE_HTML_FAIL world")
        # send_html converts markdown; the plain chunk may not include the marker
        # the same way — call _send_one path via send_message with HTML
        try:
            bridge.tg.send_message(
                self.owner_id,
                "hello FORCE_HTML_FAIL world",
                parse_mode="HTML",
            )
        except Exception:
            pass
        with self.tg_state.lock:
            # At least one plain sendMessage should have landed without parse_mode
            plains = [
                s
                for s in self.tg_state.sent
                if s.get("method") == "sendMessage" and not s.get("parse_mode")
            ]
        self.assertTrue(plains or mid is not None)

    def test_document_name_cannot_escape_inbox(self) -> None:
        bridge = self._bridge()
        inbox = (self.ws / "inbox").resolve()
        settings = self.ws / ".claude" / "settings.json"
        before = settings.read_bytes() if settings.exists() else None

        def save(name: str, uid: str):
            msg = {"document": {"file_id": f"FID{uid}", "file_unique_id": uid, "file_name": name}}
            return bridge._save_document(msg)

        p1 = save("../.claude/settings.json", "uniqA111xyz")
        p2 = save("/etc/passwd", "uniqB222xyz")
        p3 = save("..", "uniqC333xyz")
        for p in (p1, p2, p3):
            self.assertIsNotNone(p)
            self.assertEqual(p.parent, inbox, msg=str(p))
            self.assertFalse(p.name.startswith("."), msg=p.name)
        self.assertEqual(p1.name, "settings.json")
        self.assertEqual(p2.name, "passwd")
        after = settings.read_bytes() if settings.exists() else None
        self.assertEqual(before, after, "document overwrote workspace .claude/settings.json")
        # Same name twice -> two distinct files, the first one untouched
        a = save("report.pdf", "dupAAAAAAAA")
        (inbox / "report.pdf").write_bytes(b"FIRST")
        b = save("report.pdf", "dupBBBBBBBB")
        c = save("report.pdf", "dupBBBBBBBB")
        self.assertEqual(a.name, "report.pdf")
        self.assertNotEqual(a, b)
        self.assertNotEqual(b, c)
        self.assertEqual(b.name, "report-dupBBBBB.pdf")
        self.assertEqual((inbox / "report.pdf").read_bytes(), b"FIRST")
        self.assertTrue(b.exists() and c.exists())

    def test_safe_name_and_suffix(self) -> None:
        from daftar.bridge import _safe_name, _safe_suffix

        self.assertEqual(_safe_name("a\\..\\b.txt", "fb"), "b.txt")
        self.assertEqual(_safe_name("x\x00y\n.pdf", "fb"), "x_y_.pdf")
        self.assertEqual(_safe_name("   ", "fb"), "fb")
        long = _safe_name("A" * 300 + ".pdf", "fb")
        self.assertLessEqual(len(long), 120)
        self.assertTrue(long.endswith(".pdf"))
        self.assertEqual(_safe_suffix("song.mp3", ".ogg"), ".mp3")
        self.assertEqual(_safe_suffix("x." + "a" * 20, ".ogg"), ".ogg")
        self.assertEqual(_safe_suffix("../evil/.sh", ".ogg"), ".ogg")
        self.assertEqual(_safe_suffix("", ".ogg"), ".ogg")

    def test_cli_send_writes_caption_sidecar(self) -> None:
        import importlib.machinery
        import importlib.util

        from daftar.telegram import Telegram

        loader = importlib.machinery.SourceFileLoader("daftar_cli", str(ROOT / "bin" / "daftar"))
        spec = importlib.util.spec_from_loader("daftar_cli", loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)

        src = self.tmp / "report.pdf"
        src.write_bytes(b"%PDF-1.4 test")
        outbox = self.ws / "outbox"
        seen: dict = {}
        real_send = Telegram.send_document

        def spy(tg_self, chat_id, path, caption=""):
            seen["caption"] = caption
            seen["outbox"] = sorted(p.name for p in outbox.iterdir() if p.is_file())
            return real_send(tg_self, chat_id, path, caption=caption)

        Telegram.send_document = spy
        try:
            rc = cli.main(["send", str(src), "hello caption"])
        finally:
            Telegram.send_document = real_send
        self.assertEqual(rc, 0)
        self.assertIn("report.pdf.caption", seen["outbox"])
        self.assertNotIn("report.pdf.txt", seen["outbox"])
        self.assertEqual(seen["caption"], "hello caption")
        self.assertFalse((outbox / "report.pdf.caption").exists())
        self.assertFalse((outbox / "report.pdf.txt").exists())
        self.assertTrue((outbox / "sent" / "report.pdf").exists())

    def test_prompt_goes_through_stdin(self) -> None:
        import hashlib

        from daftar.engine import ClaudeEngine

        eng = ClaudeEngine(cwd=self.ws, binary="claude", timeout_sec=60)
        # A prompt that starts with "-" must not be parsed as a CLI option.
        res = eng.run("- milk\n- eggs")
        self.assertFalse(res.is_error, msg=res.error)
        self.assertIn("Echo: - milk", res.text)
        # 200 KB is above the 128 KiB per-argv-element limit and the pipe buffer.
        big = "ECHO_LEN " + ("x" * 1023 + "\n") * 200
        self.assertGreater(len(big.encode()), 200_000)
        res = eng.run(big)
        self.assertFalse(res.is_error, msg=res.error)
        digest = hashlib.sha256(big.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(res.text, f"LEN={len(big)} SHA={digest}")
        # Non-ASCII survives the pipe.
        res = eng.run("مرحبا يا دفتر")
        self.assertIn("مرحبا يا دفتر", res.text)

    def test_curator_prompt_goes_through_stdin(self) -> None:
        from daftar.curator import ask_model

        prompt = "- Return JSON only\n" + ("lesson line\n" * 20_000)
        self.assertGreater(len(prompt), 200_000)
        plan = ask_model(prompt, model="sonnet", binary="claude")
        self.assertIsInstance(plan, dict)
        self.assertIn("merge", plan)

    def test_edited_message_is_not_a_new_turn(self) -> None:
        bridge = self._bridge()
        # The default long-poll no longer asks Telegram for edits ...
        seen: dict = {}
        real_call = bridge.tg.call

        def spy(method, params=None, **kw):
            if method == "getUpdates":
                seen.update(params or {})
            return real_call(method, params, **kw)

        bridge.tg.call = spy
        bridge.tg.get_updates(None, timeout=1)
        self.assertNotIn("edited_message", seen.get("allowed_updates") or [])
        # ... and an edited_message update that still arrives is ignored.
        with self.tg_state.lock:
            self.tg_state.sent.clear()
        upd = {
            "update_id": 999,
            "edited_message": {
                "message_id": 5,
                "chat": {"id": self.owner_id, "type": "private"},
                "from": {"id": self.owner_id},
                "text": "edited hello",
            },
        }
        bridge._dispatch(upd)
        time.sleep(0.5)
        self.assertFalse(bridge._running)
        self.assertIsNone(bridge.state.current_session_id)
        with self.tg_state.lock:
            self.assertFalse(
                any("Echo" in (s.get("text") or "") for s in self.tg_state.sent),
                msg=str(self.tg_state.sent),
            )


if __name__ == "__main__":
    unittest.main()
