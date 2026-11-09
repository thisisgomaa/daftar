"""Register MCP connectors on Claude Code and/or Codex CLI."""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
from typing import Optional

log = logging.getLogger("daftar.connectors")


def _mask_secret(value: str) -> str:
    v = value or ""
    if not v:
        return ""
    if len(v) <= 4:
        return "****"
    return v[:2] + "…" + v[-2:]


def mask_argv(argv: list[str]) -> list[str]:
    """Return argv with header/env values masked for display."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-H", "--header", "-e", "--env") and i + 1 < len(argv):
            raw = argv[i + 1]
            if "=" in raw:
                k, _, v = raw.partition("=")
                # header "K: V" form
                if a in ("-H", "--header") and ":" in raw and "=" not in raw.split(":", 1)[0]:
                    k, _, v = raw.partition(":")
                    out.append(a)
                    out.append(f"{k.strip()}: {_mask_secret(v.strip())}")
                else:
                    out.append(a)
                    out.append(f"{k}={_mask_secret(v)}")
            elif ":" in raw:
                k, _, v = raw.partition(":")
                out.append(a)
                out.append(f"{k.strip()}: {_mask_secret(v.strip())}")
            else:
                out.append(a)
                out.append(_mask_secret(raw))
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def format_cmd(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in mask_argv(argv))


def _run(argv: list[str], *, timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except Exception as e:
        return 1, "", str(e)


def installed_engines() -> list[str]:
    out: list[str] = []
    if shutil.which("claude"):
        out.append("claude")
    if shutil.which("codex"):
        out.append("codex")
    return out


def list_connectors() -> list[str]:
    """Run mcp list on each installed engine; return printable lines."""
    lines: list[str] = []
    engines = installed_engines()
    if not engines:
        return ["(neither claude nor codex on PATH)"]
    for eng in engines:
        if eng == "claude":
            argv = ["claude", "mcp", "list"]
        else:
            argv = ["codex", "mcp", "list"]
        rc, out, err = _run(argv)
        lines.append(f"## {eng}")
        lines.append(f"$ {format_cmd(argv)}")
        if rc != 0:
            lines.append(f"(failed rc={rc}) {(err or out).strip()[:400]}")
        else:
            body = (out or err or "(empty)").rstrip()
            lines.append(body)
        lines.append("")
    return lines


def add_http(
    name: str,
    url: str,
    *,
    headers: Optional[list[str]] = None,
) -> list[str]:
    """Register an HTTP MCP server on every installed engine."""
    headers = headers or []
    lines: list[str] = []
    engines = installed_engines()
    if not engines:
        return ["Neither claude nor codex is on PATH."]

    for eng in engines:
        if eng == "claude":
            argv = [
                "claude",
                "mcp",
                "add",
                "--scope",
                "user",
                "--transport",
                "http",
                name,
                url,
            ]
            for h in headers:
                argv.extend(["-H", h])
        else:
            argv = ["codex", "mcp", "add", name, "--url", url]
            if headers:
                lines.append(
                    f"## codex\n(warning: Codex HTTP headers are unsupported — ignored)"
                )
        rc, out, err = _run(argv)
        block = [f"## {eng}", f"$ {format_cmd(argv)}"]
        if rc != 0:
            block.append(f"FAILED rc={rc}: {(err or out).strip()[:400]}")
        else:
            block.append((out or err or "ok").strip()[:400] or "ok")
        lines.append("\n".join(block))
    return lines


def add_stdio(
    name: str,
    command: str,
    *,
    env: Optional[list[str]] = None,
) -> list[str]:
    """Register a stdio MCP server. `command` is a shell-like string of cmd + args."""
    env = env or []
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return [f"Invalid --command: {e}"]
    if not parts:
        return ["--command must include at least a program name"]

    lines: list[str] = []
    engines = installed_engines()
    if not engines:
        return ["Neither claude nor codex is on PATH."]

    for eng in engines:
        if eng == "claude":
            argv = ["claude", "mcp", "add", "--scope", "user", name]
            for e in env:
                argv.extend(["-e", e])
            argv.append("--")
            argv.extend(parts)
        else:
            argv = ["codex", "mcp", "add", name]
            for e in env:
                argv.extend(["--env", e])
            argv.append("--")
            argv.extend(parts)
        rc, out, err = _run(argv)
        block = [f"## {eng}", f"$ {format_cmd(argv)}"]
        if rc != 0:
            block.append(f"FAILED rc={rc}: {(err or out).strip()[:400]}")
        else:
            block.append((out or err or "ok").strip()[:400] or "ok")
        lines.append("\n".join(block))
    return lines


def remove_connector(name: str) -> list[str]:
    lines: list[str] = []
    engines = installed_engines()
    if not engines:
        return ["Neither claude nor codex is on PATH."]
    for eng in engines:
        if eng == "claude":
            argv = ["claude", "mcp", "remove", "--scope", "user", name]
        else:
            argv = ["codex", "mcp", "remove", name]
        rc, out, err = _run(argv)
        block = [f"## {eng}", f"$ {format_cmd(argv)}"]
        if rc != 0:
            block.append(f"FAILED rc={rc}: {(err or out).strip()[:400]}")
        else:
            block.append((out or err or "ok").strip()[:400] or "ok")
        lines.append("\n".join(block))
    return lines


def parse_header(raw: str) -> str:
    """Normalize 'K: V' or 'K=V' header strings; never log the value."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty header")
    if ":" in raw:
        k, _, v = raw.partition(":")
        return f"{k.strip()}: {v.strip()}"
    if "=" in raw:
        k, _, v = raw.partition("=")
        return f"{k.strip()}: {v.strip()}"
    raise ValueError("header must be 'Key: Value'")


def parse_env(raw: str) -> str:
    raw = (raw or "").strip()
    if "=" not in raw or not raw.split("=", 1)[0]:
        raise ValueError("env must be KEY=VALUE")
    # Validate key is a simple identifier
    k, _, v = raw.partition("=")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
        raise ValueError(f"invalid env key: {k}")
    return f"{k}={v}"
