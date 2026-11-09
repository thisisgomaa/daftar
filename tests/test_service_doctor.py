"""systemd unit PATH, service install error handling, doctor checks."""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


class ServiceUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daftar-svc-"))
        self.bin_dir = self.tmp / "tools bin"
        self.bin_dir.mkdir()
        for tool in ("claude", "codex"):
            p = self.bin_dir / tool
            p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self._saved = {k: os.environ.get(k) for k in ("PATH", "DAFTAR_HOME")}
        os.environ["DAFTAR_HOME"] = str(self.tmp / "home")

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unit_carries_literal_path_with_engine_dirs(self) -> None:
        from daftar import service as svc

        # Engines reachable only via a relative and a ~ entry at install time.
        os.environ["PATH"] = os.pathsep.join(["relative/bin", "~/nowhere", "/usr/bin"])
        with mock.patch.object(svc.shutil, "which", lambda t: str(self.bin_dir / t)):
            _path, content = svc._linux_unit(user=True)
        lines = [l for l in content.splitlines() if "PATH=" in l]
        self.assertEqual(len(lines), 1, content)
        line = lines[0]
        # Directory has a space -> the whole assignment is quoted for systemd.
        self.assertTrue(line.startswith('Environment="PATH='), line)
        value = line[len('Environment="PATH='):-1]
        parts = value.split(":")
        self.assertEqual(parts[0], str(self.bin_dir))
        self.assertIn(str(Path(os.sys.executable).parent), parts)
        self.assertIn("/usr/bin", parts)
        self.assertIn("/usr/local/bin", parts)
        self.assertNotIn("relative/bin", parts)
        self.assertNotIn("$", value)
        self.assertNotIn("~", value)
        for p in parts:
            self.assertTrue(os.path.isabs(p), p)

    def test_install_reports_systemctl_failure(self) -> None:
        from daftar import service as svc

        unit = self.tmp / "unit" / "daftar.service"
        fail = subprocess.CompletedProcess(["systemctl"], 1, stdout="", stderr="Failed to connect to bus")
        out = io.StringIO()
        with mock.patch.object(svc.platform, "system", return_value="Linux"), \
                mock.patch.object(svc, "_linux_unit", return_value=(unit, "[Unit]\n")), \
                mock.patch.object(svc.subprocess, "run", return_value=fail) as run, \
                redirect_stdout(out):
            rc = svc.install()
        self.assertEqual(rc, 1)
        self.assertIn("failed", out.getvalue())
        self.assertIn("Failed to connect to bus", out.getvalue())
        self.assertNotIn("Installed", out.getvalue())
        self.assertEqual(run.call_count, 1)  # stops after daemon-reload fails

    def test_install_success_path(self) -> None:
        from daftar import service as svc

        unit = self.tmp / "unit" / "daftar.service"
        ok = subprocess.CompletedProcess(["systemctl"], 0, stdout="", stderr="")
        out = io.StringIO()
        with mock.patch.object(svc.platform, "system", return_value="Linux"), \
                mock.patch.object(svc, "_linux_unit", return_value=(unit, "[Unit]\n")), \
                mock.patch.object(svc.subprocess, "run", return_value=ok) as run, \
                redirect_stdout(out):
            rc = svc.install()
        self.assertEqual(rc, 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn("Installed", out.getvalue())

    def test_doctor_notes_engine_outside_systemd_path(self) -> None:
        from daftar import doctor

        out = io.StringIO()
        with mock.patch.object(doctor.platform, "system", return_value="Linux"), \
                mock.patch.object(doctor.shutil, "which", lambda t: str(self.bin_dir / t)), \
                redirect_stdout(out):
            doctor._check_service_path()
        text = out.getvalue()
        self.assertIn("claude is in", text)
        self.assertIn("codex is in", text)
        self.assertIn("informational", text)
        out = io.StringIO()
        with mock.patch.object(doctor.platform, "system", return_value="Linux"), \
                mock.patch.object(doctor.shutil, "which", lambda t: "/usr/bin/" + t), \
                redirect_stdout(out):
            doctor._check_service_path()
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
