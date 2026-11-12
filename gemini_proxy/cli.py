"""The state of every account: which one is signed in, what models it offers, how much
quota is left.

    python -m gemini_proxy check
    python -m gemini_proxy label a2 chat    # name an account (the group the chat uses)
"""
from __future__ import annotations

import asyncio
import json
import sys

from . import accounts as accounts_mod
from .pool import POOL


def set_label(account_id: str, name: str) -> int:
    """Name an account: the name is the group the chat or the bot can be pointed at."""
    found = accounts_mod.load()
    for account in found:
        if account.id != account_id and account.authuser != account_id:
            continue
        account.label = name[:60]
        accounts_mod.save(found)
        print(f"{account.id} (u/{account.authuser or '0'}) → «{account.label}»")
        return 0
    print("аккаунт не найден")
    return 1


async def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "label":
        return set_label(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    found = accounts_mod.load()
    if not found:
        print(f"[check] ни одного аккаунта в {accounts_mod.CFG.accounts_file}")
        print("[check] добавь: node deploy/gemini/gemacc.mjs login")
        return 1
    for account in found:
        if not account.usable:
            print(f"[check] {account.id} (u/{account.authuser or '0'}) — нет куков, аккаунт пропущен")
            continue
        print(f"[check] {account.id} (u/{account.authuser or '0'}) {account.email or account.label}")
        try:
            slot = next((s for s in POOL.slots() if s.key == account.key), None)
            if slot is None:
                from .pool import Slot
                slot = Slot(account=account)
                POOL._slots.append(slot)
            client = await POOL._client_for(slot)
            models = ", ".join(
                f"{getattr(m, 'model_name', '?')}"
                f"{' (advanced)' if getattr(m, 'advanced_only', False) else ''}"
                for m in slot.models
            )
            print(f"         модели: {models or 'неизвестны'}")
            usage = client.usage_info or {}
            if usage:
                print(f"         usage: {json.dumps(usage, ensure_ascii=False)[:400]}")
            quota = client.quotas or {}
            if quota:
                print(f"         квоты: {json.dumps(quota, ensure_ascii=False)[:400]}")
        except Exception as e:  # noqa: BLE001
            print(f"         ОШИБКА: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
