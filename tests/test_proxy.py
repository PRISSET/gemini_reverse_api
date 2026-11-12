import asyncio

import pytest

from gemini_proxy import accounts as accounts_mod
from gemini_proxy import openai
from gemini_proxy.pool import Pool, Slot


class FakeModel:
    def __init__(self, name):
        self.model_name = name
        self.display_name = name
        self.aliases = []
        self.advanced_only = False


class FakeClient:
    """A GeminiClient that answers from a script."""

    def __init__(self, psid=None, psidts=None, proxy=None, **kwargs):
        self.psid = psid
        self.psidts = psidts
        self.models = [FakeModel("gemini-flash"), FakeModel("gemini-pro")]
        self.calls = []

    async def init(self, **kwargs):
        return None

    def list_models(self):
        return self.models

    def resolve_model(self, name):
        for m in self.models:
            if m.model_name == name:
                return m
        raise ValueError(f"Unknown model name: {name}")

    async def generate_content(self, prompt, model=None, **kwargs):
        self.calls.append((prompt, model))
        return type("Out", (), {"text": "ответ", "text_delta": ""})()

    async def generate_content_stream(self, prompt, model=None, **kwargs):
        self.calls.append((prompt, model))
        for piece in ("часть ", "два"):
            yield type("Out", (), {"text": piece, "text_delta": piece})()

    async def close(self):
        return None

    @property
    def usage_info(self):
        return {"remaining": "7/10"}

    @property
    def quotas(self):
        return {"gemini-flash": {"remaining": 7}}


@pytest.fixture(autouse=True)
def _no_accounts_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    accounts_mod.CFG.__dict__  # the config is frozen; the path comes from the environment
    yield


def test_prompt_keeps_the_system_prompt_first():
    prompt = openai.to_prompt([
        {"role": "system", "content": "правила"},
        {"role": "user", "content": "вопрос"},
    ])
    assert prompt.startswith("правила")
    assert prompt.endswith("вопрос")


def test_prompt_marks_the_previous_answers():
    prompt = openai.to_prompt([
        {"role": "user", "content": "первый"},
        {"role": "assistant", "content": "ответ"},
        {"role": "user", "content": "второй"},
    ])
    assert "[Предыдущий ответ]" in prompt
    assert prompt.index("первый") < prompt.index("ответ") < prompt.index("второй")


def test_completion_is_openai_shaped():
    out = openai.to_completion("привет", "gemini-flash")
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"]["content"] == "привет"
    assert out["choices"][0]["message"]["role"] == "assistant"


def test_accounts_survive_a_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTS_FILE", str(tmp_path / "a.json"))
    accounts_mod.save([
        accounts_mod.Account(id="a1", authuser="0", email="you@gmail.com",
                             cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}),
    ])
    back = accounts_mod.load()
    assert len(back) == 1
    assert back[0].email == "you@gmail.com"
    assert back[0].usable


def test_the_pool_asks_the_accounts_in_turn(monkeypatch):
    import gemini_proxy.pool as pool_mod
    monkeypatch.setattr(pool_mod, "GeminiClient", FakeClient)

    pool = Pool()
    accounts = [
        accounts_mod.Account(id="a1", authuser="0", email="a@x",
                             cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}),
        accounts_mod.Account(id="a2", authuser="1", email="b@x",
                             cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}),
    ]
    pool.reload = lambda: pool._slots.__setitem__(slice(None), [Slot(account=a) for a in accounts])

    text, slot = asyncio.run(pool.generate("вопрос", "gemini-flash"))
    assert text == "ответ"
    assert slot.account.id == "a1"

    # the account that served last goes last: the next request goes to the other one
    text, slot = asyncio.run(pool.generate("вопрос", "gemini-flash"))
    assert slot.account.id == "a2"


def test_the_model_falls_back_to_what_the_account_has(monkeypatch):
    import gemini_proxy.pool as pool_mod
    monkeypatch.setattr(pool_mod, "GeminiClient", FakeClient)

    pool = Pool()
    account = accounts_mod.Account(id="a1", authuser="0", email="a@x",
                                   cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"})
    pool.reload = lambda: pool._slots.__setitem__(slice(None), [Slot(account=account)])
    # "gemini-3.8-flash" is not a name this account knows: it must not blow up
    text, slot = asyncio.run(pool.generate("вопрос", "gemini-3.8-flash"))
    assert text == "ответ"


def test_the_stream_gives_the_pieces(monkeypatch):
    import gemini_proxy.pool as pool_mod
    monkeypatch.setattr(pool_mod, "GeminiClient", FakeClient)

    pool = Pool()
    account = accounts_mod.Account(id="a1", authuser="0", email="a@x",
                                   cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"})
    pool.reload = lambda: pool._slots.__setitem__(slice(None), [Slot(account=account)])

    async def run():
        out = []
        async for delta, slot in pool.generate_stream("вопрос", "gemini-flash"):
            out.append(delta)
        return out

    assert asyncio.run(run()) == ["часть ", "два"]


def test_a_broken_account_does_not_stop_the_next_one(monkeypatch):
    """The first account fails, the second answers: the request must not be lost."""
    import gemini_proxy.pool as pool_mod

    class BrokenClient(FakeClient):
        async def generate_content(self, prompt, model=None, **kwargs):
            raise pool_mod.APIError("boom")

    clients = []

    def make(psid=None, psidts=None, proxy=None, **kwargs):
        c = BrokenClient(psid, psidts) if psid == "bad" else FakeClient(psid, psidts)
        clients.append(c)
        return c

    monkeypatch.setattr(pool_mod, "GeminiClient", make)

    pool = Pool()
    accounts = [
        accounts_mod.Account(id="a1", authuser="0", email="a@x",
                             cookies={"__Secure-1PSID": "bad", "__Secure-1PSIDTS": "y"}),
        accounts_mod.Account(id="a2", authuser="1", email="b@x",
                             cookies={"__Secure-1PSID": "good", "__Secure-1PSIDTS": "y"}),
    ]
    pool.reload = lambda: pool._slots.__setitem__(slice(None), [Slot(account=a) for a in accounts])

    text, slot = asyncio.run(pool.generate("вопрос", "gemini-flash"))
    assert text == "ответ"
    assert slot.account.id == "a2"


def test_a_label_names_a_group(monkeypatch):
    import gemini_proxy.pool as pool_mod
    monkeypatch.setattr(pool_mod, "GeminiClient", FakeClient)

    pool = Pool()
    accounts = [
        accounts_mod.Account(id="a1", authuser="0", email="pro@x", label="pro",
                             cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}),
        accounts_mod.Account(id="a2", authuser="1", email="free@x", label="chat",
                             cookies={"__Secure-1PSID": "x", "__Secure-1PSIDTS": "y"}),
    ]
    pool.reload = lambda: pool._slots.__setitem__(slice(None), [Slot(account=a) for a in accounts])

    assert [s.account.id for s in pool.pick("pool")] == ["a1", "a2"]
    assert [s.account.id for s in pool.pick("pro")] == ["a1"]
    assert [s.account.id for s in pool.pick("chat")] == ["a2"]
    assert pool.pick("nope") == []
