"""Knowledge base: topics, MAP, deduped lesson adds."""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

DUP_RATIO = 0.82
LESSON = re.compile(r"^- \d{4}-\d{2}-\d{2} ")

SYSTEM_LINES = [
    "Long-term memory lives in `knowledge/`: one file per topic; this MAP is the index.",
    "Before work that touches a topic in the index, open its file (`daftar kb show <topic>`). Do not rely on memory alone.",
    'Learned something durable, or got owner guidance? Record it immediately: `daftar kb add <topic> "one-line lesson" --by <name>`.',
    "The tool rejects near-duplicates and regenerates the MAP. A nightly curator merges and archives; nothing is deleted.",
    "The CORE section below always applies — you do not need to open anything for it.",
]


def knowledge_root(workspace: Optional[Path] = None) -> Path:
    if workspace is not None:
        return Path(workspace) / "knowledge"
    env = os.environ.get("DAFTAR_KB", "").strip()
    if env:
        return Path(env)
    # Prefer cwd/knowledge when present (Claude Code workspace)
    cwd_kb = Path.cwd() / "knowledge"
    if (cwd_kb / "topics.json").exists():
        return cwd_kb
    from .config import workspace_path

    return workspace_path() / "knowledge"


def _paths(kb: Path) -> dict[str, Path]:
    return {
        "kb": kb,
        "topics_json": kb / "topics.json",
        "topics_dir": kb / "topics",
        "core": kb / "core.md",
        "map": kb / "MAP.md",
        "lock": kb / ".lock",
    }


@contextmanager
def locked(kb: Path) -> Iterator[None]:
    paths = _paths(kb)
    paths["lock"].touch(exist_ok=True)
    try:
        import fcntl

        with open(paths["lock"], "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except ImportError:
        # Windows without fcntl — best-effort
        yield


def load_topics(kb: Path) -> dict[str, Any]:
    return json.loads(_paths(kb)["topics_json"].read_text(encoding="utf-8"))


def topic_path(kb: Path, slug: str) -> Path:
    return _paths(kb)["topics_dir"] / f"{slug}.md"


def entry_count(kb: Path, slug: str) -> int:
    p = topic_path(kb, slug)
    if not p.exists():
        return 0
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("- "))


def lesson_lines(kb: Path, slug: str) -> list[str]:
    p = topic_path(kb, slug)
    if not p.exists():
        return []
    return [l for l in p.read_text(encoding="utf-8").splitlines() if LESSON.match(l)]


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _norm(s: str) -> str:
    s = re.sub(r"^- \d{4}-\d{2}-\d{2} \[[^\]]*\]\s*", "", s.strip())
    s = re.sub(r"^- ", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def find_similar(kb: Path, text: str) -> tuple[float, Optional[str], Optional[str]]:
    n = _norm(text)
    best: tuple[float, Optional[str], Optional[str]] = (0.0, None, None)
    for slug in load_topics(kb):
        p = topic_path(kb, slug)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.startswith("- "):
                continue
            r = difflib.SequenceMatcher(None, n, _norm(line)).ratio()
            if r > best[0]:
                best = (r, slug, line)
    return best


def render_topic_header(slug: str, meta: dict[str, Any]) -> str:
    return (
        f"# {meta['title']}\n"
        f"> Open when: {meta['when']}\n"
        f'> New lesson: `daftar kb add {slug} "one-line lesson" --by <name>`\n'
    )


def render_map(kb: Path) -> str:
    topics = load_topics(kb)
    core_path = _paths(kb)["core"]
    core = []
    if core_path.exists():
        core = [l for l in core_path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    out = ["# Knowledge map", ""]
    out += [f"- {s}" for s in SYSTEM_LINES]
    out += ["", "## CORE (always applies)"]
    out += core
    out += ["", "## INDEX — open the file when work touches the topic"]
    for slug, meta in topics.items():
        out.append(
            f"- **{meta['title']}** ({entry_count(kb, slug)}): {meta['when']} "
            f"← `knowledge/topics/{slug}.md`"
        )
    return "\n".join(out) + "\n"


def cmd_map(kb: Path) -> str:
    m = render_map(kb)
    write_atomic(_paths(kb)["map"], m)
    return m


def cmd_add(
    kb: Path,
    slug: str,
    text: str,
    *,
    by: str = "unknown",
    force: bool = False,
) -> tuple[int, str]:
    topics = load_topics(kb)
    if slug not in topics:
        return 2, f"Unknown topic: {slug}\nAvailable: {', '.join(topics)}"
    text = " ".join(text.strip().split())
    if len(text) < 15:
        return 2, "Lesson too short — write a clear sentence."
    with locked(kb):
        ratio, sslug, sline = find_similar(kb, text)
        if ratio >= DUP_RATIO and not force:
            return 1, (
                f"Near-duplicate ({ratio:.0%}) of a lesson in {sslug}:\n{sline}\n"
                "If it is genuinely different, pass --force."
            )
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        line = f"- {day} [{by}] {text}"
        p = topic_path(kb, slug)
        if not p.exists():
            write_atomic(p, render_topic_header(slug, topics[slug]) + "\n## Lessons\n")
        body = p.read_text(encoding="utf-8").rstrip("\n") + "\n" + line + "\n"
        write_atomic(p, body)
        cmd_map(kb)
    return 0, f"Recorded in {slug}: {line}"


def cmd_new_topic(kb: Path, slug: str, title: str, when: str) -> tuple[int, str]:
    slug = slug.strip().lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        return 2, "Slug must be lowercase letters, digits, _ or -"
    with locked(kb):
        topics = load_topics(kb)
        if slug in topics:
            return 1, f"Topic already exists: {slug}"
        topics[slug] = {
            "title": title,
            "when": when,
            "keywords": [],
            "tools": [],
        }
        write_atomic(
            _paths(kb)["topics_json"],
            json.dumps(topics, indent=2, ensure_ascii=False) + "\n",
        )
        write_atomic(
            topic_path(kb, slug),
            render_topic_header(slug, topics[slug]) + "\n## Owner guidance\n\n## Lessons\n",
        )
        cmd_map(kb)
    return 0, f"Created topic {slug}"


def cmd_topics(kb: Path) -> str:
    lines = []
    for slug, meta in load_topics(kb).items():
        lines.append(f"{slug:28} {entry_count(kb, slug):4}  {meta['title']}")
    return "\n".join(lines)


def cmd_show(kb: Path, slug: str) -> str:
    p = topic_path(kb, slug)
    if not p.exists():
        return f"No topic file for {slug}"
    return p.read_text(encoding="utf-8")


def cmd_find(kb: Path, query: str) -> str:
    qn = _norm(query)
    hits = []
    for slug in load_topics(kb):
        p = topic_path(kb, slug)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if qn in _norm(line):
                hits.append(f"[{slug}] {line}")
    return "\n".join(hits) if hits else "(no matches)"


def cmd_check(kb: Path) -> tuple[int, str]:
    bad = 0
    msgs: list[str] = []
    topics = load_topics(kb)
    tdir = _paths(kb)["topics_dir"]
    for slug in topics:
        if not topic_path(kb, slug).exists():
            msgs.append(f"Missing file: {slug}")
            bad += 1
    if tdir.exists():
        for p in tdir.glob("*.md"):
            if p.stem not in topics:
                msgs.append(f"Orphan file not in index: {p.name}")
                bad += 1
    map_path = _paths(kb)["map"]
    if not map_path.exists() or map_path.read_text(encoding="utf-8") != render_map(kb):
        msgs.append("MAP.md is stale (run: daftar kb map)")
        bad += 1
    msgs.append("ok" if not bad else f"{bad} issue(s)")
    return (1 if bad else 0), "\n".join(msgs)


def main(argv: Optional[list[str]] = None, workspace: Optional[Path] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    kb = knowledge_root(workspace)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(
            "daftar kb: add | show | find | topics | map | check | new-topic\n"
            '  add <topic> "lesson" [--by name] [--force]\n'
            '  new-topic <slug> "<title>" "<when>"'
        )
        return 0
    cmd = argv[0]
    if cmd == "add":
        by, force = "unknown", False
        rest: list[str] = []
        i = 1
        while i < len(argv):
            a = argv[i]
            if a == "--by" and i + 1 < len(argv):
                by = argv[i + 1]
                i += 2
                continue
            if a == "--force":
                force = True
                i += 1
                continue
            rest.append(a)
            i += 1
        if len(rest) < 2:
            print('Usage: daftar kb add <topic> "lesson" [--by name] [--force]')
            return 2
        code, msg = cmd_add(kb, rest[0], " ".join(rest[1:]), by=by, force=force)
        print(msg)
        return code
    if cmd == "topics":
        print(cmd_topics(kb))
        return 0
    if cmd == "show" and len(argv) > 1:
        print(cmd_show(kb, argv[1]))
        return 0
    if cmd == "find" and len(argv) > 1:
        print(cmd_find(kb, " ".join(argv[1:])))
        return 0
    if cmd == "map":
        with locked(kb):
            m = cmd_map(kb)
        # Keep Codex AGENTS.md in sync when the workspace uses it
        try:
            ws = kb.parent
            if (ws / "AGENTS.md").exists() or (ws / "rules" / "BASE_RULES.md").exists():
                from .agents_md import ensure_agents_md

                ensure_agents_md(ws, force=True)
        except Exception:
            pass
        print(f"map ok ({len(m.encode())} bytes)")
        return 0
    if cmd == "check":
        code, msg = cmd_check(kb)
        print(msg)
        return code
    if cmd == "new-topic" and len(argv) >= 4:
        code, msg = cmd_new_topic(kb, argv[1], argv[2], " ".join(argv[3:]))
        print(msg)
        return code
    print("Unknown kb command. Try: daftar kb --help")
    return 2


if __name__ == "__main__":
    sys.exit(main())
