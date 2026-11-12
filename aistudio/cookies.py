"""The cookie jar of the account.

The browser owns the session and rotates the cookies, so the jar is read from the browser
live. The accounts file is only a fallback: it is what the harvester writes, and it goes
stale within minutes — Google refuses the SAPISIDHASH signature of a jar whose SAPISID has
already been rotated.
"""
from __future__ import annotations

import logging

from . import accounts as accounts_mod
from .cdp import Cdp
from .config import CFG

log = logging.getLogger("aistudio.cookies")


async def jar(cdp: Cdp | None = None, account: str = "") -> dict[str, str]:
    """The cookies of the account: from the browser if it is there, from the file otherwise."""
    if cdp is not None:
        try:
            live = await cdp.cookies()
            if live.get("__Secure-1PSID"):
                return live
            log.info("в браузере нет сессии Google")
        except Exception as e:  # noqa: BLE001
            log.warning("не смог прочитать куки из браузера: %s", e)
    found = accounts_mod.pick(account)
    return found.cookies if found else {}
