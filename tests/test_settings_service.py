"""Unit tests for settings_service (encryption round-trip, masking, fallbacks)."""

import pytest

from services import settings_service as ss


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch, isolated_db):
    """Redirect DATA_DIR (master key location) to a temp dir + isolated DB."""
    monkeypatch.setattr("config.settings.settings.data_dir", str(tmp_path), raising=False)
    ss._SECRET_KEY_PATH = tmp_path / ".secret_key"
    return tmp_path


def test_encrypt_decrypt_roundtrip(temp_data_dir):
    stored = ss._encrypt("sk-secret-123")
    assert stored != "sk-secret-123"
    assert ss._decrypt(stored) == "sk-secret-123"


def test_master_key_reused(temp_data_dir):
    ss.set_setting("llm_api_key", "key-A")
    key_bytes = ss._SECRET_KEY_PATH.read_bytes()
    assert key_bytes  # key generated
    # Second call must reuse the same key (no regeneration)
    ss.set_setting("llm_api_key", "key-B")
    assert ss._SECRET_KEY_PATH.read_bytes() == key_bytes
    assert ss.get_setting("llm_api_key") == "key-B"


def test_delete_setting(temp_data_dir):
    ss.set_setting("plain_key", "v1")
    assert ss.get_setting("plain_key") == "v1"
    ss.set_setting("plain_key", None)
    assert ss.get_setting("plain_key") is None


def test_mask_secret():
    assert ss.mask_secret("sk-abcdefghijklmnop") == "sk-...mnop"
    assert ss.mask_secret("short") == "*****"
    assert ss.mask_secret(None) is None
    assert ss.mask_secret("") is None


def test_llm_key_env_fallback(temp_data_dir, monkeypatch):
    monkeypatch.delenv("XKIRO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ss.get_llm_api_key() is None
    monkeypatch.setenv("XKIRO_API_KEY", "env-key")
    assert ss.get_llm_api_key() == "env-key"
    # DB wins over env
    ss.set_setting("llm_api_key", "db-key")
    assert ss.get_llm_api_key() == "db-key"


def test_llm_base_url_default(temp_data_dir):
    assert ss.get_llm_base_url() == ss.DEFAULT_LLM_BASE_URL
