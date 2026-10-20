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


def test_summary_stratified_spans_entire_video():
    # 20 candidates spanning 1000s
    candidates = [
        _candidate(i * 50, (i + 1) * 50, score=0.6)
        for i in range(20)
    ]
    tl = build_timeline(candidates, "summary", target_duration=200.0, source_duration=1000.0)
    clips = tl["clips"]
    assert len(clips) >= 4
    # The summary MUST span from early video to late video (not clustered in first 20%)
    first_clip_start = clips[0]["source_start"]
    last_clip_end = clips[-1]["source_end"]
    assert first_clip_start < 200.0
    assert last_clip_end > 700.0
    # Total duration should be close to target 200.0s
    actual_dur = sum(c["source_end"] - c["source_start"] for c in clips)
    assert abs(actual_dur - 200.0) <= 2.0
    # Story arc labels must be assigned
    labels = {c.get("label") for c in clips}
    assert "hook" in labels
    assert "conclusion" in labels

