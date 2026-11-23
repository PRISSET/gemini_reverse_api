"""OpenAI Chat Completions <-> the Gemini web app.

The web app has no system role and no JSON mode, so the conversation becomes one prompt
and the JSON is asked for in words (the caller retries when the answer is not JSON).
"""
from __future__ import annotations

import base64
import binascii
import time
from typing import Any


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict)
        )
    return ""


_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def to_files(messages: list[dict]) -> list[bytes]:
    """The pictures of the user turns, from OpenAI `image_url` parts with base64 data URLs.

    Only data URLs: the proxy does not fetch remote URLs on the caller's behalf.
    """
    files: list[bytes] = []
    for m in messages:
        content = m.get("content")
        if m.get("role") == "assistant" or not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            ref = part.get("image_url")
            url = ref.get("url", "") if isinstance(ref, dict) else str(ref or "")
            files.append(_decode_data_url(url))
    return files


def file_name(data: bytes, index: int) -> str:
    """A name with the right extension: the web app picks the type by it."""
    if data.startswith(b"\xff\xd8"):
        ext = "jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    elif data.startswith(b"GIF8"):
        ext = "gif"
    else:
        ext = "png"
    return f"image_{index}.{ext}"


def _decode_data_url(url: str) -> bytes:
    if not url.startswith("data:") or ";base64," not in url:
        raise ValueError("only base64 data URLs are accepted for images")
    head, payload = url[5:].split(";base64,", 1)
    if head and head not in _IMAGE_TYPES:
        raise ValueError(f"unsupported image type {head}")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("broken base64 in an image data URL") from e
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError("image is larger than 20 MiB")
    return data


def to_prompt(messages: list[dict]) -> str:
    """The conversation as one prompt: the system prompt first, then the turns."""
    system: list[str] = []
    turns: list[str] = []
    for m in messages:
        role = m.get("role")
        text = _text(m.get("content")).strip()
        if not text:
            continue
        if role == "system":
            system.append(text)
        elif role == "assistant":
            turns.append(f"[Предыдущий ответ]\n{text}")
        else:
            turns.append(text)
    head = "\n\n".join(system)
    body = "\n\n".join(turns)
    return f"{head}\n\n{body}" if head else body


def to_completion(text: str, model: str) -> dict:
    """A finished answer, OpenAI shaped."""
    return {
        "id": f"chatcmpl-{int(time.time() * 1000):x}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def to_delta(text: str, model: str) -> dict:
    """One streamed delta, OpenAI shaped."""
    return {
        "id": f"chatcmpl-{int(time.time() * 1000):x}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def to_error(message: str, kind: str = "upstream_error") -> dict:
    return {"error": {"message": message, "type": kind}}
