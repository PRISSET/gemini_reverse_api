"""The models AI Studio offers the account.

The list comes from Google (the ListModels RPC) and changes with the account, so it is never
hardcoded here. The page takes the model in the URL: `?model=gemini-3-flash-preview`.
"""
from __future__ import annotations

from .rpc import list_models

# What the caller asks for when it does not name a model.
DEFAULT = "gemini-flash"

# The words that describe a model, in the order we prefer them.
KEYWORDS = ("flash-lite", "flash", "hydrogen", "argon", "carbon", "pro", "auto")


async def models(cookies: dict[str, str]) -> list[dict]:
    """Every model of the account."""
    return await list_models(cookies)


def resolve(available: list[dict], wanted: str | None) -> str | None:
    """The model id to put in the URL.

    An exact name wins; then a name that contains the wanted one; then the closest by
    keyword (flash before pro, so the cheap model answers by default).
    """
    if not available:
        return None
    ids = [m["id"] for m in available]
    if not wanted:
        wanted = DEFAULT
    wanted = wanted.strip()
    if wanted in ids:
        return wanted
    lowered = [i.lower() for i in ids]
    if wanted.lower() in lowered:
        return ids[lowered.index(wanted.lower())]
    for i, low in zip(ids, lowered):
        if wanted.lower() in low:
            return i
    for keyword in KEYWORDS:
        for i, low in zip(ids, lowered):
            if keyword in low:
                return i
    return ids[0]
