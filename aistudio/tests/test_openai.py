import base64

from aistudio.openai import images_of, prompt_of, text_of


def test_the_text_of_a_message():
    assert text_of("привет") == "привет"
    assert text_of([{"type": "text", "text": "а"}, {"type": "text", "text": "б"}]) == "а\nб"
    assert text_of(None) == ""


def test_the_prompt_keeps_the_system_prompt_first():
    prompt = prompt_of([
        {"role": "system", "content": "правила"},
        {"role": "user", "content": "вопрос"},
        {"role": "assistant", "content": "прошлый ответ"},
    ])
    assert prompt.startswith("правила")
    assert "прошлый ответ" not in prompt


def test_a_picture_becomes_a_file():
    data = base64.b64encode(b"\x89PNG fake").decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "что это?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
    ]}]
    pictures = images_of(messages)
    assert len(pictures) == 1
    name, blob = pictures[0]
    assert name.endswith(".png")
    assert blob == b"\x89PNG fake"


def test_a_picture_without_data_is_skipped():
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]
    assert images_of(messages) == []


def test_a_message_without_pictures_gives_nothing():
    assert images_of([{"role": "user", "content": "текст"}]) == []
