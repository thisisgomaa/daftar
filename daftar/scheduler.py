"""Nightly papers + curator background thread."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

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
        now = datetime.now()
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
        self._write_missing_papers()
        try:
            run_curator(
                workspace=self.bridge.workspace,
                model=cfg.get("CURATOR_MODEL") or "opus",
                notify=lambda msg: self.bridge.tg.send_message(
                    self.bridge.owner_id, msg
                ),
            )
        except Exception:
            log.exception("nightly curator failed")
        state = load_state()
        state.last_nightly = today
        save_state(state)
        log.info("nightly job done")

    def _write_missing_papers(self) -> None:
        cfg = self.bridge.cfg
        state = load_state()
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
