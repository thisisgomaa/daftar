"""OpenAI Codex CLI subprocess engine (`codex exec --json`)."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .engine import (
    ENGINE_CODEX,
    BaseEngine,
    EngineResult,
    kill_process_group,
    prepend_repo_bin,
)

log = logging.getLogger("daftar.engine_codex")

CODEX_AUTH_MARKERS = (
    "not logged in",
    "please run `codex login`",
    "please run codex login",
    "authentication required",
    "unauthorized",
    "login required",
    "not authenticated",
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SKIP_SUFFIXES = {".part", ".tmp"}


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def generated_images_root() -> Path:
    return codex_home() / "generated_images"


def snapshot_image_files(root: Optional[Path] = None) -> set[Path]:
    """Return resolved paths of existing generated image files."""
    base = root or generated_images_root()
    found: set[Path] = set()
    if not base.exists():
        return found
    try:
        for p in base.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            suf = p.suffix.lower()
            if suf in SKIP_SUFFIXES or suf not in IMAGE_SUFFIXES:
                continue
            try:
                found.add(p.resolve())
            except OSError:
                continue
    except OSError:
        pass
    return found


def new_image_files(before: set[Path], root: Optional[Path] = None) -> list[Path]:
    after = snapshot_image_files(root)
    return sorted(after - before)


def sandbox_flags(permission_mode: str) -> list[str]:
    if (permission_mode or "").strip() == "bypassPermissions":
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return ["-s", "workspace-write"]


def _extract_session_id(event: dict[str, Any]) -> Optional[str]:
    """Defensively pull thread/session id from any Codex JSONL event."""
    for key in ("thread_id", "session_id", "conversation_id"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for nest_key in ("thread", "session", "conversation", "item"):
        nest = event.get(nest_key)
        if isinstance(nest, dict):
            for key in ("thread_id", "session_id", "conversation_id", "id"):
                val = nest.get(key)
                if isinstance(val, str) and val.strip():
                    # Prefer ids that look like session uuids over short item ids
                    if key == "id" and nest_key == "item" and len(val) < 20:
                        continue
                    return val.strip()
    return None


def _extract_agent_text(event: dict[str, Any]) -> Optional[str]:
    """Pull assistant text from agent_message-style events."""
    etype = str(event.get("type") or "")
    # Direct text fields
    if etype in ("agent_message", "message", "assistant_message"):
        t = event.get("text") or event.get("message") or event.get("content")
        if isinstance(t, str) and t.strip():
            return t.strip()
    item = event.get("item")
    if isinstance(item, dict):
        itype = str(item.get("type") or "")
        if itype in ("agent_message", "message", "assistant_message"):
            t = item.get("text") or item.get("content")
            if isinstance(t, str) and t.strip():
                return t.strip()
    # Nested message.content blocks
    msg = event.get("message") if isinstance(event.get("message"), dict) else None
    if msg:
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("text", "output_text"):
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            joined = "".join(parts).strip()
            if joined:
                return joined
    return None


def _extract_usage(event: dict[str, Any]) -> dict[str, Any]:
    etype = str(event.get("type") or "")
    if etype not in ("turn.completed", "token_count", "usage"):
        # Still accept nested usage on any event
        pass
    for key in ("usage", "token_count", "tokens"):
        u = event.get(key)
        if isinstance(u, dict) and u:
            return u
    return {}


class CodexEngine(BaseEngine):
    """Run the official `codex` CLI (`codex exec --json`)."""

    name = ENGINE_CODEX

    def __init__(
        self,
        *,
        cwd: Path | str,
        model: str = "",
        permission_mode: str = "bypassPermissions",
        timeout_sec: int = 2700,
        binary: str = "codex",
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.cwd = Path(cwd)
        self.model = model
        self.permission_mode = permission_mode
        self.timeout_sec = timeout_sec
        self.binary = binary
        self.env = env
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._cancelled = False

    def run(
        self,
        prompt: str,
        *,
        resume: Optional[str] = None,
        append_system_prompt: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> EngineResult:
        self._cancelled = False
        full_prompt = prompt
        if append_system_prompt:
            full_prompt = prompt.rstrip() + "\n" + append_system_prompt.strip()

        last_msg = tempfile.NamedTemporaryFile(
            prefix="daftar-codex-", suffix=".txt", delete=False
        )
        last_msg_path = Path(last_msg.name)
        last_msg.close()

        flags = [
            "--json",
            "--skip-git-repo-check",
            *sandbox_flags(self.permission_mode),
            "-o",
            str(last_msg_path),
        ]
        if self.model:
            flags.extend(["-m", self.model])
        if extra_args:
            flags.extend(extra_args)

        if resume:
            # resume does not accept -C; cwd is set via subprocess
            cmd = [self.binary, "exec", "resume", resume, full_prompt, *flags]
        else:
            cmd = [
                self.binary,
                "exec",
                *flags,
                "-C",
                str(self.cwd),
                full_prompt,
            ]

        env = prepend_repo_bin(os.environ.copy())
        if self.env:
            env.update({k: v for k, v in self.env.items() if v})

        before_images = snapshot_image_files()
        log.info("codex spawn cwd=%s resume=%s model=%s", self.cwd, resume, self.model or "default")
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError:
            last_msg_path.unlink(missing_ok=True)
            return EngineResult(
                is_error=True,
                engine=self.name,
                error=(
                    "Codex CLI (`codex`) was not found on PATH. "
                    "Install it, then run `codex login`."
                ),
            )

        result = EngineResult(engine=self.name)
        assistant_parts: list[str] = []
        stderr_chunks: list[str] = []

        def _read_stderr() -> None:
            assert self._proc and self._proc.stderr
            for line in self._proc.stderr:
                stderr_chunks.append(line)

        err_thread = threading.Thread(target=_read_stderr, daemon=True)
        err_thread.start()

        deadline = time.time() + self.timeout_sec
        assert self._proc.stdout is not None
        try:
            while True:
                if self._cancelled:
                    result.cancelled = True
                    break
                if time.time() > deadline:
                    self.cancel()
                    result.is_error = True
                    result.error = f"Turn timed out after {self.timeout_sec}s"
                    break
                line = self._proc.stdout.readline()
                if not line:
                    if self._proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    stderr_chunks.append(line + "\n")
                    continue
                if not isinstance(event, dict):
                    continue
                result.raw_events.append(event)
                self._consume_event(event, result, assistant_parts)
        finally:
            err_thread.join(timeout=2)
            self._reap()

        # Authoritative final text: -o file, then last agent_message
        try:
            if last_msg_path.exists():
                file_text = last_msg_path.read_text(encoding="utf-8").strip()
                if file_text:
                    result.text = file_text
        except OSError:
            pass
        finally:
            last_msg_path.unlink(missing_ok=True)

        if not result.text and assistant_parts:
            result.text = assistant_parts[-1].strip()

        result.images = new_image_files(before_images)

        stderr_text = "".join(stderr_chunks)
        auth_blob = (result.text + "\n" + stderr_text + "\n" + result.error).lower()
        if any(m in auth_blob for m in CODEX_AUTH_MARKERS) or (
            result.is_error and "login" in auth_blob
        ):
            result.is_error = True
            result.error = "Codex is not logged in. Run `codex login` on this machine."

        if self._cancelled:
            result.cancelled = True
            if not result.error:
                result.error = "cancelled"

        if (
            self._proc
            and self._proc.returncode not in (0, None, -signal.SIGTERM, -signal.SIGKILL)
            and not result.text
            and not result.cancelled
        ):
            result.is_error = True
            if not result.error:
                result.error = (stderr_text or f"codex exited {self._proc.returncode}")[:500]

        return result

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._proc
        if not proc or proc.poll() is not None:
            return
        kill_process_group(proc)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _reap(self) -> None:
        proc = self._proc
        if not proc:
            return
        if proc.poll() is None:
            self.cancel()
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass

    @staticmethod
    def _consume_event(
        event: dict[str, Any],
        result: EngineResult,
        assistant_parts: list[str],
    ) -> None:
        sid = _extract_session_id(event)
        if sid and not result.session_id:
            result.session_id = sid
        elif sid:
            result.session_id = sid

        text = _extract_agent_text(event)
        if text:
            assistant_parts.append(text)

        usage = _extract_usage(event)
        if usage:
            result.usage = usage
            result.input_tokens = int(
                usage.get("input_tokens")
                or usage.get("input")
                or usage.get("total_input_tokens")
                or 0
            )
            result.cache_tokens = int(
                usage.get("cached_input_tokens")
                or usage.get("cache_read_input_tokens")
                or 0
            )
            total = usage.get("total_tokens") or usage.get("total")
            if total and not result.last_context_tokens:
                try:
                    result.last_context_tokens = int(total)
                except (TypeError, ValueError):
                    pass
            # Codex's input_tokens already includes the cached part.
            if result.input_tokens and not result.last_context_tokens:
                result.last_context_tokens = result.input_tokens

        err = event.get("error") or event.get("message") if event.get("type") == "error" else None
        if isinstance(err, dict):
            err = err.get("message") or err.get("error") or str(err)
        if err and not result.error:
            result.is_error = True
            result.error = str(err)
