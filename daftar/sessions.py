"""Session registry, rotation, and handoff papers."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import load_config, state_path, workspace_path
from .engine import BaseEngine, EngineResult

log = logging.getLogger("daftar.sessions")

LIST_LIMIT = 4
HANDOFF_PROMPT = (
    "Write a HANDOFF PAPER in markdown, at most 1200 words. Include: goal, "
    "decisions made, open items, files touched, and the concrete next step. "
    "No preamble — output only the paper."
)


@dataclass
class SessionRecord:
    id: str
    title: str = ""
    created: str = ""
    last_activity: str = ""
    last_input_tokens: int = 0
    paper: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "last_activity": self.last_activity,
            "last_input_tokens": self.last_input_tokens,
            "paper": self.paper,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionRecord":
        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            created=str(d.get("created") or ""),
            last_activity=str(d.get("last_activity") or ""),
            last_input_tokens=int(d.get("last_input_tokens") or 0),
            paper=str(d.get("paper") or ""),
        )


@dataclass
class AppState:
    offset: int = 0
    current_session_id: Optional[str] = None
    sessions: list[SessionRecord] = field(default_factory=list)
    model: str = ""
    last_nightly: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "offset": self.offset,
            "current_session_id": self.current_session_id,
            "sessions": [s.to_dict() for s in self.sessions],
            "model": self.model,
            "last_nightly": self.last_nightly,
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AppState":
        known = {"offset", "current_session_id", "sessions", "model", "last_nightly"}
        return cls(
            offset=int(d.get("offset") or 0),
            current_session_id=d.get("current_session_id"),
            sessions=[SessionRecord.from_dict(x) for x in (d.get("sessions") or [])],
            model=str(d.get("model") or ""),
            last_nightly=str(d.get("last_nightly") or ""),
            extra={k: v for k, v in d.items() if k not in known},
        )


_state_lock = threading.RLock()


def load_state() -> AppState:
    path = state_path()
    if not path.exists():
        return AppState()
    try:
        return AppState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state load failed: %s", e)
        return AppState()


def save_state(state: AppState) -> None:
    from .config import ensure_home

    ensure_home()
    path = state_path()
    tmp = path.with_suffix(".tmp")
    with _state_lock:
        tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _title_from(text: str, limit: int = 50) -> str:
    one = " ".join((text or "").strip().split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"


def sessions_dir(cfg: Optional[dict[str, str]] = None) -> Path:
    d = workspace_path(cfg) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcripts_dir(cfg: Optional[dict[str, str]] = None, workspace: Optional[Path] = None) -> Path:
    ws = Path(workspace) if workspace else workspace_path(cfg)
    d = ws / "sessions" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_transcript(
    workspace: Path,
    *,
    engine: str,
    session_id: str,
    user_text: str,
    reply_text: str,
    timezone_name: str = "UTC",
    when: Optional[datetime] = None,
) -> Path:
    """Append one delivered turn to the day's transcript file (append-only)."""
    from zoneinfo import ZoneInfo

    eng = (engine or "claude").strip().lower() or "claude"
    sid8 = (session_id or "unknown")[:8]
    try:
        tz = ZoneInfo(timezone_name) if timezone_name else ZoneInfo("UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    now = when or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    day = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")
    path = transcripts_dir(workspace=workspace) / f"{day}_{eng}_{sid8}.md"
    block = (
        f"### {hm} owner\n{(user_text or '').rstrip()}\n\n"
        f"### {hm} {eng}\n{(reply_text or '').rstrip()}\n\n"
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(block)
    return path


def upsert_session(
    state: AppState,
    sid: str,
    *,
    user_text: str = "",
    input_tokens: int = 0,
    paper: str = "",
) -> SessionRecord:
    now = _now_iso()
    for rec in state.sessions:
        if rec.id == sid:
            rec.last_activity = now
            if input_tokens:
                rec.last_input_tokens = input_tokens
            if paper:
                rec.paper = paper
            if user_text and not rec.title:
                rec.title = _title_from(user_text)
            return rec
    rec = SessionRecord(
        id=sid,
        title=_title_from(user_text) if user_text else sid[:8],
        created=now,
        last_activity=now,
        last_input_tokens=input_tokens,
        paper=paper,
    )
    state.sessions.insert(0, rec)
    # Keep full history on disk via papers; list trims for /sessions UI
    return rec


def list_for_ui(state: AppState) -> list[SessionRecord]:
    # Prefer most recently active; keep current first if present
    ordered = sorted(state.sessions, key=lambda s: s.last_activity, reverse=True)
    cur = state.current_session_id
    if cur:
        ordered = [s for s in ordered if s.id == cur] + [s for s in ordered if s.id != cur]
    return ordered[:LIST_LIMIT]


def write_handoff_paper(
    engine: BaseEngine,
    sid: str,
    *,
    summary_model: str = "sonnet",
    cfg: Optional[dict[str, str]] = None,
) -> Path:
    """Ask the current session to write a handoff paper; save under sessions/."""
    cfg = cfg or load_config()
    saved_model = engine.model
    engine.model = summary_model or engine.model
    try:
        res = engine.run(HANDOFF_PROMPT, resume=sid)
    finally:
        engine.model = saved_model

    text = (res.text or "").strip()
    if not text:
        text = (
            "# Handoff paper\n\n"
            f"(Session `{sid[:8]}` ended without a model-written paper.)\n"
        )
    # Cap ~1200 words softly
    words = text.split()
    if len(words) > 1200:
        text = " ".join(words[:1200]) + "\n"

    day = datetime.now().strftime("%Y-%m-%d")
    path = sessions_dir(cfg) / f"{day}_{sid[:8]}.md"
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    log.info("handoff paper saved %s (%d chars)", path, len(text))
    return path


def maybe_rotate_after_turn(
    state: AppState,
    engine: BaseEngine,
    result: EngineResult,
    *,
    rotate_tokens: int,
    summary_model: str,
    cfg: Optional[dict[str, str]] = None,
    force: bool = False,
) -> Optional[str]:
    """If context exceeds threshold (or force), write paper and clear current sid.

    Returns path to paper when rotation happened, else None.
    The caller should deliver the user reply BEFORE calling this.
    A subsequent turn with resume=None starts fresh; pass paper as first prompt.
    """
    sid = result.session_id or state.current_session_id
    if not sid:
        return None
    ctx = result.context_tokens
    # Codex compacts its own context, and its turn usage is summed over every
    # model call, so size-based rotation would fire too early. Opt-in only.
    if (
        not force
        and getattr(result, "engine", "") == "codex"
        and (cfg or {}).get("ROTATE_CODEX", "no").lower() not in ("yes", "1", "true")
    ):
        return None
    if not force and ctx <= rotate_tokens:
        return None

    log.info("rotation: sid=%s ctx=%s force=%s", sid[:8], ctx, force)
    paper_path = write_handoff_paper(
        engine, sid, summary_model=summary_model, cfg=cfg
    )
    upsert_session(state, sid, input_tokens=ctx, paper=str(paper_path))
    # Clear current so next turn is fresh; stash pending seed in state.extra
    state.extra["pending_seed"] = (
        paper_path.read_text(encoding="utf-8")
        + "\n\nContinue from here."
    )
    state.current_session_id = None
    save_state(state)
    return str(paper_path)


def start_fresh(state: AppState) -> None:
    state.current_session_id = None
    state.extra.pop("pending_seed", None)
    save_state(state)


def switch_session(state: AppState, sid: str) -> bool:
    for rec in state.sessions:
        if rec.id == sid:
            state.current_session_id = sid
            state.extra.pop("pending_seed", None)
            save_state(state)
            return True
    return False


def consume_pending_seed(state: AppState) -> Optional[str]:
    seed = state.extra.pop("pending_seed", None)
    if seed:
        save_state(state)
    return seed


def find_sessions_without_paper(state: AppState, max_age_hours: float = 6.0) -> list[SessionRecord]:
    """Sessions that look inactive and lack a paper (for nightly)."""
    from datetime import datetime as dt

    out: list[SessionRecord] = []
    now = dt.now(timezone.utc)
    for rec in state.sessions:
        if rec.paper:
            continue
        if not rec.id or rec.id == state.current_session_id:
            continue
        try:
            last = dt.strptime(rec.last_activity, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        age_h = (now - last).total_seconds() / 3600.0
        if age_h >= max_age_hours:
            out.append(rec)
    return out
