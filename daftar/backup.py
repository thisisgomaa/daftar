"""Workspace backups: archive → verify → only then prune."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import daftar_home, get_int, load_config, workspace_path

log = logging.getLogger("daftar.backup")


def backups_dir(home: Optional[Path] = None) -> Path:
    d = (home or daftar_home()) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _should_exclude(
    arcname: str,
    full: Path,
    *,
    max_inbox_bytes: int,
) -> bool:
    parts = Path(arcname).parts
    if "__pycache__" in parts or ".git" in parts:
        return True
    # outbox/sent/
    if len(parts) >= 2 and parts[0] == "outbox" and parts[1] == "sent":
        return True
    if parts and parts[0] == "outbox" and len(parts) >= 2 and parts[1] == "sent":
        return True
    # Also match workspace-prefixed names: workspace/outbox/sent/...
    for i, p in enumerate(parts):
        if p == "outbox" and i + 1 < len(parts) and parts[i + 1] == "sent":
            return True
        if p == "__pycache__" or p == ".git":
            return True
    # inbox files larger than max
    if full.is_file():
        for i, p in enumerate(parts):
            if p == "inbox" and i + 1 < len(parts):
                try:
                    if full.stat().st_size > max_inbox_bytes:
                        return True
                except OSError:
                    return True
                break
    return False


def create_archive(
    *,
    workspace: Path,
    dest_dir: Optional[Path] = None,
    include_config: bool = False,
    max_file_mb: int = 50,
    home: Optional[Path] = None,
    stamp: Optional[str] = None,
) -> tuple[Path, Path]:
    """Write tar.gz + .sha256. Returns (archive, sha_path). Does not prune."""
    home = home or daftar_home()
    dest_dir = dest_dir or backups_dir(home)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = dest_dir / f"daftar-{stamp}.tar.gz"
    max_bytes = max(1, max_file_mb) * 1024 * 1024

    ws = Path(workspace).resolve()
    state = home / "state.json"
    config = home / "config.env"

    with tarfile.open(archive, "w:gz") as tar:
        # Workspace tree
        if ws.exists():
            for root, dirs, files in os.walk(ws):
                # Prune dirs in-place
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git") and not (
                    Path(root) == ws / "outbox" and d == "sent"
                )]
                for name in files:
                    full = Path(root) / name
                    try:
                        rel = full.relative_to(ws)
                    except ValueError:
                        continue
                    arcname = f"workspace/{rel.as_posix()}"
                    if _should_exclude(arcname, full, max_inbox_bytes=max_bytes):
                        continue
                    # Also skip outbox/sent files if walk reached them
                    if "outbox/sent" in arcname.replace("\\", "/"):
                        continue
                    try:
                        tar.add(full, arcname=arcname, recursive=False)
                    except OSError as e:
                        log.warning("skip %s: %s", full, e)
        if state.exists():
            tar.add(state, arcname="state.json")
        if include_config and config.exists():
            tar.add(config, arcname="config.env")
            print("WARNING: backup includes config.env (secrets). Keep the archive private.")

    digest = _sha256_file(archive)
    sha_path = Path(str(archive) + ".sha256")
    sha_path.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, sha_path


def verify_archive(archive: Path, sha_path: Optional[Path] = None) -> tuple[bool, str]:
    """Reopen archive, count members, compare sha256. Returns (ok, message)."""
    archive = Path(archive)
    sha_path = Path(sha_path) if sha_path else Path(str(archive) + ".sha256")
    if not archive.exists():
        return False, "archive missing"
    if not sha_path.exists():
        return False, "sha256 file missing"
    expected = sha_path.read_text(encoding="utf-8").strip().split()[0]
    actual = _sha256_file(archive)
    if actual != expected:
        return False, f"sha256 mismatch: expected {expected[:12]}… got {actual[:12]}…"
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            n = len(members)
            # Touch names to ensure readable
            for m in members:
                _ = m.name
    except Exception as e:
        return False, f"archive unreadable: {e}"
    return True, f"ok ({n} members, sha256 match)"


def list_archives(home: Optional[Path] = None) -> list[Path]:
    d = backups_dir(home)
    return sorted(d.glob("daftar-*.tar.gz"), key=lambda p: p.name)


def prune_local(keep: int, home: Optional[Path] = None) -> list[Path]:
    """Delete oldest local archives beyond keep. Returns removed paths."""
    keep = max(0, int(keep))
    archives = list_archives(home)
    removed: list[Path] = []
    if keep == 0:
        excess = archives
    else:
        excess = archives[:-keep] if len(archives) > keep else []
    for path in excess:
        sha = Path(str(path) + ".sha256")
        try:
            path.unlink(missing_ok=True)
            sha.unlink(missing_ok=True)
            removed.append(path)
        except OSError as e:
            log.warning("prune failed %s: %s", path, e)
    return removed


def _rclone_available() -> bool:
    return bool(shutil.which("rclone"))


def remote_upload_and_verify(
    archive: Path,
    sha_path: Path,
    remote: str,
) -> tuple[bool, str]:
    """rclone copyto archive + sha, then verify size / hash. Never deletes here."""
    if not remote.strip():
        return False, "no remote"
    if not _rclone_available():
        log.warning("rclone not on PATH — skipping remote backup")
        return False, "rclone missing"
    remote = remote.rstrip("/")
    name = archive.name
    sha_name = sha_path.name
    dest_a = f"{remote}/{name}"
    dest_s = f"{remote}/{sha_name}"
    try:
        for src, dest in ((archive, dest_a), (sha_path, dest_s)):
            r = subprocess.run(
                ["rclone", "copyto", str(src), dest],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if r.returncode != 0:
                return False, f"rclone copyto failed: {(r.stderr or r.stdout or '')[:300]}"
    except Exception as e:
        return False, f"rclone copyto error: {e}"

    # Verify via lsjson size
    local_size = archive.stat().st_size
    try:
        r = subprocess.run(
            ["rclone", "lsjson", dest_a],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            return False, f"rclone lsjson failed: {(r.stderr or '')[:200]}"
        import json

        rows = json.loads(r.stdout or "[]")
        if not rows:
            return False, "rclone lsjson empty"
        remote_size = int(rows[0].get("Size") or rows[0].get("size") or -1)
        if remote_size != local_size:
            return False, f"size mismatch local={local_size} remote={remote_size}"
    except Exception as e:
        return False, f"rclone lsjson error: {e}"

    # Prefer hashsum when supported
    expected = _sha256_file(archive)
    for algo in ("sha256", "md5"):
        try:
            r = subprocess.run(
                ["rclone", "hashsum", algo, dest_a],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if r.returncode != 0:
                continue
            line = (r.stdout or "").strip().splitlines()
            if not line:
                continue
            remote_hash = line[0].split()[0].strip()
            if algo == "sha256":
                if remote_hash.lower() == expected.lower():
                    return True, "remote verified (sha256)"
                return False, "remote sha256 mismatch"
            # md5: compute local md5
            import hashlib as _hl

            h = _hl.md5()
            with open(archive, "rb") as fh:
                while True:
                    c = fh.read(1024 * 1024)
                    if not c:
                        break
                    h.update(c)
            if remote_hash.lower() == h.hexdigest().lower():
                return True, "remote verified (md5)"
            return False, "remote md5 mismatch"
        except Exception:
            continue
    # Size-only fallback
    return True, "remote verified (size)"


def prune_remote(remote: str, keep: int, *, local_names: Optional[list[str]] = None) -> list[str]:
    """Prune remote archives beyond keep using explicit file paths only."""
    if not remote.strip() or not _rclone_available():
        return []
    remote = remote.rstrip("/")
    keep = max(0, int(keep))
    try:
        r = subprocess.run(
            ["rclone", "lsjson", remote],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            log.warning("rclone lsjson for prune failed: %s", (r.stderr or "")[:200])
            return []
        import json

        rows = json.loads(r.stdout or "[]")
    except Exception as e:
        log.warning("rclone prune list error: %s", e)
        return []

    archives = sorted(
        [
            str(row.get("Path") or row.get("Name") or "")
            for row in rows
            if str(row.get("Path") or row.get("Name") or "").startswith("daftar-")
            and str(row.get("Path") or row.get("Name") or "").endswith(".tar.gz")
            and not row.get("IsDir")
        ]
    )
    if keep == 0:
        excess = archives
    else:
        excess = archives[:-keep] if len(archives) > keep else []
    removed: list[str] = []
    for name in excess:
        # Explicit file path only — never a directory or pattern
        base = name.split("/")[-1]
        for fname in (base, base + ".sha256"):
            target = f"{remote}/{fname}"
            try:
                rr = subprocess.run(
                    ["rclone", "deletefile", target],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if rr.returncode == 0:
                    removed.append(fname)
                else:
                    log.warning(
                        "rclone deletefile %s: %s",
                        fname,
                        (rr.stderr or rr.stdout or "")[:200],
                    )
            except Exception as e:
                log.warning("rclone deletefile error %s: %s", fname, e)
    return removed


def run_backup(
    cfg: Optional[dict[str, str]] = None,
    *,
    home: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Create → verify → prune local; optional remote copy → verify → prune."""
    cfg = cfg or load_config()
    home = home or daftar_home()
    ws = Path(workspace) if workspace else workspace_path(cfg)
    keep = get_int(cfg, "BACKUP_KEEP", 7)
    max_mb = get_int(cfg, "BACKUP_MAX_FILE_MB", 50)
    include_config = (cfg.get("BACKUP_INCLUDE_CONFIG") or "").strip().lower() in (
        "yes",
        "1",
        "true",
    )
    remote = (cfg.get("BACKUP_RCLONE_REMOTE") or "").strip()

    report: dict[str, Any] = {
        "archive": "",
        "verified": False,
        "message": "",
        "pruned_local": [],
        "remote_ok": None,
        "pruned_remote": [],
    }
    archive, sha_path = create_archive(
        workspace=ws,
        home=home,
        include_config=include_config,
        max_file_mb=max_mb,
    )
    report["archive"] = str(archive)
    ok, msg = verify_archive(archive, sha_path)
    report["verified"] = ok
    report["message"] = msg
    if not ok:
        log.error("backup verify failed: %s — not pruning", msg)
        return report

    report["pruned_local"] = [str(p) for p in prune_local(keep, home=home)]

    if remote:
        rok, rmsg = remote_upload_and_verify(archive, sha_path, remote)
        report["remote_ok"] = rok
        report["message"] += f"; remote: {rmsg}"
        if rok:
            report["pruned_remote"] = prune_remote(remote, keep)
        else:
            log.warning("remote backup not verified — skip remote prune: %s", rmsg)
    return report


def restore_archive(
    archive: Path,
    dest: Path,
    *,
    force: bool = False,
    live_workspace: Optional[Path] = None,
) -> None:
    """Extract archive to dest. Refuses non-empty target without --force."""
    archive = Path(archive).expanduser().resolve()
    dest = Path(dest).expanduser().resolve()
    if not archive.exists():
        raise FileNotFoundError(f"archive not found: {archive}")
    live = Path(live_workspace).resolve() if live_workspace else None

    if dest.exists():
        nonempty = any(dest.iterdir()) if dest.is_dir() else True
        if nonempty and not force:
            raise RuntimeError(
                f"target {dest} is not empty — pass --force to overwrite"
            )
        if live and dest == live and not force:
            raise RuntimeError(
                "refusing to restore over the live workspace without --force"
            )
    else:
        dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        # Python 3.12+ has filter=; use data filter when available
        try:
            tar.extractall(dest, filter=tarfile.data_filter)  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            tar.extractall(dest)


def last_backup_info(home: Optional[Path] = None) -> dict[str, Any]:
    """For doctor: newest archive age + verified flag."""
    archives = list_archives(home)
    if not archives:
        return {"exists": False}
    newest = archives[-1]
    sha = Path(str(newest) + ".sha256")
    ok, msg = verify_archive(newest, sha) if sha.exists() else (False, "no sha256")
    age_sec = max(0, datetime.now().timestamp() - newest.stat().st_mtime)
    return {
        "exists": True,
        "path": str(newest),
        "age_hours": age_sec / 3600.0,
        "verified": ok,
        "message": msg,
    }
