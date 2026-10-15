"""Tests for pipeline.audio_timeline (Phase 10)."""

import json
from pathlib import Path

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    AudioDelivery,
    AudioDeliveryStatus,
    ContentBlock,
    MediaInfo,
    ReviewStatus,
)
from pipeline.audio_timeline import (
    AudioTimelineError,
    build_audio_timeline,
    check_render_readiness,
)


def _make_doc(tmp_path: Path, blocks, review_status=ReviewStatus.APPROVED) -> AnalysisDocument:
    media = MediaInfo(
        duration_ms=20000,
        fps_num=30,
        fps_den=1,
        width=1920,
        height=1080,
        audio_sample_rate=44100,
        audio_channels=2,
    )
    doc = AnalysisDocument(
        schema_version=1,
        analysis_id="test-analysis",
        project_id="proj_test",
        media=media,
        speakers=[],
        shots=[],
        transcript_segments=[],
        audio_events=[],
        content_blocks=blocks,
        review={"status": review_status},
        metadata={},
    )
    proj_dir = tmp_path / "proj_test" / "block_analysis"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "document.json").write_text(
        json.dumps(doc.model_dump(mode="json")), encoding="utf-8"
    )
    return doc


def _block(block_id, start, end, verified=True, script="nội dung", delivery=None):
    return ContentBlock(
        block_id=block_id,
        start_ms=start,
        end_ms=end,
        source_start_ms=start,
        source_end_ms=end,
        transcript="transcript",
        script_final=script,
        human_verified=verified,
        audio_delivery=delivery,
    )


def _delivery(approved=True, dur=5000):
    return AudioDelivery(
        file_path="fake.wav",
        actual_duration_ms=dur,
        expected_start_ms=0,
        expected_end_ms=5000,
        status=AudioDeliveryStatus.APPROVED if approved else AudioDeliveryStatus.UPLOADED,
        approved=approved,
    )


class TestBuildAudioTimeline:
    def test_contiguous_output_windows(self, tmp_path):
        doc = _make_doc(tmp_path, [
            _block("b1", 0, 5000),
            _block("b2", 8000, 12000),  # source gap should not affect output
        ])
        tl = build_audio_timeline(doc)
        assert tl["block_count"] == 2
        c1, c2 = tl["clips"]
        assert c1["output_start"] == 0.0
        assert c1["output_end"] == 5.0
        assert c2["output_start"] == 5.0  # contiguous, no stretch over gap
        assert c2["output_end"] == 9.0
        assert tl["total_duration_seconds"] == 9.0

    def test_unverified_blocks_excluded(self, tmp_path):
        doc = _make_doc(tmp_path, [
            _block("b1", 0, 5000, verified=True),
            _block("b2", 5000, 10000, verified=False),
        ])
        tl = build_audio_timeline(doc)
        assert tl["block_count"] == 1
        assert tl["clips"][0]["block_id"] == "b1"

    def test_no_eligible_blocks_raises(self, tmp_path):
        doc = _make_doc(tmp_path, [_block("b1", 0, 5000, verified=False)])
        with pytest.raises(AudioTimelineError):
            build_audio_timeline(doc)

    def test_unapproved_audio_blocks_raises(self, tmp_path):
        doc = _make_doc(tmp_path, [
            _block("b1", 0, 5000, delivery=_delivery(approved=False)),
        ])
        with pytest.raises(AudioTimelineError, match="chưa được duyệt"):
            build_audio_timeline(doc)

    def test_audio_overlays_present_when_delivered(self, tmp_path):
        doc = _make_doc(tmp_path, [
            _block("b1", 0, 5000, delivery=_delivery(approved=True, dur=5200)),
            _block("b2", 5000, 10000),  # no delivery → source audio fallback
        ])
        tl = build_audio_timeline(doc)
        assert len(tl["audio_overlays"]) == 1
        ov = tl["audio_overlays"][0]
        assert ov["block_id"] == "b1"
        assert ov["delta_ms"] == 200
        assert ov["fit_mode"] == "preserve_speed"

    def test_clips_carry_script(self, tmp_path):
        doc = _make_doc(tmp_path, [_block("b1", 0, 5000, script="lời bình mẫu")])
        tl = build_audio_timeline(doc)
        assert tl["clips"][0]["script"] == "lời bình mẫu"


class TestCheckRenderReadiness:
    def test_ready_document(self, tmp_path):
        doc = _make_doc(tmp_path, [_block("b1", 0, 5000)])
        res = check_render_readiness(doc)
        assert res["ready"] is True
        assert res["reasons"] == []
        assert res["eligible_blocks"] == 1

    def test_unverified_blocks_listed(self, tmp_path):
        doc = _make_doc(tmp_path, [
            _block("b1", 0, 5000, verified=False),
            _block("b2", 5000, 10000, verified=False),
        ])
        res = check_render_readiness(doc)
        assert res["ready"] is False
        assert any("2 khối chưa duyệt" in r for r in res["reasons"])
        assert res["eligible_blocks"] == 0
