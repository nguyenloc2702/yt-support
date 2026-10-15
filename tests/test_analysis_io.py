"""Tests for AnalysisDocument JSON export/import round-trip (Phase 11)."""

import json
from pathlib import Path

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    ContentBlock,
    MediaInfo,
)
from core.exceptions import AnalysisError
from services.media_analysis_service import MediaAnalysisService


@pytest.fixture
def service(tmp_path: Path) -> MediaAnalysisService:
    return MediaAnalysisService(projects_dir=tmp_path)


@pytest.fixture
def persisted_doc(tmp_path: Path) -> AnalysisDocument:
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
        transcript="xin chào",
        script_final="nội dung duyệt",
        human_verified=True,
    )
    doc = AnalysisDocument(
        schema_version=1,
        analysis_id="io-test-id",
        project_id="proj_io",
        media=media,
        speakers=[],
        shots=[],
        transcript_segments=[],
        audio_events=[],
        content_blocks=[block],
        review={"status": "approved"},
        metadata={},
    )
    proj_dir = tmp_path / "proj_io" / "block_analysis"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "document.json").write_text(
        json.dumps(doc.model_dump(mode="json")), encoding="utf-8"
    )
    return doc


class TestExportImportRoundTrip:
    def test_export_produces_valid_json(self, service, persisted_doc):
        raw = service.export_document_json("proj_io")
        data = json.loads(raw)
        assert data["analysis_id"] == "io-test-id"
        assert data["content_blocks"][0]["block_id"] == "b1"

    def test_import_restores_document(self, service, tmp_path, persisted_doc):
        raw = service.export_document_json("proj_io")
        # Simulate moving to another project dir
        other_dir = tmp_path / "proj_target"
        (other_dir / "block_analysis").mkdir(parents=True)
        svc2 = MediaAnalysisService(projects_dir=tmp_path)
        imported = svc2.import_document_json(
            "proj_target", raw, allow_foreign_project=True
        )
        assert imported.project_id == "proj_target"  # forced to target project
        assert len(imported.content_blocks) == 1
        assert imported.content_blocks[0].script_final == "nội dung duyệt"
        # persisted to disk under the new project
        assert svc2.load_existing_document("proj_target") is not None

    def test_import_rejects_invalid_json(self, service):
        with pytest.raises(AnalysisError, match="Invalid JSON"):
            service.import_document_json("proj_io", "{not valid json")

    def test_import_rejects_schema_violation(self, service):
        bad = json.dumps({"schema_version": 1})  # missing required fields
        with pytest.raises(AnalysisError, match="Schema validation failed"):
            service.import_document_json("proj_io", bad)

    def test_export_without_document_raises(self, service):
        with pytest.raises(AnalysisError, match="No saved analysis"):
            service.export_document_json("proj_missing")
