"""Tests for beat mapping and the script approval gate."""

import pytest

from core.exceptions import ScriptNotApprovedError
from pipeline.beat_mapper import beats_to_clips, estimate_script_duration
from services.script_service import ScriptService


@pytest.fixture
def transcript():
    return {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "Xin chào mọi người"},
            {"id": 1, "start": 3.5, "end": 8.0, "text": "Hôm nay review sản phẩm này"},
            {"id": 2, "start": 9.0, "end": 14.0, "text": "Chất lượng khá ổn"},
            {"id": 3, "start": 15.0, "end": 20.0, "text": "Giá cũng hợp lý"},
        ]
    }


def test_beats_to_clips_basic(transcript):
    script = {
        "beats": [
            {"id": 0, "text": "A", "source_segment_ids": [0]},
            {"id": 1, "text": "B", "source_segment_ids": [2, 3]},
        ]
    }
    clips = beats_to_clips(script, transcript)
    assert len(clips) == 2
    assert clips[0]["source_start"] == 0.0
    assert clips[0]["source_end"] == 3.0
    assert clips[1]["source_start"] == 9.0
    assert clips[1]["source_end"] == 20.0
    assert estimate_script_duration(script, transcript) == pytest.approx(
        sum(c["source_end"] - c["source_start"] for c in clips)
    )


def test_beats_invalid_ids_skipped(transcript):
    script = {"beats": [{"id": 0, "text": "A", "source_segment_ids": [99]}]}
    assert beats_to_clips(script, transcript) == []


def test_gate_rejects_unapproved(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    svc = ScriptService()
    with pytest.raises(ScriptNotApprovedError):
        svc.build_script_timeline("fake_project")


def test_gate_allows_approved(tmp_path, monkeypatch, transcript):
    import json

    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    proj = tmp_path / "p1"
    (proj / "analysis").mkdir(parents=True)
    (proj / "analysis" / "transcription.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (proj / "scripts").mkdir()
    (proj / "scripts" / "current.json").write_text(
        json.dumps(
            {
                "title": "T",
                "beats": [{"id": 0, "text": "A", "source_segment_ids": [0]}],
                "status": "approved",
            }
        ),
        encoding="utf-8",
    )
    svc = ScriptService()
    timeline = svc.build_script_timeline("p1")
    assert timeline["mode"] == "script_review"
    assert len(timeline["clips"]) == 1
    assert timeline["clips"][0]["source_start"] == 0.0
