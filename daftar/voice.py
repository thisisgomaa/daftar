"""Voice/audio transcription with multi-provider fallback (stdlib urllib)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("daftar.voice")

NO_KEY_MSG = "Voice notes need a transcription key (see README)"


def _mime(path: Path) -> str:
    guess = mimetypes.guess_type(str(path))[0]
    if guess:
        return guess
    return {
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")


def _multipart(url: str, headers: dict[str, str], fields: dict[str, str],
               file_field: str, path: Path, timeout: float = 120) -> dict:
    boundary = f"----daftarvoice{int(time.time() * 1000)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    data = path.read_bytes()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {_mime(path)}\r\n\r\n".encode())
    body.extend(data)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    hdrs = dict(headers)
    hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url, data=bytes(body), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _providers() -> list[dict]:
    out: list[dict] = []
    openai = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai:
        model = os.environ.get("OPENAI_WHISPER_MODEL", "whisper-1")
        # Prefer gpt-4o-mini-transcribe when explicitly configured
        out.append({
            "name": "openai",
            "url": "https://api.openai.com/v1/audio/transcriptions",
            "key": openai,
            "model": model,
            "file_field": "file",
            "auth": ("Authorization", f"Bearer {openai}"),
        })
        # Also try mini-transcribe as alternate if default is whisper
        if model == "whisper-1":
            out.append({
                "name": "openai-mini",
                "url": "https://api.openai.com/v1/audio/transcriptions",
                "key": openai,
                "model": "gpt-4o-mini-transcribe",
                "file_field": "file",
                "auth": ("Authorization", f"Bearer {openai}"),
            })
    groq = os.environ.get("GROQ_API_KEY", "").strip()
    if groq:
        out.append({
            "name": "groq",
            "url": "https://api.groq.com/openai/v1/audio/transcriptions",
            "key": groq,
            "model": os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3"),
            "file_field": "file",
            "auth": ("Authorization", f"Bearer {groq}"),
        })
    el = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if el:
        out.append({
            "name": "elevenlabs",
            "url": "https://api.elevenlabs.io/v1/speech-to-text",
            "key": el,
            "model": os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1"),
            "file_field": "file",
            "auth": ("xi-api-key", el),
            "eleven": True,
        })
    return out


def transcribe(audio_path: str | Path, language: str = "") -> tuple[Optional[str], str]:
    """Return (text, error_reason). error_reason is empty on success."""
    path = Path(audio_path)
    if not path.exists() or path.stat().st_size == 0:
        return None, "missing"
    providers = _providers()
    if not providers:
        return None, "no_provider"

    lang = (language or os.environ.get("TRANSCRIBE_LANGUAGE") or "").strip()
    last_reason = "other"
    for cfg in providers:
        try:
            headers = {cfg["auth"][0]: cfg["auth"][1]}
            if cfg.get("eleven"):
                fields = {"model_id": cfg["model"]}
                if lang:
                    fields["language_code"] = lang
                body = _multipart(cfg["url"], headers, fields, cfg["file_field"], path)
                text = (body.get("text") or "").strip()
            else:
                fields = {"model": cfg["model"]}
                if lang:
                    fields["language"] = lang
                body = _multipart(cfg["url"], headers, fields, cfg["file_field"], path)
                text = (body.get("text") or "").strip()
            if text:
                log.info("transcribed via %s (%d chars)", cfg["name"], len(text))
                return text, ""
            last_reason = "empty"
            log.warning("%s returned empty transcript", cfg["name"])
        except urllib.error.HTTPError as e:
            log.warning("%s HTTP %s: %s", cfg["name"], e.code, e.read()[:200])
            last_reason = "http"
        except Exception as e:
            log.warning("%s failed: %s", cfg["name"], e)
            last_reason = "other"
    return None, last_reason


def failure_message(reason: str) -> str:
    if reason == "no_provider":
        return NO_KEY_MSG
    if reason == "empty":
        return "Transcription came back empty — try recording again."
    if reason == "missing":
        return "Could not download the voice note."
    return "Transcription failed. Try again, or send text instead."
