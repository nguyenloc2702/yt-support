"""Tests for LLM credential self-healing (pipeline.llm_client).

The settings DB lives on Google Drive and a stale cloud sync can revert the
llm_api_key / llm_model rows to dead values. On a provider auth/routing
rejection the client must re-pair the row from the environment (or the known
good gateway model) and retry once — never silently succeed with the dead row.
"""

from types import SimpleNamespace

import pytest

import pipeline.llm_client as lc
from services.settings_service import LLM_KEY, LLM_MODEL_KEY


class PassthroughBreaker:
    def call(self, fn, label=""):
        return fn()


class FakeCompletions:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        err = self.errors.pop(0) if self.errors else None
        if err is not None:
            raise err
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        )


class FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


AUTH_ERR = RuntimeError(
    "Error code: 401 - {'error': {'message': 'Invalid or disabled ClientApiKey.', "
    "'type': 'authentication_error'}}"
)
ROUTING_ERR = RuntimeError(
    "Error code: 503 - model google/gemini-2.5-flash is not routable on this gateway"
)


@pytest.fixture
def heal_env(monkeypatch):
    """Fake settings store + client sequence + passthrough breaker."""
    store = {LLM_KEY: "sk-dead-key-from-reverted-db", LLM_MODEL_KEY: "google/gemini-2.5-flash"}
    set_calls = []
    monkeypatch.setattr(
        "services.settings_service.get_setting", lambda key: store.get(key)
    )
    monkeypatch.setattr(
        "services.settings_service.set_setting",
        lambda key, value: (store.__setitem__(key, value), set_calls.append((key, value))),
    )
    monkeypatch.setattr(lc, "get_ai_breaker", lambda model=None: PassthroughBreaker())
    monkeypatch.setattr("services.settings_service.mask_secret", lambda v: "sk-...test")
    return store, set_calls, monkeypatch


def test_auth_error_repairs_key_from_env_and_retries(heal_env, monkeypatch):
    store, set_calls, monkeypatch = heal_env
    monkeypatch.setenv("XKIRO_API_KEY", "sk-fresh-env-key")
    bad = FakeCompletions([AUTH_ERR])
    good = FakeCompletions([None])
    clients = iter([FakeClient(bad), FakeClient(good)])
    monkeypatch.setattr(lc, "get_llm_client", lambda: next(clients))

    result = lc.chat_json("sys", "user")

    assert result == {"ok": True}
    assert bad.calls == 1 and good.calls == 1
    assert (LLM_KEY, "sk-fresh-env-key") in set_calls


def test_auth_error_without_matching_env_key_raises(heal_env, monkeypatch):
    store, set_calls, monkeypatch = heal_env
    monkeypatch.setenv("XKIRO_API_KEY", "sk-dead-key-from-reverted-db")  # same as DB
    bad = FakeCompletions([AUTH_ERR])
    monkeypatch.setattr(lc, "get_llm_client", lambda: FakeClient(bad))

    with pytest.raises(lc.LLMResponseError):
        lc.chat_json("sys", "user")
    assert set_calls == []
    assert bad.calls == 1


def test_non_auth_error_not_healed(heal_env, monkeypatch):
    store, set_calls, monkeypatch = heal_env
    monkeypatch.setenv("XKIRO_API_KEY", "sk-fresh-env-key")
    bad = FakeCompletions([RuntimeError("connection reset by peer")])
    monkeypatch.setattr(lc, "get_llm_client", lambda: FakeClient(bad))

    with pytest.raises(lc.LLMResponseError):
        lc.chat_json("sys", "user")
    assert set_calls == []


def test_model_routing_error_restores_preferred_model(heal_env, monkeypatch):
    store, set_calls, monkeypatch = heal_env
    monkeypatch.setattr(lc, "get_llm_model", lambda: "google/gemini-2.5-flash")
    bad = FakeCompletions([ROUTING_ERR])
    good = FakeCompletions([None])
    clients = iter([FakeClient(bad), FakeClient(good)])
    monkeypatch.setattr(lc, "get_llm_client", lambda: next(clients))

    result = lc.chat_json("sys", "user")

    assert result == {"ok": True}
    assert (LLM_MODEL_KEY, lc.PREFERRED_XKIRO_MODEL) in set_calls
    assert bad.calls == 1 and good.calls == 1


def test_chat_text_heals_too(heal_env, monkeypatch):
    store, set_calls, monkeypatch = heal_env
    monkeypatch.setenv("XKIRO_API_KEY", "sk-fresh-env-key")
    bad = FakeCompletions([AUTH_ERR])
    good = FakeClient(FakeCompletions([]))
    # chat_text returns raw text, not parsed JSON
    good.chat.completions.create = lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="plain text reply"))]
    )
    clients = iter([FakeClient(bad), good])
    monkeypatch.setattr(lc, "get_llm_client", lambda: next(clients))

    out = lc.chat_text("sys", "user")
    assert out == "plain text reply"
    assert (LLM_KEY, "sk-fresh-env-key") in set_calls
