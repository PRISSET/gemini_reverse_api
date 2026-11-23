"""Google multi-login: the N of gemini.google.com/u/N/app.

One browser profile can hold several signed-in Google accounts. The cookie jar
authenticates all of them at once; the index in the path says which one is being talked to,
and the access token (SNlM0e) is fetched per account. gemini_webapi hardcodes the default
(index 0) endpoints, so the endpoint constants are swapped for a prefixed shim.

The second part is the full cookie jar: gemini_webapi's own token fetch sends only
__Secure-1PSID and __Secure-1PSIDTS, which cannot authenticate a non-default account and
is fragile even for the default one. Here the session carries every cookie the browser
would send.
"""
from __future__ import annotations

import sys
from contextvars import ContextVar

import gemini_webapi.client  # noqa: F401  (make sure the module is imported)
import gemini_webapi.utils.get_access_token  # noqa: F401
import gemini_webapi.utils.rotate_1psidts  # noqa: F401
from gemini_webapi.constants import Endpoint as _E, Headers as _H

# The package re-exports the functions, so attribute access would bind the function and the
# patch below would silently do nothing. sys.modules always holds the module.
_client = sys.modules["gemini_webapi.client"]
_gat = sys.modules["gemini_webapi.utils.get_access_token"]
_rot = sys.modules["gemini_webapi.utils.rotate_1psidts"]

# The account of the running request. A ContextVar, not a global: every asyncio task has its
# own copy, so two accounts answering at once cannot send each other's requests to the wrong
# /u/N/ path (a global swap did exactly that).
_account: ContextVar[str | None] = ContextVar("gemini_authuser", default=None)
_jar_installed = False


def _prefix() -> str:
    return f"https://gemini.google.com/u/{_account.get() or '0'}"


class _EndpointMeta(type):
    """Endpoint URLs computed per request from the current task's account."""

    @property
    def INIT(cls) -> str:  # noqa: N802
        return f"{_prefix()}/app"

    @property
    def GENERATE(cls) -> str:  # noqa: N802
        return f"{_prefix()}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"

    @property
    def BATCH_EXEC(cls) -> str:  # noqa: N802
        return f"{_prefix()}/_/BardChatUi/data/batchexecute"


class _EndpointShim(metaclass=_EndpointMeta):
    GOOGLE = str(_E.GOOGLE)
    ROTATE_COOKIES = str(_E.ROTATE_COOKIES)
    UPLOAD = str(_E.UPLOAD)


for _mod in (_client, _gat, _rot):
    _mod.Endpoint = _EndpointShim


def apply_authuser(index: str | int | None) -> None:
    """Point the current request (asyncio task) at Google account `index` (None = u/0)."""
    _account.set(None if index is None or str(index) == "" else str(index))


def current() -> str | None:
    """The account index the current request talks to."""
    return _account.get()


def install_full_jar(jar_getter) -> None:
    """Make the init request use the FULL google.com cookie jar.

    `jar_getter()` is called fresh at every init, so a re-init picks up the cookies the
    browser has rotated in the meantime.
    """
    global _jar_installed
    if _jar_installed:
        return

    async def _send_request(client, cookies, verbose=False):  # noqa: ANN001
        client.cookies.clear()
        jar = {}
        try:
            jar = jar_getter() or {}
        except Exception:  # noqa: BLE001
            jar = {}
        if jar:
            for k, v in jar.items():
                client.cookies.set(k, v, domain=".google.com")
        elif hasattr(cookies, "items"):
            for k, v in cookies.items():
                client.cookies.set(k, v, domain=".google.com")
        else:
            client.cookies.update(cookies)
        resp = await client.get(_gat.Endpoint.INIT, headers=_H.GEMINI.value)
        resp.raise_for_status()
        return resp

    _gat._send_request = _send_request
    _jar_installed = True
