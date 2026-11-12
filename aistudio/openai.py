"""The OpenAI messages for AI Studio.

AI Studio takes one prompt and the files that go with it, so the conversation is flattened:
the text of the system prompt and of the user turns becomes the prompt, and the pictures
become files that the page uploads.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any

DATA_URL = re.compile(r"^data:(?P<type>[\w.+-]+/[\w.+-]+)?;base64,(?P<data>.+)$", re.DOTALL)


def text_of(content: Any) -> str:
    """The text of a message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def images_of(messages: list[dict]) -> list[tuple[str, bytes]]:
    """The pictures of the conversation: (file name, bytes)."""
    out: list[tuple[str, bytes]] = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for i, part in enumerate(content):
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            url = (part.get("image_url") or {}).get("url")
            if not isinstance(url, str):
                continue
            match = DATA_URL.match(url.strip())
            if not match:
                continue
            try:
                data = base64.b64decode(match.group("data"), validate=False)
            except (binascii.Error, ValueError):
                continue
            kind = match.group("type") or "image/png"
            ext = kind.split("/")[-1].split("+")[0] or "png"
            out.append((f"image-{len(out) + 1}.{ext}", data))
    return out


def prompt_of(messages: list[dict]) -> str:
    """The prompt: the system prompt and the questions, joined."""
    parts = [
        text_of(m.get("content"))
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("system", "user")
    ]
    return "\n\n".join(p for p in parts if p.strip())
