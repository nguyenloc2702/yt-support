"""Unit tests for Gemini Image Generation option & Fallback Chain."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.settings_service import (
    get_image_provider,
    is_gemini_image_enabled,
    set_gemini_image_enabled,
    set_image_provider,
)
from services.gemini_image_provider import (
    DEFAULT_IMAGE_TIMEOUT_SECONDS,
    MIN_IMAGE_BYTES,
    generate_gemini_image,
)
from services.image_kenburns_provider import ImageKenBurnsProvider
from services.video_provider import ErrorKind


@pytest.fixture(autouse=True)
def _restore_image_provider_setting():
    """set_gemini_image_enabled writes the REAL settings DB; these tests must
    not leak their toggle into the owner's live configuration (it flipped the
    worker's provider priority for every later test and run)."""
    from services.settings_service import get_setting, set_setting, GEMINI_IMAGE_ENABLED_KEY, IMAGE_PROVIDER_KEY

    snapshot = (get_setting(GEMINI_IMAGE_ENABLED_KEY), get_setting(IMAGE_PROVIDER_KEY))
    yield
    enabled, provider = snapshot
    if enabled is None:
        set_setting(GEMINI_IMAGE_ENABLED_KEY, None)
    else:
        set_setting(GEMINI_IMAGE_ENABLED_KEY, enabled)
    if provider is None:
        set_setting(IMAGE_PROVIDER_KEY, None)
    else:
        set_setting(IMAGE_PROVIDER_KEY, provider)


# ---------------------------------------------------------------------------
# 1. Settings Tests
# ---------------------------------------------------------------------------

def test_settings_gemini_image_toggle(monkeypatch):
    """Test getting and setting image provider options."""
    monkeypatch.delenv("GEMINI_IMAGE_ENABLED", raising=False)
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)

    # By default, should be False (opt-in)
    set_gemini_image_enabled(False)
    assert is_gemini_image_enabled() is False
    assert get_image_provider() == "xkiro"

    # Turn on
    set_gemini_image_enabled(True)
    assert is_gemini_image_enabled() is True
    assert get_image_provider() == "gemini"

    # Turn off
    set_gemini_image_enabled(False)
    assert is_gemini_image_enabled() is False
    assert get_image_provider() == "xkiro"

    # Set via set_image_provider
    set_image_provider("gemini")
    assert is_gemini_image_enabled() is True
    assert get_image_provider() == "gemini"

    set_image_provider("auto")
    assert is_gemini_image_enabled() is True

    set_image_provider("xkiro")
    assert is_gemini_image_enabled() is False
    assert get_image_provider() == "xkiro"

    with pytest.raises(ValueError, match="Invalid image provider"):
        set_image_provider("invalid_provider")


# ---------------------------------------------------------------------------
# 2. Gemini Image Provider Direct Tests
# ---------------------------------------------------------------------------

def test_gemini_image_generation_success(tmp_path):
    """Test successful image generation via GeminiClient."""
    out_file = tmp_path / "test_char.jpg"
    session_file = tmp_path / "acc_01.json"
    session_file.write_text('{"__Secure-1PSID": "fake_psid", "__Secure-1PSIDTS": "fake_psidts"}')

    fake_image = MagicMock()
    async def fake_save(path, filename):
        target = Path(path) / filename
        target.write_bytes(b"JPEG_MOCK_IMAGE_DATA" * 100)  # > 1000 bytes

    fake_image.save = AsyncMock(side_effect=fake_save)

    fake_response = MagicMock()
    fake_response.images = [fake_image]

    fake_client_instance = MagicMock()
    fake_client_instance.init = AsyncMock()
    fake_client_instance.generate_content = AsyncMock(return_value=fake_response)
    fake_client_instance.close = AsyncMock()
    fake_client_instance.cookies = {"__Secure-1PSIDTS": "rotated_psidts"}

    with patch("gemini_webapi.GeminiClient", return_value=fake_client_instance):
        ok = generate_gemini_image(
            prompt="A cute cartoon robot",
            out_path=out_file,
            session_file=session_file,
            sanitize=True,
        )

    assert ok is True
    assert out_file.exists()
    assert out_file.stat().st_size >= MIN_IMAGE_BYTES
    fake_client_instance.close.assert_awaited_once()


def test_gemini_image_zero_fake_policy(tmp_path):
    """Zero-fake: If Gemini generates an empty/undersized image, fail and remove."""
    out_file = tmp_path / "tiny_image.jpg"
    session_file = tmp_path / "acc_01.json"
    session_file.write_text('{"__Secure-1PSID": "fake_psid", "__Secure-1PSIDTS": "fake_psidts"}')

    fake_image = MagicMock()
    async def fake_save_tiny(path, filename):
        target = Path(path) / filename
        target.write_bytes(b"too_small")  # < 1000 bytes

    fake_image.save = AsyncMock(side_effect=fake_save_tiny)

    fake_response = MagicMock()
    fake_response.images = [fake_image]

    fake_client_instance = MagicMock()
    fake_client_instance.init = AsyncMock()
    fake_client_instance.generate_content = AsyncMock(return_value=fake_response)
    fake_client_instance.close = AsyncMock()

    with patch("gemini_webapi.GeminiClient", return_value=fake_client_instance):
        ok = generate_gemini_image(
            prompt="A tiny test",
            out_path=out_file,
            session_file=session_file,
        )

    assert ok is False
    assert not out_file.exists()  # Deleted due to undersize


def test_gemini_image_auth_error_triggers_kill_switch(tmp_path):
    """AuthError must trigger kill-switch on the account pool."""
    from gemini_webapi.exceptions import AuthError

    out_file = tmp_path / "fail.jpg"
    session_file = tmp_path / "acc_01.json"
    session_file.write_text('{"__Secure-1PSID": "dead_psid"}')

    fake_client_instance = MagicMock()
    fake_client_instance.init = AsyncMock(side_effect=AuthError("Session expired"))
    fake_client_instance.close = AsyncMock()

    mock_pool = MagicMock()
    acc_info = MagicMock()
    acc_info.acc_id = "acc_01"
    acc_info.session_file = str(session_file)
    mock_pool.get_available_account.return_value = acc_info

    with patch("gemini_webapi.GeminiClient", return_value=fake_client_instance):
        ok = generate_gemini_image(
            prompt="A banker",
            out_path=out_file,
            account_pool=mock_pool,
        )

    assert ok is False
    mock_pool.trigger_kill_switch.assert_called_once()
    assert "acc_01" in mock_pool.trigger_kill_switch.call_args[0][0]


# ---------------------------------------------------------------------------
# 3. Ken Burns Provider Priority Tests
# ---------------------------------------------------------------------------

def test_kenburns_uses_gemini_when_enabled(tmp_path, monkeypatch):
    """When is_gemini_image_enabled() is True, Ken Burns calls Gemini first."""
    out = tmp_path / "shots" / "shot_01.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    set_gemini_image_enabled(True)
    called_providers = []

    def fake_gemini(prompt, out_path, **kw):
        called_providers.append("gemini")
        Path(out_path).write_bytes(b"jpg_gemini" * 200)
        return True

    def fake_xkiro(prompt, out_path, **kw):
        called_providers.append("xkiro")
        Path(out_path).write_bytes(b"jpg_xkiro" * 200)
        return "openai/gpt-image-2.5"

    monkeypatch.setattr("services.gemini_image_provider.generate_gemini_image", fake_gemini)
    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)
    monkeypatch.setattr(
        "services.image_kenburns_provider.subprocess.run",
        lambda cmd, check, capture_output, timeout: (
            out.write_bytes(b"video" * 2000) or MagicMock(returncode=0)
        ),
    )

    kb = ImageKenBurnsProvider(fallback_provider=MagicMock())
    res = kb.generate(prompt="A computer chip smiling", out_path=out, session_file=tmp_path / "acc_01.json")

    assert res.ok is True
    assert called_providers == ["gemini"]  # Gemini called first, xKiro skipped to save money!


def test_kenburns_gemini_fallback_to_xkiro_on_failure(tmp_path, monkeypatch):
    """When Gemini fails, Ken Burns gracefully falls back to xKiro."""
    out = tmp_path / "shots" / "shot_02.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    set_gemini_image_enabled(True)
    called_providers = []

    def fake_gemini_fail(prompt, out_path, **kw):
        called_providers.append("gemini")
        return False  # Failed

    def fake_xkiro(prompt, out_path, **kw):
        called_providers.append("xkiro")
        Path(out_path).write_bytes(b"jpg_xkiro" * 200)
        return "openai/gpt-image-2.5"

    monkeypatch.setattr("services.gemini_image_provider.generate_gemini_image", fake_gemini_fail)
    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)
    monkeypatch.setattr(
        "services.image_kenburns_provider.subprocess.run",
        lambda cmd, check, capture_output, timeout: (
            out.write_bytes(b"video" * 2000) or MagicMock(returncode=0)
        ),
    )

    kb = ImageKenBurnsProvider(fallback_provider=MagicMock())
    res = kb.generate(prompt="A server room", out_path=out, session_file=tmp_path / "acc_01.json")

    assert res.ok is True
    assert called_providers == ["gemini", "xkiro"]  # Gemini tried first, fell back to xKiro


# ---------------------------------------------------------------------------
# 4. Character Reference Gate 3 Tests
# ---------------------------------------------------------------------------

def test_character_sheet_respects_gemini_option(tmp_path, monkeypatch):
    """_generate_character_image calls Gemini first when enabled."""
    from pipeline.automated_factory import AutomatedFactory

    ref = tmp_path / "character_ref.png"
    set_gemini_image_enabled(True)
    called_providers = []

    def fake_gemini(prompt, out_path, **kw):
        called_providers.append("gemini")
        Path(out_path).write_bytes(b"fake_gemini_ref" * 200)
        return True

    def fake_xkiro(prompt, out_path, **kw):
        called_providers.append("xkiro")
        return "openai/gpt-image-2.5"

    monkeypatch.setattr("services.gemini_image_provider.generate_gemini_image", fake_gemini)
    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)

    factory = AutomatedFactory(db_path=tmp_path / "test.db", account_pool=MagicMock())
    factory._generate_character_image(prompt="Character portrait of Richard", ref_path=ref)

    assert ref.exists()
    assert called_providers == ["gemini"]
