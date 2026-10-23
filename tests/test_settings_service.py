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


def test_veo_is_off_until_the_owner_turns_it_on(temp_data_dir):
    """Default must be Ken Burns: Veo dies when a cookie dies, so it is opt-in."""
    assert ss.get_setting(ss.VEO_ENABLED_KEY) is None
    assert ss.is_veo_enabled() is False
    ss.set_veo_enabled(True)
    assert ss.is_veo_enabled() is True
    ss.set_veo_enabled(False)
    assert ss.is_veo_enabled() is False


def test_veo_flag_is_db_only_not_env(temp_data_dir, monkeypatch):
    """One control surface on purpose: /veo and the Settings page write the DB."""
    monkeypatch.setenv("VEO_ENABLED", "yes")
    ss.set_setting(ss.VEO_ENABLED_KEY, None)
    assert ss.is_veo_enabled() is False
    ss.set_setting(ss.VEO_ENABLED_KEY, "TRUE")
    assert ss.is_veo_enabled() is True
    ss.set_setting(ss.VEO_ENABLED_KEY, "0")
    assert ss.is_veo_enabled() is False


def test_vlm_model_does_not_inherit_text_only_generator(temp_data_dir, monkeypatch):
    """A text-only primary must never become the vision model by inheritance."""
    monkeypatch.delenv("XKIRO_VLM_MODEL", raising=False)
    ss.set_setting(ss.VLM_MODEL_KEY, None)
    ss.set_setting(ss.LLM_MODEL_KEY, "mistralai/mistral-large-4-0")
    assert ss.get_vlm_model() == ss.DEFAULT_XKIRO_VLM_MODEL
    assert ss.get_vlm_model() != ss.get_llm_model()


def test_generation_model_default_is_the_fastest_measured_model(temp_data_dir):
    assert ss.get_xkiro_generation_model() == "mistralai/mistral-large-4-0"
