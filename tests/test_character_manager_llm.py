"""Unit tests for LLM-based character detection (pipeline.character_manager)."""

import pytest

from pipeline.character_manager import detect_characters_in_text_llm


@pytest.fixture(autouse=True)
def no_settings_db(monkeypatch):
    """Keep model-name lookup off the real settings DB."""
    monkeypatch.setattr(
        "services.settings_service.get_xkiro_generation_model",
        lambda: "test-model",
    )


def test_empty_text_returns_empty_list():
    assert detect_characters_in_text_llm("") == []
    assert detect_characters_in_text_llm("   \n  ") == []


def test_extracts_characters_from_llm_response(monkeypatch):
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, **kw):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "characters": [
                {
                    "name": "Vua Hà Lan",
                    "context": "Nhân vật chính, quyền lực",
                    "descriptor": "elderly king with grey beard, royal robe",
                }
            ]
        }

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    chars = detect_characters_in_text_llm("Kể về Vua Hà Lan và đoàn tàu buôn...")
    assert len(chars) == 1
    assert chars[0]["name"] == "Vua Hà Lan"
    assert "crayon" in chars[0]["descriptor"].lower() or "king" in chars[0]["descriptor"].lower()
    assert "NHÂN VẬT THỰC" in captured["system"]
    assert "KỊCH BẢN" in captured["user"]


def test_no_characters_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        "pipeline.llm_client.chat_json",
        lambda *a, **kw: {"characters": []},
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert detect_characters_in_text_llm("Kịch bản chỉ nói về con số và thị trường.") == []


def test_gateway_failure_raises_runtime_error(monkeypatch):
    from pipeline.llm_client import LLMResponseError

    def failing_chat_json(*a, **kw):
        raise LLMResponseError("gateway unreachable")

    monkeypatch.setattr("pipeline.llm_client.chat_json", failing_chat_json)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError, match="LLM character extraction failed"):
        detect_characters_in_text_llm("Một kịch bản có nhân vật rõ ràng.")


def test_pytest_guard_blocks_real_gateway_call():
    """Under pytest the function must hard-fail so callers mock it — a real
    gateway call from the test suite would be a silent network dependency."""
    with pytest.raises(RuntimeError, match="pytest"):
        detect_characters_in_text_llm("Kịch bản có nhân vật.")
