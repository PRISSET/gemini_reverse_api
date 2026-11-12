"""A small Chrome DevTools Protocol client.

Only what the AI Studio module needs: open a tab, evaluate JavaScript in it, close it.
"""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

log = logging.getLogger("aistudio.cdp")


class CdpError(RuntimeError):
    pass


class Cdp:
    """One browser connection. `evaluate` runs JavaScript in the page and returns its value."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9337):
        self.base = f"http://{host}:{port}"
        self._ws = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def __aenter__(self) -> "Cdp":
        version = await self._version()
        self._ws = await websockets.connect(
            version["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024, open_timeout=20
        )
        self._reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws:
            await self._ws.close()
        if getattr(self, "_reader", None):
            self._reader.cancel()

    async def _version(self) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base}/json/version")
            r.raise_for_status()
            return r.json()

    async def _read(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            fut = self._pending.pop(msg.get("id", -1), None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(CdpError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result", {}))

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None):
        if self._ws is None:
            raise CdpError("not connected")
        self._id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[self._id] = fut
        payload = {"id": self._id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self._ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=180)

    # ------------------------------------------------------------------ tabs
    async def new_tab(self, url: str) -> str:
        """A new tab, attached; returns its session id."""
        target = await self.send("Target.createTarget", {"url": url})
        attached = await self.send("Target.attachToTarget",
                                   {"targetId": target["targetId"], "flatten": True})
        session = attached["sessionId"]
        await self.send("Page.enable", {}, session)
        await self.send("Runtime.enable", {}, session)
        return session

    async def close_tab(self, session_id: str) -> None:
        try:
            await self.send("Target.closeTarget", {"sessionId": session_id})
        except Exception:  # noqa: BLE001
            pass

    async def navigate(self, session_id: str, url: str) -> None:
        await self.send("Page.navigate", {"url": url}, session_id)

    async def cookies(self, domain: str = ".google.com") -> dict[str, str]:
        """The cookie jar of the browser, live.

        The browser owns the session and rotates the cookies; a jar from a file goes stale
        in minutes and Google then refuses the SAPISIDHASH signature. Asking the browser is
        the only way to stay in sync.
        """
        result = await self.send("Storage.getCookies")
        jar: dict[str, str] = {}
        for cookie in result.get("cookies") or []:
            host = str(cookie.get("domain", "")).lstrip(".")
            if host != "google.com" and not host.endswith(".google.com"):
                continue
            jar[str(cookie["name"])] = str(cookie.get("value", ""))
        return jar

    async def evaluate(self, session_id: str, expression: str):
        """Run JavaScript in the page and return its value."""
        result = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            session_id,
        )
        if result.get("exceptionDetails"):
            raise CdpError(str(result["exceptionDetails"])[:300])
        return result.get("result", {}).get("value")
