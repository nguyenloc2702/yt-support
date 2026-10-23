"""Tests for pipeline.llm_client with the OpenAI SDK mocked out."""

import pytest

from pipeline import llm_client
from pipeline.llm_client import (
    LLMNotConfiguredError,
    LLMResponseError,
    chat_json,
)
from services import settings_service as _ss

# Chain tests describe structure, not product names: they must keep passing when
# the measured-best default model changes.
PRIMARY = _ss.DEFAULT_XKIRO_GENERATION_MODEL
BACKUP_1 = _ss.DEFAULT_XKIRO_FALLBACK_MODELS[0]
BACKUP_2 = _ss.DEFAULT_XKIRO_FALLBACK_MODELS[1]


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


def test_chat_json_defaults_to_xkiro_generation_model(llm_env, monkeypatch):
    fake = _FakeClient(_FakeResponse('{"ok": true}'))
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake)
    monkeypatch.setattr("services.settings_service.get_setting", lambda key: None)
    chat_json("sys", "user")
    model = fake.chat.completions.last_kwargs["model"]
    assert model == PRIMARY
    assert model != "gemini-2.0-flash"


class _ScriptedCompletions:
    """Per-model scripted behaviour: exception instance or response content."""

    def __init__(self, behaviors, default='{"ok": true}'):
        self.behaviors = behaviors
        self.default = default
        self.attempts = []

    def create(self, **kwargs):
        model = kwargs["model"]
        self.attempts.append(model)
        behavior = self.behaviors.get(model, self.default)
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeResponse(behavior)


class _ScriptedClient:
    def __init__(self, completions):
        self.chat = type("C", (), {})()
        self.chat.completions = completions


CAPACITY_ERR = RuntimeError("Error code: 409 - Conflict: model is overloaded")
ROUTING_ERR = RuntimeError(
    'Error code: 404 - Model "some/dead-model" does not exist or is not routable'
)


def test_model_chain_puts_primary_first_then_backups(llm_env):
    chain = llm_client.model_chain()
    assert chain[0] == PRIMARY
    assert chain[1:] == _ss.DEFAULT_XKIRO_FALLBACK_MODELS


def test_model_chain_honours_explicit_model_and_dedupes(llm_env):
    chain = llm_client.model_chain(BACKUP_2)
    assert chain[0] == BACKUP_2
    assert chain.count(BACKUP_2) == 1


def test_chat_json_fails_over_on_gateway_capacity_error(llm_env, monkeypatch):
    comps = _ScriptedCompletions(
        {PRIMARY: CAPACITY_ERR},
        default='{"from": "backup"}',
    )
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    data = chat_json("sys", "user")
    assert data == {"from": "backup"}
    assert comps.attempts == [PRIMARY, BACKUP_1]


def test_chat_json_fails_over_on_unroutable_model(llm_env, monkeypatch):
    comps = _ScriptedCompletions({PRIMARY: ROUTING_ERR})
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    assert chat_json("sys", "user") == {"ok": True}
    assert len(comps.attempts) == 2


def test_chat_json_fails_over_on_truncated_output(llm_env, monkeypatch):
    comps = _ScriptedCompletions(
        {PRIMARY: '{"scenes": [{"vi": "cụt'},
    )
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    assert chat_json("sys", "user") == {"ok": True}
    assert comps.attempts[1] == BACKUP_1


def test_chat_json_raises_only_after_whole_chain(llm_env, monkeypatch):
    comps = _ScriptedCompletions({}, default=None)
    comps.behaviors = {m: CAPACITY_ERR for m in llm_client.model_chain()}
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    with pytest.raises(LLMResponseError):
        chat_json("sys", "user")
    assert comps.attempts == llm_client.model_chain()


def test_chat_text_fails_over_to_backup_model(llm_env, monkeypatch):
    comps = _ScriptedCompletions(
        {PRIMARY: CAPACITY_ERR}, default="text from backup"
    )
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    assert llm_client.chat_text("sys", "user") == "text from backup"
    assert len(comps.attempts) == 2


def test_backup_models_are_configurable(llm_env):
    from services.settings_service import (
        get_xkiro_fallback_models,
        set_xkiro_fallback_models,
    )

    set_xkiro_fallback_models([BACKUP_2, BACKUP_2])
    assert get_xkiro_fallback_models() == [BACKUP_2]
    assert llm_client.model_chain()[1:] == [BACKUP_2]

    set_xkiro_fallback_models([])
    assert get_xkiro_fallback_models() == []
    assert llm_client.model_chain() == [PRIMARY]


def test_probe_model_chain_reports_every_model(llm_env, monkeypatch):
    comps = _ScriptedCompletions({BACKUP_1: CAPACITY_ERR})
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: _ScriptedClient(comps))
    rows = llm_client.probe_model_chain()

    assert [r["model"] for r in rows] == llm_client.model_chain()
    assert rows[0]["ok"] is True and rows[0]["latency_ms"] >= 0
    dead = next(r for r in rows if r["model"] == BACKUP_1)
    assert dead["ok"] is False and "409" in dead["error"]
    assert all(r["ok"] for r in rows if r["model"] != BACKUP_1)
