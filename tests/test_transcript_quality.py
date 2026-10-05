"""Tests for pipeline.transcript_quality (Phase 1)."""

from pipeline.transcript_quality import filter_transcript, score_segment


def test_good_segment_kept():
    seg = {"start": 0.0, "end": 5.0, "text": "Hôm nay chúng ta sẽ tìm hiểu về máy ảnh mới."}
    out = score_segment(dict(seg))
    assert out["quality"]["score"] > 0.7
    assert not out["quality"]["is_hallucination"]


def test_repeated_word_hallucination_dropped():
    seg = {"start": 0.0, "end": 4.0, "text": "của của của của"}
    out = score_segment(dict(seg))
    assert out["quality"]["is_hallucination"]
    kept, meta = filter_transcript([seg])
    assert len(kept) == 0
    assert meta["dropped_segments"] == 1


def test_music_placeholder_dropped():
    seg = {"start": 0.0, "end": 3.0, "text": "âm nhạc"}
    kept, meta = filter_transcript([seg])
    assert len(kept) == 0


def test_empty_transcript_meta():
    kept, meta = filter_transcript([])
    assert kept == []
    assert meta["label"] == "poor"


def test_mixed_transcript_labels():
    segs = [
        {"start": 0.0, "end": 5.0, "text": "Đây là câu nói rõ ràng có dấu chấm."},
        {"start": 5.0, "end": 9.0, "text": "của của của của"},
        {"start": 9.0, "end": 14.0, "text": "Tiếp theo chúng ta xem phần thứ hai nhé."},
    ]
    kept, meta = filter_transcript(segs)
    assert len(kept) == 2
    assert meta["total_segments"] == 3
    assert meta["dropped_segments"] == 1
    # Very little total speech → 'poor' regardless of per-segment quality.
    assert meta["label"] == "poor"
    assert meta["average_quality"] > 0.7
