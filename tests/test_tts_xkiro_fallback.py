"""Unit tests for xKiro TTS synthesis with explicit edge-tts fallback (pipeline.tts)."""

from pathlib import Path

import pytest

from pipeline.tts import (
    XKIRO_TO_EDGE,
    is_xkiro_voice,
    synthesize_one_xkiro_with_fallback,
)


def test_is_xkiro_voice_catalog():
    assert is_xkiro_voice("confident-male-vietnamese")
    assert is_xkiro_voice("male-voice")
    assert is_xkiro_voice("xkiro:custom")
    assert not is_xkiro_voice("vi-VN-NamMinhNeural")
    assert not is_xkiro_voice("en-US-GuyNeural")
    assert not is_xkiro_voice("")
    assert not is_xkiro_voice(None)


def test_fallback_returns_original_voice_on_success(tmp_path: Path, monkeypatch):
    out = tmp_path / "seg.wav"

    def fake_xkiro(text, voice_id, out_path, api_key=None):
        out_path.write_bytes(b"fake-wav")
        return 8.0

    monkeypatch.setattr("pipeline.tts.synthesize_one_xkiro", fake_xkiro)
    used = synthesize_one_xkiro_with_fallback("Xin chào", "confident-male-vietnamese", out)
    assert used == "confident-male-vietnamese"
    assert out.read_bytes() == b"fake-wav"


def test_fallback_swaps_to_edge_on_xkiro_failure(tmp_path: Path, monkeypatch):
    out = tmp_path / "seg.wav"

    def failing_xkiro(text, voice_id, out_path, api_key=None):
        raise RuntimeError("xKiro TTS 503")

    monkeypatch.setattr("pipeline.tts.synthesize_one_xkiro", failing_xkiro)

    class FakeCommunicate:
        def __init__(self, text, voice):
            assert voice == "vi-VN-NamMinhNeural"  # mapped edge fallback voice

        async def save(self, path):
            Path(path).write_bytes(b"edge-wav")

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    used = synthesize_one_xkiro_with_fallback("Xin chào", "confident-male-vietnamese", out)
    assert used == "vi-VN-NamMinhNeural"
    assert out.read_bytes() == b"edge-wav"


def test_fallback_reraises_when_no_edge_mapping(tmp_path: Path, monkeypatch):
    out = tmp_path / "seg.wav"

    def failing_xkiro(text, voice_id, out_path, api_key=None):
        raise RuntimeError("xKiro TTS 503")

    monkeypatch.setattr("pipeline.tts.synthesize_one_xkiro", failing_xkiro)

    unknown_voice = "totally-unknown-voice"
    assert unknown_voice not in XKIRO_TO_EDGE
    with pytest.raises(RuntimeError, match="xKiro TTS 503"):
        synthesize_one_xkiro_with_fallback("Hello", unknown_voice, out)


def test_edge_mapping_covers_full_xkiro_catalog():
    from pipeline.tts import XKIRO_VOICES

    assert XKIRO_TO_EDGE.keys() >= XKIRO_VOICES
