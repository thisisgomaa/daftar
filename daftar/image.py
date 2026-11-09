"""OpenAI Images API helper for `daftar image`."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _post_json(url: str, payload: dict, key: str, timeout: float = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _multipart_edit(
    url: str,
    key: str,
    prompt: str,
    refs: list[Path],
    model: str,
    size: str,
    timeout: float = 180,
) -> dict:
    import time

    boundary = f"----daftarimg{int(time.time() * 1000)}"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    add_field("model", model)
    add_field("prompt", prompt)
    if size:
        add_field("size", size)
    for ref in refs:
        data = ref.read_bytes()
        field = "image[]" if len(refs) > 1 else "image"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{field}"; filename="{ref.name}"\r\n'.encode()
        )
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def generate(
    prompt: str,
    out: Path,
    *,
    refs: Optional[list[Path]] = None,
    size: str = "1024x1024",
    model: Optional[str] = None,
) -> Path:
    key = _api_key()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it via `daftar setup` or export it."
        )
    model = model or os.environ.get("IMAGE_MODEL", "gpt-image-1")
    refs = refs or []
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if refs:
        body = _multipart_edit(
            "https://api.openai.com/v1/images/edits",
            key,
            prompt,
            refs,
            model,
            size,
        )
    else:
        body = _post_json(
            "https://api.openai.com/v1/images/generations",
            {
                "model": model,
                "prompt": prompt,
                "size": size,
                "n": 1,
            },
            key,
        )

    data = (body.get("data") or [None])[0] or {}
    if data.get("b64_json"):
        out.write_bytes(base64.b64decode(data["b64_json"]))
    elif data.get("url"):
        req = urllib.request.Request(data["url"])
        with urllib.request.urlopen(req, timeout=120) as resp:
            out.write_bytes(resp.read())
    else:
        raise RuntimeError(f"Unexpected Images API response: {json.dumps(body)[:400]}")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="daftar image")
    p.add_argument("--prompt", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ref", action="append", default=[])
    p.add_argument("--size", default="1024x1024")
    p.add_argument("--model", default=None)
    args = p.parse_args(argv)
    try:
        path = generate(
            args.prompt,
            Path(args.out),
            refs=[Path(r) for r in args.ref],
            size=args.size,
            model=args.model,
        )
        print(json.dumps({"ok": True, "path": str(path.resolve())}))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
