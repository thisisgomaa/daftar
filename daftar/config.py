"""Load and save Daftar configuration (~/.daftar/config.env)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Optional

DEFAULTS: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "",
    "OWNER_ID": "",
    "WORKSPACE": "",
    "LANGUAGE": "English",
    "TIMEZONE": "",
    "MODEL": "",
    "ENGINE": "claude",
    "PERSONA_FILE": "persona/PERSONA.md",
    "PERMISSION_MODE": "bypassPermissions",
    "ROTATE_TOKENS": "120000",
    "TURN_TIMEOUT_SEC": "2700",
    "NIGHTLY_HOUR": "2",
    "CURATOR_MODEL": "opus",
    "CURATOR_FULL_WEEKDAY": "4",
    "SUMMARY_MODEL": "sonnet",
    "IMAGE_MODEL": "gpt-image-1",
    "TRANSCRIBE_LANGUAGE": "",
    "REMINDER": "",
    "TELEGRAM_API_BASE": "https://api.telegram.org",
    "OPENAI_API_KEY": "",
    "GROQ_API_KEY": "",
    "ELEVENLABS_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "ASSISTANT_NAME": "",
    "BOT_USERNAME": "",
}


def daftar_root() -> Path:
    """Top-level DAFTAR_HOME (~/.daftar), never a profile subdirectory."""
    override = os.environ.get("DAFTAR_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".daftar"


def normalize_profile(name: Optional[str]) -> str:
    """Return '' for the default (legacy) home; otherwise the profile name."""
    n = (name or "").strip()
    if not n or n == "default":
        return ""
    if any(ch in n for ch in "/\\") or n in (".", ".."):
        raise ValueError(f"invalid profile name: {name!r}")
    return n


def active_profile() -> str:
    """Currently selected profile ('' = default home)."""
    return normalize_profile(os.environ.get("DAFTAR_PROFILE", ""))


def set_active_profile(name: Optional[str]) -> str:
    """Set DAFTAR_PROFILE for this process. Returns normalized name ('' = default)."""
    n = normalize_profile(name)
    if n:
        os.environ["DAFTAR_PROFILE"] = n
    else:
        os.environ.pop("DAFTAR_PROFILE", None)
    return n


def profile_label(name: Optional[str] = None) -> str:
    """Human-readable profile id ('default' or the name)."""
    n = normalize_profile(name if name is not None else active_profile())
    return n or "default"


def profile_home(name: Optional[str] = None) -> Path:
    """Config/state/logs directory for a profile (default = daftar_root)."""
    n = normalize_profile(name if name is not None else active_profile())
    root = daftar_root()
    if n:
        return root / "profiles" / n
    return root


def daftar_home() -> Path:
    """Return the active profile home (DAFTAR_HOME or DAFTAR_HOME/profiles/NAME)."""
    return profile_home()


def config_path(name: Optional[str] = None) -> Path:
    return profile_home(name) / "config.env"


def state_path(name: Optional[str] = None) -> Path:
    return profile_home(name) / "state.json"


def logs_dir(name: Optional[str] = None) -> Path:
    return profile_home(name) / "logs"


def jobs_dir(name: Optional[str] = None) -> Path:
    return profile_home(name) / "jobs"


def ensure_home(name: Optional[str] = None) -> Path:
    home = profile_home(name)
    home.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def list_profile_names() -> list[str]:
    """Known profiles: 'default' if root config exists, plus profiles/* with config.env."""
    names: list[str] = []
    root = daftar_root()
    if (root / "config.env").exists():
        names.append("default")
    profiles = root / "profiles"
    if profiles.is_dir():
        for child in sorted(profiles.iterdir()):
            if child.is_dir() and (child / "config.env").exists():
                names.append(child.name)
    return names


def load_profile_config(name: str) -> dict[str, str]:
    """Load one profile's config without changing the active profile."""
    path = config_path(name)
    cfg = dict(DEFAULTS)
    cfg.update(parse_env_file(path))
    if not cfg.get("WORKSPACE"):
        cfg["WORKSPACE"] = str(Path.home() / "daftar-workspace")
    return cfg


def other_workspaces(exclude: Optional[str] = None) -> list[tuple[str, str]]:
    """(profile_label, workspace) for profiles that have a WORKSPACE set."""
    ex = profile_label(exclude if exclude is not None else active_profile())
    out: list[tuple[str, str]] = []
    for name in list_profile_names():
        if name == ex:
            continue
        cfg = load_profile_config(name)
        ws = (cfg.get("WORKSPACE") or "").strip()
        if ws:
            out.append((name, str(Path(ws).expanduser().resolve())))
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignore comments and blanks."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


ENV_OVERRIDES = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_API_BASE",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
)


def load_config() -> dict[str, str]:
    """Merge defaults, config.env, and process environment (env wins)."""
    ensure_home()
    cfg = dict(DEFAULTS)
    cfg.update(parse_env_file(config_path()))
    # Environment wins only for secrets and the API base (handy for CI/tests);
    # everything else comes from config.env so a stale shell can't override it.
    for key in ENV_OVERRIDES:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if not cfg.get("WORKSPACE"):
        cfg["WORKSPACE"] = str(Path.home() / "daftar-workspace")
    if not cfg.get("REMINDER"):
        lang = cfg.get("LANGUAGE") or "English"
        cfg["REMINDER"] = f"Reply in {lang}. Be concise."
    if not cfg.get("TIMEZONE"):
        cfg["TIMEZONE"] = _detect_timezone()
    if not (cfg.get("ENGINE") or "").strip():
        cfg["ENGINE"] = "claude"
    if not (cfg.get("PERSONA_FILE") or "").strip():
        cfg["PERSONA_FILE"] = "persona/PERSONA.md"
    return cfg


def save_config(cfg: dict[str, str]) -> Path:
    """Write config.env with mode 600. Preserves unknown keys from existing file."""
    ensure_home()
    path = config_path()
    existing = parse_env_file(path)
    merged = dict(existing)
    for k, v in cfg.items():
        if v is None:
            continue
        merged[k] = str(v)
    # Prefer known key order, then extras
    lines = [
        "# Daftar configuration — do not commit; chmod 600",
        "# Generated by `daftar setup`. Edit carefully.",
        "",
    ]
    seen: set[str] = set()
    for key in DEFAULTS:
        if key in merged:
            lines.append(f"{key}={_quote(merged[key])}")
            seen.add(key)
    for key, val in merged.items():
        if key not in seen:
            lines.append(f"{key}={_quote(val)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def apply_to_environ(cfg: dict[str, str]) -> None:
    """Export selected keys into os.environ for child processes / voice."""
    for key in (
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "ELEVENLABS_API_KEY",
        "ANTHROPIC_API_KEY",
        "TRANSCRIBE_LANGUAGE",
        "IMAGE_MODEL",
        # Claude Code refuses bypassPermissions as root unless this is set.
        # Only set it on a machine dedicated to the assistant.
        "IS_SANDBOX",
    ):
        val = (cfg.get(key) or "").strip()
        if val:
            os.environ[key] = val


def get_int(cfg: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(cfg.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def workspace_path(cfg: dict[str, str] | None = None) -> Path:
    c = cfg or load_config()
    return Path(c["WORKSPACE"]).expanduser().resolve()


def persona_path(cfg: dict[str, str] | None = None, workspace: Path | None = None) -> Path:
    c = cfg or load_config()
    ws = workspace or workspace_path(c)
    rel = (c.get("PERSONA_FILE") or "persona/PERSONA.md").strip()
    return (ws / rel).resolve()


def _quote(val: str) -> str:
    if not val:
        return ""
    if any(ch in val for ch in ' \t#"\'\\'):
        return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return val


def _detect_timezone() -> str:
    try:
        link = Path("/etc/localtime")
        if link.is_symlink():
            target = os.readlink(link)
            # .../zoneinfo/Area/City
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    tz = os.environ.get("TZ", "").strip()
    return tz or "UTC"


def config_summary(cfg: dict[str, str]) -> dict[str, Any]:
    """Redacted view for doctor/logging."""
    secret_keys = {
        "TELEGRAM_BOT_TOKEN",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "ELEVENLABS_API_KEY",
        "ANTHROPIC_API_KEY",
    }
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k in secret_keys:
            out[k] = ("set" if v else "unset")
        else:
            out[k] = v
    return out
