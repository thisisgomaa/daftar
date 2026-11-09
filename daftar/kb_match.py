"""Topic matching for Claude hooks and Codex bridge injection."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

MAX_CHARS = 9000
MAX_TOPICS = 2

INJECT_HEADER = (
    "⟨Knowledge: this work touches the topic below — it was opened for you. Follow it.⟩"
)


def load_topics_meta(kb_root: Path) -> dict:
    path = Path(kb_root) / "topics.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def match_topics(
    kb_root: Path,
    text: str,
    field: str,
    seen: Iterable[str],
    *,
    max_topics: int = MAX_TOPICS,
) -> list[str]:
    """Return topic slugs whose keywords/tools match *text*, skipping *seen*.

    ``field`` is ``\"keywords\"`` (user prompt) or ``\"tools\"`` (tool/command haystack).
    """
    topics = load_topics_meta(kb_root)
    if not topics:
        return []
    hay = (text or "").lower()
    already = set(seen)
    hits: list[str] = []
    for slug, meta in topics.items():
        if slug in already:
            continue
        keys = meta.get(field, []) or []
        if any(k and str(k).lower() in hay for k in keys):
            hits.append(slug)
        if len(hits) >= max_topics:
            break
    return hits[:max_topics]


def strip_reminder_brackets(text: str) -> str:
    return re.sub(r"⟨[^⟩]*⟩", " ", text or "")


def topic_bodies(
    kb_root: Path,
    slugs: list[str],
    *,
    max_chars: int = MAX_CHARS,
) -> list[tuple[str, str]]:
    """Load topic markdown bodies; truncate each to *max_chars*."""
    out: list[tuple[str, str]] = []
    kb = Path(kb_root)
    for slug in slugs:
        p = kb / "topics" / f"{slug}.md"
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        if len(txt) > max_chars:
            txt = txt[:max_chars] + f"\n… (rest of file: `daftar kb show {slug}`)"
        out.append((slug, txt))
    return out


def format_injection(bodies: list[tuple[str, str]], *, header: Optional[str] = None) -> str:
    if not bodies:
        return ""
    parts = [b for _, b in bodies]
    hdr = header if header is not None else INJECT_HEADER
    return hdr + "\n\n" + "\n\n".join(parts)
