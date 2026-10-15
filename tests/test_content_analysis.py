"""Tests for the content_analysis module."""

import pytest

from pipeline.content_analysis import (
    create_content_blocks,
    create_blocks_from_scenes,
    blocks_to_dict_list,
    dict_list_to_blocks,
    _find_overlapping_shots,
    _find_overlapping_segments,
    _find_overlapping_events,
    _merge_overlapping_ranges,
    _get_speech_ranges,
)
from core.analysis_schemas import (
    ContentBlock,
    ShotAnalysis,
    TranscriptSegment,
    AudioEvent,
    AudioEventType,
)


class TestMergeOverlappingRanges:
    """Tests for range merging."""

    def test_merge_overlapping(self):
        """Test merging overlapping ranges."""
        ranges = [(0, 1000), (500, 1500), (2000, 3000)]
        merged = _merge_overlapping_ranges(ranges, min_gap_ms=0)
        
        # With min_gap_ms=0, only truly overlapping ranges merge
        # (0,1000) and (500,1500) overlap, so they merge to (0,1500)
        # (2000,3000) is separate (gap of 500 > 0)
        assert len(merged) == 2
        assert merged[0] == (0, 1500)
        assert merged[1] == (2000, 3000)

    def test_merge_adjacent(self):
        """Test merging adjacent ranges within threshold."""
        ranges = [(0, 1000), (1400, 2000)]
        merged = _merge_overlapping_ranges(ranges, min_gap_ms=500)
        
        # Gap is 400ms, which is < 500ms, so should merge
        assert len(merged) == 1
        assert merged[0] == (0, 2000)

    def test_no_merge_distant(self):
        """Test that distant ranges are not merged."""
        ranges = [(0, 1000), (2000, 3000)]
        merged = _merge_overlapping_ranges(ranges, min_gap_ms=500)
        
        # Gap is 1000ms, which is > 500ms, so should not merge
        assert len(merged) == 2

    def test_empty_ranges(self):
        """Test merging empty list."""
        merged = _merge_overlapping_ranges([])
        assert merged == []


class TestGetSpeechRanges:
    """Tests for speech range extraction."""

    def test_get_ranges_from_segments(self):
        """Test getting speech ranges from segments."""
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=1000, text="Hello"),
            TranscriptSegment(id="seg_002", start_ms=2000, end_ms=3000, text="World"),
        ]
        
        ranges = _get_speech_ranges(segments, [])
        
        assert len(ranges) == 2
        assert (0, 1000) in ranges
        assert (2000, 3000) in ranges

    def test_get_ranges_from_events(self):
        """Test getting speech ranges from audio events."""
        events = [
            AudioEvent(
                event_id="ae_001",
                start_ms=0,
                end_ms=1000,
                type=AudioEventType.SPEECH,
            ),
            AudioEvent(
                event_id="ae_002",
                start_ms=2000,
                end_ms=3000,
                type=AudioEventType.SILENCE,
            ),
        ]
        
        ranges = _get_speech_ranges([], events)
        
        assert len(ranges) == 1
        assert (0, 1000) in ranges


class TestFindOverlapping:
    """Tests for finding overlapping items."""

    def test_find_overlapping_shots(self):
        """Test finding overlapping shots."""
        shots = [
            ShotAnalysis(shot_id="shot_001", start_ms=0, end_ms=2000),
            ShotAnalysis(shot_id="shot_002", start_ms=1000, end_ms=3000),
            ShotAnalysis(shot_id="shot_003", start_ms=4000, end_ms=5000),
        ]
        
        overlapping = _find_overlapping_shots(1500, 2500, shots)
        
        assert "shot_001" in overlapping
        assert "shot_002" in overlapping
        assert "shot_003" not in overlapping

    def test_find_overlapping_segments(self):
        """Test finding overlapping segments."""
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=1000, text="A"),
            TranscriptSegment(id="seg_002", start_ms=500, end_ms=1500, text="B"),
            TranscriptSegment(id="seg_003", start_ms=2000, end_ms=3000, text="C"),
        ]
        
        overlapping = _find_overlapping_segments(750, 1250, segments)
        
        assert "seg_001" in overlapping
        assert "seg_002" in overlapping
        assert "seg_003" not in overlapping

    def test_find_overlapping_events(self):
        """Test finding overlapping events."""
        events = [
            AudioEvent(event_id="ae_001", start_ms=0, end_ms=1000, type=AudioEventType.SPEECH),
            AudioEvent(event_id="ae_002", start_ms=500, end_ms=1500, type=AudioEventType.SPEECH),
            AudioEvent(event_id="ae_003", start_ms=2000, end_ms=3000, type=AudioEventType.SILENCE),
        ]
        
        overlapping = _find_overlapping_events(750, 1250, events)
        
        assert "ae_001" in overlapping
        assert "ae_002" in overlapping
        assert "ae_003" not in overlapping


class TestCreateContentBlocks:
    """Tests for content block creation."""

    def test_create_blocks_basic(self):
        """Test creating content blocks from basic inputs."""
        shots = [
            ShotAnalysis(shot_id="shot_001", start_ms=0, end_ms=5000, description="Scene 1"),
        ]
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=2000, text="Hello", confidence=0.95),
            TranscriptSegment(id="seg_002", start_ms=2500, end_ms=4000, text="World", confidence=0.90),
        ]
        events = [
            AudioEvent(
                event_id="ae_001",
                start_ms=0,
                end_ms=2000,
                type=AudioEventType.SPEECH,
                confidence=0.95,
            ),
            AudioEvent(
                event_id="ae_002",
                start_ms=2500,
                end_ms=4000,
                type=AudioEventType.SPEECH,
                confidence=0.90,
            ),
        ]
        
        result = create_content_blocks(
            shots=shots,
            transcript_segments=segments,
            audio_events=events,
            media_duration_ms=5000,
            min_block_duration_ms=1000,
        )
        
        assert len(result.content_blocks) >= 1
        
        # Check that blocks have correct references
        for block in result.content_blocks:
            assert isinstance(block, ContentBlock)
            assert block.end_ms > block.start_ms
            assert block.script_draft == block.transcript

    def test_create_blocks_with_gaps(self):
        """Test creating blocks with gaps between speech."""
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=1000, text="First"),
            TranscriptSegment(id="seg_002", start_ms=5000, end_ms=6000, text="Second"),
        ]
        
        result = create_content_blocks(
            shots=[],
            transcript_segments=segments,
            audio_events=[],
            media_duration_ms=6000,
            min_block_duration_ms=1000,
        )
        
        # Should create 2 separate blocks (gap is > min_block_duration)
        assert len(result.content_blocks) == 2


class TestCreateBlocksFromScenes:
    """Tests for creating blocks from scenes."""

    def test_create_from_scenes(self):
        """Test creating blocks directly from scenes."""
        scenes = [
            {"scene_id": 0, "start": 0, "end": 5.0},
            {"scene_id": 1, "start": 5.0, "end": 10.0},
        ]
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=2000, text="Scene 1 text"),
            TranscriptSegment(id="seg_002", start_ms=5000, end_ms=7000, text="Scene 2 text"),
        ]
        
        blocks = create_blocks_from_scenes(scenes, segments, [])
        
        assert len(blocks) == 2
        assert blocks[0].block_id == "block_0000"
        assert blocks[0].start_ms == 0
        assert blocks[0].end_ms == 5000


class TestBlocksSerialization:
    """Tests for block serialization."""

    def test_blocks_to_dict_list(self):
        """Test converting blocks to dictionary list."""
        blocks = [
            ContentBlock(
                block_id="block_001",
                start_ms=0,
                end_ms=1000,
                source_start_ms=0,
                source_end_ms=1000,
                shot_ids=["shot_001"],
                transcript_segment_ids=["seg_001"],
                audio_event_ids=["ae_001"],
                description="Test block",
                transcript="Test transcript",
                script_draft="Test script",
                confidence=0.95,
            ),
        ]
        
        result = blocks_to_dict_list(blocks)
        
        assert len(result) == 1
        assert result[0]["block_id"] == "block_001"
        assert result[0]["start_ms"] == 0
        assert result[0]["transcript"] == "Test transcript"

    def test_dict_list_to_blocks(self):
        """Test converting dictionary list to blocks."""
        data = [
            {
                "block_id": "block_001",
                "start_ms": 0,
                "end_ms": 1000,
                "source_start_ms": 0,
                "source_end_ms": 1000,
                "shot_ids": ["shot_001"],
                "transcript_segment_ids": ["seg_001"],
                "audio_event_ids": ["ae_001"],
                "description": "Test block",
                "transcript": "Test transcript",
                "speaker_ids": [],
                "script_draft": "Test script",
                "script_final": None,
                "confidence": 0.95,
                "human_verified": False,
                "review_note": "",
                "audio_delivery": None,
            },
        ]
        
        blocks = dict_list_to_blocks(data)
        
        assert len(blocks) == 1
        assert isinstance(blocks[0], ContentBlock)
        assert blocks[0].block_id == "block_001"
        assert blocks[0].start_ms == 0


class TestRoundTrip:
    """Tests for serialization round-trip."""

    def test_blocks_round_trip(self):
        """Test that blocks can be serialized and deserialized."""
        original = [
            ContentBlock(
                block_id="block_001",
                start_ms=0,
                end_ms=1000,
                source_start_ms=0,
                source_end_ms=1000,
                shot_ids=["shot_001"],
                transcript_segment_ids=["seg_001"],
                description="Test",
                transcript="Test transcript",
                script_draft="Test script",
                confidence=0.95,
            ),
        ]
        
        data = blocks_to_dict_list(original)
        restored = dict_list_to_blocks(data)
        
        assert len(restored) == len(original)
        assert restored[0].block_id == original[0].block_id
        assert restored[0].start_ms == original[0].start_ms
        assert restored[0].transcript == original[0].transcript
