"""The account pool: which Google account serves the next request.

One account is one signed-in Google account in the browser. The browser owns the session and
keeps the cookies fresh; the proxy reads them, and re-reads them when Google says the
session is stale. A 429 (the account ran out of quota) moves on to the next account.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from gemini_webapi import GeminiClient
from gemini_webapi.exceptions import (
    APIError,
    AuthError,
    GeminiError,
    TemporarilyBlockedError,
    UsageLimitExceededError,
)

from . import accounts as accounts_mod
from . import authuser
from .config import CFG

log = logging.getLogger("pool")


def _window(data: dict | None) -> dict | None:
    """One quota window: how much is left, when it refills."""
    if not isinstance(data, dict):
        return None
    return {
        "window": data.get("window"),
        "remaining": data.get("remaining_credits"),
        "percent": data.get("usage_percentage"),
        "resetAt": data.get("reset_at"),
    }


@dataclass
class Slot:
    account: accounts_mod.Account
    client: GeminiClient | None = None
    models: list[Any] = field(default_factory=list)
    last_ok: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0
    cooldown_until: float = 0.0
    quota: dict = field(default_factory=dict)
    ok: int = 0
    failed: int = 0
    limited: int = 0
    # One request at a time per account: the web session keeps a conversation state, and
    # two parallel calls on one account mix them up.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> str:
        return self.account.key

    def view(self) -> dict:
        return {
            **self.account.view(),
            "models": [getattr(m, "model_name", str(m)) for m in self.models],
            "status": self.status(),
            "cooldownUntil": self.cooldown_until or None,
            "lastOk": self.last_ok or None,
            "lastError": (
                {"at": self.last_error_at, "message": self.last_error} if self.last_error else None
            ),
            "total": {"ok": self.ok, "failed": self.failed, "limited": self.limited},
            "quota": self.quota or None,
        }

    def fail(self, message: str) -> None:
        self.last_error = message
        self.last_error_at = time.time()

    def status(self) -> str:
        if self.cooldown_until > time.time():
            return "cooldown"
        if self.last_error and not self.last_ok:
            return "dead"
        if self.last_ok:
            return "alive"
        return "unknown"


class Pool:
    """The accounts, in file order. The one that answered last is asked first."""

    def __init__(self) -> None:
        self._slots: list[Slot] = []
        self._lock = asyncio.Lock()
        self._last: str | None = None
        self._loaded_at = 0.0

    # ---------------------------------------------------------------- accounts
    def reload(self) -> None:
        """Re-read the accounts file: the browser may have rotated the cookies."""
        found = accounts_mod.load()
        by_key = {s.key: s for s in self._slots}
        slots: list[Slot] = []
        for account in found:
            if not account.usable:
                continue
            slot = by_key.get(account.key)
            if slot is None:
                slot = Slot(account=account)
                slots.append(slot)
                continue
            # A new cookie jar means the session was refreshed: the client must be rebuilt.
            if slot.account.cookies != account.cookies:
                slot.account = account
                slot.client = None
            else:
                slot.account = account
            slots.append(slot)
        self._slots = slots
        self._loaded_at = time.time()

    def slots(self) -> list[Slot]:
        if not self._slots or time.time() - self._loaded_at > CFG.accounts_reload_s:
            self.reload()
        return self._slots

    # ---------------------------------------------------------------- clients
    async def _client_for(self, slot: Slot) -> GeminiClient:
        if slot.client is not None:
            return slot.client
        account = slot.account
        authuser.apply_authuser(account.authuser)
        authuser.install_full_jar(lambda: slot.account.cookies)
        client = GeminiClient(account.psid, account.psidts, proxy=CFG.proxy or None)
        # The browser owns the session: rotating the cookies here would log it out.
        await client.init(timeout=CFG.init_timeout, auto_refresh=False)
        slot.client = client
        try:
            slot.models = client.list_models() or []
        except Exception as e:  # noqa: BLE001
            log.warning("model list for %s failed: %s", slot.key, e)
            slot.models = []
        log.info("account %s ready, models: %s", slot.key,
                 ", ".join(getattr(m, "model_name", "?") for m in slot.models))
        return client

    async def _drop_client(self, slot: Slot) -> None:
        client, slot.client = slot.client, None
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- models
    def _resolve(self, slot: Slot, name: str | None) -> Any:
        """The model of this account that matches the requested name.

        The model list comes from Google per account, so a name that works on one account
        can be missing on another. A name nobody has falls back to the closest one by
        keyword (flash / pro / auto), and to the account's own default when even that fails.
        """
        client = slot.client
        if client is None:
            return None
        if name:
            try:
                return client.resolve_model(name)
            except Exception:  # noqa: BLE001
                pass
            wanted = name.lower()
            for model in slot.models:
                haystack = " ".join([
                    getattr(model, "model_name", ""),
                    getattr(model, "display_name", ""),
                    " ".join(getattr(model, "aliases", []) or []),
                ]).lower()
                if wanted in haystack:
                    return model
            for keyword in ("flash-lite", "flash", "auto", "pro"):
                if keyword in wanted:
                    for model in slot.models:
                        haystack = " ".join([
                            getattr(model, "model_name", ""),
                            getattr(model, "display_name", ""),
                            " ".join(getattr(model, "aliases", []) or []),
                        ]).lower()
                        if keyword in haystack:
                            return model
        return None

    def _picked(self, slot: Slot, name: str | None) -> Any:
        """The model for this request, logged: the requested name can resolve to another one."""
        model = self._resolve(slot, name)
        picked = getattr(model, "model_name", None) or "account default"
        log.info("%s: model %s → %s", slot.key, name or "-", picked)
        return model

    # ---------------------------------------------------------------- requests
    def _ordered(self, bearer: str = "pool") -> list[Slot]:
        """The slots to try: the ones with quota first, the one that served last last."""
        now = time.time()
        allowed = self.pick(bearer)
        if not allowed:
            return []
        alive = [s for s in allowed if s.cooldown_until <= now]
        if not alive:
            alive = list(allowed)
        if not self._last:
            return alive
        ids = [s.key for s in alive]
        if self._last not in ids:
            return alive
        i = ids.index(self._last)
        return alive[i + 1:] + alive[:i + 1]

    def pick(self, bearer: str) -> list[Slot]:
        """
        The slots a request may go to.

        `pool` (or anything unknown) means all of them, round-robin. A slot name, an e-mail or
        a label pins the request to that account: the label may name a group, so the bot can
        write posts on the paid account while the site chat answers on the free one.
        """
        slots = self.slots()
        if not slots:
            return []
        wanted = bearer.strip().lower()
        if not wanted or wanted in ("pool", "all"):
            return slots
        exact = [s for s in slots if s.account.id.lower() == wanted
                 or s.account.email.lower() == wanted]
        if exact:
            return exact
        by_label = [s for s in slots if (s.account.label or "").lower() == wanted]
        if by_label:
            return by_label
        return []

    async def generate(self, prompt: str, model: str | None = None,
                        bearer: str = "pool") -> tuple[str, Slot]:
        """One answer. Tries the accounts in turn; raises the last error if none answered."""
        last: Exception | None = None
        for slot in self._ordered(bearer)[: max(1, CFG.max_retries)]:
            async with slot.lock:
                try:
                    client = await self._client_for(slot)
                    out = await client.generate_content(prompt, model=self._picked(slot, model))
                    text = getattr(out, "text", "") or ""
                    if not text.strip():
                        raise APIError("empty answer")
                    slot.last_ok = time.time()
                    slot.last_error = ""
                    slot.last_error_at = 0.0
                    slot.cooldown_until = 0
                    slot.ok += 1
                    self._last = slot.key
                    self._read_quota(slot, client)
                    return text, slot
                except AuthError as e:
                    # The session went stale: the browser has new cookies, read them and retry.
                    slot.fail(str(e))
                    slot.failed += 1
                    log.warning("account %s: auth failed (%s), reloading the cookies", slot.key, e)
                    await self._drop_client(slot)
                    self.reload()
                    last = e
                except (UsageLimitExceededError, TemporarilyBlockedError) as e:
                    slot.fail(str(e))
                    slot.limited += 1
                    slot.cooldown_until = time.time() + 900
                    log.warning("account %s: %s", slot.key, e)
                    last = e
                except (GeminiError, APIError, OSError) as e:
                    slot.fail(str(e))
                    slot.failed += 1
                    log.warning("account %s failed: %s", slot.key, e)
                    await self._drop_client(slot)
                    last = e
        raise last if last else APIError("no Google account answered")

    async def generate_stream(self, prompt: str, model: str | None = None,
                               bearer: str = "pool"):
        """The answer as it comes. Yields (text_delta, slot)."""
        last: Exception | None = None
        for slot in self._ordered(bearer)[: max(1, CFG.max_retries)]:
            async with slot.lock:
                started = False
                try:
                    client = await self._client_for(slot)
                    async for out in client.generate_content_stream(
                        prompt, model=self._picked(slot, model)
                    ):
                        delta = getattr(out, "text_delta", "") or ""
                        if delta:
                            started = True
                            yield delta, slot
                    slot.last_ok = time.time()
                    slot.last_error = ""
                    slot.last_error_at = 0.0
                    slot.cooldown_until = 0
                    slot.ok += 1
                    self._last = slot.key
                    self._read_quota(slot, client)
                    return
                except AuthError as e:
                    slot.fail(str(e))
                    slot.failed += 1
                    await self._drop_client(slot)
                    self.reload()
                    last = e
                    if started:
                        raise
                except (UsageLimitExceededError, TemporarilyBlockedError) as e:
                    slot.fail(str(e))
                    slot.limited += 1
                    slot.cooldown_until = time.time() + 900
                    last = e
                    if started:
                        raise
                except (GeminiError, APIError, OSError) as e:
                    slot.fail(str(e))
                    slot.failed += 1
                    await self._drop_client(slot)
                    last = e
                    if started:
                        raise
        raise last if last else APIError("no Google account answered")

    def _read_quota(self, slot: Slot, client: GeminiClient) -> None:
        """What is left of the account's quota, as Google reports it."""
        try:
            usage = client.usage_info or {}
            quotas = client.quotas or {}
            if not usage:
                # The tier lives in the quotas block on some accounts.
                usage = quotas.get("usage_info") or {}
            buckets = []
            for key, bucket in quotas.items():
                if not isinstance(bucket, dict) or "remaining" not in bucket:
                    continue
                buckets.append({
                    "label": bucket.get("label") or key,
                    "remaining": bucket.get("remaining"),
                    "total": bucket.get("total"),
                    "percent": bucket.get("usage_percentage"),
                    "resetAt": bucket.get("reset_time"),
                })
            slot.quota = {
                "tier": (usage.get("tier") or {}).get("label"),
                "window5h": _window(usage.get("current_5h")),
                "weekly": _window(usage.get("weekly")),
                "models": buckets,
            }
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- views
    def view(self) -> list[dict]:
        return [s.view() for s in self.slots()]


POOL = Pool()
