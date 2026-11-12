"""The direct RPC of AI Studio.

AI Studio is not the Bard web app: it talks to its own service over Google's gapi protocol.

    POST https://alkalimakersuite-pa.clients6.google.com/$rpc/
         google.internal.alkali.applications.makersuite.v1.MakerSuiteService/<Method>

The body is protobuf-JSON, the session is authenticated by the cookies plus a SAPISIDHASH
signature, and the public web key of the page goes in `X-Goog-Api-Key`.

What works from a script: the model list, the tier, the access token, the token count.
What does not: GenerateContent — the page signs that request with a blob only its own
JavaScript can produce, so the generation goes through the browser (see session.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx
from curl_cffi import requests

log = logging.getLogger("aistudio.rpc")

ORIGIN = "https://aistudio.google.com"
# The key AI Studio's own page sends. It is public: every visitor of the page sends it.
WEB_API_KEY = "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs"
RPC = ("https://alkalimakersuite-pa.clients6.google.com/$rpc/"
       "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/")
IMPERSONATE = "chrome145"


class RpcError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def sapisidhash(cookies: dict[str, str], origin: str = ORIGIN) -> str:
    """The signature Google asks for: SAPISIDHASH <ts>_<sha1(ts sapisid origin)>."""
    ts = str(int(time.time()))
    sapisid = cookies.get("SAPISID", "")
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest} SAPISID1PHASH {ts}_{digest} SAPISID3PHASH {ts}_{digest}"


def _headers(cookies: dict[str, str]) -> dict[str, str]:
    return {
        "Content-Type": "application/json+protobuf",
        "X-Goog-Api-Key": WEB_API_KEY,
        "X-Goog-AuthUser": "0",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "Referer": ORIGIN + "/",
        "Origin": ORIGIN,
        "Authorization": sapisidhash(cookies),
    }


def _session(cookies: dict[str, str]) -> requests.AsyncSession:
    session = requests.AsyncSession(impersonate=IMPERSONATE)
    for name, value in cookies.items():
        # Only .google.com: the RPC host is a subdomain of it, and a second copy of the
        # same cookie makes Google reject the session.
        session.cookies.set(name, value, domain=".google.com")
    return session


async def call(cookies: dict[str, str], method: str, body: Any = None) -> Any:
    """One RPC. Returns the parsed answer."""
    session = _session(cookies)
    try:
        r = await session.post(RPC + method, json=body if body is not None else [],
                                headers=_headers(cookies), timeout=40)
    except Exception as e:  # noqa: BLE001
        raise RpcError(f"{method}: {type(e).__name__}: {e}") from e
    finally:
        await session.close()
    if r.status_code != 200:
        raise RpcError(f"{method}: HTTP {r.status_code}: {r.text[:200]}", r.status_code)
    try:
        return json.loads(r.text)
    except ValueError as e:
        raise RpcError(f"{method}: bad answer: {r.text[:200]}") from e


async def list_models(cookies: dict[str, str]) -> list[dict]:
    """The models the account may use, as Google publishes them."""
    raw = await call(cookies, "ListModels")
    # The answer is a list with one element: the list of the models.
    items: list = []
    if raw and isinstance(raw[0], list):
        items = raw[0] if (raw[0] and isinstance(raw[0][0], list)) else raw
    out = []
    for item in items:
        if not isinstance(item, list) or not item:
            continue
        model_id = item[0]
        if not isinstance(model_id, str) or not model_id.startswith("models/"):
            continue
        out.append({
            "id": model_id.split("/", 1)[1],
            "name": item[3] if len(item) > 3 else model_id,
            "description": item[4] if len(item) > 4 else "",
            "context": item[5] if len(item) > 5 else None,
            "max_output": item[6] if len(item) > 6 else None,
            "methods": item[7] if len(item) > 7 else [],
        })
    return out


async def benefit_tier(cookies: dict[str, str]) -> int:
    """The tier of the account (1 = free, 2 = paid, ...)."""
    raw = await call(cookies, "GetAiStudioBenefitTier")
    return raw[0] if isinstance(raw, list) and raw else 0


async def access_token(cookies: dict[str, str]) -> str:
    """The OAuth token of the account. Its scope covers AI Studio only, not the public API."""
    raw = await call(cookies, "GenerateAccessToken", ["users/me"])
    return raw[0] if isinstance(raw, list) and raw else ""


async def count_tokens(cookies: dict[str, str], model: str, prompt: str) -> int:
    body = [f"models/{model}", [[[[None, prompt]]], "user"]]
    raw = await call(cookies, "CountTokens", body)
    try:
        return int(raw[0])
    except (TypeError, ValueError, IndexError):
        return 0
