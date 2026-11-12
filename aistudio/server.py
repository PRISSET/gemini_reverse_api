"""An OpenAI-compatible API over AI Studio.

    POST /v1/chat/completions
    GET  /v1/models
    GET  /health

The model in the request is resolved against the list the account actually has; the bearer
names the account (by id, e-mail or label), `pool` takes the first one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import accounts as accounts_mod
from . import cookies as cookies_mod
from . import models as models_mod
from . import openai as openai_mod
from . import rpc
from .cdp import Cdp
from .config import CFG
from .session import StudioPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aistudio")

app = FastAPI(title="AI Studio proxy", docs_url=None, redoc_url=None)

_cdp: Cdp | None = None
_pool: StudioPool | None = None
_lock = asyncio.Lock()


async def browser() -> tuple[Cdp, StudioPool]:
    """The browser connection and the tab pool, opened once."""
    global _cdp, _pool
    if _cdp is None:
        _cdp = await Cdp(CFG.cdp_host, CFG.cdp_port).__aenter__()
        _pool = StudioPool(_cdp)
    assert _pool is not None
    return _cdp, _pool


@app.get("/health")
async def health() -> dict:
    account = accounts_mod.pick()
    out: dict = {"status": "ok", "account": account.email if account else None}
    if account:
        try:
            cdp, _ = await browser()
            jar = await cookies_mod.jar(cdp)
            out["tier"] = await rpc.benefit_tier(jar)
        except (rpc.RpcError, Exception) as e:  # noqa: BLE001
            out["tier_error"] = str(e)[:120]
    return out


@app.get("/v1/models")
@app.get("/models")
async def models() -> dict:
    account = accounts_mod.pick()
    if account is None:
        return {"object": "list", "data": []}
    try:
        listed = await models_mod.models(account.cookies)
    except rpc.RpcError:
        listed = []
    return {
        "object": "list",
        "data": [{"id": m["id"], "object": "model", "created": 0, "owned_by": "aistudio"} for m in listed],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions", response_model=None)
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    if not messages:
        return JSONResponse({"error": {"message": "no messages", "type": "invalid_request_error"}},
                            status_code=400)

    # The bearer names the account (id, e-mail or label); without one the accounts
    # are used in turn.
    bearer = request.headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:].strip()
    account = accounts_mod.pick(bearer) if bearer else accounts_mod.next_account()
    if account is None:
        return JSONResponse({"error": {"message": "нет аккаунтов", "type": "no_account"}},
                            status_code=503)

    prompt = openai_mod.prompt_of(messages)
    pictures = openai_mod.images_of(messages)

    cdp, pool = await browser()
    jar = await cookies_mod.jar(cdp)
    try:
        listed = await models_mod.models(jar)
    except rpc.RpcError:
        listed = []
    model = models_mod.resolve(listed, str(body.get("model") or CFG.default_model))

    assert pool is not None
    # One tab per account and model, but a tab answers one question at a time.
    async with _lock:
        files: list[str] = []
        try:
            for name, data in pictures:
                path = tempfile.NamedTemporaryFile(prefix="aistudio-", suffix=Path(name).suffix,
                                                   delete=False)
                path.write(data)
                path.close()
                files.append(path.name)
            answer = await pool.tab(model or "", account.authuser).ask(
                prompt, timeout=CFG.timeout, files=files or None
            )
        except Exception as e:  # noqa: BLE001
            log.warning("no answer: %s", e)
            return JSONResponse({"error": {"message": str(e), "type": "upstream_error"}},
                                status_code=502)
        finally:
            for path in files:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    out = {
        "id": f"chatcmpl-{int(time.time() * 1000):x}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return JSONResponse(out)
