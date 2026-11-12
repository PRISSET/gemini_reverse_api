"""OpenAI Chat Completions <-> the Gemini web app.

The web app has no system role and no JSON mode, so the conversation becomes one prompt
and the JSON is asked for in words (the caller retries when the answer is not JSON).
"""
from __future__ import annotations

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
