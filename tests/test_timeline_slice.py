import pytest
from core.exceptions import RenderError
from pipeline.audio_mix import slice_timeline_by_output_range


def test_slice_timeline_single_clip():
    timeline = {
        "version": 1,
        "clips": [
            {"id": "c1", "source_start": 10.0, "source_end": 40.0, "order": 1, "enabled": True}
        ],
    }

    derived, offset = slice_timeline_by_output_range(timeline, output_start=5.0, duration=15.0)
    assert offset == 5.0
    assert len(derived["clips"]) == 1
    clip = derived["clips"][0]
    # In full output, c1 runs 0 to 30. Slice is 5 to 20 (dur 15).
    # c1 source_start was 10.0, so slice source_start = 10 + 5 = 15.0
    # source_end = 15 + 15 = 30.0
    assert clip["source_start"] == 15.0
    assert clip["source_end"] == 30.0
    assert derived["actual_duration"] == 15.0


def test_slice_timeline_across_multiple_clips():
    timeline = {
        "version": 1,
        "clips": [
            {"id": "c1", "source_start": 0.0, "source_end": 10.0, "order": 1, "enabled": True},
            {"id": "c2", "source_start": 20.0, "source_end": 35.0, "order": 2, "enabled": True},
        ],
    }
    # c1 is 10s (timeline 0-10), c2 is 15s (timeline 10-25). Total 25s.
    # Slice 5s to 15s (dur 10s):
    # Overlaps c1 from 5 to 10 (dur 5s) -> source 5 to 10
    # Overlaps c2 from 10 to 15 (dur 5s) -> source 20 to 25
    derived, offset = slice_timeline_by_output_range(timeline, output_start=5.0, duration=10.0)
    assert offset == 5.0
    assert len(derived["clips"]) == 2

    assert derived["clips"][0]["source_start"] == 5.0
    assert derived["clips"][0]["source_end"] == 10.0

    assert derived["clips"][1]["source_start"] == 20.0
    assert derived["clips"][1]["source_end"] == 25.0

    assert derived["actual_duration"] == 10.0


def test_slice_timeline_out_of_bounds():
    timeline = {
        "version": 1,
        "clips": [
            {"id": "c1", "source_start": 0.0, "source_end": 10.0, "order": 1, "enabled": True}
        ],
    }
    with pytest.raises(RenderError, match="does not overlap"):
        slice_timeline_by_output_range(timeline, output_start=15.0, duration=5.0)


def test_slice_timeline_prefers_explicit_duration_over_source_span():
    """Script-led planner clips carry an explicit output `duration` that may
    differ from the source span (source gap bridging is forbidden). Slicing
    must accumulate output positions from `duration`, not source span."""
    timeline = {
        "version": 2,
        "clips": [
            {
                "id": "b0",
                "source_start": 10.0,
                "source_end": 40.0,  # source span 30s == output window
                "duration": 30.0,
                "output_start": 0.0,
                "output_end": 30.0,
                "beat_id": 0,
                "narration_text": "beat mot",
                "order": 1,
                "enabled": True,
            },
            {
                "id": "b1",
                "source_start": 200.0,
                "source_end": 230.0,  # source span 30s == output window
                "duration": 30.0,
                "output_start": 30.0,
                "output_end": 60.0,
                "beat_id": 1,
                "narration_text": "beat hai",
                "order": 2,
                "enabled": True,
            },
        ],
    }
    # Output timeline: b0 = 0-30, b1 = 30-60 (60s total, NOT 10+40=50s).
    # Slice 25-35s should take 5s from b0 and 5s from b1.
    derived, offset = slice_timeline_by_output_range(timeline, output_start=25.0, duration=10.0)
    assert offset == 25.0
    assert len(derived["clips"]) == 2

    first, second = derived["clips"]
    # From b0: overlap 25-30 (last 5s of its window) -> source 10+25=35..40
    assert first["source_start"] == pytest.approx(35.0)
    assert first["source_end"] == pytest.approx(40.0)
    # From b1: overlap 30-35 (first 5s of its window) -> source 200..205
    assert second["source_start"] == pytest.approx(200.0)
    assert second["source_end"] == pytest.approx(205.0)

    assert derived["actual_duration"] == pytest.approx(10.0)
    # Sliced clips must not bridge the source gap.
    assert second["source_start"] - first["source_end"] > 100.0
    # Derived output metadata must be rebased for downstream TTS/QC.
    assert first["duration"] == pytest.approx(5.0)
    assert first["output_start"] == pytest.approx(0.0)
    assert first["output_end"] == pytest.approx(5.0)
    assert second["duration"] == pytest.approx(5.0)
    assert second["output_start"] == pytest.approx(5.0)
    assert second["output_end"] == pytest.approx(10.0)


def test_slice_timeline_falls_back_to_output_coordinates():
    """Clips without explicit `duration` but with output_start/output_end use
    output coordinates for accumulation."""
    timeline = {
        "version": 2,
        "clips": [
            {
                "id": "b0",
                "source_start": 100.0,
                "source_end": 120.0,  # source span 20s == output window
                "output_start": 0.0,
                "output_end": 20.0,
                "order": 1,
                "enabled": True,
            },
        ],
    }
    # Output window is 0-20. Slice 15-20s (5s) -> source 115-120.
    derived, offset = slice_timeline_by_output_range(timeline, output_start=15.0, duration=5.0)
    assert offset == 15.0
    assert len(derived["clips"]) == 1
    clip = derived["clips"][0]
    assert clip["source_start"] == pytest.approx(115.0)
    assert clip["source_end"] == pytest.approx(120.0)
    assert clip["duration"] == pytest.approx(5.0)
    assert clip["output_start"] == pytest.approx(0.0)
    assert clip["output_end"] == pytest.approx(5.0)
    assert derived["actual_duration"] == pytest.approx(5.0)


def test_slice_timeline_rejects_non_positive_duration():
    timeline = {
        "version": 2,
        "clips": [
            {"id": "c1", "source_start": 0.0, "source_end": 10.0, "enabled": True},
        ],
    }
    with pytest.raises(RenderError, match="must be positive"):
        slice_timeline_by_output_range(timeline, output_start=0.0, duration=0.0)
    with pytest.raises(RenderError, match="must be positive"):
        slice_timeline_by_output_range(timeline, output_start=0.0, duration=-5.0)


def test_slice_timeline_absolute_mode_sorts_by_output_start():
    """Clips listed out of order but carrying absolute output coordinates are
    sorted by output_start before slicing."""
    timeline = {
        "version": 2,
        "clips": [
            {
                "id": "b1",
                "source_start": 200.0,
                "source_end": 210.0,
                "output_start": 10.0,
                "output_end": 20.0,
                "order": 2,
                "enabled": True,
            },
            {
                "id": "b0",
                "source_start": 0.0,
                "source_end": 10.0,
                "output_start": 0.0,
                "output_end": 10.0,
                "order": 1,
                "enabled": True,
            },
        ],
    }
    derived, _ = slice_timeline_by_output_range(timeline, output_start=5.0, duration=10.0)
    assert len(derived["clips"]) == 2
    first, second = derived["clips"]
    # First slice piece must come from b0 (output 0-10).
    assert first["source_start"] == pytest.approx(5.0)
    assert second["source_start"] == pytest.approx(200.0)
    assert first["output_start"] == pytest.approx(0.0)
    assert second["output_start"] == pytest.approx(5.0)


def test_slice_timeline_absolute_mode_reports_output_gaps():
    """Gaps/overlaps in absolute output coordinates are surfaced as warnings,
    not silently swallowed."""
    timeline = {
        "version": 2,
        "clips": [
            {
                "id": "b0",
                "source_start": 0.0,
                "source_end": 10.0,
                "output_start": 0.0,
                "output_end": 10.0,
                "enabled": True,
            },
            {
                "id": "b1",
                "source_start": 50.0,
                "source_end": 60.0,
                "output_start": 12.0,  # 2s output gap after b0
                "output_end": 22.0,
                "enabled": True,
            },
        ],
    }
    derived, _ = slice_timeline_by_output_range(timeline, output_start=8.0, duration=8.0)
    assert derived["is_derived_preview"] is True
    assert len(derived["clips"]) == 2
    assert any("gap" in w for w in derived["warnings"])


def test_slice_timeline_warns_when_output_window_exceeds_source_span():
    timeline = {
        "version": 2,
        "clips": [
            {
                "id": "b0",
                "source_start": 0.0,
                "source_end": 5.0,    # source span 5s
                "output_start": 0.0,
                "output_end": 20.0,   # output window 20s (malformed)
                "enabled": True,
            },
        ],
    }
    derived, _ = slice_timeline_by_output_range(timeline, output_start=18.0, duration=2.0)
    clip = derived["clips"][0]
    # Clamped: cannot read past source end at 5s.
    assert clip["source_end"] <= 5.0 + 1e-6
    assert clip["duration"] == pytest.approx(2.0)
    assert any("exceeds" in w for w in derived["warnings"])
