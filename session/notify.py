"""Send one message to the owner's Saved Messages from the bot's Telegram account.

Works on a snapshot of the bot's Telethon session, so the running bot is not stopped
and its session file is never written. Usage: python -I notify.py "text"
"""
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

from telethon import TelegramClient

SESSION = Path("/opt/hfnews/data/premium.session")
ENV = Path("/opt/hfnews/.env")


def env() -> dict[str, str]:
    out = {}
    for line in ENV.read_text().splitlines():
        key, sep, val = line.partition("=")
        if sep and not key.startswith("#"):
            out[key.strip()] = val.strip().strip('"')
    return out


async def send(text: str) -> None:
    cfg = env()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "notify.session"
        src, dst = sqlite3.connect(SESSION), sqlite3.connect(copy)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        client = TelegramClient(str(copy), int(cfg["TG_API_ID"]), cfg["TG_API_HASH"])
        await client.connect()
        try:
            await client.send_message("me", text)
        finally:
            await client.disconnect()


if __name__ == "__main__":
    asyncio.run(send(sys.argv[1]))
