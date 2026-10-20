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


def test_xkiro_voices_metadata():
    assert len(voice_service.XKIRO_VOICES_METADATA) == 4
    ids = {v["id"] for v in voice_service.XKIRO_VOICES_METADATA}
    assert "confident-male-vietnamese" in ids
    assert "sweet-female-vietnamese" in ids


def test_preview_voice_routes_xkiro(monkeypatch, tmp_path):
    called = []

    def fake_synth(text, voice, out_path):
        called.append((text, voice, out_path))
        out_path.write_bytes(b"dummy_mp3")

    monkeypatch.setattr("pipeline.tts.synthesize_one_xkiro", fake_synth)
    out = tmp_path / "sample.mp3"
    voice_service.preview_voice("confident-male-vietnamese", out)
    assert len(called) == 1
    assert called[0][1] == "confident-male-vietnamese"
    assert out.exists()

