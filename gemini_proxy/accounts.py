"""The accounts: the Google accounts signed in to the browser.

The browser (Chrome) owns the session. It writes the current cookie jar of every signed-in
account here; the proxy reads it, talks to gemini.google.com with it, and re-reads the file
when the session goes stale. Nothing here expires on its own: as long as the browser stays
logged in, the cookies keep coming.

    {
      "accounts": [
        { "id": "a1", "authuser": "0", "email": "you@gmail.com", "label": "Pro",
          "cookies": { "__Secure-1PSID": "...", "SID": "...", ... },
          "updatedAt": "2026-11-12T20:00:00.000Z" }
      ]
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import CFG


@dataclass
class Account:
    id: str
    # The N of gemini.google.com/u/N/app — which of the signed-in accounts this is.
    authuser: str = ""
    email: str = ""
    label: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.id}:{self.authuser}"

    @property
    def psid(self) -> str:
        return self.cookies.get("__Secure-1PSID", "")

    @property
    def psidts(self) -> str:
        return self.cookies.get("__Secure-1PSIDTS", "")

    @property
    def usable(self) -> bool:
        return bool(self.psid and self.psidts)

    def view(self) -> dict:
        return {
            "id": self.id,
            "authuser": self.authuser,
            "email": self.email,
            "label": self.label,
            "cookies": len(self.cookies),
            "updatedAt": self.updated_at or None,
        }


def _path() -> Path:
    # Read the environment every time: the tests and the service may point it elsewhere.
    return Path(os.environ.get("ACCOUNTS_FILE") or CFG.accounts_file)


def load() -> list[Account]:
    """Every account in the file. A missing or broken file means no accounts."""
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    out = []
    for i, item in enumerate(raw.get("accounts") or []):
        if not isinstance(item, dict):
            continue
        cookies = item.get("cookies") or {}
        out.append(Account(
            id=str(item.get("id") or f"a{i + 1}"),
            authuser=str(item.get("authuser") or ""),
            email=str(item.get("email") or ""),
            label=str(item.get("label") or ""),
            cookies={str(k): str(v) for k, v in cookies.items()},
            updated_at=str(item.get("updatedAt") or ""),
        ))
    return out


def save(accounts: list[Account]) -> None:
    """Replace the file atomically (temp file + rename in the same directory)."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"accounts": [
        {"id": a.id, "authuser": a.authuser, "email": a.email, "label": a.label,
         "cookies": a.cookies, "updatedAt": a.updated_at}
        for a in accounts
    ]}
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o640)
    # The proxy runs as another user: the file has to stay readable for it.
    try:
        import grp
        os.chown(tmp, 0, grp.getgrnam("hfnews").gr_gid)
    except (OSError, KeyError, ImportError):
        pass
    tmp.replace(path)
