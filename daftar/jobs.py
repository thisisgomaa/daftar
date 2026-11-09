"""Cross-profile job delegation: queue, worker helpers, result delivery."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import ensure_home, jobs_dir, profile_home, profile_label

log = logging.getLogger("daftar.jobs")

_queue_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_jobs_dirs(profile: Optional[str] = None) -> dict[str, Path]:
    home = ensure_home(profile)
    root = home / "jobs"
    dirs = {
        "root": root,
        "queue": root / "queue",
        "running": root / "running",
        "done": root / "done",
        "results": root / "results",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def enqueue(
    to_profile: str,
    *,
    title: str,
    brief: str,
    requester: Optional[str] = None,
    attachments: Optional[list[str]] = None,
) -> str:
    """Write a job into the target profile queue. Returns job id."""
    to = profile_label(to_profile)
    req = profile_label(requester)
    job_id = uuid.uuid4().hex
    dirs = ensure_jobs_dirs(to)
    job = {
        "id": job_id,
        "title": title,
        "brief": brief,
        "requester": req,
        "worker": to,
        "attachments": [str(a) for a in (attachments or [])],
        "created": _now(),
        "status": "queued",
    }
    dest = dirs["queue"] / f"{job_id}.json"
    with _queue_lock:
        _atomic_write_json(dest, job)
    log.info("job queued id=%s to=%s from=%s", job_id[:8], to, req)
    return job_id


def claim_next(profile: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Atomically move the oldest queued job to running/. Returns job dict or None."""
    dirs = ensure_jobs_dirs(profile)
    with _queue_lock:
        queued = sorted(dirs["queue"].glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in queued:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            running = dirs["running"] / path.name
            try:
                os.replace(path, running)
            except OSError:
                continue
            data["status"] = "running"
            data["started"] = _now()
            _atomic_write_json(running, data)
            return data
    return None


def finish_job(
    job: dict[str, Any],
    *,
    status: str,
    result_dir: str,
    summary: str = "",
    error: str = "",
) -> Path:
    """Move running → done on worker; drop result JSON in requester's results/."""
    worker = profile_label(job.get("worker"))
    requester = profile_label(job.get("requester"))
    job_id = job["id"]
    dirs = ensure_jobs_dirs(worker)
    running = dirs["running"] / f"{job_id}.json"
    done_rec = dict(job)
    done_rec["status"] = status
    done_rec["finished"] = _now()
    done_rec["result_dir"] = result_dir
    done_rec["summary"] = summary
    done_rec["error"] = error
    done_path = dirs["done"] / f"{job_id}.json"
    _atomic_write_json(done_path, done_rec)
    if running.exists():
        running.unlink(missing_ok=True)

    result = {
        "id": job_id,
        "title": job.get("title") or "",
        "requester": requester,
        "worker": worker,
        "status": status,
        "result_dir": result_dir,
        "summary": summary,
        "error": error,
        "finished": done_rec["finished"],
    }
    req_dirs = ensure_jobs_dirs(requester)
    result_path = req_dirs["results"] / f"{job_id}.json"
    _atomic_write_json(result_path, result)
    return result_path


def list_pending_results(profile: Optional[str] = None) -> list[dict[str, Any]]:
    dirs = ensure_jobs_dirs(profile)
    out: list[dict[str, Any]] = []
    for path in sorted(dirs["results"].glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def consume_result(profile: Optional[str], job_id: str) -> None:
    """Move a processed result JSON into done/ under the requester home."""
    dirs = ensure_jobs_dirs(profile)
    src = dirs["results"] / f"{job_id}.json"
    if not src.exists():
        return
    dest = dirs["done"] / f"result_{job_id}.json"
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"id": job_id}
    data["consumed"] = _now()
    _atomic_write_json(dest, data)
    src.unlink(missing_ok=True)


def list_jobs(profile: Optional[str] = None) -> list[dict[str, Any]]:
    """Queued / running / done jobs for a profile (as worker)."""
    dirs = ensure_jobs_dirs(profile)
    rows: list[dict[str, Any]] = []
    for status, folder in (
        ("queued", dirs["queue"]),
        ("running", dirs["running"]),
        ("done", dirs["done"]),
    ):
        for path in sorted(folder.glob("*.json")):
            if path.name.startswith("result_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            data.setdefault("status", status)
            data["_path"] = str(path)
            rows.append(data)
    return rows


def format_jobs_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no jobs)"
    lines = []
    for j in rows:
        lines.append(
            f"{j.get('status', '?'):8} {str(j.get('id', ''))[:12]:12} "
            f"{j.get('title', '')[:40]}  "
            f"from={j.get('requester', '')} → {j.get('worker', '')}"
        )
    return "\n".join(lines)


def run_job_in_workspace(
    job: dict[str, Any],
    *,
    workspace: Path,
    engine,
) -> tuple[str, str, str]:
    """Execute a delegated job in WORKSPACE/delegations/<id>/.

    Returns (status, result_dir, summary_or_error).
    Never messages the owner.
    """
    job_id = job["id"]
    work = Path(workspace) / "delegations" / job_id
    work.mkdir(parents=True, exist_ok=True)

    for att in job.get("attachments") or []:
        src = Path(att)
        if src.is_file():
            try:
                shutil.copy2(src, work / src.name)
            except OSError as e:
                log.warning("attach copy failed %s: %s", src, e)

    brief = (job.get("brief") or "").strip()
    prompt = (
        f"{brief}\n\n"
        "Save deliverables in this folder; finish with a short summary."
    )

    # Snapshot images already in the folder
    before = {p.resolve() for p in work.rglob("*") if p.is_file()}

    # Temporarily point engine cwd at the delegation folder
    saved_cwd = getattr(engine, "cwd", None)
    try:
        engine.cwd = work
        result = engine.run(prompt, resume=None)
    finally:
        if saved_cwd is not None:
            engine.cwd = saved_cwd

    summary = (result.text or "").strip()
    status = "failed" if (result.is_error and not summary) or result.cancelled else "ok"
    if result.is_error and result.error and status == "failed":
        err = result.error
    else:
        err = result.error if status == "failed" else ""

    # Persist reply + any new images from engine
    (work / "RESULT.md").write_text(
        (summary or f"(failed: {err or 'unknown'})") + "\n",
        encoding="utf-8",
    )
    for img in getattr(result, "images", None) or []:
        try:
            dest = work / Path(img).name
            if Path(img).resolve() != dest.resolve():
                shutil.copy2(img, dest)
        except OSError:
            log.exception("copy job image failed")

    # Also pick up files the agent wrote into the folder
    after = [p for p in work.rglob("*") if p.is_file()]
    _ = before, after  # available for debugging

    if status == "failed":
        return status, str(work), err or summary or "job failed"
    return "ok", str(work), summary[:2000]


def review_prompt(result: dict[str, Any]) -> str:
    status = result.get("status") or "ok"
    title = result.get("title") or ""
    worker = result.get("worker") or ""
    folder = result.get("result_dir") or ""
    jid = result.get("id") or ""
    if status == "failed":
        err = result.get("error") or "unknown error"
        return (
            f"[system] Delegated job {jid} ({title}) failed on {worker}. "
            f"Error: {err}. Results folder (if any): {folder}. "
            "Tell the owner what went wrong."
        )
    return (
        f"[system] Delegated job {jid} ({title}) finished by {worker}. "
        f"Results: {folder}. Review them and deliver to the owner."
    )
