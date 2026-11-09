#!/usr/bin/env python3
"""Fake `codex` CLI for tests — JSONL events, resume, -o file, images, SLOW."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    # codex exec ... or codex exec resume <id> ...
    if not argv or argv[0] != "exec":
        if argv and argv[0] == "--version":
            print("codex-cli 0.165.0-fake")
            return 0
        print("fake_codex: expected exec", file=sys.stderr)
        return 2

    rest = argv[1:]
    resume_id = None
    if rest and rest[0] == "resume":
        rest = rest[1:]
        if not rest:
            print("missing session id", file=sys.stderr)
            return 2
        resume_id = rest[0]
        rest = rest[1:]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-git-repo-check", action="store_true")
    parser.add_argument("-C", "--cd", default="")
    parser.add_argument("-m", "--model", default="")
    parser.add_argument("-s", "--sandbox", default="")
    parser.add_argument("--dangerously-bypass-approvals-and-sandbox", action="store_true")
    parser.add_argument("-o", "--output-last-message", default="")
    args, unknown = parser.parse_known_args(rest)

    # Prompt is the first non-flag positional remaining
    prompt_parts = [u for u in unknown if not u.startswith("-")]
    prompt = prompt_parts[0] if prompt_parts else ""

    sid = resume_id or str(uuid.uuid4())

    if "SLOW" in prompt:
        for _ in range(200):
            time.sleep(0.05)

    if "FORCE_AUTH_FAIL" in prompt or "not logged in" in prompt.lower():
        print("Error: not logged in. Please run `codex login`", file=sys.stderr)
        _emit_started(sid)
        return 1

    if "HANDOFF" in prompt.upper():
        text = (
            "# Handoff\n\n## Goal\nTest goal.\n\n## Decisions\nNone.\n\n"
            "## Open items\nFinish tests.\n\n## Files touched\nnone\n\n## Next step\nContinue.\n"
        )
    elif "FORCE_ROTATE" in prompt:
        text = f"Echo: {prompt[:200]}"
    elif resume_id:
        text = f"Resumed({sid[:8]}): {prompt[:180]}"
    else:
        text = f"Echo: {prompt[:200]}"

    # Built-in image tool simulation
    if "MAKE_IMAGE" in prompt:
        home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        img_dir = home / "generated_images" / sid
        img_dir.mkdir(parents=True, exist_ok=True)
        img = img_dir / "out.png"
        # Minimal PNG header bytes (not a real image, but a .png file)
        img.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    input_tokens = 150000 if "FORCE_ROTATE" in prompt else 1000

    _emit_started(sid)
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": text, "id": "msg1"},
                "thread_id": sid,
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "thread_id": sid,
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": 0,
                    "total_tokens": input_tokens,
                },
            }
        ),
        flush=True,
    )

    if args.output_last_message:
        Path(args.output_last_message).write_text(text, encoding="utf-8")

    return 0


def _emit_started(sid: str) -> None:
    print(
        json.dumps({"type": "thread.started", "thread_id": sid}),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
