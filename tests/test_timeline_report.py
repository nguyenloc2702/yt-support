"""Unit tests for pipeline/timeline_report.py.

Tests timestamp remapping, transcript boundary splitting, audio state evaluation,
shot analysis integration, and empty/edge cases.
"""

from __future__ import annotations

from pipeline.timeline_report import build_timeline_report


def test_empty_timeline():
    """Empty timeline returns a valid empty report without error."""
    timeline = {"clips": [], "actual_duration": 0.0}
    report = build_timeline_report(timeline)

    assert report["schema_version"] == 1
    assert report["output_duration_seconds"] == 0.0
    assert report["rows"] == []
    assert isinstance(report["notes"], list)


def test_single_clip_no_transcript_no_scenes():
    """Single clip without speech or scenes maps output timestamps directly to source."""
    timeline = {
        "clips": [
            {
                "clip_id": "clip_1",
                "source_start": 10.0,
                "source_end": 20.0,
                "output_start": 0.0,
                "output_end": 10.0,
                "duration": 10.0,
            }
        ],
        "actual_duration": 10.0,
    }
    report = build_timeline_report(timeline)

    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["row_id"] == "output_0000"
    assert row["output_start"] == 0.0
    assert row["output_end"] == 10.0
    assert row["source_start"] == 10.0
    assert row["source_end"] == 20.0
    assert row["has_speech"] is False
    assert row["audio_state"] == "noise_or_music_or_unknown"
    assert "nhịp độ" in row["visual_description"]


def test_transcript_boundary_splitting_and_remapping():
    """Clip is split at transcript boundaries, output timestamps remapped proportionally."""
    timeline = {
        "clips": [
            {
                "clip_id": "clip_1",
                "source_start": 10.0,
                "source_end": 20.0,
                "output_start": 5.0,
                "output_end": 15.0,
                "duration": 10.0,
            }
        ],
        "actual_duration": 15.0,
    }
    transcript = [
        {"start": 12.0, "end": 16.0, "text": "Xin chào quý vị khán giả"}
    ]
    report = build_timeline_report(timeline, transcript=transcript)

    # Boundaries: 10.0, 12.0, 16.0, 20.0 -> 3 rows
    assert len(report["rows"]) == 3

    # Row 0: 10.0 -> 12.0 (duration 2.0s). Output: 5.0 -> 7.0
    r0 = report["rows"][0]
    assert r0["source_start"] == 10.0
    assert r0["source_end"] == 12.0
    assert r0["output_start"] == 5.0
    assert r0["output_end"] == 7.0
    assert r0["has_speech"] is False
    assert r0["transcript"] == ""

    # Row 1: 12.0 -> 16.0 (duration 4.0s). Output: 7.0 -> 11.0
    r1 = report["rows"][1]
    assert r1["source_start"] == 12.0
    assert r1["source_end"] == 16.0
    assert r1["output_start"] == 7.0
    assert r1["output_end"] == 11.0
    assert r1["has_speech"] is True
    assert "Xin chào quý vị khán giả" in r1["transcript"]
    assert r1["audio_state"] == "speech_detected"
    assert "chủ thể phát biểu/thuyết minh" in r1["visual_description"]

    # Row 2: 16.0 -> 20.0 (duration 4.0s). Output: 11.0 -> 15.0
    r2 = report["rows"][2]
    assert r2["source_start"] == 16.0
    assert r2["source_end"] == 20.0
    assert r2["output_start"] == 11.0
    assert r2["output_end"] == 15.0
    assert r2["has_speech"] is False


def test_audio_state_detection():
    """Correctly assigns speech_detected, silence, or noise_or_music_or_unknown."""
    timeline = {
        "clips": [
            {
                "source_start": 0.0,
                "source_end": 30.0,
                "output_start": 0.0,
                "output_end": 30.0,
                "duration": 30.0,
            }
        ]
    }
    transcript = [{"start": 0.0, "end": 10.0, "text": "Đoạn có thoại"}]
    silences = [{"start": 10.0, "end": 20.0}]

    report = build_timeline_report(timeline, transcript=transcript, silences=silences)
    assert len(report["rows"]) >= 2

    row_speech = report["rows"][0]
    assert row_speech["audio_state"] == "speech_detected"

    row_silence = next(r for r in report["rows"] if r["source_start"] == 10.0)
    assert row_silence["audio_state"] == "silence"
    assert "không có âm thanh hội thoại" in row_silence["visual_description"]


def test_multiple_clips_unique_rows_and_ordering():
    """Multiple clips create sequentially ordered rows with unique row_ids."""
    timeline = {
        "clips": [
            {"source_start": 5.0, "source_end": 10.0, "output_start": 0.0, "output_end": 5.0},
            {"source_start": 20.0, "source_end": 30.0, "output_start": 5.0, "output_end": 15.0},
            {"source_start": 40.0, "source_end": 45.0, "output_start": 15.0, "output_end": 20.0},
        ],
        "actual_duration": 20.0,
    }
    report = build_timeline_report(timeline)

    assert len(report["rows"]) == 3
    row_ids = [r["row_id"] for r in report["rows"]]
    assert row_ids == ["output_0000", "output_0001", "output_0002"]

    for i in range(len(report["rows"]) - 1):
        assert report["rows"][i]["output_end"] <= report["rows"][i + 1]["output_start"]


def test_shot_analysis_integration_with_keyframes():
    """Enriches report with shot ID, keyframe timestamps, and pacing."""
    timeline = {
        "clips": [
            {
                "source_start": 0.0,
                "source_end": 10.0,
                "output_start": 0.0,
                "output_end": 10.0,
            }
        ]
    }
    shots = [
        {
            "shot_id": "shot_001",
            "start_ms": 0,
            "end_ms": 10000,
            "keyframes": [{"time": 2.5}, {"time": 7.0}],
            "confidence": 0.95,
        }
    ]
    report = build_timeline_report(timeline, shots=shots)

    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert "shot_001" in row["scene_ids"]
    assert "shot_001" in row["visual_description"]
    assert "keyframe mốc: 2.5s, 7.0s" in row["visual_description"]
    assert row["confidence"] in ("medium", "high")
