"""Tests for pipeline.tts: narration extraction, tempo fitting, track building mocks."""
from pathlib import Path

import pytest

from core.exceptions import RenderError
from pipeline.tts import (
    MAX_TEMPO,
    narration_items_from_timeline,
    synthesize_narration,
)


def test_narration_items_from_timeline():
    timeline = {
        "clips": [
            {"source_start": 0.0, "source_end": 10.0, "enabled": True, "narration_text": "Xin chào"},
            {"source_start": 10.0, "source_end": 15.0, "enabled": True, "narration_text": ""},
            {"source_start": 15.0, "source_end": 25.0, "enabled": True, "narration_text": "Đây là đoạn hai"},
            {"source_start": 25.0, "source_end": 30.0, "enabled": False, "narration_text": "bị tắt"},
        ]
    }
    items = narration_items_from_timeline(timeline)
    assert len(items) == 2
    assert items[0]["output_start"] == 0.0
    assert items[0]["clip_duration"] == 10.0
    assert items[0]["text"] == "Xin chào"
    # second narration starts after enabled clip durations: 10 + 5 = 15
    assert items[1]["output_start"] == 15.0
    assert items[1]["clip_duration"] == 10.0


def test_narration_items_empty():
    assert narration_items_from_timeline({"clips": []}) == []
    assert narration_items_from_timeline({"clips": [{"source_start": 0, "source_end": 5}]}) == []


def test_narration_items_longer_than_clip_gets_tempo(monkeypatch):
    timeline = {
        "clips": [{"source_start": 0.0, "source_end": 5.0, "enabled": True, "narration_text": "dài"}]
    }
    items = narration_items_from_timeline(timeline)

    async def fake_synth_one(text, voice, rate, volume, out_path):
        out_path.write_bytes(b"fake-mp3")

    monkeypatch.setattr("pipeline.tts._synthesize_one", fake_synth_one)
    monkeypatch.setattr("pipeline.tts._probe_duration", lambda p: 10.0)

    narrated = synthesize_narration(items, Path("tmp_tts_test"))
    assert narrated[0]["tempo"] == pytest.approx(MAX_TEMPO)

    # cleanup
    import shutil
    shutil.rmtree("tmp_tts_test", ignore_errors=True)


def test_tempo_capped():
    assert MAX_TEMPO <= 1.6


def test_synthesize_failure_raises_and_cleans(monkeypatch, tmp_path):
    items = [{"output_start": 0.0, "text": "abc", "clip_duration": 3.0}]

    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("pipeline.tts._synthesize_one", boom)
    with pytest.raises(RenderError, match="TTS synthesis failed"):
        synthesize_narration(items, tmp_path / "out")
    assert not (tmp_path / "out").exists()
