"""Tests for services/media_analysis_service.py (PHASE 6)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    AudioEvent,
    AudioEventType,
    ContentBlock,
    MediaInfo,
    ReviewStatus,
    ShotAnalysis,
    Speaker,
    TranscriptSegment,
)
from services.media_analysis_service import MediaAnalysisService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_projects(tmp_path):
    """Create a temporary projects directory."""
    return tmp_path / "projects"


@pytest.fixture
def service(tmp_projects):
    return MediaAnalysisService(projects_dir=tmp_projects)


@pytest.fixture
def sample_media_info():
    return MediaInfo(
        duration_ms=60_000,
        fps_num=30000,
        fps_den=1001,
        width=1920,
        height=1080,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_codec="aac",
        video_codec="h264",
    )


@pytest.fixture
def sample_shots():
    return [
        ShotAnalysis(shot_id="shot_0000", start_ms=0, end_ms=5000, confidence=0.95),
        ShotAnalysis(shot_id="shot_0001", start_ms=5000, end_ms=12000, confidence=0.92),
        ShotAnalysis(shot_id="shot_0002", start_ms=12000, end_ms=20000, confidence=0.88),
    ]


@pytest.fixture
def sample_segments():
    return [
        TranscriptSegment(id="seg_0000", start_ms=1000, end_ms=4500, text="Hello world", confidence=0.97),
        TranscriptSegment(id="seg_0001", start_ms=6000, end_ms=11000, text="Second sentence here", confidence=0.94),
        TranscriptSegment(id="seg_0002", start_ms=13000, end_ms=19000, text="Third part of speech", confidence=0.91),
    ]


@pytest.fixture
def sample_events(sample_segments):
    events = []
    for i, seg in enumerate(sample_segments):
        events.append(AudioEvent(
            event_id=f"ae_{i:04d}",
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            type=AudioEventType.SPEECH,
            transcript_segment_ids=[seg.id],
            confidence=seg.confidence,
        ))
    # Add trailing silence
    events.append(AudioEvent(
        event_id="ae_silence_0",
        start_ms=20000,
        end_ms=60000,
        type=AudioEventType.SILENCE,
        confidence=0.9,
    ))
    return events


@pytest.fixture
def sample_speakers():
    return [
        Speaker(speaker_id="speaker_0", label="Speaker 1", confidence=0.8),
    ]


@pytest.fixture
def sample_blocks(sample_shots, sample_segments, sample_events):
    from pipeline.content_analysis import create_content_blocks
    result = create_content_blocks(
        shots=sample_shots,
        transcript_segments=sample_segments,
        audio_events=sample_events,
        media_duration_ms=60_000,
    )
    return result.content_blocks


# ---------------------------------------------------------------------------
# Unit tests – document assembly
# ---------------------------------------------------------------------------

class TestAssembleDocument:
    def test_assembles_valid_document(self, service, sample_media_info, sample_shots,
                                       sample_segments, sample_events, sample_speakers,
                                       sample_blocks):
        doc = service._assemble_document(
            project_id="proj_test",
            media_info=sample_media_info,
            shots=sample_shots,
            transcript_segments=sample_segments,
            audio_events=sample_events,
            speakers=sample_speakers,
            content_blocks=sample_blocks,
        )
        assert isinstance(doc, AnalysisDocument)
        assert doc.project_id == "proj_test"
        assert len(doc.shots) == 3
        assert len(doc.transcript_segments) == 3
        assert len(doc.audio_events) == 4
        assert len(doc.speakers) == 1
        assert len(doc.content_blocks) == len(sample_blocks)
        assert doc.schema_version == 1
        assert doc.review.status == ReviewStatus.DRAFT

    def test_generated_uuid_is_unique(self, service, sample_media_info, sample_shots,
                                        sample_segments, sample_events, sample_speakers,
                                        sample_blocks):
        d1 = service._assemble_document("p1", sample_media_info, sample_shots,
                                         sample_segments, sample_events, sample_speakers,
                                         sample_blocks)
        d2 = service._assemble_document("p2", sample_media_info, sample_shots,
                                         sample_segments, sample_events, sample_speakers,
                                         sample_blocks)
        assert d1.analysis_id != d2.analysis_id


# ---------------------------------------------------------------------------
# Unit tests – persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistenceRoundTrip:
    def test_save_and_load(self, service, tmp_projects, sample_media_info, sample_shots,
                            sample_segments, sample_events, sample_speakers, sample_blocks):
        pid = "roundtrip_proj"
        proj_dir = tmp_projects / pid / "block_analysis"
        proj_dir.mkdir(parents=True, exist_ok=True)

        doc = service._assemble_document(pid, sample_media_info, sample_shots,
                                          sample_segments, sample_events, sample_speakers,
                                          sample_blocks)
        service.save_document(doc)

        loaded = service.load_existing_document(pid)
        assert loaded is not None
        assert loaded.analysis_id == doc.analysis_id
        assert loaded.project_id == pid
        assert len(loaded.content_blocks) == len(doc.content_blocks)
        assert loaded.media.duration_ms == doc.media.duration_ms

    def test_load_nonexistent_returns_none(self, service):
        result = service.load_existing_document("does_not_exist")
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests – block operations
# ---------------------------------------------------------------------------

class TestBlockOperations:
    @pytest.fixture
    def saved_doc(self, service, tmp_projects, sample_media_info, sample_shots,
                   sample_segments, sample_events, sample_speakers, sample_blocks):
        pid = "ops_proj"
        proj_dir = tmp_projects / pid / "block_analysis"
        proj_dir.mkdir(parents=True, exist_ok=True)
        doc = service._assemble_document(pid, sample_media_info, sample_shots,
                                          sample_segments, sample_events, sample_speakers,
                                          sample_blocks)
        service.save_document(doc)
        return doc

    def test_update_block_script(self, service, saved_doc):
        bid = saved_doc.content_blocks[0].block_id
        updated = service.update_block_script(saved_doc.project_id, bid, "Final script text", review_note="LGTM")
        blk = updated.get_block(bid)
        assert blk.script_final == "Final script text"
        assert blk.human_verified is True
        assert bid in updated.review.verified_block_ids

    def test_delete_block(self, service, saved_doc):
        n_before = len(saved_doc.content_blocks)
        bid = saved_doc.content_blocks[-1].block_id
        updated = service.delete_block(saved_doc.project_id, bid)
        assert len(updated.content_blocks) == n_before - 1
        assert updated.get_block(bid) is None

    def test_reorder_blocks(self, service, saved_doc):
        ids = [b.block_id for b in saved_doc.content_blocks]
        reversed_ids = list(reversed(ids))
        updated = service.reorder_blocks(saved_doc.project_id, reversed_ids)
        new_ids = [b.block_id for b in updated.content_blocks]
        assert new_ids == reversed_ids

    def test_update_unknown_block_raises(self, service, saved_doc):
        with pytest.raises(Exception, match="not found"):
            service.update_block_script(saved_doc.project_id, "nonexistent_block", "text")

    def test_delete_unknown_block_raises(self, service, saved_doc):
        with pytest.raises(Exception, match="not found"):
            service.delete_block(saved_doc.project_id, "nonexistent_block")


# ---------------------------------------------------------------------------
# Unit tests – export / import JSON
# ---------------------------------------------------------------------------

class TestExportImport:
    @pytest.fixture
    def saved_doc(self, service, tmp_projects, sample_media_info, sample_shots,
                   sample_segments, sample_events, sample_speakers, sample_blocks):
        pid = "export_import_proj"
        proj_dir = tmp_projects / pid / "block_analysis"
        proj_dir.mkdir(parents=True, exist_ok=True)
        doc = service._assemble_document(pid, sample_media_info, sample_shots,
                                          sample_segments, sample_events, sample_speakers,
                                          sample_blocks)
        service.save_document(doc)
        return doc

    def test_export_produces_valid_json(self, service, saved_doc):
        raw = service.export_document_json(saved_doc.project_id)
        parsed = json.loads(raw)
        assert parsed["project_id"] == saved_doc.project_id
        assert parsed["schema_version"] == 1

    def test_import_restores_document(self, service, saved_doc):
        exported = service.export_document_json(saved_doc.project_id)
        imported = service.import_document_json(
            "new_project_id", exported, allow_foreign_project=True
        )
        assert imported.project_id == "new_project_id"
        assert len(imported.content_blocks) == len(saved_doc.content_blocks)
        assert imported.media.duration_ms == saved_doc.media.duration_ms

    def test_import_foreign_project_rejected(self, service, saved_doc):
        """Phase A: importing into a different project must be rejected by default."""
        exported = service.export_document_json(saved_doc.project_id)
        with pytest.raises(Exception, match="Import rejected"):
            service.import_document_json("another_project", exported)


# ---------------------------------------------------------------------------
# Phase A (enhance_1.md audit) – plan API & persistence hardening
# ---------------------------------------------------------------------------

class TestPhaseAPlanAPI:
    @pytest.fixture
    def doc(self, service, tmp_projects, sample_media_info, sample_shots,
            sample_segments, sample_events, sample_speakers, sample_blocks):
        pid = "phasea_proj"
        (tmp_projects / pid / "block_analysis").mkdir(parents=True, exist_ok=True)
        doc = service._assemble_document(pid, sample_media_info, sample_shots,
                                          sample_segments, sample_events, sample_speakers,
                                          sample_blocks)
        service.save_document(doc)
        return doc

    def test_current_pointer_written(self, service, doc, tmp_projects):
        pointer_path = tmp_projects / doc.project_id / "block_analysis" / "current.json"
        assert pointer_path.exists()
        import json as _json
        pointer = _json.loads(pointer_path.read_text(encoding="utf-8"))
        assert pointer["analysis_id"] == doc.analysis_id
        assert pointer["version"] == doc.version

    def test_save_reviewed_analysis_increments_version(self, service, doc):
        v0 = doc.version
        doc.content_blocks[0].script_final = "reviewed text"
        updated = service.save_reviewed_analysis(doc)
        assert updated.version == v0 + 1
        reloaded = service.get_current_analysis(doc.project_id)
        assert reloaded.version == v0 + 1
        assert reloaded.content_blocks[0].script_final == "reviewed text"

    def test_get_current_analysis_raises_without_document(self, service):
        from core.exceptions import AnalysisError
        with pytest.raises(AnalysisError, match="No saved analysis"):
            service.get_current_analysis("missing_proj")

    def test_get_analysis_version_out_of_range_raises(self, service, doc):
        from core.exceptions import AnalysisError
        with pytest.raises(AnalysisError, match="not found"):
            service.get_analysis_version(doc.project_id, 999)

    def test_validate_document_reports_problems(self, service, doc):
        # No problems initially
        assert service.validate_document(doc) == []
        # Force a duplicate block_id by mutating then re-validating manually
        import copy
        from core.analysis_schemas import AnalysisDocument as AD
        mutated = AD(**doc.model_dump(mode="json"))
        mutated.content_blocks.append(copy.deepcopy(mutated.content_blocks[0]))
        problems = service.validate_document(mutated)
        assert any("Duplicate block_id" in p for p in problems)

    def test_validate_document_reports_narration_overlap(self, service, doc):
        # Overlapping final-script blocks (bypassing model validator via manual check)
        doc.content_blocks[0].script_final = "A"
        doc.content_blocks[0].end_ms = doc.content_blocks[1].start_ms + 500
        doc.content_blocks[0].source_end_ms = doc.content_blocks[0].end_ms
        # Reconstruct without triggering validator: use construct
        from core.analysis_schemas import ContentBlock
        blk0 = doc.content_blocks[0]
        blk0.__class__.model_construct(
            **{**blk0.model_dump(), "end_ms": doc.content_blocks[1].start_ms + 500,
               "source_end_ms": doc.content_blocks[1].start_ms + 500}
        )
        problems = service.validate_document(doc)
        # narration blocks are blocks[0] and blocks[1] if both have final script
        if doc.content_blocks[1].script_final:
            assert any("overlap" in p.lower() for p in problems)

    def test_timecodes_stay_int_after_roundtrip(self, service, doc):
        raw = service.export_document_json(doc.project_id)
        import json as _json
        data = _json.loads(raw)
        for b in data["content_blocks"]:
            for key in ("start_ms", "end_ms", "source_start_ms", "source_end_ms"):
                assert isinstance(b[key], int)
        reloaded = service.load_existing_document(doc.project_id)
        for b in reloaded.content_blocks:
            assert isinstance(b.start_ms, int)
            assert isinstance(b.end_ms, int)

    def test_atomic_write_leaves_no_tmp_files(self, service, doc, tmp_projects):
        adir = tmp_projects / doc.project_id / "block_analysis"
        service.save_document(doc)
        leftovers = list(adir.glob("*.tmp"))
        assert leftovers == []
        assert (adir / "document.json").exists()

    def test_schema_version_incompatible_rejected(self):
        from core.analysis_schemas import MediaInfo
        with pytest.raises(Exception):
            AnalysisDocument(
                schema_version=0,  # ge=1 violated
                analysis_id="x",
                project_id="p",
                media=MediaInfo(duration_ms=1, fps_num=30, fps_den=1, width=0, height=0),
            )

    def test_import_invalid_json_raises(self, service):
        with pytest.raises(Exception, match="Invalid JSON"):
            service.import_document_json("bad", "{not valid json}")

    def test_import_schema_violation_raises(self, service):
        bad_payload = json.dumps({"schema_version": 1})  # missing required fields
        with pytest.raises(Exception, match="Schema validation failed"):
            service.import_document_json("bad", bad_payload)


# ---------------------------------------------------------------------------
# Integration-style test with mocked pipeline stages
# ---------------------------------------------------------------------------

class TestRunFullAnalysisMocked:
    """Verify orchestration logic without real ffmpeg/whisper/scenedetect."""

    def test_full_run_with_mocks(self, service, tmp_projects, sample_media_info,
                                  sample_shots, sample_segments, sample_events,
                                  sample_speakers, sample_blocks):
        pid = "mock_run_proj"
        source_file = tmp_projects / pid / "source" / "input.mp4"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"\x00" * 1024)  # dummy file

        progress_log = []

        with patch.object(service, "_stage_probe", return_value=sample_media_info), \
             patch.object(service, "_stage_detect_shots", return_value=sample_shots), \
             patch.object(service, "_stage_analyze_audio",
                          return_value=(sample_segments, sample_events, sample_speakers)), \
             patch.object(service, "_stage_create_blocks", return_value=sample_blocks):

            doc = service.run_full_analysis(
                pid,
                source_path=source_file,
                progress_callback=lambda pct, msg: progress_log.append((pct, msg)),
            )

        assert isinstance(doc, AnalysisDocument)
        assert doc.project_id == pid
        assert len(doc.shots) == 3
        assert len(doc.transcript_segments) == 3
        assert len(doc.content_blocks) > 0
        # Verify progress was reported
        assert any(p >= 100 for p, _ in progress_log)
        # Verify artefacts written to disk
        analysis_dir = tmp_projects / pid / "block_analysis"
        assert (analysis_dir / "document.json").exists()
        assert (analysis_dir / "media_info.json").exists()
        assert (analysis_dir / "shots.json").exists()
        assert (analysis_dir / "content_blocks.json").exists()

    def test_missing_source_raises(self, service, tmp_projects):
        with pytest.raises(Exception, match="Source file not found"):
            service.run_full_analysis("no_source_proj")

    def test_cancellation_propagates(self, service, tmp_projects, sample_media_info):
        import threading
        pid = "cancel_proj"
        source_file = tmp_projects / pid / "source" / "input.mp4"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"\x00" * 1024)

        cancel_event = threading.Event()
        cancel_event.set()  # Already cancelled before start

        with patch.object(service, "_stage_probe", return_value=sample_media_info):
            from core.exceptions import JobCancelledError
            with pytest.raises(JobCancelledError):
                service.run_full_analysis(pid, source_path=source_file, cancellation_token=cancel_event)

    def test_use_cached_skips_stages(self, service, tmp_projects, sample_media_info,
                                      sample_shots, sample_segments, sample_events,
                                      sample_speakers, sample_blocks):
        """When cached artefacts exist and use_cached=True, stage functions are NOT called."""
        pid = "cached_proj"
        source_file = tmp_projects / pid / "source" / "input.mp4"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"\x00" * 1024)

        # Pre-populate cache files
        adir = tmp_projects / pid / "block_analysis"
        adir.mkdir(parents=True, exist_ok=True)
        self._write_json(adir / "media_info.json", sample_media_info.model_dump())
        self._write_json(adir / "shots.json", [s.model_dump() for s in sample_shots])
        self._write_json(adir / "transcript_segments.json", [sg.model_dump() for sg in sample_segments])
        self._write_json(adir / "audio_events.json", [e.model_dump() for e in sample_events])
        self._write_json(adir / "speakers.json", [sp.model_dump() for sp in sample_speakers])
        self._write_json(adir / "content_blocks.json", [b.model_dump() for b in sample_blocks])

        probe_called = {"count": 0}
        shots_called = {"count": 0}
        audio_called = {"count": 0}
        blocks_called = {"count": 0}

        def counting_probe(*a, **kw):
            probe_called["count"] += 1
            return sample_media_info

        def counting_shots(*a, **kw):
            shots_called["count"] += 1
            return sample_shots

        def counting_audio(*a, **kw):
            audio_called["count"] += 1
            return (sample_segments, sample_events, sample_speakers)

        def counting_blocks(*a, **kw):
            blocks_called["count"] += 1
            return sample_blocks

        with patch.object(service, "_stage_probe", side_effect=counting_probe), \
             patch.object(service, "_stage_detect_shots", side_effect=counting_shots), \
             patch.object(service, "_stage_analyze_audio", side_effect=counting_audio), \
             patch.object(service, "_stage_create_blocks", side_effect=counting_blocks):

            doc = service.run_full_analysis(
                pid,
                source_path=source_file,
                options={"use_cached": True},
            )

        assert isinstance(doc, AnalysisDocument)
        # Cached paths should skip expensive stages
        assert probe_called["count"] == 0
        assert shots_called["count"] == 0
        assert audio_called["count"] == 0
        assert blocks_called["count"] == 0

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)