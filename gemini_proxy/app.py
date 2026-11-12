"""The proxy: an OpenAI-compatible API over the Gemini web app.

    POST /v1/chat/completions   the bot and the site chat
    GET  /v1/models             the models this account set offers
    GET  /health                the state of the pool
    GET  /admin/accounts        the accounts (admin key)
    POST /admin/check           re-init every account and read its quota (admin key)
"""
from __future__ import annotations

import asyncio
import time
import json
import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import accounts as accounts_mod
from . import openai
from .config import CFG
from .pool import POOL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("proxy")

app = FastAPI(title="Gemini web proxy", docs_url=None, redoc_url=None)


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization", "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()


def _authorized(request: Request) -> bool:
    if not CFG.admin_key:
        return False
    given = request.headers.get("x-admin-key", "")
    return given == CFG.admin_key


@app.get("/health")
async def health() -> dict:
    slots = POOL.slots()
    return {
        "status": "ok",
        "accounts": len(slots),
        "usable": len([s for s in slots if s.status() != "dead"]),
        "slots": POOL.view(),
    }


@app.get("/v1/models")
@app.get("/models")
async def models() -> dict:
    listed = CFG.models
    known: list[str] = []
    for slot in POOL.slots():
        for m in slot.models:
            name = getattr(m, "model_name", None) or getattr(m, "display_name", None)
            if name and name not in known:
                known.append(name)
    ids = known or listed
    return {
        "object": "list",
        "data": [{"id": mid, "object": "model", "created": 0, "owned_by": "gemini-web"} for mid in ids],
    }


@app.post("/v1/chat/completions", response_model=None)
@app.post("/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return JSONResponse(openai.to_error("no messages", "invalid_request_error"), status_code=400)

    model = str(body.get("model") or CFG.default_model)
    prompt = openai.to_prompt(messages)
    stream = bool(body.get("stream"))
    # The bearer may name a group: the bot writes posts on the paid account, the site chat
    # answers on the free one.
    group = _bearer(request)

    if not stream:
        try:
            text, slot = await POOL.generate(prompt, model, group)
        except Exception as e:  # noqa: BLE001
            log.warning("no answer: %s", e)
            return JSONResponse(openai.to_error(str(e)), status_code=502)
        log.info("answered by %s (%d chars)", slot.key, len(text))
        return JSONResponse(openai.to_completion(text, model))

    async def events():
        try:
            async for delta, slot in POOL.generate_stream(prompt, model, group):
                yield f"data: {json.dumps(openai.to_delta(delta, model), ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except Exception as e:  # noqa: BLE001
            log.warning("stream failed: %s", e)
            yield f"data: {json.dumps(openai.to_error(str(e)), ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/admin/accounts")
async def admin_accounts(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse(openai.to_error("Not found", "not_found"), status_code=404)
    return JSONResponse({"accounts": [_account_view(s) for s in POOL.slots()]})


@app.post("/admin/check")
async def admin_check(request: Request) -> JSONResponse:
    """Re-init every account and read what is left of its quota."""
    if not _authorized(request):
        return JSONResponse(openai.to_error("Not found", "not_found"), status_code=404)
    POOL.reload()
    for slot in POOL.slots():
        try:
            client = await POOL._client_for(slot)
            POOL._read_quota(slot, client)
            slot.last_ok = slot.last_ok or time.time()
        except Exception as e:  # noqa: BLE001
            slot.fail(str(e))
    return JSONResponse({"accounts": [_account_view(s) for s in POOL.slots()]})


@app.put("/admin/accounts/{account_id}")
async def admin_rename(account_id: str, request: Request) -> JSONResponse:
    """Rename an account (the label the owner gave it)."""
    if not _authorized(request):
        return JSONResponse(openai.to_error("Not found", "not_found"), status_code=404)
    body = await request.json()
    found = accounts_mod.load()
    for account in found:
        if account_id != account_id_of(account):
            continue
        account.label = str(body.get("label") or "")[:60]
        accounts_mod.save(found)
        POOL.reload()
        return JSONResponse({"accounts": [_account_view(s) for s in POOL.slots()]})
    return JSONResponse(openai.to_error("Аккаунт не найден", "not_found"), status_code=404)


@app.delete("/admin/accounts/{account_id}")
async def admin_delete(account_id: str, request: Request) -> JSONResponse:
    """Forget an account (the browser session is left alone)."""
    if not _authorized(request):
        return JSONResponse(openai.to_error("Not found", "not_found"), status_code=404)
    found = accounts_mod.load()
    kept = [a for a in found if account_id_of(a) != account_id]
    if len(kept) == len(found):
        return JSONResponse(openai.to_error("Аккаунт не найден", "not_found"), status_code=404)
    accounts_mod.save(kept)
    POOL.reload()
    return JSONResponse({"accounts": [_account_view(s) for s in POOL.slots()]})


def _iso(stamp: float) -> str | None:
    """A timestamp the admin panel can show (ISO, like everywhere else)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)) if stamp else None


def _account_view(slot) -> dict:
    """One account, in the shape the admin panel reads."""
    account = slot.account
    quota = slot.quota or {}
    return {
        "id": account_id_of(account),
        "slot": f"u/{account.authuser or '0'}",
        "label": account.label,
        "email": account.email,
        "masked": _mask(account.email),
        "tier": quota.get("tier"),
        "models": [getattr(m, "model_name", str(m)) for m in slot.models],
        "status": slot.status(),
        "cookiesAt": account.updated_at or None,
        "lastOk": _iso(slot.last_ok),
        "lastError": (
            {"at": _iso(slot.last_error_at), "message": slot.last_error} if slot.last_error else None
        ),
        "total": {"ok": slot.ok, "failed": slot.failed, "limited": slot.limited},
        "quota": quota or None,
    }


def account_id_of(account) -> str:
    """A stable id for the admin panel (16 hex chars, like the old token ids)."""
    import hashlib
    return hashlib.sha256(account.key.encode()).hexdigest()[:16]


def _mask(email: str) -> str:
    if not email or "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return f"{name[:2]}{'*' * max(1, len(name) - 2)}@{domain}"
