"""The signed-in Chrome on this server as the source of the Gemini session.

Since 2026-11-21 Google answers this server's IP with 302 -> www.google.com/sorry for the
/app page fetched by a script and for every batchexecute RPC (account status, models,
quota), from curl_cffi and from fetch() inside the browser tab alike. A real page
navigation in the browser passes, and so does StreamGenerate (the answers). So the access
token is read from the loaded Gemini tab (window.WIZ_global_data), the cookie jar from the
browser, and the client then talks to StreamGenerate directly - no browser time per answer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import uuid
from pathlib import Path
import urllib.request

import websockets
from curl_cffi.requests import AsyncSession
from gemini_webapi.exceptions import APIError, AuthError
from gemini_webapi.types import AvailableModel

from .config import CFG

log = logging.getLogger("browser")

CDP = f"http://127.0.0.1:{int(os.environ.get('GEMINI_CDP_PORT', '9334'))}"
# The account's model list, captured where batchexecute works (gemini-proxy-lab, models.json):
# Google does not hand it out on this IP, and the ids are stable.
MODELS_FILE = Path(os.environ.get("GEMINI_MODELS_FILE", "/opt/gemini-proxy/models.json"))
ORIGIN = "https://gemini.google.com"
FETCH_TIMEOUT = 60.0


class BrowserUnavailable(APIError):
    """The browser is down, has no Gemini tab, or Google blocked it too."""


# ------------------------------------------------------------------------------ CDP
def _json(path: str, method: str = "GET"):
    req = urllib.request.Request(CDP + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (localhost only)
        return json.load(r)


async def _cdp(ws_url: str, method: str, params: dict | None = None,
               timeout: float = FETCH_TIMEOUT) -> dict:
    async with websockets.connect(ws_url, max_size=None, open_timeout=10) as ws:
        await ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg.get("id") != 1:
                continue
            if "error" in msg:
                raise BrowserUnavailable(f"CDP {method}: {msg['error'].get('message')}")
            return msg.get("result") or {}


async def browser_cookies() -> list[dict]:
    """Every google.com cookie the browser holds (name, value, domain, path)."""
    try:
        version = await asyncio.to_thread(_json, "/json/version")
    except Exception as e:  # noqa: BLE001
        raise BrowserUnavailable(f"browser is not running ({e})") from e
    r = await _cdp(version["webSocketDebuggerUrl"], "Storage.getCookies")
    return [c for c in r.get("cookies") or [] if c.get("domain", "").endswith("google.com")]


def _first(regex: re.Pattern, text: str) -> str | None:
    m = regex.search(text)
    return m.group(1) if m else None


def _tab_path(n: str) -> str:
    return "/app" if n == "0" else f"/u/{n}/app"


_WIZ = ("JSON.stringify(window.WIZ_global_data ? {a: WIZ_global_data.SNlM0e, "
        "b: WIZ_global_data.cfb2h, s: WIZ_global_data.FdrFJe, l: WIZ_global_data.TuX5cc, "
        "p: WIZ_global_data.qKIAYe, e: WIZ_global_data.oPEP7c} : {})")


def _close(target_id: str) -> None:
    urllib.request.urlopen(f"{CDP}/json/close/{target_id}", timeout=10).read()  # noqa: S310


async def _fresh_tokens(n: str) -> dict:
    """Open /u/N/app in a new background tab, read its tokens and account, close the tab.

    A fresh navigation each time: Google renumbers the accounts when one is added or
    signed out, so an old tab can show an account that is no longer u/N. A navigation
    passes Google's block where a fetch() of the same page does not.
    """
    tab = await asyncio.to_thread(_json, f"/json/new?{ORIGIN}{_tab_path(n)}", "PUT")
    try:
        for _ in range(90):
            await asyncio.sleep(0.5)
            try:
                r = await _cdp(tab["webSocketDebuggerUrl"], "Runtime.evaluate",
                               {"expression": _WIZ, "returnByValue": True}, 5)
                w = json.loads((r.get("result") or {}).get("value") or "{}")
            except (APIError, OSError, asyncio.TimeoutError, websockets.WebSocketException):
                continue
            if w.get("a"):
                return w
        raise AuthError(f"u/{n}: no signed-in Gemini page in the browser")
    finally:
        try:
            await asyncio.to_thread(_close, tab["id"])
        except OSError:
            pass


async def locate(email: str, hint: str, tries: int = 4) -> tuple[str, dict]:
    """The u/N that shows `email` now, with its tokens; `hint` (the last known N) first."""
    order = [hint] + [str(i) for i in range(tries) if str(i) != hint]
    seen = []
    for n in order:
        w = await _fresh_tokens(n)
        got = (w.get("e") or "").lower()
        if not email or got == email.lower():
            return n, w
        seen.append(got)
        if got in seen[:-1]:
            break  # Google sends unknown indexes to the default account: nothing further
    raise AuthError(f"{email} is not signed in in the browser")


async def connect(client, hint: str, timeout: float, email: str = "") -> str:  # noqa: ANN001
    """Make `client` ready from the browser without GeminiClient.init(); returns the u/N.

    init() fetches the /app page and Google answers it with /sorry for this IP; the tab's
    token plus the browser's cookie jar work for StreamGenerate and batchexecute. The
    account is found by e-mail, not by a fixed index: Google renumbers them.
    """
    n, w = await locate(email, hint)
    session = AsyncSession(impersonate="chrome", proxy=CFG.proxy or None, timeout=timeout)
    for c in await browser_cookies():
        session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path") or "/")
    client.client = session
    client.access_token = w["a"]
    client.build_label = w.get("b")
    client.session_id = w.get("s")
    client.language = w.get("l") or "ru"
    if w.get("p"):
        client.push_id = w["p"]
    client.timeout = timeout
    client._sessionid = str(uuid.uuid4()).upper()  # noqa: SLF001
    client._reqid = random.randint(10000, 99999)  # noqa: S311, SLF001
    client._running = True  # noqa: SLF001
    _load_models(client)
    return n

    # A blocked batchexecute (activity sync around every answer) would close the client,
    # and the library would then re-init it the direct way - as a guest. Keep it open.
    rpc = client._batch_execute  # noqa: SLF001

    async def _keep_open(payloads, source_path="/app", close_on_error=True, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return await rpc(payloads, source_path, close_on_error=False, **kwargs)

    client._batch_execute = _keep_open  # noqa: SLF001




def _load_models(client) -> None:  # noqa: ANN001
    """Fill the client's model registry from MODELS_FILE (no-op when it is missing)."""
    try:
        snapshot = json.loads(MODELS_FILE.read_text())
    except (OSError, ValueError) as e:
        log.warning("no model snapshot (%s): the account default model answers", e)
        return
    for raw in snapshot:
        model = AvailableModel(**raw)
        client._model_registry[model.model_id] = model  # noqa: SLF001
