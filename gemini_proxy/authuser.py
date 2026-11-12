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

import gemini_webapi.client  # noqa: F401  (make sure the module is imported)
import gemini_webapi.utils.get_access_token  # noqa: F401
import gemini_webapi.utils.rotate_1psidts  # noqa: F401
from gemini_webapi.constants import Endpoint as _E, Headers as _H

# The package re-exports the functions, so attribute access would bind the function and the
# patch below would silently do nothing. sys.modules always holds the module.
_client = sys.modules["gemini_webapi.client"]
_gat = sys.modules["gemini_webapi.utils.get_access_token"]
_rot = sys.modules["gemini_webapi.utils.rotate_1psidts"]

_current: str | None = None
_jar_installed = False


def apply_authuser(index: str | int | None) -> None:
    """Point gemini_webapi at the Google account `index` (None = the default, u/0)."""
    global _current
    if index is None or str(index) == "":
        return
    n = str(index)
    if _current == n:
        return
    prefix = f"https://gemini.google.com/u/{n}"

    class _EndpointShim:
        GOOGLE = str(_E.GOOGLE)
        INIT = f"{prefix}/app"
        GENERATE = f"{prefix}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        ROTATE_COOKIES = str(_E.ROTATE_COOKIES)
        UPLOAD = str(_E.UPLOAD)
        BATCH_EXEC = f"{prefix}/_/BardChatUi/data/batchexecute"

    for mod in (_client, _gat, _rot):
        mod.Endpoint = _EndpointShim
    _current = n


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
