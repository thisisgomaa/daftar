"""Install / uninstall OS service (systemd --user or LaunchAgent)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import daftar_home, load_config


def _daftar_bin() -> Path:
    # Prefer the running script
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == "daftar" and argv0.exists():
        return argv0
    which = shutil.which("daftar")
    if which:
        return Path(which)
    # Fall back to repo bin next to package
    repo = Path(__file__).resolve().parents[1] / "bin" / "daftar"
    return repo


def _linux_unit(user: bool) -> tuple[Path, str]:
    home = Path.home()
    bin_path = _daftar_bin()
    env_file = daftar_home() / "config.env"
    content = f"""[Unit]
Description=Daftar Telegram bridge for Claude Code
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={bin_path} run
Restart=on-failure
RestartSec=5
Environment=DAFTAR_HOME={daftar_home()}
EnvironmentFile=-{env_file}
WorkingDirectory={Path.home()}

[Install]
WantedBy={"default.target" if user else "multi-user.target"}
"""
    if user:
        path = home / ".config" / "systemd" / "user" / "daftar.service"
    else:
        path = Path("/etc/systemd/system/daftar.service")
    return path, content


def _macos_plist() -> tuple[Path, str]:
    bin_path = _daftar_bin()
    home = Path.home()
    logs = daftar_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.daftar.bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>{bin_path}</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DAFTAR_HOME</key>
    <string>{daftar_home()}</string>
    <key>PATH</key>
    <string>{os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{logs / "service.out.log"}</string>
  <key>StandardErrorPath</key>
  <string>{logs / "service.err.log"}</string>
  <key>WorkingDirectory</key>
  <string>{home}</string>
</dict>
</plist>
"""
    path = home / "Library" / "LaunchAgents" / "com.daftar.bridge.plist"
    return path, content


def install() -> int:
    system = platform.system()
    if system == "Darwin":
        path, content = _macos_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr or r.stdout or "launchctl load failed")
            return 1
        print(f"Installed LaunchAgent: {path}")
        return 0

    if system == "Linux":
        user = os.geteuid() != 0
        path, content = _linux_unit(user=user)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if user:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", "daftar.service"], check=False)
            print(f"Installed user unit: {path}")
            print("Tip: enable lingering so it survives logout: loginctl enable-linger $USER")
        else:
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "enable", "--now", "daftar.service"], check=False)
            print(f"Installed system unit: {path}")
        return 0

    print(f"Unsupported OS for service install: {system}. Use WSL on Windows.")
    return 1


def uninstall() -> int:
    system = platform.system()
    if system == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / "com.daftar.bridge.plist"
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        if path.exists():
            path.unlink()
            print(f"Removed {path}")
        return 0
    if system == "Linux":
        user = os.geteuid() != 0
        unit = "daftar.service"
        if user:
            subprocess.run(["systemctl", "--user", "disable", "--now", unit], check=False)
            path = Path.home() / ".config" / "systemd" / "user" / unit
        else:
            subprocess.run(["systemctl", "disable", "--now", unit], check=False)
            path = Path("/etc/systemd/system") / unit
        if path.exists():
            path.unlink()
        print(f"Removed {path}")
        return 0
    print("Nothing to uninstall on this OS.")
    return 0


def status() -> int:
    system = platform.system()
    if system == "Darwin":
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        found = "com.daftar.bridge" in (r.stdout or "")
        print("LaunchAgent com.daftar.bridge:", "loaded" if found else "not loaded")
        return 0 if found else 1
    if system == "Linux":
        user = os.geteuid() != 0
        cmd = ["systemctl", "--user", "is-active", "daftar.service"] if user else [
            "systemctl", "is-active", "daftar.service"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print((r.stdout or r.stderr or "").strip() or "unknown")
        return 0 if r.returncode == 0 else 1
    print("unknown")
    return 1
