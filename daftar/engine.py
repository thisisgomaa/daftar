"""Engine interface: Claude Code and Codex CLI subprocess runners."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("daftar.engine")

AUTH_MARKERS = (
    "not logged in",
    "please run /login",
    "authentication required",
    "unauthorized",
    "please login",
    "claude ai account",
    "run claude and log in",
)

ENGINE_CLAUDE = "claude"
ENGINE_CODEX = "codex"
KNOWN_ENGINES = (ENGINE_CLAUDE, ENGINE_CODEX)


@dataclass
class EngineResult:
    text: str = ""
    session_id: Optional[str] = None
    is_error: bool = False
    error: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    cache_tokens: int = 0
    total_cost_usd: float = 0.0
    cancelled: bool = False
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    last_context_tokens: int = 0
    images: list[Path] = field(default_factory=list)
    engine: str = ENGINE_CLAUDE

    @property
    def context_tokens(self) -> int:
        """Current conversation size (last API call), falling back to turn totals."""
        if self.last_context_tokens:
            return int(self.last_context_tokens)
        return int(self.input_tokens) + int(self.cache_tokens)


class BaseEngine(ABC):
    """Common subprocess engine contract."""

    name: str = "base"
    model: str = ""

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        resume: Optional[str] = None,
        append_system_prompt: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> EngineResult:
        ...

    @abstractmethod
    def cancel(self) -> None:
        ...

    def is_running(self) -> bool:
        return False


def repo_bin_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "bin"


def prepend_repo_bin(env: dict[str, str]) -> dict[str, str]:
    """Ensure the `daftar` CLI is on PATH for the agent subprocess."""
    bin_dir = str(repo_bin_dir())
    env = dict(env)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM the process group, then SIGKILL if needed."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    deadline = time.time() + 5
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class ClaudeEngine(BaseEngine):
    """Run the unmodified `claude` binary as a subprocess."""

    name = ENGINE_CLAUDE

    def __init__(
        self,
        *,
        cwd: Path | str,
        model: str = "",
        permission_mode: str = "bypassPermissions",
        timeout_sec: int = 2700,
        binary: str = "claude",
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
        output_format: str = "stream-json",
    ) -> EngineResult:
        self._cancelled = False
        cmd = [
            self.binary,
            "-p",
            prompt,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            cmd.append("--verbose")
        if self.model:
            cmd.extend(["--model", self.model])
        if self.permission_mode:
            cmd.extend(["--permission-mode", self.permission_mode])
        if resume:
            cmd.extend(["--resume", resume])
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])
        if extra_args:
            cmd.extend(extra_args)

        env = prepend_repo_bin(os.environ.copy())
        if self.env:
            env.update({k: v for k, v in self.env.items() if v})

        log.info("claude spawn cwd=%s resume=%s model=%s", self.cwd, resume, self.model or "default")
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,  # own process group
            )
        except FileNotFoundError:
            return EngineResult(
                is_error=True,
                engine=self.name,
                error=(
                    "Claude Code CLI (`claude`) was not found on PATH. "
                    "Install it: curl -fsSL https://claude.ai/install.sh | bash"
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
                if output_format == "text":
                    assistant_parts.append(line)
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON chatter — keep for auth detection
                    stderr_chunks.append(line + "\n")
                    continue
                result.raw_events.append(event)
                self._consume_event(event, result, assistant_parts)
        finally:
            err_thread.join(timeout=2)
            self._reap()

        stderr_text = "".join(stderr_chunks)
        if output_format == "text" and not result.text:
            result.text = "\n".join(assistant_parts).strip()

        if not result.text and assistant_parts:
            result.text = "\n".join(assistant_parts).strip()

        auth_blob = (result.text + "\n" + stderr_text + "\n" + result.error).lower()
        if any(m in auth_blob for m in AUTH_MARKERS) or (
            result.is_error and "login" in auth_blob
        ):
            result.is_error = True
            result.error = (
                "Claude Code is not logged in. Run `claude` once on this machine and log in."
            )

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
                result.error = (stderr_text or f"claude exited {self._proc.returncode}")[:500]

        return result

    def run_text(
        self,
        prompt: str,
        *,
        extra_args: Optional[list[str]] = None,
        model: Optional[str] = None,
    ) -> str:
        """Convenience for headless one-shot text output (curator / papers)."""
        saved_model = self.model
        if model:
            self.model = model
        try:
            args = list(extra_args or [])
            # Safe headless defaults for curator-style calls
            if "--no-session-persistence" not in args:
                args.append("--no-session-persistence")
            res = self.run(prompt, extra_args=args, output_format="text")
            if res.is_error and not res.text:
                raise RuntimeError(res.error or "claude failed")
            return res.text
        finally:
            self.model = saved_model

    def cancel(self) -> None:
        """Kill the process group of the running claude subprocess."""
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
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid:
                result.session_id = sid
            return

        if etype == "assistant":
            msg = event.get("message") or {}
            # The result event sums usage over every API call in the turn, which
            # overstates the context size. The last assistant message's usage is
            # the real size of the conversation right now.
            u = msg.get("usage") or {}
            if isinstance(u, dict) and u:
                result.last_context_tokens = int(u.get("input_tokens") or 0) + int(
                    u.get("cache_read_input_tokens") or 0
                ) + int(u.get("cache_creation_input_tokens") or 0)
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text") or ""
                    if t:
                        assistant_parts.append(t)
            return

        if etype == "result":
            sid = event.get("session_id")
            if sid:
                result.session_id = sid
            result.is_error = bool(event.get("is_error"))
            result.total_cost_usd = float(event.get("total_cost_usd") or 0)
            usage = event.get("usage") or {}
            result.usage = usage if isinstance(usage, dict) else {}
            result.input_tokens = int(
                result.usage.get("input_tokens")
                or result.usage.get("input")
                or event.get("input_tokens")
                or 0
            )
            # cache tokens may appear as cache_read_input_tokens / cache_creation
            cache = int(
                result.usage.get("cache_read_input_tokens")
                or result.usage.get("cache_read_tokens")
                or 0
            )
            cache += int(result.usage.get("cache_creation_input_tokens") or 0)
            result.cache_tokens = cache
            final = event.get("result")
            if isinstance(final, str) and final.strip():
                result.text = final.strip()
            elif assistant_parts:
                result.text = "\n".join(assistant_parts).strip()
            err = event.get("error") or event.get("errors")
            if err and not result.error:
                result.error = str(err)
            return


def make_engine(
    cfg: dict[str, str],
    *,
    workspace: Path | str | None = None,
    model: str = "",
    name: str = "",
) -> BaseEngine:
    """Factory: pick Claude or Codex from config ENGINE (default claude)."""
    from .config import get_int, workspace_path

    engine_name = (name or cfg.get("ENGINE") or ENGINE_CLAUDE).strip().lower()
    ws = Path(workspace) if workspace else workspace_path(cfg)
    timeout = get_int(cfg, "TURN_TIMEOUT_SEC", 2700)
    perm = cfg.get("PERMISSION_MODE") or "bypassPermissions"
    use_model = model if model else (cfg.get("MODEL") or "")
    if engine_name == ENGINE_CODEX:
        from .engine_codex import CodexEngine

        return CodexEngine(
            cwd=ws,
            model=use_model,
            permission_mode=perm,
            timeout_sec=timeout,
        )
    env = None
    if cfg.get("ANTHROPIC_API_KEY"):
        env = {"ANTHROPIC_API_KEY": cfg["ANTHROPIC_API_KEY"]}
    return ClaudeEngine(
        cwd=ws,
        model=use_model,
        permission_mode=perm,
        timeout_sec=timeout,
        env=env,
    )
