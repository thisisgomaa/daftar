"""Project status pages derived from session transcripts."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .curator import parse_json

log = logging.getLogger("daftar.projects")

TIMEOUT = 420
SUMMARY_PROMPT = """You summarize one assistant session transcript for a personal notebook.

Existing projects (slug — title):
{project_list}

Read the transcript below. Return JSON only, no prose:
{{"projects":[{{"slug":"existing-slug-or-empty","new_title":"","entry":"2-5 line log entry","status":"updated where-it-stands text or empty"}}],"decisions":[],"open_items":[]}}

Rules:
- Prefer an existing slug when the talk clearly continues that project.
- To create a project, leave slug empty and set new_title.
- entry is required for every projects[] item you include (2-5 lines, plain text).
- status is the short current-state page; omit or leave empty when unchanged.
- decisions / open_items are short strings; empty lists are fine.
- Never invent projects from chatter that is not real work.

Transcript file: {transcript_name}
Engine: {engine}
Session: {session_id}

---
{body}
"""


def projects_root(workspace: Path) -> Path:
    d = Path(workspace) / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slugify(title: str) -> str:
    s = (title or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:64] or "project"


def list_projects(workspace: Path) -> list[dict[str, str]]:
    """Return [{slug, title, status_line, updated}] for each project dir."""
    root = projects_root(workspace)
    out: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        status_path = child / "STATUS.md"
        title = child.name
        status_line = ""
        updated = ""
        if status_path.exists():
            text = status_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip() or title
                elif line.lower().startswith("last updated:"):
                    updated = line.split(":", 1)[1].strip()
                elif line.startswith("> "):
                    status_line = line[2:].strip()
            if not status_line:
                # First non-heading, non-meta paragraph line
                for line in text.splitlines():
                    t = line.strip()
                    if not t or t.startswith("#") or t.lower().startswith("last updated"):
                        continue
                    if t.startswith(">"):
                        status_line = t.lstrip("> ").strip()
                    else:
                        status_line = t
                    break
        out.append(
            {
                "slug": child.name,
                "title": title,
                "status_line": status_line,
                "updated": updated,
            }
        )
    return out


def ensure_project(workspace: Path, slug: str, title: str = "") -> Path:
    slug = slugify(slug) if slug else slugify(title or "project")
    d = projects_root(workspace) / slug
    d.mkdir(parents=True, exist_ok=True)
    status = d / "STATUS.md"
    log_path = d / "LOG.md"
    if not status.exists():
        name = title.strip() or slug.replace("-", " ").title()
        status.write_text(
            f"# {name}\n\nLast updated: (never)\n\n> (no status yet)\n",
            encoding="utf-8",
        )
    if not log_path.exists():
        log_path.write_text(f"# Log — {title.strip() or slug}\n\n", encoding="utf-8")
    return d


def append_log(
    workspace: Path,
    slug: str,
    entry: str,
    *,
    date: str,
    engine: str = "",
    session_id: str = "",
) -> None:
    d = ensure_project(workspace, slug)
    path = d / "LOG.md"
    meta = date
    if engine:
        meta += f" · {engine}"
    if session_id:
        meta += f" · `{session_id[:8]}`"
    body = (entry or "").rstrip() + "\n"
    block = f"## {meta}\n\n{body}\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)


def write_status(workspace: Path, slug: str, status: str, *, title: str = "", date: str = "") -> None:
    d = ensure_project(workspace, slug, title=title)
    path = d / "STATUS.md"
    existing_title = title
    if path.exists() and not existing_title:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                existing_title = line[2:].strip()
                break
    name = existing_title or slug.replace("-", " ").title()
    day = date or datetime.now().strftime("%Y-%m-%d")
    text = (status or "").strip()
    path.write_text(
        f"# {name}\n\nLast updated: {day}\n\n{text}\n",
        encoding="utf-8",
    )


def regenerate_index(workspace: Path) -> Path:
    root = projects_root(workspace)
    rows = list_projects(workspace)
    lines = [
        "# Projects index",
        "",
        "One line per project. Read `projects/<slug>/STATUS.md` for the current state.",
        "",
    ]
    if not rows:
        lines.append("(no projects yet)")
    else:
        for r in rows:
            updated = r["updated"] or "?"
            status = r["status_line"] or "(no status)"
            # One-line status
            status = " ".join(status.split())
            if len(status) > 120:
                status = status[:117] + "…"
            lines.append(f"- **{r['title']}** (`{r['slug']}`) · {updated} — {status}")
    lines.append("")
    dest = root / "INDEX.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def _state_path(workspace: Path) -> Path:
    return projects_root(workspace) / ".summary_state.json"


def load_summary_state(workspace: Path) -> dict[str, Any]:
    path = _state_path(workspace)
    if not path.exists():
        return {"processed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"processed": {}}
        data.setdefault("processed", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"processed": {}}


def save_summary_state(workspace: Path, state: dict[str, Any]) -> None:
    path = _state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def _parse_transcript_name(name: str) -> tuple[str, str, str]:
    """Return (date, engine, sid8) from YYYY-MM-DD_<engine>_<sid8>.md."""
    stem = name[:-3] if name.endswith(".md") else name
    parts = stem.split("_", 2)
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def transcripts_for_date(workspace: Path, date: str) -> list[Path]:
    d = Path(workspace) / "sessions" / "transcripts"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"{date}_*.md") if p.is_file())


def ask_summary_model(
    prompt: str,
    *,
    preferred_engine: str = "claude",
    model: str = "sonnet",
    runner: Optional[Callable[[str], str]] = None,
) -> Optional[dict]:
    """One headless model call; prefer bridge engine, fall back to the other."""
    if runner is not None:
        return parse_json(runner(prompt))

    preferred = (preferred_engine or "claude").strip().lower()
    order = [preferred]
    other = "codex" if preferred == "claude" else "claude"
    if other not in order:
        order.append(other)

    for eng in order:
        if eng == "claude":
            if not shutil.which("claude"):
                continue
            text = _run_claude(prompt, model=model)
        else:
            if not shutil.which("codex"):
                continue
            text = _run_codex(prompt, model=model)
        if text is None:
            continue
        parsed = parse_json(text)
        if parsed is not None:
            return parsed
        log.warning("summary model (%s) returned non-JSON", eng)
    return None


def _run_claude(prompt: str, *, model: str) -> Optional[str]:
    try:
        p = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                model,
                "--no-session-persistence",
                "--output-format",
                "text",
                "--allowedTools",
                "",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd="/tmp",
            check=False,
        )
        if p.returncode == 0:
            return p.stdout
        log.warning("claude summary failed rc=%s %s", p.returncode, (p.stderr or "")[:200])
    except Exception as e:
        log.warning("claude summary error: %s", e)
    return None


def _run_codex(prompt: str, *, model: str) -> Optional[str]:
    try:
        with tempfile.TemporaryDirectory(prefix="daftar-summary-") as td:
            out = Path(td) / "out.txt"
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-s",
                "read-only",
                "-o",
                str(out),
            ]
            if model:
                cmd.extend(["-m", model])
            cmd.append("-")  # prompt on stdin
            p = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=td,
                check=False,
            )
            if out.exists() and out.stat().st_size > 0:
                return out.read_text(encoding="utf-8")
            if p.returncode != 0:
                log.warning(
                    "codex summary failed rc=%s %s",
                    p.returncode,
                    (p.stderr or "")[:200],
                )
    except Exception as e:
        log.warning("codex summary error: %s", e)
    return None


def apply_summary_result(
    workspace: Path,
    data: dict[str, Any],
    *,
    date: str,
    engine: str,
    session_id: str,
) -> int:
    """Validate model JSON and update project files. Returns entries appended."""
    existing = {p["slug"]: p for p in list_projects(workspace)}
    n = 0
    for item in data.get("projects") or []:
        if not isinstance(item, dict):
            continue
        slug = (item.get("slug") or "").strip()
        new_title = (item.get("new_title") or "").strip()
        entry = (item.get("entry") or "").strip()
        status = (item.get("status") or "").strip()
        if slug and slug not in existing:
            # Unknown slug: only create when new_title provided
            if not new_title:
                continue
            slug = slugify(new_title)
            ensure_project(workspace, slug, title=new_title)
            existing[slug] = {"slug": slug, "title": new_title}
        elif not slug:
            if not new_title:
                continue
            slug = slugify(new_title)
            ensure_project(workspace, slug, title=new_title)
            existing[slug] = {"slug": slug, "title": new_title}
        else:
            ensure_project(workspace, slug)
        if not entry:
            continue
        append_log(
            workspace,
            slug,
            entry,
            date=date,
            engine=engine,
            session_id=session_id,
        )
        n += 1
        if status:
            title = existing.get(slug, {}).get("title") or new_title
            write_status(workspace, slug, status, title=title, date=date)
    regenerate_index(workspace)
    return n


def summarize_day(
    workspace: Path,
    date: str,
    *,
    preferred_engine: str = "claude",
    model: str = "sonnet",
    runner: Optional[Callable[[str], str]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process transcripts for date. Idempotent via size+mtime fingerprints."""
    ws = Path(workspace)
    state = load_summary_state(ws)
    processed: dict[str, str] = dict(state.get("processed") or {})
    files = transcripts_for_date(ws, date)
    report: dict[str, Any] = {
        "date": date,
        "transcripts": len(files),
        "processed": 0,
        "skipped": 0,
        "entries": 0,
        "errors": [],
    }
    project_list = list_projects(ws)
    plist = (
        "\n".join(f"- {p['slug']} — {p['title']}" for p in project_list)
        if project_list
        else "(none yet)"
    )

    for path in files:
        key = str(path.relative_to(ws)) if path.is_relative_to(ws) else str(path)
        fp = _fingerprint(path)
        if processed.get(key) == fp:
            report["skipped"] += 1
            continue
        _date, engine, sid8 = _parse_transcript_name(path.name)
        body = path.read_text(encoding="utf-8")
        prompt = SUMMARY_PROMPT.format(
            project_list=plist,
            transcript_name=path.name,
            engine=engine or preferred_engine,
            session_id=sid8,
            body=body[:80_000],
        )
        if dry_run:
            report["processed"] += 1
            continue
        data = ask_summary_model(
            prompt,
            preferred_engine=preferred_engine,
            model=model,
            runner=runner,
        )
        if data is None:
            report["errors"].append(path.name)
            continue
        n = apply_summary_result(
            ws,
            data,
            date=date or _date,
            engine=engine or preferred_engine,
            session_id=sid8,
        )
        report["entries"] += n
        report["processed"] += 1
        processed[key] = fp
        # Refresh list for subsequent transcripts
        project_list = list_projects(ws)
        plist = (
            "\n".join(f"- {p['slug']} — {p['title']}" for p in project_list)
            if project_list
            else "(none yet)"
        )

    if not dry_run:
        state["processed"] = processed
        save_summary_state(ws, state)
        regenerate_index(ws)
    return report


def index_text(workspace: Path) -> str:
    path = projects_root(workspace) / "INDEX.md"
    if not path.exists():
        regenerate_index(workspace)
    return path.read_text(encoding="utf-8")


def show_project(workspace: Path, slug: str) -> Optional[str]:
    d = projects_root(workspace) / slug
    status = d / "STATUS.md"
    if not status.exists():
        return None
    return status.read_text(encoding="utf-8")


def try_workspace_step_lock(workspace: Path, step: str, date: str) -> bool:
    """Exclusive nightly step lock in WORKSPACE. True if we acquired it."""
    lock_dir = Path(workspace) / ".daftar_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"nightly_{step}_{date}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"{os.getpid()}\n{datetime.now().isoformat()}\n".encode())
    finally:
        os.close(fd)
    return True
