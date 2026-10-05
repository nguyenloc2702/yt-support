"""Tests for pipeline.llm_client with the OpenAI SDK mocked out."""

import pytest

from pipeline import llm_client
from pipeline.llm_client import (
    LLMNotConfiguredError,
    LLMResponseError,
    chat_json,
)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.chat = type("C", (), {})()
        self.chat.completions = _FakeCompletions(response)


@pytest.fixture
def llm_env(monkeypatch, tmp_path, isolated_db):
    monkeypatch.setattr(
        "config.settings.settings.data_dir", tmp_path / "data", raising=False
    )
    from services import settings_service as ss

    ss._SECRET_KEY_PATH = tmp_path / "data" / ".secret_key"
    ss.set_setting("llm_api_key", "test-key")
    return ss


def test_chat_json_parses_valid_response(llm_env, monkeypatch):
    fake = _FakeClient(_FakeResponse('{"title": "Hi", "beats": []}'))
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)
    data = chat_json("sys", "user")
    assert data == {"title": "Hi", "beats": []}
    # Request must include model + messages
    assert fake.chat.completions.last_kwargs["model"]
    assert fake.chat.completions.last_kwargs["messages"][0]["role"] == "system"


def test_chat_json_invalid_json_raises(llm_env, monkeypatch):
    monkeypatch.setattr(
        llm_client, "get_llm_client", lambda: _FakeClient(_FakeResponse("not json{"))
    )
    with pytest.raises(LLMResponseError):
        chat_json("sys", "user")


def test_chat_json_empty_content_raises(llm_env, monkeypatch):
    monkeypatch.setattr(
        llm_client, "get_llm_client", lambda: _FakeClient(_FakeResponse(""))
    )
    with pytest.raises(LLMResponseError):
        chat_json("sys", "user")


def test_no_api_key_raises(monkeypatch, tmp_path, isolated_db):
    monkeypatch.delenv("XKIRO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "config.settings.settings.data_dir", tmp_path / "data", raising=False
    )
    from services import settings_service as ss

    ss._SECRET_KEY_PATH = tmp_path / "data" / ".secret_key"
    with pytest.raises(LLMNotConfiguredError):
        chat_json("sys", "user")
