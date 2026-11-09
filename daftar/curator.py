"""Nightly knowledge curator — model proposes, code applies with protections."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import kb as kbmod

log = logging.getLogger("daftar.curator")

TOPIC_SOFT_CAP = 10_000
PROTECT_TAG = re.compile(r"\[owner\]", re.I)
TIMEOUT = 420

PROMPT = """You are curating a personal knowledge topic file titled «{title}».
Each line below is numbered [N]. Lines marked 🔒 are protected: never merge or retire them.

Goal: keep the file useful and compact without losing still-valid information.
- merge: two or more lines saying the same thing → one clear English line with all important details.
- retire: superseded, obsolete, or a trap that was permanently fixed. Require a clear reason.
- move: line belongs in another topic from: {slugs}.
- propose_core: catastrophic-if-forgotten rule that may belong in core.md (rare).
- propose_rule: stable standing rule that may belong in shared rules (rare).
If nothing to do, return empty lists. Do not invent facts.

Return JSON only, no prose:
{{"merge":[{{"lines":[N,M],"text":"merged line"}}],"retire":[{{"line":N,"why":"..."}}],"move":[{{"line":N,"to":"slug"}}],"propose_core":[{{"line":N,"why":"..."}}],"propose_rule":[{{"line":N,"why":"..."}}]}}

File:
{body}
"""


def _state_path(kb: Path) -> Path:
    return kb / ".curator_state.json"


def _retired_path(kb: Path) -> Path:
    p = kb / "_archive" / "retired.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _proposals_path(kb: Path) -> Path:
    return kb / "_proposals.md"


def _report_path(kb: Path) -> Path:
    p = kb / "_archive" / "curator_last_run.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def parse_json(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def ask_model(
    prompt: str,
    *,
    model: str,
    binary: str = "claude",
    runner: Optional[Callable[[str], str]] = None,
) -> Optional[dict]:
    if runner is not None:
        return parse_json(runner(prompt))
    try:
        p = subprocess.run(
            [
                binary,
                "-p",
                "--model",
                model,
                "--no-session-persistence",
                "--output-format",
                "text",
                "--allowedTools",
                "",
            ],
            # Prompt via stdin (not argv): no "-"-prefix parsing, no 128 KiB argv cap.
            # run(input=...) uses communicate(), so large prompts cannot deadlock.
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            cwd="/tmp",
            check=False,
        )
        if p.returncode == 0:
            return parse_json(p.stdout)
        log.warning("curator model failed rc=%s %s", p.returncode, (p.stderr or "")[:200])
    except Exception as e:
        log.warning("curator model error: %s", e)
    return None


def entries(text: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    section = ""
    for i, line in enumerate(text.splitlines()):
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("- "):
            out.append((i, line, section))
    return out


def is_protected(line: str, section: str) -> bool:
    return section.lower().startswith("owner guidance") or bool(PROTECT_TAG.search(line))


def curate_topic(
    kb: Path,
    slug: str,
    meta: dict[str, Any],
    *,
    dry: bool,
    report: list[str],
    proposals: list[str],
    model: str,
    runner: Optional[Callable[[str], str]] = None,
) -> None:
    path = kbmod.topic_path(kb, slug)
    text = path.read_text(encoding="utf-8")
    ents = entries(text)
    if len(ents) < 2:
        return
    numbered = []
    for n, (_, line, sec) in enumerate(ents, 1):
        lock = "🔒 " if is_protected(line, sec) else ""
        numbered.append(f"[{n}] {lock}{line[2:]}")
    prompt = PROMPT.format(
        title=meta["title"],
        slugs=", ".join(s for s in kbmod.load_topics(kb) if s != slug),
        body="\n".join(numbered),
    )
    ops = ask_model(prompt, model=model, runner=runner)
    if ops is None:
        report.append(f"- {slug}: model failed, deferred")
        return

    def ent(n: Any) -> Optional[tuple[int, str, str]]:
        return ents[n - 1] if isinstance(n, int) and 1 <= n <= len(ents) else None

    drop: set[int] = set()
    add_lines: list[str] = []
    moves: list[tuple[str, str]] = []
    retired_out: list[str] = []
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for m in ops.get("merge", []) or []:
        idx = [n for n in m.get("lines", []) if ent(n)]
        txt = (m.get("text") or "").strip().replace("\n", " ")
        txt = re.sub(r"^-?\s*\d{4}-\d{2}-\d{2}\s*(\[[^\]]*\])?\s*", "", txt)
        if len(idx) < 2 or len(txt) < 15:
            continue
        if any(is_protected(ent(n)[1], ent(n)[2]) for n in idx):  # type: ignore[index]
            continue
        if any(n in drop for n in idx):
            continue
        dates = [re.match(r"^- (\d{4}-\d{2}-\d{2})", ent(n)[1]) for n in idx]  # type: ignore[index]
        first = min((d.group(1) for d in dates if d), default=day)
        new = f"- {first} [curator] {txt}"
        add_lines.append(new)
        for n in idx:
            drop.add(n)
            retired_out.append(f"{ent(n)[1]}\n  ↳ merged {day} into: {txt[:80]}")  # type: ignore[index]

    for r in ops.get("retire", []) or []:
        n = r.get("line")
        why = (r.get("why") or "").strip()
        e = ent(n)
        if not e or n in drop or not why or is_protected(e[1], e[2]):
            continue
        drop.add(n)
        retired_out.append(f"{e[1]}\n  ↳ archived {day}: {why}")

    for mv in ops.get("move", []) or []:
        n, to = mv.get("line"), mv.get("to")
        e = ent(n)
        topics = kbmod.load_topics(kb)
        if not e or n in drop or to not in topics or to == slug:
            continue
        if e[2].lower().startswith("owner guidance"):
            continue
        drop.add(n)
        moves.append((to, e[1]))

    for key, label in (("propose_core", "for CORE"), ("propose_rule", "for rules")):
        for p in ops.get(key, []) or []:
            e = ent(p.get("line"))
            if e:
                proposals.append(
                    f"- [{slug}] {label}: {e[1][2:]}\n  ↳ why: {p.get('why', '')}"
                )

    report.append(
        f"- {slug}: merge ops {len(ops.get('merge') or [])} · "
        f"retire/merge lines {len(retired_out)} · moves {len(moves)}"
    )
    if dry or not (drop or add_lines or moves):
        for r in retired_out:
            report.append("  " + r.replace("\n", "\n  "))
        for to, line in moves:
            report.append(f"  move → {to}: {line[:100]}")
        for a in add_lines:
            report.append(f"  new: {a[:140]}")
        return

    # Lock only while writing, never while the model thinks, so `kb add` from a
    # live session never waits on the curator. If the file changed meanwhile the
    # plan is stale: skip it and retry next night.
    with kbmod.locked(kb):
        if path.read_text(encoding="utf-8") != text:
            report.append(f"  {slug}: changed during curation, retry next run")
            return
        drop_idx = {ent(n)[0] for n in drop}  # type: ignore[index]
        lines = [l for i, l in enumerate(text.splitlines()) if i not in drop_idx]
        body = "\n".join(lines).rstrip("\n") + "\n" + "".join(a + "\n" for a in add_lines)
        kbmod.write_atomic(path, body)
        for to, line in moves:
            tp = kbmod.topic_path(kb, to)
            kbmod.write_atomic(tp, tp.read_text(encoding="utf-8").rstrip("\n") + "\n" + line + "\n")
        if retired_out:
            with _retired_path(kb).open("a", encoding="utf-8") as fh:
                fh.write("\n".join(retired_out) + "\n")


def run_curator(
    *,
    workspace: Optional[Path] = None,
    dry_run: bool = False,
    full: bool = False,
    topic: Optional[str] = None,
    model: Optional[str] = None,
    runner: Optional[Callable[[str], str]] = None,
    notify: Optional[Callable[[str], None]] = None,
    now: Optional[datetime] = None,
) -> int:
    """``now`` (owner's local time) picks the full-run weekday; default: server clock."""
    kb = kbmod.knowledge_root(workspace)
    model = model or os.environ.get("CURATOR_MODEL") or "opus"
    full_weekday = int(os.environ.get("CURATOR_FULL_WEEKDAY", "4"))
    do_full = full or (now or datetime.now()).weekday() == full_weekday
    state = json.loads(_state_path(kb).read_text()) if _state_path(kb).exists() else {}
    last = state.get("last_run", "0000-00-00")
    topics = kbmod.load_topics(kb)
    todo: list[str] = []
    for slug in topics:
        if topic and slug != topic:
            continue
        new = [l for l in kbmod.lesson_lines(kb, slug) if l[2:12] >= last]
        if do_full or topic or new:
            todo.append(slug)

    report = [
        f"# Curator last run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"(dry={dry_run} full={do_full})"
    ]
    proposals: list[str] = []
    for slug in todo:
        curate_topic(
            kb,
            slug,
            topics[slug],
            dry=dry_run,
            report=report,
            proposals=proposals,
            model=model,
            runner=runner,
        )
    if not dry_run:
        with kbmod.locked(kb):
            kbmod.cmd_map(kb)

    big = [
        f"{s} ({kbmod.topic_path(kb, s).stat().st_size // 1000}KB)"
        for s in topics
        if kbmod.topic_path(kb, s).exists()
        and kbmod.topic_path(kb, s).stat().st_size > TOPIC_SOFT_CAP
    ]
    if big:
        report.append(f"- Topics over soft cap ({TOPIC_SOFT_CAP // 1000}KB): {', '.join(big)}")

    kbmod.write_atomic(_report_path(kb), "\n".join(report) + "\n")
    if proposals and not dry_run:
        with _proposals_path(kb).open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n## {datetime.now(timezone.utc):%Y-%m-%d}\n"
                + "\n".join(proposals)
                + "\n"
            )
        msg = (
            f"Nightly curator has {len(proposals)} proposal(s) for CORE or rules. "
            "Open knowledge/_proposals.md when ready."
        )
        if notify:
            notify(msg)
        else:
            print(msg)
    if not dry_run:
        state["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _state_path(kb).write_text(json.dumps(state))
    print("\n".join(report[1:6]))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    return run_curator(
        dry_run="--dry-run" in argv,
        full="--full" in argv,
        topic=argv[argv.index("--topic") + 1] if "--topic" in argv else None,
    )


if __name__ == "__main__":
    sys.exit(main())
