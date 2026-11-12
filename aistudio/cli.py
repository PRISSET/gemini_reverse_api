"""python -m aistudio check     the models and the tier of the account
   python -m aistudio ask "..."  one answer
   python -m aistudio serve     an OpenAI-compatible API on :3087
"""
from __future__ import annotations

import asyncio
import json
import sys

from . import accounts as accounts_mod
from . import cookies as cookies_mod
from . import models as models_mod
from . import rpc
from .cdp import Cdp
from .config import CFG
from .session import StudioPool


async def check() -> int:
    account = accounts_mod.pick()
    if account is None:
        print(f"нет аккаунтов в {CFG.accounts_file}")
        return 1
    print(f"аккаунт: {account.email or account.id} ({account.id}, u/{account.authuser or '0'})")
    async with Cdp(CFG.cdp_host, CFG.cdp_port) as cdp:
        jar = await cookies_mod.jar(cdp)
    try:
        tier = await rpc.benefit_tier(jar)
        print(f"тариф: {tier}")
    except rpc.RpcError as e:
        print(f"тариф: не прочитать ({e})")
    try:
        listed = await models_mod.models(jar)
    except rpc.RpcError as e:
        print(f"модели: не прочитать ({e})")
        return 1
    for m in listed:
        print(f"  {m['id']:34} {m['name']:28} контекст {m['context']}")
    return 0


async def ask(prompt: str, model: str = "") -> int:
    account = accounts_mod.pick()
    if account is None:
        print(f"нет аккаунтов в {CFG.accounts_file}")
        return 1
    async with Cdp(CFG.cdp_host, CFG.cdp_port) as cdp:
        jar = await cookies_mod.jar(cdp)
        listed = await models_mod.models(jar)
        chosen = models_mod.resolve(listed, model or CFG.default_model)
        print(f"модель: {chosen}")
        pool = StudioPool(cdp)
        try:
            answer = await pool.tab(chosen).ask(prompt, timeout=CFG.timeout)
        finally:
            await pool.close()
    print(answer)
    return 0


def serve() -> int:
    import uvicorn

    from .server import app

    uvicorn.run(app, host="127.0.0.1", port=int(__import__("os").environ.get("AISTUDIO_PORT", "3087")))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] == "check":
        return asyncio.run(check())
    if argv[0] == "ask":
        prompt = argv[1] if len(argv) > 1 else "Привет"
        model = argv[2] if len(argv) > 2 else ""
        return asyncio.run(ask(prompt, model))
    if argv[0] == "serve":
        return serve()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
