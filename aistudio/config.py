"""Settings of the AI Studio module."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    # The same accounts file the Gemini proxy uses: the cookie jar of every account.
    accounts_file: str = _env("ACCOUNTS_FILE", "/etc/gemini-proxy/accounts.json")
    # The browser that holds the session (the debug port of Chrome).
    cdp_host: str = _env("AISTUDIO_CDP_HOST", "127.0.0.1")
    cdp_port: int = int(_env("AISTUDIO_CDP_PORT", "9337") or 9337)
    # The account to use: by id (a1), by e-mail, or the first one.
    account: str = _env("AISTUDIO_ACCOUNT", "")
    # The model when the caller does not name one.
    default_model: str = _env("AISTUDIO_DEFAULT_MODEL", "gemini-flash")
    # How long one answer may take.
    timeout: float = float(_env("AISTUDIO_TIMEOUT", "180") or 180)


CFG = Config()
