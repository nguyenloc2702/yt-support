"""Tests for pipeline.semantic_summary snapping/validation (Phase 4).

LLM calls are NOT tested here (integration concern); only the pure
validation/snap logic via _validate_and_snap.
"""

import pytest

from pipeline.llm_client import LLMResponseError
from pipeline.semantic_summary import _validate_and_snap


SEGMENTS = [
    {"start": 0.0, "end": 10.0, "text": "a"},
    {"start": 10.0, "end": 20.0, "text": "b"},
    {"start": 20.0, "end": 30.0, "text": "c"},
]


def test_snaps_to_boundary():
    result = {
        "title": "T",
        "summary": "S",
        "chapters": [],
        "candidate_clips": [
            {"start": 9.5, "end": 20.4, "reason": "r", "chapter_index": 0, "confidence": 0.9}
        ],
    }
    out = _validate_and_snap(result, SEGMENTS, None)
    clip = out["candidate_clips"][0]
    assert clip["start"] == 10.0  # snapped to boundary 10.0
    assert clip["end"] == 20.0


def test_out_of_bounds_clamped():
    result = {
        "title": "T",
        "summary": "S",
        "chapters": [],
        "candidate_clips": [
            {"start": -5.0, "end": 999.0, "reason": "r", "chapter_index": 0, "confidence": 5}
        ],
    }
    out = _validate_and_snap(result, SEGMENTS, None)
    clip = out["candidate_clips"][0]
    assert clip["start"] >= 0.0
    assert clip["end"] <= 30.0
    assert clip["confidence"] <= 1.0


def test_end_at_least_start_plus_one():
    result = {
        "title": "T",
        "summary": "S",
        "chapters": [],
        "candidate_clips": [
            {"start": 10.0, "end": 10.0, "reason": "r", "chapter_index": 0, "confidence": 0.5}
        ],
    }
    out = _validate_and_snap(result, SEGMENTS, None)
    assert out["candidate_clips"][0]["end"] >= 11.0


def test_build_semantic_summary_raises_on_empty():
    from pipeline.semantic_summary import build_semantic_summary

    with pytest.raises(LLMResponseError):
        build_semantic_summary([], topic="", keywords=[])
