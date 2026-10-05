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
