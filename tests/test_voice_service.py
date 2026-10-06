"""Tests for the dynamic voice catalog service (provider mocked)."""

import pytest

from core.exceptions import RenderError
from services import voice_service


@pytest.fixture(autouse=True)
def reset_cache():
    voice_service._voice_cache["voices"] = None
    voice_service._voice_cache["fetched_at"] = 0.0
    yield
    voice_service._voice_cache["voices"] = None
    voice_service._voice_cache["fetched_at"] = 0.0


FAKE_VOICES = [
    {"id": "vi-VN-HoaiMyNeural", "locale": "vi-VN", "gender": "Female", "styles": ["General"]},
    {"id": "vi-VN-NamMinhNeural", "locale": "vi-VN", "gender": "Male", "styles": ["General"]},
    {"id": "en-US-AriaNeural", "locale": "en-US", "gender": "Female", "styles": ["News"]},
    {"id": "en-US-GuyNeural", "locale": "en-US", "gender": "Male", "styles": ["Novel"]},
]


def _inject_cache():
    voice_service._voice_cache["voices"] = FAKE_VOICES
    import time

    voice_service._voice_cache["fetched_at"] = time.time()


def test_list_voices_filters_locale_and_gender(monkeypatch):
    _inject_cache()
    vi = voice_service.list_voices(language="vi-VN")
    assert {v["id"] for v in vi} == {"vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"}

    males = voice_service.list_voices(language="vi-VN", gender="Male")
    assert [v["id"] for v in males] == ["vi-VN-NamMinhNeural"]


def test_list_languages_unique_sorted(monkeypatch):
    _inject_cache()
    assert voice_service.list_languages() == ["en-US", "vi-VN"]


def test_validate_voice(monkeypatch):
    _inject_cache()
    assert voice_service.validate_voice("vi-VN-NamMinhNeural")
    assert not voice_service.validate_voice("vi-VN-GhostNeural")


def test_get_voice_returns_metadata(monkeypatch):
    _inject_cache()
    v = voice_service.get_voice("en-US-AriaNeural")
    assert v and v["gender"] == "Female" and v["locale"] == "en-US"


def test_provider_failure_uses_cached_list(monkeypatch):
    _inject_cache()
    # Expire cache but keep the voices dict — fallback path must still serve it.
    voice_service._voice_cache["fetched_at"] = 0.0

    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr("edge_tts.list_voices", boom)
    voices = voice_service.list_voices(language="vi-VN")
    assert len(voices) == 2


def test_provider_failure_no_cache_raises(monkeypatch):
    import asyncio

    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr("edge_tts.list_voices", boom)
    with pytest.raises(RenderError, match="danh sách giọng"):
        voice_service.list_voices()
