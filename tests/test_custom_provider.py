"""Unit tests for custom provider management, presets, and connection testing.

Tests provider presets, DB settings persistence, Fernet encryption, and
test_provider_connection behavior (success, 401, vision errors).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.database import init_db
from services.settings_service import (
    PROVIDER_PRESETS,
    get_llm_api_key,
    get_provider_preset,
    get_vlm_model,
    is_vlm_enabled,
    set_provider_preset,
    set_setting,
    set_vlm_enabled,
    set_vlm_model,
    verify_provider_connection,
)


def setup_module():
    init_db()


def test_provider_presets_structure():
    """Verify built-in presets have valid base URLs and model lists."""
    assert "openai" in PROVIDER_PRESETS
    assert "gemini" in PROVIDER_PRESETS
    assert "openrouter" in PROVIDER_PRESETS
    assert "ollama" in PROVIDER_PRESETS
    assert "xkiro" in PROVIDER_PRESETS
    assert "custom" in PROVIDER_PRESETS

    gemini = PROVIDER_PRESETS["gemini"]
    assert "googleapis.com" in gemini["base_url"]
    assert "gemini-2.0-flash" in gemini["suggested_models"]

    ollama = PROVIDER_PRESETS["ollama"]
    assert "11434" in ollama["base_url"]
    assert "qwen2-vl" in ollama["suggested_models"]


def test_vlm_settings_persistence():
    """Verify saving and retrieving VLM model, preset, and enable flag."""
    set_provider_preset("gemini")
    assert get_provider_preset() == "gemini"

    set_vlm_model("gemini-1.5-flash")
    assert get_vlm_model() == "gemini-1.5-flash"

    set_vlm_enabled(True)
    assert is_vlm_enabled() is True

    set_vlm_enabled(False)
    assert is_vlm_enabled() is False


def test_api_key_fernet_encryption():
    """API key is stored encrypted and retrieved cleanly."""
    raw_key = "sk-custom-secret-key-12345"
    set_setting("llm_api_key", raw_key)
    retrieved = get_llm_api_key()
    assert retrieved == raw_key


def test_test_provider_connection_empty_url():
    """Empty base_url triggers a clear validation error."""
    result = verify_provider_connection(base_url="", api_key="sk-test", model_name="gpt-4o")
    assert result["success"] is False
    assert "Base URL" in result["message"]


@patch("openai.OpenAI")
def test_test_provider_connection_success(mock_openai_cls):
    """Successful connection test returns success with latency."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    mock_choice = MagicMock()
    mock_choice.message.content = "OK"
    mock_response = MagicMock(choices=[mock_choice])
    mock_client.chat.completions.create.return_value = mock_response

    result = verify_provider_connection(
        base_url="https://api.openai.com/v1",
        api_key="sk-valid-key",
        model_name="gpt-4o-mini",
        test_vision=True,
    )

    assert result["success"] is True
    assert "Kết nối thành công" in result["message"]
    assert result["is_multimodal"] is True
    assert result["latency_ms"] >= 0


@patch("openai.OpenAI")
def test_test_provider_connection_auth_error(mock_openai_cls):
    """401 Unauthorized returns a user-friendly error message."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = Exception("Error 401: Unauthorized API key")

    result = verify_provider_connection(
        base_url="https://api.openai.com/v1",
        api_key="sk-invalid-key",
        model_name="gpt-4o-mini",
        test_vision=True,
    )

    assert result["success"] is False
    assert "401" in result["message"]
    assert "API key không hợp lệ" in result["message"]


@patch("openai.OpenAI")
def test_test_provider_connection_vision_unsupported(mock_openai_cls):
    """Model that rejects vision payloads returns a helpful warning."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = Exception("Model does not support image or vision inputs")

    result = verify_provider_connection(
        base_url="https://api.openai.com/v1",
        api_key="sk-valid-key",
        model_name="gpt-3.5-turbo",
        test_vision=True,
    )

    assert result["success"] is False
    assert "không hỗ trợ ảnh/Vision" in result["message"]
