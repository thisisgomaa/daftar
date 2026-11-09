"""Health checks for a Daftar install."""

from __future__ import annotations

import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

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


def _info(msg: str) -> None:
    print(f"! {msg}")


def _check_service_path() -> None:
    """Informational: engine CLIs outside the default systemd PATH (Linux)."""
    if platform.system() != "Linux":
        return
    default_dirs = set(svc.SYSTEMD_DEFAULT_PATH.split(":"))
    for binary in ("claude", "codex"):
        found = shutil.which(binary)
        if not found:
            continue
        d = str(Path(found).parent)
        if d not in default_dirs:
            _info(
                f"{binary} is in {d}, outside the default systemd PATH; "
                "the service unit carries PATH (re-run `daftar service install` "
                "after moving it), so this is informational"
            )


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
    if sys.version_info >= (3, 9):
        _ok(f"Python {sys.version.split()[0]}")
    else:
        _bad(f"Python {sys.version.split()[0]}", "Need Python >= 3.9")
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
    _check_service_path()

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

    # backups
    try:
        from . import backup as backupmod

        info = backupmod.last_backup_info()
        if not info.get("exists"):
            _ok("backup: none yet (run `daftar backup now`)")
        else:
            age_h = float(info.get("age_hours") or 0)
            age_s = f"{age_h:.1f}h ago" if age_h < 48 else f"{age_h / 24:.1f}d ago"
            if info.get("verified"):
                _ok(f"backup: {Path(info['path']).name} ({age_s}, verified)")
            else:
                _bad(
                    f"backup: {Path(info['path']).name} ({age_s}) not verified",
                    "daftar backup now",
                )
                bad += 1
        remote = (cfg.get("BACKUP_RCLONE_REMOTE") or "").strip()
        if remote:
            if shutil.which("rclone"):
                _ok(f"BACKUP_RCLONE_REMOTE={remote}")
            else:
                _bad("BACKUP_RCLONE_REMOTE set but rclone not on PATH", "install rclone")
                bad += 1
    except Exception as e:
        _bad(f"backup check error: {e}")
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
