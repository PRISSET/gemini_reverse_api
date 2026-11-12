"""The generation, through the browser.

AI Studio signs its GenerateContent request with a blob that only the page's own JavaScript
can produce, so the request has to be made by the page. The tab does the work: we put the
prompt in, press Run and read the answer out of the chat.

One tab per model: the model is part of the page URL, and a tab that is already on the right
model does not have to be loaded again.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from .cdp import Cdp

log = logging.getLogger("aistudio.session")

BASE = "https://aistudio.google.com/prompts/new_chat"
U_PATH = "https://aistudio.google.com/u/{n}/prompts/new_chat"

# The prompt field, the Run button and the answer, all as JavaScript.
FILL = """(function(){
  var ta = document.querySelector('textarea');
  if (!ta) return 'нет поля ввода';
  var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, %s);
  ta.dispatchEvent(new Event('input', {bubbles: true}));
  return 'ок';
})()"""

RUN = """(function(){
  var all = document.querySelectorAll('button,[role="button"]');
  for (var i = 0; i < all.length; i++) {
    var label = ((all[i].innerText || '') + ' ' + (all[i].getAttribute('aria-label') || '')).trim();
    if (/^Run(\\b|$)/i.test(label)) { all[i].click(); return 'нажал'; }
  }
  return 'нет кнопки Run';
})()"""

# The turns of the chat: the question, the thoughts, the answer.
TURNS = """(function(){
  var out = [];
  var turns = document.querySelectorAll('ms-chat-turn');
  for (var i = 0; i < turns.length; i++) {
    var clone = turns[i].cloneNode(true);
    var junk = clone.querySelectorAll('.turn-footer, .response-feedback-button, button, mat-icon');
    for (var j = 0; j < junk.length; j++) junk[j].remove();
    var text = (clone.innerText || '').trim();
    if (text) out.push(text);
  }
  return out;
})()"""

# The header of a turn ("Model 11:30 PM", "User 11:27 PM") and the thoughts block.
HEADER = re.compile(r"^(User|Model)\s+\d{1,2}:\d{2}\s*(AM|PM)?\s*", re.IGNORECASE)
THOUGHTS = re.compile(r"^(Thoughts|Expand to view model thoughts)\s*$", re.IGNORECASE)


def clean_turn(text: str) -> str:
    """The answer without the header of the turn."""
    return HEADER.sub("", (text or "").strip()).strip()


def pick_answer(turns: list[str]) -> str:
    """The last turn with a real answer: the thoughts and the empty turns are skipped."""
    for text in reversed(turns or []):
        cleaned = clean_turn(text)
        if not cleaned or THOUGHTS.match(cleaned):
            continue
        if cleaned.lower() in ("thoughts", "expand to view model thoughts"):
            continue
        return cleaned
    return ""


# How many files are attached to the prompt right now.
ATTACHED = """(function(){
  var n = 0;
  var all = document.querySelectorAll('img,[class*="chip"],[class*="attachment"],[class*="preview"]');
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if ((el.getAttribute('src') || '').indexOf('blob:') === 0) n++;
    else if (/attachment|chip|preview/i.test(String(el.className || ''))) n++;
  }
  return n;
})()"""


class Studio:
    """One AI Studio tab: one account, one model."""

    def __init__(self, cdp: Cdp, model: str, authuser: str = ""):
        self.cdp = cdp
        self.model = model
        self.authuser = str(authuser or "")
        self.session: str | None = None
        self.url = self._url()

    def _url(self) -> str:
        # The index of the Google account in the browser (multi-login).
        base = U_PATH.format(n=self.authuser) if self.authuser else BASE
        return f"{base}?model={self.model}"

    async def open(self, timeout: float = 60) -> None:
        """The tab, loaded and ready."""
        if self.session:
            return
        self.session = await self.cdp.new_tab("about:blank")
        await self.cdp.navigate(self.session, self.url)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            ready = await self.cdp.evaluate(self.session, """(function(){
              return !!document.querySelector('textarea') &&
                !/loading|Загрузка/i.test(document.body.innerText.slice(0, 200));
            })()""")
            if ready:
                return
        raise TimeoutError("AI Studio не открылся")

    async def close(self) -> None:
        if self.session:
            await self.cdp.close_tab(self.session)
            self.session = None

    async def attach(self, files: list[str]) -> None:
        """Put the files into the prompt: the page uploads them itself."""
        if not files:
            return
        assert self.session is not None
        before = await self.cdp.evaluate(self.session, ATTACHED) or 0
        doc = await self.cdp.send("DOM.getDocument", {"depth": -1}, self.session)
        node = await self.cdp.send(
            "DOM.querySelector",
            {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"},
            self.session,
        )
        node_id = node.get("nodeId", 0)
        if not node_id:
            raise RuntimeError("AI Studio: в странице нет поля для файла")
        await self.cdp.send("DOM.setFileInputFiles", {"files": files, "nodeId": node_id}, self.session)
        # The upload takes a moment: wait until the file shows up in the prompt.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            now = await self.cdp.evaluate(self.session, ATTACHED) or 0
            if now > before:
                return
        raise TimeoutError("AI Studio: файл не загрузился")

    async def ask(self, prompt: str, timeout: float = 180, files: list[str] | None = None) -> str:
        """One answer, with the files attached."""
        await self.open()
        assert self.session is not None
        before = len(await self._turns())

        if files:
            await self.attach(files)

        filled = await self.cdp.evaluate(self.session, FILL % _js_string(prompt))
        if filled != "ок":
            raise RuntimeError(f"AI Studio: {filled}")
        await asyncio.sleep(0.8)
        clicked = await self.cdp.evaluate(self.session, RUN)
        if not str(clicked).startswith("нажал"):
            raise RuntimeError(f"AI Studio: {clicked}")

        deadline = time.monotonic() + timeout
        last, stable = "", 0
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            turns = await self._turns()
            if len(turns) <= before:
                continue
            text = pick_answer(turns)
            if text and text == last:
                stable += 1
            else:
                stable = 0
            last = text
            if stable >= 2 and last:
                return last
        raise TimeoutError("AI Studio не ответил вовремя")

    async def _turns(self) -> list[str]:
        assert self.session is not None
        turns = await self.cdp.evaluate(self.session, TURNS)
        return list(turns or [])


def _js_string(value: str) -> str:
    """A JavaScript string literal."""
    import json

    return json.dumps(value, ensure_ascii=False)


class StudioPool:
    """One tab per account and model, reused between the requests."""

    def __init__(self, cdp: Cdp):
        self.cdp = cdp
        self._tabs: dict[tuple[str, str], Studio] = {}

    def tab(self, model: str, authuser: str = "") -> Studio:
        """The tab of this account and model, created on demand."""
        key = (str(authuser or ""), model)
        tab = self._tabs.get(key)
        if tab is None:
            tab = Studio(self.cdp, model, authuser)
            self._tabs[key] = tab
        return tab

    async def close(self) -> None:
        for tab in self._tabs.values():
            await tab.close()
        self._tabs.clear()
