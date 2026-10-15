"""Tests for the block-based script workflow (Phase 8 / enhance_1.md audit Phase B)."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    ContentBlock,
    MediaInfo,
    ReviewInfo,
)
from core.exceptions import AnalysisError, ScriptNotApprovedError
from services.media_analysis_service import MediaAnalysisService
from services.script_service import ScriptService


def _doc(project_id: str = "proj_bs", two_blocks: bool = True) -> AnalysisDocument:
    """Build a minimal two-block analysis document."""
    media = MediaInfo(
        duration_ms=20000,
        fps_num=30,
        fps_den=1,
        width=1920,
        height=1080,
    )
    blocks = [
        ContentBlock(
            block_id="block_001",
            start_ms=0,
            end_ms=5000,
            source_start_ms=0,
            source_end_ms=5000,
            transcript="xin chao moi nguoi",
            script_draft="Xin chào mọi người!",
        ),
    ]
    if two_blocks:
        blocks.append(
            ContentBlock(
                block_id="block_002",
                start_ms=5000,
                end_ms=10000,
                source_start_ms=5000,
                source_end_ms=10000,
                transcript="hom nay troi dep",
                script_draft="Hôm nay trời đẹp quá.",
            )
        )
    return AnalysisDocument(
        schema_version=1,
        analysis_id="bs-test",
        project_id=project_id,
        media=media,
        speakers=[],
        shots=[],
        transcript_segments=[],
        audio_events=[],
        content_blocks=blocks,
        review=ReviewInfo(),
        metadata={},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def svc(tmp_path):
    svc = ScriptService()
    svc._media_service = MediaAnalysisService(projects_dir=tmp_path)
    return svc


@pytest.fixture
def saved_doc(svc, tmp_path):
    doc = _doc()
    svc._media_service.save_document(doc)
    return doc


def _patch_chat_text(text="Draft narration text"):
    return patch(
        "pipeline.llm_client.chat_text",
        return_value=text,
    )


class TestGenerateBlockDraft:
    def test_draft_only_updates_draft(self, svc, saved_doc):
        with _patch_chat_text("AI draft v2"):
            draft = svc.generate_block_draft("proj_bs", "block_001", instructions="sáng hơn")
        assert draft == "AI draft v2"
        blk = svc._media_service.load_existing_document("proj_bs").get_block("block_001")
        assert blk.script_draft == "AI draft v2"
        assert blk.script_final is None
        assert blk.human_verified is False
        # Timecodes untouched
        assert blk.start_ms == 0 and blk.end_ms == 5000

    def test_draft_unknown_block_raises(self, svc, saved_doc):
        with _patch_chat_text("x"):
            with pytest.raises(AnalysisError, match="not found"):
                svc.generate_block_draft("proj_bs", "nope")

    def test_draft_requires_document(self, svc):
        with pytest.raises(AnalysisError):
            svc.generate_block_draft("no_doc_proj", "block_001")


class TestSaveBlockScript:
    def test_save_final(self, svc, saved_doc):
        doc = svc.save_block_script("proj_bs", "block_001", "Lời đọc cuối cùng")
        blk = doc.get_block("block_001")
        assert blk.script_final == "Lời đọc cuối cùng"
        assert blk.start_ms == 0 and blk.end_ms == 5000

    def test_save_empty_rejected(self, svc, saved_doc):
        with pytest.raises(AnalysisError, match="empty"):
            svc.save_block_script("proj_bs", "block_001", "   ")

    def test_save_too_long_rejected(self, svc, saved_doc):
        # block_001 window = 5000ms → 14*5=70 chars limit, x2 = 140
        with pytest.raises(AnalysisError, match="too long"):
            svc.save_block_script("proj_bs", "block_001", "x" * 500)

    def test_edit_one_block_does_not_touch_other(self, svc, saved_doc):
        svc.save_block_script("proj_bs", "block_001", "A final")
        doc = svc.save_block_script("proj_bs", "block_002", "B final")
        assert doc.get_block("block_001").script_final == "A final"
        assert doc.get_block("block_002").script_final == "B final"

    def test_changing_final_resets_verified(self, svc, saved_doc):
        svc.save_block_script("proj_bs", "block_001", "first")
        # simulate verification
        doc = svc._media_service.load_existing_document("proj_bs")
        doc.get_block("block_001").human_verified = True
        doc.review.verified_block_ids.append("block_001")
        svc._media_service.save_document(doc, keep_version=False)
        # Now edit → verified must reset
        doc = svc.save_block_script("proj_bs", "block_001", "second")
        blk = doc.get_block("block_001")
        assert blk.human_verified is False
        assert "block_001" not in doc.review.verified_block_ids


class TestApproveReviewedBlocks:
    def test_draft_only_cannot_approve(self, svc, saved_doc):
        # has drafts but no final → reject
        with pytest.raises(AnalysisError, match="Missing script_final"):
            svc.approve_reviewed_blocks("proj_bs")

    def test_unverified_block_rejected(self, svc, saved_doc):
        svc.save_block_script("proj_bs", "block_001", "Final A")
        svc.save_block_script("proj_bs", "block_002", "Final B")
        # Not verified → reject
        with pytest.raises(AnalysisError, match="human_verified"):
            svc.approve_reviewed_blocks("proj_bs")

    def test_missing_final_on_required_block_rejected(self, svc, saved_doc):
        # block_001 has final, block_002 doesn't; both have transcripts
        svc.save_block_script("proj_bs", "block_001", "Final A")
        with pytest.raises(AnalysisError, match="block_002"):
            svc.approve_reviewed_blocks("proj_bs")

    def test_overlapping_narration_rejected(self, svc, tmp_path, monkeypatch):
        """Overlap gate: two final-script blocks whose windows overlap are rejected.

        The schema validator already rejects overlapping script_final blocks at
        persistence time, so the service-level sweep is defense-in-depth. Inject
        an invalid in-memory document (bypassing validators via model_construct)
        to exercise the service gate directly.
        """
        from core.analysis_schemas import ReviewStatus
        base = _doc()
        b0, b1 = base.content_blocks
        b0 = b0.model_copy(update={
            "script_final": "A", "end_ms": 7000, "source_end_ms": 7000,
            "human_verified": True,
        })
        b1 = b1.model_copy(update={"script_final": "B", "human_verified": True})
        overlap_doc = base.model_construct(
            content_blocks=[b0, b1],
            review=base.review.model_copy(update={"status": ReviewStatus.DRAFT}),
        )
        monkeypatch.setattr(
            svc._media_service, "get_current_analysis", lambda pid: overlap_doc
        )
        with pytest.raises(AnalysisError, match="overlap"):
            svc.approve_reviewed_blocks("proj_bs")

    def test_successful_approve(self, svc, saved_doc):
        svc.save_block_script("proj_bs", "block_001", "Final A")
        svc.save_block_script("proj_bs", "block_002", "Final B")
        # mark verified directly in service (simulating UI verify action)
        for bid in ("block_001", "block_002"):
            svc._media_service.update_block_script(
                "proj_bs", bid, svc._media_service.load_existing_document(
                    "proj_bs").get_block(bid).script_final
            )
        approved = svc.approve_reviewed_blocks("proj_bs")
        assert str(approved.review.status.value) == "approved"
        assert approved.review.approved_at is not None

    def test_diagnostics_list_problem_blocks(self, svc, saved_doc):
        with pytest.raises(AnalysisError) as exc_info:
            svc.approve_reviewed_blocks("proj_bs")
        msg = str(exc_info.value)
        assert "block_001" in msg and "block_002" in msg


class TestBuildScriptFromAnalysis:
    def test_not_approved_raises(self, svc, saved_doc):
        with pytest.raises(ScriptNotApprovedError):
            svc.build_script_from_analysis("proj_bs")

    def test_timeline_keeps_block_mapping(self, svc, saved_doc):
        svc.save_block_script("proj_bs", "block_001", "Final A")
        svc.save_block_script("proj_bs", "block_002", "Final B")
        for bid in ("block_001", "block_002"):
            svc._media_service.update_block_script(
                "proj_bs", bid, svc._media_service.load_existing_document(
                    "proj_bs").get_block(bid).script_final
            )
        svc.approve_reviewed_blocks("proj_bs")

        timeline = svc.build_script_from_analysis("proj_bs")
        assert timeline["source"] == "block_analysis"
        ids = [item["block_id"] for item in timeline["beats"]]
        assert ids == ["block_001", "block_002"]
        for item in timeline["beats"]:
            assert isinstance(item["start_ms"], int)
            assert isinstance(item["end_ms"], int)
        assert timeline["beats"][0]["text"] == "Final A"
        assert timeline["beats"][0]["narration"] is True

    def test_non_narration_block_preserved(self, svc, tmp_path):
        doc = _doc(two_blocks=False)
        # Add a visual-only block (no transcript, no draft) via construct
        visual = ContentBlock.model_construct(
            block_id="block_v", start_ms=10000, end_ms=15000,
            source_start_ms=10000, source_end_ms=15000,
            transcript="", script_draft="", script_final=None,
            human_verified=True, description="chỉ hình ảnh",
        )
        doc.content_blocks.append(visual)
        svc._media_service.save_document(doc, keep_version=False)
        svc.save_block_script("proj_bs", "block_001", "Final A")
        svc._media_service.update_block_script("proj_bs", "block_001", "Final A")
        # visually verify the visual-only block so it doesn't block approve
        doc = svc._media_service.load_existing_document("proj_bs")
        doc.review.verified_block_ids.append("block_v")
        svc._media_service.save_document(doc, keep_version=False)
        svc.approve_reviewed_blocks("proj_bs")
        timeline = svc.build_script_from_analysis("proj_bs")
        ids = [item["block_id"] for item in timeline["beats"]]
        assert "block_v" in ids
        visual_item = next(i for i in timeline["beats"] if i["block_id"] == "block_v")
        assert visual_item["narration"] is False
