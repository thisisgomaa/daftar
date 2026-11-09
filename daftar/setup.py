"""Interactive `daftar setup`."""

from __future__ import annotations

import getpass
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .config import (
    ensure_home,
    load_config,
    save_config,
    _detect_timezone,
)
from .telegram import Telegram


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    """Ask a question; secrets are read without echo and never shown back."""
    try:
        if secret:
            hint = " [saved, Enter to keep]" if default else ""
            raw = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            suffix = f" [{default}]" if default else ""
            raw = input(f"{label}{suffix}: ").strip()
        return raw if raw else default
    except EOFError:
        return default


def _yes(label: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = _prompt(f"{label} ({d})", "")
    if not raw:
        return default
    return raw.lower() in ("y", "yes")


def _template_root() -> Path:
    return Path(__file__).resolve().parents[1] / "template" / "workspace"


def _copy_workspace(dest: Path) -> None:
    src = _template_root()
    dest.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dest / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            s = Path(root) / name
            d = target_dir / name
            if d.exists() and d.stat().st_size > 0 and name not in (
                "CLAUDE.md",
                "topics.json",
                "MAP.md",
                "core.md",
                "BASE_RULES.md",
            ):
                # Keep user-edited persona/rules; still ensure knowledge scaffolding
                if "persona" in str(rel) or name == "MY_RULES.md":
                    continue
            if not d.exists() or d.stat().st_size == 0:
                shutil.copy2(s, d)
            elif name in ("topics.json", "BASE_RULES.md", "CLAUDE.md") and not d.exists():
                shutil.copy2(s, d)


def _rewrite_hook_settings(workspace: Path) -> None:
    hook = Path(__file__).resolve().parent / "hooks" / "kb_hook.py"
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = f"python3 {hook}"
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": cmd}]}
            ],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": cmd}],
                }
            ],
        }
    }
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _check_claude() -> bool:
    if not shutil.which("claude"):
        print("✗ `claude` not found on PATH.")
        print("  Install Claude Code: curl -fsSL https://claude.ai/install.sh | bash")
        return False
    try:
        r = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30
        )
        print(f"✓ Claude Code: {(r.stdout or r.stderr or '').strip().splitlines()[0]}")
        return True
    except Exception as e:
        print(f"✗ claude --version failed: {e}")
        return False


def _pair_owner(tg: Telegram, existing: str = "") -> str:
    print()
    print("Pairing: open Telegram and send any message to your bot.")
    print("I will show the sender and a 6-digit code — type that code here.")
    if existing:
        if not _yes(f"Re-pair? Current OWNER_ID={existing}", default=False):
            return existing
    seen: set[int] = set()
    started = time.time()
    deadline = started + 300
    offset = None
    codes: dict[int, str] = {}
    while time.time() < deadline:
        try:
            updates = tg.get_updates(offset, timeout=20)
        except Exception as e:
            print(f"  poll error: {e}")
            time.sleep(2)
            continue
        for upd in updates:
            uid = int(upd.get("update_id", 0))
            offset = uid + 1
            msg = upd.get("message") or {}
            frm = msg.get("from") or {}
            # Only messages sent after setup started (ignore an old backlog).
            if not frm or int(msg.get("date") or 0) < started - 5:
                continue
            user_id = int(frm["id"])
            if user_id in codes:
                continue
            name = (
                frm.get("username")
                or " ".join(
                    x for x in (frm.get("first_name"), frm.get("last_name")) if x
                )
                or str(user_id)
            )
            code = f"{random.randint(0, 999999):06d}"
            codes[user_id] = code
            try:
                tg.send_message(
                    user_id,
                    f"Daftar pairing code: {code}\nType this code in the setup terminal.",
                )
            except Exception:
                pass
            print(f"  Saw message from {name} (id={user_id}). Code sent to chat.")
            seen.add(user_id)
        if codes:
            typed = _prompt("Enter the 6-digit code")
            for user_id, code in codes.items():
                if typed == code:
                    print(f"✓ Paired OWNER_ID={user_id}")
                    # Skip the pairing messages so the bridge doesn't answer them.
                    from .sessions import load_state, save_state

                    st = load_state()
                    st.offset = offset
                    save_state(st)
                    return str(user_id)
            print("  Code mismatch — try again (or wait for another message).")
    raise RuntimeError("Pairing timed out. Re-run `daftar setup`.")


def run_setup() -> int:
    print("=== Daftar setup ===")
    ensure_home()
    cfg = load_config()

    print("\n(1) Claude Code CLI")
    if not _check_claude():
        if not _yes("Continue without Claude on PATH?", default=False):
            return 1

    print("\n(2) Telegram bot token")
    print("  Talk to @BotFather → /newbot → copy the token.")
    token = _prompt("Bot token", cfg.get("TELEGRAM_BOT_TOKEN", ""), secret=True)
    if not token:
        print("Token required.")
        return 1
    tg = Telegram(token, api_base=cfg.get("TELEGRAM_API_BASE") or "https://api.telegram.org")
    try:
        me = tg.get_me()
        print(f"✓ Bot @{me.get('username')} ({me.get('first_name')})")
    except Exception as e:
        print(f"✗ getMe failed: {e}")
        return 1
    cfg["TELEGRAM_BOT_TOKEN"] = token

    print("\n(3) Pair your Telegram account")
    cfg["OWNER_ID"] = _pair_owner(tg, cfg.get("OWNER_ID", ""))

    print("\n(4) Workspace")
    default_ws = cfg.get("WORKSPACE") or str(Path.home() / "daftar-workspace")
    ws = Path(_prompt("Workspace path", default_ws)).expanduser()
    cfg["WORKSPACE"] = str(ws.resolve())
    if not any(ws.iterdir()) if ws.exists() else True:
        _copy_workspace(ws)
        print(f"✓ Template copied to {ws}")
    else:
        # Ensure missing scaffolding
        _copy_workspace(ws)
        print(f"✓ Workspace ready at {ws}")
    _rewrite_hook_settings(ws)

    print("\n(5) Language / timezone / model")
    cfg["LANGUAGE"] = _prompt("Reply language", cfg.get("LANGUAGE") or "English")
    cfg["TIMEZONE"] = _prompt("Timezone", cfg.get("TIMEZONE") or _detect_timezone())
    cfg["MODEL"] = _prompt(
        "Model (blank = Claude Code default)", cfg.get("MODEL") or ""
    )
    cfg["REMINDER"] = f"Reply in {cfg['LANGUAGE']}. Be concise."

    print("\n(6) Permission mode")
    print("  bypassPermissions — full autonomy (ONLY on a machine dedicated to this assistant).")
    print("  acceptEdits / default — safer; Claude Code may prompt for some actions.")
    print("  WARNING: bypassPermissions lets the agent edit and run commands without asking.")
    mode = _prompt(
        "Permission mode",
        cfg.get("PERMISSION_MODE") or "bypassPermissions",
    )
    cfg["PERMISSION_MODE"] = mode
    if mode == "bypassPermissions" and hasattr(os, "geteuid") and os.geteuid() == 0:
        print("  You are running as root. Claude Code refuses full autonomy as root unless")
        print("  IS_SANDBOX=1 is set. Safer: create a normal user for the assistant.")
        if _yes("Set IS_SANDBOX=1 (this machine is dedicated to the assistant)?", default=False):
            cfg["IS_SANDBOX"] = "1"

    print("\n(7) Persona")
    name = _prompt("Assistant name", cfg.get("ASSISTANT_NAME") or "Daftar")
    cfg["ASSISTANT_NAME"] = name
    owner_desc = _prompt("Who are you / what do you do?", "")
    tone = _prompt("Tone (e.g. concise, warm, direct)", "concise and direct")
    always = _prompt("Anything to always do?", "")
    never = _prompt("Anything to never do?", "")
    persona = ws / "persona" / "PERSONA.md"
    persona.parent.mkdir(parents=True, exist_ok=True)
    persona.write_text(
        f"# Persona\n\n"
        f"You are **{name}**, a personal assistant running via Claude Code and Telegram.\n\n"
        f"## Owner\n{owner_desc or '(not specified)'}\n\n"
        f"## Tone\n{tone}\n\n"
        f"## Always\n{always or '- Be helpful and honest.'}\n\n"
        f"## Never\n{never or '- Never invent facts or credentials.'}\n",
        encoding="utf-8",
    )
    rules = ws / "rules" / "MY_RULES.md"
    rules.parent.mkdir(parents=True, exist_ok=True)
    if not rules.exists() or rules.stat().st_size == 0:
        rules.write_text(
            "# My rules\n\n"
            "Add personal standing rules here. Owner guidance that belongs in a\n"
            "knowledge topic should use: `daftar kb add <topic> \"…\" --by owner`.\n",
            encoding="utf-8",
        )

    print("\n(8) Optional API keys (press Enter to skip)")
    print("  OPENAI — voice (Whisper) + `daftar image`")
    print("  GROQ — voice fallback (whisper-large-v3)")
    print("  ELEVENLABS — voice fallback (Scribe)")
    print("  ANTHROPIC — optional API-key mode (passed through to `claude` child env)")
    for key, label in (
        ("OPENAI_API_KEY", "OPENAI_API_KEY"),
        ("GROQ_API_KEY", "GROQ_API_KEY"),
        ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
        ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    ):
        cfg[key] = _prompt(label, cfg.get(key, ""), secret=True)

    save_config(cfg)
    print(f"\n✓ Saved {ensure_home() / 'config.env'} (mode 600)")

    print("\n(9) Service")
    if _yes("Install background service now?", default=False):
        from . import service as svc

        svc.install()
    else:
        print("  Later: `daftar service install` then `daftar run` (or let the service start).")

    print("\nDone. Next: `daftar run` (or start the service), then send /ping to the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(run_setup())
