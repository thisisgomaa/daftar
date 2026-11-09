"""Nightly scheduler: the nightly hour follows the configured TIMEZONE."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


def _other_zone() -> str:
    """A fixed-offset zone whose wall-clock hour differs from server local time."""
    local_hour = datetime.now().hour
    for off in range(1, 13):
        name = f"Etc/GMT-{off}"  # POSIX sign: Etc/GMT-3 is UTC+3
        if datetime.now(ZoneInfo(name)).hour != local_hour:
            return name
    return "Etc/GMT+6"


class SchedulerTimezoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-sched-"))
        self._old_home = os.environ.get("DAFTAR_HOME")
        os.environ["DAFTAR_HOME"] = str(self.tmp)
        os.environ.pop("DAFTAR_PROFILE", None)
        from daftar.config import set_active_profile

        set_active_profile("")

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("DAFTAR_HOME", None)
        else:
            os.environ["DAFTAR_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_local_now_uses_timezone(self) -> None:
        from daftar.scheduler import local_now

        now = local_now({"TIMEZONE": "Asia/Tokyo"})
        self.assertEqual(getattr(now.tzinfo, "key", ""), "Asia/Tokyo")
        self.assertIsNone(local_now({"TIMEZONE": ""}).tzinfo)
        with self.assertLogs("daftar.scheduler", level="WARNING"):
            self.assertIsNone(local_now({"TIMEZONE": "Not/AZone"}).tzinfo)

    def _run_tick(self, cfg: dict[str, str]) -> list[str]:
        from daftar.scheduler import NightlyScheduler

        bridge = SimpleNamespace(cfg=cfg, _running=False, workspace=self.tmp)
        sched = NightlyScheduler(bridge)  # type: ignore[arg-type]
        ran: list[str] = []
        sched._run_all_steps = lambda today: ran.append(today) or []  # type: ignore[assignment]
        sched._maybe_run()
        return ran

    def test_nightly_hour_in_configured_zone(self) -> None:
        zone = _other_zone()
        zone_now = datetime.now(ZoneInfo(zone))
        # Server local hour does not match: nothing runs.
        self.assertEqual(
            self._run_tick({"TIMEZONE": zone, "NIGHTLY_HOUR": str(datetime.now().hour)}),
            [],
        )
        # The zone's hour matches: runs once, dated in that zone.
        ran = self._run_tick({"TIMEZONE": zone, "NIGHTLY_HOUR": str(zone_now.hour)})
        self.assertEqual(ran, [zone_now.strftime("%Y-%m-%d")])
        # Same day again: already done.
        self.assertEqual(
            self._run_tick({"TIMEZONE": zone, "NIGHTLY_HOUR": str(zone_now.hour)}),
            [],
        )

    def test_curator_gets_zone_time(self) -> None:
        from daftar import scheduler as schedmod

        seen: dict = {}

        def fake_curator(**kw):  # type: ignore[no-untyped-def]
            seen.update(kw)
            return 0

        bridge = SimpleNamespace(cfg={"TIMEZONE": "Asia/Tokyo"}, workspace=self.tmp)
        sched = schedmod.NightlyScheduler(bridge)  # type: ignore[arg-type]
        orig = schedmod.run_curator
        schedmod.run_curator = fake_curator  # type: ignore[assignment]
        try:
            sched._run_curator()
        finally:
            schedmod.run_curator = orig  # type: ignore[assignment]
        self.assertEqual(getattr(seen["now"].tzinfo, "key", ""), "Asia/Tokyo")


if __name__ == "__main__":
    unittest.main()
