"""Tests for document versioning (Phase 12, optional)."""

import json
import time
from pathlib import Path

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    ContentBlock,
    MediaInfo,
)
from core.exceptions import AnalysisError
from services.media_analysis_service import MediaAnalysisService


def _doc(project_id="proj_v", marker="v0") -> AnalysisDocument:
    media = MediaInfo(
        duration_ms=10000,
        fps_num=30,
        fps_den=1,
        width=1920,
        height=1080,
    )
    block = ContentBlock(
        block_id="b1",
        start_ms=0,
        end_ms=5000,
        source_start_ms=0,
        source_end_ms=5000,
        transcript="t",
        script_final=marker,
        human_verified=True,
    )
    return AnalysisDocument(
        schema_version=1,
        analysis_id="ver-test",
        project_id=project_id,
        media=media,
        speakers=[],
        shots=[],
        transcript_segments=[],
        audio_events=[],
        content_blocks=[block],
        review={"status": "draft"},
        metadata={},
    )


class TestDocumentVersioning:
    def test_save_creates_version_snapshot(self, tmp_path):
        svc = MediaAnalysisService(projects_dir=tmp_path)
        svc.save_document(_doc(marker="first"), keep_version=False)
        # Second save snapshots the first state
        time.sleep(0.01)
        svc.save_document(_doc(marker="second"))
        versions = svc.list_document_versions("proj_v")
        assert len(versions) == 1
        # Snapshot holds the "first" content
        snap = json.loads(Path(versions[0]["path"]).read_text(encoding="utf-8"))
        assert snap["content_blocks"][0]["script_final"] == "first"
        # Current holds "second"
        cur = svc.load_existing_document("proj_v")
        assert cur.content_blocks[0].script_final == "second"

    def test_restore_previous_version(self, tmp_path):
        svc = MediaAnalysisService(projects_dir=tmp_path)
        svc.save_document(_doc(marker="original"), keep_version=False)
        time.sleep(0.01)
        svc.save_document(_doc(marker="changed"))
        versions = svc.list_document_versions("proj_v")
        assert len(versions) == 1
        restored = svc.restore_document_version("proj_v", versions[0]["name"])
        assert restored.content_blocks[0].script_final == "original"
        assert svc.load_existing_document("proj_v").content_blocks[0].script_final == "original"

    def test_version_cap(self, tmp_path):
        svc = MediaAnalysisService(projects_dir=tmp_path)
        svc.save_document(_doc(marker="base"), keep_version=False)
        for i in range(svc.MAX_VERSIONS + 5):
            time.sleep(0.01)
            svc.save_document(_doc(marker=f"gen{i}"))
        versions = svc.list_document_versions("proj_v")
        assert len(versions) == svc.MAX_VERSIONS

    def test_restore_unknown_version_raises(self, tmp_path):
        svc = MediaAnalysisService(projects_dir=tmp_path)
        with pytest.raises(AnalysisError, match="Không tìm thấy phiên bản"):
            svc.restore_document_version("proj_v", "document_0000.json")

    def test_update_block_script_creates_version(self, tmp_path):
        svc = MediaAnalysisService(projects_dir=tmp_path)
        svc.save_document(_doc(marker="before-edit"), keep_version=False)
        svc.update_block_script("proj_v", "b1", script_final="after-edit")
        versions = svc.list_document_versions("proj_v")
        assert len(versions) == 1
        assert svc.load_existing_document("proj_v").content_blocks[0].script_final == "after-edit"
