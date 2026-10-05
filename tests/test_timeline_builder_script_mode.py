"""Tests for timeline_builder edge cases and renderer-compatible clips."""

from pipeline.timeline_builder import build_timeline


def _candidate(start, end, score=0.8):
    return {
        "source_start": start,
        "source_end": end,
        "duration": end - start,
        "scores": {"overall": score},
        "reason": "",
    }


def test_empty_candidates_fallback():
    tl = build_timeline([], "remove_silence", target_duration=60.0, source_duration=120.0)
    assert tl["clips"] and tl["clips"][0]["source_start"] == 0.0


def test_shorten_selects_top_until_target():
    candidates = [_candidate(0, 10, 0.5), _candidate(20, 40, 0.9), _candidate(50, 70, 0.7)]
    tl = build_timeline(candidates, "shorten", target_duration=25.0, source_duration=100.0)
    total = sum(c["source_end"] - c["source_start"] for c in tl["clips"])
    assert total <= 25.0 + 0.001
    assert len(tl["clips"]) >= 1


def test_all_clips_renderer_compatible_fields():
    candidates = [_candidate(0, 5), _candidate(10, 15)]
    tl = build_timeline(candidates, "remove_silence", target_duration=30.0, source_duration=30.0)
    for i, clip in enumerate(tl["clips"], start=1):
        assert clip["source_end"] > clip["source_start"]
        assert clip["order"] == i
        assert clip["enabled"] is True
        assert "transition" in clip
    assert tl["actual_duration"] > 0
    assert tl["render_options"]["video_codec"] == "h264"
