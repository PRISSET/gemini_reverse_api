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


async def _account_tab(n: str) -> str:
    """The debugger URL of a tab showing account u/N; navigates a new one when missing.

    A page navigation passes Google's block where a fetch() of the same page does not.
    """
    tabs = await asyncio.to_thread(_json, "/json/list")
    want = (ORIGIN + "/app", ORIGIN + "/u/0/app") if n == "0" else (ORIGIN + f"/u/{n}/",)
    for tab in tabs:
        if tab.get("type") == "page" and tab.get("url", "").startswith(want):
            return tab["webSocketDebuggerUrl"]
    log.info("opening a tab for u/%s", n)
    tab = await asyncio.to_thread(_json, f"/json/new?{ORIGIN}{_tab_path(n)}", "PUT")
    return tab["webSocketDebuggerUrl"]


_WIZ = ("JSON.stringify(window.WIZ_global_data ? {a: WIZ_global_data.SNlM0e, "
        "b: WIZ_global_data.cfb2h, s: WIZ_global_data.FdrFJe, l: WIZ_global_data.TuX5cc, "
        "p: WIZ_global_data.qKIAYe} : {})")


async def page_tokens(n: str) -> dict:
    """SNlM0e and friends from the loaded tab of account u/N (waits up to ~30 s for it)."""
    ws = await _account_tab(n)
    for _ in range(60):
        try:
            r = await _cdp(ws, "Runtime.evaluate", {"expression": _WIZ, "returnByValue": True}, 5)
            w = json.loads((r.get("result") or {}).get("value") or "{}")
            if w.get("a"):
                return w
        except (APIError, OSError, asyncio.TimeoutError, websockets.WebSocketException):
            pass
        await asyncio.sleep(0.5)
    raise AuthError(f"u/{n}: the tab has no signed-in Gemini page")


async def connect(client, n: str, timeout: float) -> None:  # noqa: ANN001
    """Make `client` ready from the browser without GeminiClient.init().

    init() fetches the /app page and runs batchexecute RPCs, and Google answers both with
    /sorry for this IP. The answers (StreamGenerate) pass, so the client gets the browser's
    token and cookie jar and talks to StreamGenerate directly; batchexecute calls made along
    the way (activity sync) fail quietly as before. No model list: the account default runs.
    """
    w = await page_tokens(n)
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
