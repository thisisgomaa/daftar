#!/usr/bin/env python3
"""Claude Code hook: inject a topic file when work touches it.

UserPromptSubmit → keywords in the prompt.
PostToolUse → tool name / command patterns from topics.json.

Each topic is injected at most once per session; max 2 topics per event;
body capped at 9000 chars. Failures are silent — never block the agent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

MAX_CHARS = 9000
MAX_TOPICS = 2


def _kb_root() -> Path:
    env = os.environ.get("DAFTAR_KB", "").strip()
    if env:
        return Path(env)
    # settings.json may set CLAUDE_PROJECT_DIR; else walk up from cwd
    proj = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if proj and (Path(proj) / "knowledge" / "topics.json").exists():
        return Path(proj) / "knowledge"
    cwd = Path.cwd()
    if (cwd / "knowledge" / "topics.json").exists():
        return cwd / "knowledge"
    # Hook file lives at .../daftar/hooks/kb_hook.py — not the workspace
    return cwd / "knowledge"


def _state_dir() -> Path:
    override = os.environ.get("DAFTAR_HOOK_STATE", "").strip()
    base = Path(override) if override else Path(tempfile.gettempdir()) / "daftar_kb_hook"
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> None:
    data = json.load(sys.stdin)
    event = data.get("hook_event_name", "")
    sid = str(data.get("session_id", "nosession"))[:64]
    kb = _kb_root()
    topics_path = kb / "topics.json"
    if not topics_path.exists():
        return
    topics = json.loads(topics_path.read_text(encoding="utf-8"))

    if event == "UserPromptSubmit":
        hay = re.sub(r"⟨[^⟩]*⟩", " ", data.get("prompt") or "").lower()
        field = "keywords"
    elif event == "PostToolUse":
        tin = data.get("tool_input") or {}
        hay = (str(data.get("tool_name", "")) + " " + str(tin.get("command", ""))).lower()
        field = "tools"
    else:
        return

    sf = _state_dir() / f"{sid}.json"
    seen = set(json.loads(sf.read_text())) if sf.exists() else set()

    hits: list[str] = []
    for slug, meta in topics.items():
        if slug in seen:
            continue
        if any(k and str(k).lower() in hay for k in meta.get(field, []) or []):
            hits.append(slug)
    hits = hits[:MAX_TOPICS]
    if not hits:
        return

    parts: list[str] = []
    for slug in hits:
        p = kb / "topics" / f"{slug}.md"
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        if len(txt) > MAX_CHARS:
            txt = txt[:MAX_CHARS] + f"\n… (rest of file: `daftar kb show {slug}`)"
        parts.append(txt)
        seen.add(slug)
    sf.write_text(json.dumps(sorted(seen)))
    if not parts:
        return
    ctx = (
        "⟨Knowledge: this work touches the topic below — it was opened for you. Follow it.⟩\n\n"
        + "\n\n".join(parts)
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": ctx,
                }
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
