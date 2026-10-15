"""Tests for the analysis schemas module."""

import json
from pathlib import Path

import pytest

from core.analysis_schemas import (
    AnalysisDocument,
    AudioDelivery,
    AudioDeliveryStatus,
    AudioEvent,
    AudioEventType,
    AudioFitMode,
    ContentBlock,
    DeliveryReport,
    MediaInfo,
    ReviewInfo,
    ReviewStatus,
    ShotAnalysis,
    Speaker,
    TimeRangeMs,
    TranscriptSegment,
    TranscriptWord,
)


@pytest.fixture
def sample_analysis_json():
    """Load the sample analysis JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "analysis_sample" / "analysis_expected.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestTimeRangeMs:
    """Tests for TimeRangeMs schema."""

    def test_valid_range(self):
        tr = TimeRangeMs(start_ms=100, end_ms=500)
        assert tr.duration_ms == 400

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="end_ms.*must be > start_ms"):
            TimeRangeMs(start_ms=500, end_ms=100)

    def test_zero_start(self):
        tr = TimeRangeMs(start_ms=0, end_ms=100)
        assert tr.duration_ms == 100


class TestMediaInfo:
    """Tests for MediaInfo schema."""

    def test_fps_calculation(self):
        media = MediaInfo(
            duration_ms=10000,
            fps_num=30000,
            fps_den=1001,
            width=1920,
            height=1080,
            audio_sample_rate=48000,
            audio_channels=2,
        )
        assert media.fps == pytest.approx(29.97002997, rel=1e-6)
        assert media.duration_seconds == 10.0


class TestShotAnalysis:
    """Tests for ShotAnalysis schema."""

    def test_valid_shot(self):
        shot = ShotAnalysis(
            shot_id="shot_001",
            start_ms=0,
            end_ms=2500,
            confidence=0.98,
            description="Test shot",
        )
        assert shot.duration_ms == 2500

    def test_invalid_shot_time_raises(self):
        with pytest.raises(ValueError, match="Shot end_ms must be > start_ms"):
            ShotAnalysis(shot_id="bad", start_ms=100, end_ms=50)


class TestTranscriptWord:
    """Tests for TranscriptWord schema."""

    def test_valid_word(self):
        word = TranscriptWord(text="hello", start_ms=100, end_ms=200, confidence=0.95)
        assert word.duration_ms == 100

    def test_invalid_word_time_raises(self):
        with pytest.raises(ValueError, match="Word end_ms must be > start_ms"):
            TranscriptWord(text="bad", start_ms=200, end_ms=100)


class TestTranscriptSegment:
    """Tests for TranscriptSegment schema."""

    def test_valid_segment(self):
        segment = TranscriptSegment(
            id="seg_001",
            start_ms=0,
            end_ms=2500,
            text="Hello world",
            confidence=0.95,
        )
        assert segment.duration_ms == 2500

    def test_segment_with_words(self):
        words = [
            TranscriptWord(text="Hello", start_ms=0, end_ms=500, confidence=0.95),
            TranscriptWord(text="world", start_ms=500, end_ms=1000, confidence=0.94),
        ]
        segment = TranscriptSegment(
            id="seg_001",
            start_ms=0,
            end_ms=1000,
            text="Hello world",
            words=words,
        )
        assert len(segment.words) == 2

    def test_words_outside_segment_range_raises(self):
        words = [TranscriptWord(text="bad", start_ms=0, end_ms=500)]
        with pytest.raises(ValueError, match="timing outside segment range"):
            TranscriptSegment(
                id="seg_001",
                start_ms=1000,
                end_ms=2000,
                text="Test",
                words=words,
            )


class TestAudioEvent:
    """Tests for AudioEvent schema."""

    def test_valid_event(self):
        event = AudioEvent(
            event_id="ae_001",
            start_ms=0,
            end_ms=2500,
            type=AudioEventType.SPEECH,
            confidence=0.95,
        )
        assert event.duration_ms == 2500

    def test_event_from_json_with_type_field(self):
        """Test that AudioEvent can be created from JSON with 'type' field."""
        data = {
            "event_id": "ae_001",
            "start_ms": 0,
            "end_ms": 2500,
            "type": "speech",
            "transcript_segment_ids": ["seg_001"],
            "confidence": 0.95,
        }
        event = AudioEvent(**data)
        assert event.type == AudioEventType.SPEECH
        assert event.event_id == "ae_001"


class TestContentBlock:
    """Tests for ContentBlock schema."""

    def test_valid_block(self):
        block = ContentBlock(
            block_id="block_001",
            start_ms=0,
            end_ms=2500,
            source_start_ms=0,
            source_end_ms=2500,
            shot_ids=["shot_001"],
            transcript_segment_ids=["seg_001"],
            audio_event_ids=["ae_001"],
            description="Test block",
            transcript="Test transcript",
            script_draft="Test script",
        )
        assert block.duration_ms == 2500

    def test_source_duration_mismatch_raises(self):
        with pytest.raises(ValueError, match="Output duration.*!= source duration"):
            ContentBlock(
                block_id="bad",
                start_ms=0,
                end_ms=2500,
                source_start_ms=0,
                source_end_ms=3000,  # Mismatch!
            )


class TestReviewInfo:
    """Tests for ReviewInfo schema."""

    def test_default_review(self):
        review = ReviewInfo()
        assert review.status == ReviewStatus.DRAFT


class TestAnalysisDocument:
    """Tests for AnalysisDocument schema."""

    def test_load_from_fixture(self, sample_analysis_json):
        """Test loading the sample analysis JSON."""
        doc = AnalysisDocument(**sample_analysis_json)
        
        assert doc.schema_version == 1
        assert doc.analysis_id == "analysis_sample_001"
        assert doc.project_id == "test_sample"
        assert len(doc.shots) == 4
        assert len(doc.transcript_segments) == 4
        assert len(doc.audio_events) == 4
        assert len(doc.content_blocks) == 4

    def test_blocks_within_media_validation(self, sample_analysis_json):
        """Test that blocks within media duration are valid."""
        doc = AnalysisDocument(**sample_analysis_json)
        # Should not raise - all blocks are within media duration
        assert doc.media.duration_ms == 10000

    def test_overlapping_narration_blocks_raises(self):
        """Test that overlapping narration blocks raise an error."""
        data = {
            "schema_version": 1,
            "analysis_id": "test_overlap",
            "project_id": "test",
            "media": {
                "duration_ms": 10000,
                "fps_num": 30000,
                "fps_den": 1001,
                "width": 1920,
                "height": 1080,
                "audio_sample_rate": 48000,
                "audio_channels": 2,
            },
            "content_blocks": [
                {
                    "block_id": "block_001",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "source_start_ms": 0,
                    "source_end_ms": 3000,
                    "script_final": "First block",
                },
                {
                    "block_id": "block_002",
                    "start_ms": 2000,
                    "end_ms": 5000,
                    "source_start_ms": 2000,
                    "source_end_ms": 5000,
                    "script_final": "Second block - overlaps with first",
                },
            ],
        }
        with pytest.raises(ValueError, match="overlap"):
            AnalysisDocument(**data)

    def test_get_block(self, sample_analysis_json):
        """Test getting a block by ID."""
        doc = AnalysisDocument(**sample_analysis_json)
        block = doc.get_block("block_001")
        assert block is not None
        assert block.block_id == "block_001"

    def test_get_block_not_found(self, sample_analysis_json):
        """Test getting a non-existent block."""
        doc = AnalysisDocument(**sample_analysis_json)
        block = doc.get_block("nonexistent")
        assert block is None

    def test_get_verified_blocks(self, sample_analysis_json):
        """Test getting verified blocks."""
        doc = AnalysisDocument(**sample_analysis_json)
        verified = doc.get_verified_blocks()
        assert len(verified) == 0  # No blocks are verified in the fixture

    def test_get_blocks_with_final_script(self, sample_analysis_json):
        """Test getting blocks with final script."""
        doc = AnalysisDocument(**sample_analysis_json)
        blocks_with_script = doc.get_blocks_with_final_script()
        assert len(blocks_with_script) == 0  # No blocks have final script in fixture

    def test_has_overlap_no_overlaps(self, sample_analysis_json):
        """Test has_overlap with no overlaps."""
        doc = AnalysisDocument(**sample_analysis_json)
        assert doc.has_overlap() is False

    def test_is_ready_for_render(self, sample_analysis_json):
        """Test is_ready_for_render."""
        doc = AnalysisDocument(**sample_analysis_json)
        # No blocks have final script, so not ready
        assert doc.is_ready_for_render() is False


class TestAudioDelivery:
    """Tests for AudioDelivery schema."""

    def test_valid_delivery(self):
        delivery = AudioDelivery(
            file_path="/path/to/audio.mp3",
            actual_duration_ms=2500,
            expected_start_ms=0,
            expected_end_ms=2500,
            fit_mode=AudioFitMode.PRESERVE_SPEED,
            status=AudioDeliveryStatus.UPLOADED,
        )
        assert delivery.expected_duration_ms == 2500


class TestDeliveryReport:
    """Tests for DeliveryReport schema."""

    def test_valid_report(self):
        report = DeliveryReport(
            project_id="test_project",
            analysis_id="test_analysis",
            total_blocks=4,
            blocks_with_script=2,
            blocks_with_audio=1,
            blocks_approved=1,
            has_overlaps=False,
            has_gaps=False,
            total_duration_ms=10000,
            ready_for_render=False,
        )
        assert report.project_id == "test_project"


class TestAudioFitMode:
    """Tests for AudioFitMode enum."""

    def test_all_modes(self):
        assert AudioFitMode.PRESERVE_SPEED == "preserve_speed"
        assert AudioFitMode.TRIM_EDGE_SILENCE == "trim_edge_silence"
        assert AudioFitMode.FIT_95_105_PERCENT == "fit_95_105_percent"
        assert AudioFitMode.MANUAL_TRIM == "manual_trim"


class TestReviewStatus:
    """Tests for ReviewStatus enum."""

    def test_all_statuses(self):
        assert ReviewStatus.DRAFT == "draft"
        assert ReviewStatus.IN_REVIEW == "in_review"
        assert ReviewStatus.APPROVED == "approved"
        assert ReviewStatus.REJECTED == "rejected"


class TestAudioDeliveryStatus:
    """Tests for AudioDeliveryStatus enum."""

    def test_all_statuses(self):
        assert AudioDeliveryStatus.MISSING == "missing"
        assert AudioDeliveryStatus.UPLOADED == "uploaded"
        assert AudioDeliveryStatus.VALIDATED == "validated"
        assert AudioDeliveryStatus.APPROVED == "approved"


class TestAudioEventType:
    """Tests for AudioEventType enum."""

    def test_all_types(self):
        assert AudioEventType.SPEECH == "speech"
        assert AudioEventType.SILENCE == "silence"
        assert AudioEventType.MUSIC == "music"
        assert AudioEventType.NOISE == "noise"
        assert AudioEventType.LAUGHTER == "laughter"
        assert AudioEventType.OTHER == "other"
