"""Tests for pipeline.script_rewrite with chat_json mocked (no real LLM)."""

import json

import pytest

from core.exceptions import AnalysisError
from pipeline import script_rewrite as sr
from pipeline.script_rewrite import rewrite_script


def test_rewrite_script_builds_beats(monkeypatch):
    transcript = {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "chao"},
            {"id": 1, "start": 3.5, "end": 8.0, "text": "review"},
        ]
    }
    llm_payload = {
        "title": "Review bựa",
        "beats": [
            {"text": "Beat A", "source_segment_ids": [0], "emotion": "excited"},
            {"text": "Beat B", "source_segment_ids": [1, 99], "emotion": "funny"},  # 99 invalid
            {"text": "Beat C", "source_segment_ids": [42], "emotion": "neutral"},  # all invalid
        ],
    }
    monkeypatch.setattr(sr, "chat_json", lambda *a, **k: llm_payload)
    script = rewrite_script(transcript, tone="bựa")
    assert script["title"] == "Review bựa"
    assert script["status"] == "draft"
    # Beat B keeps only the valid id [1]; Beat C dropped entirely
    assert len(script["beats"]) == 2
    assert script["beats"][1]["source_segment_ids"] == [1]


def test_rewrite_script_all_invalid_beats_raises(monkeypatch):
    transcript = {"segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "x"}]}
    monkeypatch.setattr(
        sr, "chat_json", lambda *a, **k: {"title": "T", "beats": [{"text": "a", "source_segment_ids": [7]}]}
    )
    with pytest.raises(AnalysisError):
        rewrite_script(transcript)


def test_rewrite_script_empty_transcript_raises():
    with pytest.raises(AnalysisError):
        rewrite_script({"segments": []})


def test_save_and_load_script_roundtrip(tmp_path):
    script = {"title": "T", "status": "draft", "beats": [{"id": 0, "text": "a", "source_segment_ids": [0]}]}
    path = sr.save_script(tmp_path, script)
    assert path == tmp_path / "scripts" / "current.json"
    assert sr.load_script(tmp_path) == script


def test_load_script_corrupt_returns_none(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "current.json").write_text("{broken", encoding="utf-8")
    assert sr.load_script(tmp_path) is None
    assert sr.load_script(tmp_path / "nonexistent") is None
