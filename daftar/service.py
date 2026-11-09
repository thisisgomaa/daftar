"""Install / uninstall OS service (systemd --user or LaunchAgent)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import active_profile, daftar_home, profile_label


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


def service_unit_name(profile: str | None = None) -> str:
    """systemd unit / launchd label suffix: daftar or daftar-<profile>."""
    label = profile_label(profile if profile is not None else active_profile())
    if label == "default":
        return "daftar"
    return f"daftar-{label}"


def launchd_label(profile: str | None = None) -> str:
    unit = service_unit_name(profile)
    if unit == "daftar":
        return "com.daftar.bridge"
    return f"com.daftar.bridge.{profile_label(profile)}"


def _profile_args() -> list[str]:
    prof = active_profile()
    if prof:
        return ["--profile", prof]
    return []


def _linux_unit(user: bool) -> tuple[Path, str]:
    from .config import daftar_root

    home = Path.home()
    bin_path = _daftar_bin()
    env_file = daftar_home() / "config.env"
    unit = service_unit_name()
    label = profile_label()
    args = " ".join(_profile_args())
    exec_start = f"{bin_path} {args} run".replace("  ", " ").strip()
    content = f"""[Unit]
Description=Daftar Telegram bridge ({label})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Environment=DAFTAR_HOME={daftar_root()}
EnvironmentFile=-{env_file}
WorkingDirectory={Path.home()}

[Install]
WantedBy={"default.target" if user else "multi-user.target"}
"""
    if user:
        path = home / ".config" / "systemd" / "user" / f"{unit}.service"
    else:
        path = Path("/etc/systemd/system") / f"{unit}.service"
    return path, content


def _macos_plist() -> tuple[Path, str]:
    bin_path = _daftar_bin()
    home = Path.home()
    from .config import daftar_root

    logs = daftar_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    label = launchd_label()
    prog_args = [str(bin_path), *_profile_args(), "run"]
    args_xml = "\n".join(f"    <string>{a}</string>" for a in prog_args)
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DAFTAR_HOME</key>
    <string>{daftar_root()}</string>
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
    path = home / "Library" / "LaunchAgents" / f"{label}.plist"
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
        unit = service_unit_name() + ".service"
        if user:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=False)
            print(f"Installed user unit: {path}")
            print("Tip: enable lingering so it survives logout: loginctl enable-linger $USER")
        else:
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "enable", "--now", unit], check=False)
            print(f"Installed system unit: {path}")
        return 0

    print(f"Unsupported OS for service install: {system}. Use WSL on Windows.")
    return 1


def uninstall() -> int:
    system = platform.system()
    if system == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{launchd_label()}.plist"
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        if path.exists():
            path.unlink()
            print(f"Removed {path}")
        return 0
    if system == "Linux":
        user = os.geteuid() != 0
        unit = service_unit_name() + ".service"
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


def status(profile: str | None = None) -> int:
    system = platform.system()
    if system == "Darwin":
        label = launchd_label(profile)
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        found = label in (r.stdout or "")
        print(f"LaunchAgent {label}:", "loaded" if found else "not loaded")
        return 0 if found else 1
    if system == "Linux":
        user = os.geteuid() != 0
        unit = service_unit_name(profile) + ".service"
        cmd = ["systemctl", "--user", "is-active", unit] if user else [
            "systemctl", "is-active", unit
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print((r.stdout or r.stderr or "").strip() or "unknown")
        return 0 if r.returncode == 0 else 1
    print("unknown")
    return 1


def status_text(profile: str | None = None) -> str:
    """Short status string for `daftar profiles`."""
    system = platform.system()
    try:
        if system == "Darwin":
            label = launchd_label(profile)
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            return "loaded" if label in (r.stdout or "") else "not loaded"
        if system == "Linux":
            user = os.geteuid() != 0
            unit = service_unit_name(profile) + ".service"
            cmd = ["systemctl", "--user", "is-active", unit] if user else [
                "systemctl", "is-active", unit
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return (r.stdout or r.stderr or "unknown").strip() or "unknown"
    except Exception:
        return "unknown"
    return "n/a"
