"""Settings, read from the environment (systemd loads them from an EnvironmentFile)."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    port: int = _int("PROXY_PORT", 3086)
    # Where the accounts live. Written by the login tool (the browser session owner).
    accounts_file: str = _env("ACCOUNTS_FILE", "/etc/gemini-proxy/accounts.json")
    # Key for the admin routes. Without it those routes do not exist.
    admin_key: str = _env("PROXY_ADMIN_KEY")

    # The model used when the caller does not name one. The web app resolves model names
    # per account, so an unknown name falls back to the account's own default.
    default_model: str = _env("GEMINI_DEFAULT_MODEL", "gemini-flash")
    models: list[str] = field(default_factory=lambda: [
        m.strip() for m in _env("GEMINI_MODELS", (
            "gemini-pro,gemini-flash,gemini-flash-lite,gemini-auto"
        )).split(",") if m.strip()
    ])

    # SOCKS5/HTTP proxy for google.com, when the server needs one.
    proxy: str = _env("GEMINI_PROXY")
    # How long one answer may take.
    timeout: float = _float("GEMINI_TIMEOUT", 450)
    # How long the first request to a fresh account may take (it fetches the model list).
    init_timeout: float = _float("GEMINI_INIT_TIMEOUT", 120)
    # How many accounts one request may try before it gives up.
    max_retries: int = _int("POOL_MAX_RETRIES", 3)
    # How often the accounts file is re-read (the browser session owner refreshes the cookies).
    accounts_reload_s: float = _float("ACCOUNTS_RELOAD_S", 60)


CFG = Config()
