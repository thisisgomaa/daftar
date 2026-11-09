"""Markdown → Telegram HTML conversion and fence-aware message splitting."""

from __future__ import annotations

import html
import re

TG_MAX = 3900

_FENCE_RE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```|```([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def md_to_html(text: str) -> str:
    """Convert a subset of Markdown to Telegram HTML.

    Supports bold, italic, inline code, fenced code, links, and headers.
    Everything else is HTML-escaped.
    """
    if not text:
        return ""

    placeholders: dict[str, str] = {}
    counter = 0

    def ph(value: str) -> str:
        nonlocal counter
        key = f"\x00PH{counter}\x00"
        counter += 1
        placeholders[key] = value
        return key

    # Protect fenced code blocks first
    def protect_fence(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            lang = (m.group(1) or "").strip()
            body = m.group(2) or ""
        else:
            lang = ""
            body = m.group(3) or ""
        attrs = f' class="language-{escape_html(lang)}"' if lang else ""
        return ph(f"<pre><code{attrs}>{escape_html(body)}</code></pre>")

    work = _FENCE_RE.sub(protect_fence, text)

    def protect_inline(m: re.Match[str]) -> str:
        return ph(f"<code>{escape_html(m.group(1))}</code>")

    work = _INLINE_CODE_RE.sub(protect_inline, work)

    def protect_link(m: re.Match[str]) -> str:
        label = escape_html(m.group(1))
        url = m.group(2).replace('"', "%22")
        return ph(f'<a href="{escape_html(url)}">{label}</a>')

    work = _LINK_RE.sub(protect_link, work)

    work = _HEADER_RE.sub(lambda m: ph(f"<b>{escape_html(m.group(1).strip())}</b>"), work)

    def bold_sub(m: re.Match[str]) -> str:
        inner = m.group(1) or m.group(2) or ""
        return ph(f"<b>{escape_html(inner)}</b>")

    work = _BOLD_RE.sub(bold_sub, work)

    def italic_sub(m: re.Match[str]) -> str:
        inner = m.group(1) or m.group(2) or ""
        return ph(f"<i>{escape_html(inner)}</i>")

    work = _ITALIC_RE.sub(italic_sub, work)

    # Escape remaining plain text carefully around placeholders
    parts = re.split(r"(\x00PH\d+\x00)", work)
    out: list[str] = []
    for part in parts:
        if part in placeholders:
            out.append(placeholders[part])
        else:
            out.append(escape_html(part))
    return "".join(out)


def split_message(text: str, limit: int = TG_MAX) -> list[str]:
    """Split text into chunks ≤ limit without cutting inside ``` fences."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    segments = _segment_fences(text)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for is_code, seg in segments:
        if is_code:
            if len(seg) <= limit:
                if current and len(current) + len(seg) > limit:
                    flush()
                if len(seg) > limit:
                    flush()
                    chunks.extend(_split_code_block(seg, limit))
                else:
                    current += seg
            else:
                flush()
                chunks.extend(_split_code_block(seg, limit))
        else:
            remaining = seg
            while remaining:
                room = limit - len(current)
                if room <= 0:
                    flush()
                    room = limit
                if len(remaining) <= room:
                    current += remaining
                    remaining = ""
                else:
                    cut = _best_cut(remaining, room)
                    if cut <= 0:
                        if current:
                            flush()
                            continue
                        cut = room
                    current += remaining[:cut]
                    flush()
                    remaining = remaining[cut:].lstrip("\n")
    flush()
    return [c for c in chunks if c]


def _segment_fences(text: str) -> list[tuple[bool, str]]:
    parts: list[tuple[bool, str]] = []
    pos = 0
    n = len(text)
    while pos < n:
        a = text.find("```", pos)
        if a < 0:
            tail = text[pos:]
            if tail:
                parts.append((False, tail))
            break
        if a > pos:
            parts.append((False, text[pos:a]))
        b = text.find("```", a + 3)
        if b < 0:
            parts.append((True, text[a:].rstrip() + "\n```"))
            break
        parts.append((True, text[a : b + 3]))
        pos = b + 3
    return parts


def _split_code_block(block: str, limit: int) -> list[str]:
    lines = block.splitlines()
    opener = lines[0] if lines and lines[0].startswith("```") else "```"
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    overhead = len(opener) + 5
    for ln in body:
        if cur and cur_len + len(ln) + 1 + overhead > limit:
            out.append(opener + "\n" + "\n".join(cur) + "\n```")
            cur, cur_len = [], 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        out.append(opener + "\n" + "\n".join(cur) + "\n```")
    return out or [block]


def _best_cut(text: str, room: int) -> int:
    if room >= len(text):
        return len(text)
    window = text[:room]
    for sep in ("\n\n", "\n", " "):
        idx = window.rfind(sep)
        if idx >= max(room // 3, 1):
            return idx + len(sep) if sep != " " else idx + 1
    return room
