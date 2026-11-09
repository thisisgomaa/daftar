"""Telegram Bot API client using urllib (stdlib only)."""

from __future__ import annotations

import json
import logging
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from .format import md_to_html, split_message

log = logging.getLogger("daftar.telegram")

DEFAULT_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, status: int = 0, body: Any = None):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.status = status
        self.body = body


class Telegram:
    """Minimal Bot API wrapper with retries and HTML→plain fallback."""

    def __init__(
        self,
        token: str,
        *,
        api_base: str = DEFAULT_BASE,
        timeout: float = 70.0,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener()

    # ── low-level ─────────────────────────────────────────────────

    def call(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        url = f"{self.api_base}/bot{self.token}/{method}"
        data = None
        headers = {"Content-Type": "application/json"}
        if params is not None:
            data = json.dumps(params).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                body = json.loads(raw)
                if not body.get("ok"):
                    desc = body.get("description") or "unknown error"
                    raise TelegramError(method, desc, body=body)
                return body
            except TelegramError:
                raise
            except urllib.error.HTTPError as e:
                payload = e.read().decode("utf-8", errors="replace")
                try:
                    body = json.loads(payload)
                    desc = body.get("description") or payload
                except json.JSONDecodeError:
                    body, desc = payload, payload
                # 400 on send is handled by callers for parse-mode fallback
                err = TelegramError(method, str(desc), status=e.code, body=body)
                if e.code == 400:
                    raise err
                last_err = err
                time.sleep(min(2 ** attempt, 8))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                time.sleep(min(2 ** attempt, 8))
        raise TelegramError(method, f"failed after retries: {last_err}")

    def _multipart(
        self,
        method: str,
        fields: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        *,
        retries: int = 3,
    ) -> dict[str, Any]:
        boundary = f"----daftar{int(time.time() * 1000)}"
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")
        for name, (filename, content, mime) in files.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())
        url = f"{self.api_base}/bot{self.token}/{method}"
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                if not parsed.get("ok"):
                    raise TelegramError(method, parsed.get("description", "error"), body=parsed)
                return parsed
            except TelegramError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(min(2 ** attempt, 8))
        raise TelegramError(method, f"multipart failed: {last_err}")

    # ── high-level ────────────────────────────────────────────────

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe", {}).get("result", {})

    def get_updates(
        self,
        offset: Optional[int] = None,
        *,
        timeout: int = 50,
        allowed_updates: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            # No "edited_message": an edit must not re-run as a new turn.
            "allowed_updates": allowed_updates or ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        # Long poll needs a longer socket timeout
        body = self.call("getUpdates", params, timeout=timeout + 20, retries=2)
        return list(body.get("result") or [])

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[dict] = None,
        disable_preview: bool = True,
    ) -> Optional[int]:
        """Send text; on HTML 400, retry as plain text. Returns last message_id."""
        if not text:
            text = "(empty)"
        last_id: Optional[int] = None
        chunks = split_message(text)
        if not chunks:
            chunks = ["(empty)"]
        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == len(chunks) - 1 else None
            last_id = self._send_one(
                chat_id, chunk, parse_mode=parse_mode, reply_markup=markup,
                disable_preview=disable_preview,
            )
        return last_id

    def send_html(
        self,
        chat_id: int,
        markdown_text: str,
        *,
        reply_markup: Optional[dict] = None,
    ) -> Optional[int]:
        """Convert markdown → HTML, send with plain fallback on 400."""
        if not markdown_text:
            markdown_text = "(empty)"
        chunks = split_message(markdown_text)
        last_id: Optional[int] = None
        for i, chunk in enumerate(chunks):
            html_chunk = md_to_html(chunk)
            markup = reply_markup if i == len(chunks) - 1 else None
            try:
                last_id = self._send_raw(
                    chat_id, html_chunk, parse_mode="HTML", reply_markup=markup
                )
            except TelegramError as e:
                if e.status == 400 or "parse" in e.description.lower() or "can't parse" in e.description.lower():
                    last_id = self._send_raw(
                        chat_id, chunk, parse_mode=None, reply_markup=markup
                    )
                else:
                    raise
        return last_id

    def _send_one(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str],
        reply_markup: Optional[dict],
        disable_preview: bool,
    ) -> Optional[int]:
        try:
            return self._send_raw(
                chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup,
                disable_preview=disable_preview,
            )
        except TelegramError as e:
            if parse_mode and (
                e.status == 400
                or "parse" in e.description.lower()
                or "can't parse" in e.description.lower()
            ):
                return self._send_raw(
                    chat_id, text, parse_mode=None, reply_markup=reply_markup,
                    disable_preview=disable_preview,
                )
            raise

    def _send_raw(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: Optional[str],
        reply_markup: Optional[dict] = None,
        disable_preview: bool = True,
    ) -> Optional[int]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        body = self.call("sendMessage", params)
        return (body.get("result") or {}).get("message_id")

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": action}, retries=1)
        except Exception:
            log.debug("sendChatAction failed", exc_info=True)

    def send_photo(
        self, chat_id: int, path: Path, *, caption: str = ""
    ) -> Optional[int]:
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        body = self._multipart(
            "sendPhoto",
            fields,
            {"photo": (path.name, data, mime)},
        )
        return (body.get("result") or {}).get("message_id")

    def send_document(
        self, chat_id: int, path: Path, *, caption: str = ""
    ) -> Optional[int]:
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        body = self._multipart(
            "sendDocument",
            fields,
            {"document": (path.name, data, mime)},
        )
        return (body.get("result") or {}).get("message_id")

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self.call("getFile", {"file_id": file_id}, timeout=20).get("result", {})

    def download_file(self, file_id: str, dest: Path) -> Path:
        info = self.get_file(file_id)
        file_path = info.get("file_path") or ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if file_path.startswith("/") and Path(file_path).is_file():
            dest.write_bytes(Path(file_path).read_bytes())
            return dest
        url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        req = urllib.request.Request(url)
        with self._opener.open(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
        return dest

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self.call("setMyCommands", {"commands": commands})

    def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        try:
            self.call(
                "answerCallbackQuery",
                {"callback_query_id": callback_query_id, "text": text},
                retries=1,
            )
        except Exception:
            log.debug("answerCallbackQuery failed", exc_info=True)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        try:
            self.call("editMessageText", params)
            return True
        except TelegramError as e:
            if "message is not modified" in e.description.lower():
                return True
            if parse_mode:
                params.pop("parse_mode", None)
                try:
                    self.call("editMessageText", params)
                    return True
                except TelegramError as e2:
                    if "message is not modified" in e2.description.lower():
                        return True
                    log.warning("editMessageText failed: %s", e2)
                    return False
            log.warning("editMessageText failed: %s", e)
            return False


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
