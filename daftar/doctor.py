"""Health checks for a Daftar install."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys

from . import kb as kbmod
from . import service as svc
from .config import (
    config_path,
    load_config,
    profile_label,
    workspace_path,
)
from .telegram import Telegram


def _ok(msg: str) -> None:
    print(f"✓ {msg}")


def _bad(msg: str, fix: str = "") -> None:
    print(f"✗ {msg}")
    if fix:
        print(f"  → {fix}")


def _check_engine(engine: str) -> int:
    bad = 0
    engine = (engine or "claude").strip().lower()
    if engine == "codex":
        binary = "codex"
        hint = "install Codex CLI, then run `codex login`"
        login_hint = "Codex auth: run `codex login` (ChatGPT account or API key). Daftar never stores credentials."
    else:
        binary = "claude"
        hint = "curl -fsSL https://claude.ai/install.sh | bash"
        login_hint = "Claude auth: run `claude` once interactively and log in."

    path = shutil.which(binary)
    if path:
        try:
            r = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            ver = (
                (r.stdout or r.stderr or "").strip().splitlines()[0]
                if (r.stdout or r.stderr)
                else "ok"
            )
            _ok(f"{binary} on PATH ({path}): {ver}")
        except Exception as e:
            _bad(f"{binary} found but --version failed: {e}")
            bad += 1
    else:
        _bad(f"{binary} not on PATH", hint)
        bad += 1
    print(f"  {login_hint}")
    return bad


def run_doctor() -> int:
    bad = 0
    label = profile_label()
    print(f"— profile: {label} —")

    # Python
    if sys.version_info >= (3, 10):
        _ok(f"Python {sys.version.split()[0]}")
    else:
        _bad(f"Python {sys.version.split()[0]}", "Need Python >= 3.10")
        bad += 1

    # config
    path = config_path()
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode == 0o600:
            _ok(f"config {path} mode 600")
        else:
            _bad(f"config {path} mode {oct(mode)}", f"chmod 600 {path}")
            bad += 1
        cfg = load_config()
    else:
        _bad(f"config missing: {path}", "run: daftar setup")
        bad += 1
        return bad

    engine = (cfg.get("ENGINE") or "claude").strip().lower()
    _ok(f"ENGINE={engine}")
    bad += _check_engine(engine)

    # Telegram
    token = (cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        _bad("TELEGRAM_BOT_TOKEN empty", "run: daftar setup")
        bad += 1
    else:
        try:
            me = Telegram(
                token, api_base=cfg.get("TELEGRAM_API_BASE") or "https://api.telegram.org"
            ).get_me()
            _ok(f"Telegram getMe ok (@{me.get('username', '?')})")
        except Exception as e:
            _bad(f"Telegram getMe failed: {e}", "check TELEGRAM_BOT_TOKEN")
            bad += 1

    if not (cfg.get("OWNER_ID") or "").strip():
        _bad("OWNER_ID empty", "run: daftar setup (pairing)")
        bad += 1
    else:
        _ok(f"OWNER_ID={cfg['OWNER_ID']}")

    # workspace
    ws = workspace_path(cfg)
    needed = [
        "knowledge/topics.json",
        "knowledge/MAP.md",
        "knowledge/core.md",
        "inbox",
        "outbox",
        "sessions",
    ]
    if engine == "claude":
        needed.insert(0, "CLAUDE.md")
    missing = [p for p in needed if not (ws / p).exists()]
    if missing:
        _bad(f"workspace incomplete ({ws}): missing {', '.join(missing)}", "re-run daftar setup")
        bad += 1
    else:
        _ok(f"workspace layout ok ({ws})")

    persona = cfg.get("PERSONA_FILE") or "persona/PERSONA.md"
    if (ws / persona).exists():
        _ok(f"persona {persona}")
    else:
        _bad(f"persona missing: {persona}", "re-run daftar setup")
        bad += 1

    # kb
    try:
        code, msg = kbmod.cmd_check(ws / "knowledge")
        if code == 0:
            _ok("kb check ok")
        else:
            _bad(f"kb check: {msg.splitlines()[-1]}", "daftar kb map")
            bad += 1
    except Exception as e:
        _bad(f"kb check error: {e}")
        bad += 1

    # service
    try:
        print("— service —")
        svc.status()
    except Exception:
        pass

    print()
    print("doctor: all good" if bad == 0 else f"doctor: {bad} issue(s)")
    return 1 if bad else 0
