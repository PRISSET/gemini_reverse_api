import pytest

from aistudio import models as models_mod
from aistudio.rpc import sapisidhash
from aistudio.session import clean_turn, pick_answer


MODELS = [
    {"id": "gemini-4-hydrogen", "name": "Gemini 4 Hydrogen"},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"},
    {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash Lite"},
    {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview"},
    {"id": "antigravity-preview-09-2026", "name": "Antigravity Agent Preview"},
]


def test_an_exact_name_wins():
    assert models_mod.resolve(MODELS, "gemini-3.8-flash") == "gemini-3.8-flash"


def test_the_case_does_not_matter():
    assert models_mod.resolve(MODELS, "GEMINI-3.8-FLASH") == "gemini-3.8-flash"


def test_a_partial_name_finds_the_model():
    assert models_mod.resolve(MODELS, "hydrogen") == "gemini-4-hydrogen"


def test_a_flash_lite_ask_does_not_land_on_a_flash():
    assert models_mod.resolve(MODELS, "flash-lite") == "gemini-3.5-flash-lite"


def test_without_a_name_the_cheap_model_answers():
    assert models_mod.resolve(MODELS, None) == "gemini-3.5-flash-lite"


def test_an_unknown_name_still_gives_a_model():
    assert models_mod.resolve(MODELS, "нетакой") in [m["id"] for m in MODELS]


def test_the_signature_is_built_from_the_sapisid():
    value = sapisidhash({"SAPISID": "abc"})
    assert value.startswith("SAPISIDHASH ")
    assert "SAPISID1PHASH" in value and "SAPISID3PHASH" in value
    # one timestamp and one digest for all three
    stamps = {part.split("_", 1)[1] for part in value.split() if "_" in part}
    assert len(stamps) == 1


def test_the_header_of_a_turn_is_removed():
    assert clean_turn("Model 11:30 PMПривет") == "Привет"
    assert clean_turn("User 11:27 PMВопрос") == "Вопрос"


def test_the_answer_is_the_last_turn_with_text():
    turns = ["edit more_vert", "Model 11:27 PMThoughts", "Model 11:27 PMОтвет"]
    assert pick_answer(turns) == "Ответ"


def test_the_thoughts_are_not_an_answer():
    assert pick_answer(["Model 11:27 PMThoughts", "Model 11:27 PMExpand to view model thoughts"]) == ""


def test_an_empty_chat_has_no_answer():
    assert pick_answer([]) == ""
