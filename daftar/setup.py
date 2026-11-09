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
    active_profile,
    ensure_home,
    load_config,
    other_workspaces,
    profile_label,
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


def _check_binary(name: str) -> tuple[bool, str]:
    if not shutil.which(name):
        return False, ""
    try:
        r = subprocess.run(
            [name, "--version"], capture_output=True, text=True, timeout=30
        )
        ver = (r.stdout or r.stderr or "").strip().splitlines()
        return True, (ver[0] if ver else "ok")
    except Exception as e:
        return False, str(e)


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
    label = profile_label()
    print(f"=== Daftar setup (profile: {label}) ===")
    ensure_home()
    cfg = load_config()

    print("\n(1) Engine")
    claude_ok, claude_ver = _check_binary("claude")
    codex_ok, codex_ver = _check_binary("codex")
    if claude_ok:
        print(f"✓ Claude Code: {claude_ver}")
    else:
        print("✗ `claude` not found on PATH.")
        print("  Install: curl -fsSL https://claude.ai/install.sh | bash")
    if codex_ok:
        print(f"✓ Codex CLI: {codex_ver}")
    else:
        print("✗ `codex` not found on PATH (optional — for a Codex bridge).")
    print("  Codex auth: run `codex login` on this machine (ChatGPT account or API key).")
    print("  Daftar never stores Codex/Claude credentials.")

    default_engine = (cfg.get("ENGINE") or "claude").strip().lower()
    if default_engine not in ("claude", "codex"):
        default_engine = "claude"
    engine = _prompt("Engine for this bridge (claude|codex)", default_engine).strip().lower()
    if engine not in ("claude", "codex"):
        print("Engine must be claude or codex.")
        return 1
    if engine == "claude" and not claude_ok:
        if not _yes("Continue without Claude on PATH?", default=False):
            return 1
    if engine == "codex" and not codex_ok:
        if not _yes("Continue without Codex on PATH?", default=False):
            return 1
    cfg["ENGINE"] = engine

    print("\n(2) Telegram bot token")
    print("  Talk to @BotFather → /newbot → copy the token.")
    print("  Each bridge needs its own bot (one bot per engine/profile).")
    token = _prompt("Bot token", cfg.get("TELEGRAM_BOT_TOKEN", ""), secret=True)
    if not token:
        print("Token required.")
        return 1
    tg = Telegram(token, api_base=cfg.get("TELEGRAM_API_BASE") or "https://api.telegram.org")
    try:
        me = tg.get_me()
        print(f"✓ Bot @{me.get('username')} ({me.get('first_name')})")
        cfg["BOT_USERNAME"] = str(me.get("username") or "")
    except Exception as e:
        print(f"✗ getMe failed: {e}")
        return 1
    cfg["TELEGRAM_BOT_TOKEN"] = token

    print("\n(3) Pair your Telegram account")
    cfg["OWNER_ID"] = _pair_owner(tg, cfg.get("OWNER_ID", ""))

    print("\n(4) Workspace")
    shared = False
    others = other_workspaces(label)
    if others:
        print("  Other profiles already have a workspace:")
        for pname, pws in others:
            print(f"    - {pname}: {pws}")
        # Offer the first as default reuse target
        reuse_name, reuse_ws = others[0]
        if _yes(f"Share memory with {reuse_name}?", default=True):
            ws = Path(reuse_ws)
            shared = True
            cfg["WORKSPACE"] = str(ws)
            print(f"✓ Reusing workspace {ws}")
        else:
            default_ws = cfg.get("WORKSPACE") or str(Path.home() / "daftar-workspace")
            ws = Path(_prompt("Workspace path", default_ws)).expanduser()
            cfg["WORKSPACE"] = str(ws.resolve())
    else:
        default_ws = cfg.get("WORKSPACE") or str(Path.home() / "daftar-workspace")
        ws = Path(_prompt("Workspace path", default_ws)).expanduser()
        cfg["WORKSPACE"] = str(ws.resolve())

    ws = Path(cfg["WORKSPACE"])
    if not any(ws.iterdir()) if ws.exists() else True:
        _copy_workspace(ws)
        print(f"✓ Template copied to {ws}")
    else:
        _copy_workspace(ws)
        print(f"✓ Workspace ready at {ws}")
    _rewrite_hook_settings(ws)

    print("\n(5) Language / timezone / model")
    cfg["LANGUAGE"] = _prompt("Reply language", cfg.get("LANGUAGE") or "English")
    cfg["TIMEZONE"] = _prompt(
        "Timezone (IANA name; the nightly job runs at 2 AM here by default)",
        cfg.get("TIMEZONE") or _detect_timezone(),
    )
    model_hint = "CLI default"
    cfg["MODEL"] = _prompt(
        f"Model (blank = {model_hint})", cfg.get("MODEL") or ""
    )
    cfg["REMINDER"] = f"Reply in {cfg['LANGUAGE']}. Be concise."

    print("\n(6) Permission mode")
    print("  bypassPermissions — full autonomy (ONLY on a machine dedicated to this assistant).")
    print("  For Codex this maps to --dangerously-bypass-approvals-and-sandbox;")
    print("  otherwise Codex uses -s workspace-write.")
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

    # Shared workspace → PERSONA.<profile>.md so each bot keeps its own voice
    if shared:
        persona_rel = f"persona/PERSONA.{label}.md"
    else:
        persona_rel = "persona/PERSONA.md"
    cfg["PERSONA_FILE"] = persona_rel
    persona = ws / persona_rel
    persona.parent.mkdir(parents=True, exist_ok=True)
    engine_label = "Codex" if engine == "codex" else "Claude Code"
    persona.write_text(
        f"# Persona\n\n"
        f"You are **{name}**, a personal assistant running via {engine_label} and Telegram.\n\n"
        f"## Owner\n{owner_desc or '(not specified)'}\n\n"
        f"## Tone\n{tone}\n\n"
        f"## Always\n{always or '- Be helpful and honest.'}\n\n"
        f"## Never\n{never or '- Never invent facts or credentials.'}\n",
        encoding="utf-8",
    )
    # Point CLAUDE.md at this persona when not using the default path
    claude_md = ws / "CLAUDE.md"
    if claude_md.exists() and persona_rel != "persona/PERSONA.md":
        text = claude_md.read_text(encoding="utf-8")
        import re

        claude_md.write_text(re.sub(r"@persona/\S+", f"@{persona_rel}", text), encoding="utf-8")

    if engine == "codex":
        from .agents_md import ensure_agents_md

        ensure_agents_md(ws, persona_file=persona, cfg=cfg, force=True)
        print(f"✓ Wrote AGENTS.md for Codex (persona {persona_rel})")

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
    for key, label_key in (
        ("OPENAI_API_KEY", "OPENAI_API_KEY"),
        ("GROQ_API_KEY", "GROQ_API_KEY"),
        ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
        ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    ):
        cfg[key] = _prompt(label_key, cfg.get(key, ""), secret=True)

    print("\n(9) Optional backup remote")
    print("  Nightly backups write tar.gz under DAFTAR_HOME/backups/.")
    print("  To also upload: install rclone, run `rclone config`, then set a remote")
    print("  like gdrive:Daftar (folder path after the colon). Leave blank to skip.")
    cfg["BACKUP_RCLONE_REMOTE"] = _prompt(
        "BACKUP_RCLONE_REMOTE", cfg.get("BACKUP_RCLONE_REMOTE") or ""
    )
    if cfg["BACKUP_RCLONE_REMOTE"] and not shutil.which("rclone"):
        print("  Note: `rclone` is not on PATH yet — install it before remote uploads work.")

    print("\n(10) Optional connectors (MCP)")
    print("  Attach HTTP or stdio MCP servers later with:")
    print('    daftar connect add <name> --url https://example.com/mcp')
    print('    daftar connect add <name> --command "npx -y some-mcp-server"')
    print("  See docs/CONNECTORS.md. OAuth connectors may need one interactive")
    print("  `claude` / `codex` run to authorize.")

    save_config(cfg)
    print(f"\n✓ Saved {ensure_home() / 'config.env'} (mode 600)")

    print("\n(11) Service")
    if _yes("Install background service now?", default=False):
        from . import service as svc

        svc.install()
    else:
        flag = f" --profile {label}" if active_profile() else ""
        print(f"  Later: `daftar{flag} service install` then `daftar{flag} run`.")

    print("\nDone. Next: start the bridge, then send /ping to the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(run_setup())
