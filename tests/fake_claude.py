#!/usr/bin/env python3
"""Fake `claude` CLI for tests — emits stream-json, honors --resume, sleeps on SLOW."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid


def main() -> int:
    # claude -p PROMPT --output-format stream-json --verbose ...
    argv = sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-p", dest="prompt", default="")
    parser.add_argument("--output-format", default="stream-json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--permission-mode", default="")
    parser.add_argument("--resume", default=None)
    parser.add_argument("--append-system-prompt", default="")
    parser.add_argument("--no-session-persistence", action="store_true")
    parser.add_argument("--allowedTools", default="")
    parser.add_argument("--allowed-tools", default="")
    args, _unknown = parser.parse_known_args(argv)

    prompt = args.prompt or ""
    # Sometimes -p is passed as next positional after flags oddly — keep raw join fallback
    if not prompt and "-p" in argv:
        i = argv.index("-p")
        if i + 1 < len(argv):
            prompt = argv[i + 1]

    sid = args.resume or str(uuid.uuid4())

    if "SLOW" in prompt:
        # Allow interrupt tests to cancel us
        for _ in range(200):
            time.sleep(0.05)

    if "HANDOFF PAPER" in prompt.upper() or "handoff paper" in prompt.lower() or "HANDOFF" in prompt:
        paper = (
            "# Handoff\n\n## Goal\nTest goal.\n\n## Decisions\nNone.\n\n"
            "## Open items\nFinish tests.\n\n## Files touched\nnone\n\n## Next step\nContinue.\n"
        )
        if args.output_format == "text":
            sys.stdout.write(paper)
            return 0
        _emit_stream(sid, paper, input_tokens=100, cache=0)
        return 0

    # Curator JSON
    if "Return JSON only" in prompt or '"merge"' in prompt or "propose_core" in prompt:
        plan = {
            "merge": [{"lines": [1, 2], "text": "Merged durable lesson about backups and verify."}],
            "retire": [],
            "move": [],
            "propose_core": [],
            "propose_rule": [],
        }
        raw = json.dumps(plan)
        if args.output_format == "text":
            sys.stdout.write(raw)
            return 0
        _emit_stream(sid, raw, input_tokens=50, cache=0)
        return 0

    # Auth failure simulation
    if "FORCE_AUTH_FAIL" in prompt:
        sys.stderr.write("Error: not logged in. Please run /login\n")
        _emit_stream(sid, "", input_tokens=0, cache=0, is_error=True, result_text="")
        return 1

    # Rotation threshold: report huge input tokens when asked
    input_tokens = 1000
    cache = 0
    if "FORCE_ROTATE" in prompt:
        input_tokens = 150000
        cache = 0

    reply = f"Echo: {prompt[:200]}"
    if args.resume:
        reply = f"Resumed({sid[:8]}): {prompt[:180]}"

    if args.output_format == "text":
        sys.stdout.write(reply)
        return 0

    _emit_stream(sid, reply, input_tokens=input_tokens, cache=cache)
    return 0


def _emit_stream(
    sid: str,
    text: str,
    *,
    input_tokens: int,
    cache: int,
    is_error: bool = False,
    result_text: str | None = None,
) -> None:
    print(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": sid,
            }
        ),
        flush=True,
    )
    if text:
        print(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                    "session_id": sid,
                }
            ),
            flush=True,
        )
    final = text if result_text is None else result_text
    print(
        json.dumps(
            {
                "type": "result",
                "session_id": sid,
                "result": final,
                "is_error": is_error,
                "total_cost_usd": 0.01,
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": cache,
                },
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
