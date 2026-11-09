"""Nightly papers + project summaries + curator + backup."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional

from . import backup as backupmod
from . import projects as projectsmod
from .config import get_int
from .curator import run_curator
from .sessions import (
    find_sessions_without_paper,
    load_state,
    save_state,
    upsert_session,
    write_handoff_paper,
)

if TYPE_CHECKING:
    from .bridge import Bridge

log = logging.getLogger("daftar.scheduler")
_bad_zones: set[str] = set()


def local_now(cfg: dict[str, str]) -> datetime:
    """Current time in cfg TIMEZONE; server local time if unset or invalid."""
    name = (cfg.get("TIMEZONE") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(name))
        except Exception:
            if name not in _bad_zones:  # the scheduler ticks every 30 s
                _bad_zones.add(name)
                log.warning("unknown TIMEZONE %r; using server local time", name)
    return datetime.now()


class NightlyScheduler:
    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="daftar-nightly", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(30.0):
            try:
                self._maybe_run()
            except Exception:
                log.exception("nightly tick failed")

    def _maybe_run(self) -> None:
        cfg = self.bridge.cfg
        hour = get_int(cfg, "NIGHTLY_HOUR", 2)
        now = local_now(cfg)
        today = now.strftime("%Y-%m-%d")
        state = load_state()
        if state.last_nightly == today:
            return
        if now.hour != hour:
            return
        # Wait until no user turn is active
        for _ in range(120):  # up to ~10 min
            if self._stop.is_set():
                return
            if not self.bridge._running:
                break
            time.sleep(5)
        else:
            log.info("nightly deferred: turn still active")
            return

        log.info("nightly job starting")
        failures = self._run_all_steps(today)
        state = load_state()
        state.last_nightly = today
        save_state(state)
        if failures:
            try:
                brief = "; ".join(failures[:4])
                if len(failures) > 4:
                    brief += f" (+{len(failures) - 4} more)"
                self.bridge.tg.send_message(
                    self.bridge.owner_id,
                    f"Nightly: {brief}",
                )
            except Exception:
                log.exception("nightly failure notify failed")
        log.info("nightly job done failures=%s", failures or "none")

    def _run_all_steps(self, today: str) -> list[str]:
        """Run each nightly step in isolation. Returns short failure labels."""
        failures: list[str] = []
        steps: list[tuple[str, Callable[[], None], bool]] = [
            # (name, fn, needs_workspace_lock)
            ("papers", lambda: self._write_missing_papers(), False),
            ("summaries", lambda: self._run_summaries(today), True),
            ("curator", lambda: self._run_curator(), True),
            ("backup", lambda: self._run_backup(), True),
        ]
        ws = self.bridge.workspace
        for name, fn, need_lock in steps:
            if need_lock:
                if not projectsmod.try_workspace_step_lock(ws, name, today):
                    log.info("nightly %s skipped (another bridge holds the lock)", name)
                    continue
            try:
                fn()
            except Exception as e:
                log.exception("nightly %s failed", name)
                failures.append(f"{name} failed")
                _ = e
        return failures

    def _write_missing_papers(self) -> None:
        cfg = self.bridge.cfg
        state = load_state()
        errors = 0
        for rec in find_sessions_without_paper(state):
            try:
                path = write_handoff_paper(
                    self.bridge.engine,
                    rec.id,
                    summary_model=cfg.get("SUMMARY_MODEL") or "sonnet",
                    cfg=cfg,
                )
                upsert_session(state, rec.id, paper=str(path))
                save_state(state)
            except Exception:
                log.exception("nightly paper failed for %s", rec.id[:8])
                errors += 1
        if errors:
            raise RuntimeError(f"{errors} handoff paper(s) failed")

    def _run_summaries(self, today: str) -> None:
        # The nightly hour is early morning, so most of the work is in
        # yesterday's transcripts. Fingerprints make the reruns no-ops.
        cfg = self.bridge.cfg
        engine = (cfg.get("ENGINE") or "claude").strip().lower()
        day = datetime.strptime(today, "%Y-%m-%d")
        yesterday = (day - timedelta(days=1)).strftime("%Y-%m-%d")
        errors = 0
        for date in (yesterday, today):
            report = projectsmod.summarize_day(
                self.bridge.workspace,
                date,
                preferred_engine=engine,
                model=cfg.get("SUMMARY_MODEL") or "sonnet",
            )
            errors += len(report.get("errors") or [])
            log.info(
                "project summaries %s: processed=%s skipped=%s entries=%s",
                date,
                report.get("processed"),
                report.get("skipped"),
                report.get("entries"),
            )
        if errors:
            raise RuntimeError(f"summary errors on {errors} transcript(s)")

    def _run_curator(self) -> None:
        cfg = self.bridge.cfg
        # Curator notify suppressed here — owner gets one combined failure message.
        code = run_curator(
            workspace=self.bridge.workspace,
            model=cfg.get("CURATOR_MODEL") or "opus",
            notify=None,
            now=local_now(cfg),
        )
        if code not in (0, None):
            raise RuntimeError(f"curator exit {code}")

    def _run_backup(self) -> None:
        report = backupmod.run_backup(
            self.bridge.cfg,
            workspace=self.bridge.workspace,
        )
        if not report.get("verified"):
            raise RuntimeError(report.get("message") or "backup verify failed")
