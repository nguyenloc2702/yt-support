"""Tests for the script-led output timeline planner."""

import pytest

from pipeline.script_timeline_planner import (
    allocate_beat_windows,
    plan_script_timeline,
)


def _script(target=60.0, beats=None):
    return {
        "title": "T",
        "target_duration_seconds": target,
        "duration_tolerance_seconds": 2.0,
        "beats": beats
        or [
            {"id": 0, "text": "beat mot hai ba", "emotion": "hype", "source_segment_ids": [0]},
            {"id": 1, "text": "beat hai bon nam sau", "emotion": "bua", "source_segment_ids": [1, 2]},
        ],
    }


def _transcript():
    return {
        "segments": [
            # Plenty of material so beat budgets can be filled (60s target).
            {"id": 0, "start": 10.0, "end": 60.0, "text": "a"},
            # NOTE: big gap between 60s and 200s — legacy mapper would stretch.
            {"id": 1, "start": 200.0, "end": 220.0, "text": "b"},
            {"id": 2, "start": 221.0, "end": 240.0, "text": "c"},
        ]
    }


def test_allocate_windows_sum_to_target():
    windows = allocate_beat_windows(_script(target=60.0))
    assert len(windows) == 2
    total = sum(w["duration"] for w in windows)
    assert total == pytest.approx(60.0, abs=0.01)
    assert windows[0]["output_start"] == pytest.approx(0.0)


def test_plan_output_duration_matches_target():
    timeline = plan_script_timeline(_script(target=60.0), _transcript())
    assert timeline["actual_duration"] == pytest.approx(60.0, abs=0.5)
    assert timeline["duration_status"] == "ok"
    assert timeline["visual_coverage"] == pytest.approx(1.0, abs=0.01)


def test_plan_does_not_stretch_across_source_gaps():
    """A beat referencing segments 200s apart must not produce a 200s clip."""
    timeline = plan_script_timeline(_script(target=60.0), _transcript())
    for clip in timeline["clips"]:
        # Clip must not bridge the 60s..200s source gap: source span == output span.
        src_span = clip["source_end"] - clip["source_start"]
        assert src_span == pytest.approx(clip["duration"], abs=0.05)
        assert src_span <= 40.0
    # Output times must be contiguous from 0
    cursor = 0.0
    for clip in sorted(timeline["clips"], key=lambda c: c["output_start"]):
        assert clip["output_start"] == pytest.approx(cursor, abs=0.05)
        cursor = clip["output_end"]


def test_clips_carry_narration_and_emotion():
    timeline = plan_script_timeline(_script(target=60.0), _transcript())
    by_beat = {c["beat_id"]: c for c in timeline["clips"]}
    assert by_beat[0]["narration_text"] == "beat mot hai ba"
    assert by_beat[0]["emotion"] == "hype"
    assert by_beat[1]["emotion"] == "bua"


def test_plan_warns_on_missing_segments():
    script = _script(beats=[{"id": 0, "text": "x", "emotion": "hype", "source_segment_ids": [999]}])
    timeline = plan_script_timeline(script, _transcript())
    assert timeline["clips"] == []
    assert any("999" in w or "segment" in w for w in timeline["warnings"])
