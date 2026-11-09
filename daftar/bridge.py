"""Main Telegram ↔ engine bridge loop."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import jobs as jobsmod
from . import voice as voicemod
from .agents_md import ensure_agents_md
from .config import (
    active_profile,
    apply_to_environ,
    get_int,
    load_config,
    logs_dir,
    persona_path,
    profile_label,
    workspace_path,
)
from .engine import ENGINE_CODEX, make_engine
from .kb_match import format_injection, match_topics, strip_reminder_brackets, topic_bodies
from .scheduler import NightlyScheduler
from .sessions import (
    append_transcript,
    consume_pending_seed,
    list_for_ui,
    load_state,
    maybe_rotate_after_turn,
    save_state,
    start_fresh,
    switch_session,
    upsert_session,
    write_handoff_paper,
)
from .telegram import Telegram, is_image_path

log = logging.getLogger("daftar.bridge")

# Optional caption for an outbox file: "<file name>.caption" next to it.
CAPTION_SUFFIX = ".caption"

# Inbox file names come from Telegram (sender-controlled); keep them to a safe basename.
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\]")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_MAX_NAME_LEN = 120


def _safe_name(name: str, fallback: str) -> str:
    """Reduce an untrusted file name to a plain basename safe to create in inbox/.

    Drops any directory part (both / and \\), replaces NUL/control chars and
    separators, strips leading dots/whitespace (no hidden or ``..`` names) and
    truncates to ~120 chars while keeping a short extension. Returns
    ``fallback`` when nothing usable is left.
    """
    raw = str(name or "").replace("\\", "/")
    base = Path(raw).name
    base = _UNSAFE_NAME_CHARS.sub("_", base)
    base = base.strip().lstrip(". \t")
    if len(base) > _MAX_NAME_LEN:
        suffix = Path(base).suffix
        if not _SAFE_SUFFIX.match(suffix):
            suffix = ""
        base = base[: _MAX_NAME_LEN - len(suffix)].rstrip() + suffix
    if not base or base in (".", ".."):
        return fallback
    return base


def _safe_suffix(name: str, default: str) -> str:
    """Extension of ``name`` if it is short and alphanumeric, else ``default``."""
    suffix = Path(_safe_name(name, "")).suffix
    return suffix if _SAFE_SUFFIX.match(suffix) else default


def _unique_dest(inbox: Path, name: str, tag: str = "") -> Path:
    """``inbox/name``, or a non-existing variant (``stem-<tag>.ext`` / ``stem-N.ext``)."""
    dest = inbox / name
    if not dest.exists():
        return dest
    p = Path(name)
    stem, suffix = (p.stem, p.suffix) if _SAFE_SUFFIX.match(p.suffix) else (name, "")
    tag = re.sub(r"[^A-Za-z0-9_-]", "", tag or "")[:8]
    if tag:
        cand = inbox / f"{stem}-{tag}{suffix}"
        if not cand.exists():
            return cand
    for i in range(2, 10_000):
        cand = inbox / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
    return inbox / f"{stem}-{int(time.time() * 1000)}{suffix}"


COMMANDS = [
    {"command": "new", "description": "Start a fresh session (with handoff)"},
    {"command": "sessions", "description": "List / switch recent sessions"},
    {"command": "projects", "description": "List project status index"},
    {"command": "model", "description": "Show or set model"},
    {"command": "stop", "description": "Cancel the running turn"},
    {"command": "ping", "description": "Health check"},
    {"command": "help", "description": "Command help"},
]

HELP_TEXT = (
    "Daftar commands:\n"
    "/new — handoff paper + fresh session\n"
    "/sessions — switch among the last 4 sessions\n"
    "/projects — where projects stand (INDEX.md)\n"
    "/model [name] — show or set model (sonnet/opus/haiku/default)\n"
    "/stop — cancel the current turn\n"
    "/ping — health check\n"
    "/help — this message\n\n"
    "Send text, photos, documents, or voice notes as usual."
)


class Bridge:
    def __init__(self, cfg: Optional[dict[str, str]] = None) -> None:
        self.cfg = cfg or load_config()
        apply_to_environ(self.cfg)
        token = (self.cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing — run `daftar setup`")
        owner = (self.cfg.get("OWNER_ID") or "").strip()
        if not owner:
            raise RuntimeError("OWNER_ID missing — run `daftar setup`")
        self.owner_id = int(owner)
        self.workspace = workspace_path(self.cfg)
        self.profile = profile_label()
        self.tg = Telegram(
            token,
            api_base=self.cfg.get("TELEGRAM_API_BASE") or "https://api.telegram.org",
        )
        model = (self.cfg.get("MODEL") or "").strip()
        self.state = load_state()
        if self.state.model:
            model = self.state.model
        self.engine = make_engine(self.cfg, workspace=self.workspace, model=model)
        self._turn_lock = threading.Lock()
        self._running = False
        self._pending: list[str] = []
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._scheduler = NightlyScheduler(self)
        self._outbox_thread: Optional[threading.Thread] = None
        self._jobs_thread: Optional[threading.Thread] = None

    @property
    def is_codex(self) -> bool:
        return (self.cfg.get("ENGINE") or "").strip().lower() == ENGINE_CODEX or (
            getattr(self.engine, "name", "") == ENGINE_CODEX
        )

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        for sub in (
            "inbox",
            "outbox",
            "outbox/sent",
            "sessions",
            "sessions/transcripts",
            "projects",
            "knowledge",
            "delegations",
        ):
            (self.workspace / sub).mkdir(parents=True, exist_ok=True)
        jobsmod.ensure_jobs_dirs(active_profile())
        try:
            self.tg.set_my_commands(COMMANDS)
        except Exception:
            log.warning("setMyCommands failed", exc_info=True)
        self._outbox_thread = threading.Thread(
            target=self._outbox_loop, name="daftar-outbox", daemon=True
        )
        self._outbox_thread.start()
        self._jobs_thread = threading.Thread(
            target=self._jobs_loop, name="daftar-jobs", daemon=True
        )
        self._jobs_thread.start()
        self._scheduler.start()
        log.info(
            "bridge started profile=%s engine=%s owner=%s workspace=%s offset=%s",
            self.profile,
            getattr(self.engine, "name", "?"),
            self.owner_id,
            self.workspace,
            self.state.offset,
        )
        self._poll_loop()

    def stop(self) -> None:
        self._stop.set()
        self.engine.cancel()
        self._scheduler.stop()

    # ── polling ───────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(
                    self.state.offset or None, timeout=50
                )
            except Exception as e:
                log.warning("getUpdates error: %s", e)
                time.sleep(3)
                continue
            for upd in updates:
                uid = int(upd.get("update_id", 0))
                self.state.offset = uid + 1
                save_state(self.state)
                try:
                    self._dispatch(upd)
                except Exception:
                    log.exception("dispatch failed update_id=%s", uid)

    def _dispatch(self, upd: dict[str, Any]) -> None:
        if "callback_query" in upd:
            self._on_callback(upd["callback_query"])
            return
        msg = upd.get("message")
        if not msg:
            # Edits of already-answered messages must not re-run as a new turn.
            if "edited_message" in upd:
                log.debug("ignored edited_message update_id=%s", upd.get("update_id"))
            return
        from_user = msg.get("from") or {}
        uid = int(from_user.get("id") or 0)
        if uid != self.owner_id:
            log.info("ignored non-owner uid=%s", uid)
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/"):
            self._handle_command(msg, text)
            return
        pieces: list[str] = []
        # media
        if msg.get("photo"):
            path = self._save_photo(msg)
            if path:
                pieces.append(f"[photo saved at {path}]")
        if msg.get("document"):
            path = self._save_document(msg)
            if path:
                pieces.append(f"[document saved at {path}]")
        if msg.get("voice") or msg.get("audio"):
            transcript = self._transcribe_voice(msg)
            if transcript is None:
                return  # already replied with error
            pieces.append(transcript)
        elif text:
            pieces.append(text)
        elif msg.get("caption"):
            pieces.append(msg["caption"])
        if not pieces:
            return
        self._enqueue_user_text("\n".join(pieces))

    # ── commands ──────────────────────────────────────────────────

    def _handle_command(self, msg: dict, text: str) -> None:
        chat_id = int(msg["chat"]["id"])
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/ping":
            self.tg.send_message(chat_id, "pong")
            return
        if cmd == "/help":
            self.tg.send_message(chat_id, HELP_TEXT)
            return
        if cmd == "/stop":
            self.engine.cancel()
            self.tg.send_message(chat_id, "Cancelled.")
            return
        if cmd == "/model":
            if not arg:
                cur = self.engine.model or "(CLI default)"
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "sonnet", "callback_data": "model:sonnet"},
                            {"text": "opus", "callback_data": "model:opus"},
                        ],
                        [
                            {"text": "haiku", "callback_data": "model:haiku"},
                            {"text": "default", "callback_data": "model:"},
                        ],
                    ]
                }
                self.tg.send_message(
                    chat_id, f"Current model: {cur}", reply_markup=keyboard
                )
                return
            self._set_model(arg if arg != "default" else "")
            self.tg.send_message(
                chat_id, f"Model set to {self.engine.model or 'default'}."
            )
            return
        if cmd == "/sessions":
            self._send_sessions(chat_id)
            return
        if cmd == "/projects":
            self._send_projects(chat_id)
            return
        if cmd == "/new":
            self._cmd_new(chat_id)
            return
        # Unknown slash — treat as normal text
        self._enqueue_user_text(text)

    def _cmd_new(self, chat_id: int) -> None:
        sid = self.state.current_session_id
        if sid:
            try:
                path = write_handoff_paper(
                    self.engine,
                    sid,
                    summary_model=self.cfg.get("SUMMARY_MODEL") or "sonnet",
                    cfg=self.cfg,
                )
                upsert_session(self.state, sid, paper=str(path))
            except Exception:
                log.exception("handoff on /new failed")
        start_fresh(self.state)
        self._clear_kb_seen()
        self.tg.send_message(chat_id, "Fresh session started.")

    def _send_sessions(self, chat_id: int) -> None:
        rows = list_for_ui(self.state)
        if not rows:
            self.tg.send_message(chat_id, "No sessions yet.")
            return
        keyboard = []
        lines = ["Recent sessions:"]
        for rec in rows:
            mark = "•" if rec.id == self.state.current_session_id else "○"
            title = rec.title or rec.id[:8]
            lines.append(f"{mark} {title} (`{rec.id[:8]}`)")
            keyboard.append(
                [{"text": title[:40], "callback_data": f"session:{rec.id}"}]
            )
        self.tg.send_message(
            chat_id,
            "\n".join(lines),
            reply_markup={"inline_keyboard": keyboard},
        )

    def _send_projects(self, chat_id: int) -> None:
        from . import projects as projectsmod

        text = projectsmod.index_text(self.workspace)
        # Keep the Telegram reply short: drop the heading fluff
        lines = [
            ln
            for ln in text.splitlines()
            if ln.strip() and not ln.startswith("#") and not ln.startswith("One line")
        ]
        if not lines or lines == ["(no projects yet)"]:
            self.tg.send_message(chat_id, "No projects yet.")
            return
        body = "\n".join(lines[:40])
        if len(body) > 3500:
            body = body[:3490] + "…"
        self.tg.send_message(chat_id, body)

    def _set_model(self, name: str) -> None:
        self.engine.model = name
        self.state.model = name
        save_state(self.state)

    def _on_callback(self, cbq: dict) -> None:
        from_user = cbq.get("from") or {}
        if int(from_user.get("id") or 0) != self.owner_id:
            self.tg.answer_callback_query(cbq["id"], "Not authorized")
            return
        data = cbq.get("data") or ""
        chat = (cbq.get("message") or {}).get("chat") or {}
        chat_id = int(chat.get("id") or self.owner_id)
        self.tg.answer_callback_query(cbq["id"])
        if data.startswith("session:"):
            sid = data.split(":", 1)[1]
            if switch_session(self.state, sid):
                self._sync_kb_seen_session()
                self.tg.send_message(chat_id, f"Switched to session `{sid[:8]}`.")
            else:
                self.tg.send_message(chat_id, "Unknown session.")
            return
        if data.startswith("model:"):
            name = data.split(":", 1)[1]
            self._set_model(name)
            self.tg.send_message(
                chat_id, f"Model set to {self.engine.model or 'default'}."
            )

    # ── turn handling / interrupt ─────────────────────────────────

    def _enqueue_user_text(self, text: str) -> None:
        """Start a turn in the background, or interrupt the running one.

        Turns run off the polling thread so new messages (and /stop) are seen
        while the engine works; a message that arrives mid-turn cancels it and is
        merged with any other pending messages into the next turn.
        """
        with self._pending_lock:
            if self._running:
                self._pending.append(text)
                self.engine.cancel()
                log.info("interrupt: queued message (%d pending)", len(self._pending))
                return
            self._running = True
        threading.Thread(target=self._run_turn, args=(text,), name="daftar-turn", daemon=True).start()

    def _run_turn(self, text: str) -> None:
        with self._turn_lock:
            self._running = True
            try:
                while True:
                    self._execute_turn(text)
                    # Drain messages that arrived mid-turn; flip _running under the
                    # same lock so nothing slips in between "empty" and "idle".
                    with self._pending_lock:
                        if not self._pending:
                            self._running = False
                            return
                        text = "\n\n".join(self._pending)
                        self._pending.clear()
            except Exception:
                log.exception("turn failed")
                with self._pending_lock:
                    self._running = False
                try:
                    self.tg.send_message(self.owner_id, "Something went wrong running that turn. Check `daftar doctor` and the logs.")
                except Exception:
                    pass

    def _reminder(self) -> str:
        rem = (self.cfg.get("REMINDER") or "Reply in English. Be concise.").strip()
        return f"⟨{rem}⟩"

    def _clear_kb_seen(self) -> None:
        self.state.extra["kb_seen"] = []
        self.state.extra["kb_seen_for"] = self.state.current_session_id
        save_state(self.state)

    def _sync_kb_seen_session(self) -> None:
        sid = self.state.current_session_id
        if self.state.extra.get("kb_seen_for") != sid:
            self.state.extra["kb_seen"] = []
            self.state.extra["kb_seen_for"] = sid
            save_state(self.state)

    def _codex_topic_prefix(self, user_text: str) -> str:
        """Inject matched topic files once per session for Codex turns."""
        self._sync_kb_seen_session()
        seen = set(self.state.extra.get("kb_seen") or [])
        kb = self.workspace / "knowledge"
        hay = strip_reminder_brackets(user_text).lower()
        hits = match_topics(kb, hay, "keywords", seen)
        if not hits:
            return ""
        bodies = topic_bodies(kb, hits)
        if not bodies:
            return ""
        for slug, _ in bodies:
            seen.add(slug)
        self.state.extra["kb_seen"] = sorted(seen)
        self.state.extra["kb_seen_for"] = self.state.current_session_id
        save_state(self.state)
        return format_injection(bodies) + "\n\n"

    def _prepare_codex_workspace(self) -> None:
        ensure_agents_md(
            self.workspace,
            persona_file=persona_path(self.cfg, self.workspace),
            cfg=self.cfg,
        )
        # Keep CLAUDE.md persona import pointed at this bridge's file when shared
        self._sync_claude_persona_import()

    def _sync_claude_persona_import(self) -> None:
        rel = (self.cfg.get("PERSONA_FILE") or "persona/PERSONA.md").strip()
        claude_md = self.workspace / "CLAUDE.md"
        if not claude_md.exists():
            return
        try:
            text = claude_md.read_text(encoding="utf-8")
        except OSError:
            return
        import re

        new = re.sub(
            r"@persona/\S+",
            f"@{rel}",
            text,
        )
        if new != text:
            claude_md.write_text(new, encoding="utf-8")

    def _execute_turn(self, user_text: str, *, deliver: bool = True) -> None:
        chat_id = self.owner_id
        seed = consume_pending_seed(self.state)
        prompt = user_text
        if seed:
            prompt = seed + "\n\n" + user_text

        if self.is_codex:
            self._prepare_codex_workspace()
            prefix = self._codex_topic_prefix(user_text)
            if prefix:
                prompt = prefix + prompt
        else:
            self._sync_claude_persona_import()

        typing_stop = threading.Event()

        def _typing() -> None:
            while not typing_stop.wait(4.0):
                self.tg.send_chat_action(chat_id, "typing")

        t = threading.Thread(target=_typing, daemon=True)
        if deliver:
            self.tg.send_chat_action(chat_id, "typing")
            t.start()
        try:
            result = self.engine.run(
                prompt,
                resume=self.state.current_session_id,
                append_system_prompt=self._reminder(),
            )
        finally:
            typing_stop.set()
            if deliver:
                t.join(timeout=1)

        if result.cancelled and not result.text:
            # Interrupted — pending messages will be drained by _run_turn
            return

        if result.session_id:
            prev = self.state.current_session_id
            upsert_session(
                self.state,
                result.session_id,
                user_text=user_text,
                input_tokens=result.context_tokens,
            )
            self.state.current_session_id = result.session_id
            if prev != result.session_id and not prev:
                # First sid of a fresh session — keep kb_seen accumulated this turn
                self.state.extra["kb_seen_for"] = result.session_id
            save_state(self.state)

        if not deliver:
            return

        if result.is_error and result.error and not result.text:
            self.tg.send_message(chat_id, result.error)
            return

        reply = (result.text or "").strip()
        if not reply:
            self.tg.send_message(
                chat_id, "(No text in the reply — the turn finished empty.)"
            )
        else:
            self.tg.send_html(chat_id, reply)

        # Append-only transcript for nightly project summaries
        try:
            sid = result.session_id or self.state.current_session_id or "unknown"
            eng = getattr(self.engine, "name", None) or (
                self.cfg.get("ENGINE") or "claude"
            )
            append_transcript(
                self.workspace,
                engine=str(eng),
                session_id=sid,
                user_text=user_text,
                reply_text=reply or "(empty)",
                timezone_name=self.cfg.get("TIMEZONE") or "UTC",
            )
        except Exception:
            log.exception("transcript append failed")

        # Codex built-in image tool → send new files as photos
        for img in result.images or []:
            try:
                if Path(img).is_file():
                    self.tg.send_photo(chat_id, Path(img))
            except Exception:
                log.exception("send codex image failed: %s", img)

        # Silent rotation after delivery
        try:
            rotated = maybe_rotate_after_turn(
                self.state,
                self.engine,
                result,
                rotate_tokens=get_int(self.cfg, "ROTATE_TOKENS", 120000),
                summary_model=self.cfg.get("SUMMARY_MODEL") or "sonnet",
                cfg=self.cfg,
            )
            if rotated:
                self._clear_kb_seen()
        except Exception:
            log.exception("rotation failed")

    # ── jobs (delegation) ─────────────────────────────────────────

    def _jobs_loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                if not self._running:
                    self._process_one_job()
                self._poll_job_results()
            except Exception:
                log.exception("jobs loop error")

    def _process_one_job(self) -> None:
        with self._pending_lock:
            if self._running:
                return
            self._running = True
        try:
            job = jobsmod.claim_next(active_profile())
            if not job:
                return
            log.info("running delegated job %s", job.get("id", "")[:8])
            try:
                status, result_dir, summary = jobsmod.run_job_in_workspace(
                    job,
                    workspace=self.workspace,
                    engine=self.engine,
                )
                jobsmod.finish_job(
                    job,
                    status=status,
                    result_dir=result_dir,
                    summary=summary if status == "ok" else "",
                    error=summary if status == "failed" else "",
                )
            except Exception as e:
                log.exception("job failed")
                work = self.workspace / "delegations" / job["id"]
                work.mkdir(parents=True, exist_ok=True)
                jobsmod.finish_job(
                    job,
                    status="failed",
                    result_dir=str(work),
                    error=str(e)[:500],
                )
        finally:
            with self._pending_lock:
                self._running = False

    def _poll_job_results(self) -> None:
        pending = jobsmod.list_pending_results(active_profile())
        for result in pending:
            # Don't interleave with an owner turn
            if self._running:
                return
            prompt = jobsmod.review_prompt(result)
            log.info("reviewing delegated result %s", result.get("id", "")[:8])
            with self._pending_lock:
                if self._running:
                    return
                self._running = True
            try:
                with self._turn_lock:
                    self._execute_turn(prompt, deliver=True)
            finally:
                with self._pending_lock:
                    self._running = False
            jobsmod.consume_result(active_profile(), result["id"])

    # ── media helpers ─────────────────────────────────────────────

    def _inbox(self) -> Path:
        d = self.workspace / "inbox"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_photo(self, msg: dict) -> Optional[Path]:
        photos = msg.get("photo") or []
        if not photos:
            return None
        best = photos[-1]
        dest = self._inbox() / _safe_name(f"photo_{best['file_id'][:16]}.jpg", "photo.jpg")
        try:
            self.tg.download_file(best["file_id"], dest)
            return dest.resolve()
        except Exception:
            log.exception("photo download failed")
            return None

    def _save_document(self, msg: dict) -> Optional[Path]:
        doc = msg.get("document") or {}
        fallback = _safe_name(f"doc_{str(doc.get('file_id') or 'x')[:16]}", "document")
        name = _safe_name(doc.get("file_name") or "", fallback)
        inbox = self._inbox()
        tag = str(doc.get("file_unique_id") or doc.get("file_id") or "")
        dest = _unique_dest(inbox, name, tag)
        if dest.resolve().parent != inbox.resolve():
            log.warning("document name %r escapes inbox; using %s", doc.get("file_name"), fallback)
            dest = _unique_dest(inbox, fallback, tag)
        try:
            self.tg.download_file(doc["file_id"], dest)
            return dest.resolve()
        except Exception:
            log.exception("document download failed")
            return None

    def _transcribe_voice(self, msg: dict) -> Optional[str]:
        media = msg.get("voice") or msg.get("audio") or {}
        file_id = media.get("file_id")
        if not file_id:
            return None
        ext = ".ogg" if msg.get("voice") else _safe_suffix(media.get("file_name") or "", ".ogg")
        dest = self._inbox() / _safe_name(f"voice_{file_id[:16]}{ext}", f"voice{ext}")
        try:
            self.tg.download_file(file_id, dest)
        except Exception:
            log.exception("voice download failed")
            self.tg.send_message(self.owner_id, "Could not download the voice note.")
            return None
        text, reason = voicemod.transcribe(
            dest, language=self.cfg.get("TRANSCRIBE_LANGUAGE") or ""
        )
        if not text:
            self.tg.send_message(self.owner_id, voicemod.failure_message(reason))
            return None
        return text

    # ── outbox ────────────────────────────────────────────────────

    def _outbox_loop(self) -> None:
        while not self._stop.wait(3.0):
            try:
                self.deliver_outbox()
            except Exception:
                log.exception("outbox loop error")

    def deliver_outbox(self, once_path: Optional[Path] = None) -> int:
        """Send files from outbox/ (or a single path). Returns count sent."""
        outbox = self.workspace / "outbox"
        sent_dir = outbox / "sent"
        sent_dir.mkdir(parents=True, exist_ok=True)
        if once_path is not None:
            paths = [Path(once_path)]
        else:
            paths = sorted(
                p
                for p in outbox.iterdir()
                if p.is_file() and not p.name.startswith(".") and not p.name.endswith(CAPTION_SUFFIX)
            )
        n = 0
        for path in paths:
            if not path.exists():
                continue
            cap_file = path.parent / (path.name + CAPTION_SUFFIX)
            caption = cap_file.read_text(encoding="utf-8").strip()[:1024] if cap_file.exists() else ""
            try:
                if is_image_path(path):
                    self.tg.send_photo(self.owner_id, path, caption=caption)
                else:
                    self.tg.send_document(self.owner_id, path, caption=caption)
                dest = sent_dir / path.name
                if dest.exists():
                    dest = sent_dir / f"{int(time.time())}_{path.name}"
                shutil.move(str(path), str(dest))
                if cap_file.exists():
                    cap_file.unlink(missing_ok=True)
                n += 1
            except Exception:
                log.exception("outbox send failed for %s", path)
        return n


def run_bridge(cfg: Optional[dict[str, str]] = None) -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(logs_dir() / "bridge.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    bridge = Bridge(cfg)
    try:
        bridge.start()
    except KeyboardInterrupt:
        bridge.stop()
