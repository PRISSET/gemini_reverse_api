"""The accounts: which Google account the module talks as.

The same file the Gemini proxy uses — the browser owns the session and the harvester keeps
the cookies fresh.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass

from .config import CFG


@dataclass
class Account:
    id: str
    authuser: str
    email: str
    label: str
    cookies: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.id}:{self.authuser}"


def load() -> list[Account]:
    try:
        raw = json.loads(open(CFG.accounts_file).read())
    except (OSError, ValueError):
        return []
    out = []
    for i, item in enumerate(raw.get("accounts") or []):
        if not isinstance(item, dict):
            continue
        cookies = {str(k): str(v) for k, v in (item.get("cookies") or {}).items()}
        if not cookies.get("__Secure-1PSID"):
            continue
        out.append(Account(
            id=str(item.get("id") or f"a{i + 1}"),
            authuser=str(item.get("authuser") or ""),
            email=str(item.get("email") or ""),
            label=str(item.get("label") or ""),
            cookies=cookies,
        ))
    return out


def pick(wanted: str = "") -> Account | None:
    """The account to use: by id, by e-mail, by label, or the first one."""
    accounts = load()
    if not accounts:
        return None
    wanted = (wanted or CFG.account).strip().lower()
    if not wanted:
        return accounts[0]
    for a in accounts:
        if a.id.lower() == wanted or a.email.lower() == wanted or a.label.lower() == wanted:
            return a
    return accounts[0]


_turn = itertools.count()


def next_account() -> Account | None:
    """The next account in turn: the requests spread over the accounts."""
    accounts = load()
    if not accounts:
        return None
    return accounts[next(_turn) % len(accounts)]
